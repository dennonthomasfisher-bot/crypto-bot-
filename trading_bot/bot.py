#!/usr/bin/env python3
"""
bot.py – Crypto trading bot main entry point.

Usage
─────
    python bot.py

The bot polls all configured trading pairs every POLL_INTERVAL_SECONDS,
computes a combined signal score from four strategies (RSI, momentum, DCA,
news sentiment), and places market orders when the score crosses a threshold.

All parameters are controlled via a .env file (see .env.example).
DRY_RUN=true (the default) prevents any real orders from being submitted.
"""
from __future__ import annotations

import logging
import sys
import time
from typing import List

from config import Config
from exchange import CryptoComClient
from risk_manager import RiskManager
from signal_aggregator import SignalResult, aggregate
from strategies import DCAStrategy, SentimentAnalyzer, momentum_signal, rsi_signal


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


# ── DCA execution (independent of signal score) ───────────────────────────────

def execute_dca(
    pair: str,
    current_price: float,
    cfg: Config,
    client: CryptoComClient,
    risk: RiskManager,
    dca: DCAStrategy,
) -> None:
    """
    Unconditionally execute a DCA buy for `pair` if the budget allows.
    Called only when the DCA timer has fired (dca.is_due() == True).
    """
    log = logging.getLogger("bot.dca")

    dca_size = min(cfg.dca_amount_usd, cfg.max_per_trade, risk.remaining_daily_budget())
    if dca_size < 1.0:
        log.info("DCA %s skipped – insufficient budget (remaining=$%.2f)", pair, risk.remaining_daily_budget())
        return

    result = client.create_market_buy(pair, dca_size)
    if result:
        qty = dca_size / current_price if current_price > 0 else 0.0
        risk.open_position(pair, current_price, qty, dca_size)
        dca.record_buy(pair)
        log.info(
            "DCA BUY  %-15s  $%.2f  qty=%.8f  price=%.4f  next_in=%.1fh",
            pair, dca_size, qty, current_price, dca.hours_until_next(pair),
        )
    else:
        log.warning("DCA BUY order failed for %s", pair)


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

    # In dry-run mode, simulate a $10,000 balance so sizing logic is exercised
    balance = client.get_usdt_balance() if not cfg.dry_run else 10_000.0
    order_size = risk.calculate_order_size(balance)

    if order_size < 1.0:
        log.info(
            "BUY skipped for %s – order_size=$%.2f too small "
            "(balance=$%.2f  remaining_daily=$%.2f)",
            pair, order_size, balance, risk.remaining_daily_budget(),
        )
        return

    result = client.create_market_buy(pair, order_size)
    if result:
        qty = order_size / current_price if current_price > 0 else 0.0
        risk.open_position(pair, current_price, qty, order_size)
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
    dca: DCAStrategy,
    sentiment: SentimentAnalyzer,
) -> None:
    log = logging.getLogger("bot")

    # ── 1. Fetch candles ──────────────────────────────────────────────────────
    candle_count = cfg.momentum_period + cfg.rsi_period + 10
    candles = client.get_candlestick(pair, timeframe="1h", count=candle_count)
    closes = extract_closes(candles)

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
        execute_signal_sell(pair, current_price, SignalResult(0, 0, 0, 0, 0, "SELL"),
                            cfg, client, risk, reason=exit_reason)
        return

    # ── 3. Compute individual signals ─────────────────────────────────────────
    rsi_sig  = rsi_signal(closes, cfg.rsi_period, cfg.rsi_oversold, cfg.rsi_overbought)
    mom_sig  = momentum_signal(closes, cfg.momentum_period, cfg.momentum_threshold)
    dca_sig  = dca.signal(pair)
    sent_sig = sentiment.aggregate_signal()

    # ── 4. Combine into one score ─────────────────────────────────────────────
    signal = aggregate(
        rsi_sig, mom_sig, dca_sig, sent_sig,
        cfg.signal_buy_threshold, cfg.signal_sell_threshold,
    )

    log.info(
        "%-15s  price=%10.4f  %s",
        pair, current_price, signal,
    )

    # ── 5a. DCA path – fires independently on its own timer ───────────────────
    if dca.is_due(pair):
        execute_dca(pair, current_price, cfg, client, risk, dca)
        # After DCA, re-check the signal action (DCA buy may be all we need)
        if signal.action != "SELL":
            return

    # ── 5b. Signal path – RSI / momentum / sentiment driven trades ────────────
    if signal.action == "BUY":
        if risk.has_position(pair):
            log.debug("%s: position already open, not adding via signal", pair)
        else:
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
    log.info("  Mode          : %s", "⚠  DRY RUN (no real orders)" if cfg.dry_run else "🔴 LIVE TRADING")
    log.info("  Pairs         : %s", ", ".join(cfg.trading_pairs))
    log.info("  DCA pairs     : %s", ", ".join(cfg.dca_pairs))
    log.info("  Daily cap     : $%.2f", cfg.daily_spend_cap)
    log.info("  Max per trade : $%.2f", cfg.max_per_trade)
    log.info("  Max pos. size : %.0f%% of balance", cfg.max_position_pct * 100)
    log.info("  Stop loss     : %.1f%%", cfg.stop_loss_pct * 100)
    log.info("  Take profit   : %.1f%%", cfg.take_profit_pct * 100)
    log.info("  Poll interval : %ds", cfg.poll_interval_seconds)
    log.info("  Buy threshold : %+.2f", cfg.signal_buy_threshold)
    log.info("  Sell threshold: %+.2f", cfg.signal_sell_threshold)
    log.info(separator)

    if not cfg.dry_run and (not cfg.api_key or not cfg.api_secret):
        log.error("LIVE mode requires CRYPTO_COM_API_KEY and CRYPTO_COM_API_SECRET in .env")
        sys.exit(1)

    # ── Initialise components ─────────────────────────────────────────────────
    client    = CryptoComClient(cfg.api_key, cfg.api_secret, cfg.dry_run)
    risk      = RiskManager(
        cfg.daily_spend_cap,
        cfg.max_per_trade,
        cfg.max_position_pct,
        cfg.stop_loss_pct,
        cfg.take_profit_pct,
    )
    dca       = DCAStrategy(cfg.dca_interval_hours, cfg.dca_pairs)
    sentiment = SentimentAnalyzer()

    # ── Trading loop ──────────────────────────────────────────────────────────
    cycle = 0
    while True:
        cycle += 1
        log.info("─── Cycle %d ───────────────────────────────────────────────", cycle)

        try:
            for pair in cfg.trading_pairs:
                try:
                    process_pair(pair, cfg, client, risk, dca, sentiment)
                except Exception as exc:
                    log.error("Error processing %s: %s", pair, exc, exc_info=True)
        except KeyboardInterrupt:
            log.info("Keyboard interrupt – shutting down.")
            break
        except Exception as exc:
            log.error("Unexpected main-loop error: %s", exc, exc_info=True)

        log.info("Cycle %d complete.\n%s", cycle, risk.summary())
        log.info("Sleeping %ds …", cfg.poll_interval_seconds)

        try:
            time.sleep(cfg.poll_interval_seconds)
        except KeyboardInterrupt:
            log.info("Keyboard interrupt during sleep – shutting down.")
            break


if __name__ == "__main__":
    main()
