"""
NewsAPI.org client for fetching crypto news.
Sign up at https://newsapi.org/ to get an API key.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone, timedelta

import requests

NEWS_API_KEY = "cb9aa26e4d7e45809bd6757337d5669e"
NEWS_API_URL = "https://newsapi.org/v2/everything"

logger = logging.getLogger(__name__)


def fetch_crypto_news() -> list[dict]:
    """
    Fetch recent crypto articles from NewsAPI.org.

    Returns a list of dicts with keys:
        title, url, source, published_at
    Only articles published within the last 2 hours are included.
    """
    params = {
        "q": "bitcoin OR ethereum OR crypto OR blockchain",
        "language": "en",
        "sortBy": "publishedAt",
        "pageSize": 20,
        "apiKey": NEWS_API_KEY,
    }
    try:
        resp = requests.get(NEWS_API_URL, params=params, timeout=15)
        resp.raise_for_status()
        articles = resp.json().get("articles", [])
    except requests.RequestException as exc:
        logger.warning("NewsAPI fetch failed: %s", exc)
        return []

    cutoff = datetime.now(timezone.utc) - timedelta(hours=2)
    results: list[dict] = []
    for article in articles:
        published_raw = article.get("publishedAt", "")
        try:
            published_at = datetime.fromisoformat(published_raw.replace("Z", "+00:00"))
        except (ValueError, AttributeError):
            continue
        if published_at < cutoff:
            continue
        results.append({
            "title": article.get("title", "").strip(),
            "url": article.get("url", ""),
            "source": (article.get("source") or {}).get("name", ""),
            "published_at": published_at.isoformat(),
        })

    return results
