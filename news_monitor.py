"""
News monitor – polls CryptoPanic's free API for hot/important crypto news
and returns story objects that haven't been posted yet.

Free-tier CryptoPanic API: https://cryptopanic.com/developers/api/
  - No charge, rate-limit ~100 req/day on free tier.
  - Sign up at https://cryptopanic.com/accounts/signup/ to get an API key.
"""

import time
import hashlib
import logging
import requests

import config
import ai_writer

logger = logging.getLogger(__name__)

# Set of story hashes we've already posted (cleared after NEWS_DEDUP_WINDOW)
_posted_hashes: dict[str, float] = {}   # hash -> timestamp when posted


def _story_hash(story: dict) -> str:
    """Stable identifier for a story based on its URL."""
    return hashlib.md5(story.get("url", story.get("title", "")).encode()).hexdigest()


def _prune_old_hashes() -> None:
    """Remove hashes older than NEWS_DEDUP_WINDOW to keep memory bounded."""
    cutoff = time.time() - config.NEWS_DEDUP_WINDOW
    to_delete = [h for h, ts in _posted_hashes.items() if ts < cutoff]
    for h in to_delete:
        del _posted_hashes[h]


def _fetch_news() -> list[dict]:
    """
    Fetch the latest hot/important stories from CryptoPanic.
    Returns a list of story dicts, or [] on error.

    Retries up to 3 times with exponential backoff on 429 rate-limit responses.
    Backoff delays: 60 s → 120 s → 240 s.
    """
    if not config.CRYPTOPANIC_API_KEY:
        logger.warning(
            "CRYPTOPANIC_API_KEY not set – news monitoring disabled. "
            "Get a free key at https://cryptopanic.com/developers/api/"
        )
        return []

    url = f"{config.CRYPTOPANIC_BASE}/posts/"
    params = {
        "auth_token": config.CRYPTOPANIC_API_KEY,
        "filter":     config.CRYPTOPANIC_FILTER,
        "public":     "true",
        "kind":       "news",
    }
    for attempt in range(1, 4):
        try:
            resp = requests.get(url, params=params, timeout=15)
            if resp.status_code == 429:
                wait = 60 * (2 ** (attempt - 1))   # 60 s, 120 s, 240 s
                logger.warning(
                    "CryptoPanic 429 rate limit (attempt %d/3) – waiting %ds before retry.",
                    attempt, wait,
                )
                if attempt < 3:
                    time.sleep(wait)
                    continue
                return []
            resp.raise_for_status()
            return resp.json().get("results", [])
        except requests.RequestException as exc:
            logger.warning("CryptoPanic fetch failed: %s", exc)
            return []
    return []


def check_news() -> list[dict]:
    """
    Return list of new story dicts that haven't been posted yet.
    Side-effect: marks returned stories as posted.
    """
    _prune_old_hashes()
    stories = _fetch_news()
    new_stories = []
    for story in stories:
        h = _story_hash(story)
        if h not in _posted_hashes:
            new_stories.append(story)
            _posted_hashes[h] = time.time()
    return new_stories


def fetch_latest_headlines(n: int = 3) -> list[str]:
    """
    Return up to `n` titles from the most recent CryptoPanic stories.
    Does NOT mark stories as posted – safe to call for recap generation.
    """
    stories = _fetch_news()
    return [s["title"] for s in stories[:n] if s.get("title")]


def format_news_tweet(story: dict) -> str:
    """Turn a CryptoPanic story dict into a ready-to-post tweet string."""
    return ai_writer.generate_news_tweet(story)
