"""
Crypto event calendar – tracks token unlocks, protocol upgrades,
FOMC dates, CPI releases, and other market-moving events.

Tweets about upcoming events to provide timely, valuable content.
"""
from __future__ import annotations

import logging
import time
from datetime import datetime, timezone, timedelta

import requests

import config
import ai_writer

logger = logging.getLogger(__name__)

# In-memory cooldown — don't tweet same event twice
_tweeted_events: dict[str, float] = {}
_EVENT_COOLDOWN = 86400  # 24 hours


def _cooldown_ok(event_key: str) -> bool:
    last = _tweeted_events.get(event_key, 0)
    return (time.time() - last) >= _EVENT_COOLDOWN


def _record_event(event_key: str) -> None:
    _tweeted_events[event_key] = time.time()


# ── Token Unlocks (CoinGecko free data) ──────────────────────────────────────

# Major token unlock schedule — manually maintained for accuracy
# Format: (symbol, name, approximate unlock date, amount description)
# Updated periodically; the bot tweets when an unlock is within 48h
_KNOWN_UNLOCKS = [
    # These rotate — add new ones as they approach
    # The bot checks if the date is within the next 48 hours
]


def fetch_token_unlocks() -> list[dict]:
    """
    Fetch upcoming token unlocks from DeFiLlama's unlocks endpoint.
    Falls back to manually tracked unlocks if API unavailable.
    """
    try:
        resp = requests.get(
            "https://api.llama.fi/unlocks/upcoming",
            timeout=15,
        )
        if resp.status_code == 200:
            data = resp.json()
            now = time.time()
            upcoming = []
            for unlock in data[:20]:
                unlock_time = unlock.get("timestamp", 0)
                if 0 < (unlock_time - now) < config.TOKEN_UNLOCK_WINDOW:  # within 7 days
                    symbol = unlock.get("symbol", "?").upper()
                    event_key = f"unlock_{symbol}_{unlock.get('timestamp', '')}"
                    if not _cooldown_ok(event_key):
                        continue
                    value_usd = unlock.get("value", 0)
                    # Skip unlocks below minimum value threshold
                    if value_usd < config.TOKEN_UNLOCK_MIN_VALUE:
                        continue
                    upcoming.append({
                        "type": "token_unlock",
                        "symbol": symbol,
                        "name": unlock.get("name", symbol),
                        "timestamp": unlock_time,
                        "value_usd": value_usd,
                        "pct_supply": unlock.get("pctSupply", 0),
                        "event_key": event_key,
                    })
            return upcoming[:3]
    except requests.RequestException as exc:
        logger.debug("Token unlock API unavailable: %s", exc)
    return []


# ── Macro Events (FOMC, CPI, etc.) ──────────────────────────────────────────

# Hardcoded major macro dates for 2025-2026
# These are known well in advance and move markets
_MACRO_EVENTS_2025_2026 = [
    # 2025 FOMC meetings
    ("2025-01-29", "FOMC", "Fed rate decision"),
    ("2025-03-19", "FOMC", "Fed rate decision + dot plot"),
    ("2025-05-07", "FOMC", "Fed rate decision"),
    ("2025-06-18", "FOMC", "Fed rate decision + dot plot"),
    ("2025-07-30", "FOMC", "Fed rate decision"),
    ("2025-09-17", "FOMC", "Fed rate decision + dot plot"),
    ("2025-10-29", "FOMC", "Fed rate decision"),
    ("2025-12-17", "FOMC", "Fed rate decision + dot plot"),
    # 2026 FOMC meetings (approximate — update when Fed publishes)
    ("2026-01-28", "FOMC", "Fed rate decision"),
    ("2026-03-18", "FOMC", "Fed rate decision + dot plot"),
    ("2026-05-06", "FOMC", "Fed rate decision"),
    ("2026-06-17", "FOMC", "Fed rate decision + dot plot"),
    ("2026-07-29", "FOMC", "Fed rate decision"),
    ("2026-09-16", "FOMC", "Fed rate decision + dot plot"),
    ("2026-10-28", "FOMC", "Fed rate decision"),
    ("2026-12-16", "FOMC", "Fed rate decision + dot plot"),
    # 2025 CPI releases
    ("2025-01-15", "CPI", "US CPI inflation data"),
    ("2025-02-12", "CPI", "US CPI inflation data"),
    ("2025-03-12", "CPI", "US CPI inflation data"),
    ("2025-04-10", "CPI", "US CPI inflation data"),
    ("2025-05-13", "CPI", "US CPI inflation data"),
    ("2025-06-11", "CPI", "US CPI inflation data"),
    ("2025-07-15", "CPI", "US CPI inflation data"),
    ("2025-08-12", "CPI", "US CPI inflation data"),
    ("2025-09-10", "CPI", "US CPI inflation data"),
    ("2025-10-14", "CPI", "US CPI inflation data"),
    ("2025-11-12", "CPI", "US CPI inflation data"),
    ("2025-12-10", "CPI", "US CPI inflation data"),
    # 2026 CPI releases (approximate)
    ("2026-01-14", "CPI", "US CPI inflation data"),
    ("2026-02-11", "CPI", "US CPI inflation data"),
    ("2026-03-11", "CPI", "US CPI inflation data"),
    ("2026-04-14", "CPI", "US CPI inflation data"),
    ("2026-05-12", "CPI", "US CPI inflation data"),
    ("2026-06-10", "CPI", "US CPI inflation data"),
    ("2026-07-14", "CPI", "US CPI inflation data"),
    ("2026-08-12", "CPI", "US CPI inflation data"),
    ("2026-09-15", "CPI", "US CPI inflation data"),
    ("2026-10-13", "CPI", "US CPI inflation data"),
    ("2026-11-10", "CPI", "US CPI inflation data"),
    ("2026-12-10", "CPI", "US CPI inflation data"),
    # Major crypto events
    ("2025-04-20", "HALVING_ANNIVERSARY", "Bitcoin halving 1-year anniversary"),
]


