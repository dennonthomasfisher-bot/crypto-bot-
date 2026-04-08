#!/bin/bash
# Start (or restart) the CoinWatchAlert bot via launchd
PLIST="$HOME/Library/LaunchAgents/com.coinwatchalert.bot.plist"
SRC="$(cd "$(dirname "$0")" && pwd)/com.coinwatchalert.bot.plist"

mkdir -p "$HOME/Library/LaunchAgents"

# Inject API key into reply_tool.html
"$(cd "$(dirname "$0")" && pwd)/inject_key.sh"

# Clean bootout before restarting — no zombie processes
launchctl bootout gui/$(id -u) "$PLIST" 2>/dev/null
sleep 2

cp "$SRC" "$PLIST"
launchctl bootstrap gui/$(id -u) "$PLIST"
echo "CoinWatchAlert bot started. Logs: ~/crypto-bot-/bot.log"
