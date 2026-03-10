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

# ── CryptoPanic API (free tier – sign up at cryptopanic.com) ──────────────────
CRYPTOPANIC_API_KEY = os.getenv("CRYPTOPANIC_API_KEY", "")

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
PRICE_ALERT_1H_PCT  = 3.0   # 3 % move in 1 hour  (lowered temporarily to catch up)
PRICE_ALERT_24H_PCT = 7.0   # 7 % move in 24 hours (lowered temporarily to catch up)

# ── Polling intervals (seconds) ───────────────────────────────────────────────
PRICE_CHECK_INTERVAL = 180   # 3 minutes  (tightened temporarily to catch up)
NEWS_CHECK_INTERVAL  = 300   # 5 minutes  (tightened temporarily to catch up)

# ── Dedup window ──────────────────────────────────────────────────────────────
# Don't re-alert on the same coin price move within this window (seconds)
PRICE_ALERT_COOLDOWN = 1800  # 30 mins per coin (halved temporarily to catch up)

# Don't repost the same news story within this window (seconds)
NEWS_DEDUP_WINDOW = 86400    # 24 hours

# ── CoinGecko ─────────────────────────────────────────────────────────────────
COINGECKO_BASE = "https://api.coingecko.com/api/v3"

# ── CryptoPanic ───────────────────────────────────────────────────────────────
CRYPTOPANIC_BASE = "https://cryptopanic.com/api/v1"
# Only post news that CryptoPanic marks "hot" or "important" (bullish/bearish)
CRYPTOPANIC_FILTER = "hot"   # options: hot | rising | important | saved | lol

# ── Misc ──────────────────────────────────────────────────────────────────────
LOG_FILE = os.path.join(os.path.dirname(__file__), "crypto_bot.log")
