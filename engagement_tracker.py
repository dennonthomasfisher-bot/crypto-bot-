"""
Engagement analytics – tracks tweet performance and feeds insights
back into AI prompts for better content.

Stores metrics in .engagement.json and provides summaries for the AI writer.
"""
from __future__ import annotations

import json
import logging
import os
import time

logger = logging.getLogger(__name__)

_DATA_FILE = os.path.join(os.path.dirname(__file__), ".engagement.json")
_MAX_RECORDS = 200  # keep last 200 tweets

_data: dict = {
    "tweets": [],       # list of { id, text, type, posted_at, likes, retweets, replies, impressions }
    "best_performers": {
        "most_liked": None,
        "most_retweeted": None,
    },
}


def load() -> None:
    """Load engagement data from disk."""
    global _data
    if not os.path.exists(_DATA_FILE):
        return
    try:
        with open(_DATA_FILE) as f:
            _data.update(json.load(f))
        logger.info("Loaded engagement data (%d tweets tracked)", len(_data["tweets"]))
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("Could not load engagement data: %s", exc)


def save() -> None:
    """Persist engagement data to disk."""
    try:
        with open(_DATA_FILE, "w") as f:
            json.dump(_data, f, indent=2)
    except OSError as exc:
        logger.warning("Could not save engagement data: %s", exc)


def record_tweet(tweet_id: str, text: str, tweet_type: str) -> None:
    """Record a newly posted tweet for tracking."""
    _data["tweets"].append({
        "id": tweet_id,
        "text": text[:200],
        "type": tweet_type,
        "posted_at": time.time(),
        "likes": 0,
        "retweets": 0,
        "replies": 0,
        "impressions": 0,
        "checked": False,
    })
    # Prune old records
    if len(_data["tweets"]) > _MAX_RECORDS:
        _data["tweets"] = _data["tweets"][-_MAX_RECORDS:]
    save()


def update_metrics(client) -> int:
    """
    Fetch engagement metrics for recent tweets from Twitter API.
    Returns number of tweets updated.
    """
    # Only check tweets posted in the last 48 hours that haven't been fully checked
    cutoff = time.time() - 48 * 3600
    to_check = [
        t for t in _data["tweets"]
        if t["posted_at"] > cutoff and not t.get("checked", False)
    ]

    if not to_check:
        return 0

    # Batch lookup by IDs (Twitter API v2 supports up to 100)
    tweet_ids = [t["id"] for t in to_check[:100]]

    try:
        response = client.get_tweets(
            ids=tweet_ids,
            tweet_fields=["public_metrics"],
        )
    except Exception as exc:
        logger.warning("Failed to fetch tweet metrics: %s", exc)
        return 0

    if not response.data:
        return 0

    updated = 0
    metrics_map = {str(t.id): t.data.get("public_metrics", {}) for t in response.data}

    for record in _data["tweets"]:
        if record["id"] in metrics_map:
            m = metrics_map[record["id"]]
            record["likes"] = m.get("like_count", 0)
            record["retweets"] = m.get("retweet_count", 0)
            record["replies"] = m.get("reply_count", 0)
            record["impressions"] = m.get("impression_count", 0)
            # Mark as checked if posted > 24h ago (final metrics)
            if record["posted_at"] < time.time() - 24 * 3600:
                record["checked"] = True
            updated += 1

    if updated:
        _update_best_performers()
        save()
        logger.info("Updated engagement metrics for %d tweets", updated)

    return updated


def _update_best_performers() -> None:
    """Recalculate best performing tweets."""
    if not _data["tweets"]:
        return

    checked = [t for t in _data["tweets"] if t.get("likes", 0) > 0 or t.get("retweets", 0) > 0]
    if not checked:
        return

    most_liked = max(checked, key=lambda t: t.get("likes", 0))
    most_rt = max(checked, key=lambda t: t.get("retweets", 0))

    _data["best_performers"] = {
        "most_liked": {
            "text": most_liked["text"],
            "likes": most_liked["likes"],
            "type": most_liked.get("type", "unknown"),
        },
        "most_retweeted": {
            "text": most_rt["text"],
            "retweets": most_rt["retweets"],
            "type": most_rt.get("type", "unknown"),
        },
    }


def get_performance_summary() -> str:
    """
    Get a text summary of recent engagement for AI prompt injection.
    Returns empty string if not enough data.
    """
    checked = [t for t in _data["tweets"] if t.get("checked")]
    if len(checked) < 5:
        return ""

    # Calculate averages by type
    type_stats: dict[str, dict] = {}
    for t in checked[-50:]:  # last 50 checked tweets
        tt = t.get("type", "unknown")
        if tt not in type_stats:
            type_stats[tt] = {"count": 0, "likes": 0, "retweets": 0}
        type_stats[tt]["count"] += 1
        type_stats[tt]["likes"] += t.get("likes", 0)
        type_stats[tt]["retweets"] += t.get("retweets", 0)

    lines = ["Recent performance:"]
    for tt, stats in sorted(type_stats.items(), key=lambda x: x[1]["likes"], reverse=True):
        avg_likes = stats["likes"] / stats["count"]
        avg_rt = stats["retweets"] / stats["count"]
        lines.append(f"  {tt}: avg {avg_likes:.0f} likes, {avg_rt:.0f} RTs ({stats['count']} tweets)")

    bp = _data.get("best_performers", {})
    if bp.get("most_liked"):
        lines.append(f"  Best tweet ({bp['most_liked']['likes']} likes): \"{bp['most_liked']['text'][:80]}...\"")

    return "\n".join(lines)


def get_best_posting_hours() -> list[int]:
    """
    Analyze which hours get the most engagement.
    Returns list of top 3 hours (0-23 UTC) sorted by avg engagement.
    """
    checked = [t for t in _data["tweets"] if t.get("checked")]
    if len(checked) < 10:
        return []

    hour_stats: dict[int, dict] = {}
    for t in checked:
        from datetime import datetime, timezone
        hour = datetime.fromtimestamp(t["posted_at"], tz=timezone.utc).hour
        if hour not in hour_stats:
            hour_stats[hour] = {"count": 0, "engagement": 0}
        hour_stats[hour]["count"] += 1
        hour_stats[hour]["engagement"] += t.get("likes", 0) + t.get("retweets", 0) * 2

    # Average engagement per hour
    hour_avgs = {
        h: s["engagement"] / s["count"]
        for h, s in hour_stats.items()
        if s["count"] >= 2  # need at least 2 data points
    }

    if not hour_avgs:
        return []

    return sorted(hour_avgs, key=hour_avgs.get, reverse=True)[:3]
