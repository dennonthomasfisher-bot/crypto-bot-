"""
strategies/ema.py – EMA trend-filter signal.

Computes a fast EMA and a slow EMA over close prices and returns the
*current* relationship between them — not just the crossover bar.
This makes EMA a persistent trend filter: it stays +1 throughout a
bullish trend and -1 throughout a bearish trend, giving the aggregator
a meaningful non-zero vote every bar.

Signal values
─────────────
+1.0  EMA(fast) > EMA(slow)  — uptrend in effect
-1.0  EMA(fast) < EMA(slow)  — downtrend in effect
 0.0  EMAs are exactly equal, or insufficient data
"""
from __future__ import annotations

import logging
from typing import List

logger = logging.getLogger(__name__)


def _ema_series(prices: List[float], period: int) -> List[float]:
    """Return a full EMA series using the standard multiplier k = 2/(period+1).

    The first value is seeded with the first price; the burn-in effect is
    negligible after ~3x period bars.
    """
    k = 2.0 / (period + 1)
    series = [prices[0]]
    for price in prices[1:]:
        series.append(price * k + series[-1] * (1.0 - k))
    return series


def ema_crossover_signal(
    closes: List[float],
    fast: int = 8,
    slow: int = 21,
) -> float:
    """
    Return the current EMA trend direction (trend filter, not crossover-only).

    Returns +1.0 whenever EMA(fast) is above EMA(slow) — the signal stays
    positive throughout an uptrend, not just on the crossover bar.
    Likewise returns -1.0 throughout any downtrend.

    Parameters
    ----------
    closes : close prices, oldest first.
    fast   : period for the fast EMA (default 8).
    slow   : period for the slow EMA (default 21).

    Returns
    -------
    +1.0  EMA(fast) > EMA(slow)  (bullish trend)
    -1.0  EMA(fast) < EMA(slow)  (bearish trend)
     0.0  EMAs equal or insufficient data
    """
    if fast >= slow:
        raise ValueError(f"fast ({fast}) must be < slow ({slow})")

    # Need at least `slow` closes to compute a meaningful slow EMA.
    if len(closes) < slow:
        logger.debug(
            "EMA: insufficient data (%d closes, need %d)",
            len(closes), slow,
        )
        return 0.0

    fast_series = _ema_series(closes, fast)
    slow_series = _ema_series(closes, slow)

    curr_fast = fast_series[-1]
    curr_slow = slow_series[-1]

    logger.debug(
        "EMA%d=%.6f  EMA%d=%.6f  diff=%+.6f",
        fast, curr_fast, slow, curr_slow, curr_fast - curr_slow,
    )

    if curr_fast > curr_slow:
        return 1.0
    if curr_fast < curr_slow:
        return -1.0
    return 0.0
