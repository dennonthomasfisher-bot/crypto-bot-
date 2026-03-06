#!/usr/bin/env python3
"""
backtest.py – Historical signal simulation for the crypto trading bot.

Fetches up to 500 candles of real data from Crypto.com (15m timeframe).
If the network is unavailable, falls back to a seeded synthetic price
series so you still get a meaningful test run.

Usage:
    cd trading_bot
    python backtest.py
"""
from __future__ import annotations

import math
import os
import random
import sys
from typing import List, Optional, Tuple

# ── allow running from the repo root or from trading_bot/ ────────────────────
sys.path.insert(0, os.path.dirname(__file__))

from config import Config
from exchange import CryptoComClient
from signal_aggregator import aggregate
from strategies import (
    bollinger_signal,
    ema_crossover_signal,
    momentum_signal,
    rsi_signal,
    volume_signal,
)

PAIRS        = ["BTC_USDT", "ETH_USDT", "SOL_USDT"]
TRADE_SIZE   = 50.0   # USD per simulated position
TIMEFRAME    = "15m"
TARGET_BARS  = 500


# ── Synthetic data fallback ───────────────────────────────────────────────────

def _synthetic_candles(
    pair: str,
    n: int,
    seed: int,
) -> Tuple[List[float], List[float]]:
    """
    Generate a realistic-looking OHLCV series using a seeded geometric
    random walk with mean-reversion, momentum, and volume clustering.
    """
    rng   = random.Random(seed)
    start = {"BTC_USDT": 65_000.0, "ETH_USDT": 3_200.0, "SOL_USDT": 155.0}.get(pair, 1.0)
    price = start
    vol   = start * 0.15   # base volume

    closes,  volumes = [], []
    momentum = 0.0
    for _ in range(n):
        drift     = 0.0001
        shock     = rng.gauss(0, 0.008)
        momentum  = 0.6 * momentum + 0.4 * shock
        ret       = drift + momentum
        price     = max(price * math.exp(ret), 0.01)
        closes.append(price)
        # Volume: surge on big moves, otherwise cluster around base
        vol_mult  = 1.0 + 3.0 * abs(ret) / 0.008
        vol       = max(0.01, vol * (0.85 + 0.15 * rng.random()) * vol_mult)
        volumes.append(vol)

    return closes, volumes


# ── Fetch from Crypto.com (best-effort) ──────────────────────────────────────

def _fetch_candles(
    client: CryptoComClient,
    pair: str,
) -> Tuple[List[float], List[float], bool]:
    """Return (closes, volumes, is_real).  Falls back to synthetic on error."""
    try:
        candles = client.get_candlestick(pair, timeframe=TIMEFRAME)
        closes  = [float(c["c"]) for c in candles if "c" in c]
        volumes = [float(c["v"]) for c in candles if "v" in c]
        if len(closes) >= 50:
            return closes, volumes, True
    except Exception:
        pass

    seed = sum(ord(ch) for ch in pair)
    closes, volumes = _synthetic_candles(pair, TARGET_BARS, seed)
    return closes, volumes, False


# ── Per-bar signal evaluation ────────────────────────────────────────────────

def _signal_at(
    closes: List[float],
    volumes: List[float],
    cfg: Config,
):
    """Run all five strategies on the current bar and return the aggregated result."""
    vols = volumes if volumes else []
    rsi  = rsi_signal(closes, cfg.rsi_period, cfg.rsi_oversold, cfg.rsi_overbought)
    ema  = ema_crossover_signal(closes, cfg.ema_fast, cfg.ema_slow)
    mom  = momentum_signal(closes, cfg.momentum_period, cfg.momentum_threshold)
    bb   = bollinger_signal(closes, cfg.bb_period, cfg.bb_std)
    vol  = volume_signal(vols, closes, cfg.vol_period, cfg.vol_threshold) if len(vols) >= cfg.vol_period + 1 else 0.0
    return aggregate(
        rsi=rsi, ema=ema, momentum=mom, bollinger=bb, volume=vol,
        buy_threshold=cfg.signal_buy_threshold,
        sell_threshold=cfg.signal_sell_threshold,
        min_buy_signals=cfg.min_buy_signals,
    )


