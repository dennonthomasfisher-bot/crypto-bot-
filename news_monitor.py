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
    for attempt in range(3):
        try:
            resp = requests.get(url, params=params, timeout=15)
            if resp.status_code == 429:
                wait = 2 ** (attempt + 1)
                logger.warning("CryptoPanic rate limited (429), retrying in %ds…", wait)
                time.sleep(wait)
                continue
            resp.raise_for_status()
            return resp.json().get("results", [])
        except requests.RequestException as exc:
            logger.warning("CryptoPanic fetch failed (attempt %d/3): %s", attempt + 1, exc)
            if attempt < 2:
                time.sleep(2 ** (attempt + 1))
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
    # Truncate title so the tweet stays under 280 chars after URL + hashtags
    max_title_len = 200
    if len(title) > max_title_len:
        title = title[:max_title_len - 1] + "…"

    url = story.get("url", "")

    # Build hashtags from currencies mentioned in the story
    currencies = story.get("currencies") or []
    tags = " ".join(
        f"#{c['code']}" for c in currencies[:3]
        if c.get("code") and c["code"] != "?"
    )
    if not tags:
        tags = "#Crypto #CryptoNews"

    parts = [f"📰 {title}", url, tags]
    return "\n".join(p for p in parts if p)
