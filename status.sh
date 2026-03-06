#!/usr/bin/env bash
# status.sh – Overnight health check for the crypto trading bot.
#
# Shows:
#   1. Bot process status
#   2. Total simulated PnL (sum of all SELL lines in trading_bot.log)
#   3. Signal firings today (BUY/SELL actions logged today)
#   4. Last 3 replies posted (from posted_tweets.log)
#
# Usage:  bash status.sh   (or  chmod +x status.sh && ./status.sh)

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BOT_LOG="$SCRIPT_DIR/trading_bot.log"
TWEET_LOG="$SCRIPT_DIR/posted_tweets.log"
TODAY="$(date '+%Y-%m-%d')"

SEP="════════════════════════════════════════════════════════════════"
sep="────────────────────────────────────────────────────────────────"

echo ""
echo "$SEP"
echo "  Crypto Bot Status  —  $TODAY"
echo "$SEP"

# ── 1. Bot process ────────────────────────────────────────────────────────────
echo ""
echo "  [1] BOT PROCESS"
echo "  $sep"
if pgrep -f "python.*bot\.py" > /dev/null 2>&1; then
    PID=$(pgrep -f "python.*bot\.py" | head -1)
    STARTED=$(ps -p "$PID" -o lstart= 2>/dev/null | xargs)
    echo "  Status  : RUNNING  (PID $PID)"
    echo "  Started : $STARTED"
else
    echo "  Status  : NOT RUNNING"
fi

# ── 2. Total simulated PnL ────────────────────────────────────────────────────
echo ""
echo "  [2] TOTAL SIMULATED PnL  (all SELL lines in trading_bot.log)"
echo "  $sep"
if [[ ! -f "$BOT_LOG" ]]; then
    echo "  Log not found: $BOT_LOG"
else
    # Log format: "SELL [reason]  PAIR  qty=...  entry=...  exit=...  PnL=+X.XX%"
    # Extract the PnL% values, sum them, and multiply by the default trade size ($25).
    python3 - "$BOT_LOG" << 'PYEOF'
import re, sys

log_path = sys.argv[1]
pattern  = re.compile(r'SELL\s+\[.*?\].*?PnL=([+-]?\d+\.\d+)%')
trade_usd = 25.0   # default MAX_PER_TRADE

total_pct = 0.0
total_usd = 0.0
count     = 0
wins      = 0

with open(log_path) as f:
    for line in f:
        m = pattern.search(line)
        if m:
            pct = float(m.group(1))
            usd = trade_usd * pct / 100.0
            total_pct += pct
            total_usd += usd
            count     += 1
            if pct > 0:
                wins += 1

if count == 0:
    print("  No closed trades found in log.")
else:
    sign = "+" if total_usd >= 0 else ""
    wr   = wins / count * 100
    print(f"  Closed trades : {count}")
    print(f"  Win rate      : {wr:.1f}%  ({wins}W / {count-wins}L)")
    print(f"  Total PnL     : {sign}${total_usd:.2f}  ({sign}{total_pct:.2f}% avg per $25 trade)")
PYEOF
fi

# ── 3. Signals fired today ────────────────────────────────────────────────────
echo ""
echo "  [3] SIGNALS FIRED TODAY  ($TODAY)"
echo "  $sep"
if [[ ! -f "$BOT_LOG" ]]; then
    echo "  Log not found: $BOT_LOG"
else
    # Count lines containing today's date AND signal data (score=)
    SIGNAL_COUNT=$(grep "^$TODAY" "$BOT_LOG" 2>/dev/null | grep -c "score=" || true)
    BUY_COUNT=$(grep   "^$TODAY" "$BOT_LOG" 2>/dev/null | grep -c "-> BUY"  || true)
    SELL_COUNT=$(grep  "^$TODAY" "$BOT_LOG" 2>/dev/null | grep -c "-> SELL" || true)
    HOLD_COUNT=$(grep  "^$TODAY" "$BOT_LOG" 2>/dev/null | grep -c "-> HOLD" || true)

    echo "  Signal evaluations : $SIGNAL_COUNT"
    echo "  BUY  signals       : $BUY_COUNT"
    echo "  SELL signals       : $SELL_COUNT"
    echo "  HOLD signals       : $HOLD_COUNT"

    # Show the last 5 signal lines from today
    LAST=$(grep "^$TODAY" "$BOT_LOG" 2>/dev/null | grep "score=" | tail -5)
    if [[ -n "$LAST" ]]; then
        echo ""
        echo "  Last signal lines:"
        while IFS= read -r line; do
            echo "    $line"
        done <<< "$LAST"
    fi
fi

# ── 4. Last 3 tweets posted ───────────────────────────────────────────────────
echo ""
echo "  [4] LAST 3 TWEETS / REPLIES POSTED"
echo "  $sep"
if [[ ! -f "$TWEET_LOG" ]]; then
    echo "  No posted_tweets.log found yet."
else
    COUNT=$(wc -l < "$TWEET_LOG" | tr -d ' ')
    echo "  Total posted: $COUNT"
    echo ""
    tail -3 "$TWEET_LOG" | while IFS= read -r line; do
        echo "  $line"
    done
fi

echo ""
echo "$SEP"
echo ""