# ── Core simulation ───────────────────────────────────────────────────────────

def _simulate(
    closes: List[float],
    volumes: List[float],
    cfg: Config,
) -> dict:
    """
    Walk bar-by-bar through the price series, apply signal logic, and
    simulate trades with the configured stop-loss and take-profit levels.

    Returns a summary dict.
    """
    # Minimum warm-up bars needed for all indicators
    warmup = max(
        cfg.rsi_period + 1,
        cfg.ema_slow,
        cfg.bb_period,
        cfg.momentum_period + 1,
        cfg.vol_period + 1,
    )
    if len(closes) < warmup + 5:
        return {"error": f"insufficient data ({len(closes)} bars, need {warmup + 5})"}

    trades: list[dict] = []
    equity: list[float] = [0.0]
    position: Optional[dict] = None   # {entry, cost, qty}

    for i in range(warmup, len(closes)):
        c = closes[: i + 1]
        v = volumes[: i + 1]
        price = c[-1]

        # ── Check stop-loss / take-profit for open position ───────────────
        if position is not None:
            sl = position["entry"] * (1.0 - cfg.stop_loss_pct)
            tp = position["entry"] * (1.0 + cfg.take_profit_pct)

            if price <= sl or price >= tp:
                reason  = "STOP_LOSS" if price <= sl else "TAKE_PROFIT"
                exit_px = sl if price <= sl else tp
                exit_px = price   # use actual bar close
                pnl_pct = (exit_px - position["entry"]) / position["entry"]
                pnl_usd = position["cost"] * pnl_pct
                trades.append({"pnl_usd": pnl_usd, "pnl_pct": pnl_pct,
                                "win": pnl_usd > 0, "reason": reason})
                equity.append(equity[-1] + pnl_usd)
                position = None
                continue

        # ── Evaluate signals ───────────────────────────────────────────────
        sig = _signal_at(c, v, cfg)

        if position is None and sig.action == "BUY":
            qty      = TRADE_SIZE / price
            position = {"entry": price, "cost": TRADE_SIZE, "qty": qty}

        elif position is not None and sig.action == "SELL":
            pnl_pct = (price - position["entry"]) / position["entry"]
            pnl_usd = position["cost"] * pnl_pct
            trades.append({"pnl_usd": pnl_usd, "pnl_pct": pnl_pct,
                            "win": pnl_usd > 0, "reason": "SIGNAL"})
            equity.append(equity[-1] + pnl_usd)
            position = None

        else:
            equity.append(equity[-1])

    # Close any still-open position at last price
    if position is not None:
        pnl_pct = (closes[-1] - position["entry"]) / position["entry"]
        pnl_usd = position["cost"] * pnl_pct
        trades.append({"pnl_usd": pnl_usd, "pnl_pct": pnl_pct,
                        "win": pnl_usd > 0, "reason": "OPEN@END"})

    # ── Metrics ────────────────────────────────────────────────────────────
    n      = len(trades)
    wins   = sum(1 for t in trades if t["win"])
    total_pnl = sum(t["pnl_usd"] for t in trades)

    # Max drawdown on equity curve
    peak   = equity[0]
    max_dd = 0.0
    for e in equity:
        if e > peak:
            peak = e
        max_dd = max(max_dd, peak - e)

    # Sharpe ratio: mean trade return / std trade return (not annualised —
    # gives a dimensionless quality ratio; >1.0 is solid)
    if n >= 2:
        returns = [t["pnl_pct"] for t in trades]
        mean_r  = sum(returns) / n
        std_r   = math.sqrt(sum((r - mean_r) ** 2 for r in returns) / (n - 1))
        sharpe  = (mean_r / std_r) if std_r > 0 else 0.0
    else:
        sharpe = 0.0

    return {
        "trades":    n,
        "wins":      wins,
        "win_rate":  wins / n if n else 0.0,
        "total_pnl": total_pnl,
        "max_dd":    max_dd,
        "sharpe":    sharpe,
    }


