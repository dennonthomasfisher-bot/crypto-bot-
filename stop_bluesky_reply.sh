#!/bin/bash
PLIST="$HOME/Library/LaunchAgents/com.cryptovault.bluesky_reply.plist"

launchctl bootout gui/$(id -u) "$PLIST" 2>/dev/null && \
    echo "Bluesky reply engine stopped." || \
    echo "Bluesky reply engine was not running."
