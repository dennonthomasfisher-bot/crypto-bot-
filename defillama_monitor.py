"""
DeFi Llama monitor — surfaces TVL movers and protocol narratives.

Uses the free, no-auth DeFi Llama API (https://api.llama.fi). Finds the
biggest 7-day TVL changes across the top N protocols, then returns a
structured dict the bot can turn into a post.

This unlocks content nobody else in the feed is covering with specific
numbers: 'AAVE TVL -$400M this week, largest outflow since X.'
"""
from __future__ import annotations

import logging
import time
from typing import Optional

import requests

logger = logging.getLogger(__name__)

_API_BASE = "https://api.llama.fi"
_MIN_TVL_USD = 50_000_000          # ignore sub-$50M protocols (noise)
_MIN_ABS_CHANGE_PCT = 8.0          # must move at least this in 7d to qualify
_TOP_N = 60                         # check top 60 by current TVL

# Simple process-local cache so we don't hit the API more than once per cycle.
_cache: dict = {"data": None, "ts": 0.0}
_CACHE_TTL = 3600                   # 1 hour


def _fetch_protocols() -> list[dict]:
    """Return the full protocols list (heavy: ~2 MB). Cached 1 hour."""
    now = time.time()
    if _cache["data"] is not None and (now - _cache["ts"]) < _CACHE_TTL:
        return _cache["data"]
    try:
        resp = requests.get(f"{_API_BASE}/protocols", timeout=20)
        if resp.status_code != 200:
            logger.warning("DefiLlama /protocols %d: %s", resp.status_code, resp.text[:200])
            return []
        data = resp.json()
        if not isinstance(data, list):
            return []
        _cache["data"] = data
        _cache["ts"] = now
        return data
    except Exception as exc:
        logger.warning("DefiLlama fetch failed: %s", exc)
        return []


def _clean(p: dict) -> dict | None:
    """Normalise a protocol entry. Returns None if the record is missing
    critical fields or is below the TVL floor."""
    try:
        tvl = p.get("tvl")
        if not isinstance(tvl, (int, float)) or tvl < _MIN_TVL_USD:
            return None
        name = p.get("name") or ""
        symbol = p.get("symbol") or ""
        pct_7d = p.get("change_7d")
        pct_1d = p.get("change_1d")
        if not isinstance(pct_7d, (int, float)):
            return None
        return {
            "name": name,
            "symbol": (symbol or "").upper(),
            "category": p.get("category") or "",
            "chain": p.get("chain") or "",
            "tvl": float(tvl),
            "change_1d": float(pct_1d) if isinstance(pct_1d, (int, float)) else 0.0,
            "change_7d": float(pct_7d),
            "slug": p.get("slug") or "",
        }
    except Exception:
        return None


def find_tvl_movers() -> dict:
    """Return the biggest TVL gainer and biggest loser over the 7d window,
    among top-60 protocols with TVL >= $50M.

    Returns {"gainer": {...} | None, "loser": {...} | None}.
    Either field can be None if no protocol cleared the threshold.
    """
    raw = _fetch_protocols()
    if not raw:
        return {"gainer": None, "loser": None}

    # Top N by current TVL
    raw_sorted = sorted(
        [r for r in raw if isinstance(r.get("tvl"), (int, float))],
        key=lambda r: r["tvl"],
        reverse=True,
    )[:_TOP_N]
    cleaned = [c for c in (_clean(p) for p in raw_sorted) if c is not None]
    if not cleaned:
        return {"gainer": None, "loser": None}

    qualifying = [c for c in cleaned if abs(c["change_7d"]) >= _MIN_ABS_CHANGE_PCT]
    if not qualifying:
        return {"gainer": None, "loser": None}

    gainer = max(qualifying, key=lambda c: c["change_7d"])
    loser = min(qualifying, key=lambda c: c["change_7d"])
    return {
        "gainer": gainer if gainer["change_7d"] > 0 else None,
        "loser": loser if loser["change_7d"] < 0 else None,
    }


def format_tvl_pseudo_story() -> Optional[dict]:
    """Produce a story-shaped dict the existing news pipeline can score.

    This lets DeFi Llama surface findings via the same code path as
    RSS / NewsAPI so we don't duplicate formatting logic.
    Returns None when nothing worth posting is found.
    """
    movers = find_tvl_movers()
    gainer = movers.get("gainer")
    loser = movers.get("loser")
    if not gainer and not loser:
        return None

    parts: list[str] = []
    if gainer:
        parts.append(
            f"{gainer['name']} TVL {gainer['change_7d']:+.1f}% in 7d "
            f"(${gainer['tvl']/1e9:.2f}B)"
        )
    if loser:
        parts.append(
            f"{loser['name']} TVL {loser['change_7d']:+.1f}% in 7d "
            f"(${loser['tvl']/1e9:.2f}B)"
        )
    title = "DeFi TVL movers: " + " | ".join(parts)

    return {
        "title": title,
        "url": "https://defillama.com",
        "source": "DefiLlama",
        "published_at": int(time.time()),
        "gainer": gainer,
        "loser": loser,
    }
