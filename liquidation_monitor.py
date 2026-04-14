"""
Liquidation data monitor – tracks crypto liquidation events.

Uses the CoinGlass public API for aggregated liquidation data.
Falls back to Coingecko derivatives data if CoinGlass is unavailable.
"""
from __future__ import annotations

import logging
import time

import requests

import ai_writer

logger = logging.getLogger(__name__)

# CoinGlass public endpoints (no API key required for basic data)
_COINGLASS_LIQMAP_URL = "https://open-api.coinglass.com/public/v2/liquidation_history"
_COINGLASS_LIQINFO_URL = "https://open-api.coinglass.com/public/v2/liquidation_info"

# Fallback: aggregate from exchange APIs
_CACHE: dict = {}
_CACHE_TTL = 300  # 5 min cache


def _fetch_coinglass_liquidations() -> dict | None:
    """
    Fetch aggregated liquidation data from CoinGlass public API.
    Returns dict with total_liq_24h, long_liq_24h, short_liq_24h, etc.
    """
    # Try the public liquidation endpoint
    for attempt in range(3):
        try:
            resp = requests.get(
                "https://open-api.coinglass.com/public/v2/liquidation_history",
                params={"time_type": 2, "symbol": "all"},
                timeout=15,
            )
            if resp.status_code == 429:
                time.sleep(2 ** (attempt + 1))
                continue
            if resp.status_code != 200:
                break
            data = resp.json()
            if data.get("code") == "0" and data.get("data"):
                return data["data"]
        except requests.RequestException as exc:
            logger.warning("CoinGlass fetch failed (attempt %d/3): %s", attempt + 1, exc)
            if attempt < 2:
                time.sleep(2 ** (attempt + 1))
    return None


