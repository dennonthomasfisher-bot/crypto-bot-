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
    logger.info("[REPLY] check_and_reply fired")

    if not config.TWITTER_BEARER_TOKEN:
        logger.info("[REPLY] No TWITTER_BEARER_TOKEN — reply engine disabled")
        return

    if not _can_reply():
        logger.info("[REPLY] Rate limited — skipping this cycle")
        return

    accounts = config.REPLY_ACCOUNTS
    if not accounts:
        logger.info("[REPLY] No REPLY_ACCOUNTS configured — skipping")
        return

    logger.info("[REPLY] Monitoring %d accounts: %s", len(accounts), ", ".join(f"@{a}" for a in accounts))

    replied_ids = _load_replied_ids()
    logger.info("[REPLY] Loaded %d previously replied tweet IDs", len(replied_ids))
    now_utc = datetime.now(timezone.utc)
    max_age = timedelta(minutes=10)

    # Build search query: from any target account, exclude retweets
    query_parts = [f"from:{acct}" for acct in accounts]
    query = f"({' OR '.join(query_parts)}) -is:retweet"
    logger.info("[REPLY] Search query: %s", query)

    try:
        tweets = twitter_client.search_recent_tweets(query, max_results=20)
        logger.info("[REPLY] Search returned %d tweets", len(tweets) if tweets else 0)
    except Exception as exc:
        logger.error("[REPLY] API error searching tweets: %s", exc)
        return

    if not tweets:
        logger.info("[REPLY] No recent tweets from target accounts")
        return

    # Filter candidates
    candidates = []
    for tweet in tweets:
        tid = tweet["id"]
        text = tweet.get("text", "")
        author = tweet.get("author_id", "")
        created = tweet.get("created_at")

        # Already replied
        if tid in replied_ids:
            logger.debug("[REPLY] Skip %s — already replied", tid)
            continue

        # Too old
        if created and (now_utc - created) > max_age:
            age_mins = (now_utc - created).total_seconds() / 60
            logger.debug("[REPLY] Skip %s — too old (%.0fm)", tid, age_mins)
            continue

        # Too short (skip under 10 words)
        if len(text.split()) < 10:
            logger.debug("[REPLY] Skip %s — too short (%d words)", tid, len(text.split()))
            continue

        # Never reply to same account twice in a row
        if author == _last_replied_account:
            logger.debug("[REPLY] Skip %s — same account as last reply", tid)
            continue

        logger.info("[REPLY] Eligible candidate: %s | %.60s", tid, text)
        candidates.append(tweet)

    if not candidates:
        logger.info("[REPLY] No eligible candidates after filtering %d tweets", len(tweets))
        return

    logger.info("[REPLY] %d eligible candidates found", len(candidates))

    # Pick the most recent candidate
    candidate = candidates[0]
    tweet_text = candidate["text"]
    tweet_id = candidate["id"]
    author_id = candidate.get("author_id", "unknown")

    logger.info("[REPLY] Generating reply to tweet %s from author %s: %.80s",
                tweet_id, author_id, tweet_text)

    # Generate reply
    reply_text = ai_writer.generate_reply(tweet_text)
    if not reply_text:
        logger.warning("[REPLY] generate_reply returned None for tweet %s", tweet_id)
        return

    logger.info("[REPLY] Generated reply: %.100s", reply_text)

    # Generate chart if coin detected
    chart_path: str | None = None
    coin = _detect_coin(tweet_text)
    if coin:
        logger.info("[REPLY] Coin detected: %s — generating chart", coin[1])
        try:
            chart_path = chart_generator.generate_line_fill(coin[0], coin[1], 1)
        except Exception as exc:
            logger.warning("[REPLY] Chart generation failed: %s", exc)
    else:
        logger.info("[REPLY] No coin detected — text-only reply")

    # Random delay 30-120 seconds before posting
    delay = random.randint(30, 120)
    logger.info("[REPLY] Waiting %ds before posting reply to %s", delay, tweet_id)
    time.sleep(delay)

    # Re-check rate limit after delay
    if not _can_reply():
        logger.info("[REPLY] Rate limit hit after delay — skipping")
        return

    # Post reply
    logger.info("[REPLY] Posting reply to %s (chart=%s)", tweet_id, "yes" if chart_path else "no")
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
