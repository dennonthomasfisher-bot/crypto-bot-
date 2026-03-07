"""
Polymarket monitor – scans crypto prediction markets for significant
odds movements and generates daily summary tweets.

Polls the Polymarket API for crypto-related markets, tracking odds
changes and flagging significant moves.
"""
from __future__ import annotations

import time
import logging
import requests

import config
import state

logger = logging.getLogger(__name__)

_POLYMARKET_API = "https://clob.polymarket.com"

# In-memory cache of last known odds for change detection
_last_odds: dict[str, float] = {}


def _fetch_crypto_markets() -> list[dict]:
    """
    Fetch active crypto-related prediction markets from Polymarket.
    Returns list of market dicts or [] on error.
    """
    for attempt in range(3):
        try:
            resp = requests.get(
                f"{_POLYMARKET_API}/markets",
                params={"limit": 50, "active": True},
                timeout=15,
            )
            if resp.status_code == 429:
                wait = 2 ** (attempt + 1)
                logger.warning("Polymarket rate limited, retrying in %ds…", wait)
                time.sleep(wait)
                continue
            resp.raise_for_status()
            markets = resp.json()

            # Filter for crypto-related markets
            crypto_keywords = [
                "bitcoin", "btc", "ethereum", "eth", "crypto",
                "solana", "sol", "xrp", "defi", "nft",
            ]
            crypto_markets = []
            for m in markets if isinstance(markets, list) else markets.get("data", []):
                question = (m.get("question", "") + m.get("description", "")).lower()
                if any(kw in question for kw in crypto_keywords):
                    crypto_markets.append(m)

            return crypto_markets[:config.POLYMARKET_MAX_MARKETS]

        except (ConnectionResetError, ConnectionAbortedError) as exc:
            wait = 2 ** (attempt + 1)
            logger.error(
                "Polymarket connection error: %s. Retrying in %ds… (%d/%d)",
                exc, wait, attempt + 1, 3,
            )
            time.sleep(wait)

        except requests.RequestException as exc:
            logger.warning(
                "Polymarket fetch failed (attempt %d/3): %s",
                attempt + 1, exc,
            )
            if attempt < 2:
                time.sleep(2 ** (attempt + 1))

    return []


def scan_markets() -> list[dict]:
    """
    Scan markets for significant odds movements.

    Returns list of alert dicts with keys:
        question, odds, prev_odds, change_pct, market_id
    """
    global _last_odds

    markets = _fetch_crypto_markets()
    if not markets:
        return []

    alerts = []
    for market in markets:
        market_id = market.get("condition_id", market.get("id", ""))
        question = market.get("question", "Unknown market")

        # Try to get current odds/probability
        odds = None
        for field in ("outcomePrices", "outcome_prices", "best_bid", "last_price"):
            val = market.get(field)
            if val is not None:
                if isinstance(val, list) and val:
                    odds = float(val[0])
                elif isinstance(val, (int, float)):
                    odds = float(val)
                break

        if odds is None:
            continue

        prev = _last_odds.get(market_id)
        _last_odds[market_id] = odds

        if prev is not None:
            change = abs(odds - prev) * 100
            if change >= config.POLYMARKET_ALERT_THRESHOLD_PCT:
                alerts.append({
                    "market_id": market_id,
                    "question": question,
                    "odds": odds,
                    "prev_odds": prev,
                    "change_pct": change,
                })

    logger.info(
        "Polymarket scan: %d markets, %d alerts",
        len(markets), len(alerts),
    )
    return alerts


def format_polymarket_alert(alert: dict) -> str:
    """Format a single market move alert as a tweet."""
    direction = "📈" if alert["odds"] > alert["prev_odds"] else "📉"
    question = alert["question"]
    if len(question) > 130:
        question = question[:127] + "..."

    change_word = "surged" if alert["odds"] > alert["prev_odds"] else "dropped"

    return (
        f"{direction} Prediction market alert\n"
        f"\n"
        f"{question}\n"
        f"\n"
        f"Odds {change_word}: {alert['prev_odds']:.0%} → {alert['odds']:.0%} "
        f"({alert['change_pct']:+.1f}pp)\n"
        f"\n"
        f"Source: Polymarket"
    )


def format_daily_summary() -> str | None:
    """
    Generate a daily summary tweet of top crypto prediction markets.
    Returns None if no markets available.
    """
    markets = _fetch_crypto_markets()
    if not markets:
        return None

    lines = [
        "Crypto Prediction Markets",
        "",
    ]
    count = 0
    for m in markets[:5]:
        question = m.get("question", "?")
        if len(question) > 55:
            question = question[:52] + "..."

        odds = None
        for field in ("outcomePrices", "outcome_prices", "best_bid", "last_price"):
            val = m.get(field)
            if val is not None:
                if isinstance(val, list) and val:
                    odds = float(val[0])
                elif isinstance(val, (int, float)):
                    odds = float(val)
                break

        if odds is not None:
            bar = "▓" * int(odds * 10) + "░" * (10 - int(odds * 10))
            lines.append(f"{question}")
            lines.append(f"{bar} {odds:.0%}")
            lines.append("")
            count += 1

    if count == 0:
        return None

    lines.append(f"Source: Polymarket")

    tweet = "\n".join(lines)
    if len(tweet) > 280:
        # Trim last market entry to fit
        while len(tweet) > 280 and count > 1:
            lines = lines[:-(4)]  # Remove last market (question + bar + blank)
            count -= 1
            tweet = "\n".join(lines)
    return tweet
