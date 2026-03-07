"""
Webhook alerts – send notifications to Discord and/or Telegram
alongside Twitter posts.

Configure via .env:
  DISCORD_WEBHOOK_URL=https://discord.com/api/webhooks/...
  TELEGRAM_BOT_TOKEN=123456:ABC-DEF...
  TELEGRAM_CHAT_ID=-1001234567890
"""
from __future__ import annotations

import logging
import os

import requests

logger = logging.getLogger(__name__)

# Load from env
DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL", "")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")


def _discord_enabled() -> bool:
    return bool(DISCORD_WEBHOOK_URL)


def _telegram_enabled() -> bool:
    return bool(TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID)


def send_discord(message: str, embed_title: str | None = None) -> bool:
    """Send a message to Discord via webhook. Returns True on success."""
    if not _discord_enabled():
        return False

    payload: dict = {}

    if embed_title:
        payload["embeds"] = [{
            "title": embed_title,
            "description": message,
            "color": 0xF7931A,  # Bitcoin orange
        }]
    else:
        payload["content"] = message

    try:
        resp = requests.post(DISCORD_WEBHOOK_URL, json=payload, timeout=10)
        if resp.status_code in (200, 204):
            logger.debug("Discord webhook sent successfully")
            return True
        logger.warning("Discord webhook returned %d: %s", resp.status_code, resp.text[:200])
        return False
    except requests.RequestException as exc:
        logger.warning("Discord webhook failed: %s", exc)
        return False


def send_telegram(message: str) -> bool:
    """Send a message to Telegram. Returns True on success."""
    if not _telegram_enabled():
        return False

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message,
        "parse_mode": "Markdown",
        "disable_web_page_preview": True,
    }

    try:
        resp = requests.post(url, json=payload, timeout=10)
        data = resp.json()
        if data.get("ok"):
            logger.debug("Telegram message sent successfully")
            return True
        logger.warning("Telegram API error: %s", data.get("description", "unknown"))
        return False
    except requests.RequestException as exc:
        logger.warning("Telegram send failed: %s", exc)
        return False


def broadcast(message: str, title: str | None = None) -> dict[str, bool]:
    """
    Send to all configured channels. Returns dict of channel → success.
    Silently skips channels that aren't configured.
    """
    results = {}

    if _discord_enabled():
        results["discord"] = send_discord(message, embed_title=title)

    if _telegram_enabled():
        results["telegram"] = send_telegram(message)

    if results:
        logger.info(
            "Webhook broadcast: %s",
            ", ".join(f"{k}={'OK' if v else 'FAIL'}" for k, v in results.items()),
        )

    return results


def status() -> dict[str, bool]:
    """Return which webhook channels are configured."""
    return {
        "discord": _discord_enabled(),
        "telegram": _telegram_enabled(),
    }
