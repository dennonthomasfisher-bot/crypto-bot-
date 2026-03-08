"""
Reply-back monitor – checks mentions and replies to our tweets, then
responds with AI-generated, conversational replies.

Uses get_users_mentions (Free-tier compatible) instead of search_recent_tweets.
Runs every 30 minutes. Capped at 5 reply-backs per day.
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

# Cache bot user ID across calls
_cached_bot_user_id: str | None = None


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
    """Fetch the authenticated bot's user ID (cached)."""
    global _cached_bot_user_id
    if _cached_bot_user_id:
        return _cached_bot_user_id
    try:
        client = twitter_client.get_client()
        me = client.get_me()
        if me.data:
            _cached_bot_user_id = str(me.data.id)
            return _cached_bot_user_id
    except Exception as exc:
        logger.warning("Could not fetch bot user ID: %s", exc)
    return None


def _get_mentions(user_id: str, max_results: int = 20) -> list[dict]:
    """Fetch recent mentions using get_users_mentions (Free tier)."""
    try:
        client = twitter_client.get_client()
        response = client.get_users_mentions(
            id=user_id,
            max_results=max_results,
            tweet_fields=["author_id", "public_metrics", "created_at",
                          "conversation_id", "in_reply_to_user_id"],
            expansions=["author_id"],
            user_fields=["public_metrics", "username"],
        )
        if not response.data:
            return []

        # Build author info map
        author_info: dict[str, dict] = {}
        if response.includes and "users" in response.includes:
            for user in response.includes["users"]:
                followers = (user.public_metrics.get("followers_count", 0)
                             if user.public_metrics else 0)
                author_info[str(user.id)] = {
                    "followers": followers,
                    "username": user.username,
                }

        return [
            {
                "id": str(t.id),
                "text": t.text,
                "author_id": str(t.author_id),
                "username": author_info.get(str(t.author_id), {}).get("username", ""),
                "likes": (t.public_metrics.get("like_count", 0)
                          if t.public_metrics else 0),
                "followers": author_info.get(str(t.author_id), {}).get("followers", 0),
                "conversation_id": str(t.conversation_id) if t.conversation_id else None,
                "in_reply_to": str(t.in_reply_to_user_id) if t.in_reply_to_user_id else None,
            }
            for t in response.data
        ]
    except tweepy.errors.Forbidden:
        logger.warning("Mentions fetch forbidden – check API access")
    except tweepy.TweepyException as exc:
        logger.warning("Failed to fetch mentions: %s", exc)
    return []


def _generate_reply_back(original_context: str, reply_text: str) -> str | None:
    """Generate an AI reply to someone who mentioned or replied to us."""
    if not ai_writer.is_available():
        return None

    btc_data = tweet_generators._get_btc_data()
    price_ctx = ""
    if btc_data:
        price = btc_data.get("current_price", 0)
        pct = btc_data.get("price_change_percentage_24h_in_currency") or 0
        price_ctx = f"\nCurrent BTC: ${price:,.0f} ({pct:+.1f}% 24h)"

    system = (
        "You are @CoinWatchAlert replying to someone on Twitter. "
        "Be conversational and add genuine insight or data. "
        "Sound like a trader chatting with a friend, not a bot.\n\n"
        "RULES:\n"
        "- Max 220 characters\n"
        "- Be agreeable — build on what they said, don't argue\n"
        "- Add a data point, insight, or interesting angle\n"
        "- NO emojis except 🟢 🔴 if needed for price direction\n"
        "- NO exclamation marks\n"
        "- NO 'NFA', 'DYOR', disclaimers\n"
        "- NO hashtags\n"
        "- NO generic replies like 'thanks', 'great point', 'couldn't agree more'\n"
        "- Be specific — reference their point and add something new\n"
        "- Do NOT wrap response in quotes"
    )

    prompt = (
        f"Their tweet/reply:\n\"{reply_text[:250]}\"\n"
        f"{price_ctx}\n\n"
        "Write a short, conversational reply (max 220 chars) that engages "
        "with what they said and adds insight. Nothing else."
    )

    return ai_writer._call_claude(system, prompt, max_tokens=120)


def check_and_reply() -> int:
    """
    Check mentions and replies, respond to quality ones.
    Uses get_users_mentions which works on Free tier.
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

    mentions = _get_mentions(bot_user_id, max_results=20)
    if not mentions:
        logger.info("No recent mentions found for reply-back.")
        return 0

    # Filter: skip our own tweets, skip already responded
    mentions = [
        m for m in mentions
        if m["author_id"] != bot_user_id and m["id"] not in _responded_ids
    ]

    # Prioritize: replies to our tweets first, then direct mentions
    # Sort by followers + likes for quality
    mentions.sort(
        key=lambda m: m["followers"] + m["likes"] * 10,
        reverse=True,
    )

    replied = 0
    for mention in mentions:
        if not can_reply_back():
            break
        if not state.can_tweet():
            logger.warning("Monthly tweet limit reached — skipping reply-backs")
            break

        reply_text = _generate_reply_back("", mention["text"])
        if not reply_text:
            continue

        # Clean up
        reply_text = re.sub(r'\s*#\w+', '', reply_text).strip()
        reply_text = reply_text.replace("!", ".")
        if len(reply_text) > 220:
            reply_text = reply_text[:217].rsplit(" ", 1)[0] + "..."

        try:
            client = twitter_client.get_client()
            client.create_tweet(
                text=reply_text,
                in_reply_to_tweet_id=mention["id"],
            )
            _responded_ids.add(mention["id"])
            state.record_tweet()
            _record_reply_back()
            replied += 1
            logger.info(
                "Reply-back to @%s (id=%s, followers=%d): %.100s",
                mention["username"], mention["id"],
                mention["followers"], reply_text,
            )
            time.sleep(3)  # Pace replies

        except tweepy.errors.Forbidden:
            logger.warning("Cannot reply to %s — forbidden", mention["id"])
        except tweepy.TweepyException as exc:
            logger.warning("Reply-back failed for %s: %s", mention["id"], exc)

    # Keep set bounded
    if len(_responded_ids) > 500:
        _responded_ids.clear()

    return replied
