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
        "BTC_USDT,ETH_USDT,SOL_USDT,BNB_USDT,XRP_USDT,"
        "ADA_USDT,AVAX_USDT,DOGE_USDT,DOT_USDT,MATIC_USDT",
    ))

    # ── Risk management ───────────────────────────────────────────────────────
    daily_spend_cap: float = field(default_factory=lambda: _env_float("DAILY_SPEND_CAP", 100.0))
    max_per_trade: float = field(default_factory=lambda: _env_float("MAX_PER_TRADE", 25.0))
    max_position_pct: float = field(default_factory=lambda: _env_float("MAX_POSITION_PCT", 0.10))
    stop_loss_pct: float = field(default_factory=lambda: _env_float("STOP_LOSS_PCT", 0.05))
    take_profit_pct: float = field(default_factory=lambda: _env_float("TAKE_PROFIT_PCT", 0.08))

    # ── RSI strategy ──────────────────────────────────────────────────────────
    rsi_period: int = field(default_factory=lambda: _env_int("RSI_PERIOD", 14))
    rsi_oversold: float = field(default_factory=lambda: _env_float("RSI_OVERSOLD", 30.0))
    rsi_overbought: float = field(default_factory=lambda: _env_float("RSI_OVERBOUGHT", 70.0))

    # ── Momentum / breakout strategy ──────────────────────────────────────────
    momentum_period: int = field(default_factory=lambda: _env_int("MOMENTUM_PERIOD", 20))
    momentum_threshold: float = field(default_factory=lambda: _env_float("MOMENTUM_THRESHOLD", 0.03))

    # ── DCA strategy ──────────────────────────────────────────────────────────
    dca_interval_hours: int = field(default_factory=lambda: _env_int("DCA_INTERVAL_HOURS", 24))
    dca_pairs: List[str] = field(default_factory=lambda: _env_list(
        "DCA_PAIRS", "BTC_USDT,ETH_USDT,SOL_USDT"
    ))
    dca_amount_usd: float = field(default_factory=lambda: _env_float("DCA_AMOUNT_USD", 10.0))

    # ── Signal aggregation thresholds ─────────────────────────────────────────
    # Combined score in [-1, +1].  score >= buy_threshold → BUY
    signal_buy_threshold: float = field(
        default_factory=lambda: _env_float("SIGNAL_BUY_THRESHOLD", 0.30)
    )
    signal_sell_threshold: float = field(
        default_factory=lambda: _env_float("SIGNAL_SELL_THRESHOLD", -0.30)
    )

    # ── Polling ───────────────────────────────────────────────────────────────
    poll_interval_seconds: int = field(
        default_factory=lambda: _env_int("POLL_INTERVAL_SECONDS", 60)
    )

    # ── Logging ───────────────────────────────────────────────────────────────
    log_level: str = field(default_factory=lambda: os.getenv("LOG_LEVEL", "INFO"))
