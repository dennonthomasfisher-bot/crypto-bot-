"""
Reply sentiment analyzer – analyzes replies to our tweets to understand
audience sentiment and engagement quality.

Uses keyword-based sentiment scoring (no external API needed).
Stores insights in .reply_sentiment.json.
"""
from __future__ import annotations

import json
import logging
import os
import time

logger = logging.getLogger(__name__)

_DATA_FILE = os.path.join(os.path.dirname(__file__), ".reply_sentiment.json")

_data: dict = {
    "analyzed_tweets": [],   # [{ tweet_id, sentiment_score, reply_count, bullish, bearish, neutral }]
    "overall": {
        "total_replies_analyzed": 0,
        "avg_sentiment": 0.0,
        "bullish_pct": 0.0,
        "bearish_pct": 0.0,
    },
}

# Sentiment keyword lists
_BULLISH_WORDS = {
    "bull", "bullish", "moon", "pump", "buy", "buying", "long", "calls",
    "breakout", "ath", "accumulate", "accumulating", "loading", "load",
    "up", "green", "send", "rip", "higher", "rally", "run", "boom",
    "agree", "exactly", "facts", "yes", "right", "based", "alpha",
    "great", "nice", "solid", "strong", "good", "love",
}

_BEARISH_WORDS = {
    "bear", "bearish", "dump", "sell", "selling", "short", "puts",
    "crash", "scam", "rug", "down", "red", "rekt", "dead",
    "overvalued", "bubble", "top", "careful", "drop", "fade",
    "wrong", "disagree", "no", "nah", "bad", "weak", "garbage",
    "ponzi", "trap", "fake", "manipulation",
}


def load() -> None:
    global _data
    if not os.path.exists(_DATA_FILE):
        return
    try:
        with open(_DATA_FILE) as f:
            _data.update(json.load(f))
        logger.info("Loaded reply sentiment data (%d tweets analyzed)",
                     len(_data["analyzed_tweets"]))
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("Could not load reply sentiment data: %s", exc)


def save() -> None:
    try:
        with open(_DATA_FILE, "w") as f:
            json.dump(_data, f, indent=2)
    except OSError as exc:
        logger.warning("Could not save reply sentiment data: %s", exc)


def _score_reply(text: str) -> dict:
    """Score a single reply for sentiment. Returns { score, bullish, bearish }."""
    words = set(text.lower().split())
    bull_hits = len(words & _BULLISH_WORDS)
    bear_hits = len(words & _BEARISH_WORDS)

    if bull_hits > bear_hits:
        return {"score": 1, "bullish": True, "bearish": False}
    elif bear_hits > bull_hits:
        return {"score": -1, "bullish": False, "bearish": True}
    return {"score": 0, "bullish": False, "bearish": False}


def analyze_replies(client, tweet_id: str) -> dict | None:
    """
    Fetch and analyze replies to a specific tweet.

    Returns { tweet_id, reply_count, sentiment_score, bullish, bearish, neutral }
    or None if no replies found.
    """
    try:
        # Search for replies to this tweet
        response = client.search_recent_tweets(
            query=f"conversation_id:{tweet_id}",
            max_results=50,
            tweet_fields=["text", "author_id"],
        )
    except Exception as exc:
        logger.debug("Failed to fetch replies for %s: %s", tweet_id, exc)
        return None

    if not response.data:
        return None

    replies = response.data
    scores = [_score_reply(r.text) for r in replies]
    total = len(scores)
    bullish = sum(1 for s in scores if s["bullish"])
    bearish = sum(1 for s in scores if s["bearish"])
    neutral = total - bullish - bearish
    avg_score = sum(s["score"] for s in scores) / total if total > 0 else 0

    result = {
        "tweet_id": tweet_id,
        "reply_count": total,
        "sentiment_score": round(avg_score, 2),
        "bullish": bullish,
        "bearish": bearish,
        "neutral": neutral,
        "bullish_pct": round(bullish / total * 100, 1) if total > 0 else 0,
        "bearish_pct": round(bearish / total * 100, 1) if total > 0 else 0,
        "timestamp": time.time(),
    }

    # Store result
    _data["analyzed_tweets"].append(result)
    if len(_data["analyzed_tweets"]) > 200:
        _data["analyzed_tweets"] = _data["analyzed_tweets"][-200:]

    # Update overall stats
    _update_overall()
    save()

    return result


def _update_overall() -> None:
    """Recalculate overall sentiment stats."""
    records = _data["analyzed_tweets"]
    if not records:
        return

    total_replies = sum(r["reply_count"] for r in records)
    total_bullish = sum(r["bullish"] for r in records)
    total_bearish = sum(r["bearish"] for r in records)
    avg_sent = sum(r["sentiment_score"] for r in records) / len(records)

    _data["overall"] = {
        "total_replies_analyzed": total_replies,
        "avg_sentiment": round(avg_sent, 2),
        "bullish_pct": round(total_bullish / total_replies * 100, 1) if total_replies > 0 else 0,
        "bearish_pct": round(total_bearish / total_replies * 100, 1) if total_replies > 0 else 0,
    }


def analyze_recent_tweets(client, tweet_ids: list[str]) -> int:
    """
    Analyze replies for a batch of recent tweet IDs.
    Returns number of tweets analyzed.
    """
    # Skip tweets we already analyzed
    analyzed_ids = {r["tweet_id"] for r in _data["analyzed_tweets"]}
    new_ids = [tid for tid in tweet_ids if tid not in analyzed_ids]

    count = 0
    for tweet_id in new_ids[:10]:  # max 10 per batch
        result = analyze_replies(client, tweet_id)
        if result:
            count += 1
            if result["reply_count"] >= 3:
                sentiment = "bullish" if result["sentiment_score"] > 0.2 else "bearish" if result["sentiment_score"] < -0.2 else "neutral"
                logger.info(
                    "Reply sentiment for %s: %s (score=%.2f, %d replies, %d bull/%d bear)",
                    tweet_id, sentiment, result["sentiment_score"],
                    result["reply_count"], result["bullish"], result["bearish"],
                )
    return count


def get_audience_mood() -> str:
    """
    Return a one-line audience mood summary for AI prompt injection.
    """
    overall = _data.get("overall", {})
    total = overall.get("total_replies_analyzed", 0)
    if total < 10:
        return ""
    avg = overall.get("avg_sentiment", 0)
    bull_pct = overall.get("bullish_pct", 0)

    if avg > 0.2:
        return f"Your audience is currently bullish ({bull_pct:.0f}% bullish replies)."
    elif avg < -0.2:
        return f"Your audience is currently bearish ({100 - bull_pct:.0f}% bearish replies)."
    return f"Your audience is mixed — {bull_pct:.0f}% bullish, balanced sentiment."
