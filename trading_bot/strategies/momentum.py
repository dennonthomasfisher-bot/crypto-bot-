"""
strategies/momentum.py – Momentum / breakout signal.

A bullish breakout is detected when the latest close rises more than
`threshold` (default 1 %) above the highest close of the preceding
`period` (default 20) candles.

Signal values
─────────────
+1.0  breakout confirmed → buy signal
 0.0  no breakout (or insufficient data)
"""
from __future__ import annotations

import logging
from typing import List

logger = logging.getLogger(__name__)


def momentum_signal(
    closes: List[float],
    period: int = 20,
    threshold: float = 0.01,
) -> float:
    """
    Detect a momentum breakout above the `period`-bar high.

    The lookback window is the `period` candles immediately *before* the
    latest close so the current bar does not inflate the reference high.

    Parameters
    ----------
    closes    : list of close prices, oldest first.
    period    : number of historical bars to define the high.
    threshold : fractional excess required (0.01 = 1 %).

    Returns
    -------
    +1.0 if current close > (1 + threshold) × period_high, else 0.0.
    """
    if len(closes) < period + 1:
        logger.debug(
            "Momentum: insufficient data (%d candles, need %d)",
            len(closes), period + 1,
        )
        return 0.0

    # Exclude the current bar from the lookback window
    lookback = closes[-(period + 1):-1]
    period_high = max(lookback)
    current = closes[-1]
    breakout_level = period_high * (1.0 + threshold)

    logger.debug(
        "Momentum: close=%.6f  %d-bar high=%.6f  breakout_level=%.6f",
        current, period, period_high, breakout_level,
    )

    return 1.0 if current > breakout_level else 0.0
