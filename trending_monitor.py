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

# Only tweet about coins ranked top-100 by market cap
_MCAP_RANK_LIMIT = 100

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


def _fetch_coin_details(coin_id: str) -> dict | None:
    """
    Fetch price, 24h change, volume, and logo URL for a single coin.
    Returns None if the request fails or price/volume are missing — callers
    must skip the coin entirely in that case.
    """
    try:
        resp = requests.get(
            f"{config.COINGECKO_BASE}/coins/markets",
            params={
                "vs_currency": "usd",
                "ids": coin_id,
                "price_change_percentage": "24h",
            },
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()
        if not data:
            return None
        coin = data[0]
        price = coin.get("current_price")
        volume = coin.get("total_volume")
        if not price or not volume:
            logger.debug("Skipping %s — no price or volume data", coin_id)
            return None
        return {
            "current_price": price,
            "pct_24h":       coin.get("price_change_percentage_24h_in_currency") or 0,
            "market_cap":    coin.get("market_cap", 0),
            "volume_24h":    volume,
            "image":         coin.get("image"),
        }
    except requests.RequestException as exc:
        logger.warning("Coin details fetch failed for %s: %s", coin_id, exc)
        return None


def fetch_trending() -> list[dict]:
    """
    Fetch CoinGecko trending coins (free endpoint, no key).

    Returns list of dicts with: id, symbol, name, market_cap_rank, price_btc
    """
    print("[DEBUG fetch_trending] START", flush=True)
    logger.warning("[DEBUG fetch_trending] START")
    try:
        resp = requests.get(
            f"{config.COINGECKO_BASE}/search/trending",
            timeout=15,
        )
        print(f"[DEBUG fetch_trending] HTTP {resp.status_code}", flush=True)
        logger.warning("[DEBUG fetch_trending] HTTP %s", resp.status_code)
        resp.raise_for_status()
        data = resp.json()
        coins = data.get("coins", [])
        print(f"[DEBUG fetch_trending] coins in response: {len(coins)}", flush=True)
        logger.warning("[DEBUG fetch_trending] coins in response: %d", len(coins))
        results = []
        for entry in coins:
            coin = entry.get("item", {})
            # score is the 0-indexed position in CoinGecko's trending list,
            # so trending_rank 1 = most-searched coin right now.
            score = coin.get("score", 0)
            try:
                trending_rank = int(score) + 1
            except (TypeError, ValueError):
                trending_rank = None
            # market_cap_rank can be null for very new / unranked tokens.
            mcap_rank = coin.get("market_cap_rank")
            if mcap_rank is not None:
                try:
                    mcap_rank = int(mcap_rank)
                except (TypeError, ValueError):
                    mcap_rank = None
            results.append({
                "id":              coin.get("id", ""),
                "symbol":          coin.get("symbol", "").upper(),
                "name":            coin.get("name", ""),
                "market_cap_rank": mcap_rank,
                "trending_rank":   trending_rank,
                "price_btc":       coin.get("price_btc", 0),
                "score":           score,
                "source":          "trending",
            })
        print(f"[DEBUG fetch_trending] END returning {len(results)} results", flush=True)
        logger.warning("[DEBUG fetch_trending] END returning %d results", len(results))
        return results
    except requests.RequestException as exc:
        print(f"[DEBUG fetch_trending] RequestException: {exc}", flush=True)
        logger.warning("[DEBUG fetch_trending] RequestException: %s", exc)
        return []
    except Exception as exc:
        print(f"[DEBUG fetch_trending] Unexpected exception: {type(exc).__name__}: {exc}", flush=True)
        logger.warning("[DEBUG fetch_trending] Unexpected exception: %s: %s", type(exc).__name__, exc)
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
                    "id":            coin_id,
                    "symbol":        coin.get("symbol", "").upper(),
                    "name":          coin.get("name", ""),
                    "current_price": coin.get("current_price", 0),
                    "pct_24h":       pct,
                    "market_cap_rank": coin.get("market_cap_rank"),
                    "market_cap":    coin.get("market_cap", 0),
                    "volume_24h":    coin.get("total_volume", 0),
                    "image":         coin.get("image"),
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

    Guards (all must pass):
      1. Not already in our main watchlist
      2. Market cap rank ≤ 100
      3. Per-coin cooldown clear (4 h)
      4. Price AND volume data available from CoinGecko
    """
    alerts = []

    # 1. CoinGecko trending (search popularity)
    trending = fetch_trending()
    for coin in trending[:5]:
        coin_id = coin["id"]
        if coin_id in _TRACKED_IDS:
            continue
        mcap_rank = coin.get("market_cap_rank")
        if mcap_rank is None or mcap_rank > _MCAP_RANK_LIMIT:
            logger.debug("Trending skip %s — mcap rank %s > %d",
                         coin_id, mcap_rank, _MCAP_RANK_LIMIT)
            continue
        if not _cooldown_ok(coin_id):
            continue
        details = _fetch_coin_details(coin_id)
        if not details:
            logger.debug("Trending skip %s — no price/volume data", coin_id)
            continue
        alerts.append({
            "source":          "trending",
            "id":              coin_id,
            "symbol":          coin["symbol"],
            "name":            coin["name"],
            "trending_rank":   coin.get("trending_rank"),
            "market_cap_rank": mcap_rank,
            **details,
        })

    # 2. Big movers (price action)
    movers = fetch_top_movers()
    seen_ids = {a["id"] for a in alerts}
    for coin in movers:
        if coin["id"] in seen_ids:
            continue
        mcap_rank = coin.get("market_cap_rank")
        if mcap_rank is None or mcap_rank > _MCAP_RANK_LIMIT:
            logger.debug("Mover skip %s — mcap rank %s > %d",
                         coin["id"], mcap_rank, _MCAP_RANK_LIMIT)
            continue
        if not _cooldown_ok(coin["id"]):
            continue
        if not coin.get("current_price") or not coin.get("volume_24h"):
            logger.debug("Mover skip %s — missing price or volume", coin["id"])
            continue
        alerts.append({
            "source":          "mover",
            "id":              coin["id"],
            "symbol":          coin["symbol"],
            "name":            coin["name"],
            "current_price":   coin["current_price"],
            "pct_24h":         coin.get("pct_24h", 0),
            "market_cap_rank": mcap_rank,
            "market_cap":      coin.get("market_cap", 0),
            "volume_24h":      coin.get("volume_24h", 0),
            "image":           coin.get("image"),
        })

    return alerts[:3]  # max 3 candidates per check; daily cap enforced in bot.py


def format_trending_tweet(alert: dict) -> str | None:
    """Format a trending coin alert into a tweet."""
    print(f"[DEBUG format_trending_tweet] START alert keys={list(alert.keys())} source={alert.get('source')} symbol={alert.get('symbol')}", flush=True)
    logger.warning("[DEBUG format_trending_tweet] START alert keys=%s source=%s symbol=%s", list(alert.keys()), alert.get("source"), alert.get("symbol"))
    symbol = alert["symbol"]
    name   = alert["name"]

    # For "trending" alerts: show the CoinGecko trending position (1 = most searched).
    # For "mover" alerts: show market cap rank if available.
    if alert["source"] == "trending":
        rank        = alert.get("trending_rank")        # e.g. 1, 2, 3 …
        mcap_rank   = alert.get("market_cap_rank")
        rank_label  = f"#{rank} trending on CoinGecko" if rank is not None else "trending on CoinGecko"
        mcap_label  = f"market cap rank #{mcap_rank}" if mcap_rank is not None else None
    else:
        rank        = alert.get("market_cap_rank")
        rank_label  = f"rank #{rank}" if rank is not None else None
        mcap_label  = None

    if alert["source"] == "mover":
        price = alert.get("current_price", 0)
        pct   = alert.get("pct_24h", 0)
        emoji = "🟢" if pct > 0 else "🔴"
        sign  = "+" if pct > 0 else ""
        if price < 0.01:
            price_str = f"${price:.6f}"
        elif price < 1000:
            price_str = f"${price:,.2f}"
        else:
            price_str = f"${price:,.0f}"

        rank_context = f" ({rank_label})" if rank_label else ""

        # Try AI first
        if ai_writer.is_available():
            direction = "pumping" if pct > 0 else "dumping"
            prompt = f"""Write a tweet about {name} ({symbol}) {direction} hard.

{symbol}: {price_str} ({sign}{pct:.1f}% 24h){rank_context}

This is NOT a coin we normally cover — you spotted it moving.
Make a CALL: is this the start of a bigger move, or a trap? Give a level to watch.
DO NOT say "worth watching", "worth a closer look", or "could be something".
Instead say WHERE it goes next: "breaks $X and this runs to $Y" or "dead cat bounce, avoid."

Keep it under 275 chars. NO hashtags.

Write the tweet now. Nothing else."""
            system = "You are @CoinWatchAlert. When you spot a move outside the usual names, you make a quick call — not a wishy-washy observation. Direction + level + conviction."
            ai_tweet = ai_writer._call_claude(system, prompt)
            if ai_tweet and len(ai_tweet) <= 275:
                print(f"[DEBUG format_trending_tweet] returning AI mover tweet ({len(ai_tweet)} chars)", flush=True)
                logger.warning("[DEBUG format_trending_tweet] returning AI mover tweet (%d chars)", len(ai_tweet))
                _record(alert["id"])
                return ai_tweet
            else:
                print(f"[DEBUG format_trending_tweet] AI mover tweet rejected: ai_tweet={bool(ai_tweet)} len={len(ai_tweet) if ai_tweet else 0}", flush=True)
                logger.warning("[DEBUG format_trending_tweet] AI mover tweet rejected: ai_tweet=%s len=%d", bool(ai_tweet), len(ai_tweet) if ai_tweet else 0)

        # Template fallback
        print("[DEBUG format_trending_tweet] returning mover template fallback", flush=True)
        logger.warning("[DEBUG format_trending_tweet] returning mover template fallback")
        _record(alert["id"])
        direction_word = "ripping" if pct > 0 else "dumping"
        next_move = "break higher and this runs" if pct > 0 else "no real support visible — more downside likely"
        return (
            f"{emoji} {symbol} {direction_word} {sign}{pct:.1f}% — now {price_str}"
            f"{f' ({rank_label})' if rank_label else ''}\n"
            f"\n{next_move}."
        )

    else:  # trending search
        rank_context = rank_label  # already formatted, e.g. "#1 trending on CoinGecko"
        mcap_context = f", {mcap_label}" if mcap_label else ""

        # Try AI
        if ai_writer.is_available():
            prompt = f"""Write a tweet about {name} ({symbol}) {rank_context}{mcap_context}.

Search interest is spiking. Don't just report that it's trending — take a STANCE.
Is this legit momentum or bag holders pumping search? Say why or why not.
DO NOT say "worth watching", "worth a closer look", "could be something or just noise."
Make a call: "This has legs because X" or "Hype with no substance — avoid."

Keep it under 275 chars. NO hashtags.

Write the tweet now. Nothing else."""
            system = "You are @CoinWatchAlert. When a coin starts trending, you tell people whether to pay attention or ignore it — with a reason. Never sit on the fence."
            ai_tweet = ai_writer._call_claude(system, prompt)
            if ai_tweet and len(ai_tweet) <= 275:
                print(f"[DEBUG format_trending_tweet] returning AI trending tweet ({len(ai_tweet)} chars)", flush=True)
                logger.warning("[DEBUG format_trending_tweet] returning AI trending tweet (%d chars)", len(ai_tweet))
                _record(alert["id"])
                return ai_tweet
            else:
                print(f"[DEBUG format_trending_tweet] AI trending tweet rejected: ai_tweet={bool(ai_tweet)} len={len(ai_tweet) if ai_tweet else 0}", flush=True)
                logger.warning("[DEBUG format_trending_tweet] AI trending tweet rejected: ai_tweet=%s len=%d", bool(ai_tweet), len(ai_tweet) if ai_tweet else 0)

        # Template fallback
        print("[DEBUG format_trending_tweet] returning trending template fallback", flush=True)
        logger.warning("[DEBUG format_trending_tweet] returning trending template fallback")
        _record(alert["id"])
        return (
            f"{symbol} {rank_context}{mcap_context} — search interest spiking.\n"
            f"\nNo price catalyst yet — pure speculation or early accumulation. Avoid chasing without a level."
        )
