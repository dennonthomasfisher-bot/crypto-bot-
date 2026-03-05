#!/bin/bash
# start.sh – Launch both bots inside a tmux session with a clean 3-pane layout.
#
# Layout:
#   ┌─────────────────────┬─────────────────────┐
#   │  top-left           │  top-right          │
#   │  news bot tweets    │  trading BUY/SELL   │
#   ├─────────────────────┴─────────────────────┤
#   │  bottom: command prompt                   │
#   └───────────────────────────────────────────┘
#
# Usage:  bash ~/crypto-bot-/start.sh
#         bash ~/crypto-bot-/start.sh --attach   (attach to existing session)

REPO="$HOME/crypto-bot-"
SESSION="crypto"
BOT_LOG="$REPO/bot.log"
TRADE_LOG="$REPO/trading_bot.log"

# ── Attach to existing session if requested ───────────────────────────────────
if [[ "$1" == "--attach" ]]; then
  tmux attach -t "$SESSION"
  exit $?
fi

# ── Kill any existing session ─────────────────────────────────────────────────
tmux kill-session -t "$SESSION" 2>/dev/null

# ── Kill any running bot processes ───────────────────────────────────────────
pkill -f "python.*trading_bot/bot.py" 2>/dev/null
pkill -f "python.*bot.py" 2>/dev/null
sleep 1

# ── Start both bots in the background ────────────────────────────────────────
cd "$REPO"
nohup python3 trading_bot/bot.py >> "$TRADE_LOG" 2>&1 &
TRADE_PID=$!

if [ -f "$REPO/requirements.txt" ] || [ -d "$REPO/.venv" ]; then
  PYTHON_NEWS="python3"
  [ -f "$REPO/.venv/bin/python3" ] && PYTHON_NEWS="$REPO/.venv/bin/python3"
  nohup $PYTHON_NEWS "$REPO/bot.py" >> "$BOT_LOG" 2>&1 &
  NEWS_PID=$!
else
  NEWS_PID=""
fi

echo "Trading bot PID: $TRADE_PID"
[ -n "$NEWS_PID" ] && echo "News bot PID:    $NEWS_PID"
sleep 2

# ── Create tmux session ───────────────────────────────────────────────────────
tmux new-session -d -s "$SESSION" -x 220 -y 50

# Pane 0 (top-left): news bot – tweets only
tmux send-keys -t "$SESSION:0.0" \
  "echo '=== News Bot Tweets ===' && tail -F '$BOT_LOG' | grep --line-buffered -E 'Tweet posted|ERROR|quote tweet'" \
  Enter

# Split right (pane 1, top-right): trading bot – BUY/SELL/Capital/errors only
tmux split-window -h -t "$SESSION:0.0"
tmux send-keys -t "$SESSION:0.1" \
  "echo '=== Trading: BUY/SELL ===' && tail -F '$TRADE_LOG' | grep --line-buffered -E 'BUY|SELL|Capital|ERROR|Restored'" \
  Enter

# Select left pane, split bottom (pane 2): command prompt
tmux select-pane -t "$SESSION:0.0"
tmux split-window -v -t "$SESSION:0.0"
tmux send-keys -t "$SESSION:0.2" "cd '$REPO' && echo 'Both bots running. status: bash status.sh'" Enter

# Size the top row at ~70% height, left/right equal width
tmux resize-pane -t "$SESSION:0.0" -y 35
tmux resize-pane -t "$SESSION:0.1" -y 35

# ── Attach ────────────────────────────────────────────────────────────────────
tmux attach -t "$SESSION"
