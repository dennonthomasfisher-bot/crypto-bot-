#!/usr/bin/env python3
"""
bot.py – Crypto trading bot main entry point.

Usage
─────
    python bot.py

The bot polls all configured trading pairs every POLL_INTERVAL_SECONDS,
computes a combined signal score from five strategies (RSI, momentum,
Bollinger Bands, volume surge, news sentiment), and places market orders
when the score crosses a threshold and enough signals agree.

All parameters are controlled via a .env file (see .env.example).
DRY_RUN=true (the default) prevents any real orders from being submitted.
"""
from __future__ import annotations

import logging
import os
import sys
import time
from typing import Dict, List

from config import Config
import exchange                          # 'exchange' in module scope – prevents NameError
from exchange import CryptoComClient     # also imported directly for type hints
from risk_manager import RiskManager
from signal_aggregator import SignalResult, aggregate

# Absolute path so positions.json is always in the same directory as bot.py,
# regardless of the working directory the bot is launched from.
POSITIONS_FILE = os.path.join(os.path.abspath(os.path.dirname(__file__)), "positions.json")

from strategies import (
    bollinger_signal,
    ema_crossover_signal,
    momentum_signal,
    rsi_signal,
    volume_signal,
)


# ── Logging ───────────────────────────────────────────────────────────────────

def setup_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s  %(levelname)-8s  %(name)-20s  %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        handlers=[
            logging.StreamHandler(sys.stdout),
        ],
    )


# ── Helpers ───────────────────────────────────────────────────────────────────

def extract_closes(candles: List[dict]) -> List[float]:
    """Pull closing prices from the Crypto.com candlestick payload."""
    closes = []
    for c in candles:
        try:
            closes.append(float(c["c"]))
        except (KeyError, ValueError, TypeError):
            pass
    return closes


def extract_volumes(candles: List[dict]) -> List[float]:
    """Pull volume values from the Crypto.com candlestick payload (field 'v')."""
    volumes = []
    for c in candles:
        try:
            volumes.append(float(c["v"]))
        except (KeyError, ValueError, TypeError):
            pass
    return volumes


def current_price_from_ticker(client: CryptoComClient, pair: str) -> float:
    """Fetch the latest trade price from the ticker endpoint."""
    ticker = client.get_ticker(pair)
    if not ticker:
        return 0.0
    # 'a' = latest trade price, 'k' = best ask, 'b' = best bid
    for key in ("a", "k", "b"):
        val = ticker.get(key)
        if val:
            try:
                return float(val)
            except (ValueError, TypeError):
                pass
    return 0.0


# ── Signal-based trading ──────────────────────────────────────────────────────

def execute_signal_buy(
    pair: str,
    current_price: float,
    signal: SignalResult,
    cfg: Config,
    client: CryptoComClient,
    risk: RiskManager,
) -> None:
    """Place a signal-driven market buy."""
    log = logging.getLogger("bot.trade")

    if pair in risk.positions:
        log.warning(
            "DUPLICATE BUY blocked: %s already has an open position "
            "(entry=%.4f  qty=%.8f  cost=$%.2f)",
            pair,
            risk.positions[pair].entry_price,
            risk.positions[pair].quantity,
            risk.positions[pair].cost_basis,
        )
        return

    order_size = risk.calculate_order_size()

    if order_size < 1.0:
        log.info(
            "BUY skipped for %s – order_size=$%.2f too small "
            "(committed=$%.2f  available=$%.2f)",
            pair, order_size, risk.total_committed(), risk.available_capital(),
        )
        return

    result = client.create_market_buy(pair, order_size)
    if result:
        qty = order_size / current_price if current_price > 0 else 0.0
        risk.open_position(pair, current_price, qty, order_size)
        risk.save_positions(POSITIONS_FILE)
        log.info(
            "BUY  %-15s  $%.2f  qty=%.8f  price=%.4f  score=%+.3f",
            pair, order_size, qty, current_price, signal.score,
        )
    else:
        log.warning("BUY order failed for %s", pair)


