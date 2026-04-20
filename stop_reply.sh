#!/bin/bash
# Stop the browser reply engine launchd job.
PLIST="$HOME/Library/LaunchAgents/com.cryptovault.reply.plist"

launchctl bootout gui/$(id -u) "$PLIST" 2>/dev/null && \
    echo "Reply engine stopped." || \
    echo "Reply engine was not running."
