#!/bin/bash
# Installs goto as a network-wide service on Linux (Raspberry Pi OS, Debian, Ubuntu).
#   sudo ./install-linux.sh
#
# - runs as a dedicated 'goto' system user, listening on port 80 on all interfaces
# - stores links in /var/lib/goto/goto.db
# - installs the `goto` command to /usr/local/bin
# Then point the name 'goto' at this machine for the rest of your network (see README).
#
# Extra hostnames people will type (e.g. goto.lan) can be passed in:
#   sudo GOTO_ALLOW_HOSTS="goto.lan,goto.home" ./install-linux.sh
set -euo pipefail

[[ $EUID -eq 0 ]] || { echo "Run with sudo: sudo $0"; exit 1; }
command -v systemctl >/dev/null || { echo "This installer needs systemd."; exit 1; }
command -v python3 >/dev/null || { echo "Install Python 3 first: sudo apt install python3"; exit 1; }

HERE="$(cd "$(dirname "$0")" && pwd)"
PORT="${GOTO_PORT:-80}"
PY="$(command -v python3)"

# Hostnames allowed to use the editing API: this machine's names and IPs + anything passed in.
ALLOW="$(hostname),$(hostname).local,$(hostname -I 2>/dev/null | tr ' ' ',')${GOTO_ALLOW_HOSTS:+,$GOTO_ALLOW_HOSTS}"
ALLOW="$(echo "$ALLOW" | sed -E 's/,+/,/g; s/^,|,$//g')"

echo "==> Installing goto"
id goto >/dev/null 2>&1 || useradd --system --home /var/lib/goto --shell /usr/sbin/nologin goto
install -d -o goto -g goto -m 755 /var/lib/goto
install -d -m 755 /opt/goto
install -m 755 "$HERE/goto.py" /opt/goto/goto.py
cat > /usr/local/bin/goto <<EOF
#!/bin/sh
exec "$PY" /opt/goto/goto.py "\$@"
EOF
chmod 755 /usr/local/bin/goto

# So 'goto' also works on the Pi itself.
grep -qE '^[[:space:]]*127\.0\.0\.1[[:space:]]+([^#]*[[:space:]])?goto([[:space:]]|$)' /etc/hosts \
  || echo "127.0.0.1 goto" >> /etc/hosts
grep -qE '^[[:space:]]*::1[[:space:]]+([^#]*[[:space:]])?goto([[:space:]]|$)' /etc/hosts \
  || echo "::1 goto" >> /etc/hosts

cat > /etc/systemd/system/goto.service <<EOF
[Unit]
Description=goto short links
After=network-online.target
Wants=network-online.target

[Service]
User=goto
Group=goto
ExecStart=$PY /opt/goto/goto.py serve --host 0.0.0.0 --port $PORT --db /var/lib/goto/goto.db --allow-host $ALLOW
Restart=on-failure
AmbientCapabilities=CAP_NET_BIND_SERVICE
CapabilityBoundingSet=CAP_NET_BIND_SERVICE
NoNewPrivileges=true
ProtectSystem=strict
ProtectHome=true
ReadWritePaths=/var/lib/goto
PrivateTmp=true

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable --now goto.service
systemctl restart goto.service
sleep 1

if curl -fsS --noproxy '*' "http://127.0.0.1:$PORT/_/health" >/dev/null 2>&1; then
  echo "==> goto is running on port $PORT."
  echo "    This machine's address(es): $(hostname -I 2>/dev/null || echo '?')"
  echo "    Next: make 'goto' resolve to that address on your network (see README, 'Whole home network')."
else
  echo "goto didn't start. Check: journalctl -u goto -n 50"
  exit 1
fi
