"""
state.py – Persistent bot state (monthly tweet counter + daily type counts).

Tracks monthly tweet count against the 1,500/month Twitter free-tier cap,
and per-type daily counts that reset at UK midnight.
State is persisted to .bot_state.json so restarts don't reset the counter.
"""
from __future__ import annotations

import datetime
import json
import logging
import os
from zoneinfo import ZoneInfo

_LONDON_TZ = ZoneInfo("Europe/London")

logger = logging.getLogger(__name__)

_STATE_FILE = os.path.join(os.path.dirname(__file__), ".bot_state.json")
MONTHLY_TWEET_CAP = 1500

_state: dict = {}


def _load() -> None:
    global _state
    try:
        with open(_STATE_FILE) as f:
            _state = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        _state = {}


def _save() -> None:
    try:
        with open(_STATE_FILE, "w") as f:
            json.dump(_state, f)
    except OSError as exc:
        logger.warning("Could not save bot state: %s", exc)


def _month_key() -> str:
    return datetime.date.today().strftime("%Y-%m")


def _day_key_uk() -> str:
    """Return today's date string in UK/London timezone (YYYY-MM-DD)."""
    return datetime.datetime.now(_LONDON_TZ).strftime("%Y-%m-%d")


def can_tweet() -> bool:
    """Return True if we're still under the 1,500 tweet/month cap."""
    _load()
    count = _state.get(_month_key(), 0)
    return count < MONTHLY_TWEET_CAP


def record_tweet(count: int = 1) -> None:
    """Increment the monthly tweet counter and persist."""
    _load()
    key = _month_key()
    _state[key] = _state.get(key, 0) + max(1, count)
    _save()
    logger.debug("Tweet recorded. Monthly total: %d", _state[key])


def record_content_category(category: str) -> None:
    """Track content category for variety enforcement."""
    _load()
    cats = _state.get("recent_categories", [])
    cats.append(category)
    _state["recent_categories"] = cats[-20:]  # keep last 20
    _save()


def get_recent_content_categories(n: int = 6) -> list[str]:
    """Return the last n content categories posted."""
    _load()
    return _state.get("recent_categories", [])[-n:]


def record_quote_style(style: str) -> None:
    """Track last quote tweet template style."""
    _load()
    _state["last_quote_style"] = style
    _save()


def get_last_quote_style() -> str:
    """Return last quote tweet template style, or empty string."""
    _load()
    return _state.get("last_quote_style", "")


# ── Daily counts (resets at UK midnight) ─────────────────────────────────────

def get_daily_count(tweet_type: str) -> int:
    """Return today's post count for a given tweet type (UK date)."""
    _load()
    day = _day_key_uk()
    return _state.get("daily_counts", {}).get(day, {}).get(tweet_type, 0)


def get_total_daily_tweets() -> int:
    """Return total tweets posted today (UK date)."""
    _load()
    day = _day_key_uk()
    return _state.get("daily_counts", {}).get(day, {}).get("_total", 0)


def get_thread_topic_index() -> int:
    """Return the persisted evening thread topic index."""
    _load()
    return _state.get("thread_topic_index", 0)


def set_thread_topic_index(index: int) -> None:
    """Persist the evening thread topic index."""
    _load()
    _state["thread_topic_index"] = index
    _save()


def get_replied_ids() -> set[str]:
    """Return the set of tweet IDs we have already replied to."""
    _load()
    return set(_state.get("replied_ids", []))


def add_replied_id(tweet_id: str) -> None:
    """Record a tweet ID as replied-to and persist. Keeps last 500."""
    _load()
    ids = _state.get("replied_ids", [])
    if tweet_id not in ids:
        ids.append(tweet_id)
    _state["replied_ids"] = ids[-500:]
    _save()


def increment_daily_count(tweet_type: str, amount: int = 1) -> None:
    """Increment today's count for tweet_type and the daily total."""
    _load()
    day = _day_key_uk()
    # Prune any stale day entries (keep only today)
    _state["daily_counts"] = {day: _state.get("daily_counts", {}).get(day, {})}
    counts = _state["daily_counts"][day]
    counts[tweet_type] = counts.get(tweet_type, 0) + amount
    counts["_total"] = counts.get("_total", 0) + amount
    _save()
