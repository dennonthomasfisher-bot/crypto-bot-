"""
strategies/bollinger.py – Bollinger Band signal.

Bollinger Bands place an upper and lower band at `num_std` standard
deviations around a `period`-bar simple moving average.  Price touching
or piercing the lower band is a classic mean-reversion buy signal;
touching the upper band is a sell signal.

Signal values
─────────────
+1.0  price ≤ lower band → buy signal  (oversold / mean-reversion)
-1.0  price ≥ upper band → sell signal (overbought / mean-reversion)
 0.0  price is between the bands (or insufficient data)
"""
from __future__ import annotations

import logging
import math
from typing import List

logger = logging.getLogger(__name__)


def bollinger_signal(
    closes: List[float],
    period: int = 20,
    num_std: float = 2.0,
) -> float:
    """
    Compute Bollinger Bands over the last `period` closes and return a
    directional signal based on where the latest close sits.

    Parameters
    ----------
    closes  : list of close prices, oldest first.
    period  : SMA lookback window (default 20).
    num_std : band width in standard deviations (default 2.0).

    Returns
    -------
    +1.0  close ≤ lower band
    -1.0  close ≥ upper band
     0.0  close is within the bands (or insufficient data)
    """
    if len(closes) < period:
        logger.debug(
            "Bollinger: insufficient data (%d closes, need %d)",
            len(closes), period,
        )
        return 0.0

    window = closes[-period:]
    mean = sum(window) / period
    variance = sum((x - mean) ** 2 for x in window) / period
    std = math.sqrt(variance)

    if std == 0.0:
        logger.debug("Bollinger: std=0, price locked – skipping")
        return 0.0

    upper = mean + num_std * std
    lower = mean - num_std * std
    current = closes[-1]

    logger.debug(
        "Bollinger: price=%.6f  lower=%.6f  mid=%.6f  upper=%.6f  std=%.6f",
        current, lower, mean, upper, std,
    )

    if current <= lower:
        return 1.0
    if current >= upper:
        return -1.0
    return 0.0
