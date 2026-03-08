"""
DeFi monitor – tracks TVL data from DefiLlama (free, no API key).

Provides:
  - Total DeFi TVL and daily change
  - Chain TVL rankings (Ethereum, Solana, BSC, etc.)
  - Protocol TVL movers (big gains/losses)
  - Data for DeFi-focused tweets
"""
from __future__ import annotations

import logging
import time

import requests

import ai_writer

logger = logging.getLogger(__name__)

_DEFILLAMA_BASE = "https://api.llama.fi"

# Cache to avoid hammering the API
_cache: dict = {}
_CACHE_TTL = 1800  # 30 minutes


def _cached_get(url: str, cache_key: str) -> dict | list | None:
    """Fetch with simple cache."""
    now = time.time()
    if cache_key in _cache and (now - _cache[cache_key]["time"]) < _CACHE_TTL:
        return _cache[cache_key]["data"]
    try:
        resp = requests.get(url, timeout=15)
        resp.raise_for_status()
        data = resp.json()
        _cache[cache_key] = {"data": data, "time": now}
        return data
    except requests.RequestException as exc:
        logger.warning("DefiLlama fetch failed (%s): %s", cache_key, exc)
        return None


def fetch_total_tvl() -> dict | None:
    """Fetch current total DeFi TVL across all chains."""
    data = _cached_get(f"{_DEFILLAMA_BASE}/v2/historicalChainTvl", "total_tvl")
    if not data or not isinstance(data, list) or len(data) < 2:
        return None
    current = data[-1]
    previous = data[-2]
    tvl = current.get("tvl", 0)
    prev_tvl = previous.get("tvl", 0)
    change = tvl - prev_tvl
    pct = (change / prev_tvl * 100) if prev_tvl > 0 else 0
    return {
        "tvl": tvl,
        "change_24h": change,
        "pct_24h": pct,
    }


def fetch_chain_tvls() -> list[dict]:
    """Fetch TVL by chain, sorted by TVL descending."""
    data = _cached_get(f"{_DEFILLAMA_BASE}/v2/chains", "chain_tvls")
    if not data or not isinstance(data, list):
        return []
    chains = []
    for chain in data[:15]:
        chains.append({
            "name": chain.get("name", "?"),
            "tvl": chain.get("tvl", 0),
            "change_1d": chain.get("change_1d", 0),
            "change_7d": chain.get("change_7d", 0),
        })
    chains.sort(key=lambda c: c["tvl"], reverse=True)
    return chains


def fetch_protocol_movers() -> list[dict]:
    """Find protocols with the biggest TVL changes (up or down)."""
    data = _cached_get(f"{_DEFILLAMA_BASE}/protocols", "protocols")
    if not data or not isinstance(data, list):
        return []

    protocols = []
    for p in data[:200]:
        tvl = p.get("tvl", 0)
        change = p.get("change_1d", 0)
        if tvl >= 50_000_000 and abs(change or 0) >= 5.0:  # $50M+ TVL, 5%+ move
            protocols.append({
                "name": p.get("name", "?"),
                "symbol": p.get("symbol", "?").upper() if p.get("symbol") else "?",
                "tvl": tvl,
                "change_1d": change or 0,
                "chain": p.get("chain", "Multi"),
                "category": p.get("category", ""),
            })

    protocols.sort(key=lambda p: abs(p["change_1d"]), reverse=True)
    return protocols[:5]


def fetch_dex_volumes() -> dict | None:
    """Fetch 24h DEX volume data."""
    data = _cached_get(f"{_DEFILLAMA_BASE}/overview/dexs?excludeTotalDataChart=true&excludeTotalDataChartBreakdown=true&dataType=dailyVolume", "dex_volumes")
    if not data:
        return None
    total = data.get("totalDataChart", [])
    total_24h = data.get("total24h", 0)
    total_change = data.get("change_1d", 0)

    # Top DEXs
    protocols = data.get("protocols", [])
    top_dexes = []
    for p in sorted(protocols, key=lambda x: x.get("total24h", 0) or 0, reverse=True)[:5]:
        top_dexes.append({
            "name": p.get("name", "?"),
            "volume_24h": p.get("total24h", 0) or 0,
            "change_1d": p.get("change_1d", 0) or 0,
        })

    return {
        "total_24h": total_24h,
        "change_1d": total_change,
        "top_dexes": top_dexes,
    }


