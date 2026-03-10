"""
state.py – Persistent bot state (monthly tweet counter).

Tracks monthly tweet count against the 1,500/month Twitter free-tier cap.
State is persisted to .bot_state.json so restarts don't reset the counter.
"""

import datetime
import json
import logging
import os

logger = logging.getLogger(__name__)

_STATE_FILE = os.path.join(os.path.dirname(__file__), ".bot_state.json")
MONTHLY_TWEET_CAP = 1500

_state: dict = {}


def _load() -> None:
    global _state
    try:
        with open(_STATE_FILE) as f:
            _state = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        _state = {}


def _save() -> None:
    try:
        with open(_STATE_FILE, "w") as f:
            json.dump(_state, f)
    except OSError as exc:
        logger.warning("Could not save bot state: %s", exc)


def _month_key() -> str:
    return datetime.date.today().strftime("%Y-%m")


def can_tweet() -> bool:
    """Return True if we're still under the 1,500 tweet/month cap."""
    _load()
    count = _state.get(_month_key(), 0)
    return count < MONTHLY_TWEET_CAP


def record_tweet() -> None:
    """Increment the monthly tweet counter and persist."""
    _load()
    key = _month_key()
    _state[key] = _state.get(key, 0) + 1
    _save()
    logger.debug("Tweet recorded. Monthly total: %d", _state[key])
