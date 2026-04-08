"""
Volume anomaly scanner — detects unusual volume without matching price moves.

Uses Binance 1d klines to find coins where current volume is 2x+ the 7-day
average but price has barely moved (< 3%). This signals accumulation,
distribution, or early positioning before a larger move.
"""
from __future__ import annotations

import logging
import requests

logger = logging.getLogger(__name__)

_BINANCE_KLINES_URL = "https://api.binance.com/api/v3/klines"

# Coins to monitor — Binance USDT pairs
_TRACKED_COINS = [
    ("BTC", "BTCUSDT"),
    ("ETH", "ETHUSDT"),
    ("SOL", "SOLUSDT"),
    ("XRP", "XRPUSDT"),
    ("BNB", "BNBUSDT"),
    ("ADA", "ADAUSDT"),
    ("AVAX", "AVAXUSDT"),
    ("DOGE", "DOGEUSDT"),
    ("LINK", "LINKUSDT"),
    ("DOT", "DOTUSDT"),
]


def scan() -> dict | None:
    """Scan tracked coins for volume anomalies.

    Returns the top anomaly as a dict with keys:
        symbol, price, vol_ratio, price_change
    or None if no coins meet the threshold.
    """
    anomalies: list[dict] = []

    for symbol, pair in _TRACKED_COINS:
        try:
            resp = requests.get(
                _BINANCE_KLINES_URL,
                params={"symbol": pair, "interval": "1d", "limit": 8},
                timeout=15,
            )
            resp.raise_for_status()
            klines = resp.json()

            if not isinstance(klines, list) or len(klines) < 8:
                logger.debug("[ANOMALY] %s: insufficient klines (%d)",
                             symbol, len(klines) if isinstance(klines, list) else 0)
                continue

            # kline format: [open_time, open, high, low, close, volume, ...]
            current = klines[-1]
            previous_7 = klines[:-1]  # 7 prior candles

            current_volume = float(current[5])
            current_close = float(current[4])
            previous_close = float(klines[-2][4])

            avg_volume = sum(float(k[5]) for k in previous_7) / len(previous_7)

            if avg_volume == 0:
                logger.debug("[ANOMALY] %s: avg_volume is 0 — skipping", symbol)
                continue

            vol_ratio = current_volume / avg_volume
            price_change = ((current_close - previous_close) / previous_close) * 100

            logger.debug("[ANOMALY] %s: vol_ratio=%.2f, price_change=%.2f%%",
                         symbol, vol_ratio, price_change)

            # Detection: 2x+ volume with < 3% price move
            if vol_ratio >= 2.0 and abs(price_change) <= 3.0:
                anomalies.append({
                    "symbol": symbol,
                    "price": current_close,
                    "vol_ratio": round(vol_ratio, 2),
                    "price_change": round(price_change, 2),
                })
                logger.info("[ANOMALY] %s: %.2fx volume, %.2f%% move — DETECTED",
                            symbol, vol_ratio, price_change)

        except requests.RequestException as exc:
            logger.warning("[ANOMALY] Failed to fetch klines for %s: %s", symbol, exc)
        except (ValueError, IndexError, KeyError) as exc:
            logger.warning("[ANOMALY] Data parsing error for %s: %s", symbol, exc)

    if not anomalies:
        logger.debug("[ANOMALY] No anomalies detected this cycle")
        return None

    # Sort by vol_ratio descending, return top 1
    anomalies.sort(key=lambda a: a["vol_ratio"], reverse=True)
    top = anomalies[0]
    logger.info("[ANOMALY] Top anomaly: %s — %.2fx volume, %.2f%% move, price $%.4f",
                top["symbol"], top["vol_ratio"], top["price_change"], top["price"])
    return top
