#!/bin/bash
# Start (or restart) the CryptoVault bot via launchd
set -e
PLIST="$HOME/Library/LaunchAgents/com.cryptovault.bot.plist"
SRC="$(cd "$(dirname "$0")" && pwd)/com.cryptovault.bot.plist"
BOT_DIR="$(cd "$(dirname "$0")" && pwd)"

mkdir -p "$HOME/Library/LaunchAgents"

# Inject API key into reply_tool.html
"$BOT_DIR/inject_key.sh"

# 1. Clean bootout — removes the launchd agent if present
launchctl bootout gui/$(id -u) "$PLIST" 2>/dev/null || true

# 2. Kill any orphan bot.py python processes so bootstrap can't race
#    against a zombie. pkill returns non-zero if nothing matched — fine.
pkill -f "$BOT_DIR/bot.py" 2>/dev/null || true

# 3. Wait for processes to actually die before installing the new plist.
#    launchctl bootout is asynchronous; polling is safer than a fixed sleep.
for i in 1 2 3 4 5 6 7 8 9 10; do
    if ! pgrep -f "$BOT_DIR/bot.py" >/dev/null 2>&1; then
        break
    fi
    sleep 1
done

# 4. Clear any stale PID file so the singleton guard starts clean
rm -f "$BOT_DIR/bot.pid"

# 5. Install plist and bootstrap the agent
cp "$SRC" "$PLIST"
launchctl bootstrap gui/$(id -u) "$PLIST"

echo "CryptoVault bot started. Logs: ~/crypto-bot-/bot.log"
