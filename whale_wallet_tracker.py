"""
Whale wallet tracker – monitors known whale wallets for large movements
using Etherscan's free API (ETH) and public blockchain APIs.

Checks every 15 minutes. Tweets when a whale moves >$1M in a single
transaction, including exchange flow direction (buy/sell signal).
Capped at 4 whale wallet alerts per day.
"""
from __future__ import annotations

import datetime
import logging
import time

import requests

import config
import ai_writer

logger = logging.getLogger(__name__)

# ── Known whale wallets ─────────────────────────────────────────────────────
# Format: (label, address)
# Sources: Arkham Intelligence, Etherscan labels, on-chain research

ETH_WHALE_WALLETS = [
    ("Ethereum Foundation", "0xde0b295669a9fd93d5f28d9ec85e40f4cb697bae"),
    ("Justin Sun", "0x3ddfa8ec3052539b6c9549f12cea2c295cff5296"),
    ("Vitalik Buterin", "0xd8da6bf26964af9d7eed9e03e53415d37aa96045"),
    ("Wintermute", "0x0000006daea1723962647b7e189d311d757fb793"),
    ("Jump Trading", "0xf584f8728b874a6a5c7a8d4d387c9aae9172d621"),
    ("Galaxy Digital", "0x7a9d69e5a2bce1adef49735f58d6b7300688f927"),
    ("Binance Cold Wallet", "0xbe0eb53f46cd790cd13851d5eff43d12404d33e8"),
    ("Coinbase Cold", "0xa9d1e08c7793af67e9d92fe308d5697fb81d3e43"),
    ("Kraken Hot Wallet", "0x2910543af39aba0cd09dbb2d50200b3e800a63d2"),
    ("Bitfinex Hot", "0x1151314c646ce4e0efd76d1af4760ae66a9fe30f"),
]

# Known exchange deposit addresses (to detect exchange inflow = sell signal)
_EXCHANGE_ADDRESSES = {
    # Binance
    "0xbe0eb53f46cd790cd13851d5eff43d12404d33e8",
    "0x28c6c06298d514db089934071355e5743bf21d60",
    "0x21a31ee1afc51d94c2efccaa2092ad1028285549",
    # Coinbase
    "0xa9d1e08c7793af67e9d92fe308d5697fb81d3e43",
    "0x71660c4005ba85c37ccec55d0c4493e66fe775d3",
    # Kraken
    "0x2910543af39aba0cd09dbb2d50200b3e800a63d2",
    "0x53d284357ec70ce289d6d64134dfac8e511c8a3d",
    # Bitfinex
    "0x1151314c646ce4e0efd76d1af4760ae66a9fe30f",
    # OKX
    "0x6cc5f688a315f3dc28a7781717a9a798a59fda7b",
    # Gemini
    "0xd24400ae8bfebb18ca49be86258a3c749cf46853",
}

# Track already-tweeted transactions
_seen_tx_hashes: set[str] = set()

# Daily cap tracking
_wallet_alert_count = 0
_wallet_alert_day = 0


def _reset_daily_cap() -> None:
    global _wallet_alert_count, _wallet_alert_day
    today = datetime.date.today().toordinal()
    if _wallet_alert_day != today:
        _wallet_alert_count = 0
        _wallet_alert_day = today


def can_wallet_alert() -> bool:
    _reset_daily_cap()
    return _wallet_alert_count < config.WHALE_WALLET_DAILY_CAP


def _record_wallet_alert() -> None:
    global _wallet_alert_count
    _reset_daily_cap()
    _wallet_alert_count += 1


def _get_eth_price() -> float:
    """Fetch current ETH price from CoinGecko."""
    try:
        resp = requests.get(
            f"{config.COINGECKO_BASE}/simple/price",
            params={"ids": "ethereum", "vs_currencies": "usd"},
            timeout=10,
        )
        if resp.status_code == 200:
            return resp.json().get("ethereum", {}).get("usd", 0)
    except requests.RequestException:
        pass
    return 0


def _classify_flow(from_addr: str, to_addr: str) -> str:
    """Classify a transfer as exchange inflow, outflow, or unknown."""
    from_lower = from_addr.lower()
    to_lower = to_addr.lower()
    to_exchange = to_lower in _EXCHANGE_ADDRESSES
    from_exchange = from_lower in _EXCHANGE_ADDRESSES

    if to_exchange and not from_exchange:
        return "exchange_inflow"   # depositing to exchange = potential sell
    elif from_exchange and not to_exchange:
        return "exchange_outflow"  # withdrawing from exchange = accumulation
    elif to_exchange and from_exchange:
        return "exchange_internal"
    return "wallet_transfer"


def _get_flow_label(flow: str) -> str:
    """Human-readable label for flow direction."""
    return {
        "exchange_inflow": "moved to exchange (sell signal)",
        "exchange_outflow": "withdrawn from exchange (accumulation)",
        "exchange_internal": "moved between exchanges",
        "wallet_transfer": "wallet-to-wallet transfer",
    }.get(flow, "moved")


