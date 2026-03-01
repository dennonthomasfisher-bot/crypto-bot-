"""
strategies/rsi.py – Relative Strength Index (RSI) signal.

Buy signal  : RSI ≤ oversold threshold  (default 30)  → +1.0
Sell signal : RSI ≥ overbought threshold (default 70) → -1.0
Neutral     : neither condition met                   →  0.0
"""
from __future__ import annotations

import logging
from typing import List, Optional

import numpy as np

logger = logging.getLogger(__name__)


def calculate_rsi(closes: List[float], period: int = 14) -> Optional[float]:
    """
    Compute RSI using Wilder's smoothed moving average.

    Returns the most recent RSI value (0–100), or None if there are fewer
    than `period + 1` data points.
    """
    if len(closes) < period + 1:
        return None

    arr = np.array(closes, dtype=float)
    deltas = np.diff(arr)

    gains = np.where(deltas > 0, deltas, 0.0)
    losses = np.where(deltas < 0, -deltas, 0.0)

    # Seed with simple average over the first `period` bars
    avg_gain = float(np.mean(gains[:period]))
    avg_loss = float(np.mean(losses[:period]))

    # Apply Wilder's smoothing for the remaining bars
    for i in range(period, len(deltas)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period

    if avg_loss == 0.0:
        return 100.0

    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1.0 + rs))


def rsi_signal(
    closes: List[float],
    period: int = 14,
    oversold: float = 30.0,
    overbought: float = 70.0,
) -> float:
    """
    Return the directional RSI signal for the latest close price.

    Returns
    -------
    +1.0  RSI is oversold  → buy signal
    -1.0  RSI is overbought → sell signal
     0.0  neutral (or insufficient data)
    """
    rsi = calculate_rsi(closes, period)
    if rsi is None:
        logger.debug("RSI: insufficient data (%d candles, need %d)", len(closes), period + 1)
        return 0.0

    logger.debug("RSI=%.2f  (oversold=%.0f  overbought=%.0f)", rsi, oversold, overbought)

    if rsi <= oversold:
        return 1.0
    if rsi >= overbought:
        return -1.0
    return 0.0
