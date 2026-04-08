#!/bin/bash
FILE=~/crypto-bot-/reply_tool.html
KEY=$(grep ANTHROPIC_API_KEY ~/crypto-bot-/.env | cut -d'=' -f2 | tr -d '[:space:]')
git checkout -- "$FILE"
sed -i '' "s|REPLY_TOOL_API_KEY_PLACEHOLDER|$KEY|" "$FILE"
echo "Key injected cleanly."
