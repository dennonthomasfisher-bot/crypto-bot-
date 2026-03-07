"""
bot_state.py – thin JSON persistence layer for cross-restart state.

Keys managed here:
  posting:date          – ISO date string, used by the old posting counter
  posting:count         – daily post count
  posting:next_allowed  – unix timestamp for rate-limiting
  recap:headlines       – list of {title, ts} used by the morning recap
"""

import json
import logging
import os
import time

logger = logging.getLogger(__name__)

_STATE_PATH = os.path.join(os.path.dirname(__file__), "bot_state.json")

# Keep headlines for 48 h so the recap always has content even if the bot
# was quiet overnight.
_HEADLINE_TTL = 48 * 3600


def _load() -> dict:
    try:
        with open(_STATE_PATH) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _save(state: dict) -> None:
    try:
        with open(_STATE_PATH, "w") as f:
            json.dump(state, f, indent=2)
    except OSError as exc:
        logger.warning("Could not save bot state: %s", exc)


def record_headline(title: str) -> None:
    """Append a posted story title; prune entries older than 48 h."""
    state = _load()
    headlines = state.get("recap:headlines", [])
    cutoff = time.time() - _HEADLINE_TTL
    headlines = [h for h in headlines if h.get("ts", 0) >= cutoff]
    headlines.append({"title": title, "ts": time.time()})
    state["recap:headlines"] = headlines
    _save(state)


def get_recent_headlines(hours: int = 24) -> list[str]:
    """Return titles posted within the last *hours* hours, newest first."""
    state = _load()
    cutoff = time.time() - hours * 3600
    titles = [
        h["title"]
        for h in state.get("recap:headlines", [])
        if h.get("ts", 0) >= cutoff and h.get("title")
    ]
    return list(reversed(titles))
