"""
Reply-back monitor – checks replies to our own tweets and responds
to high-quality ones with AI-generated, data-driven replies.

Runs every 30 minutes. Only replies to tweets with 1+ likes or from
accounts with 500+ followers. Capped at 5 reply-backs per day.
"""
from __future__ import annotations

import datetime
import logging
import re
import time

import tweepy

import config
import state
import twitter_client
import tweet_generators
import ai_writer

logger = logging.getLogger(__name__)

# Track which replies we've already responded to (in-memory, bounded)
_responded_ids: set[str] = set()

# Daily cap tracking
_replyback_count = 0
_replyback_day = 0


def _reset_daily_cap() -> None:
    global _replyback_count, _replyback_day
    today = datetime.date.today().toordinal()
    if _replyback_day != today:
        _replyback_count = 0
        _replyback_day = today


def can_reply_back() -> bool:
    _reset_daily_cap()
    return _replyback_count < config.REPLY_BACK_DAILY_CAP


def _record_reply_back() -> None:
    global _replyback_count
    _reset_daily_cap()
    _replyback_count += 1


def _get_bot_user_id() -> str | None:
    """Fetch the authenticated bot's user ID."""
    try:
        client = twitter_client.get_client()
        me = client.get_me()
        if me.data:
            return str(me.data.id)
    except Exception as exc:
        logger.warning("Could not fetch bot user ID: %s", exc)
    return None


def _get_recent_bot_tweets(user_id: str, max_tweets: int = 10) -> list[dict]:
    """Fetch our most recent tweets (last ~24h worth)."""
    try:
        client = twitter_client.get_client()
        response = client.get_users_tweets(
            id=user_id,
            max_results=max_tweets,
            tweet_fields=["created_at", "public_metrics"],
            exclude=["retweets", "replies"],
        )
        if response.data:
            return [
                {"id": str(t.id), "text": t.text, "created_at": t.created_at}
                for t in response.data
            ]
    except tweepy.TweepyException as exc:
        logger.warning("Failed to fetch bot tweets: %s", exc)
    return []


def _get_replies_to_tweet(tweet_id: str, bot_user_id: str) -> list[dict]:
    """Search for replies to a specific tweet."""
    try:
        client = twitter_client.get_client()
        query = f"conversation_id:{tweet_id} is:reply -from:{bot_user_id}"
        response = client.search_recent_tweets(
            query=query,
            max_results=20,
            tweet_fields=["author_id", "public_metrics", "created_at"],
            expansions=["author_id"],
            user_fields=["public_metrics"],
        )
        if not response.data:
            return []

        # Build author follower map from includes
        author_followers: dict[str, int] = {}
        if response.includes and "users" in response.includes:
            for user in response.includes["users"]:
                followers = user.public_metrics.get("followers_count", 0) if user.public_metrics else 0
                author_followers[str(user.id)] = followers

        return [
            {
                "id": str(t.id),
                "text": t.text,
                "author_id": str(t.author_id),
                "likes": t.public_metrics.get("like_count", 0) if t.public_metrics else 0,
                "followers": author_followers.get(str(t.author_id), 0),
            }
            for t in response.data
        ]
    except tweepy.errors.Forbidden:
        logger.warning("Twitter search forbidden for replies – may need elevated access")
    except tweepy.TweepyException as exc:
        logger.warning("Failed to search replies: %s", exc)
    return []


def _generate_reply_back(original_tweet_text: str, reply_text: str) -> str | None:
    """Generate an AI reply to someone who replied to our tweet."""
    if not ai_writer.is_available():
        return None

    btc_data = tweet_generators._get_btc_data()
    price_ctx = ""
    if btc_data:
        price = btc_data.get("current_price", 0)
        pct = btc_data.get("price_change_percentage_24h_in_currency") or 0
        price_ctx = f"\nCurrent BTC: ${price:,.0f} ({pct:+.1f}% 24h)"

    system = (
        "You are @CoinWatchAlert replying to someone who responded to your tweet. "
        "Be agreeable, conversational, and add genuine insight or data. "
        "Sound like a trader chatting with a friend, not a bot.\n\n"
        "RULES:\n"
        "- Max 220 characters\n"
        "- Be agreeable — build on what they said, don't argue\n"
        "- Add a data point, insight, or interesting angle they didn't mention\n"
        "- Only use these emojis if needed: \U0001f680\U0001f4c9\u26a1\U0001f440\n"
        "- NO \u26a0\ufe0f emoji, NO exclamation marks, NO 'NFA', NO 'DYOR'\n"
        "- NO hashtags\n"
        "- NO generic replies like 'thanks', 'great point', 'couldn't agree more'\n"
        "- Be specific — reference their point and add something new\n"
        "- Do NOT wrap response in quotes"
    )

    prompt = (
        f"Your original tweet:\n\"{original_tweet_text[:200]}\"\n\n"
        f"Their reply:\n\"{reply_text[:200]}\"\n"
        f"{price_ctx}\n\n"
        "Write a conversational reply (max 220 chars) that agrees with them "
        "and adds insight or data. Nothing else."
    )

    return ai_writer._call_claude(system, prompt, max_tokens=120)


def check_and_reply() -> int:
    """
    Check replies to our recent tweets and respond to quality ones.
    Returns count of replies posted.
    """
    if config.is_quiet_hours():
        logger.info("Quiet hours — skipping reply-back check")
        return 0

    if not can_reply_back():
        logger.info("Reply-back daily cap (%d) reached.", config.REPLY_BACK_DAILY_CAP)
        return 0

    bot_user_id = _get_bot_user_id()
    if not bot_user_id:
        return 0

    our_tweets = _get_recent_bot_tweets(bot_user_id, max_tweets=10)
    if not our_tweets:
        logger.info("No recent bot tweets found for reply-back check.")
        return 0

    replied = 0
    for tweet in our_tweets:
        if not can_reply_back():
            break
        if not state.can_tweet():
            logger.warning("Monthly tweet limit reached — skipping reply-backs")
            break

        replies = _get_replies_to_tweet(tweet["id"], bot_user_id)
        for reply in replies:
            if not can_reply_back():
                break
            if not state.can_tweet():
                break

            if reply["id"] in _responded_ids:
                continue

            # Quality filter: 1+ likes OR 500+ followers
            if reply["likes"] < 1 and reply["followers"] < 500:
                continue

            reply_text = _generate_reply_back(tweet["text"], reply["text"])
            if not reply_text:
                continue

            # Enforce max length and strip hashtags
            reply_text = re.sub(r'\s*#\w+', '', reply_text).strip()
            # Remove exclamation marks
            reply_text = reply_text.replace("!", ".")
            if len(reply_text) > 220:
                reply_text = reply_text[:217].rsplit(" ", 1)[0] + "..."

            try:
                client = twitter_client.get_client()
                client.create_tweet(
                    text=reply_text,
                    in_reply_to_tweet_id=reply["id"],
                )
                _responded_ids.add(reply["id"])
                state.record_tweet()
                _record_reply_back()
                replied += 1
                logger.info(
                    "Reply-back to %s (likes=%d, followers=%d): %.100s",
                    reply["id"], reply["likes"], reply["followers"], reply_text,
                )
                time.sleep(3)  # Pace replies

            except tweepy.errors.Forbidden:
                logger.warning("Cannot reply-back to %s — forbidden", reply["id"])
            except tweepy.TweepyException as exc:
                logger.warning("Reply-back failed for %s: %s", reply["id"], exc)

        if replied >= config.REPLY_BACK_DAILY_CAP:
            break

    # Keep set bounded
    if len(_responded_ids) > 500:
        _responded_ids.clear()

    return replied
