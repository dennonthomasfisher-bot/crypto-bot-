"""
polymarket.py – Polymarket prediction market integration.

Fetches crypto-related markets from Polymarket's public Gamma API,
tracks odds changes in bot_state.json, and surfaces significant moves
as tweet alerts.

Public API — no auth required:
    https://gamma-api.polymarket.com/markets
"""

import json
import logging
import time
from typing import Optional

import requests

import bot_state

logger = logging.getLogger(__name__)

_GAMMA_MARKETS = "https://gamma-api.polymarket.com/markets"

# Keywords used to classify a market as crypto-related (matched case-insensitively).
# A market question must contain at least one of these — no partial matches on
# generic words like "etf", "staking", "blackrock" that bleed in non-crypto markets.
_CRYPTO_KEYWORDS = {
    "bitcoin", "btc", "ethereum", "eth", "crypto", "solana", "sol",
    "xrp", "ripple", "defi", "nft", "altcoin", "blockchain",
    "coinbase", "binance", "stablecoin", "usdc", "usdt",
    "megaeth", "base", "arbitrum",
}

# A YES-probability shift of this many percentage points triggers an alert
ODDS_SHIFT_THRESHOLD = 0.10  # 10 pp

# How far back to compare odds against (seconds)
_ODDS_WINDOW = 24 * 3600  # 24 h

_session: Optional[requests.Session] = None


def _get_session() -> requests.Session:
    global _session
    if _session is None:
        _session = requests.Session()
        _session.headers.update({"User-Agent": "crypto-news-bot/1.0"})
    return _session


def _parse_yes_price(market: dict) -> Optional[float]:
    """Return the YES probability (0–1) from a market dict, or None on failure."""
    raw = market.get("outcomePrices")
    if raw is None:
        return None
    try:
        prices = json.loads(raw) if isinstance(raw, str) else raw
        return float(prices[0])
    except (ValueError, IndexError, json.JSONDecodeError, TypeError):
        return None


def _is_crypto(market: dict) -> bool:
    text = (market.get("question") or "").lower()
    return any(kw in text for kw in _CRYPTO_KEYWORDS)


def _fetch_raw(limit: int = 100) -> list:
    """Fetch active, open markets from the Gamma API."""
    try:
        resp = _get_session().get(
            _GAMMA_MARKETS,
            params={"limit": limit, "active": "true", "closed": "false"},
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()
        return data if isinstance(data, list) else []
    except requests.RequestException as exc:
        logger.warning("Polymarket API error: %s", exc)
        return []


def _to_market_dict(raw: dict, yes_price: float) -> dict:
    """Normalise a raw API market into a consistent internal dict."""
    volume = float(raw.get("volumeNum") or 0)
    liquidity = float(raw.get("liquidityNum") or 0)
    return {
        "id":        raw.get("id", ""),
        "question":  raw.get("question", ""),
        "yes_price": yes_price,
        "volume":    volume,
        "liquidity": liquidity,
    }


def get_top_markets(n: int = 5) -> list:
    """
    Return the *n* most liquid active crypto prediction markets with current odds.

    Each item: {id, question, yes_price, volume, liquidity}
    """
    raw_markets = _fetch_raw(limit=200)
    result = []
    # Sort by liquidity descending before filtering so we pick the best ones
    crypto = [m for m in raw_markets if _is_crypto(m) and not m.get("closed")]
    crypto.sort(key=lambda m: float(m.get("liquidityNum") or 0), reverse=True)
    for m in crypto:
        yes = _parse_yes_price(m)
        if yes is None:
            continue
        result.append(_to_market_dict(m, yes))
        if len(result) >= n:
            break
    return result


def get_polymarket_alerts() -> list:
    """
    Return markets where the YES probability has shifted by ≥ ODDS_SHIFT_THRESHOLD
    (10 pp) since the last snapshot stored in bot_state.json.

    Snapshots are always updated after each call so the next cycle compares
    against the current price, not the original one.

    Each alert dict: {id, question, yes_price, yes_prev, shift, direction,
                      volume, liquidity}
    """
    raw_markets = _fetch_raw(limit=200)
    crypto = [m for m in raw_markets if _is_crypto(m) and not m.get("closed")]

    alerts = []
    now = time.time()

    for m in crypto:
        market_id = m.get("id", "")
        if not market_id:
            continue

        yes_now = _parse_yes_price(m)
        if yes_now is None:
            continue

        snapshot = bot_state.get_polymarket_snapshot(market_id)
        if snapshot:
            yes_then = snapshot["yes_price"]
            age = now - snapshot["ts"]
            shift = abs(yes_now - yes_then)
            if age <= _ODDS_WINDOW and shift >= ODDS_SHIFT_THRESHOLD:
                direction = "up" if yes_now > yes_then else "down"
                entry = _to_market_dict(m, yes_now)
                entry["yes_prev"] = yes_then
                entry["shift"] = shift
                entry["direction"] = direction
                alerts.append(entry)

        # Always update the snapshot so the next cycle uses today's price as baseline
        bot_state.save_polymarket_snapshot(market_id, yes_now)

    alerts.sort(key=lambda a: a["shift"], reverse=True)
    logger.info(
        "Polymarket check: %d crypto market(s) scanned, %d alert(s) found.",
        len(crypto), len(alerts),
    )
    return alerts


def format_polymarket_tweet(market: dict) -> str:
    """
    Format a clean fallback tweet for a Polymarket market.

    Works for both alert dicts (from get_polymarket_alerts) and
    top-market dicts (from get_top_markets).

    Rules: max 220 chars, no hashtags, no NFA, no ⚠️, no exclamation marks.
    """
    question = market.get("question", "Unknown market")
    yes_pct = round(market["yes_price"] * 100)

    volume = market.get("volume", 0)
    if volume >= 1_000_000:
        vol_str = f" — ${volume / 1_000_000:.1f}M vol"
    elif volume >= 1_000:
        vol_str = f" — ${volume / 1_000:.0f}K vol"
    else:
        vol_str = ""

    shift_str = ""
    if "shift" in market:
        shift_pct = round(market["shift"] * 100)
        sign = "+" if market["direction"] == "up" else "-"
        shift_str = f" ({sign}{shift_pct}pp)"

    base = f"{{q}} — YES {yes_pct}%{shift_str}{vol_str}"
    max_q = 220 - len(base.format(q="")) - 1
    if len(question) > max_q:
        question = question[:max_q].rsplit(" ", 1)[0] + "…"

    tweet = base.format(q=question)
    if len(tweet) > 220:
        tweet = tweet[:219].rsplit(" ", 1)[0] + "…"
    return tweet
