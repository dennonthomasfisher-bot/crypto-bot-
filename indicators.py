"""
Technical indicators for trading signals.

Computes RSI, EMA crossovers, Bollinger Bands, Momentum, and Volume
signals from candlestick data. Each indicator returns a score between
-1.0 (strong sell) and +1.0 (strong buy).
"""

import logging
from typing import Sequence

logger = logging.getLogger(__name__)


def _closes(candles: list[dict]) -> list[float]:
    """Extract closing prices from candlestick data."""
    return [float(c.get("c", c.get("close", 0))) for c in candles]


def _volumes(candles: list[dict]) -> list[float]:
    """Extract volumes from candlestick data."""
    return [float(c.get("v", c.get("volume", 0))) for c in candles]


def _highs(candles: list[dict]) -> list[float]:
    """Extract high prices from candlestick data."""
    return [float(c.get("h", c.get("high", 0))) for c in candles]


def _lows(candles: list[dict]) -> list[float]:
    """Extract low prices from candlestick data."""
    return [float(c.get("l", c.get("low", 0))) for c in candles]


# ── EMA calculation ──────────────────────────────────────────────────────────

def ema(values: Sequence[float], period: int) -> list[float]:
    """Compute Exponential Moving Average."""
    if len(values) < period:
        return []
    k = 2.0 / (period + 1)
    result = [sum(values[:period]) / period]
    for v in values[period:]:
        result.append(v * k + result[-1] * (1 - k))
    return result


# ── RSI ──────────────────────────────────────────────────────────────────────

def rsi(candles: list[dict], period: int = 14) -> float | None:
    """
    Compute RSI (Relative Strength Index).

    Returns RSI value (0-100) or None if insufficient data.
    """
    closes = _closes(candles)
    if len(closes) < period + 1:
        return None

    deltas = [closes[i] - closes[i - 1] for i in range(1, len(closes))]
    gains = [d if d > 0 else 0.0 for d in deltas]
    losses = [-d if d < 0 else 0.0 for d in deltas]

    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period

    for i in range(period, len(deltas)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period

    if avg_loss == 0:
        return 100.0

    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1.0 + rs))


def rsi_signal(candles: list[dict], period: int = 14) -> float:
    """
    RSI signal score.

    Returns:
        +1.0 if RSI < 30 (oversold → buy)
        -1.0 if RSI > 70 (overbought → sell)
         0.0 if neutral or insufficient data
    """
    val = rsi(candles, period)
    if val is None:
        return 0.0
    if val < 30:
        return +1.0
    if val > 70:
        return -1.0
    return 0.0


# ── EMA crossover ────────────────────────────────────────────────────────────

def ema_crossover_signal(
    candles: list[dict],
    fast_period: int = 20,
    slow_period: int = 50,
) -> float:
    """
    EMA crossover signal.

    Returns:
        +1.0 if fast EMA > slow EMA (bullish crossover)
        -1.0 if fast EMA < slow EMA (bearish crossover)
         0.0 if insufficient data
    """
    closes = _closes(candles)
    fast = ema(closes, fast_period)
    slow = ema(closes, slow_period)

    if not fast or not slow:
        return 0.0

    if fast[-1] > slow[-1]:
        return +1.0
    if fast[-1] < slow[-1]:
        return -1.0
    return 0.0


def ema_trend(candles: list[dict], fast: int = 20, slow: int = 50) -> str:
    """Return human-readable EMA trend string."""
    sig = ema_crossover_signal(candles, fast, slow)
    if sig > 0:
        return f"EMA{fast} > EMA{slow}"
    if sig < 0:
        return f"EMA{fast} < EMA{slow}"
    return f"EMA{fast} = EMA{slow}"


# ── Bollinger Bands ──────────────────────────────────────────────────────────

def bollinger_bands(
    candles: list[dict],
    period: int = 20,
    num_std: float = 2.0,
) -> tuple[float, float, float] | None:
    """
    Compute Bollinger Bands (upper, middle, lower).

    Returns (upper, middle, lower) or None if insufficient data.
    """
    closes = _closes(candles)
    if len(closes) < period:
        return None

    window = closes[-period:]
    middle = sum(window) / period
    variance = sum((x - middle) ** 2 for x in window) / period
    std = variance ** 0.5
    return (middle + num_std * std, middle, middle - num_std * std)


def bollinger_signal(candles: list[dict], period: int = 20) -> float:
    """
    Bollinger Band signal.

    Returns:
        +1.0 if price is below lower band (oversold → buy)
        -1.0 if price is above upper band (overbought → sell)
         0.0 otherwise
    """
    bands = bollinger_bands(candles, period)
    if bands is None:
        return 0.0

    upper, _middle, lower = bands
    closes = _closes(candles)
    price = closes[-1]

    if price < lower:
        return +1.0
    if price > upper:
        return -1.0
    return 0.0


# ── Momentum ─────────────────────────────────────────────────────────────────

def momentum_signal(candles: list[dict], period: int = 10) -> float:
    """
    Simple momentum signal based on price change over period.

    Returns:
        +1.0 if positive momentum (price rising)
        -1.0 if negative momentum (price falling)
         0.0 if insufficient data
    """
    closes = _closes(candles)
    if len(closes) < period + 1:
        return 0.0

    current = closes[-1]
    past = closes[-(period + 1)]

    if past == 0:
        return 0.0

    pct = (current - past) / past * 100

    if pct > 2.0:
        return +1.0
    if pct < -2.0:
        return -1.0
    return 0.0


# ── Volume ───────────────────────────────────────────────────────────────────

def volume_signal(candles: list[dict], period: int = 20) -> float:
    """
    Volume signal based on current volume vs average.

    Returns:
        +1.0 if volume spike with price up (bullish confirmation)
        -1.0 if volume spike with price down (bearish confirmation)
         0.0 if no significant volume change
    """
    volumes = _volumes(candles)
    closes = _closes(candles)

    if len(volumes) < period + 1 or len(closes) < 2:
        return 0.0

    avg_vol = sum(volumes[-(period + 1):-1]) / period
    current_vol = volumes[-1]

    if avg_vol == 0:
        return 0.0

    vol_ratio = current_vol / avg_vol

    if vol_ratio < 1.5:
        return 0.0

    # Volume spike detected — check price direction
    if closes[-1] > closes[-2]:
        return +1.0
    if closes[-1] < closes[-2]:
        return -1.0
    return 0.0


# ── Combined signal ──────────────────────────────────────────────────────────

def compute_signals(candles: list[dict]) -> dict:
    """
    Compute all signals and return a summary dict.

    Returns:
        {
            "rsi": float,     # -1/0/+1
            "ema": float,     # -1/0/+1
            "mom": float,     # -1/0/+1
            "bb": float,      # -1/0/+1
            "vol": float,     # -1/0/+1
            "score": float,   # average of all signals
            "signals": int,   # count of non-zero signals
            "total": int,     # total signals (5)
            "action": str,    # "BUY" / "SELL" / "HOLD"
        }
    """
    signals = {
        "rsi": rsi_signal(candles),
        "ema": ema_crossover_signal(candles),
        "mom": momentum_signal(candles),
        "bb": bollinger_signal(candles),
        "vol": volume_signal(candles),
    }

    values = list(signals.values())
    score = sum(values) / len(values) if values else 0.0
    active = sum(1 for v in values if v != 0.0)

    if score >= 0.3:
        action = "BUY"
    elif score <= -0.3:
        action = "SELL"
    else:
        action = "HOLD"

    return {
        **signals,
        "score": round(score, 3),
        "signals": active,
        "total": len(values),
        "action": action,
    }
