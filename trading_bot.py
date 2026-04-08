#!/usr/bin/env python3
"""
trading_bot.py – Crypto.com multi-pair spot trading bot.

Strategy: weighted composite of 5 technical signals.
  RSI(w=0.30)  EMA(w=0.25)  MOM(w=0.20)  BB(w=0.15)  VOL(w=0.10)

Buy  when composite score >= +0.10 AND at least 3/5 signals are positive.
Sell when composite score <= -0.30 OR stop-loss / take-profit triggered.

Usage:
    python trading_bot.py            # live trading (requires CDX keys)
    python trading_bot.py --dry-run  # simulate without placing orders
"""
from __future__ import annotations

import argparse
import logging
import math
import os
import signal
import sys
import time
from dataclasses import dataclass, field

import config
import exchange

# ── Logging ───────────────────────────────────────────────────────────────────
_fmt = "%(asctime)s  %(levelname)-8s  %(name)-20s %(message)s"
_handlers: list[logging.Handler] = [logging.FileHandler(config.TRADING_LOG_FILE)]
logging.basicConfig(level=logging.INFO, format=_fmt, handlers=_handlers)
logger = logging.getLogger("bot")

# ── Signal weights ─────────────────────────────────────────────────────────────
_W_RSI = 0.30
_W_EMA = 0.25
_W_MOM = 0.20
_W_BB  = 0.15
_W_VOL = 0.10

# ── Parameters ────────────────────────────────────────────────────────────────
_RSI_PERIOD       = 14
_RSI_OVERSOLD     = 30.0
_RSI_OVERBOUGHT   = 70.0
_EMA_SHORT        = 9
_EMA_LONG         = 21
_MOM_PERIOD       = 10
_BB_PERIOD        = 20
_BB_STD           = 2.0
_VOL_PERIOD       = 20
_VOL_SURGE_MULT   = 1.5   # vol must be ≥ this × avg to boost signal

BUY_SCORE_THRESHOLD  = 0.10
BUY_MIN_SIGNALS      = 3
SELL_SCORE_THRESHOLD = -0.30

# ── Position ───────────────────────────────────────────────────────────────────
@dataclass
class Position:
    pair: str
    side: str            # "long"
    entry_price: float
    quantity: float      # base asset
    cost: float          # USDT spent
    stop_loss: float
    take_profit: float
    opened_at: float = field(default_factory=time.time)

    def pnl_pct(self, current_price: float) -> float:
        return (current_price - self.entry_price) / self.entry_price

    def should_stop(self, current_price: float) -> bool:
        return current_price <= self.stop_loss

    def should_take_profit(self, current_price: float) -> bool:
        return current_price >= self.take_profit


# ── Indicator helpers (pure Python, no numpy) ─────────────────────────────────

def _ema(values: list[float], period: int) -> list[float]:
    """Compute EMA for a series.  Returns same-length list (NaN at start)."""
    if len(values) < period:
        return [float("nan")] * len(values)
    k = 2.0 / (period + 1)
    result: list[float] = [float("nan")] * len(values)
    # Seed with SMA of first `period` values
    result[period - 1] = sum(values[:period]) / period
    for i in range(period, len(values)):
        result[i] = values[i] * k + result[i - 1] * (1 - k)
    return result


def _rsi(closes: list[float], period: int = 14) -> float | None:
    """Return latest RSI value or None if insufficient data."""
    if len(closes) < period + 1:
        return None
    gains, losses = [], []
    for i in range(1, len(closes)):
        diff = closes[i] - closes[i - 1]
        gains.append(max(diff, 0.0))
        losses.append(max(-diff, 0.0))
    # Use Wilder smoothing (equivalent to EMA with period)
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    for i in range(period, len(gains)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1.0 + rs))


def _bollinger(closes: list[float], period: int = 20, num_std: float = 2.0) -> tuple[float, float, float] | None:
    """Return (lower, mid, upper) for the latest bar."""
    if len(closes) < period:
        return None
    window = closes[-period:]
    mid = sum(window) / period
    variance = sum((x - mid) ** 2 for x in window) / period
    std = math.sqrt(variance)
    return mid - num_std * std, mid, mid + num_std * std


