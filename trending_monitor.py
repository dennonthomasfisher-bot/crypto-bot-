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
            if abs(pct) >= config.TRENDING_SURGE_PCT:
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
            direction = "pumping" if pct > 0 else "dumping"
            prompt = f"""Write a tweet about {name} ({symbol}) {direction} hard.

{symbol}: {price_str} ({sign}{pct:.1f}% 24h)
Market cap rank: #{rank or '?'}

This is NOT a coin we normally cover — you spotted it moving.
Make a CALL: is this the start of a bigger move, or a trap? Give a level to watch.
DO NOT say "worth watching", "worth a closer look", or "could be something".
Instead say WHERE it goes next: "breaks $X and this runs to $Y" or "dead cat bounce, avoid."

Keep it under 275 chars. NO hashtags.

Write the tweet now. Nothing else."""
            system = "You are @CoinWatchAlert. When you spot a move outside the usual names, you make a quick call — not a wishy-washy observation. Direction + level + conviction."
            ai_tweet = ai_writer._call_claude(system, prompt)
            if ai_tweet and len(ai_tweet) <= 275:
                _record(alert["id"])
                return ai_tweet

        # Template fallback
        _record(alert["id"])
        direction_word = "ripping" if pct > 0 else "dumping"
        next_move = "break higher and this runs" if pct > 0 else "no real support visible — more downside likely"
        return (
            f"{emoji} {symbol} {direction_word} {sign}{pct:.1f}% — now {price_str}"
            f"{f' (rank #{rank})' if rank else ''}\n"
            f"\n"
            f"{next_move}."
        )

    else:  # trending search
        # Try AI
        if ai_writer.is_available():
            prompt = f"""Write a tweet about {name} ({symbol}) trending on CoinGecko.

Market cap rank: #{rank or '?'}

Search interest is spiking. Don't just report that it's trending — take a STANCE.
Is this legit momentum or bag holders pumping search? Say why or why not.
DO NOT say "worth watching", "worth a closer look", "could be something or just noise."
Make a call: "This has legs because X" or "Hype with no substance — avoid."

Keep it under 275 chars. NO hashtags.

Write the tweet now. Nothing else."""
            system = "You are @CoinWatchAlert. When a coin starts trending, you tell people whether to pay attention or ignore it — with a reason. Never sit on the fence."
            ai_tweet = ai_writer._call_claude(system, prompt)
            if ai_tweet and len(ai_tweet) <= 275:
                _record(alert["id"])
                return ai_tweet

        # Template fallback
        _record(alert["id"])
        rank_str = f" (rank #{rank})" if rank else ""
        return (
            f"{symbol}{rank_str} search interest spiking on CoinGecko.\n"
            f"\n"
            f"No price catalyst yet — pure speculation or early accumulation. Avoid chasing without a level."
        )

    return None
