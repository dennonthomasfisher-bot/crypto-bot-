"""
account_monitor.py – monitors tweets from a curated list of major crypto accounts
and returns unread, recent tweets for the bot to reply to.

Rules enforced here:
  - Only tweets posted in the last REPLY_WINDOW_MINUTES are considered
  - Max 1 candidate returned per account per cycle
  - Tweets already replied to (tracked in bot_state.json) are excluded
  - The daily cap check is a gate: returns [] immediately if already at the limit
"""

import logging

import bot_state
import twitter_client

logger = logging.getLogger(__name__)

# Target accounts (handles without @)
TARGET_ACCOUNTS: list[str] = [
    "CoinDesk",
    "Cointelegraph",
    "WatcherGuru",
    "BitcoinMagazine",
    "CryptoSlate",
    "DecryptMedia",
    "TheBlock__",
    "PeckShieldAlert",
]

AUTO_REPLY_DAILY_CAP   = 8   # max total auto-replies across all accounts per day
REPLY_WINDOW_MINUTES   = 30  # only reply to tweets posted within this window


def get_reply_candidates() -> list[dict]:
    """
    Return a list of tweet dicts that the bot should consider replying to.

    Each dict has: {id, text, author_id, author_username, created_at}

    Selection logic:
      1. Bail immediately if the daily cap is already reached.
      2. Fetch tweets from all target accounts posted in the last
         REPLY_WINDOW_MINUTES via a single batched Twitter search.
      3. Filter out tweet IDs already replied to (from bot_state).
      4. Keep at most one tweet per account (the first / most recent).
    """
    if bot_state.get_auto_reply_count_today() >= AUTO_REPLY_DAILY_CAP:
        logger.info(
            "Auto-reply daily cap (%d) reached – skipping account monitor cycle.",
            AUTO_REPLY_DAILY_CAP,
        )
        return []

    replied_ids = bot_state.get_replied_ids()

    tweets = twitter_client.fetch_account_tweets(
        usernames=TARGET_ACCOUNTS,
        minutes=REPLY_WINDOW_MINUTES,
    )

    seen_authors: set[str] = set()
    candidates: list[dict] = []

    for tweet in tweets:
        if tweet["id"] in replied_ids:
            continue
        author = tweet["author_username"]
        if author in seen_authors:
            continue   # already have one candidate for this account this cycle
        seen_authors.add(author)
        candidates.append(tweet)

    logger.info(
        "Account monitor: %d candidate tweet(s) from %d account(s) checked.",
        len(candidates), len(TARGET_ACCOUNTS),
    )
    return candidates
