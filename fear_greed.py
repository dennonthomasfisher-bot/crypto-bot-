"""
Fear & Greed Index monitor – polls the alternative.me API (free, no key needed).

Posts the current index value with historical comparison.
API docs: https://alternative.me/crypto/fear-and-greed-index/
"""
from __future__ import annotations

import logging
import time

import requests

import ai_writer
import chart_generator
import state

logger = logging.getLogger(__name__)

_API_URL = "https://api.alternative.me/fng/"

# Minimum gap between Fear & Greed posts (6 hours)
_MIN_POST_GAP = 6 * 3600


def fetch_fear_greed() -> dict | None:
    """
    Fetch the current Fear & Greed Index.
    Returns dict with keys: value, value_classification, timestamp
    Also fetches yesterday's value for comparison.
    """
    for attempt in range(3):
        try:
            resp = requests.get(
                _API_URL,
                params={"limit": 2, "format": "json"},
                timeout=15,
            )
            if resp.status_code == 429:
                time.sleep(2 ** (attempt + 1))
                continue
            resp.raise_for_status()
            data = resp.json().get("data", [])
            if not data:
                return None
            result = {
                "value": int(data[0]["value"]),
                "classification": data[0]["value_classification"],
                "timestamp": int(data[0]["timestamp"]),
            }
            if len(data) > 1:
                result["yesterday_value"] = int(data[1]["value"])
                result["yesterday_classification"] = data[1]["value_classification"]
            return result
        except (requests.RequestException, KeyError, ValueError) as exc:
            logger.warning("Fear & Greed fetch failed (attempt %d/3): %s", attempt + 1, exc)
            if attempt < 2:
                time.sleep(2 ** (attempt + 1))
    return None


def _classification_emoji(classification: str) -> str:
    """Map classification to emoji."""
    mapping = {
        "Extreme Fear": "😱",
        "Fear": "😨",
        "Neutral": "😐",
        "Greed": "🤑",
        "Extreme Greed": "🤩",
    }
    return mapping.get(classification, "📊")


def _value_bar(value: int) -> str:
    """Create a simple visual bar for the index value."""
    filled = value // 10
    empty = 10 - filled
    return "█" * filled + "░" * empty


def should_post(value: int) -> bool:
    """Check if we should post (avoid duplicate/too-frequent posts).

    Guards:
      1. Time-based: at least 6 hours since last Fear & Greed post (persisted to disk).
      2. Value-based: skip if exact same value was posted last time.
    """

    # Check persistent cooldown (survives restarts)
    last_ts = state.get_fear_greed_last_posted_ts()
    if last_ts and (time.time() - last_ts) < _MIN_POST_GAP:
        logger.info("Fear & Greed cooldown active (%.0f min remaining), skipping.",
                     (_MIN_POST_GAP - (time.time() - last_ts)) / 60)
        return False

    last_val = state.get_fear_greed_last_value()
    if last_val is not None and last_val == value:
        return False

    return True


def record_posted(data: dict) -> None:
    """Record that a Fear & Greed tweet was posted (persisted to disk)."""
    state.record_fear_greed_posted(data["value"])


def format_fear_greed_tweet(data: dict) -> tuple[str, str | None] | None:
    """Generate a Fear & Greed Index tweet and gauge chart image.

    Returns (tweet_text, img_path) on success, or None on failure.
    img_path may be None if chart generation fails.
    """
    value = data["value"]
    classification = data["classification"]
    yesterday = data.get("yesterday_value")

    # Try AI first
    if ai_writer.is_available():
        change_str = ""
        if yesterday is not None:
            diff = value - yesterday
            change_str = f"Yesterday: {yesterday} ({data.get('yesterday_classification', '')}). Change: {diff:+d} points."

        prompt = f"""Write a tweet about the Crypto Fear & Greed Index.

Current reading: {value}/100 — {classification}
{change_str}

Rules:
- Lead with the Fear & Greed number and classification
- State what it means for the market — declarative, no hedging ('could', 'might', 'may')
- If extreme (below 25 or above 75), make the contrarian call directly (e.g. "Historically this is where BTC bottoms" — not "this might be")
- No questions
- No hashtags
- Emojis only from: 📉 🚀 ⚡ 👀
- Hard cap: 220 characters
- No disclaimers or NFA
Write the tweet now. Nothing else."""

        system = """You are @CoinWatchAlert. You read the Fear & Greed Index as a contrarian signal and make direct, conviction-based calls. Analyst tone — declarative, no hedging, no questions, no hashtags."""

        tweet = ai_writer._call_claude(system, prompt)
        if tweet and len(tweet) <= 220:
            img_path = chart_generator.generate_fear_greed_gauge(value, classification)
            return tweet, img_path

    # Template fallback
    if yesterday is not None:
        diff = value - yesterday
        direction = "up" if diff > 0 else "down" if diff < 0 else "unchanged"
        change_line = f"\nYesterday: {yesterday} ({direction} {abs(diff)} pts)" if diff != 0 else ""
    else:
        change_line = ""

    if value <= 20:
        insight = "\nHistorically, extreme fear marks BTC cycle lows. 🚀"
    elif value <= 30:
        insight = "\nFear this deep has preceded every major BTC recovery. 🚀"
    elif value >= 80:
        insight = "\nExtreme greed precedes corrections. Watch your exposure. 📉"
    elif value >= 70:
        insight = "\nGreed building. Overextension risk is real. 👀"
    else:
        insight = ""

    tweet = f"Fear & Greed: {value}/100 — {classification}{change_line}{insight}"
    if len(tweet) > 220:
        tweet = tweet[:217].rsplit(" ", 1)[0] + "…"
    img_path = chart_generator.generate_fear_greed_gauge(value, classification)
    return tweet, img_path
