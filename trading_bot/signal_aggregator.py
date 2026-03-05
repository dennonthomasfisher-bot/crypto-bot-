"""
signal_aggregator.py – Combine five strategy signals into a single score.

Weights
───────
  RSI strategy        0.30
  Momentum/breakout   0.25
  Bollinger Bands     0.20
  Volume surge        0.15
  News sentiment      0.10
  ─────────────────────────
  Total               1.00

A BUY is only triggered when BOTH conditions hold:
  1. Weighted score ≥ buy_threshold
  2. Number of bullish signals (value > 0) ≥ min_buy_signals

A SELL is triggered when score ≤ sell_threshold.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

logger = logging.getLogger(__name__)

# Strategy weights – must sum to 1.0
WEIGHTS = {
    "rsi":       0.30,
    "momentum":  0.25,
    "bollinger": 0.20,
    "volume":    0.15,
    "sentiment": 0.10,
}


@dataclass(frozen=True)
class SignalResult:
    """Immutable snapshot of all five signals and the derived action."""

    rsi:       float
    momentum:  float
    bollinger: float
    volume:    float
    sentiment: float
    score:     float
    signals_fired: int   # number of bullish signals that are > 0
    action:    str       # "BUY" | "SELL" | "HOLD"

    def __str__(self) -> str:
        return (
            f"RSI={self.rsi:+.2f}  MOM={self.momentum:+.2f}  "
            f"BB={self.bollinger:+.2f}  VOL={self.volume:+.2f}  "
            f"SENT={self.sentiment:+.2f}  "
            f"score={self.score:+.3f}  signals={self.signals_fired}/5  → {self.action}"
        )


def aggregate(
    rsi: float,
    momentum: float,
    bollinger: float,
    volume: float,
    sentiment: float,
    buy_threshold: float = 0.10,
    sell_threshold: float = -0.30,
    min_buy_signals: int = 3,
) -> SignalResult:
    """
    Compute a weighted signal score and determine the trading action.

    Parameters
    ----------
    rsi             : RSI signal in {-1, 0, +1}
    momentum        : Momentum/breakout signal in {0, +1}
    bollinger       : Bollinger band signal in {-1, 0, +1}
    volume          : Volume surge signal in {0, +1}
    sentiment       : News sentiment in [-1.0, +1.0]
    buy_threshold   : Minimum score to trigger a BUY
    sell_threshold  : Maximum score to trigger a SELL
    min_buy_signals : Minimum number of bullish signals required for a BUY

    Returns
    -------
    SignalResult with all signals, composite score, signals_fired count, and action.
    """
    score = (
        WEIGHTS["rsi"]       * rsi
        + WEIGHTS["momentum"]  * momentum
        + WEIGHTS["bollinger"] * bollinger
        + WEIGHTS["volume"]    * volume
        + WEIGHTS["sentiment"] * sentiment
    )

    # Count how many signals are pointing bullish (value strictly > 0)
    signals_fired = sum(1 for v in (rsi, momentum, bollinger, volume, sentiment) if v > 0)

    if score >= buy_threshold and signals_fired >= min_buy_signals:
        action = "BUY"
    elif score <= sell_threshold:
        action = "SELL"
    else:
        action = "HOLD"

    result = SignalResult(
        rsi=rsi, momentum=momentum, bollinger=bollinger,
        volume=volume, sentiment=sentiment,
        score=score, signals_fired=signals_fired, action=action,
    )
    logger.debug("Signal aggregate: %s", result)
    return result