def execute_signal_sell(
    pair: str,
    current_price: float,
    signal: SignalResult,
    cfg: Config,
    client: CryptoComClient,
    risk: RiskManager,
    reason: str = "SIGNAL",
) -> None:
    """Place a market sell for the full open position."""
    log = logging.getLogger("bot.trade")

    pos = risk.positions.get(pair)
    if pos is None:
        return

    result = client.create_market_sell(pair, pos.quantity)
    if result:
        pnl_pct = (current_price - pos.entry_price) / pos.entry_price * 100
        risk.close_position(pair)
        risk.save_positions(POSITIONS_FILE)
        log.info(
            "SELL [%s]  %-15s  qty=%.8f  entry=%.4f  exit=%.4f  PnL=%+.2f%%",
            reason, pair, pos.quantity, pos.entry_price, current_price, pnl_pct,
        )
    else:
        log.warning("SELL order failed for %s", pair)


# ── Per-pair processing ───────────────────────────────────────────────────────

def process_pair(
    pair: str,
    cfg: Config,
    client: CryptoComClient,
    risk: RiskManager,
    candle_history: Dict[str, List[float]],
    volume_history: Dict[str, List[float]],
) -> None:
    log = logging.getLogger("bot")

    # ── 1. Fetch latest candles and update history ────────────────────────────
    candles = client.get_candlestick(pair, timeframe=cfg.candle_timeframe)
    fresh_closes = extract_closes(candles)
    fresh_volumes = extract_volumes(candles)

    if fresh_closes:
        stored = candle_history.get(pair, [])
        candle_history[pair] = fresh_closes if len(fresh_closes) >= len(stored) else stored

    if fresh_volumes:
        stored_vol = volume_history.get(pair, [])
        volume_history[pair] = fresh_volumes if len(fresh_volumes) >= len(stored_vol) else stored_vol

    closes  = candle_history.get(pair, [])
    volumes = volume_history.get(pair, [])

    if not closes:
        log.warning("%s: no candle data returned, skipping", pair)
        return

    current_price = closes[-1]
    if current_price <= 0.0:
        current_price = current_price_from_ticker(client, pair)
    if current_price <= 0.0:
        log.warning("%s: unable to determine current price, skipping", pair)
        return

    # ── 2. Check stop-loss / take-profit (exit before computing new signals) ──
    exit_reason = risk.check_exit_conditions(pair, current_price)
    if exit_reason:
        execute_signal_sell(
            pair, current_price,
            SignalResult(rsi=0, ema=0, momentum=0, bollinger=0, volume=0,
                         score=0, signals_fired=0, action="SELL"),
            cfg, client, risk, reason=exit_reason,
        )
        return

    # ── 3. Compute individual signals ─────────────────────────────────────────
    rsi_sig = rsi_signal(closes, cfg.rsi_period, cfg.rsi_oversold, cfg.rsi_overbought)
    ema_sig = ema_crossover_signal(closes, cfg.ema_fast, cfg.ema_slow)
    mom_sig = momentum_signal(closes, cfg.momentum_period, cfg.momentum_threshold)
    bb_sig  = bollinger_signal(closes, cfg.bb_period, cfg.bb_std)
    vol_sig = volume_signal(volumes, closes, cfg.vol_period, cfg.vol_threshold)

    # ── 4. Combine into one score ─────────────────────────────────────────────
    signal = aggregate(
        rsi=rsi_sig,
        ema=ema_sig,
        momentum=mom_sig,
        bollinger=bb_sig,
        volume=vol_sig,
        buy_threshold=cfg.signal_buy_threshold,
        sell_threshold=cfg.signal_sell_threshold,
        min_buy_signals=cfg.min_buy_signals,
    )

    # ── 5. Signal path – RSI / momentum / BB / volume / sentiment driven trades
    if signal.action == "BUY" and pair in risk.positions:
        log.warning(
            "DUPLICATE BUY blocked (process_pair): %s position already open "
            "(entry=%.4f  cost=$%.2f)",
            pair,
            risk.positions[pair].entry_price,
            risk.positions[pair].cost_basis,
        )
        return

    log.info(
        "%-15s  price=%10.4f  %s",
        pair, current_price, signal,
    )

    if signal.action == "BUY":
        execute_signal_buy(pair, current_price, signal, cfg, client, risk)

    elif signal.action == "SELL":
        if risk.has_position(pair):
            execute_signal_sell(pair, current_price, signal, cfg, client, risk)
        else:
            log.debug("%s: SELL signal but no position held", pair)


# ── Main loop ─────────────────────────────────────────────────────────────────

