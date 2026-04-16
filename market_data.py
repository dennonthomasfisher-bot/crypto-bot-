"""
Market data enrichment — fetches on-chain metrics and technical indicators
for richer, more professional tweet generation.

Free APIs used:
- Binance: klines for RSI/MA calculation
- CoinGlass: open interest, funding rates (free tier)
- Alternative.me: Fear & Greed index
"""
from __future__ import annotations

import logging
import requests
import json

logger = logging.getLogger(__name__)

_BINANCE_KLINES = "https://api.binance.com/api/v3/klines"
_BINANCE_TICKER = "https://api.binance.com/api/v3/ticker/24hr"


# ── Technical indicators from Binance klines ─────────────────────────────────

def _fetch_closes(symbol: str = "BTCUSDT", interval: str = "1h", limit: int = 50) -> list[float]:
    """Fetch closing prices from Binance."""
    try:
        resp = requests.get(_BINANCE_KLINES, params={
            "symbol": symbol, "interval": interval, "limit": limit
        }, timeout=10)
        resp.raise_for_status()
        return [float(k[4]) for k in resp.json()]
    except Exception as exc:
        logger.warning("Failed to fetch klines for %s: %s", symbol, exc)
        return []


def _calc_rsi(closes: list[float], period: int = 14) -> float | None:
    """Calculate RSI from closing prices."""
    if len(closes) < period + 1:
        return None
    gains, losses = [], []
    for i in range(1, len(closes)):
        diff = closes[i] - closes[i - 1]
        gains.append(max(diff, 0))
        losses.append(max(-diff, 0))
    avg_gain = sum(gains[-period:]) / period
    avg_loss = sum(losses[-period:]) / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))


def _calc_sma(closes: list[float], period: int) -> float | None:
    """Calculate simple moving average."""
    if len(closes) < period:
        return None
    return sum(closes[-period:]) / period


def get_technical_context(symbol: str = "BTCUSDT") -> str:
    """Get RSI, moving averages, and price position for a coin.

    Returns a human-readable context string for prompts.
    """
    closes = _fetch_closes(symbol, "1h", 50)
    if not closes:
        return ""

    price = closes[-1]
    rsi = _calc_rsi(closes)
    sma_20 = _calc_sma(closes, 20)
    sma_50 = _calc_sma(closes, 50) if len(closes) >= 50 else None

    parts = []
    if rsi is not None:
        if rsi > 70:
            parts.append(f"RSI: {rsi:.0f} (overbought)")
        elif rsi < 30:
            parts.append(f"RSI: {rsi:.0f} (oversold)")
        else:
            parts.append(f"RSI: {rsi:.0f}")

    if sma_20:
        above_below = "above" if price > sma_20 else "below"
        parts.append(f"Price {above_below} 20-hour MA (${sma_20:,.0f})")

    if sma_50:
        above_below = "above" if price > sma_50 else "below"
        parts.append(f"Price {above_below} 50-hour MA (${sma_50:,.0f})")

    return " | ".join(parts) if parts else ""


# ── On-chain / derivatives data ──────────────────────────────────────────────

def get_funding_rate() -> str:
    """Get BTC perpetual funding rate from Binance."""
    try:
        resp = requests.get("https://fapi.binance.com/fapi/v1/fundingRate",
                           params={"symbol": "BTCUSDT", "limit": 1}, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        if data:
            rate = float(data[0]["fundingRate"]) * 100
            if rate > 0.03:
                return f"Funding: {rate:.4f}% (longs paying — crowded long)"
            elif rate < -0.03:
                return f"Funding: {rate:.4f}% (shorts paying — crowded short)"
            else:
                return f"Funding: {rate:.4f}% (neutral)"
    except Exception as exc:
        logger.debug("Funding rate fetch failed: %s", exc)
    return ""


def get_open_interest() -> str:
    """Get BTC futures open interest from Binance."""
    try:
        resp = requests.get("https://fapi.binance.com/fapi/v1/openInterest",
                           params={"symbol": "BTCUSDT"}, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        oi = float(data.get("openInterest", 0))
        if oi > 0:
            return f"Open interest: {oi:,.0f} BTC"
    except Exception as exc:
        logger.debug("Open interest fetch failed: %s", exc)
    return ""


def get_exchange_volume_context() -> str:
    """Get top exchange volumes for market context."""
    try:
        pairs = ["BTCUSDT", "ETHUSDT", "SOLUSDT"]
        resp = requests.get(_BINANCE_TICKER,
                           params={"symbols": json.dumps(pairs)}, timeout=10)
        resp.raise_for_status()
        parts = []
        for t in resp.json():
            sym = t["symbol"].replace("USDT", "")
            vol = float(t["quoteVolume"]) / 1e9
            parts.append(f"{sym}: ${vol:.1f}B vol")
        return " | ".join(parts)
    except Exception as exc:
        logger.debug("Volume context fetch failed: %s", exc)
    return ""


# ── Combined enriched context ────────────────────────────────────────────────

def get_enriched_context(symbol: str = "BTCUSDT") -> str:
    """Get full enriched market context for premium tweet generation.

    Combines technical indicators, funding rates, open interest, and volume.
    Returns a multi-line string suitable for Claude prompts.
    """
    parts = []

    tech = get_technical_context(symbol)
    if tech:
        parts.append(f"Technical: {tech}")

    funding = get_funding_rate()
    if funding:
        parts.append(funding)

    oi = get_open_interest()
    if oi:
        parts.append(oi)

    vol = get_exchange_volume_context()
    if vol:
        parts.append(f"24h volume: {vol}")

    return "\n".join(parts) if parts else ""
