"""
Telegram Bot API client — sends messages and photos to the configured channel.

Uses only the `requests` library (no python-telegram-bot dependency).
Every call is wrapped so a Telegram failure never crashes the main bot.
"""
from __future__ import annotations

import logging
import os

import requests

import config

logger = logging.getLogger(__name__)

_API_BASE = "https://api.telegram.org/bot{token}"


def _api_url(method: str) -> str:
    token = config.TELEGRAM_BOT_TOKEN
    if not token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN is not set")
    return f"{_API_BASE.format(token=token)}/{method}"


def send_telegram(
    text: str,
    image_path: str | None = None,
    chat_id: str | None = None,
) -> bool:
    """Send a message (and optional photo) to Telegram.

    Defaults to TELEGRAM_CHANNEL_ID (the public subscriber channel) but the
    `chat_id` argument lets health alerts and other internal messages target
    a separate chat (private DM or private group) so they don't leak into
    the public feed.

    Returns True on success, False on any failure.
    """
    if not config.TELEGRAM_ENABLED:
        return False

    token = config.TELEGRAM_BOT_TOKEN
    target_chat_id = chat_id or config.TELEGRAM_CHANNEL_ID
    if not token or not target_chat_id:
        logger.debug("Telegram credentials missing — skipping")
        return False

    try:
        if image_path and os.path.isfile(image_path):
            # Send photo with caption
            url = _api_url("sendPhoto")
            with open(image_path, "rb") as img:
                resp = requests.post(
                    url,
                    data={"chat_id": target_chat_id, "caption": text, "parse_mode": "HTML"},
                    files={"photo": img},
                    timeout=30,
                )
        else:
            # Text-only message
            url = _api_url("sendMessage")
            resp = requests.post(
                url,
                json={
                    "chat_id": target_chat_id,
                    "text": text,
                    "parse_mode": "HTML",
                    "disable_web_page_preview": True,
                },
                timeout=15,
            )

        if resp.status_code == 200 and resp.json().get("ok"):
            logger.info("Telegram message sent: %.60s", text)
            return True

        logger.warning(
            "Telegram API error %d: %s", resp.status_code, resp.text[:200]
        )
        return False

    except Exception as exc:
        logger.warning("Telegram send failed: %s", exc)
        return False
