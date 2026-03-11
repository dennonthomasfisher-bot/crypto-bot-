"""
Central configuration for the crypto news Twitter bot.
All thresholds and settings are controlled here.
"""
from __future__ import annotations

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
PRICE_ALERT_1H_PCT  = 5.0   # 5 % move in 1 hour
PRICE_ALERT_24H_PCT = 6.0   # 6 % move in 24 hours

# ── Polling intervals (seconds) ───────────────────────────────────────────────
PRICE_CHECK_INTERVAL = 300   # 5 minutes
NEWS_CHECK_INTERVAL  = 900   # 15 minutes

# ── Dedup window ──────────────────────────────────────────────────────────────
# Don't re-alert on the same coin price move within this window (seconds)
PRICE_ALERT_COOLDOWN = 1800  # 30 mins per coin

# Don't repost the same news story within this window (seconds)
NEWS_DEDUP_WINDOW = 86400    # 24 hours

# Minimum gap between any two news tweets (seconds)
NEWS_COOLDOWN_SECS = 3600    # 60 minutes

# ── CoinGecko ─────────────────────────────────────────────────────────────────
COINGECKO_BASE = "https://api.coingecko.com/api/v3"

# ── CryptoPanic ───────────────────────────────────────────────────────────────
CRYPTOPANIC_BASE = "https://cryptopanic.com/api/v1"
# Only post news that CryptoPanic marks "hot" or "important" (bullish/bearish)
CRYPTOPANIC_FILTER = "hot"   # options: hot | rising | important | saved | lol

# ── Misc ──────────────────────────────────────────────────────────────────────
LOG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "bot.log")

# ── Trending coins monitor ────────────────────────────────────────────────────
TRENDING_SURGE_PCT = float(os.getenv("TRENDING_SURGE_PCT", "10.0"))

# ── Daily caps ────────────────────────────────────────────────────────────────
DAILY_TWEET_CAP        = int(os.getenv("DAILY_TWEET_CAP",        "15"))
QUOTE_TWEET_DAILY_CAP  = int(os.getenv("QUOTE_TWEET_DAILY_CAP",  "1"))
AUTO_REPLY_DAILY_CAP   = int(os.getenv("AUTO_REPLY_DAILY_CAP",   "5"))
CT_NARRATIVE_DAILY_CAP = int(os.getenv("CT_NARRATIVE_DAILY_CAP", "3"))
PRICE_ALERT_DAILY_CAP  = int(os.getenv("PRICE_ALERT_DAILY_CAP",  "3"))
NEWS_DAILY_CAP         = int(os.getenv("NEWS_DAILY_CAP",          "4"))
TRENDING_DAILY_CAP     = int(os.getenv("TRENDING_DAILY_CAP",     "1"))
