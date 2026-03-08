"""
Trending coin monitor – detects coins surging outside the configured
watchlist by scanning CoinGecko's trending and top movers endpoints.

Posts tweets when a coin outside the tracked list is pumping hard.
"""
from __future__ import annotations

import logging
import time

import requests

import config
import state
import ai_writer

logger = logging.getLogger(__name__)

# Coins we already track — skip these
_TRACKED_IDS = set(config.COINS.keys())

# In-memory cooldown to avoid spamming the same trending coin
_recently_tweeted: dict[str, float] = {}
_TRENDING_COOLDOWN = 14400  # 4 hours before tweeting same trending coin


def _cooldown_ok(coin_id: str) -> bool:
    now = time.time()
    last = _recently_tweeted.get(coin_id, 0)
    return (now - last) >= _TRENDING_COOLDOWN


def _record(coin_id: str) -> None:
    _recently_tweeted[coin_id] = time.time()
    # Prune old entries
    cutoff = time.time() - _TRENDING_COOLDOWN * 2
    expired = [k for k, v in _recently_tweeted.items() if v < cutoff]
    for k in expired:
        del _recently_tweeted[k]


def fetch_trending() -> list[dict]:
    """
    Fetch CoinGecko trending coins (free endpoint, no key).

    Returns list of dicts with: id, symbol, name, market_cap_rank, price_btc
    """
    try:
        resp = requests.get(
            f"{config.COINGECKO_BASE}/search/trending",
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()
        coins = data.get("coins", [])
        results = []
        for entry in coins:
            coin = entry.get("item", {})
            results.append({
                "id": coin.get("id", ""),
                "symbol": coin.get("symbol", "").upper(),
                "name": coin.get("name", ""),
                "market_cap_rank": coin.get("market_cap_rank"),
                "price_btc": coin.get("price_btc", 0),
                "score": coin.get("score", 0),
            })
        return results
    except requests.RequestException as exc:
        logger.warning("CoinGecko trending fetch failed: %s", exc)
        return []


def fetch_top_movers() -> list[dict]:
    """
    Fetch top 250 coins and find those with big 24h moves that we don't
    already track. Returns coins moving 10%+ in 24h.
    """
    try:
        resp = requests.get(
            f"{config.COINGECKO_BASE}/coins/markets",
            params={
                "vs_currency": "usd",
                "order": "market_cap_desc",
                "per_page": 250,
                "page": 1,
                "price_change_percentage": "24h",
            },
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()

        movers = []
        for coin in data:
            coin_id = coin.get("id", "")
            if coin_id in _TRACKED_IDS:
                continue  # already covered by price_monitor
            pct = coin.get("price_change_percentage_24h_in_currency") or 0
            if abs(pct) >= 10.0:
                movers.append({
                    "id": coin_id,
                    "symbol": coin.get("symbol", "").upper(),
                    "name": coin.get("name", ""),
                    "current_price": coin.get("current_price", 0),
                    "pct_24h": pct,
                    "market_cap_rank": coin.get("market_cap_rank"),
                    "market_cap": coin.get("market_cap", 0),
                })
        # Sort by absolute move size
        movers.sort(key=lambda c: abs(c["pct_24h"]), reverse=True)
        return movers[:5]

    except requests.RequestException as exc:
        logger.warning("CoinGecko top movers fetch failed: %s", exc)
        return []


def check_trending() -> list[dict]:
    """
    Main entry point — returns tweetable trending coin alerts.

    Checks both trending search and big movers, deduplicates, and
    respects cooldowns.
    """
    alerts = []

    # 1. Check CoinGecko trending (search popularity)
    trending = fetch_trending()
    for coin in trending[:5]:
        coin_id = coin["id"]
        if coin_id in _TRACKED_IDS:
            continue
        if not _cooldown_ok(coin_id):
            continue
        alerts.append({
            "source": "trending",
            "id": coin_id,
            "symbol": coin["symbol"],
            "name": coin["name"],
            "market_cap_rank": coin.get("market_cap_rank"),
        })

    # 2. Check big movers (price action)
    movers = fetch_top_movers()
    seen_ids = {a["id"] for a in alerts}
    for coin in movers:
        if coin["id"] in seen_ids:
            continue
        if not _cooldown_ok(coin["id"]):
            continue
        alerts.append({
            "source": "mover",
            "id": coin["id"],
            "symbol": coin["symbol"],
            "name": coin["name"],
            "current_price": coin.get("current_price", 0),
            "pct_24h": coin.get("pct_24h", 0),
            "market_cap_rank": coin.get("market_cap_rank"),
        })

    return alerts[:3]  # max 3 alerts per check


def format_trending_tweet(alert: dict) -> str | None:
    """Format a trending coin alert into a tweet."""
    symbol = alert["symbol"]
    name = alert["name"]
    rank = alert.get("market_cap_rank")

    if alert["source"] == "mover":
        price = alert.get("current_price", 0)
        pct = alert.get("pct_24h", 0)
        emoji = "🟢" if pct > 0 else "🔴"
        sign = "+" if pct > 0 else ""
        price_str = f"${price:,.2f}" if price < 1000 else f"${price:,.0f}"
        if price < 0.01:
            price_str = f"${price:.6f}"

        # Try AI first
        if ai_writer.is_available():
            prompt = f"""Write a tweet about {name} ({symbol}) making a big move.

{symbol}: {price_str} ({sign}{pct:.1f}% 24h)
Market cap rank: #{rank or '?'}

This is NOT a coin we normally cover. Make it sound like you spotted something interesting.
Keep it under 275 chars. NO hashtags. Sound like a trader who just noticed this move.

Write the tweet now. Nothing else."""
            system = "You are @CoinWatchAlert. You spot moves across the entire market, not just the top coins. Quick, data-driven, no hype."
            ai_tweet = ai_writer._call_claude(system, prompt)
            if ai_tweet and len(ai_tweet) <= 280:
                _record(alert["id"])
                return ai_tweet

        # Template fallback
        _record(alert["id"])
        return (
            f"{emoji} {symbol} ({name}) {sign}{pct:.1f}% in 24h\n"
            f"\n"
            f"Currently at {price_str}"
            f"{f' (rank #{rank})' if rank else ''}\n"
            f"\n"
            f"Not one of the usual suspects — worth watching."
        )

    else:  # trending search
        # Try AI
        if ai_writer.is_available():
            prompt = f"""Write a tweet about {name} ({symbol}) trending on CoinGecko right now.

Market cap rank: #{rank or '?'}

This coin is gaining search interest. Make it sound curious/interesting, not hype.
Keep it under 275 chars. NO hashtags.

Write the tweet now. Nothing else."""
            system = "You are @CoinWatchAlert. You notice when coins start trending before the crowd catches on."
            ai_tweet = ai_writer._call_claude(system, prompt)
            if ai_tweet and len(ai_tweet) <= 280:
                _record(alert["id"])
                return ai_tweet

        # Template fallback
        _record(alert["id"])
        return (
            f"{symbol} ({name}) trending on CoinGecko right now"
            f"{f' — rank #{rank}' if rank else ''}\n"
            f"\n"
            f"Search interest spiking. Early signal or just noise?"
        )

    return None
