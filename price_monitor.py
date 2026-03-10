"""
Price monitor – polls CoinGecko (free, no API key required) and returns
alert objects whenever a coin crosses the configured move thresholds.
"""

import time
import logging
import requests

import config

logger = logging.getLogger(__name__)

# Track the last time we fired an alert for each coin so we don't spam.
# Structure: { coin_id: { "1h": timestamp, "24h": timestamp } }
_last_alert: dict[str, dict[str, float]] = {}


def _fetch_prices() -> dict | None:
    """
    Fetch current price + % change for all tracked coins in a single request.
    Returns raw CoinGecko market data list, or None on error.
    """
    coin_ids = ",".join(config.COINS.keys())
    url = f"{config.COINGECKO_BASE}/coins/markets"
    params = {
        "vs_currency": "usd",
        "ids": coin_ids,
        "price_change_percentage": "1h,24h",
        "per_page": len(config.COINS),
        "page": 1,
    }
    try:
        resp = requests.get(url, params=params, timeout=15)
        resp.raise_for_status()
        return resp.json()
    except requests.RequestException as exc:
        logger.warning("CoinGecko fetch failed: %s", exc)
        return None


def _cooldown_ok(coin_id: str, window: str) -> bool:
    """Return True if we haven't alerted for this coin+window recently."""
    now = time.time()
    last = _last_alert.get(coin_id, {}).get(window, 0)
    return (now - last) >= config.PRICE_ALERT_COOLDOWN


def _record_alert(coin_id: str, window: str) -> None:
    _last_alert.setdefault(coin_id, {})[window] = time.time()


def check_prices() -> list[dict]:
    """
    Poll CoinGecko and return a list of alert dicts for coins that have
    moved beyond the configured thresholds.

    Each alert dict has the keys:
        coin_id, symbol, price_usd, pct_change, window, direction
    """
    data = _fetch_prices()
    if not data:
        return []

    alerts = []
    for coin in data:
        coin_id = coin["id"]
        symbol  = config.COINS.get(coin_id, coin["symbol"].upper())
        price   = coin.get("current_price", 0)

        pct_1h  = coin.get("price_change_percentage_1h_in_currency")
        pct_24h = coin.get("price_change_percentage_24h_in_currency")

        if pct_1h is not None and abs(pct_1h) >= config.PRICE_ALERT_1H_PCT:
            if _cooldown_ok(coin_id, "1h"):
                alerts.append({
                    "coin_id":    coin_id,
                    "symbol":     symbol,
                    "price_usd":  price,
                    "pct_change": pct_1h,
                    "window":     "1h",
                    "direction":  "up" if pct_1h > 0 else "down",
                })
                _record_alert(coin_id, "1h")

        if pct_24h is not None and abs(pct_24h) >= config.PRICE_ALERT_24H_PCT:
            if _cooldown_ok(coin_id, "24h"):
                alerts.append({
                    "coin_id":    coin_id,
                    "symbol":     symbol,
                    "price_usd":  price,
                    "pct_change": pct_24h,
                    "window":     "24h",
                    "direction":  "up" if pct_24h > 0 else "down",
                })
                _record_alert(coin_id, "24h")

    return alerts


def _format_price(price: float) -> str:
    """Format a price with appropriate decimal places based on magnitude."""
    if price >= 1000:
        return f"${price:,.2f}"
    if price >= 1:
        return f"${price:.2f}"
    if price >= 0.01:
        return f"${price:.4f}"
    if price >= 0.0001:
        return f"${price:.6f}"
    return f"${price:.8f}"


def format_price_tweet(alert: dict) -> str:
    """Turn a price alert dict into a ready-to-post tweet string."""
    arrow     = "🚀" if alert["direction"] == "up" else "🔴"
    sign      = "+" if alert["pct_change"] > 0 else ""
    pct_str   = f"{sign}{alert['pct_change']:.1f}%"
    price_str = _format_price(alert["price_usd"])
    window    = alert["window"]

    return (
        f"{arrow} #{alert['symbol']} just moved {pct_str} in {window}!\n"
        f"Current price: {price_str}\n"
        f"#Crypto #Bitcoin #Cryptocurrency"
    )
