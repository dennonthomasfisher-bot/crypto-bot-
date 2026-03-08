"""
Follower growth tracker – logs daily follower count and provides
growth insights to evaluate which content strategies work.

Stores data in .followers.json.
"""
from __future__ import annotations

import json
import logging
import os
import time
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

_DATA_FILE = os.path.join(os.path.dirname(__file__), ".followers.json")
_MAX_RECORDS = 365  # keep up to 1 year of daily records

_data: dict = {
    "daily": [],       # [{ "date": "YYYY-MM-DD", "count": N, "timestamp": T }]
    "last_check": 0,
}


def load() -> None:
    global _data
    if not os.path.exists(_DATA_FILE):
        return
    try:
        with open(_DATA_FILE) as f:
            _data.update(json.load(f))
        logger.info("Loaded follower data (%d days tracked)", len(_data["daily"]))
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("Could not load follower data: %s", exc)


def save() -> None:
    try:
        with open(_DATA_FILE, "w") as f:
            json.dump(_data, f, indent=2)
    except OSError as exc:
        logger.warning("Could not save follower data: %s", exc)


def record_count(client) -> dict | None:
    """
    Fetch current follower count from Twitter API and record it.
    Only records once per day.

    Returns { "count": N, "change": +/-N, "pct_change": +/-X.X } or None.
    """
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    # Already recorded today?
    if _data["daily"] and _data["daily"][-1].get("date") == today:
        return None

    try:
        me = client.get_me(user_fields=["public_metrics"])
        if not me.data:
            return None
        count = me.data.public_metrics.get("followers_count", 0)
    except Exception as exc:
        logger.warning("Failed to fetch follower count: %s", exc)
        return None

    record = {
        "date": today,
        "count": count,
        "timestamp": time.time(),
    }
    _data["daily"].append(record)
    if len(_data["daily"]) > _MAX_RECORDS:
        _data["daily"] = _data["daily"][-_MAX_RECORDS:]
    _data["last_check"] = time.time()
    save()

    # Calculate change
    change = 0
    pct = 0.0
    if len(_data["daily"]) >= 2:
        prev = _data["daily"][-2]["count"]
        change = count - prev
        pct = (change / prev * 100) if prev > 0 else 0.0

    logger.info("Follower count: %d (%+d, %+.1f%%)", count, change, pct)
    return {"count": count, "change": change, "pct_change": pct}


def get_growth_summary() -> dict:
    """
    Return growth stats: current, 7d change, 30d change, best/worst days.
    """
    records = _data["daily"]
    if not records:
        return {}

    current = records[-1]["count"]
    result = {"current": current}

    if len(records) >= 2:
        result["yesterday"] = records[-2]["count"]
        result["daily_change"] = current - records[-2]["count"]

    if len(records) >= 7:
        week_ago = records[-7]["count"]
        result["weekly_change"] = current - week_ago
        result["weekly_pct"] = (result["weekly_change"] / week_ago * 100) if week_ago > 0 else 0

    if len(records) >= 30:
        month_ago = records[-30]["count"]
        result["monthly_change"] = current - month_ago
        result["monthly_pct"] = (result["monthly_change"] / month_ago * 100) if month_ago > 0 else 0

    # Best and worst days (by absolute change)
    if len(records) >= 2:
        changes = []
        for i in range(1, len(records)):
            changes.append({
                "date": records[i]["date"],
                "change": records[i]["count"] - records[i-1]["count"],
            })
        if changes:
            result["best_day"] = max(changes, key=lambda x: x["change"])
            result["worst_day"] = min(changes, key=lambda x: x["change"])

    return result


def format_growth_log() -> str:
    """Format a human-readable growth summary for logging."""
    summary = get_growth_summary()
    if not summary:
        return "No follower data yet."

    lines = [f"Followers: {summary['current']}"]
    if "daily_change" in summary:
        lines.append(f"  Today: {summary['daily_change']:+d}")
    if "weekly_change" in summary:
        lines.append(f"  7d: {summary['weekly_change']:+d} ({summary['weekly_pct']:+.1f}%)")
    if "monthly_change" in summary:
        lines.append(f"  30d: {summary['monthly_change']:+d} ({summary['monthly_pct']:+.1f}%)")
    return "\n".join(lines)
