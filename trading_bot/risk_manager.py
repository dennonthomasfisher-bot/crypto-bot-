"""
risk_manager.py – Position sizing, capital tracking, stop-loss, take-profit,
trailing stops, daily loss limit, and weekly capital cycle.

All monetary limits are configurable via environment variables (see .env.example).
"""
from __future__ import annotations

import datetime
import json
import logging
import os
import time
from dataclasses import dataclass
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)


@dataclass
class Position:
    """Represents an open holding in a single instrument."""

    pair: str
    entry_price: float   # Average entry price (USD)
    quantity: float      # Base-asset quantity held
    cost_basis: float    # Total USD spent to acquire this position


class RiskManager:
    """
    Enforces all monetary risk rules:

    1. Weekly capital  – resets every Monday; compounds profits, absorbs losses.
    2. Daily loss cap  – pauses new BUYs until midnight if losses exceed 5% of weekly capital.
    3. Dynamic sizing  – Kelly-adjacent: (weekly_capital × 2%) / stop_loss_pct,
                         adjusted ×1.0–1.5 by signal score, capped at 20% weekly capital.
    4. Trailing stop   – once up 3% → stop moves to breakeven;
                         once up 5% → stop trails 3% below the peak.
    (Fixed take-profit removed — exits rely solely on trailing stop and sell signals.)
    """

    def __init__(
        self,
        total_capital: float,
        max_per_trade: float,
        max_position_pct: float,
        stop_loss_pct: float,
        take_profit_pct: float,
        daily_loss_limit_pct: float = 0.05,
        weekly_deposit: float = 100.0,
        trailing_breakeven_pct: float = 0.03,
        trailing_trigger_pct: float = 0.05,
        trailing_distance_pct: float = 0.02,
    ) -> None:
        self.total_capital = total_capital
        self.max_per_trade = max_per_trade
        self.max_position_pct = max_position_pct
        self.stop_loss_pct = stop_loss_pct
        self.take_profit_pct = take_profit_pct
        self.daily_loss_limit_pct = daily_loss_limit_pct
        self.weekly_deposit = weekly_deposit
        self.trailing_breakeven_pct = trailing_breakeven_pct
        self.trailing_trigger_pct = trailing_trigger_pct
        self.trailing_distance_pct = trailing_distance_pct

        # ── Position state ────────────────────────────────────────────────────
        self.positions: Dict[str, Position] = {}
        self.price_highs: Dict[str, float] = {}   # high-water marks for trailing stops

        # ── Daily loss tracking ───────────────────────────────────────────────
        self.daily_date: str = datetime.date.today().isoformat()
        self.daily_pnl_usd: float = 0.0
        self.trading_paused_until: Optional[float] = None   # Unix timestamp

        # ── Weekly capital cycle ──────────────────────────────────────────────
        today = datetime.date.today()
        monday = today - datetime.timedelta(days=today.weekday())
        self.weekly_start_date: str = monday.isoformat()
        self.weekly_capital: float = weekly_deposit
        self.weekly_start_capital: float = weekly_deposit
        self.weekly_pnl_usd: float = 0.0
        self.weekly_trades: List[Dict] = []
        self._last_report_key: str = ""

    # ── Capital tracking ──────────────────────────────────────────────────────

    def total_committed(self) -> float:
        """Return total USD currently locked in open positions."""
        return sum(p.cost_basis for p in self.positions.values())

    def available_capital(self) -> float:
        """Return how much USD from the weekly budget can still be deployed."""
        return max(0.0, self.weekly_capital - self.total_committed())

    # ── Dynamic position sizing ───────────────────────────────────────────────

    def calculate_order_size(self, signal_score: float = 0.5) -> float:
        """
        Kelly-adjacent dynamic position size.

        Base  = (weekly_capital × 2%) / stop_loss_pct
                → risks exactly 2% of capital if stop is hit
        Adj   = base × (1.0 + clamp(score, 0, 1) × 0.5)
                → 1.0× at weakest valid signal, 1.5× at full conviction
        Caps  = min(max_per_trade, weekly_capital × 20%, available_capital)
        Floor = $10; returns 0.0 if available capital is below $10.
        """
        available = self.available_capital()
        if available < 10.0:
            return 0.0

        base_size = (self.weekly_capital * 0.02) / max(self.stop_loss_pct, 0.01)
        score_adj = 1.0 + min(max(signal_score, 0.0), 1.0) * 0.5
        adjusted = base_size * score_adj

        # Capital-protection cap: never more than 20% of weekly capital per trade
        max_weekly_size = self.weekly_capital * 0.20
        max_size = min(self.max_per_trade, max_weekly_size)

        size = min(adjusted, max_size, available)
        return max(10.0, size)

    # ── Trailing stop / exit conditions ──────────────────────────────────────

    def check_exit_conditions(
        self, pair: str, current_price: float
    ) -> Optional[str]:
        """
        Evaluate exit conditions in priority order:
          1. Trailing stop (overrides plain stop loss once activated)
          2. Plain stop loss

        Trailing stop levels:
          peak gain >= trailing_trigger_pct (5%)  → trail at trailing_distance (3%) below peak
          peak gain >= trailing_breakeven_pct (3%) → stop moves to entry (breakeven)
          otherwise                               → plain stop at entry × (1 − stop_loss_pct)

        Fixed take-profit removed; exits rely on trailing stop and sell signals only.
        Returns exit reason string, or None if no exit triggered.
        """
        pos = self.positions.get(pair)
        if pos is None:
            return None

        # Update high-water mark
        prev_high = self.price_highs.get(pair, pos.entry_price)
        high = max(prev_high, current_price)
        self.price_highs[pair] = high

        peak_gain_pct = (high - pos.entry_price) / pos.entry_price

        # Determine effective stop level based on peak gain achieved so far
        if peak_gain_pct >= self.trailing_trigger_pct:
            # Trailing mode: stop trails trailing_distance below peak, never below breakeven
            trail_level = high * (1.0 - self.trailing_distance_pct)
            effective_stop = max(pos.entry_price, trail_level)
            exit_label = "TRAILING_STOP"
        elif peak_gain_pct >= self.trailing_breakeven_pct:
            # Breakeven mode: stop sits at entry price
            effective_stop = pos.entry_price
            exit_label = "TRAILING_STOP"
        else:
            effective_stop = pos.entry_price * (1.0 - self.stop_loss_pct)
            exit_label = "STOP_LOSS"

        if current_price <= effective_stop:
            pct_chg = (current_price - pos.entry_price) / pos.entry_price * 100
            logger.warning(
                "%s  %s  entry=%.6f  current=%.6f  (%.2f%%)  "
                "stop=%.6f  peak_gain=%.2f%%",
                exit_label, pair, pos.entry_price, current_price, pct_chg,
                effective_stop, peak_gain_pct * 100,
            )
            return exit_label

        return None

    # ── Daily loss limit ──────────────────────────────────────────────────────

    def is_trading_paused(self) -> bool:
        """Return True if daily loss limit has been hit and trading is paused until midnight."""
        if self.trading_paused_until is None:
            return False
        if time.time() >= self.trading_paused_until:
            self.trading_paused_until = None
            logger.info("Daily loss limit pause expired — trading resumed.")
            return False
        return True

    def record_trade_pnl(self, pnl_usd: float, pair: str, pnl_pct: float) -> None:
        """
        Update daily and weekly PnL trackers after a trade closes.
        Triggers the daily loss limit pause if the threshold is breached.
        """
        today = datetime.date.today().isoformat()
        if today != self.daily_date:
            # New calendar day: reset daily tracker
            self.daily_date = today
            self.daily_pnl_usd = 0.0
            self.trading_paused_until = None

        self.daily_pnl_usd += pnl_usd
        self.weekly_pnl_usd += pnl_usd
        self.weekly_trades.append({
            "pair": pair,
            "pnl_usd": round(pnl_usd, 4),
            "pnl_pct": round(pnl_pct, 4),
        })

        # Check daily loss limit
        daily_limit_usd = self.weekly_capital * self.daily_loss_limit_pct
        if self.daily_pnl_usd < -daily_limit_usd and self.trading_paused_until is None:
            midnight = datetime.datetime.combine(
                datetime.date.today() + datetime.timedelta(days=1),
                datetime.time.min,
            )
            self.trading_paused_until = midnight.timestamp()
            logger.warning(
                "DAILY LOSS LIMIT hit: daily_pnl=$%.2f  limit=$%.2f "
                "(%.0f%% of $%.2f weekly capital). "
                "New BUYs PAUSED until %s.",
                self.daily_pnl_usd, -daily_limit_usd,
                self.daily_loss_limit_pct * 100, self.weekly_capital,
                midnight.strftime("%Y-%m-%d 00:00"),
            )

    # ── Weekly capital cycle ──────────────────────────────────────────────────

    def check_weekly_reset(self) -> bool:
        """
        If today is a new week (Monday), reset weekly capital.
        Profits from the previous week are carried forward; losses are not compounded.
        Returns True if a reset occurred.
        """
        today = datetime.date.today()
        monday = today - datetime.timedelta(days=today.weekday())
        monday_str = monday.isoformat()

        if monday_str == self.weekly_start_date:
            return False   # Still the same week

        # New week
        if self.weekly_pnl_usd > 0:
            new_capital = self.weekly_deposit + self.weekly_pnl_usd
            logger.info(
                "WEEKLY RESET (%s): carrying forward $%.2f profit → "
                "new capital $%.2f ($%.2f deposit + $%.2f profit)",
                monday_str, self.weekly_pnl_usd, new_capital,
                self.weekly_deposit, self.weekly_pnl_usd,
            )
        else:
            new_capital = self.weekly_deposit
            logger.info(
                "WEEKLY RESET (%s): starting fresh at $%.2f deposit "
                "(last week PnL was $%.2f — losses not compounded)",
                monday_str, self.weekly_deposit, self.weekly_pnl_usd,
            )

        self.weekly_capital = new_capital
        self.weekly_start_capital = new_capital
        self.weekly_start_date = monday_str
        self.weekly_pnl_usd = 0.0
        self.weekly_trades = []
        self.daily_pnl_usd = 0.0
        self.trading_paused_until = None
        return True

    def check_friday_report(self) -> Optional[str]:
        """
        Generate and return the weekly performance report on Friday at 20:xx.
        Fires at most once per hour. Returns the report string or None.
        """
        now = datetime.datetime.now()
        if now.weekday() != 4 or now.hour != 20:
            return None
        key = now.strftime("%Y-%m-%d %H")
        if key == self._last_report_key:
            return None
        self._last_report_key = key
        return self.weekly_report()

    def weekly_report(self) -> str:
        """Format the weekly performance summary for logging."""
        trades = self.weekly_trades
        n = len(trades)
        end_capital = self.weekly_start_capital + self.weekly_pnl_usd
        pnl_pct = (
            self.weekly_pnl_usd / self.weekly_start_capital * 100
            if self.weekly_start_capital > 0 else 0.0
        )
        sep = "=" * 62

        if n == 0:
            return "\n".join([
                sep,
                "  WEEKLY PERFORMANCE REPORT",
                sep,
                f"  Week of     : {self.weekly_start_date}",
                f"  Capital     : ${self.weekly_start_capital:.2f} → ${end_capital:.2f}",
                f"  Total PnL   : $0.00  (0.0%)  — no trades this week",
                sep,
            ])

        wins = [t for t in trades if t["pnl_usd"] > 0]
        best = max(trades, key=lambda t: t["pnl_usd"])
        worst = min(trades, key=lambda t: t["pnl_usd"])

        return "\n".join([
            sep,
            "  WEEKLY PERFORMANCE REPORT",
            sep,
            f"  Week of     : {self.weekly_start_date}",
            f"  Capital     : ${self.weekly_start_capital:.2f} → ${end_capital:.2f}",
            f"  Total PnL   : ${self.weekly_pnl_usd:+.2f}  ({pnl_pct:+.1f}%)",
            f"  Trades      : {n}  |  Win rate: {len(wins)/n*100:.0f}%",
            f"  Best trade  : {best['pair']}  ${best['pnl_usd']:+.2f}  ({best['pnl_pct']:+.1f}%)",
            f"  Worst trade : {worst['pair']}  ${worst['pnl_usd']:+.2f}  ({worst['pnl_pct']:+.1f}%)",
            sep,
        ])

    # ── Position lifecycle ────────────────────────────────────────────────────

    def open_position(
        self,
        pair: str,
        entry_price: float,
        quantity: float,
        cost: float,
    ) -> None:
        """
        Record a new position for `pair`.

        Raises ValueError if a position already exists — callers must check
        `has_position` before calling.  Silently averaging-in was removed
        because it masked duplicate-BUY bugs.
        """
        if pair in self.positions:
            raise ValueError(
                f"open_position called for {pair} but a position already exists "
                f"(entry={self.positions[pair].entry_price:.6f}  "
                f"qty={self.positions[pair].quantity:.8f}).  "
                f"Close the existing position before opening a new one."
            )
        self.positions[pair] = Position(pair, entry_price, quantity, cost)
        self.price_highs[pair] = entry_price   # initialise trailing stop watermark at entry
        logger.info(
            "Opened position %s  entry=%.6f  qty=%.8f  cost=%.2f",
            pair, entry_price, quantity, cost,
        )

    def close_position(self, pair: str) -> Optional[Position]:
        """Remove and return the position for `pair`, or None if not held."""
        pos = self.positions.pop(pair, None)
        self.price_highs.pop(pair, None)   # clear trailing stop watermark
        if pos:
            logger.info(
                "Closed position %s (qty=%.8f  cost=%.2f)",
                pair, pos.quantity, pos.cost_basis,
            )
        return pos

    def has_position(self, pair: str) -> bool:
        """Return True if the bot currently holds `pair`."""
        return pair in self.positions

    # ── Persistence ───────────────────────────────────────────────────────────

    def save_positions(self, path: str) -> bool:
        """
        Persist full state (positions, price highs, daily/weekly trackers) to JSON.
        Uses atomic write (.tmp → rename) to avoid partial writes.
        Returns True on success, False on IO error.
        """
        data = {
            "schema_version": 2,
            "positions": {
                pair: {
                    "entry_price": pos.entry_price,
                    "quantity":    pos.quantity,
                    "cost_basis":  pos.cost_basis,
                }
                for pair, pos in self.positions.items()
            },
            "price_highs": dict(self.price_highs),
            "daily": {
                "date":          self.daily_date,
                "pnl_usd":       self.daily_pnl_usd,
                "paused_until":  self.trading_paused_until,
            },
            "weekly": {
                "start_date":    self.weekly_start_date,
                "start_capital": self.weekly_start_capital,
                "capital":       self.weekly_capital,
                "pnl_usd":       self.weekly_pnl_usd,
                "trades":        self.weekly_trades,
            },
        }
        tmp = path + ".tmp"
        try:
            with open(tmp, "w") as f:
                json.dump(data, f, indent=2)
            os.replace(tmp, path)
        except OSError as exc:
            logger.error(
                "CRITICAL: failed to write positions to %s – in-memory state is "
                "correct but positions.json is stale.  Restart will lose open "
                "positions!  Error: %s",
                path, exc,
            )
            return False
        logger.info(
            "Positions saved → %s  (%d open: %s)",
            path, len(data["positions"]), ", ".join(data["positions"]) or "none",
        )
        return True

    def load_positions(self, path: str) -> int:
        """
        Reload full state from a JSON file written by save_positions.
        Handles both schema v2 (current) and v1 (legacy positions-only format).
        Returns the number of positions restored.
        """
        if not os.path.exists(path):
            return 0
        try:
            with open(path) as f:
                data = json.load(f)

            if data.get("schema_version") == 2:
                # ── Schema v2 ──────────────────────────────────────────────
                pos_data = data.get("positions", {})
                self.price_highs = {
                    k: float(v) for k, v in data.get("price_highs", {}).items()
                }
                daily = data.get("daily", {})
                self.daily_date         = daily.get("date", self.daily_date)
                self.daily_pnl_usd      = float(daily.get("pnl_usd", 0.0))
                self.trading_paused_until = daily.get("paused_until")

                weekly = data.get("weekly", {})
                self.weekly_start_date    = weekly.get("start_date", self.weekly_start_date)
                self.weekly_start_capital = float(weekly.get("start_capital", self.weekly_deposit))
                self.weekly_capital       = float(weekly.get("capital", self.weekly_deposit))
                self.weekly_pnl_usd       = float(weekly.get("pnl_usd", 0.0))
                self.weekly_trades        = weekly.get("trades", [])
            else:
                # ── Schema v1 legacy: top-level keys are position pairs ────
                pos_data = {
                    k: v for k, v in data.items()
                    if isinstance(v, dict) and "entry_price" in v
                }

            for pair, d in pos_data.items():
                self.positions[pair] = Position(
                    pair=pair,
                    entry_price=float(d["entry_price"]),
                    quantity=float(d["quantity"]),
                    cost_basis=float(d["cost_basis"]),
                )
                # Initialise high watermark at entry if not loaded from file
                if pair not in self.price_highs:
                    self.price_highs[pair] = float(d["entry_price"])

            logger.info("Restored %d position(s) from %s", len(pos_data), path)
            return len(pos_data)

        except Exception as exc:
            logger.warning("Could not load positions from %s: %s", path, exc)
            return 0

    # ── Summary ───────────────────────────────────────────────────────────────

    def summary(self) -> str:
        lines = []
        if self.trading_paused_until:
            resume = datetime.datetime.fromtimestamp(self.trading_paused_until)
            lines.append(
                f"  *** TRADING PAUSED until {resume.strftime('%H:%M')} "
                "(daily loss limit) ***"
            )
        lines += [
            f"  Weekly capital : ${self.weekly_capital:.2f}  "
            f"(week of {self.weekly_start_date})",
            f"  Weekly PnL     : ${self.weekly_pnl_usd:+.2f}",
            f"  Daily PnL      : ${self.daily_pnl_usd:+.2f}",
            f"  Committed      : ${self.total_committed():.2f} / ${self.weekly_capital:.2f}  "
            f"(${self.available_capital():.2f} available)",
            f"  Open positions ({len(self.positions)}):",
        ]
        for pair, pos in self.positions.items():
            high = self.price_highs.get(pair, pos.entry_price)
            peak_pct = (high - pos.entry_price) / pos.entry_price * 100
            lines.append(
                f"    {pair:15s}  qty={pos.quantity:.8f}  "
                f"entry=${pos.entry_price:.4f}  cost=${pos.cost_basis:.2f}  "
                f"peak={peak_pct:+.1f}%"
            )
        return "\n".join(lines)
