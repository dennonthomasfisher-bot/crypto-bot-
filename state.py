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


def coin_global_cooldown_ok(coin_id: str) -> bool:
    """Return True if no alert (any window/source) was posted for this coin recently."""
    now = time.time()
    windows = _state["price_alerts"].get(coin_id, {})
    if not windows:
        return True
    most_recent = max(windows.values())
    return (now - most_recent) >= config.COIN_GLOBAL_COOLDOWN


def record_price_alert(coin_id: str, window: str) -> None:
    _state["price_alerts"].setdefault(coin_id, {})[window] = time.time()
    save()


def record_coin_alert(coin_id: str) -> None:
    """Record that we tweeted about a coin (from any source, e.g. CMC spotlight)."""
    _state["price_alerts"].setdefault(coin_id, {})["global"] = time.time()
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


# ── Last run timestamp (prevents tweet spam on rapid restarts) ────────────

def get_last_run_time() -> float:
    """Return the timestamp of the last bot run, or 0 if never."""
    return _state.get("last_run_time", 0)


def record_last_run_time() -> None:
    """Record the current time as the last run time."""
    _state["last_run_time"] = time.time()
    save()


# ── Tweet variety tracking (prevents repeating the same style) ──────────────

def get_last_quote_style() -> str:
    """Return the name of the last quote tweet generator used."""
    return _state.get("last_quote_style", "")


def record_quote_style(style_name: str) -> None:
    """Record which quote tweet style was just used."""
    _state["last_quote_style"] = style_name
    save()


# ── Content category tracking (prevents same topic dominating feed) ──────

_MAX_CATEGORY_HISTORY = 6


def get_recent_categories() -> list[str]:
    """Return the last N content categories posted."""
    return _state.get("content_categories", [])


def record_content_category(category: str) -> None:
    """Record the content category of a tweet that was just posted."""
    cats = _state.setdefault("content_categories", [])
    cats.append(category)
    if len(cats) > _MAX_CATEGORY_HISTORY:
        _state["content_categories"] = cats[-_MAX_CATEGORY_HISTORY:]
    save()


# ── Fear & Greed dedup (persisted across restarts) ──────────────────────

def get_fear_greed_last_posted_ts() -> float:
    """Return timestamp of last Fear & Greed post, or 0."""
    return _state.get("fear_greed_last_ts", 0)


def get_fear_greed_last_value() -> int | None:
    """Return the last posted Fear & Greed value, or None."""
    return _state.get("fear_greed_last_value")


def record_fear_greed_posted(value: int) -> None:
    """Record that a Fear & Greed tweet was posted."""
    _state["fear_greed_last_ts"] = time.time()
    _state["fear_greed_last_value"] = value
    save()


# ── Sentiment tracking (prevents one-sided feed) ─────────────────────────

_BEARISH_WORDS = {"capitulation", "bleeding", "risk-off", "fear", "dump",
                  "crash", "plunge", "selloff", "sell-off", "weakness",
                  "breakdown", "bearish", "lower", "danger", "ugly"}
_BULLISH_WORDS = {"accumulation", "reversal", "support holding", "opportunity",
                  "undervalued", "breakout", "rally", "bullish", "bounce",
                  "recovery", "higher", "strength", "upside", "buying"}
_MAX_SENTIMENT_HISTORY = 5


def _classify_sentiment(text: str) -> str:
    """Classify tweet text as bullish, bearish, or neutral."""
    lower = text.lower()
    bear_hits = sum(1 for w in _BEARISH_WORDS if w in lower)
    bull_hits = sum(1 for w in _BULLISH_WORDS if w in lower)
    if bear_hits > bull_hits and bear_hits >= 1:
        return "bearish"
    if bull_hits > bear_hits and bull_hits >= 1:
        return "bullish"
    return "neutral"


def record_sentiment(text: str) -> None:
    """Classify and record the sentiment of a posted tweet."""
    sentiment = _classify_sentiment(text)
    history = _state.setdefault("sentiment_history", [])
    history.append(sentiment)
    if len(history) > _MAX_SENTIMENT_HISTORY:
        _state["sentiment_history"] = history[-_MAX_SENTIMENT_HISTORY:]
    save()


def get_recent_sentiments() -> list[str]:
    """Return the last N sentiment labels (bullish/bearish/neutral)."""
    return _state.get("sentiment_history", [])


# ── Cross-source coin dedup (prevents same coin from multiple sources) ────

def record_coins_mentioned(coins: list[str]) -> None:
    """Record coin symbols mentioned in a tweet, with timestamp."""
    mentions = _state.setdefault("coins_mentioned", {})
    now = time.time()
    for coin in coins:
        mentions[coin.upper()] = now
    save()


def get_recently_mentioned_coins(hours: float = 2) -> set[str]:
    """Return coin symbols mentioned within the last N hours."""
    mentions = _state.get("coins_mentioned", {})
    cutoff = time.time() - (hours * 3600)
    return {sym for sym, ts in mentions.items() if ts >= cutoff}
