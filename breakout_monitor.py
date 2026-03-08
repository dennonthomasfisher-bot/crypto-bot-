"""
Breakout monitor – detects when BTC crosses key psychological/technical levels.

Checks every 5 minutes and fires alerts when price crosses significant round
numbers or historically important levels. Cooldown prevents spam.
"""
from __future__ import annotations

import logging
import time

import requests

import config
import ai_writer

logger = logging.getLogger(__name__)

# Key levels to watch (updated dynamically based on price range)
_BTC_KEY_LEVELS = [
    50000, 55000, 60000, 62000, 64000, 65000, 66000, 67000, 68000,
    69000, 70000, 72000, 75000, 80000, 85000, 90000, 95000, 100000,
    105000, 110000, 120000, 125000, 150000,
]

# ETH key levels
_ETH_KEY_LEVELS = [
    1500, 1600, 1700, 1800, 1900, 2000, 2100, 2200, 2500, 3000,
    3500, 4000, 4500, 5000,
]

# SOL key levels
_SOL_KEY_LEVELS = [
    100, 120, 130, 140, 150, 160, 170, 180, 190, 200, 225, 250, 300,
]

_LEVEL_MAP = {
    "bitcoin": _BTC_KEY_LEVELS,
    "ethereum": _ETH_KEY_LEVELS,
    "solana": _SOL_KEY_LEVELS,
}

_SYMBOL_MAP = {
    "bitcoin": "BTC",
    "ethereum": "ETH",
    "solana": "SOL",
}

# Track last known price and cooldowns
_last_prices: dict[str, float] = {}
_level_cooldowns: dict[str, float] = {}  # "coin:level" -> timestamp


def _fetch_current_prices() -> dict[str, float]:
    """Fetch current prices for BTC, ETH, SOL."""
    try:
        resp = requests.get(
            f"{config.COINGECKO_BASE}/simple/price",
            params={
                "ids": "bitcoin,ethereum,solana",
                "vs_currencies": "usd",
            },
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()
        return {
            coin: data[coin]["usd"]
            for coin in data
            if "usd" in data[coin]
        }
    except (requests.RequestException, KeyError) as exc:
        logger.warning("Breakout price fetch failed: %s", exc)
        return {}


def _cooldown_ok(coin_id: str, level: int) -> bool:
    """Check if a level alert is off cooldown."""
    key = f"{coin_id}:{level}"
    last = _level_cooldowns.get(key, 0)
    return (time.time() - last) >= config.BREAKOUT_COOLDOWN


def _record_level(coin_id: str, level: int) -> None:
    _level_cooldowns[f"{coin_id}:{level}"] = time.time()


def check_breakouts() -> list[dict]:
    """
    Check if any tracked coins have crossed key levels since last check.
    Returns list of breakout alert dicts.
    """
    prices = _fetch_current_prices()
    if not prices:
        return []

    alerts = []

    for coin_id, current_price in prices.items():
        levels = _LEVEL_MAP.get(coin_id, [])
        symbol = _SYMBOL_MAP.get(coin_id, coin_id.upper())
        last_price = _last_prices.get(coin_id)

        if last_price is None:
            _last_prices[coin_id] = current_price
            continue

        for level in levels:
            # Check if price crossed this level (in either direction)
            crossed_up = last_price < level <= current_price
            crossed_down = last_price > level >= current_price

            if (crossed_up or crossed_down) and _cooldown_ok(coin_id, level):
                direction = "above" if crossed_up else "below"
                alerts.append({
                    "coin_id": coin_id,
                    "symbol": symbol,
                    "level": level,
                    "current_price": current_price,
                    "direction": direction,
                    "crossed_up": crossed_up,
                })
                _record_level(coin_id, level)

        _last_prices[coin_id] = current_price

    return alerts


def format_breakout_tweet(alert: dict) -> str | None:
    """Generate a tweet about a key level breakout."""
    symbol = alert["symbol"]
    level = alert["level"]
    price = alert["current_price"]
    direction = alert["direction"]
    crossed_up = alert["crossed_up"]

    emoji = "🟢" if crossed_up else "🔴"
    action = "broke above" if crossed_up else "dropped below"

    # Format level nicely
    if level >= 1000:
        level_str = f"${level:,.0f}"
    else:
        level_str = f"${level}"

    price_str = f"${price:,.0f}" if price >= 1000 else f"${price:,.2f}"

    if ai_writer.is_available():
        prompt = f"""Write a BREAKING tweet — {symbol} just {action} {level_str}.

Current price: {price_str}
Direction: {'bullish breakout' if crossed_up else 'bearish breakdown'}
Key level: {level_str}

The tweet should:
- Lead with the level break — this is urgent, time-sensitive
- Say what the next level to watch is (pick a reasonable one)
- Sound like a trader calling it out in real-time
- Short and punchy — under 250 characters
- NO hashtags
{ai_writer._get_recent_context()}
Write the tweet now. Nothing else."""

        system = """You are @CoinWatchAlert. When price breaks a key level, you're the first to call it. Sharp, urgent, data-driven. No hashtags."""

        tweet = ai_writer._call_claude(system, prompt)
        if tweet and len(tweet) <= 280:
            return tweet

    # Template fallback
    next_levels = {
        True: [l for l in _LEVEL_MAP.get(alert["coin_id"], []) if l > level],
        False: [l for l in _LEVEL_MAP.get(alert["coin_id"], []) if l < level],
    }
    next_list = next_levels[crossed_up]
    next_level = next_list[0] if next_list else None

    lines = [
        f"{emoji} {symbol} just {action} {level_str}",
        f"",
        f"Currently at {price_str}",
    ]

    if next_level:
        next_str = f"${next_level:,.0f}" if next_level >= 1000 else f"${next_level}"
        watching = "resistance" if crossed_up else "support"
        lines.append(f"Next {watching}: {next_str}")

    return "\n".join(lines)
