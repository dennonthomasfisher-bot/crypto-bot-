#!/usr/bin/env python3
"""
backtest.py – Historical signal simulation using CoinGecko's free public API.

Fetches 30 days of 1-hour OHLCV data (no API key required) for BTC, ETH,
and SOL, then walks bar-by-bar applying the same signal stack and risk
rules as the live bot.  Falls back to a seeded synthetic series if the
network is unavailable.

Usage:
    cd trading_bot
    python backtest.py

Metrics reported per pair:
    Trades    – total round-trips completed
    Win %     – percentage of trades closed at a profit
    PnL $     – total realised profit/loss (USD) at $50/trade
    Max DD    – maximum peak-to-trough drawdown on the equity curve
    Sharpe    – per-trade mean/std ratio (>1.0 is solid, not annualised)
"""
from __future__ import annotations

import math
import os
import sys
import time
from typing import Dict, List, Optional, Tuple

import requests

# ── allow running from the repo root or from trading_bot/ ────────────────────
sys.path.insert(0, os.path.dirname(__file__))

from config import Config
from signal_aggregator import aggregate
from strategies import (
    bollinger_signal,
    ema_crossover_signal,
    momentum_signal,
    rsi_signal,
    volume_signal,
)

# ── Constants ─────────────────────────────────────────────────────────────────

COINGECKO_BASE = "https://api.coingecko.com/api/v3"

# Crypto.com pair name → CoinGecko coin ID
PAIR_TO_CG: Dict[str, str] = {
    "BTC_USDT": "bitcoin",
    "ETH_USDT": "ethereum",
    "SOL_USDT": "solana",
}

TRADE_SIZE = 50.0   # USD per simulated position
DAYS       = 30     # calendar days of history to fetch


# ── Data fetching ─────────────────────────────────────────────────────────────

def _fetch_coingecko(
    pair: str,
) -> Tuple[List[float], List[float], str]:
    """
    Fetch 30 days of hourly close prices and volumes from CoinGecko.

    CoinGecko automatically returns hourly granularity for day windows
    between 2 and 90 days, giving ~720 data points per pair.

    Returns (closes, volumes, source_label).
    Falls back to synthetic data on any network or parsing error.
    """
    cg_id = PAIR_TO_CG.get(pair)
    if not cg_id:
        return _synthetic_fallback(pair)

    url    = f"{COINGECKO_BASE}/coins/{cg_id}/market_chart"
    params = {"vs_currency": "usd", "days": str(DAYS)}

    try:
        resp = requests.get(url, params=params, timeout=20,
                            headers={"Accept": "application/json"})
        if not resp.ok:
            print(f"    ↳ CoinGecko HTTP {resp.status_code} — falling back to synthetic")
            return _synthetic_fallback(pair)

        data    = resp.json()
        prices  = data.get("prices", [])         # [[timestamp_ms, price], …]
        volumes = data.get("total_volumes", [])  # [[timestamp_ms, volume], …]

        closes = [float(p[1]) for p in prices]
        vols   = [float(v[1]) for v in volumes]

        n = min(len(closes), len(vols))
        if n < 50:
            print(f"    ↳ CoinGecko returned only {n} points — falling back to synthetic")
            return _synthetic_fallback(pair)

        return closes[:n], vols[:n], "CoinGecko"

    except requests.RequestException as exc:
        print(f"    ↳ Network error ({exc.__class__.__name__}) — falling back to synthetic")
        return _synthetic_fallback(pair)
    except Exception as exc:
        print(f"    ↳ Parse error ({exc}) — falling back to synthetic")
        return _synthetic_fallback(pair)


def _synthetic_fallback(pair: str) -> Tuple[List[float], List[float], str]:
    """
    Seeded geometric random walk with momentum clustering.
    Used when CoinGecko is unreachable (e.g. this sandboxed environment).
    Produces DAYS*24 hourly bars.
    """
    import random

    rng      = random.Random(sum(ord(c) for c in pair))
    start    = {"BTC_USDT": 85_000.0, "ETH_USDT": 2_000.0, "SOL_USDT": 140.0}.get(pair, 1.0)
    price    = start
    vol      = start * 0.15
    closes: List[float]  = []
    volumes: List[float] = []
    mom      = 0.0

    for _ in range(DAYS * 24):
        shock = rng.gauss(0, 0.008)
        mom   = 0.6 * mom + 0.4 * shock
        price = max(price * math.exp(0.0001 + mom), 0.01)
        closes.append(price)
        vol_mult = 1.0 + 3.0 * abs(mom) / 0.008
        vol      = max(0.01, vol * (0.85 + 0.15 * rng.random()) * vol_mult)
        volumes.append(vol)

    return closes, volumes, "synthetic"


# ── Signal evaluation ─────────────────────────────────────────────────────────

