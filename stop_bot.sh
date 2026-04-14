#!/bin/bash
# Stop the CryptoVault bot — full cleanup
PLIST="$HOME/Library/LaunchAgents/com.cryptovault.bot.plist"

launchctl bootout gui/$(id -u) "$PLIST" 2>/dev/null
pkill -9 -f bot.py 2>/dev/null
rm -f ~/crypto-bot-/bot.pid
echo "CryptoVault bot stopped."
