"""
Reddit /r/cryptocurrency hot-post monitor.

Uses the public JSON endpoint (no auth required, just a real User-Agent).
Surfaces posts with enough community engagement to be relevant — higher
signal than random news, lower latency than a journalist's writeup.

Returns posts as story-shaped dicts so the existing news scoring/formatting
pipeline can consume them directly.
"""
from __future__ import annotations

import logging
import time
from typing import Iterable

import requests

logger = logging.getLogger(__name__)

_HOT_URL_TMPL = "https://www.reddit.com/r/{sub}/hot.json?limit={limit}"
_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36 CryptoVaultBot/1.0"
)

_DEFAULT_SUBS = ("CryptoCurrency", "Bitcoin", "ethfinance")
_MIN_SCORE = 400           # minimum upvotes to consider a post noteworthy
_MAX_AGE_HOURS = 8         # must be recent — Reddit moves fast
_TOP_N_PER_SUB = 15        # check first N hot posts


def _fetch_hot(subreddit: str, limit: int = _TOP_N_PER_SUB) -> list[dict]:
    """Fetch the JSON 'hot' feed for a subreddit."""
    url = _HOT_URL_TMPL.format(sub=subreddit, limit=limit)
    try:
        resp = requests.get(
            url,
            headers={"User-Agent": _USER_AGENT},
            timeout=10,
        )
        if resp.status_code != 200:
            logger.debug("Reddit /r/%s -> %d", subreddit, resp.status_code)
            return []
        data = resp.json()
        children = data.get("data", {}).get("children", [])
        return [c.get("data", {}) for c in children if isinstance(c, dict)]
    except Exception as exc:
        logger.debug("Reddit fetch for /r/%s failed: %s", subreddit, exc)
        return []


def _qualifies(post: dict) -> bool:
    """Score, age, and format filters."""
    if post.get("stickied") or post.get("over_18"):
        return False
    if post.get("is_self") is False and not post.get("url", "").startswith("http"):
        return False
    score = post.get("score", 0)
    if not isinstance(score, (int, float)) or score < _MIN_SCORE:
        return False
    created = post.get("created_utc")
    if not isinstance(created, (int, float)):
        return False
    age_hours = (time.time() - created) / 3600
    if age_hours > _MAX_AGE_HOURS:
        return False
    return True


def fetch_hot_stories(subreddits: Iterable[str] = _DEFAULT_SUBS) -> list[dict]:
    """Return qualifying hot posts as story-shaped dicts.

    Dict shape matches what news_monitor expects so these can feed the
    same scoring + dedup pipeline without special handling.
    """
    stories: list[dict] = []
    for sub in subreddits:
        for post in _fetch_hot(sub):
            if not _qualifies(post):
                continue
            title = (post.get("title") or "").strip()
            if not title:
                continue
            permalink = post.get("permalink") or ""
            link_url = post.get("url_overridden_by_dest") or post.get("url") or ""
            full_url = (
                f"https://www.reddit.com{permalink}"
                if permalink.startswith("/")
                else (link_url or f"https://www.reddit.com/r/{sub}")
            )
            stories.append({
                "title": title,
                "url": full_url,
                "source": f"Reddit /r/{sub}",
                "published_at": int(post.get("created_utc", time.time())),
                "reddit_score": int(post.get("score", 0)),
                "reddit_sub": sub,
                "num_comments": int(post.get("num_comments", 0)),
            })
    # Newest first
    stories.sort(key=lambda s: s["published_at"], reverse=True)
    return stories
