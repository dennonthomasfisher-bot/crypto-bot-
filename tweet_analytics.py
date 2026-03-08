#!/usr/bin/env python3
"""
Tweet analytics – fetches recent tweets, ranks by engagement rate,
categorizes by type, and saves a detailed report to analytics_report.json.

Runnable standalone: python tweet_analytics.py
"""
from __future__ import annotations

import json
import logging
import os
import re
import time
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

_REPORT_FILE = os.path.join(os.path.dirname(__file__), "analytics_report.json")

# ── Tweet type classification ────────────────────────────────────────────────

_TYPE_PATTERNS: list[tuple[str, re.Pattern | None, callable | None]] = [
    # Order matters — first match wins
    ("thread",     None, lambda t: t.get("referenced_tweets") and
                         any(r["type"] == "replied_to" for r in t.get("referenced_tweets", []))),
    ("quote_tweet", None, lambda t: t.get("referenced_tweets") and
                          any(r["type"] == "quoted" for r in t.get("referenced_tweets", []))),
    ("question",   re.compile(r'\?\s*$', re.MULTILINE), None),
    ("data_dump",  re.compile(r'(→.*→|📊|market check|recap)', re.IGNORECASE), None),
    ("hot_take",   re.compile(r'(unpopular|contrarian|hot take|disagree|controversial|fight me)',
                              re.IGNORECASE), None),
    ("opinion",    re.compile(r'(i think|i believe|my take|imo|honestly|personally|'
                              r'if you ask me|not convinced|overrated|underrated)',
                              re.IGNORECASE), None),
]


def classify_tweet(tweet_data: dict, text: str) -> str:
    """Classify a tweet into a type based on content and metadata."""
    for type_name, pattern, func in _TYPE_PATTERNS:
        if func and func(tweet_data):
            return type_name
        if pattern and pattern.search(text):
            return type_name
    return "general"


# ── Engagement calculation ───────────────────────────────────────────────────

def _engagement_rate(metrics: dict) -> float:
    """Calculate engagement rate: (likes + retweets + replies) / impressions."""
    impressions = metrics.get("impression_count", 0)
    if impressions == 0:
        return 0.0
    engagement = (
        metrics.get("like_count", 0) +
        metrics.get("retweet_count", 0) +
        metrics.get("reply_count", 0)
    )
    return engagement / impressions


def _best_posting_hour(tweets: list[dict]) -> int | None:
    """Find the hour (0-23 UTC) with the highest average engagement rate."""
    hour_stats: dict[int, list[float]] = {}
    for t in tweets:
        created = t.get("created_at", "")
        if not created:
            continue
        try:
            dt = datetime.fromisoformat(created.replace("Z", "+00:00"))
            hour = dt.hour
        except (ValueError, AttributeError):
            continue
        rate = _engagement_rate(t.get("public_metrics", {}))
        hour_stats.setdefault(hour, []).append(rate)

    if not hour_stats:
        return None

    # Only consider hours with 2+ tweets
    avg_by_hour = {
        h: sum(rates) / len(rates)
        for h, rates in hour_stats.items()
        if len(rates) >= 2
    }
    if not avg_by_hour:
        # Fall back to any hour
        avg_by_hour = {
            h: sum(rates) / len(rates) for h, rates in hour_stats.items()
        }
    return max(avg_by_hour, key=avg_by_hour.get)


# ── Main analytics function ──────────────────────────────────────────────────