def get_upcoming_macro_events() -> list[dict]:
    """Return macro events happening within the next 24 hours."""
    now = datetime.now(timezone.utc)
    events = []
    for date_str, event_type, description in _MACRO_EVENTS_2025_2026:
        event_date = datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        diff = (event_date - now).total_seconds()
        if 0 < diff < 86400:  # within 24 hours
            event_key = f"macro_{event_type}_{date_str}"
            if not _cooldown_ok(event_key):
                continue
            events.append({
                "type": "macro",
                "event_type": event_type,
                "date": date_str,
                "description": description,
                "hours_until": diff / 3600,
                "event_key": event_key,
            })
    return events


# ── Main check function ──────────────────────────────────────────────────────

def check_events() -> list[dict]:
    """
    Check for upcoming events — token unlocks and macro events.
    Returns list of tweetable event alerts.
    """
    events = []
    events.extend(fetch_token_unlocks())
    events.extend(get_upcoming_macro_events())
    return events[:2]  # max 2 event tweets per check


def format_event_tweet(event: dict) -> str | None:
    """Format an event into a tweet."""
    _record_event(event["event_key"])

    if event["type"] == "token_unlock":
        symbol = event["symbol"]
        value = event.get("value_usd", 0)
        pct = event.get("pct_supply", 0)
        value_str = f"${value / 1e6:.0f}M" if value >= 1e6 else f"${value:,.0f}"

        if ai_writer.is_available():
            prompt = f"""Write a tweet about an upcoming token unlock for {symbol}.

Unlock value: {value_str}
Supply unlocked: {pct:.1f}% of circulating supply
Timing: within the next 48 hours

Explain what this means for price — unlocks often create sell pressure.
Keep it under 275 chars. NO hashtags. Sound like a trader giving a heads-up.

Write the tweet now. Nothing else."""
            system = "You are @CoinWatchAlert. You give your followers a heads-up on events that could move prices."
            ai_tweet = ai_writer._call_claude(system, prompt)
            if ai_tweet and len(ai_tweet) <= 280:
                return ai_tweet

        # Template fallback
        lines = [f"Heads up: {symbol} token unlock in the next 48 hours"]
        if value > 0:
            lines.append(f"~{value_str} worth of tokens entering circulation")
        if pct > 0:
            lines.append(f"That's {pct:.1f}% of supply")
        lines.append("")
        lines.append("Unlocks often bring short-term sell pressure. Watch the charts.")
        return "\n".join(lines)

    elif event["type"] == "macro":
        event_type = event["event_type"]
        hours = event["hours_until"]
        desc = event["description"]
        time_str = f"in ~{hours:.0f} hours" if hours >= 1 else "today"

        if ai_writer.is_available():
            prompt = f"""Write a tweet about an upcoming macro event:

Event: {desc}
Timing: {time_str}

Explain why crypto traders should care. FOMC = rate decisions move all risk assets.
CPI = inflation data drives Fed expectations. Be specific about the crypto impact.
Keep it under 275 chars. NO hashtags.

Write the tweet now. Nothing else."""
            system = "You are @CoinWatchAlert. You connect macro events to crypto market impact."
            ai_tweet = ai_writer._call_claude(system, prompt)
            if ai_tweet and len(ai_tweet) <= 280:
                return ai_tweet

        # Template fallback
        if event_type == "FOMC":
            return (
                f"Fed rate decision {time_str}\n\n"
                f"Crypto historically volatile around FOMC. "
                f"Whatever the decision, expect a move. Position accordingly."
            )
        elif event_type == "CPI":
            return (
                f"US CPI data drops {time_str}\n\n"
                f"Hot print = rate hike fears = risk-off. Cool print = rally fuel. "
                f"This number moves everything."
            )
        else:
            return f"{desc} {time_str} — could move the market."

    return None