def _momentum(closes: list[float], period: int = 10) -> float | None:
    """Return close[-1] - close[-period-1]."""
    if len(closes) < period + 1:
        return None
    return closes[-1] - closes[-(period + 1)]


# ── Signal scoring ─────────────────────────────────────────────────────────────

def score_candles(candles: list[dict]) -> tuple[float, int]:
    """
    Compute the composite signal score and the count of positive/negative signals.

    Returns (score, n_active_signals) where score ∈ [-1, +1].
    Each indicator contributes its weight positively (bullish) or negatively (bearish).
    """
    if len(candles) < _BB_PERIOD + 1:
        return 0.0, 0

    closes  = [float(c["c"]) for c in candles]
    volumes = [float(c["v"]) for c in candles]

    score = 0.0
    n_signals = 0

    # RSI
    rsi_val = _rsi(closes, _RSI_PERIOD)
    if rsi_val is not None:
        if rsi_val < _RSI_OVERSOLD:
            score += _W_RSI
            n_signals += 1
        elif rsi_val > _RSI_OVERBOUGHT:
            score -= _W_RSI
            n_signals += 1

    # EMA crossover
    ema_short_series = _ema(closes, _EMA_SHORT)
    ema_long_series  = _ema(closes, _EMA_LONG)
    ema_short = ema_short_series[-1]
    ema_long  = ema_long_series[-1]
    if not math.isnan(ema_short) and not math.isnan(ema_long):
        if ema_short > ema_long:
            score += _W_EMA
            n_signals += 1
        elif ema_short < ema_long:
            score -= _W_EMA
            n_signals += 1

    # Momentum
    mom = _momentum(closes, _MOM_PERIOD)
    if mom is not None:
        if mom > 0:
            score += _W_MOM
            n_signals += 1
        elif mom < 0:
            score -= _W_MOM
            n_signals += 1

    # Bollinger Bands
    bb = _bollinger(closes, _BB_PERIOD, _BB_STD)
    if bb is not None:
        lower, mid, upper = bb
        price = closes[-1]
        if price < lower:
            score += _W_BB
            n_signals += 1
        elif price > upper:
            score -= _W_BB
            n_signals += 1

    # Volume surge (amplifies the directional bias)
    if len(volumes) >= _VOL_PERIOD + 1:
        avg_vol = sum(volumes[-_VOL_PERIOD - 1:-1]) / _VOL_PERIOD
        cur_vol = volumes[-1]
        if avg_vol > 0 and cur_vol >= avg_vol * _VOL_SURGE_MULT:
            # Volume surge — add weight in the direction the score is leaning
            direction = 1.0 if score >= 0 else -1.0
            score += _W_VOL * direction
            n_signals += 1

    return score, n_signals


# ── Capital / position management ─────────────────────────────────────────────

