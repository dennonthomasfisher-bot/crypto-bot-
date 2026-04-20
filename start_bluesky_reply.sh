#!/bin/bash
# Start (or restart) the Bluesky reply engine via launchd.
# Runs every 20 min, caps at 2/hr, 10/day.
PLIST="$HOME/Library/LaunchAgents/com.cryptovault.bluesky_reply.plist"
SRC="$(cd "$(dirname "$0")" && pwd)/com.cryptovault.bluesky_reply.plist"

mkdir -p "$HOME/Library/LaunchAgents"

launchctl bootout gui/$(id -u) "$PLIST" 2>/dev/null
sleep 1

cp "$SRC" "$PLIST"
launchctl bootstrap gui/$(id -u) "$PLIST"
echo "Bluesky reply engine started. Logs: ~/crypto-bot-/bluesky_reply.log"
echo "Runs every 20 min; caps at 2/hr, 10/day."
