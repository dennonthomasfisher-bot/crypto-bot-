#!/bin/bash
# Start (or restart) the browser reply engine via launchd.
# Runs every 15 min, caps at 4 replies/hour (internal rate limit).
PLIST="$HOME/Library/LaunchAgents/com.cryptovault.reply.plist"
SRC="$(cd "$(dirname "$0")" && pwd)/com.cryptovault.reply.plist"
COOKIES="$(cd "$(dirname "$0")" && pwd)/.x_cookies.json"

if [ ! -f "$COOKIES" ]; then
    echo "Error: $COOKIES not found."
    echo "Run ./setup_reply.sh first to log in and save cookies."
    exit 1
fi

mkdir -p "$HOME/Library/LaunchAgents"

launchctl bootout gui/$(id -u) "$PLIST" 2>/dev/null
sleep 1

cp "$SRC" "$PLIST"
launchctl bootstrap gui/$(id -u) "$PLIST"
echo "Reply engine started. Logs: ~/crypto-bot-/browser_reply.log"
echo "Runs every 15 min; caps at 4 replies/hour."
