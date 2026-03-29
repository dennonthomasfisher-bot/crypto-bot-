"""
Reply engine — monitor target accounts and post analytical replies.

Monitors tweets from configured accounts, generates calm/analytical
replies via ai_writer.generate_reply(), and posts them with optional
coin chart attachments.

Rate limits: MAX_REPLIES_PER_HOUR, REPLY_MIN_GAP_SECONDS, never
reply to same account twice in a row, persistent replied-IDs store.
"""
from __future__ import annotations

import json
import logging
import os
import random
import time
from datetime import datetime, timezone, timedelta

import config
import ai_writer
import twitter_client
import chart_generator

logger = logging.getLogger(__name__)

_DIR = os.path.dirname(os.path.abspath(__file__))
_REPLIED_IDS_FILE = os.path.join(_DIR, ".replied_tweet_ids.json")
_MAX_STORED_IDS = 2000

# ── Persistent store of replied tweet IDs ────────────────────────────────────

def _load_replied_ids() -> set[str]:
    if not os.path.exists(_REPLIED_IDS_FILE):
        return set()
    try:
        with open(_REPLIED_IDS_FILE) as f:
            data = json.load(f)
        return set(data) if isinstance(data, list) else set()
    except (json.JSONDecodeError, OSError):
        return set()


def _save_replied_ids(ids: set[str]) -> None:
    # Keep only the most recent IDs to prevent unbounded growth
    id_list = sorted(ids)[-_MAX_STORED_IDS:]
    try:
        with open(_REPLIED_IDS_FILE, "w") as f:
            json.dump(id_list, f)
    except OSError as exc:
        logger.warning("Failed to save replied IDs: %s", exc)


# ── Rate limiting state ──────────────────────────────────────────────────────

_reply_timestamps: list[float] = []  # timestamps of replies in current hour
_last_reply_time: float = 0.0
_last_replied_account: str = ""


def _can_reply() -> bool:
    """Check hourly cap and minimum gap."""
    global _reply_timestamps
    now = time.time()

    # Prune timestamps older than 1 hour
    cutoff = now - 3600
    _reply_timestamps = [t for t in _reply_timestamps if t > cutoff]

    if len(_reply_timestamps) >= config.MAX_REPLIES_PER_HOUR:
        logger.debug("Reply hourly cap (%d) reached.", config.MAX_REPLIES_PER_HOUR)
        return False

    if _last_reply_time > 0 and (now - _last_reply_time) < config.REPLY_MIN_GAP_SECONDS:
        gap_left = int(config.REPLY_MIN_GAP_SECONDS - (now - _last_reply_time))
        logger.debug("Reply min gap — %ds left.", gap_left)
        return False

    return True


def _record_reply(account: str) -> None:
    """Record a reply for rate limiting."""
    global _last_reply_time, _last_replied_account
    _reply_timestamps.append(time.time())
    _last_reply_time = time.time()
    _last_replied_account = account


# ── Coin detection for chart attachment ──────────────────────────────────────

# Reuse bot.py's coin map via a lightweight inline version
_COIN_KEYWORDS: dict[str, tuple[str, str]] = {
    "BTC": ("bitcoin", "BTC"), "BITCOIN": ("bitcoin", "BTC"),
    "ETH": ("ethereum", "ETH"), "ETHEREUM": ("ethereum", "ETH"),
    "SOL": ("solana", "SOL"), "SOLANA": ("solana", "SOL"),
    "BNB": ("binancecoin", "BNB"), "XRP": ("ripple", "XRP"),
    "ADA": ("cardano", "ADA"), "DOGE": ("dogecoin", "DOGE"),
    "AVAX": ("avalanche-2", "AVAX"), "LINK": ("chainlink", "LINK"),
    "DOT": ("polkadot", "DOT"), "SUI": ("sui", "SUI"),
    "ARB": ("arbitrum", "ARB"), "OP": ("optimism", "OP"),
    "TAO": ("bittensor", "TAO"), "INJ": ("injective-protocol", "INJ"),
}

