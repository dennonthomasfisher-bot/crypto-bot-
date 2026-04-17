#!/bin/bash
# Stop the CryptoVault health monitor.
PLIST="$HOME/Library/LaunchAgents/com.cryptovault.healthcheck.plist"

launchctl bootout gui/$(id -u) "$PLIST" 2>/dev/null
rm -f "$PLIST"
echo "CryptoVault health monitor stopped."