# ── Pretty-print table ────────────────────────────────────────────────────────

def _print_table(rows: list[dict]) -> None:
    sep = "─" * 74
    print(f"\n{sep}")
    print(f"  {'PAIR':<12}  {'SRC':<9}  {'TRADES':>6}  {'WIN%':>6}  "
          f"{'PnL $':>8}  {'MAX DD':>8}  {'SHARPE':>7}")
    print(sep)
    for r in rows:
        if "error" in r:
            print(f"  {r['pair']:<12}  {r['src']:<9}  ERROR: {r['error']}")
            continue
        sign   = "+" if r["total_pnl"] >= 0 else ""
        dd_str = f"-${r['max_dd']:.2f}"
        print(
            f"  {r['pair']:<12}  {r['src']:<9}  {r['trades']:>6}  "
            f"{r['win_rate']*100:>5.1f}%  "
            f"{sign}{r['total_pnl']:>7.2f}  "
            f"{dd_str:>8}  "
            f"{r['sharpe']:>7.3f}"
        )
    print(sep)

    # Aggregate row
    valid = [r for r in rows if "error" not in r]
    if valid:
        agg_pnl = sum(r["total_pnl"] for r in valid)
        agg_dd  = max(r["max_dd"]    for r in valid)
        agg_wr  = sum(r["win_rate"]  for r in valid) / len(valid)
        agg_sh  = sum(r["sharpe"]    for r in valid) / len(valid)
        agg_tr  = sum(r["trades"]    for r in valid)
        sign    = "+" if agg_pnl >= 0 else ""
        print(
            f"  {'TOTAL':<12}  {'':9}  {agg_tr:>6}  "
            f"{agg_wr*100:>5.1f}%  "
            f"{sign}{agg_pnl:>7.2f}  "
            f"-${agg_dd:>6.2f}  "
            f"{agg_sh:>7.3f}"
        )
        print(sep)

    print(f"\n  Config: ${TRADE_SIZE}/trade  "
          f"SL={cfg_global.stop_loss_pct*100:.1f}%  "
          f"TP={cfg_global.take_profit_pct*100:.1f}%  "
          f"RSI_OS={cfg_global.rsi_oversold:.0f}  "
          f"EMA={cfg_global.ema_fast}/{cfg_global.ema_slow}  "
          f"VOL_THR={cfg_global.vol_threshold:.1f}x\n")


# ── Entry point ───────────────────────────────────────────────────────────────

cfg_global: Config = Config()   # initialised here; main() may override with its own instance


def main() -> None:
    global cfg_global
    cfg    = Config()
    cfg_global = cfg
    client = CryptoComClient(cfg.api_key, cfg.api_secret, dry_run=True)

    print(f"\nBacktest — {TIMEFRAME} candles, up to {TARGET_BARS} bars per pair")
    print(f"Signals: RSI(OS={cfg.rsi_oversold})  EMA({cfg.ema_fast}/{cfg.ema_slow})  "
          f"MOM({cfg.momentum_threshold*100:.2f}%)  BB(±{cfg.bb_std}σ)  "
          f"VOL(>{cfg.vol_threshold}x)")

    rows: list[dict] = []
    for pair in PAIRS:
        closes, volumes, is_real = _fetch_candles(client, pair)
        src  = "live" if is_real else "synthetic"
        bars = len(closes)
        print(f"  {pair}: {bars} bars ({src})", end="  …  ", flush=True)

        result = _simulate(closes, volumes, cfg)
        result["pair"] = pair
        result["src"]  = src
        rows.append(result)

        if "error" in result:
            print(f"ERROR – {result['error']}")
        else:
            print(f"{result['trades']} trades  PnL ${result['total_pnl']:+.2f}")

    _print_table(rows)


if __name__ == "__main__":
    main()
