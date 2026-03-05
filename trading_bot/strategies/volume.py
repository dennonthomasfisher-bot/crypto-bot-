"""
strategies/volume.py – Volume surge signal with directional confirmation.

A volume surge is detected when the current bar's volume is at least
`threshold` × the mean volume of the preceding `period` bars.
Direction is confirmed by comparing the current close to the previous close:

Signal values
─────────────
+1.0  volume surge on a green candle (close > prev close) → bullish
-1.0  volume surge on a red candle  (close < prev close) → bearish
 0.0  no surge, or candle is flat, or insufficient data
"""
from __future__ import annotations

import logging
from typing import List

logger = logging.getLogger(__name__)


def volume_signal(
    volumes: List[float],
    closes: List[float],
    period: int = 20,
    threshold: float = 1.5,
) -> float:
    """
    Detect a directional volume surge.

    Parameters
    ----------
    volumes   : list of bar volumes, oldest first.
    closes    : list of close prices, oldest first (same length as volumes).
    period    : number of historical bars to compute the mean volume.
    threshold : multiple of mean required (1.5 = 50 % above average).

    Returns
    -------
    +1.0  high-volume green candle (surge + close > prev close)
    -1.0  high-volume red  candle (surge + close < prev close)
     0.0  no surge, flat candle, or insufficient data
    """
    if len(volumes) < period + 1 or len(closes) < 2:
        logger.debug(
            "Volume: insufficient data (volumes=%d, need %d; closes=%d)",
            len(volumes), period + 1, len(closes),
        )
        return 0.0

    lookback = volumes[-(period + 1):-1]
    avg = sum(lookback) / len(lookback)

    if avg <= 0.0:
        logger.debug("Volume: mean volume is zero, skipping")
        return 0.0

    current_vol = volumes[-1]
    ratio = current_vol / avg

    logger.debug(
        "Volume: current=%.4f  %d-bar avg=%.4f  ratio=%.2fx  threshold=%.1fx",
        current_vol, period, avg, ratio, threshold,
    )

    if ratio < threshold:
        return 0.0

    # Surge confirmed — determine direction from candle colour
    current_close = closes[-1]
    prev_close    = closes[-2]

    if current_close > prev_close:
        logger.debug("Volume: surge on GREEN candle → +1.0")
        return 1.0
    if current_close < prev_close:
        logger.debug("Volume: surge on RED candle → -1.0")
        return -1.0

    logger.debug("Volume: surge on DOJI candle → 0.0")
    return 0.0
