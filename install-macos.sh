#!/bin/bash
# Installs goto on macOS:
#   - copies goto.py to ~/.goto/            (your links live in ~/.goto/goto.db)
#   - adds "127.0.0.1 goto" to /etc/hosts   (needs your password once)
#   - adds the `goto` command to /usr/local/bin
#   - starts the server now and at every login (LaunchAgent)
#     (it listens on port 80 on all addresses, which macOS allows without root,
#      but only answers requests from this Mac)
# Safe to run again to upgrade; your links are kept.
set -euo pipefail

PORT="${GOTO_PORT:-80}"
HERE="$(cd "$(dirname "$0")" && pwd)"
APP_DIR="$HOME/.goto"
LABEL="com.goto.server"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
BIN="/usr/local/bin/goto"

say() { printf '\033[1m==>\033[0m %s\n' "$*"; }

[[ "$(uname)" == "Darwin" ]] || { echo "This installer is for macOS. On Linux / Raspberry Pi use install-linux.sh."; exit 1; }

PY="$(command -v python3 || true)"
if [[ -z "$PY" ]] || ! "$PY" -c 'import sys; assert sys.version_info >= (3, 8)' 2>/dev/null; then
  echo "goto needs Python 3.8+. Run 'xcode-select --install' (or 'brew install python') and try again."
  exit 1
fi

say "Installing goto into $APP_DIR"
mkdir -p "$APP_DIR" "$HOME/Library/LaunchAgents"
cp "$HERE/goto.py" "$APP_DIR/goto.py"
chmod 755 "$APP_DIR/goto.py"

say "Adding 'goto' to /etc/hosts and the goto command to $BIN (you may be asked for your password)"
if ! grep -qE '^[[:space:]]*127\.0\.0\.1[[:space:]]+([^#]*[[:space:]])?goto([[:space:]]|$)' /etc/hosts; then
  echo "127.0.0.1 goto" | sudo tee -a /etc/hosts >/dev/null
  sudo dscacheutil -flushcache || true
  sudo killall -HUP mDNSResponder 2>/dev/null || true
fi
sudo mkdir -p "$(dirname "$BIN")"
# A tiny wrapper so the command keeps working with whichever python3 installed it.
sudo tee "$BIN" >/dev/null <<EOF
#!/bin/sh
exec "$PY" "$APP_DIR/goto.py" "\$@"
EOF
sudo chmod 755 "$BIN"

say "Setting up the background service"
cat > "$PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key>
  <array>
    <string>$PY</string>
    <string>$APP_DIR/goto.py</string>
    <string>serve</string>
    <string>--host</string><string>0.0.0.0</string>
    <string>--local-only</string>
    <string>--port</string><string>$PORT</string>
    <string>--db</string><string>$APP_DIR/goto.db</string>
  </array>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>StandardOutPath</key><string>$APP_DIR/server.log</string>
  <key>StandardErrorPath</key><string>$APP_DIR/server.log</string>
</dict>
</plist>
EOF

launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$PLIST"

URL="http://goto"; [[ "$PORT" != "80" ]] && URL="http://goto:$PORT"
for _ in $(seq 1 20); do
  curl -fsS --noproxy '*' "$URL/_/health" >/dev/null 2>&1 && break
  sleep 0.25
done

if curl -fsS --noproxy '*' "$URL/_/health" >/dev/null 2>&1; then
  say "Done! goto is running."
  echo
  echo "  Open $URL/ to add links, or from the terminal:"
  echo "    goto add mail https://mail.google.com -a m"
  echo "    goto ls"
  echo
  echo "  Tip: the first time, type http://goto/ in your browser so it learns that goto/ is a site."
  [[ "$PORT" != "80" ]] && echo "  Note: running on port $PORT, so set GOTO_SERVER=$URL in your shell profile."
else
  echo "The service didn't answer yet. Check the log: tail $APP_DIR/server.log"
  echo "If port $PORT is busy, re-run with another port: GOTO_PORT=8080 ./install-macos.sh"
  exit 1
fi
