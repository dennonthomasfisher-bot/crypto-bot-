"""
Central configuration for the crypto news Twitter bot.
All thresholds and settings are controlled here.
"""

import os
from dotenv import load_dotenv

load_dotenv()

# ── Twitter / X API credentials ──────────────────────────────────────────────
TWITTER_API_KEY             = os.getenv("TWITTER_API_KEY", "")
TWITTER_API_SECRET          = os.getenv("TWITTER_API_SECRET", "")
TWITTER_ACCESS_TOKEN        = os.getenv("TWITTER_ACCESS_TOKEN", "")
TWITTER_ACCESS_TOKEN_SECRET = os.getenv("TWITTER_ACCESS_TOKEN_SECRET", "")
TWITTER_BEARER_TOKEN        = os.getenv("TWITTER_BEARER_TOKEN", "")

# ── Anthropic / Claude API ────────────────────────────────────────────────────
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")

# ── Coins to monitor ─────────────────────────────────────────────────────────
# CoinGecko IDs → display symbols
COINS = {
    "bitcoin":       "BTC",
    "ethereum":      "ETH",
    "binancecoin":   "BNB",
    "solana":        "SOL",
    "ripple":        "XRP",
    "cardano":       "ADA",
    "dogecoin":      "DOGE",
    "avalanche-2":   "AVAX",
    "polkadot":      "DOT",
    "chainlink":     "LINK",
}

# ── Alert thresholds ──────────────────────────────────────────────────────────
# Post a tweet when a coin moves more than this % in the given window
PRICE_ALERT_1H_PCT  = 5.0   # 5 % move in 1 hour
PRICE_ALERT_24H_PCT = 10.0  # 10 % move in 24 hours

# ── Polling intervals (seconds) ───────────────────────────────────────────────
PRICE_CHECK_INTERVAL = 600   # 10 minutes

# ── Dedup window ──────────────────────────────────────────────────────────────
# Don't re-alert on the same coin price move within this window (seconds)
PRICE_ALERT_COOLDOWN = 3600  # 1 hour per coin

# ── CoinGecko ─────────────────────────────────────────────────────────────────
COINGECKO_BASE = "https://api.coingecko.com/api/v3"

# ── Posting guard ─────────────────────────────────────────────────────────────
# Minimum seconds between any two posts to avoid bursting the Twitter rate limit.
# Scheduled events (e.g. morning recap) can bypass this guard.
MIN_POST_INTERVAL = 60   # 1 minute

# ── Misc ──────────────────────────────────────────────────────────────────────
LOG_FILE = os.path.join(os.path.dirname(__file__), "crypto_bot.log")
