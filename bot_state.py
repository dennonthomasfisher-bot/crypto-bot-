"""
bot_state.py – thin JSON persistence layer for cross-restart state.

Keys managed here:
  posting:date          – ISO date string, used by the old posting counter
  posting:count         – daily post count
  posting:next_allowed  – unix timestamp for rate-limiting
  recap:headlines       – list of {title, ts} used by the morning recap
"""

import datetime
import json
import logging
import os
import time

logger = logging.getLogger(__name__)

_STATE_PATH = os.path.join(os.path.dirname(__file__), "bot_state.json")

# Keep headlines for 48 h so the recap always has content even if the bot
# was quiet overnight.
_HEADLINE_TTL = 48 * 3600

# Keep replied tweet IDs for 30 days to avoid re-replying after restarts
_REPLY_TTL = 30 * 24 * 3600


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


def get_replied_ids() -> set[str]:
    """Return the set of tweet IDs the bot has already replied to (within 30 days)."""
    state = _load()
    cutoff = time.time() - _REPLY_TTL
    return {
        e["id"]
        for e in state.get("autoreplies:replied", [])
        if e.get("ts", 0) >= cutoff
    }


def record_reply(tweet_id: str) -> None:
    """Persist a replied tweet ID and increment today's auto-reply counter."""
    state = _load()

    # Persist the ID (prune old entries first)
    cutoff = time.time() - _REPLY_TTL
    entries = [e for e in state.get("autoreplies:replied", []) if e.get("ts", 0) >= cutoff]
    entries.append({"id": tweet_id, "ts": time.time()})
    state["autoreplies:replied"] = entries

    # Increment daily count (reset if it's a new day)
    today_str = datetime.date.today().isoformat()
    if state.get("autoreplies:date") != today_str:
        state["autoreplies:date"] = today_str
        state["autoreplies:count"] = 0
    state["autoreplies:count"] = state.get("autoreplies:count", 0) + 1

    _save(state)


def get_auto_reply_count_today() -> int:
    """Return the number of auto-replies already posted today."""
    state = _load()
    today_str = datetime.date.today().isoformat()
    if state.get("autoreplies:date") != today_str:
        return 0
    return state.get("autoreplies:count", 0)


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
