#!/bin/bash
# Stop the CoinWatchAlert bot
PLIST="$HOME/Library/LaunchAgents/com.coinwatchalert.bot.plist"

launchctl unload "$PLIST" 2>/dev/null
echo "CoinWatchAlert bot stopped."
