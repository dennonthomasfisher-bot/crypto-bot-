"""
config.py – Load all bot settings from environment variables / .env file.
Every configurable value has a sensible default so the bot works out of the
box without editing the .env (in dry-run mode).
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import List

from dotenv import load_dotenv

load_dotenv()


def _env_float(key: str, default: float) -> float:
    return float(os.getenv(key, str(default)))


def _env_pct(key: str, default: float) -> float:
    """Read a fractional percentage; if the stored value is >1 treat it as a
    whole-number percentage and divide by 100 (e.g. 5 → 0.05, 0.05 → 0.05)."""
    v = float(os.getenv(key, str(default)))
    return v / 100.0 if v > 1.0 else v


def _env_int(key: str, default: int) -> int:
    return int(os.getenv(key, str(default)))


def _env_bool(key: str, default: bool) -> bool:
    return os.getenv(key, str(default)).lower() in ("1", "true", "yes")


def _env_list(key: str, default: str) -> List[str]:
    return [p.strip() for p in os.getenv(key, default).split(",") if p.strip()]


@dataclass
class Config:
    # ── Crypto.com Exchange API credentials ───────────────────────────────────
    api_key: str = field(default_factory=lambda: os.getenv("CRYPTO_COM_API_KEY", ""))
    api_secret: str = field(default_factory=lambda: os.getenv("CRYPTO_COM_API_SECRET", ""))

    # ── Operation mode ────────────────────────────────────────────────────────
    # DRY_RUN=true  →  no real orders placed; all order calls are logged only
    dry_run: bool = field(default_factory=lambda: _env_bool("DRY_RUN", True))

    # ── Trading pairs ─────────────────────────────────────────────────────────
    trading_pairs: List[str] = field(default_factory=lambda: _env_list(
        "TRADING_PAIRS",
        "BTC_USDT,XRP_USDT",
    ))

    # ── Candle timeframe ──────────────────────────────────────────────────────
    candle_timeframe: str = field(default_factory=lambda: os.getenv("CANDLE_TIMEFRAME", "15m"))

    # ── Capital & risk management ─────────────────────────────────────────────
    total_capital: float = field(default_factory=lambda: _env_float("TOTAL_CAPITAL", 200.0))
    max_per_trade: float = field(default_factory=lambda: _env_float("MAX_PER_TRADE", 25.0))
    max_position_pct: float = field(default_factory=lambda: _env_pct("MAX_POSITION_PCT", 0.10))
    stop_loss_pct: float = field(default_factory=lambda: _env_pct("STOP_LOSS_PCT", 0.07))
    take_profit_pct: float = field(default_factory=lambda: _env_pct("TAKE_PROFIT_PCT", 0.06))

    # ── RSI strategy ──────────────────────────────────────────────────────────
    rsi_period: int = field(default_factory=lambda: _env_int("RSI_PERIOD", 14))
    rsi_oversold: float = field(default_factory=lambda: _env_float("RSI_OVERSOLD", 40.0))
    rsi_overbought: float = field(default_factory=lambda: _env_float("RSI_OVERBOUGHT", 70.0))

    # ── Momentum / breakout strategy ──────────────────────────────────────────
    momentum_period: int = field(default_factory=lambda: _env_int("MOMENTUM_PERIOD", 3))
    momentum_threshold: float = field(default_factory=lambda: _env_float("MOMENTUM_THRESHOLD", 0.005))

    # ── Bollinger Bands strategy ───────────────────────────────────────────────
    bb_period: int = field(default_factory=lambda: _env_int("BB_PERIOD", 20))
    bb_std: float = field(default_factory=lambda: _env_float("BB_STD", 2.0))

    # ── EMA crossover strategy ────────────────────────────────────────────────
    ema_fast: int = field(default_factory=lambda: _env_int("EMA_FAST", 8))
    ema_slow: int = field(default_factory=lambda: _env_int("EMA_SLOW", 21))

    # ── Volume surge strategy ─────────────────────────────────────────────────
    vol_period: int = field(default_factory=lambda: _env_int("VOL_PERIOD", 20))
    vol_threshold: float = field(default_factory=lambda: _env_float("VOL_THRESHOLD", 1.5))

    # ── Signal aggregation thresholds ─────────────────────────────────────────
    # Combined score in [-1, +1].  score >= buy_threshold AND signals_fired >= min_buy_signals → BUY
    signal_buy_threshold: float = field(
        default_factory=lambda: _env_float("SIGNAL_BUY_THRESHOLD", 0.10)
    )
    signal_sell_threshold: float = field(
        default_factory=lambda: _env_float("SIGNAL_SELL_THRESHOLD", -0.30)
    )
    min_buy_signals: int = field(default_factory=lambda: _env_int("MIN_BUY_SIGNALS", 2))

    # ── BTC macro trend filter ─────────────────────────────────────────────────
    # BUYs across all pairs are blocked when BTC EMA(fast) <= EMA(slow) (downtrend).
    btc_trend_ema_fast: int = field(default_factory=lambda: _env_int("BTC_TREND_EMA_FAST", 20))
    btc_trend_ema_slow: int = field(default_factory=lambda: _env_int("BTC_TREND_EMA_SLOW", 50))

    # ── Minimum RSI before any BUY ────────────────────────────────────────────
    # RSI must be >= this value (not too oversold in a crash) to allow a buy.
    buy_min_rsi: float = field(default_factory=lambda: _env_float("BUY_MIN_RSI", 40.0))

    # ── Trailing stop ─────────────────────────────────────────────────────────
    # Once position is up trailing_breakeven_pct, move stop to entry (breakeven).
    # Once position is up trailing_trigger_pct, trail stop at trailing_distance below peak.
    trailing_breakeven_pct: float = field(default_factory=lambda: _env_pct("TRAILING_BREAKEVEN_PCT", 0.03))
    trailing_trigger_pct: float = field(default_factory=lambda: _env_pct("TRAILING_TRIGGER_PCT", 0.05))
    trailing_distance_pct: float = field(default_factory=lambda: _env_pct("TRAILING_DISTANCE_PCT", 0.03))

    # ── Daily loss limit ──────────────────────────────────────────────────────
    # If daily realised PnL < -(weekly_capital * daily_loss_limit_pct), pause new BUYs until midnight.
    daily_loss_limit_pct: float = field(default_factory=lambda: _env_pct("DAILY_LOSS_LIMIT_PCT", 0.05))

    # ── Weekly capital cycle ──────────────────────────────────────────────────
    # Every Monday the bot resets to weekly_deposit (+ carried profits if last week was positive).
    weekly_deposit: float = field(default_factory=lambda: _env_float("WEEKLY_DEPOSIT", 100.0))

    # ── Polling ───────────────────────────────────────────────────────────────
    poll_interval_seconds: int = field(
        default_factory=lambda: _env_int("POLL_INTERVAL_SECONDS", 60)
    )

    # ── Logging ───────────────────────────────────────────────────────────────
    log_level: str = field(default_factory=lambda: os.getenv("LOG_LEVEL", "INFO"))