def main() -> None:
    cfg = Config()
    setup_logging(cfg.log_level)
    log = logging.getLogger("bot")

    # ── Banner ────────────────────────────────────────────────────────────────
    separator = "=" * 65
    log.info(separator)
    log.info("  Crypto Trading Bot")
    log.info(separator)
    log.info("  Mode          : %s", "  DRY RUN (no real orders)" if cfg.dry_run else "LIVE TRADING")
    log.info("  Pairs         : %s", ", ".join(cfg.trading_pairs))
    log.info("  Timeframe     : %s", cfg.candle_timeframe)
    log.info("  Total capital : $%.2f", cfg.total_capital)
    log.info("  Max per trade : $%.2f", cfg.max_per_trade)
    log.info("  Max pos. size : %.0f%% of capital", cfg.max_position_pct * 100)
    log.info("  Stop loss     : %.1f%%", cfg.stop_loss_pct * 100)
    log.info("  Take profit   : %.1f%%", cfg.take_profit_pct * 100)
    log.info("  Poll interval : %ds", cfg.poll_interval_seconds)
    log.info("  Signals       : RSI(w=0.30) EMA(w=0.25) MOM(w=0.20) BB(w=0.15) VOL(w=0.10)")
    log.info("  Buy threshold : score>=%+.2f  min signals: %d/5",
             cfg.signal_buy_threshold, cfg.min_buy_signals)
    log.info("  Sell threshold: score<=%+.2f", cfg.signal_sell_threshold)
    log.info(separator)

    if not cfg.dry_run and (not cfg.api_key or not cfg.api_secret):
        log.error("LIVE mode requires CRYPTO_COM_API_KEY and CRYPTO_COM_API_SECRET in .env")
        sys.exit(1)

    # ── Initialise components ─────────────────────────────────────────────────
    client    = exchange.CryptoComClient(cfg.api_key, cfg.api_secret, cfg.dry_run)
    risk      = RiskManager(
        cfg.total_capital,
        cfg.max_per_trade,
        cfg.max_position_pct,
        cfg.stop_loss_pct,
        cfg.take_profit_pct,
    )

    # ── Restore positions from last run ───────────────────────────────────────
    restored = risk.load_positions(POSITIONS_FILE)
    if restored:
        log.info("  Restored %d open position(s) from positions.json", restored)
        for pair, pos in risk.positions.items():
            log.info("    %-15s  qty=%.8f  entry=%.4f  cost=$%.2f",
                     pair, pos.quantity, pos.entry_price, pos.cost_basis)
        log.info(separator)

    # ── Pre-fetch candle history (≥30 candles per pair before cycle 1) ────────
    candle_history: Dict[str, List[float]] = {}
    volume_history: Dict[str, List[float]] = {}
    log.info("Pre-fetching candle history (%s) …", cfg.candle_timeframe)
    for pair in cfg.trading_pairs:
        candles = client.get_candlestick(pair, timeframe=cfg.candle_timeframe)
        closes  = extract_closes(candles)
        volumes = extract_volumes(candles)
        candle_history[pair] = closes
        volume_history[pair] = volumes
        vol_last3 = volumes[-3:] if len(volumes) >= 3 else volumes
        vol_avg = sum(volumes[-cfg.vol_period:]) / len(volumes[-cfg.vol_period:]) if volumes else 0.0
        log.info(
            "  %-15s  %d candles  vol(last3)=%s  vol_avg%d=%.2f",
            pair, len(closes),
            [f"{v:.2f}" for v in vol_last3],
            cfg.vol_period, vol_avg,
        )
    log.info(separator)

    # ── Trading loop ──────────────────────────────────────────────────────────
    cycle = 0
    last_committed = -1.0  # sentinel so first cycle always prints
    while True:
        cycle += 1
        log.info("─── Cycle %d ───────────────────────────────────────────────", cycle)

        try:
            for pair in cfg.trading_pairs:
                try:
                    process_pair(pair, cfg, client, risk,
                                 candle_history, volume_history)
                except Exception as exc:
                    log.error("Error processing %s: %s", pair, exc, exc_info=True)
        except KeyboardInterrupt:
            log.info("Keyboard interrupt – shutting down.")
            break
        except Exception as exc:
            log.error("Unexpected main-loop error: %s", exc, exc_info=True)

        committed = risk.total_committed()
        if committed != last_committed:
            log.info("Cycle %d complete.\n%s", cycle, risk.summary())
            last_committed = committed

        log.info("Sleeping %ds …", cfg.poll_interval_seconds)

        try:
            time.sleep(cfg.poll_interval_seconds)
        except KeyboardInterrupt:
            log.info("Keyboard interrupt during sleep – shutting down.")
            break


if __name__ == "__main__":
    main()
