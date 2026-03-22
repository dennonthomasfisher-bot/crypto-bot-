"""
NewsAPI.org client for fetching crypto news.
Sign up at https://newsapi.org/ to get an API key.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone, timedelta

import os
import feedparser
import requests
from dotenv import load_dotenv

load_dotenv()

NEWS_API_KEY = os.getenv("NEWS_API_KEY", "")
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
    Falls back gracefully to empty list if NEWS_API_KEY is missing.
    """
    if not NEWS_API_KEY:
        logger.debug("NEWS_API_KEY not set — skipping NewsAPI fetch")
        return []
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


_RSS_FEEDS = [
    ("https://cointelegraph.com/rss", "CoinTelegraph"),
    ("https://decrypt.co/feed", "Decrypt"),
    ("https://bitcoinmagazine.com/.rss/full/", "Bitcoin Magazine"),
    ("https://cryptoslate.com/feed/", "CryptoSlate"),
    ("https://cryptobriefing.com/feed/", "Crypto Briefing"),
    ("https://thedefiant.io/feed", "The Defiant"),
    ("https://blockworks.co/feed", "Blockworks"),
    ("https://www.theblock.co/rss.xml", "The Block"),
    ("https://cryptopotato.com/feed/", "CryptoPotato"),
    ("https://ambcrypto.com/feed/", "AMBCrypto"),
    ("https://newsbtc.com/feed/", "NewsBTC"),
    ("https://coinjournal.net/feed/", "CoinJournal"),
    ("https://u.today/rss", "U.Today"),
    ("https://beincrypto.com/feed/", "BeInCrypto"),
]


def fetch_rss_news() -> list[dict]:
    """
    Fetch recent crypto articles from RSS feeds.

    Returns a list of dicts with keys:
        title, url, source, published_at
    Entries older than 3 hours are excluded. Results are deduplicated by URL.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(hours=3)
    seen_urls: set[str] = set()
    results: list[dict] = []

    for feed_url, default_source in _RSS_FEEDS:
        try:
            feed = feedparser.parse(feed_url)
            if feed.bozo and not feed.entries:
                logger.warning("RSS parse error for %s: %s", feed_url, feed.bozo_exception)
                continue
            source = feed.feed.get("title", default_source)
            for entry in feed.entries:
                url = entry.get("link", "")
                if not url or url in seen_urls:
                    continue
                title = entry.get("title", "").strip()
                if not title:
                    continue
                published_parsed = entry.get("published_parsed")
                if published_parsed:
                    published_dt = datetime(*published_parsed[:6], tzinfo=timezone.utc)
                    if published_dt < cutoff:
                        continue
                    published_at = published_dt.strftime("%Y-%m-%dT%H:%M:%SZ")
                else:
                    published_at = ""
                seen_urls.add(url)
                results.append({
                    "title": title,
                    "url": url,
                    "source": source,
                    "published_at": published_at,
                })
        except Exception as exc:
            logger.warning("RSS fetch failed for %s: %s", feed_url, exc)

    return results