def _fetch_derivative_data() -> dict | None:
    """
    Fallback: fetch derivatives/open interest data from CoinGecko.
    """
    try:
        resp = requests.get(
            "https://api.coingecko.com/api/v3/derivatives",
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()
        if not data:
            return None

        # Aggregate some useful stats
        total_oi = 0
        total_volume = 0
        funding_rates = []

        for d in data[:20]:
            oi = d.get("open_interest") or 0
            vol = d.get("h24_volume") or 0
            total_oi += oi
            total_volume += vol
            fr = d.get("funding_rate")
            if fr is not None:
                try:
                    funding_rates.append(float(fr))
                except (ValueError, TypeError):
                    pass

        avg_funding = sum(funding_rates) / len(funding_rates) if funding_rates else 0

        return {
            "total_open_interest": total_oi,
            "total_volume_24h": total_volume,
            "avg_funding_rate": avg_funding,
            "exchanges_count": len(data),
        }
    except requests.RequestException as exc:
        logger.warning("CoinGecko derivatives fetch failed: %s", exc)
        return None


def fetch_liquidation_data() -> dict | None:
    """
    Fetch liquidation/derivatives data from available sources.
    Returns normalized dict with whatever data we could gather.
    """
    now = time.time()

    # Check cache
    if _CACHE and (now - _CACHE.get("_ts", 0)) < _CACHE_TTL:
        return _CACHE

    # Try CoinGlass first
    cg_data = _fetch_coinglass_liquidations()
    if cg_data:
        result = {"source": "coinglass", "raw": cg_data, "_ts": now}
        _CACHE.update(result)
        return result

    # Fallback to CoinGecko derivatives
    deriv = _fetch_derivative_data()
    if deriv:
        result = {"source": "derivatives", **deriv, "_ts": now}
        _CACHE.update(result)
        return result

    return None


def _fmt_usd(val: float) -> str:
    """Format large USD values."""
    if val >= 1e9:
        return f"${val / 1e9:.1f}B"
    if val >= 1e6:
        return f"${val / 1e6:.0f}M"
    if val >= 1e3:
        return f"${val / 1e3:.0f}K"
    return f"${val:.0f}"


def format_liquidation_tweet(data: dict) -> str | None:
    """Generate a liquidation/derivatives tweet from available data."""
    source = data.get("source", "")

    if source == "coinglass" and data.get("raw"):
        raw = data["raw"]
        # CoinGlass data format varies — handle list or dict
        if isinstance(raw, list) and raw:
            entry = raw[0] if isinstance(raw[0], dict) else {}
            long_liq = entry.get("longLiquidationUsd", 0) or 0
            short_liq = entry.get("shortLiquidationUsd", 0) or 0
            total = long_liq + short_liq

            if total < 1_000_000:
                return None  # Not interesting enough

            if ai_writer.is_available():
                prompt = f"""Write a tweet about crypto liquidations in the last 24 hours.

Liquidation data:
- Total liquidated: {_fmt_usd(total)}
- Longs liquidated: {_fmt_usd(long_liq)}
- Shorts liquidated: {_fmt_usd(short_liq)}
- {'Longs got wrecked harder' if long_liq > short_liq else 'Shorts got squeezed harder'}

The tweet should:
- Lead with the total liquidation number — it's dramatic
- Note which side (longs or shorts) got hit harder
- Explain what this means for the market structure
- Sound like a trader watching the carnage, not a news report
- Under 270 characters
- NO hashtags
{ai_writer._get_recent_context()}
Write the tweet now. Nothing else."""

                system = """You are @CryptoVault88. Liquidation events are your bread and butter — you call them out in real-time with sharp commentary. No hashtags, no filler."""
                tweet = ai_writer._call_claude(system, prompt)
                if tweet and len(tweet) <= 275:
                    return tweet

            # Template fallback
            dominant = "longs" if long_liq > short_liq else "shorts"
            emoji = "🔴" if long_liq > short_liq else "🟢"
            return (
                f"💥 {_fmt_usd(total)} liquidated in 24h\n\n"
                f"→ Longs: {_fmt_usd(long_liq)}\n"
                f"→ Shorts: {_fmt_usd(short_liq)}\n\n"
                f"{emoji} {'Bears winning' if long_liq > short_liq else 'Bulls winning'} — "
                f"{dominant} getting wiped out."
            )

    if source == "derivatives":
        oi = data.get("total_open_interest", 0)
        vol = data.get("total_volume_24h", 0)
        funding = data.get("avg_funding_rate", 0)

        if oi <= 0 and vol <= 0:
            return None

        if ai_writer.is_available():
            funding_str = f"{funding:.4f}%" if funding else "neutral"
            prompt = f"""Write a tweet about crypto derivatives/leverage data.

Derivatives data:
- Total open interest: {_fmt_usd(oi)}
- 24h derivatives volume: {_fmt_usd(vol)}
- Average funding rate: {funding_str}
- {'Positive funding = longs paying shorts (market leaning bullish)' if funding > 0 else 'Negative funding = shorts paying longs (market leaning bearish)' if funding < 0 else 'Neutral funding = market balanced'}

The tweet should:
- Focus on what the leverage data tells us about positioning
- If funding is extreme (>0.03% or <-0.03%), flag it as a potential reversal signal
- Sound like a trader who watches the derivatives market closely
- Under 270 characters
- NO hashtags
{ai_writer._get_recent_context()}
Write the tweet now. Nothing else."""

            system = """You are @CryptoVault88. You track derivatives data obsessively — open interest, funding rates, and leverage tell you where the crowd is positioned. No hashtags, no filler."""
            tweet = ai_writer._call_claude(system, prompt)
            if tweet and len(tweet) <= 275:
                return tweet

        # Template fallback
        lines = ["📊 Derivatives snapshot:"]
        lines.append("")
        if oi > 0:
            lines.append(f"→ Open interest: {_fmt_usd(oi)}")
        if vol > 0:
            lines.append(f"→ 24h volume: {_fmt_usd(vol)}")
        if funding:
            direction = "longs pay" if funding > 0 else "shorts pay"
            lines.append(f"→ Avg funding: {funding:.4f}% ({direction})")
            if abs(funding) > 0.03:
                lines.extend(["", "⚡ Extreme funding — watch for a squeeze."])

        tweet = "\n".join(lines)
        if len(tweet) > 275:
            tweet = tweet[:272].rsplit("\n", 1)[0] + "…"
        return tweet

    return None
