"""
risk_manager.py – Position sizing, capital tracking, stop-loss and take-profit.

All monetary limits are configurable via environment variables (see .env.example).
"""
from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
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

    1. Total capital    – hard ceiling on total USD committed across all positions.
    2. Max per trade    – single-order size ceiling in USD.
    3. Max position pct – minimum order floor as a percentage of total capital.
    4. Stop-loss        – auto-exit when price falls below `entry × (1 − sl_pct)`.
    5. Take-profit      – auto-exit when price rises above `entry × (1 + tp_pct)`.
    """

    def __init__(
        self,
        total_capital: float,
        max_per_trade: float,
        max_position_pct: float,
        stop_loss_pct: float,
        take_profit_pct: float,
    ) -> None:
        self.total_capital = total_capital
        self.max_per_trade = max_per_trade
        self.max_position_pct = max_position_pct
        self.stop_loss_pct = stop_loss_pct
        self.take_profit_pct = take_profit_pct

        self.positions: Dict[str, Position] = {}

    # ── Capital tracking ──────────────────────────────────────────────────────

    def total_committed(self) -> float:
        """Return total USD currently locked in open positions."""
        return sum(p.cost_basis for p in self.positions.values())

    def available_capital(self) -> float:
        """Return how much USD can still be deployed."""
        return max(0.0, self.total_capital - self.total_committed())

    # ── Position sizing ───────────────────────────────────────────────────────

    def calculate_order_size(self) -> float:
        """
        Compute the USD amount to spend on a single buy, respecting:

        * `max_per_trade`    – absolute per-trade ceiling
        * `max_position_pct` – minimum viable order floor (total_capital × pct)
        * available capital  – never exceed uncommitted funds

        Returns 0.0 if available capital is below the minimum viable order size.
        """
        available = self.available_capital()
        min_order = self.total_capital * self.max_position_pct
        if available < min_order:
            return 0.0
        return min(self.max_per_trade, available)

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

    def close_position(self, pair: str) -> Optional[Position]:
        """Remove and return the position for `pair`, or None if not held."""
        pos = self.positions.pop(pair, None)
        if pos:
            logger.info("Closed position %s (qty=%.8f  cost=%.2f)", pair, pos.quantity, pos.cost_basis)
        return pos

    def has_position(self, pair: str) -> bool:
        """Return True if the bot currently holds `pair`."""
        return pair in self.positions

    # ── Persistence ───────────────────────────────────────────────────────────

    def save_positions(self, path: str) -> None:
        """Persist open positions to a JSON file so restarts don't wipe state."""
        data = {
            pair: {
                "entry_price": pos.entry_price,
                "quantity": pos.quantity,
                "cost_basis": pos.cost_basis,
            }
            for pair, pos in self.positions.items()
        }
        tmp = path + ".tmp"
        with open(tmp, "w") as f:
            json.dump(data, f, indent=2)
        os.replace(tmp, path)
        logger.debug("Positions saved to %s (%d open)", path, len(data))

    def load_positions(self, path: str) -> int:
        """Reload positions from a JSON file written by save_positions.

        Returns the number of positions restored.
        """
        if not os.path.exists(path):
            return 0
        try:
            with open(path) as f:
                data = json.load(f)
            for pair, d in data.items():
                self.positions[pair] = Position(
                    pair=pair,
                    entry_price=float(d["entry_price"]),
                    quantity=float(d["quantity"]),
                    cost_basis=float(d["cost_basis"]),
                )
            logger.info("Restored %d position(s) from %s", len(data), path)
            return len(data)
        except Exception as exc:
            logger.warning("Could not load positions from %s: %s", path, exc)
            return 0

    # ── Summary ───────────────────────────────────────────────────────────────

    def summary(self) -> str:
        lines = [
            f"  Capital     : ${self.total_committed():.2f} used / ${self.total_capital:.2f} total"
            f"  (${self.available_capital():.2f} available)",
            f"  Open positions ({len(self.positions)}):",
        ]
        for pair, pos in self.positions.items():
            lines.append(
                f"    {pair:15s}  qty={pos.quantity:.8f}  "
                f"entry=${pos.entry_price:.4f}  cost=${pos.cost_basis:.2f}"
            )
        return "\n".join(lines)
