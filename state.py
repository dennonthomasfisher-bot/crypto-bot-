"""
Persistent state for dedup tracking and tweet counting.

Stores data as JSON on disk so it survives bot restarts.
"""

import json
import os
import time
import logging
from datetime import datetime, timezone

import config

logger = logging.getLogger(__name__)

_STATE_FILE = os.path.join(os.path.dirname(__file__), ".bot_state.json")

_state: dict = {
    "price_alerts": {},    # { coin_id: { "1h": timestamp, "24h": timestamp } }
    "news_hashes": {},     # { hash: timestamp }
    "tweet_count": 0,      # tweets posted this month
    "tweet_month": "",     # "YYYY-MM" string for current counting period
}


def _current_month() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m")


def load() -> None:
    """Load state from disk. Safe to call multiple times."""
    global _state
    if not os.path.exists(_STATE_FILE):
        return
    try:
        with open(_STATE_FILE, "r") as f:
            data = json.load(f)
        _state.update(data)
        logger.info("Loaded state from %s", _STATE_FILE)
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("Could not load state file, starting fresh: %s", exc)


def save() -> None:
    """Persist current state to disk."""
    try:
        with open(_STATE_FILE, "w") as f:
            json.dump(_state, f, indent=2)
    except OSError as exc:
        logger.warning("Could not save state file: %s", exc)


# ── Price alert cooldowns ────────────────────────────────────────────────────

def price_cooldown_ok(coin_id: str, window: str) -> bool:
    now = time.time()
    last = _state["price_alerts"].get(coin_id, {}).get(window, 0)
    return (now - last) >= config.PRICE_ALERT_COOLDOWN


def record_price_alert(coin_id: str, window: str) -> None:
    _state["price_alerts"].setdefault(coin_id, {})[window] = time.time()
    save()


# ── News dedup ───────────────────────────────────────────────────────────────

def news_already_posted(story_hash: str) -> bool:
    return story_hash in _state["news_hashes"]


def record_news_posted(story_hash: str) -> None:
    _state["news_hashes"][story_hash] = time.time()
    save()


def prune_old_news_hashes() -> None:
    cutoff = time.time() - config.NEWS_DEDUP_WINDOW
    to_delete = [h for h, ts in _state["news_hashes"].items() if ts < cutoff]
    for h in to_delete:
        del _state["news_hashes"][h]
    if to_delete:
        save()


# ── Tweet counter ────────────────────────────────────────────────────────────

MONTHLY_TWEET_LIMIT = 1500  # Twitter free tier

def record_tweet() -> None:
    month = _current_month()
    if _state["tweet_month"] != month:
        _state["tweet_month"] = month
        _state["tweet_count"] = 0
    _state["tweet_count"] += 1
    save()


def tweets_remaining() -> int:
    month = _current_month()
    if _state["tweet_month"] != month:
        return MONTHLY_TWEET_LIMIT
    return max(0, MONTHLY_TWEET_LIMIT - _state["tweet_count"])


def can_tweet() -> bool:
    return tweets_remaining() > 0
