"""
NewsAPI.org client for fetching crypto news.
Sign up at https://newsapi.org/ to get an API key.
"""
from __future__ import annotations

import logging

import requests

NEWS_API_KEY = "cb9aa26e4d7e45809bd6757337d5669e"
NEWS_API_URL = "https://newsapi.org/v2/everything"

logger = logging.getLogger(__name__)

_JUNK_SOURCES = ("pypi", "beehiiv", "substack")

_CRYPTO_KEYWORDS = (
    "bitcoin", "btc", "ethereum", "eth", "crypto", "blockchain",
    "defi", "nft", "altcoin", "binance", "coinbase", "stablecoin",
    "web3", "satoshi", "halving", "mining", "wallet", "token",
    "sec", "etf", "regulation",
)


def _is_junk(title: str, source_name: str) -> bool:
    source_lower = source_name.lower()
    if any(s in source_lower for s in _JUNK_SOURCES):
        return True
    title_lower = title.lower()
    if not any(kw in title_lower for kw in _CRYPTO_KEYWORDS):
        return True
    return False


def fetch_crypto_news() -> list[dict]:
    """
    Fetch recent crypto articles from NewsAPI.org.

    Returns a list of dicts with keys:
        title, url, source, published_at
    Junk sources (pypi, beehiiv, substack) and non-crypto titles are excluded.
    Dedup against already-posted stories is handled by news_monitor._posted_hashes.
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

    results: list[dict] = []
    for article in articles:
        title = article.get("title", "").strip()
        source_name = (article.get("source") or {}).get("name", "")
        if not title or _is_junk(title, source_name):
            continue
        results.append({
            "title": title,
            "url": article.get("url", ""),
            "source": source_name,
            "published_at": article.get("publishedAt", ""),
        })

    return results
