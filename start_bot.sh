#!/bin/bash
# Start (or restart) the CoinWatchAlert bot via launchd
PLIST="$HOME/Library/LaunchAgents/com.coinwatchalert.bot.plist"
SRC="$(cd "$(dirname "$0")" && pwd)/com.coinwatchalert.bot.plist"

mkdir -p "$HOME/Library/LaunchAgents"
cp "$SRC" "$PLIST"

launchctl unload "$PLIST" 2>/dev/null
launchctl load "$PLIST"
echo "CoinWatchAlert bot started. Logs: ~/crypto-bot-/bot.log"