class Portfolio:
    def __init__(self, total_capital: float, dry_run: bool):
        self.total_capital = total_capital
        self.used_capital  = 0.0
        self.positions: dict[str, Position] = {}
        self.dry_run = dry_run

    @property
    def available(self) -> float:
        return self.total_capital - self.used_capital

    def max_trade_size(self) -> float:
        limit_by_cap  = min(config.TRADING_MAX_PER_TRADE, self.available)
        limit_by_pct  = self.total_capital * config.TRADING_MAX_POS_PCT
        return min(limit_by_cap, limit_by_pct)

    def open_position(self, pair: str, price: float, cost: float) -> bool:
        if pair in self.positions:
            return False
        if cost > self.available + 0.01:
            logger.warning("%s: insufficient capital (need %.2f, have %.2f)", pair, cost, self.available)
            return False

        quantity = cost / price
        stop_loss   = price * (1 - config.TRADING_STOP_LOSS_PCT)
        take_profit = price * (1 + config.TRADING_TAKE_PROFIT_PCT)

        if not self.dry_run:
            result = exchange.place_order(pair, "BUY", quantity, "MARKET")
            if not result:
                logger.error("%s: BUY order failed", pair)
                return False
            logger.info("%s: BUY %.6f @ %.4f (cost $%.2f)", pair, quantity, price, cost)
        else:
            logger.info("[DRY RUN] %s: BUY %.6f @ %.4f (cost $%.2f)", pair, quantity, price, cost)

        self.positions[pair] = Position(
            pair=pair,
            side="long",
            entry_price=price,
            quantity=quantity,
            cost=cost,
            stop_loss=stop_loss,
            take_profit=take_profit,
        )
        self.used_capital += cost
        return True

    def close_position(self, pair: str, price: float, reason: str) -> bool:
        pos = self.positions.get(pair)
        if not pos:
            return False

        pnl_pct = pos.pnl_pct(price)
        pnl_usd = pos.cost * pnl_pct

        if not self.dry_run:
            result = exchange.place_order(pair, "SELL", pos.quantity, "MARKET")
            if not result:
                logger.error("%s: SELL order failed", pair)
                return False
            logger.info("%s: SELL %.6f @ %.4f | PnL: %+.2f%% ($%+.2f) [%s]",
                        pair, pos.quantity, price, pnl_pct * 100, pnl_usd, reason)
        else:
            logger.info("[DRY RUN] %s: SELL %.6f @ %.4f | PnL: %+.2f%% ($%+.2f) [%s]",
                        pair, pos.quantity, price, pnl_pct * 100, pnl_usd, reason)

        self.used_capital -= pos.cost
        del self.positions[pair]
        return True

    def summary(self) -> str:
        lines = [
            f"  Capital     : ${self.used_capital:.2f} used / ${self.total_capital:.2f} total"
            f"  (${self.available:.2f} available)",
            f"  Open positions ({len(self.positions)}):",
        ]
        for pair, pos in self.positions.items():
            lines.append(
                f"    {pair:12s}  entry={pos.entry_price:.4f}  qty={pos.quantity:.6f}"
                f"  sl={pos.stop_loss:.4f}  tp={pos.take_profit:.4f}"
            )
        return "\n".join(lines)


# ── Candle cache ──────────────────────────────────────────────────────────────

# pair → list of candles (oldest first), kept trimmed to last 300
_candle_cache: dict[str, list[dict]] = {}
_MIN_CANDLES = _BB_PERIOD + _MOM_PERIOD + 5  # need at least ~35 bars


def _update_candles(pair: str, timeframe: str) -> list[dict]:
    """Fetch fresh candles and merge into cache.  Returns updated candle list."""
    fresh = exchange.get_candlesticks(pair, timeframe, depth=200)
    if not fresh:
        return _candle_cache.get(pair, [])

    existing = _candle_cache.get(pair, [])
    if not existing:
        _candle_cache[pair] = fresh
    else:
        last_t = existing[-1]["t"]
        new = [c for c in fresh if c["t"] > last_t]
        if new:
            existing.extend(new)
            # Trim to last 300
            _candle_cache[pair] = existing[-300:]

    return _candle_cache[pair]


# ── Shutdown ───────────────────────────────────────────────────────────────────

def _shutdown(signum, frame):
    logger.info("Received signal %d — shutting down.", signum)
    sys.exit(0)

