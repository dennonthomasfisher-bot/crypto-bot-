"""
strategies/sentiment.py – News sentiment signal from crypto RSS feeds.

Scans the latest headlines and summaries from CoinTelegraph, CoinDesk, and
Bitcoin Magazine, counting bullish vs bearish keyword occurrences.

The aggregate signal is the average score across all configured feeds,
clamped to [-1.0, +1.0].  Results are cached for CACHE_TTL seconds to avoid
hammering the feeds on every polling cycle.
"""
from __future__ import annotations

import logging
import time
from typing import Dict, List, Tuple

import feedparser

logger = logging.getLogger(__name__)

# ── Feed sources ──────────────────────────────────────────────────────────────

RSS_FEEDS: Dict[str, str] = {
    "CoinTelegraph":  "https://cointelegraph.com/rss",
    "CoinDesk":       "https://www.coindesk.com/arc/outboundfeeds/rss/",
    "Bitcoin Magazine": "https://bitcoinmagazine.com/.rss/full/",
}

# ── Keyword dictionaries ──────────────────────────────────────────────────────

BULLISH_KEYWORDS: frozenset = frozenset({
    "bull", "bullish", "surge", "surged", "surging",
    "rally", "rallied", "rallying",
    "gain", "gains", "gained",
    "pump", "pumped", "pumping",
    "moon", "mooning",
    "breakout", "broke out",
    "all-time high", "ath",
    "adoption", "adopted",
    "positive", "optimistic",
    "growth", "growing",
    "rise", "rose", "rising",
    "upward", "uptrend",
    "record high", "milestone",
    "buy", "accumulate", "accumulating",
    "recovery", "recovering", "bounce", "bounced",
    "outperform", "upgrade", "upgraded",
    "launch", "launched", "partnership", "approved", "approval",
    "etf approval", "institutional",
})

BEARISH_KEYWORDS: frozenset = frozenset({
    "bear", "bearish",
    "crash", "crashed", "crashing",
    "dump", "dumped", "dumping",
    "drop", "dropped", "dropping",
    "fall", "fell", "falling",
    "decline", "declined", "declining",
    "plunge", "plunged", "plunging",
    "negative", "pessimistic",
    "ban", "banned", "banning",
    "hack", "hacked", "hacking",
    "fraud", "fraudulent",
    "loss", "losses",
    "fear", "fud",
    "downturn", "correction",
    "collapse", "collapsed",
    "sell-off", "selloff",
    "lawsuit", "sued", "fine", "penalty",
    "investigation", "investigated",
    "exploit", "vulnerability",
    "delist", "delisted",
    "shutdown",
})

# Cache TTL in seconds (10 minutes)
CACHE_TTL: int = 600


class SentimentAnalyzer:
    """Aggregate news sentiment from crypto RSS feeds."""

    def __init__(self) -> None:
        # Maps feed URL → (fetch_timestamp, score)
        self._cache: Dict[str, Tuple[float, float]] = {}

    # ── Internal helpers ──────────────────────────────────────────────────────

    def _score_text(self, text: str) -> Tuple[int, int]:
        """
        Count bullish and bearish keyword hits in `text` (case-insensitive).

        Returns (bull_count, bear_count).
        """
        lowered = text.lower()
        bull = sum(1 for kw in BULLISH_KEYWORDS if kw in lowered)
        bear = sum(1 for kw in BEARISH_KEYWORDS if kw in lowered)
        return bull, bear

    def _fetch_feed(self, url: str) -> float:
        """
        Parse an RSS feed and return a sentiment score in [-1.0, +1.0].
        Results are served from the in-memory cache for up to CACHE_TTL seconds.
        """
        cached = self._cache.get(url)
        if cached and (time.monotonic() - cached[0]) < CACHE_TTL:
            return cached[1]

        try:
            feed = feedparser.parse(url)
        except Exception as exc:
            logger.warning("Could not parse feed %s: %s", url, exc)
            return 0.0

        if not feed.entries:
            logger.debug("Feed %s returned no entries", url)
            return 0.0

        total_bull = total_bear = 0
        for entry in feed.entries:
            text = (
                (entry.get("title") or "")
                + " "
                + (entry.get("summary") or "")
            )
            bull, bear = self._score_text(text)
            total_bull += bull
            total_bear += bear

        total = total_bull + total_bear
        score = (total_bull - total_bear) / total if total else 0.0
        score = max(-1.0, min(1.0, score))

        self._cache[url] = (time.monotonic(), score)
        logger.debug(
            "Sentiment %-20s  bull=%3d  bear=%3d  score=%+.3f",
            url.split("/")[2], total_bull, total_bear, score,
        )
        return score

    # ── Public API ────────────────────────────────────────────────────────────

    def aggregate_signal(self) -> float:
        """
        Fetch all configured RSS feeds and return the average sentiment score.

        Returns
        -------
        float in [-1.0, +1.0]
          +1.0  strongly bullish
          -1.0  strongly bearish
           0.0  neutral or no data
        """
        scores: List[float] = []
        for name, url in RSS_FEEDS.items():
            try:
                score = self._fetch_feed(url)
                scores.append(score)
                logger.debug("%-20s  sentiment=%+.3f", name, score)
            except Exception as exc:
                logger.warning("Sentiment error for %s: %s", name, exc)

        if not scores:
            return 0.0

        result = sum(scores) / len(scores)
        logger.debug("Aggregate sentiment: %+.3f", result)
        return result
