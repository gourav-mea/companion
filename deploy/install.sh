#!/bin/bash
# Installs the companion as a user-level systemd service that starts on
# every boot -- even before anyone logs in (via `loginctl enable-linger`).
#
# Run on the Pi (not this dev machine):
#   bash deploy/install.sh [path-to-repo-on-the-pi]
#
# Defaults to ~/Makeathon-21 if no path is given.
#
# Also installs the two optional persistent whisper.cpp servers (only
# useful once you've built whisper.cpp's `server` target and set
# config.WHISPER_SERVER_ENABLED = True) -- harmless to leave installed
# even if disabled, systemd just restarts them quietly in the background.
set -e

REPO_DIR="${1:-$HOME/Makeathon-21}"
UNIT_DIR="$HOME/.config/systemd/user"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

mkdir -p "$UNIT_DIR"
sed "s|%h/Makeathon-21|$REPO_DIR|g" "$SCRIPT_DIR/companion.service" > "$UNIT_DIR/companion.service"
cp "$SCRIPT_DIR/whisper-main.service" "$UNIT_DIR/whisper-main.service"
cp "$SCRIPT_DIR/whisper-wake.service" "$UNIT_DIR/whisper-wake.service"

systemctl --user daemon-reload
systemctl --user enable companion.service whisper-main.service whisper-wake.service
loginctl enable-linger "$(whoami)"   # let the user services run without an interactive login
systemctl --user restart whisper-main.service whisper-wake.service companion.service

echo "Installed. The companion will now start automatically on every boot."
echo "Status: systemctl --user status companion.service"
echo "Logs:   journalctl --user -u companion -f"
echo "Stop:   systemctl --user stop companion.service"
echo "Disable (stop auto-start): systemctl --user disable companion.service"