def _signal_at(
    closes:  List[float],
    volumes: List[float],
    cfg:     Config,
):
    """Run all five live-bot strategies on the current bar and aggregate."""
    rsi = rsi_signal(closes, cfg.rsi_period, cfg.rsi_oversold, cfg.rsi_overbought)
    ema = ema_crossover_signal(closes, cfg.ema_fast, cfg.ema_slow)
    mom = momentum_signal(closes, cfg.momentum_period, cfg.momentum_threshold)
    bb  = bollinger_signal(closes, cfg.bb_period, cfg.bb_std)
    vol = (
        volume_signal(volumes, closes, cfg.vol_period, cfg.vol_threshold)
        if len(volumes) >= cfg.vol_period + 1
        else 0.0
    )
    return aggregate(
        rsi=rsi, ema=ema, momentum=mom, bollinger=bb, volume=vol,
        buy_threshold=cfg.signal_buy_threshold,
        sell_threshold=cfg.signal_sell_threshold,
        min_buy_signals=cfg.min_buy_signals,
    )


# ── Core simulation ───────────────────────────────────────────────────────────

def _simulate(
    closes:  List[float],
    volumes: List[float],
    cfg:     Config,
) -> dict:
    """
    Walk bar-by-bar through the price series applying signal and risk logic
    identical to the live bot (trailing stop, SL, TP, signal-based exit).

    Returns a metrics dict, or {"error": reason} if data is too short.
    """
    warmup = max(
        cfg.rsi_period + 1,
        cfg.ema_slow,
        cfg.bb_period,
        cfg.momentum_period + 1,
        cfg.vol_period + 1,
    )
    if len(closes) < warmup + 5:
        return {"error": f"only {len(closes)} bars, need ≥{warmup + 5}"}

    trades:   List[dict]    = []
    equity:   List[float]   = [0.0]
    position: Optional[dict] = None  # {"entry", "cost", "high"}

    for i in range(warmup, len(closes)):
        c     = closes[:i + 1]
        v     = volumes[:i + 1]
        price = c[-1]

        # ── Exit checks (trailing stop → SL → TP) ─────────────────────────
        if position is not None:
            position["high"] = max(position["high"], price)
            peak_gain = (position["high"] - position["entry"]) / position["entry"]

            # Mirror live bot's trailing stop logic exactly
            if peak_gain >= cfg.trailing_trigger_pct:
                eff_stop   = max(
                    position["entry"],
                    position["high"] * (1.0 - cfg.trailing_distance_pct),
                )
                stop_label = "TRAIL_STOP"
            elif peak_gain >= cfg.trailing_breakeven_pct:
                eff_stop   = position["entry"]
                stop_label = "TRAIL_STOP"
            else:
                eff_stop   = position["entry"] * (1.0 - cfg.stop_loss_pct)
                stop_label = "STOP_LOSS"

            if price <= eff_stop:
                pnl_pct = (price - position["entry"]) / position["entry"]
                pnl_usd = position["cost"] * pnl_pct
                trades.append({"pnl_usd": pnl_usd, "pnl_pct": pnl_pct,
                                "win": pnl_usd > 0, "reason": stop_label})
                equity.append(equity[-1] + pnl_usd)
                position = None
                continue

        # ── Signal evaluation ──────────────────────────────────────────────
        sig = _signal_at(c, v, cfg)

        if position is None and sig.action == "BUY":
            position = {"entry": price, "cost": TRADE_SIZE, "high": price}
            equity.append(equity[-1])

        elif position is not None and sig.action == "SELL":
            pnl_pct = (price - position["entry"]) / position["entry"]
            pnl_usd = position["cost"] * pnl_pct
            trades.append({"pnl_usd": pnl_usd, "pnl_pct": pnl_pct,
                            "win": pnl_usd > 0, "reason": "SIGNAL"})
            equity.append(equity[-1] + pnl_usd)
            position = None

        else:
            equity.append(equity[-1])

    # Close any still-open position at the final bar
    if position is not None:
        pnl_pct = (closes[-1] - position["entry"]) / position["entry"]
        pnl_usd = position["cost"] * pnl_pct
        trades.append({"pnl_usd": pnl_usd, "pnl_pct": pnl_pct,
                        "win": pnl_usd > 0, "reason": "OPEN@END"})

    # ── Metrics ────────────────────────────────────────────────────────────
    n         = len(trades)
    wins      = sum(1 for t in trades if t["win"])
    total_pnl = sum(t["pnl_usd"] for t in trades)

    # Max drawdown on cumulative equity curve
    peak   = 0.0
    max_dd = 0.0
    for e in equity:
        peak   = max(peak, e)
        max_dd = max(max_dd, peak - e)

    # Per-trade Sharpe: mean(returns) / std(returns)
    if n >= 2:
        rets   = [t["pnl_pct"] for t in trades]
        mean_r = sum(rets) / n
        std_r  = math.sqrt(sum((r - mean_r) ** 2 for r in rets) / (n - 1))
        sharpe = (mean_r / std_r) if std_r > 0 else 0.0
    else:
        sharpe = 0.0

    # Exit-reason breakdown
    by_reason: Dict[str, int] = {}
    for t in trades:
        by_reason[t["reason"]] = by_reason.get(t["reason"], 0) + 1

    return {
        "trades":    n,
        "wins":      wins,
        "win_rate":  wins / n if n else 0.0,
        "total_pnl": total_pnl,
        "max_dd":    max_dd,
        "sharpe":    sharpe,
        "by_reason": by_reason,
    }


