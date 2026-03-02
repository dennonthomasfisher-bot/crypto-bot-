"""
signal_aggregator.py – Combine the three strategy signals into a single score.

Weights
───────
  RSI strategy        0.40
  Momentum/breakout   0.35
  News sentiment      0.25
  ─────────────────────────
  Total               1.00

The weighted sum produces a score in approximately [-1.0, +1.0].
  score ≥  BUY_THRESHOLD  → "BUY"
  score ≤ SELL_THRESHOLD  → "SELL"
  otherwise               → "HOLD"
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

logger = logging.getLogger(__name__)

# Strategy weights – must sum to 1.0
WEIGHTS = {
    "rsi":       0.40,
    "momentum":  0.35,
    "sentiment": 0.25,
}


@dataclass(frozen=True)
class SignalResult:
    """Immutable snapshot of all three signals and the derived action."""

    rsi:       float
    momentum:  float
    sentiment: float
    score:     float
    action:    str   # "BUY" | "SELL" | "HOLD"

    def __str__(self) -> str:
        return (
            f"RSI={self.rsi:+.2f}  MOM={self.momentum:+.2f}  "
            f"SENT={self.sentiment:+.2f}  "
            f"score={self.score:+.3f}  → {self.action}"
        )


def aggregate(
    rsi: float,
    momentum: float,
    sentiment: float,
    buy_threshold: float = 0.30,
    sell_threshold: float = -0.30,
) -> SignalResult:
    """
    Compute a weighted signal score and determine the trading action.

    Parameters
    ----------
    rsi           : RSI signal in {-1, 0, +1}
    momentum      : Momentum/breakout signal in {0, +1}
    sentiment     : News sentiment in [-1.0, +1.0]
    buy_threshold : Minimum score to trigger a BUY (default 0.30)
    sell_threshold: Maximum score to trigger a SELL (default -0.30)

    Returns
    -------
    SignalResult with the individual signals, composite score, and action.
    """
    score = (
        WEIGHTS["rsi"]       * rsi
        + WEIGHTS["momentum"]  * momentum
        + WEIGHTS["sentiment"] * sentiment
    )

    if score >= buy_threshold:
        action = "BUY"
    elif score <= sell_threshold:
        action = "SELL"
    else:
        action = "HOLD"

    result = SignalResult(
        rsi=rsi, momentum=momentum,
        sentiment=sentiment, score=score, action=action,
    )
    logger.debug("Signal aggregate: %s", result)
    return result
