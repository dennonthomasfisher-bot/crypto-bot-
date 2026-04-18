"""
Trend Spotter — monitors trending crypto topics and generates timely tweets.

Checks CoinGecko trending, Binance top movers, and RSS headlines
to identify what's hot RIGHT NOW. Generates a tweet if something
is trending that the bot hasn't covered yet.

Runs every 30 minutes via bot scheduler.
"""
from __future__ import annotations

import logging
import time
import requests
import json

import ai_writer
import config

logger = logging.getLogger(__name__)

_BINANCE_TICKER = "https://api.binance.com/api/v3/ticker/24hr"
_COINGECKO_TRENDING = "https://api.coingecko.com/api/v3/search/trending"

# Track what we've already covered to avoid duplicates
_covered_topics: list[str] = []
_MAX_COVERED = 20


def _get_top_movers() -> list[dict]:
    """Get coins with biggest 24h moves from Binance."""
    pairs = [
        "BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT", "BNBUSDT",
        "ADAUSDT", "DOGEUSDT", "AVAXUSDT", "DOTUSDT", "LINKUSDT",
        "SUIUSDT", "APTUSDT", "NEARUSDT", "ARBUSDT", "OPUSDT",
    ]
    try:
        resp = requests.get(_BINANCE_TICKER,
                           params={"symbols": json.dumps(pairs, separators=(",", ":"))}, timeout=10)
        resp.raise_for_status()
        tickers = resp.json()
        movers = []
        for t in tickers:
            pct = float(t["priceChangePercent"])
            price = float(t["lastPrice"])
            vol = float(t["quoteVolume"])
            sym = t["symbol"].replace("USDT", "")
            movers.append({
                "symbol": sym,
                "price": price,
                "pct_24h": pct,
                "volume_usd": vol,
            })
        # Sort by absolute % change
        movers.sort(key=lambda x: abs(x["pct_24h"]), reverse=True)
        return movers
    except Exception as exc:
        logger.warning("Failed to fetch top movers: %s", exc)
        return []


def _get_trending_coins() -> list[dict]:
    """Get trending coins from CoinGecko."""
    try:
        resp = requests.get(_COINGECKO_TRENDING, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        trending = []
        for item in data.get("coins", [])[:5]:
            coin = item.get("item", {})
            trending.append({
                "name": coin.get("name", ""),
                "symbol": coin.get("symbol", ""),
                "market_cap_rank": coin.get("market_cap_rank"),
            })
        return trending
    except Exception as exc:
        logger.debug("CoinGecko trending fetch failed: %s", exc)
        return []


def _already_covered(topic: str) -> bool:
    """Check if we've already posted about this topic recently."""
    topic_lower = topic.lower()
    return any(topic_lower in c for c in _covered_topics)


def _record_covered(topic: str) -> None:
    """Mark a topic as covered."""
    global _covered_topics
    _covered_topics.append(topic.lower())
    if len(_covered_topics) > _MAX_COVERED:
        _covered_topics = _covered_topics[-_MAX_COVERED:]


def generate_trend_tweet() -> tuple[str | None, str | None, str | None]:
    """Find the hottest trending topic and generate a tweet about it.

    Returns (tweet_text, coin_id_for_chart, symbol) or (None, None, None) if
    nothing trending. The symbol is used for chart labelling.
    """
    # Check top movers first — big moves are the most tweetable
    movers = _get_top_movers()
    for mover in movers[:3]:
        sym = mover["symbol"]
        pct = mover["pct_24h"]

        # Only care about significant moves (>5%)
        if abs(pct) < 5.0:
            continue

        if _already_covered(sym):
            continue

        # Build context
        price = mover["price"]
        vol_b = mover["volume_usd"] / 1e9

        try:
            import market_data
            enriched = market_data.get_technical_context(f"{sym}USDT")
        except Exception:
            enriched = ""

        direction = "surging" if pct > 0 else "dumping"
        price_str = f"${price:,.0f}" if price >= 1000 else f"${price:,.2f}"

        prompt = (
            f"{sym} is {direction} {pct:+.1f}% in 24h. Price: {price_str}. "
            f"24h volume: ${vol_b:.1f}B.\n"
        )
        if enriched:
            prompt += f"{enriched}\n"
        prompt += (
            "\nWrite a tweet about this move using the HEADLINE + context + level format:\n"
            "HEADLINE IN CAPS (3-8 words)\n\n"
            "What's happening with real data.\n\n"
            "Directional call.\n\n"
            f"This tweet is ONLY about {sym}. Use ONLY the data provided."
        )

        tweet = ai_writer._call_claude_safe(ai_writer._SYSTEM, prompt, max_tokens=150)
        if tweet and len(tweet) > 30:
            tweet = ai_writer._strip_emojis(tweet)
            tweet = ai_writer._truncate_tweet(tweet, limit=275)
            _record_covered(sym)

            # Map symbol to coin_id for chart
            coin_map = {
                "BTC": "bitcoin", "ETH": "ethereum", "SOL": "solana",
                "XRP": "ripple", "BNB": "binancecoin", "ADA": "cardano",
                "DOGE": "dogecoin", "AVAX": "avalanche-2", "DOT": "polkadot",
                "LINK": "chainlink", "SUI": "sui", "APT": "aptos",
                "NEAR": "near", "ARB": "arbitrum", "OP": "optimism",
            }
            coin_id = coin_map.get(sym, "bitcoin")
            logger.info("[TREND] Generated trend tweet for %s (%+.1f%%): %.60s",
                       sym, pct, tweet)
            return tweet, coin_id, sym

    # CoinGecko trending fallback removed: it surfaced obscure top-900 coins
    # with no price data, which produced low-quality dismissive tweets like
    # "RANK 914 AND TRENDING. READ THAT AGAIN." Trend tweets now only fire
    # when a real Binance mover (known ticker, real price data) is detected.
    logger.info("[TREND] No significant Binance movers this cycle")
    return None, None, None
