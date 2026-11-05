#!/bin/bash
# Removes the goto service from macOS.
# Your links (~/.goto/goto.db) are kept unless you pass --purge.
set -euo pipefail

LABEL="com.goto.server"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"

launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
rm -f "$PLIST"

echo "Removing the goto command and the /etc/hosts entry (you may be asked for your password)"
sudo rm -f /usr/local/bin/goto
sudo sed -i '' -E '/^[[:space:]]*(127\.0\.0\.1|::1)[[:space:]]+goto[[:space:]]*$/d' /etc/hosts
sudo dscacheutil -flushcache || true
sudo killall -HUP mDNSResponder 2>/dev/null || true

if [[ "${1:-}" == "--purge" ]]; then
  rm -rf "$HOME/.goto"
  echo "goto removed, including your links."
else
  echo "goto removed. Your links are still in ~/.goto/goto.db (run with --purge to delete them)."
fi
