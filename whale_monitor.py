"""
Whale monitor – tracks large crypto transactions using free public APIs.

Uses Blockchain.com and Etherscan-like APIs to detect large transfers
that could signal institutional moves.

Also uses Whale Alert-style data from free endpoints.
"""
from __future__ import annotations

import logging
import time

import requests

import ai_writer

logger = logging.getLogger(__name__)

# Cooldown to avoid tweeting about same whale activity
_recent_alerts: dict[str, float] = {}
_WHALE_COOLDOWN = 7200  # 2 hours


def _cooldown_ok(alert_key: str) -> bool:
    last = _recent_alerts.get(alert_key, 0)
    return (time.time() - last) >= _WHALE_COOLDOWN


def _record_alert(alert_key: str) -> None:
    _recent_alerts[alert_key] = time.time()
    # Prune
    cutoff = time.time() - _WHALE_COOLDOWN * 2
    expired = [k for k, v in _recent_alerts.items() if v < cutoff]
    for k in expired:
        del _recent_alerts[k]


def fetch_btc_whale_txns() -> list[dict]:
    """
    Fetch large BTC transactions from Blockchain.com API (free, no key).
    Looks at recent unconfirmed transactions over $10M.
    """
    try:
        # Use Blockchair API (free tier, no key for basic queries)
        resp = requests.get(
            "https://api.blockchair.com/bitcoin/transactions",
            params={
                "s": "output_total(desc)",
                "limit": 10,
            },
            timeout=15,
        )
        if resp.status_code != 200:
            return []
        data = resp.json()
        txns = data.get("data", [])

        whales = []
        for tx in txns:
            value_usd = tx.get("output_total_usd", 0) or 0
            if value_usd >= 10_000_000:  # $10M+
                value_btc = tx.get("output_total", 0) / 1e8  # satoshis to BTC
                alert_key = f"btc_whale_{tx.get('hash', '')[:16]}"
                if not _cooldown_ok(alert_key):
                    continue
                whales.append({
                    "chain": "Bitcoin",
                    "symbol": "BTC",
                    "value_usd": value_usd,
                    "value_native": value_btc,
                    "tx_hash": tx.get("hash", ""),
                    "alert_key": alert_key,
                })
        return whales[:3]

    except requests.RequestException as exc:
        logger.debug("BTC whale fetch failed: %s", exc)
        return []


def fetch_eth_whale_txns() -> list[dict]:
    """
    Fetch large ETH transfers from Etherscan-compatible free API.
    """
    try:
        # Use Blockchair for ETH too
        resp = requests.get(
            "https://api.blockchair.com/ethereum/transactions",
            params={
                "s": "value(desc)",
                "limit": 10,
            },
            timeout=15,
        )
        if resp.status_code != 200:
            return []
        data = resp.json()
        txns = data.get("data", [])

        whales = []
        for tx in txns:
            value_usd = tx.get("value_usd", 0) or 0
            if value_usd >= 5_000_000:  # $5M+
                value_eth = tx.get("value", 0) / 1e18
                alert_key = f"eth_whale_{tx.get('hash', '')[:16]}"
                if not _cooldown_ok(alert_key):
                    continue
                whales.append({
                    "chain": "Ethereum",
                    "symbol": "ETH",
                    "value_usd": value_usd,
                    "value_native": value_eth,
                    "tx_hash": tx.get("hash", ""),
                    "alert_key": alert_key,
                })
        return whales[:3]

    except requests.RequestException as exc:
        logger.debug("ETH whale fetch failed: %s", exc)
        return []


def fetch_exchange_flows() -> dict | None:
    """
    Fetch exchange netflow data from CryptoQuant-style free endpoints.
    Uses CoinGlass as a proxy for exchange flow data.
    """
    try:
        resp = requests.get(
            "https://open-api.coinglass.com/public/v2/index/bitcoin-profitable-days",
            timeout=15,
        )
        # This endpoint may not have exactly what we want,
        # but we try to get exchange flow signals
        if resp.status_code == 200:
            data = resp.json()
            return data.get("data")
    except requests.RequestException:
        pass
    return None


def check_whale_activity() -> list[dict]:
    """
    Main entry point — check for whale transactions.
    Returns list of tweetable whale alerts.
    """
    alerts = []

    btc_whales = fetch_btc_whale_txns()
    alerts.extend(btc_whales)

    eth_whales = fetch_eth_whale_txns()
    alerts.extend(eth_whales)

    # Sort by value
    alerts.sort(key=lambda a: a.get("value_usd", 0), reverse=True)
    return alerts[:2]  # max 2 per check


def format_whale_tweet(alert: dict) -> str | None:
    """Format a whale alert into a tweet."""
    _record_alert(alert["alert_key"])

    symbol = alert["symbol"]
    value_usd = alert["value_usd"]
    value_native = alert.get("value_native", 0)
    chain = alert["chain"]

    usd_str = f"${value_usd / 1e6:.0f}M" if value_usd >= 1e6 else f"${value_usd:,.0f}"
    native_str = f"{value_native:,.0f}" if value_native >= 1 else f"{value_native:,.2f}"

    if ai_writer.is_available():
        prompt = f"""Write a tweet about a large whale transaction detected.

Chain: {chain}
Amount: {native_str} {symbol} (~{usd_str})

Speculate on what this could mean — exchange deposit (sell pressure),
exchange withdrawal (accumulation), or OTC deal.
Keep it under 275 chars. NO hashtags. Sound like a trader who watches on-chain.

Write the tweet now. Nothing else."""
        system = "You are @CoinWatchAlert. You spot whale moves before CT does. Data-driven, no hype."
        ai_tweet = ai_writer._call_claude(system, prompt)
        if ai_tweet and len(ai_tweet) <= 280:
            return ai_tweet

    # Template fallback
    return (
        f"Whale alert: {native_str} {symbol} ({usd_str}) just moved on {chain}\n\n"
        f"Large transfers like this often precede volatility. Watching."
    )
