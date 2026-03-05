"""
strategies/volume.py – Volume surge signal.

A volume surge is detected when the current bar's volume is at least
`threshold` × the mean volume of the preceding `period` bars.

Signal values
─────────────
+1.0  volume surge confirmed → buy signal (heightened activity)
 0.0  no surge (or insufficient data)

Note: volume alone is direction-neutral; this strategy fires on any
spike, so it is combined with directional signals (RSI, momentum) in
the aggregator. Its weight is intentionally modest (0.15).
"""
from __future__ import annotations

import logging
from typing import List

logger = logging.getLogger(__name__)


def volume_signal(
    volumes: List[float],
    period: int = 20,
    threshold: float = 1.5,
) -> float:
    """
    Detect a volume surge above the `period`-bar rolling mean.

    The lookback window is the `period` bars immediately *before* the
    current bar so the current bar does not inflate the reference average.

    Parameters
    ----------
    volumes   : list of bar volumes, oldest first.
    period    : number of historical bars to compute the mean.
    threshold : multiple of mean required (1.5 = 50% above average).

    Returns
    -------
    +1.0 if current volume >= threshold × mean(period bars), else 0.0.
    """
    if len(volumes) < period + 1:
        logger.debug(
            "Volume: insufficient data (%d bars, need %d)",
            len(volumes), period + 1,
        )
        return 0.0

    lookback = volumes[-(period + 1):-1]
    avg = sum(lookback) / len(lookback)

    if avg <= 0.0:
        logger.debug("Volume: mean volume is zero, skipping")
        return 0.0

    current = volumes[-1]
    ratio = current / avg

    logger.debug(
        "Volume: current=%.4f  %d-bar avg=%.4f  ratio=%.2fx  threshold=%.1fx",
        current, period, avg, ratio, threshold,
    )

    return 1.0 if ratio >= threshold else 0.0