import re as _re

def _detect_coin(text: str) -> tuple[str, str] | None:
    upper = text.upper()
    for key in sorted(_COIN_KEYWORDS, key=len, reverse=True):
        if len(key) <= 4:
            if _re.search(r'(?:\$|\b)' + _re.escape(key) + r'\b', upper):
                return _COIN_KEYWORDS[key]
        else:
            if key in upper:
                return _COIN_KEYWORDS[key]
    return None


# ── Core engine ──────────────────────────────────────────────────────────────

def check_and_reply() -> None:
    """Main entry point — called by bot.py scheduler every 5 minutes.

    Searches for recent tweets from target accounts, picks the best
    candidate, generates a reply, and posts it.
    """
    if not config.TWITTER_BEARER_TOKEN:
        logger.debug("No TWITTER_BEARER_TOKEN — reply engine disabled.")
        return

    if not _can_reply():
        return

    accounts = config.REPLY_ACCOUNTS
    if not accounts:
        logger.debug("No REPLY_ACCOUNTS configured — skipping.")
        return

    replied_ids = _load_replied_ids()
    now_utc = datetime.now(timezone.utc)
    max_age = timedelta(minutes=10)

    # Build search query: from any target account, exclude retweets
    query_parts = [f"from:{acct}" for acct in accounts]
    query = f"({' OR '.join(query_parts)}) -is:retweet"

    try:
        tweets = twitter_client.search_recent_tweets(query, max_results=20)
    except Exception as exc:
        logger.warning("Reply engine search failed: %s", exc)
        return

    if not tweets:
        logger.debug("Reply engine: no recent tweets from target accounts.")
        return

    # Filter candidates
    candidates = []
    for tweet in tweets:
        tid = tweet["id"]

        # Already replied
        if tid in replied_ids:
            continue

        # Too old
        created = tweet.get("created_at")
        if created and (now_utc - created) > max_age:
            continue

        # Too short (skip under 10 words)
        text = tweet.get("text", "")
        if len(text.split()) < 10:
            continue

        # Never reply to same account twice in a row
        # We need the username — derive from author_id or skip this check
        # For now, use author_id as proxy
        author = tweet.get("author_id", "")
        if author == _last_replied_account:
            continue

        candidates.append(tweet)

    if not candidates:
        logger.debug("Reply engine: no eligible candidates after filtering.")
        return

    # Pick the most recent candidate
    candidate = candidates[0]
    tweet_text = candidate["text"]
    tweet_id = candidate["id"]
    author_id = candidate.get("author_id", "unknown")

    logger.info("Reply engine: generating reply to tweet %s from author %s",
                tweet_id, author_id)

    # Generate reply
    reply_text = ai_writer.generate_reply(tweet_text)
    if not reply_text:
        logger.warning("Reply engine: generate_reply returned None for tweet %s", tweet_id)
        return

    # Generate chart if coin detected
    chart_path: str | None = None
    coin = _detect_coin(tweet_text)
    if coin:
        try:
            chart_path = chart_generator.generate_line_fill(coin[0], coin[1], 1)
        except Exception as exc:
            logger.warning("Reply chart generation failed: %s", exc)

    # Random delay 30-120 seconds before posting
    delay = random.randint(30, 120)
    logger.info("Reply engine: waiting %ds before posting reply to %s", delay, tweet_id)
    time.sleep(delay)

    # Re-check rate limit after delay
    if not _can_reply():
        logger.info("Reply engine: rate limit hit after delay — skipping.")
        return

    # Post reply
    posted = twitter_client.post_tweet(
        reply_text,
        image_path=chart_path,
        in_reply_to_tweet_id=tweet_id,
    )

    if posted:
        _record_reply(author_id)
        replied_ids.add(tweet_id)
        _save_replied_ids(replied_ids)
        snippet = reply_text[:60].replace("\n", " ")
        logger.info("[REPLY] Replied to author %s: %s", author_id, snippet)
    else:
        logger.warning("Reply engine: post_tweet failed for tweet %s", tweet_id)
