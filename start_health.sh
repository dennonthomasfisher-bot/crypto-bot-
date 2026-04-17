#!/bin/bash
# Start (or restart) the CryptoVault health monitor via launchd.
# Runs health_check.py every 5 minutes; alerts on Telegram when anything looks wrong.
PLIST="$HOME/Library/LaunchAgents/com.cryptovault.healthcheck.plist"
SRC="$(cd "$(dirname "$0")" && pwd)/com.cryptovault.healthcheck.plist"

mkdir -p "$HOME/Library/LaunchAgents"

launchctl bootout gui/$(id -u) "$PLIST" 2>/dev/null
sleep 1

cp "$SRC" "$PLIST"
launchctl bootstrap gui/$(id -u) "$PLIST"
echo "CryptoVault health monitor started. Logs: ~/crypto-bot-/health.log"
