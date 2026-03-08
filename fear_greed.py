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

logger = logging.getLogger(__name__)

_API_URL = "https://api.alternative.me/fng/"

# Cache last value to avoid duplicate posts
_last_posted_value: int | None = None


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


def should_post(data: dict) -> bool:
    """Check if we should post (avoid duplicate consecutive posts)."""
    global _last_posted_value
    value = data["value"]
    if _last_posted_value == value:
        return False
    _last_posted_value = value
    return True


def format_fear_greed_tweet(data: dict) -> str | None:
    """Generate a Fear & Greed Index tweet."""
    value = data["value"]
    classification = data["classification"]
    emoji = _classification_emoji(classification)
    bar = _value_bar(value)
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

The tweet should:
- Lead with the Fear & Greed reading
- Interpret what it means for the market right now
- If it's extreme (below 25 or above 75), note that historically these are contrarian signals
- Include the number and classification
- Sound like a trader reading the room, not reporting news
- Under 270 characters
- NO hashtags
{ai_writer._get_recent_context()}
Write the tweet now. Nothing else."""

        system = """You are @CoinWatchAlert. You track market sentiment closely. The Fear & Greed Index is one of your favorite tools — you use it to gauge when the crowd is wrong. No hashtags, no filler."""

        tweet = ai_writer._call_claude(system, prompt)
        if tweet and len(tweet) <= 280:
            return tweet

    # Template fallback
    lines = [
        f"{emoji} Crypto Fear & Greed Index: {value}/100",
        f"{bar}",
        f"Reading: {classification}",
    ]

    if yesterday is not None:
        diff = value - yesterday
        direction = "up" if diff > 0 else "down" if diff < 0 else "unchanged"
        if diff != 0:
            lines.append(f"Yesterday: {yesterday} ({direction} {abs(diff)} pts)")

    # Add contrarian insight at extremes
    if value <= 20:
        lines.extend(["", "Historically, extreme fear = buying opportunity."])
    elif value <= 30:
        lines.extend(["", "Fear in the market. Smart money often buys here."])
    elif value >= 80:
        lines.extend(["", "Extreme greed. Historically a time for caution."])
    elif value >= 70:
        lines.extend(["", "Greed building. Watch for overextension."])

    tweet = "\n".join(lines)
    if len(tweet) > 280:
        tweet = tweet[:277].rsplit("\n", 1)[0] + "..."
    return tweet