# ── Output formatting ─────────────────────────────────────────────────────────

def _print_table(rows: List[dict], cfg: Config) -> None:
    W   = 82
    sep = "─" * W
    dbl = "═" * W

    print(f"\n{dbl}")
    print(f"  BACKTEST  —  last {DAYS} days  ·  1-hour candles  ·  ${TRADE_SIZE:.0f} per trade")
    print(f"  SL {cfg.stop_loss_pct*100:.1f}%"
          f"  Trail breakeven +{cfg.trailing_breakeven_pct*100:.0f}%"
          f"  Trail trigger +{cfg.trailing_trigger_pct*100:.0f}% / -{cfg.trailing_distance_pct*100:.0f}%"
          f"  (no fixed TP)")
    print(f"  MOM period={cfg.momentum_period} thr={cfg.momentum_threshold*100:.1f}%"
          f"  RSI OS={cfg.rsi_oversold:.0f}"
          f"  EMA {cfg.ema_fast}/{cfg.ema_slow}"
          f"  BB ±{cfg.bb_std}σ"
          f"  VOL >{cfg.vol_threshold}x"
          f"  min_signals={cfg.min_buy_signals}/5")
    print(dbl)
    print(f"  {'PAIR':<12}  {'SOURCE':<11}  {'BARS':>5}  {'TRADES':>6}  "
          f"{'WIN%':>6}  {'PnL $':>9}  {'MAX DD':>8}  {'SHARPE':>7}")
    print(sep)

    valid = []
    for r in rows:
        if "error" in r:
            print(f"  {r['pair']:<12}  {r.get('src',''):<11}  "
                  f"{'ERROR':>5}  {r['error']}")
            continue

        valid.append(r)
        pnl_col = f"{r['total_pnl']:+.2f}"
        dd_col  = f"-${r['max_dd']:.2f}"
        print(
            f"  {r['pair']:<12}  {r['src']:<11}  {r['bars']:>5}  "
            f"{r['trades']:>6}  {r['win_rate']*100:>5.1f}%  "
            f"{pnl_col:>9}  {dd_col:>8}  {r['sharpe']:>7.3f}"
        )
        # Exit breakdown on the next line, indented
        if r.get("by_reason"):
            parts = "  ".join(
                f"{k}={v}" for k, v in sorted(r["by_reason"].items())
            )
            print(f"  {'':12}  {'':11}  {'':5}  {'exits →':>6}  {parts}")

    if valid:
        print(sep)
        agg_pnl = sum(r["total_pnl"] for r in valid)
        agg_dd  = max(r["max_dd"]    for r in valid)
        agg_wr  = sum(r["win_rate"]  for r in valid) / len(valid)
        agg_sh  = sum(r["sharpe"]    for r in valid) / len(valid)
        agg_tr  = sum(r["trades"]    for r in valid)
        print(
            f"  {'ALL PAIRS':<12}  {'avg/total':<11}  {'':>5}  "
            f"{agg_tr:>6}  {agg_wr*100:>5.1f}%  "
            f"{agg_pnl:>+9.2f}  -${agg_dd:>6.2f}  {agg_sh:>7.3f}"
        )
    print(dbl + "\n")


# ── Entry point ───────────────────────────────────────────────────────────────

cfg_global: Config = Config()


def main() -> None:
    global cfg_global
    cfg        = Config()
    cfg_global = cfg

    print(f"\nFetching {DAYS}-day hourly data from CoinGecko (free API, no key) …")

    rows: List[dict] = []
    for idx, pair in enumerate(cfg.trading_pairs):
        if idx > 0:
            time.sleep(1.5)   # stay well within CoinGecko free-tier rate limit

        print(f"  {pair} … ", end="", flush=True)
        closes, volumes, src = _fetch_coingecko(pair)
        bars = len(closes)
        print(f"{bars} bars ({src})  →  ", end="", flush=True)

        result = _simulate(closes, volumes, cfg)
        result["pair"] = pair
        result["src"]  = src
        result["bars"] = bars
        rows.append(result)

        if "error" in result:
            print(f"ERROR — {result['error']}")
        else:
            print(
                f"{result['trades']} trades  "
                f"PnL ${result['total_pnl']:+.2f}  "
                f"win={result['win_rate']*100:.0f}%  "
                f"Sharpe={result['sharpe']:.3f}"
            )

    _print_table(rows, cfg)


if __name__ == "__main__":
    main()
