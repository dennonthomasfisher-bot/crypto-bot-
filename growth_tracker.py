#!/usr/bin/env python3
"""
Growth tracker – logs daily follower count, tweets posted, and best tweet.
Calculates days-to-500 based on average daily growth rate.
Appends to growth_log.json.

Runnable standalone: python growth_tracker.py
"""
from __future__ import annotations

import json
import logging
import os
import time
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

_LOG_FILE = os.path.join(os.path.dirname(__file__), "growth_log.json")


def _load_log() -> list[dict]:
    """Load the growth log from disk."""
    if not os.path.exists(_LOG_FILE):
        return []
    try:
        with open(_LOG_FILE) as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except (json.JSONDecodeError, OSError):
        return []


def _save_log(entries: list[dict]) -> None:
    """Save the growth log to disk."""
    try:
        with open(_LOG_FILE, "w") as f:
            json.dump(entries, f, indent=2)
    except OSError as exc:
        logger.warning("Could not save growth log: %s", exc)


def _get_today_tweet_count(client, user_id: str) -> int:
    """Count tweets posted today by fetching recent tweets and filtering by date."""
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    try:
        response = client.get_users_tweets(
            id=user_id,
            max_results=100,
            tweet_fields=["created_at"],
            exclude=["replies"],
        )
        if not response.data:
            return 0
        count = 0
        for tweet in response.data:
            created = tweet.data.get("created_at", "")
            if created.startswith(today):
                count += 1
            else:
                # Tweets are in reverse chronological order — stop once we pass today
                break
        return count
    except Exception as exc:
        logger.warning("Could not fetch today's tweet count: %s", exc)
        return 0


def _get_best_tweet_today(client, user_id: str) -> str | None:
    """Get the ID of today's best-performing tweet (by likes + RTs)."""
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    try:
        response = client.get_users_tweets(
            id=user_id,
            max_results=50,
            tweet_fields=["created_at", "public_metrics"],
            exclude=["replies"],
        )
        if not response.data:
            return None
        best_id = None
        best_score = -1
        for tweet in response.data:
            created = tweet.data.get("created_at", "")
            if not created.startswith(today):
                break
            m = tweet.data.get("public_metrics", {})
            score = m.get("like_count", 0) + m.get("retweet_count", 0) * 2
            if score > best_score:
                best_score = score
                best_id = str(tweet.id)
        return best_id
    except Exception as exc:
        logger.debug("Could not find best tweet today: %s", exc)
        return None


def run_growth_tracker(client=None) -> dict | None:
    """
    Fetch current follower count and today's tweet count,
    append to growth_log.json, calculate days-to-500, and log a summary.

    Returns the new log entry or None on failure.
    """
    if client is None:
        import twitter_client
        client = twitter_client.get_client()

    # Get user info
    try:
        me = client.get_me(user_fields=["public_metrics"])
        if not me.data:
            logger.warning("Could not fetch user data for growth tracking")
            return None
        user_id = str(me.data.id)
        follower_count = me.data.public_metrics.get("followers_count", 0)
    except Exception as exc:
        logger.warning("Failed to fetch follower count: %s", exc)
        return None

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    log = _load_log()

    # Skip if already recorded today
    if log and log[-1].get("date") == today:
        logger.info("Growth tracker already ran today — skipping.")
        return log[-1]

    # Gather today's stats
    tweets_posted = _get_today_tweet_count(client, user_id)
    best_tweet_id = _get_best_tweet_today(client, user_id)

    entry = {
        "date": today,
        "follower_count": follower_count,
        "tweets_posted": tweets_posted,
        "best_tweet_id": best_tweet_id,
        "timestamp": time.time(),
    }

    log.append(entry)
    # Keep max 365 days
    if len(log) > 365:
        log = log[-365:]
    _save_log(log)

    # Calculate growth stats
    day_number = len(log)
    daily_change = 0
    if len(log) >= 2:
        daily_change = follower_count - log[-2]["follower_count"]

    # Days to 500 estimate
    days_to_500 = None
    if follower_count < 500 and len(log) >= 2:
        # Average daily growth over all recorded days
        first_count = log[0]["follower_count"]
        total_days = len(log) - 1
        if total_days > 0:
            total_growth = follower_count - first_count
            avg_daily = total_growth / total_days
            if avg_daily > 0:
                remaining = 500 - follower_count
                days_to_500 = int(remaining / avg_daily)
    elif follower_count >= 500:
        days_to_500 = 0  # already there

    # Log the summary line
    change_str = f"{daily_change:+d} today" if len(log) >= 2 else "first day"
    summary = f"Day {day_number}: {follower_count} followers ({change_str})"
    if days_to_500 is not None and days_to_500 > 0:
        summary += f", est. {days_to_500} days to 500"
    elif days_to_500 == 0:
        summary += " — 500 follower milestone reached!"

    logger.info("=== Growth Tracker ===")
    logger.info(summary)
    logger.info("Tweets posted today: %d", tweets_posted)
    if best_tweet_id:
        logger.info("Best tweet today: %s", best_tweet_id)

    # Add computed fields to entry for the JSON log
    entry["daily_change"] = daily_change
    entry["days_to_500"] = days_to_500
    _save_log(log)

    return entry


# ── Standalone entry point ───────────────────────────────────────────────────

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s  %(levelname)-8s  %(message)s")
    import twitter_client
    client = twitter_client.get_client()
    result = run_growth_tracker(client)
    if result:
        print(f"\nGrowth log saved to {_LOG_FILE}")
        print(f"Followers: {result['follower_count']} | "
              f"Tweets today: {result['tweets_posted']}")
        if result.get("days_to_500"):
            print(f"Est. days to 500: {result['days_to_500']}")
    else:
        print("Growth tracking failed — check logs.")
