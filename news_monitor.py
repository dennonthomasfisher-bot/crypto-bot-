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
import state

logger = logging.getLogger(__name__)

# Track consecutive failures to log warnings about flaky API
_consecutive_failures = 0


def _story_hash(story: dict) -> str:
    """Stable identifier for a story based on its URL."""
    return hashlib.sha256(story.get("url", story.get("title", "")).encode()).hexdigest()


def _prune_old_hashes() -> None:
    """Remove hashes older than NEWS_DEDUP_WINDOW to keep memory bounded."""
    state.prune_old_news_hashes()


def _fetch_news() -> list[dict]:
    """
    Fetch the latest hot/important stories from CryptoPanic.
    Returns a list of story dicts, or [] on error.
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
    global _consecutive_failures
    for attempt in range(3):
        try:
            resp = requests.get(url, params=params, timeout=15)
            if resp.status_code in (404, 502, 503):
                logger.warning("CryptoPanic returned %d — API may be down", resp.status_code)
                break  # don't retry server errors, they won't fix themselves
            if resp.status_code == 429:
                wait = 2 ** (attempt + 1)
                logger.warning("CryptoPanic rate limited (429), retrying in %ds…", wait)
                time.sleep(wait)
                continue
            resp.raise_for_status()
            _consecutive_failures = 0
            return resp.json().get("results", [])
        except requests.RequestException as exc:
            logger.warning("CryptoPanic fetch failed (attempt %d/3): %s", attempt + 1, exc)
            if attempt < 2:
                time.sleep(2 ** (attempt + 1))
    _consecutive_failures += 1
    if _consecutive_failures >= 5:
        logger.warning(
            "CryptoPanic has failed %d consecutive checks — API may be permanently down. "
            "News tweets will be skipped until it recovers.",
            _consecutive_failures,
        )
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
        if not state.news_already_posted(h):
            new_stories.append(story)
            state.record_news_posted(h)
    return new_stories


def format_news_tweet(story: dict) -> str:
    """Turn a CryptoPanic story dict into a ready-to-post tweet string."""
    title = story.get("title", "Breaking crypto news")
    max_title_len = 180
    if len(title) > max_title_len:
        title = title[:max_title_len - 1] + "..."

    url = story.get("url", "")

    parts = [title]
    if url:
        parts.append("")
        parts.append(url)

    return "\n".join(parts)
