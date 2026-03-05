"""
strategies/ema.py – EMA crossover signal.

Computes a fast EMA and a slow EMA over close prices.  A signal fires only
on the bar where the two lines *cross*; a bar where fast is already above
slow (but crossed earlier) returns 0.0.

Signal values
─────────────
+1.0  fast EMA crossed *above* slow EMA this bar  (bullish crossover)
-1.0  fast EMA crossed *below* slow EMA this bar  (bearish crossover)
 0.0  no crossover this bar, or insufficient data
"""
from __future__ import annotations

import logging
from typing import List

logger = logging.getLogger(__name__)


def _ema_series(prices: List[float], period: int) -> List[float]:
    """Return a full EMA series using the standard multiplier k = 2/(period+1).

    The first value is seeded with the first price (no SMA warm-up needed for
    a real-time signal; the burn-in effect is negligible after ~3×period bars).
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
    Detect a crossover between the fast and slow EMA on the most recent bar.

    Parameters
    ----------
    closes : close prices, oldest first.
    fast   : period for the fast EMA (default 8).
    slow   : period for the slow EMA (default 21).

    Returns
    -------
    +1.0  fast just crossed above slow (bullish)
    -1.0  fast just crossed below slow (bearish)
     0.0  no crossover this bar or insufficient data
    """
    if fast >= slow:
        raise ValueError(f"fast ({fast}) must be < slow ({slow})")

    # Need at least slow+1 closes so we have two consecutive EMA values to
    # compare (current and one bar back).
    if len(closes) < slow + 1:
        logger.debug(
            "EMA crossover: insufficient data (%d closes, need %d)",
            len(closes), slow + 1,
        )
        return 0.0

    fast_series = _ema_series(closes, fast)
    slow_series = _ema_series(closes, slow)

    curr_fast, prev_fast = fast_series[-1], fast_series[-2]
    curr_slow, prev_slow = slow_series[-1], slow_series[-2]

    logger.debug(
        "EMA%d=%.6f (prev %.6f)  EMA%d=%.6f (prev %.6f)",
        fast, curr_fast, prev_fast,
        slow, curr_slow, prev_slow,
    )

    # Bullish crossover: fast was at-or-below slow, now above
    if prev_fast <= prev_slow and curr_fast > curr_slow:
        logger.debug("EMA crossover: BULLISH -> +1.0")
        return 1.0

    # Bearish crossover: fast was at-or-above slow, now below
    if prev_fast >= prev_slow and curr_fast < curr_slow:
        logger.debug("EMA crossover: BEARISH -> -1.0")
        return -1.0

    return 0.0