# ── Tweet formatting ─────────────────────────────────────────────────────────

def generate_defi_tweet() -> str | None:
    """
    Generate a DeFi-focused tweet using TVL data.
    Picks the most interesting angle from available data.
    """
    import random

    # Gather data
    total = fetch_total_tvl()
    chains = fetch_chain_tvls()
    movers = fetch_protocol_movers()

    if not total and not chains:
        return None

    # Pick an angle
    angles = []
    if total:
        angles.append("tvl_overview")
    if chains and len(chains) >= 3:
        angles.append("chain_comparison")
    if movers:
        angles.append("protocol_mover")

    if not angles:
        return None

    angle = random.choice(angles)

    if angle == "tvl_overview" and total:
        tvl_b = total["tvl"] / 1e9
        pct = total["pct_24h"]

        if ai_writer.is_available():
            prompt = f"""Write a tweet about DeFi TVL.

Total DeFi TVL: ${tvl_b:.1f}B ({pct:+.1f}% 24h)

DO NOT mention Bitcoin. This is about DeFi.
Comment on what TVL levels signal — is money flowing in or out?
Keep it under 275 chars. NO hashtags.

Write the tweet now. Nothing else."""
            system = "You are @CoinWatchAlert. You track DeFi data closely. No hashtags."
            ai_tweet = ai_writer._call_claude(system, prompt)
            if ai_tweet and len(ai_tweet) <= 280:
                return ai_tweet

        emoji = "🟢" if pct > 0 else "🔴"
        return (
            f"{emoji} DeFi TVL: ${tvl_b:.1f}B ({pct:+.1f}% 24h)\n\n"
            f"{'Capital flowing in — bullish signal for DeFi.' if pct > 0 else 'Capital leaving — risk-off mode in DeFi.'}"
        )

    elif angle == "chain_comparison" and chains:
        top3 = chains[:3]
        chain_lines = ""
        for c in top3:
            tvl_b = c["tvl"] / 1e9
            change = c.get("change_1d", 0)
            chain_lines += f"  {c['name']}: ${tvl_b:.1f}B ({change:+.1f}%)\n"

        if ai_writer.is_available():
            prompt = f"""Write a tweet comparing chain TVLs.

Top chains by TVL:
{chain_lines}

Compare the chains — who's gaining, who's losing. Make it interesting.
DO NOT mention Bitcoin price. This is about DeFi/L1 competition.
Keep it under 275 chars. NO hashtags.

Write the tweet now. Nothing else."""
            system = "You are @CoinWatchAlert. You track the L1 war through TVL data."
            ai_tweet = ai_writer._call_claude(system, prompt)
            if ai_tweet and len(ai_tweet) <= 280:
                return ai_tweet

        lines = ["Chain TVL check:"]
        for c in top3:
            tvl_b = c["tvl"] / 1e9
            lines.append(f"  {c['name']}: ${tvl_b:.1f}B")
        return "\n".join(lines)

    elif angle == "protocol_mover" and movers:
        mover = movers[0]
        tvl_m = mover["tvl"] / 1e6
        change = mover["change_1d"]
        emoji = "🟢" if change > 0 else "🔴"

        if ai_writer.is_available():
            prompt = f"""Write a tweet about a DeFi protocol making a big move.

Protocol: {mover['name']} ({mover['symbol']})
TVL: ${tvl_m:.0f}M ({change:+.1f}% 24h)
Chain: {mover['chain']}
Category: {mover['category']}

Why might TVL be surging or dropping? Speculation is fine.
Keep it under 275 chars. NO hashtags.

Write the tweet now. Nothing else."""
            system = "You are @CoinWatchAlert. You spot DeFi protocol moves before CT catches on."
            ai_tweet = ai_writer._call_claude(system, prompt)
            if ai_tweet and len(ai_tweet) <= 280:
                return ai_tweet

        return (
            f"{emoji} {mover['name']} ({mover['symbol']}) TVL {change:+.1f}% today\n\n"
            f"${tvl_m:.0f}M locked on {mover['chain']}\n\n"
            f"{'Big inflows — someone knows something.' if change > 0 else 'Capital exiting — watch for more.'}"
        )

    return None
