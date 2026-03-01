"""
risk_manager.py – Position sizing, daily spend tracking, stop-loss and take-profit.

All monetary limits are configurable via environment variables (see .env.example).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date
from typing import Dict, Optional

logger = logging.getLogger(__name__)


@dataclass
class Position:
    """Represents an open holding in a single instrument."""

    pair: str
    entry_price: float   # Average entry price (USD)
    quantity: float      # Base-asset quantity held
    cost_basis: float    # Total USD spent to acquire this position

    @property
    def stop_loss_price(self, pct: float = 0.05) -> float:
        return self.entry_price * (1.0 - pct)

    @property
    def take_profit_price(self, pct: float = 0.08) -> float:
        return self.entry_price * (1.0 + pct)


class RiskManager:
    """
    Enforces all monetary risk rules:

    1. Daily spend cap    – hard ceiling on how much USD can be deployed per day.
    2. Max per trade      – single-order size ceiling in USD.
    3. Max position size  – ceiling as a percentage of available balance.
    4. Stop-loss          – auto-exit when price falls below `entry × (1 − sl_pct)`.
    5. Take-profit        – auto-exit when price rises above `entry × (1 + tp_pct)`.
    """

    def __init__(
        self,
        daily_spend_cap: float,
        max_per_trade: float,
        max_position_pct: float,
        stop_loss_pct: float,
        take_profit_pct: float,
    ) -> None:
        self.daily_spend_cap = daily_spend_cap
        self.max_per_trade = max_per_trade
        self.max_position_pct = max_position_pct
        self.stop_loss_pct = stop_loss_pct
        self.take_profit_pct = take_profit_pct

        self._daily_spend: Dict[date, float] = {}
        self.positions: Dict[str, Position] = {}

    # ── Daily spend tracking ──────────────────────────────────────────────────

    def today_spent(self) -> float:
        """Return total USD deployed so far today."""
        return self._daily_spend.get(date.today(), 0.0)

    def remaining_daily_budget(self) -> float:
        """Return how much USD can still be spent today."""
        return max(0.0, self.daily_spend_cap - self.today_spent())

    def can_spend(self, amount: float) -> bool:
        """Return True if `amount` fits within today's remaining budget."""
        return amount <= self.remaining_daily_budget()

    def record_spend(self, amount: float) -> None:
        """Accumulate `amount` against today's spend cap."""
        today = date.today()
        self._daily_spend[today] = self._daily_spend.get(today, 0.0) + amount
        logger.debug(
            "Daily spend: $%.2f / $%.2f",
            self._daily_spend[today], self.daily_spend_cap,
        )

    # ── Position sizing ───────────────────────────────────────────────────────

    def calculate_order_size(self, balance_usd: float) -> float:
        """
        Compute the USD amount to spend on a single buy, respecting:

        * `max_per_trade`      – absolute per-trade ceiling
        * `max_position_pct`   – fraction-of-balance ceiling
        * remaining daily cap  – calendar-day ceiling

        Returns 0.0 if no budget remains.
        """
        cap_balance = balance_usd * self.max_position_pct
        cap_daily   = self.remaining_daily_budget()
        size = min(self.max_per_trade, cap_balance, cap_daily)
        return max(0.0, size)

    # ── Stop-loss / take-profit ───────────────────────────────────────────────

    def check_exit_conditions(
        self, pair: str, current_price: float
    ) -> Optional[str]:
        """
        Evaluate stop-loss and take-profit levels for an open position.

        Returns
        -------
        "STOP_LOSS"   if current_price has breached the stop level
        "TAKE_PROFIT" if current_price has reached the profit target
        None          if no exit condition is met (or no position open)
        """
        pos = self.positions.get(pair)
        if pos is None:
            return None

        sl_level = pos.entry_price * (1.0 - self.stop_loss_pct)
        tp_level = pos.entry_price * (1.0 + self.take_profit_pct)

        if current_price <= sl_level:
            pct_chg = (current_price - pos.entry_price) / pos.entry_price * 100
            logger.warning(
                "STOP LOSS  %s  entry=%.6f  current=%.6f  (%.2f%%)",
                pair, pos.entry_price, current_price, pct_chg,
            )
            return "STOP_LOSS"

        if current_price >= tp_level:
            pct_chg = (current_price - pos.entry_price) / pos.entry_price * 100
            logger.info(
                "TAKE PROFIT %s  entry=%.6f  current=%.6f  (+%.2f%%)",
                pair, pos.entry_price, current_price, pct_chg,
            )
            return "TAKE_PROFIT"

        return None

    # ── Position lifecycle ────────────────────────────────────────────────────

    def open_position(
        self,
        pair: str,
        entry_price: float,
        quantity: float,
        cost: float,
    ) -> None:
        """
        Record a new (or averaged-in) position.

        If a position already exists for `pair`, the entry price is
        recalculated as a weighted average (cost-averaging).
        """
        existing = self.positions.get(pair)
        if existing:
            total_qty  = existing.quantity  + quantity
            total_cost = existing.cost_basis + cost
            avg_price  = total_cost / total_qty
            self.positions[pair] = Position(pair, avg_price, total_qty, total_cost)
            logger.info(
                "Averaged into %s  new_avg=%.6f  total_qty=%.8f  total_cost=%.2f",
                pair, avg_price, total_qty, total_cost,
            )
        else:
            self.positions[pair] = Position(pair, entry_price, quantity, cost)
            logger.info(
                "Opened position %s  entry=%.6f  qty=%.8f  cost=%.2f",
                pair, entry_price, quantity, cost,
            )
        self.record_spend(cost)

    def close_position(self, pair: str) -> Optional[Position]:
        """Remove and return the position for `pair`, or None if not held."""
        pos = self.positions.pop(pair, None)
        if pos:
            logger.info("Closed position %s (qty=%.8f  cost=%.2f)", pair, pos.quantity, pos.cost_basis)
        return pos

    def has_position(self, pair: str) -> bool:
        """Return True if the bot currently holds `pair`."""
        return pair in self.positions

    # ── Summary ───────────────────────────────────────────────────────────────

    def summary(self) -> str:
        lines = [
            f"  Daily spend : ${self.today_spent():.2f} / ${self.daily_spend_cap:.2f}",
            f"  Open positions ({len(self.positions)}):",
        ]
        for pair, pos in self.positions.items():
            lines.append(
                f"    {pair:15s}  qty={pos.quantity:.8f}  "
                f"entry=${pos.entry_price:.4f}  cost=${pos.cost_basis:.2f}"
            )
        return "\n".join(lines)
