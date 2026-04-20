#!/bin/bash
# One-time setup for the browser-based reply engine.
#
# 1. Installs Playwright + Chromium into the bot's venv
# 2. Opens a browser for you to log in as @CVault88
# 3. Saves cookies to .x_cookies.json
#
# After this runs successfully, use start_reply.sh to enable the launchd job.
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

VENV_PY="$SCRIPT_DIR/.venv/bin/python3"
VENV_PIP="$SCRIPT_DIR/.venv/bin/pip"

if [ ! -x "$VENV_PY" ]; then
    echo "Error: venv not found at $VENV_PY"
    echo "Expected layout: $SCRIPT_DIR/.venv/bin/python3"
    exit 1
fi

echo "==> Installing Playwright into venv..."
"$VENV_PIP" install --upgrade playwright >/dev/null

echo "==> Installing Chromium browser..."
"$VENV_PY" -m playwright install chromium

echo "==> Launching browser for X login..."
echo "    Log in as @CVault88 in the window that opens."
echo "    When you see your home feed, return here and press ENTER."
"$VENV_PY" "$SCRIPT_DIR/browser_reply_engine.py" --login

if [ -f "$SCRIPT_DIR/.x_cookies.json" ]; then
    echo ""
    echo "==> Setup complete. Cookies saved."
    echo "    Next: run ./start_reply.sh to enable the 15-min reply cron."
else
    echo ""
    echo "==> Cookies were NOT saved. Re-run this script."
    exit 1
fi