signal.signal(signal.SIGTERM, _shutdown)
signal.signal(signal.SIGINT,  _shutdown)


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="Crypto.com Trading Bot")
    parser.add_argument("--dry-run", action="store_true",
                        help="Simulate trades without placing real orders")
    args = parser.parse_args()
    dry_run: bool = args.dry_run

    # Also attach stdout handler so the launch-agent log file captures output
    if sys.stdout.isatty() or dry_run:
        logging.getLogger().addHandler(logging.StreamHandler(sys.stdout))

    pairs     = config.TRADING_PAIRS
    timeframe = config.TRADING_TIMEFRAME

    signal_desc = (
        f"RSI(w={_W_RSI:.2f}) "
        f"EMA(w={_W_EMA:.2f}) "
        f"MOM(w={_W_MOM:.2f}) "
        f"BB(w={_W_BB:.2f}) "
        f"VOL(w={_W_VOL:.2f})"
    )

    logger.info("=" * 65)
    logger.info("  Crypto Trading Bot")
    logger.info("=" * 65)
    logger.info("  Mode          :   %s", "DRY RUN (no real orders)" if dry_run else "LIVE")
    logger.info("  Pairs         : %s", ", ".join(pairs))
    logger.info("  Timeframe     : %s", timeframe)
    logger.info("  Total capital : $%.2f", config.TRADING_CAPITAL)
    logger.info("  Max per trade : $%.2f", config.TRADING_MAX_PER_TRADE)
    logger.info("  Max pos. size : %.0f%% of capital", config.TRADING_MAX_POS_PCT * 100)
    logger.info("  Stop loss     : %.1f%%", config.TRADING_STOP_LOSS_PCT * 100)
    logger.info("  Take profit   : %.1f%%", config.TRADING_TAKE_PROFIT_PCT * 100)
    logger.info("  Poll interval : %ds", config.TRADING_POLL_INTERVAL)
    logger.info("  Signals       : %s", signal_desc)
    logger.info("  Buy threshold : score>=+%.2f  min signals: %d/5",
                BUY_SCORE_THRESHOLD, BUY_MIN_SIGNALS)
    logger.info("  Sell threshold: score<=%.2f", SELL_SCORE_THRESHOLD)
    logger.info("=" * 65)

    portfolio = Portfolio(config.TRADING_CAPITAL, dry_run)

    # ── Pre-fetch candle history ───────────────────────────────────────────────
    logger.info("Pre-fetching candle history (%s) …", timeframe)
    for pair in pairs:
        candles = _update_candles(pair, timeframe)
        vols = [float(c["v"]) for c in candles[-3:]] if candles else []
        vol_avg = (
            sum(float(c["v"]) for c in candles[-_VOL_PERIOD - 1:-1]) / _VOL_PERIOD
            if len(candles) > _VOL_PERIOD
            else 0.0
        )
        logger.info("  %-12s %3d candles  vol(last3)=%-20s vol_avg20=%.2f",
                    pair, len(candles), str([round(v, 2) for v in vols]), vol_avg)
    logger.info("=" * 65)

    cycle = 0
    while True:
        cycle += 1
        logger.info("─── Cycle %d ───────────────────────────────────────────────", cycle)

        for pair in pairs:
            # ── Update candles ────────────────────────────────────────────────
            candles = _update_candles(pair, timeframe)
            if not candles:
                logger.warning("%s: no candle data returned, skipping", pair)
                continue
            if len(candles) < _MIN_CANDLES:
                logger.debug("%s: only %d candles (need %d), skipping",
                             pair, len(candles), _MIN_CANDLES)
                continue

            current_price = float(candles[-1]["c"])

            # ── Manage open positions ─────────────────────────────────────────
            if pair in portfolio.positions:
                pos = portfolio.positions[pair]
                if pos.should_stop(current_price):
                    portfolio.close_position(pair, current_price, "stop-loss")
                    continue
                if pos.should_take_profit(current_price):
                    portfolio.close_position(pair, current_price, "take-profit")
                    continue
                score, n_sig = score_candles(candles)
                if score <= SELL_SCORE_THRESHOLD:
                    portfolio.close_position(pair, current_price, f"signal-sell score={score:.3f}")
                continue

            # ── Evaluate buy signal ───────────────────────────────────────────
            score, n_sig = score_candles(candles)
            logger.debug("%s: score=%.3f  n_signals=%d  price=%.4f",
                         pair, score, n_sig, current_price)

            if score >= BUY_SCORE_THRESHOLD and n_sig >= BUY_MIN_SIGNALS:
                trade_size = portfolio.max_trade_size()
                if trade_size < 1.0:
                    logger.info("%s: BUY signal but insufficient capital ($%.2f)", pair, trade_size)
                    continue
                logger.info("%s: BUY signal — score=%.3f n_sig=%d price=%.4f size=$%.2f",
                            pair, score, n_sig, current_price, trade_size)
                portfolio.open_position(pair, current_price, trade_size)

        # ── Cycle summary ─────────────────────────────────────────────────────
        logger.info("Cycle %d complete.\n%s", cycle, portfolio.summary())
        logger.info("Sleeping %ds …", config.TRADING_POLL_INTERVAL)
        time.sleep(config.TRADING_POLL_INTERVAL)


if __name__ == "__main__":
    main()
