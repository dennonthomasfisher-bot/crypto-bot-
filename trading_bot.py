#!/usr/bin/env python3
"""
Crypto Trading Bot – technical analysis and automated trading.

Runs in continuous cycles, analyzing configured trading pairs using
RSI, EMA, Bollinger Bands, Momentum, and Volume indicators.

Requires BTC trend (EMA20 > EMA50) as a macro filter before any buys.

Usage:
    python trading_bot.py              # live trading
    python trading_bot.py --dry-run    # log signals without placing orders
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import signal
import sys
import time

import config
import exchange_client
import indicators

# ── Logging ──────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
    handlers=[
        logging.FileHandler(config.TRADING_LOG_FILE),
        logging.StreamHandler(sys.stdout),
    ],
)
logger = logging.getLogger("bot")

# ── Globals ──────────────────────────────────────────────────────────────────
DRY_RUN = False

# Position tracking file
_POSITIONS_FILE = os.path.join(os.path.dirname(__file__), "positions.json")


# ── Position management ──────────────────────────────────────────────────────

def _load_positions() -> dict:
    """Load tracked positions from disk."""
    if not os.path.exists(_POSITIONS_FILE):
        return {}
    try:
        with open(_POSITIONS_FILE, "r") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return {}


def _save_positions(positions: dict) -> None:
    """Save positions to disk."""
    try:
        with open(_POSITIONS_FILE, "w") as f:
            json.dump(positions, f, indent=2)
    except OSError as exc:
        logger.error("Could not save positions: %s", exc)


def has_open_position(instrument: str) -> bool:
    positions = _load_positions()
    return instrument in positions


def open_position(instrument: str, entry_price: float, quantity: float, cost: float) -> None:
    positions = _load_positions()
    positions[instrument] = {
        "entry": entry_price,
        "quantity": quantity,
        "cost": cost,
        "opened_at": time.time(),
    }
    _save_positions(positions)
    logger.info(
        "Position opened: %s entry=%.4f qty=%.8f cost=$%.2f",
        instrument, entry_price, quantity, cost,
    )


def close_position(instrument: str) -> dict | None:
    positions = _load_positions()
    pos = positions.pop(instrument, None)
    if pos:
        _save_positions(positions)
        logger.info("Position closed: %s", instrument)
    return pos


# ── BTC trend filter ─────────────────────────────────────────────────────────

def check_btc_trend() -> bool:
    """
    Check if BTC macro trend is bullish (EMA20 > EMA50).
    All buys are blocked when BTC trend is bearish.
    """
    candles = exchange_client.get_candlestick("BTC_USDT", timeframe="1h", count=60)
    if not candles:
        logger.warning("Could not fetch BTC candles for trend check")
        return False

    trend = indicators.ema_trend(candles, fast=20, slow=50)
    sig = indicators.ema_crossover_signal(candles, fast_period=20, slow_period=50)
    logger.info("BTC trend   : %s", trend)

    return sig > 0


# ── Process a single trading pair ────────────────────────────────────────────

def process_pair(instrument: str, buy_notional: float) -> None:
    """Analyze a trading pair and execute trades based on signals."""
    candles = exchange_client.get_candlestick(instrument, timeframe="1h", count=60)
    if not candles:
        logger.warning("No candle data for %s – skipping", instrument)
        return

    closes = [float(c.get("c", c.get("close", 0))) for c in candles]
    price = closes[-1] if closes else 0.0

    signals = indicators.compute_signals(candles)

    logger.info(
        "%-12s price=%11.4f  RSI=%+.2f  EMA=%+.2f  MOM=%+.2f  "
        "BB=%+.2f  VOL=%+.2f  score=%+.3f  signals=%d/%d  -> %s",
        instrument,
        price,
        signals["rsi"],
        signals["ema"],
        signals["mom"],
        signals["bb"],
        signals["vol"],
        signals["score"],
        signals["signals"],
        signals["total"],
        signals["action"],
    )

    if signals["action"] == "BUY":
        if has_open_position(instrument):
            pos = _load_positions()[instrument]
            logger.warning(
                "DUPLICATE BUY blocked (process_pair): %s position already open "
                "(entry=%.4f  cost=$%.2f)",
                instrument, pos["entry"], pos["cost"],
            )
            return

        result = exchange_client.market_buy(instrument, buy_notional, dry_run=DRY_RUN)
        if result:
            quantity = buy_notional / price if price > 0 else 0
            logger.info(
                "BUY  %s  $%.2f  qty=%.8f  price=%.4f  score=%+.3f",
                instrument, buy_notional, quantity, price, signals["score"],
            )
            open_position(instrument, price, quantity, buy_notional)

    elif signals["action"] == "SELL":
        if not has_open_position(instrument):
            return

        pos = _load_positions()[instrument]
        result = exchange_client.market_sell(
            instrument, pos["quantity"], dry_run=DRY_RUN,
        )
        if result:
            pnl = (price - pos["entry"]) * pos["quantity"]
            logger.info(
                "SELL %s  qty=%.8f  entry=%.4f  exit=%.4f  PnL=$%.2f",
                instrument, pos["quantity"], pos["entry"], price, pnl,
            )
            close_position(instrument)


# ── Main loop ────────────────────────────────────────────────────────────────

def run_cycle(cycle_num: int) -> None:
    """Run one analysis cycle across all trading pairs."""
    logger.info("── Cycle %d ──────────────────────────────────────────────", cycle_num)

    btc_bullish = check_btc_trend()

    for instrument, notional in config.TRADING_PAIRS.items():
        if not btc_bullish and not has_open_position(instrument):
            logger.info(
                "BTC trend   : EMA20 > EMA50 required for any BUY",
            )
            # Still process to check for sells on open positions
            if has_open_position(instrument):
                process_pair(instrument, notional)
            continue
        process_pair(instrument, notional)


# ── PID file management ─────────────────────────────────────────────────────

_PID_FILE = os.path.join(os.path.dirname(__file__), "bot.pid")


def _check_pid_file() -> None:
    """Check for stale PID file and clean up if needed."""
    if not os.path.exists(_PID_FILE):
        return

    try:
        with open(_PID_FILE, "r") as f:
            old_pid = int(f.read().strip())
        # Check if the process is still running
        try:
            os.kill(old_pid, 0)
            logger.error(
                "ERROR: another instance is already running (PID %d). "
                "Stop it first, or delete %s if it is stale.",
                old_pid, _PID_FILE,
            )
            sys.exit(1)
        except OSError:
            logger.warning("Removing stale PID file (PID %d no longer running)", old_pid)
            os.remove(_PID_FILE)
    except (ValueError, OSError):
        os.remove(_PID_FILE)


def _write_pid_file() -> None:
    with open(_PID_FILE, "w") as f:
        f.write(str(os.getpid()))


def _cleanup_pid_file(*_args) -> None:
    try:
        os.remove(_PID_FILE)
    except OSError:
        pass


# ── Shutdown ─────────────────────────────────────────────────────────────────

def _shutdown(signum, _frame):
    logger.info("Received signal %d – shutting down.", signum)
    _cleanup_pid_file()
    sys.exit(0)


signal.signal(signal.SIGTERM, _shutdown)
signal.signal(signal.SIGINT, _shutdown)


# ── Entry point ──────────────────────────────────────────────────────────────

def main() -> None:
    global DRY_RUN

    parser = argparse.ArgumentParser(description="Crypto Trading Bot")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Log signals without placing real orders",
    )
    args = parser.parse_args()
    DRY_RUN = args.dry_run

    _check_pid_file()
    _write_pid_file()

    if DRY_RUN:
        logger.info("DRY RUN mode – no real orders will be placed.")

    logger.info("Trading bot starting up…")
    logger.info(
        "Trading pairs: %s",
        ", ".join(f"{k} (${v})" for k, v in config.TRADING_PAIRS.items()),
    )

    cycle = 0
    try:
        while True:
            cycle += 1
            run_cycle(cycle)
            logger.info("Sleeping %ds…", config.TRADING_CYCLE_INTERVAL)
            time.sleep(config.TRADING_CYCLE_INTERVAL)
    finally:
        _cleanup_pid_file()


if __name__ == "__main__":
    main()
