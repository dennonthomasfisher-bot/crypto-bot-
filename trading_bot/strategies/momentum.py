"""
strategies/momentum.py – Momentum / breakout signal.

Bullish breakout : latest close rises more than `threshold` above the
                   highest close of the preceding `period` candles.
Bearish breakdown: latest close falls more than `threshold` below the
                   lowest close of the preceding `period` candles.

Signal values
─────────────
+1.0  bullish breakout confirmed  → buy signal
-1.0  bearish breakdown confirmed → sell signal
 0.0  neither (or insufficient data)
"""
from __future__ import annotations

import logging
from typing import List

logger = logging.getLogger(__name__)


def momentum_signal(
    closes: List[float],
    period: int = 20,
    threshold: float = 0.002,
) -> float:
    """
    Detect a momentum breakout above the `period`-bar high, or a breakdown
    below the `period`-bar low.

    The lookback window is the `period` candles immediately *before* the
    latest close so the current bar does not inflate the reference level.

    Parameters
    ----------
    closes    : list of close prices, oldest first.
    period    : number of historical bars to define the high/low range.
    threshold : fractional excess required (0.002 = 0.2 %).

    Returns
    -------
    +1.0  close > (1 + threshold) × period_high  (bullish breakout)
    -1.0  close < (1 - threshold) × period_low   (bearish breakdown)
     0.0  neither condition met, or insufficient data
    """
    if len(closes) < period + 1:
        logger.debug(
            "Momentum: insufficient data (%d candles, need %d)",
            len(closes), period + 1,
        )
        return 0.0

    lookback = closes[-(period + 1):-1]
    period_high = max(lookback)
    period_low  = min(lookback)
    current     = closes[-1]

    breakout_level  = period_high * (1.0 + threshold)
    breakdown_level = period_low  * (1.0 - threshold)

    logger.debug(
        "Momentum: close=%.6f  %d-bar high=%.6f  low=%.6f  "
        "breakout=%.6f  breakdown=%.6f",
        current, period, period_high, period_low,
        breakout_level, breakdown_level,
    )

    if current > breakout_level:
        return 1.0
    if current < breakdown_level:
        return -1.0
    return 0.0