def fetch_eth_whale_transactions() -> list[dict]:
    """
    Fetch recent transactions from tracked ETH whale wallets via Etherscan.
    Returns list of large transactions (>$1M).
    """
    if not config.ETHERSCAN_API_KEY:
        logger.debug("ETHERSCAN_API_KEY not set — whale wallet tracking disabled")
        return []

    eth_price = _get_eth_price()
    if eth_price <= 0:
        logger.debug("Could not fetch ETH price for whale tracking")
        return []

    alerts = []

    for label, address in ETH_WHALE_WALLETS:
        if not can_wallet_alert():
            break

        try:
            resp = requests.get(
                "https://api.etherscan.io/api",
                params={
                    "module": "account",
                    "action": "txlist",
                    "address": address,
                    "startblock": 0,
                    "endblock": 99999999,
                    "page": 1,
                    "offset": 5,  # last 5 txns
                    "sort": "desc",
                    "apikey": config.ETHERSCAN_API_KEY,
                },
                timeout=15,
            )
            if resp.status_code != 200:
                continue

            data = resp.json()
            if data.get("status") != "1" or not data.get("result"):
                continue

            for tx in data["result"]:
                tx_hash = tx.get("hash", "")
                if tx_hash in _seen_tx_hashes:
                    continue

                # Check age — only care about txns in last 30 minutes
                tx_time = int(tx.get("timeStamp", 0))
                if time.time() - tx_time > 1800:
                    continue

                value_wei = int(tx.get("value", 0))
                value_eth = value_wei / 1e18
                value_usd = value_eth * eth_price

                if value_usd < config.WHALE_WALLET_MIN_USD:
                    continue

                from_addr = tx.get("from", "")
                to_addr = tx.get("to", "")
                flow = _classify_flow(from_addr, to_addr)

                # Skip internal exchange shuffles
                if flow == "exchange_internal":
                    continue

                alerts.append({
                    "label": label,
                    "symbol": "ETH",
                    "value_native": value_eth,
                    "value_usd": value_usd,
                    "flow": flow,
                    "flow_label": _get_flow_label(flow),
                    "tx_hash": tx_hash,
                    "from_addr": from_addr,
                    "to_addr": to_addr,
                })

            # Rate limit: Etherscan free tier = 5 calls/sec
            time.sleep(0.25)

        except requests.RequestException as exc:
            logger.debug("Etherscan fetch failed for %s: %s", label, exc)

    # Sort by value, biggest first
    alerts.sort(key=lambda a: a["value_usd"], reverse=True)
    return alerts[:3]


def generate_whale_wallet_tweet(alert: dict) -> str | None:
    """Generate an AI tweet about a whale wallet movement."""
    _seen_tx_hashes.add(alert["tx_hash"])

    label = alert["label"]
    symbol = alert["symbol"]
    value_usd = alert["value_usd"]
    value_native = alert["value_native"]
    flow = alert["flow"]
    flow_label = alert["flow_label"]

    usd_str = f"${value_usd / 1e6:.1f}M" if value_usd >= 1e6 else f"${value_usd:,.0f}"
    native_str = f"{value_native:,.0f}" if value_native >= 1 else f"{value_native:,.2f}"

    if ai_writer.is_available():
        system = (
            "You are @CoinWatchAlert. You track whale wallets and report movements "
            "with an analyst tone. Brief, data-driven, no hype.\n\n"
            "RULES:\n"
            "- Max 220 characters\n"
            "- Only use these emojis if needed: \U0001f680\U0001f4c9\u26a1\U0001f440\n"
            "- NO \u26a0\ufe0f emoji, NO exclamation marks, NO 'NFA', NO 'DYOR'\n"
            "- NO hashtags\n"
            "- Mention who moved it, how much, and what it signals\n"
            "- Do NOT wrap response in quotes"
        )

        if flow == "exchange_inflow":
            signal = "This is an exchange deposit — often a sell signal. Mention potential sell pressure."
        elif flow == "exchange_outflow":
            signal = "This is an exchange withdrawal — typically accumulation. Mention conviction/holding."
        else:
            signal = "This is a wallet-to-wallet transfer. Could be OTC, cold storage, or repositioning."

        prompt = (
            f"Write a tweet about this whale wallet movement:\n\n"
            f"Who: {label}\n"
            f"Amount: {native_str} {symbol} (~{usd_str})\n"
            f"Direction: {flow_label}\n\n"
            f"{signal}\n\n"
            f"Max 220 chars. Analyst tone. No hashtags. Write the tweet now. Nothing else."
        )

        tweet = ai_writer._call_claude(system, prompt, max_tokens=120)
        if tweet:
            tweet = tweet.replace("!", ".")
            if len(tweet) <= 220:
                return tweet

    # Template fallback
    if flow == "exchange_inflow":
        return f"🐋 {label} just moved {native_str} {symbol} ({usd_str}) to an exchange\n\n📈 {flow_label.capitalize()} \U0001f440"
    elif flow == "exchange_outflow":
        return f"🐋 {label} pulled {native_str} {symbol} ({usd_str}) off exchange\n\n📉 {flow_label.capitalize()} \U0001f440"
    else:
        return f"🐋 {label} moved {native_str} {symbol} ({usd_str})\n\n🎯 {flow_label.capitalize()}"


def check_whale_wallets() -> list[str]:
    """
    Main entry point — check whale wallets and return tweetable alerts.
    Returns list of tweet strings ready to post.
    """
    if not can_wallet_alert():
        logger.info("Whale wallet daily cap (%d) reached.", config.WHALE_WALLET_DAILY_CAP)
        return []

    eth_alerts = fetch_eth_whale_transactions()
    tweets = []

    for alert in eth_alerts:
        if not can_wallet_alert():
            break
        tweet = generate_whale_wallet_tweet(alert)
        if tweet:
            _record_wallet_alert()
            tweets.append(tweet)

    # Bound the seen set
    if len(_seen_tx_hashes) > 500:
        _seen_tx_hashes.clear()

    return tweets
