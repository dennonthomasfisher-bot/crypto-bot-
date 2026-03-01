"""
strategies/dca.py – Dollar-Cost Averaging (DCA) strategy.

Tracks per-pair buy timestamps and returns a positive signal whenever the
configured interval has elapsed since the last DCA purchase.

This module does *not* place orders directly; it only reports whether a DCA
buy is due.  The bot main loop is responsible for executing the buy and then
calling `record_buy()` to reset the timer.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)


class DCAStrategy:
    """
    Per-pair interval tracker for Dollar-Cost Averaging.

    Parameters
    ----------
    interval_hours : Hours between automatic DCA buys (default 24).
    dca_pairs      : List of instrument names eligible for DCA.
    """

    def __init__(self, interval_hours: int = 24, dca_pairs: Optional[List[str]] = None) -> None:
        self.interval_hours = interval_hours
        self.dca_pairs: List[str] = dca_pairs or []
        self._last_buy: Dict[str, datetime] = {}

    def is_due(self, pair: str) -> bool:
        """Return True if a DCA buy is due for *pair*."""
        if pair not in self.dca_pairs:
            return False

        last = self._last_buy.get(pair)
        if last is None:
            logger.debug("DCA: %s has never been bought – marking as due", pair)
            return True

        elapsed_hours = (datetime.now(tz=timezone.utc) - last).total_seconds() / 3600
        due = elapsed_hours >= self.interval_hours
        logger.debug(
            "DCA: %s  elapsed=%.1fh  interval=%dh  due=%s",
            pair, elapsed_hours, self.interval_hours, due,
        )
        return due

    def signal(self, pair: str) -> float:
        """
        Return a positive signal weight when a DCA buy is due.

        The value 0.5 (half of the maximum +1.0) reflects that DCA is a
        *systematic* strategy — it should contribute to the buy score but
        not dominate discretionary signals.

        Returns
        -------
        +0.5  DCA buy is due for this pair
         0.0  not yet due, or pair not in DCA list
        """
        return 0.5 if self.is_due(pair) else 0.0

    def record_buy(self, pair: str) -> None:
        """Reset the DCA timer for *pair* after a successful buy."""
        self._last_buy[pair] = datetime.now(tz=timezone.utc)
        logger.debug("DCA: recorded buy for %s at %s", pair, self._last_buy[pair].isoformat())

    def hours_until_next(self, pair: str) -> float:
        """Return the number of hours remaining until the next DCA buy is due."""
        last = self._last_buy.get(pair)
        if last is None:
            return 0.0
        elapsed = (datetime.now(tz=timezone.utc) - last).total_seconds() / 3600
        return max(0.0, self.interval_hours - elapsed)