def run_analytics(client=None) -> dict | None:
    """
    Fetch last 50 tweets, analyze engagement, categorize, and save report.
    Returns the report dict or None on failure.

    If client is None, creates one from twitter_client.
    """
    if client is None:
        import twitter_client
        client = twitter_client.get_client()

    # Fetch our user ID
    try:
        me = client.get_me()
        if not me.data:
            logger.warning("Could not fetch own user ID for analytics")
            return None
        user_id = me.data.id
    except Exception as exc:
        logger.warning("Failed to get user ID: %s", exc)
        return None

    # Fetch last 50 tweets with metrics
    try:
        response = client.get_users_tweets(
            id=user_id,
            max_results=50,
            tweet_fields=["public_metrics", "created_at", "referenced_tweets"],
            exclude=["replies"],  # only original tweets + quotes
        )
    except Exception as exc:
        logger.warning("Failed to fetch tweets for analytics: %s", exc)
        return None

    if not response.data:
        logger.info("No tweets found for analytics")
        return None

    # Process each tweet
    analyzed: list[dict] = []
    for tweet in response.data:
        text = tweet.text or ""
        metrics = tweet.data.get("public_metrics", {})
        rate = _engagement_rate(metrics)
        tweet_type = classify_tweet(tweet.data, text)

        analyzed.append({
            "id": str(tweet.id),
            "text": text[:200],
            "type": tweet_type,
            "created_at": tweet.data.get("created_at", ""),
            "likes": metrics.get("like_count", 0),
            "retweets": metrics.get("retweet_count", 0),
            "replies": metrics.get("reply_count", 0),
            "impressions": metrics.get("impression_count", 0),
            "engagement_rate": round(rate, 6),
        })

    # Sort by engagement rate
    analyzed.sort(key=lambda t: t["engagement_rate"], reverse=True)

    # Top 5 and bottom 5
    top_5 = analyzed[:5]
    bottom_5 = analyzed[-5:] if len(analyzed) >= 5 else analyzed

    # Average engagement by type
    type_stats: dict[str, dict] = {}
    for t in analyzed:
        tt = t["type"]
        if tt not in type_stats:
            type_stats[tt] = {"count": 0, "total_rate": 0.0, "total_likes": 0,
                              "total_retweets": 0, "total_replies": 0,
                              "total_impressions": 0}
        stats = type_stats[tt]
        stats["count"] += 1
        stats["total_rate"] += t["engagement_rate"]
        stats["total_likes"] += t["likes"]
        stats["total_retweets"] += t["retweets"]
        stats["total_replies"] += t["replies"]
        stats["total_impressions"] += t["impressions"]

    avg_by_type = {}
    for tt, stats in type_stats.items():
        c = stats["count"]
        avg_by_type[tt] = {
            "count": c,
            "avg_engagement_rate": round(stats["total_rate"] / c, 6),
            "avg_likes": round(stats["total_likes"] / c, 1),
            "avg_retweets": round(stats["total_retweets"] / c, 1),
            "avg_replies": round(stats["total_replies"] / c, 1),
            "avg_impressions": round(stats["total_impressions"] / c, 0),
        }

    # Sort types by avg engagement rate
    avg_by_type = dict(sorted(avg_by_type.items(),
                              key=lambda x: x[1]["avg_engagement_rate"], reverse=True))

    # Best posting hour
    best_hour = _best_posting_hour([t.data for t in response.data])

    # Build report
    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "tweets_analyzed": len(analyzed),
        "top_5": top_5,
        "bottom_5": bottom_5,
        "avg_engagement_by_type": avg_by_type,
        "best_posting_hour_utc": best_hour,
        "overall_avg_engagement_rate": round(
            sum(t["engagement_rate"] for t in analyzed) / len(analyzed), 6
        ) if analyzed else 0,
    }

    # Save report
    try:
        with open(_REPORT_FILE, "w") as f:
            json.dump(report, f, indent=2)
        logger.info("Analytics report saved to %s", _REPORT_FILE)
    except OSError as exc:
        logger.warning("Could not save analytics report: %s", exc)

    # Log summary
    logger.info("=== Tweet Analytics Report ===")
    logger.info("Tweets analyzed: %d", len(analyzed))
    logger.info("Overall avg engagement rate: %.4f%%",
                report["overall_avg_engagement_rate"] * 100)
    if best_hour is not None:
        logger.info("Best posting hour (UTC): %02d:00", best_hour)
    logger.info("--- Avg engagement by type ---")
    for tt, stats in avg_by_type.items():
        logger.info("  %-15s %3d tweets | %.4f%% eng | %.1f likes | %.1f RTs",
                     tt, stats["count"], stats["avg_engagement_rate"] * 100,
                     stats["avg_likes"], stats["avg_retweets"])
    if top_5:
        logger.info("--- Top tweet ---")
        t = top_5[0]
        logger.info("  [%s] %.80s… (%.4f%% eng, %d likes)",
                     t["type"], t["text"], t["engagement_rate"] * 100, t["likes"])

    return report


# ── Standalone entry point ───────────────────────────────────────────────────

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s  %(levelname)-8s  %(message)s")
    import twitter_client
    client = twitter_client.get_client()
    report = run_analytics(client)
    if report:
        print(f"\nReport saved to {_REPORT_FILE}")
        print(f"Top tweet: {report['top_5'][0]['text'][:80]}…")
    else:
        print("Analytics failed — check logs.")
