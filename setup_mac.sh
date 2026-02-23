#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# setup_mac.sh  –  One-shot installer for the Crypto News Bot on macOS
#
# What it does:
#   1. Checks Python 3 is available
#   2. Creates a virtualenv and installs dependencies
#   3. Prompts you to confirm your .env is filled in
#   4. Writes a launchd plist with the correct absolute paths
#   5. Loads the plist so the bot starts now and at every login
#
# Usage:
#   chmod +x setup_mac.sh
#   ./setup_mac.sh
# ─────────────────────────────────────────────────────────────────────────────
set -euo pipefail

BOLD="\033[1m"
GREEN="\033[0;32m"
YELLOW="\033[0;33m"
RED="\033[0;31m"
RESET="\033[0m"

info()    { echo -e "${GREEN}[setup]${RESET} $*"; }
warn()    { echo -e "${YELLOW}[warn]${RESET}  $*"; }
error()   { echo -e "${RED}[error]${RESET} $*" >&2; }

# ── 0. Resolve script directory ───────────────────────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

info "Working directory: $SCRIPT_DIR"

# ── 1. Python check ───────────────────────────────────────────────────────────
if ! command -v python3 &>/dev/null; then
    error "python3 not found. Install it from https://www.python.org or via Homebrew:"
    error "  brew install python"
    exit 1
fi
PYTHON=$(command -v python3)
info "Using Python: $PYTHON ($($PYTHON --version))"

# ── 2. Virtual environment ────────────────────────────────────────────────────
VENV_DIR="$SCRIPT_DIR/.venv"
if [[ ! -d "$VENV_DIR" ]]; then
    info "Creating virtual environment in $VENV_DIR …"
    "$PYTHON" -m venv "$VENV_DIR"
fi
VENV_PYTHON="$VENV_DIR/bin/python"
info "Installing / upgrading dependencies…"
"$VENV_PYTHON" -m pip install --quiet --upgrade pip
"$VENV_PYTHON" -m pip install --quiet -r "$SCRIPT_DIR/requirements.txt"
info "Dependencies installed."

# ── 3. .env check ─────────────────────────────────────────────────────────────
if [[ ! -f "$SCRIPT_DIR/.env" ]]; then
    warn ".env not found – copying .env.example to .env"
    cp "$SCRIPT_DIR/.env.example" "$SCRIPT_DIR/.env"
    echo ""
    echo -e "${BOLD}ACTION REQUIRED:${RESET}"
    echo "  Edit $SCRIPT_DIR/.env and fill in your API keys, then re-run this script."
    echo ""
    exit 0
fi

if grep -q "your_api_key_here" "$SCRIPT_DIR/.env"; then
    warn ".env still contains placeholder values."
    echo ""
    echo -e "${BOLD}ACTION REQUIRED:${RESET}"
    echo "  Edit $SCRIPT_DIR/.env and replace all placeholder values."
    echo ""
    read -rp "Continue anyway? (y/N) " REPLY
    [[ "$REPLY" =~ ^[Yy]$ ]] || exit 0
fi

# ── 4. Write launchd plist ────────────────────────────────────────────────────
PLIST_SRC="$SCRIPT_DIR/com.cryptobot.plist"
PLIST_DEST="$HOME/Library/LaunchAgents/com.cryptobot.plist"
LOG_OUT="/tmp/cryptobot.out.log"
LOG_ERR="/tmp/cryptobot.err.log"

mkdir -p "$HOME/Library/LaunchAgents"

# Build plist with real paths substituted
cat > "$PLIST_DEST" <<PLIST_EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.cryptobot</string>

    <key>ProgramArguments</key>
    <array>
        <string>${VENV_PYTHON}</string>
        <string>${SCRIPT_DIR}/bot.py</string>
    </array>

    <key>WorkingDirectory</key>
    <string>${SCRIPT_DIR}</string>

    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <true/>
    <key>ThrottleInterval</key>
    <integer>30</integer>

    <key>StandardOutPath</key>
    <string>${LOG_OUT}</string>
    <key>StandardErrorPath</key>
    <string>${LOG_ERR}</string>

    <key>EnvironmentVariables</key>
    <dict>
        <key>PATH</key>
        <string>/usr/local/bin:/usr/bin:/bin:/opt/homebrew/bin</string>
    </dict>
</dict>
</plist>
PLIST_EOF

info "Plist written to $PLIST_DEST"

# ── 5. Load (or reload) the agent ─────────────────────────────────────────────
# Unload first in case it was already loaded
launchctl unload "$PLIST_DEST" 2>/dev/null || true
launchctl load "$PLIST_DEST"
info "launchd agent loaded."

# ── 6. Verify ─────────────────────────────────────────────────────────────────
sleep 2
if launchctl list | grep -q "com.cryptobot"; then
    info "Bot is running in the background."
else
    warn "Agent doesn't appear in launchctl list yet – check logs:"
    warn "  stdout: $LOG_OUT"
    warn "  stderr: $LOG_ERR"
fi

echo ""
echo -e "${BOLD}Setup complete!${RESET}"
echo ""
echo "Useful commands:"
echo "  View live logs:  tail -f $LOG_OUT"
echo "  View errors:     tail -f $LOG_ERR"
echo "  Stop the bot:    launchctl unload $PLIST_DEST"
echo "  Start the bot:   launchctl load   $PLIST_DEST"
echo "  Status:          launchctl list | grep cryptobot"
echo ""
