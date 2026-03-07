"""
Central configuration for the crypto news Twitter bot, trading bot,
and all scheduled tweet generators.
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

# ── CryptoPanic API (free tier – sign up at cryptopanic.com) ──────────────────
CRYPTOPANIC_API_KEY = os.getenv("CRYPTOPANIC_API_KEY", "")

# ── Crypto.com Exchange API credentials ──────────────────────────────────────
EXCHANGE_API_KEY    = os.getenv("EXCHANGE_API_KEY", "")
EXCHANGE_API_SECRET = os.getenv("EXCHANGE_API_SECRET", "")

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
PRICE_ALERT_1H_PCT  = 5.0   # 5 % move in 1 hour
PRICE_ALERT_24H_PCT = 10.0  # 10 % move in 24 hours

# ── Polling intervals (seconds) ───────────────────────────────────────────────
PRICE_CHECK_INTERVAL  = 600   # 10 minutes
NEWS_CHECK_INTERVAL   = 600   # 10 minutes

# ── Scheduled tweet intervals ────────────────────────────────────────────────
QUOTE_TWEET_INTERVAL  = 14400  # 4 hours (in seconds)
QUOTE_TWEET_DAILY_CAP = 4      # max quote tweets per day
AUTO_REPLY_INTERVAL   = 1800   # 30 minutes
AUTO_REPLY_DAILY_CAP  = 8      # max auto-replies per day

# Scheduled times (24h format, UK timezone)
MORNING_RECAP_TIME    = "08:00"    # morning recap tweet
OPINION_TWEET_TIME    = "12:00"    # opinion/analysis tweet
POLYMARKET_DAILY_TIME = "15:00"    # polymarket daily summary

# ── Polymarket ───────────────────────────────────────────────────────────────
POLYMARKET_CHECK_INTERVAL     = 1800   # 30 minutes
POLYMARKET_MAX_MARKETS        = 5      # max crypto markets to track
POLYMARKET_ALERT_THRESHOLD_PCT = 5.0   # alert on 5+ percentage point move

# ── Dedup window ──────────────────────────────────────────────────────────────
PRICE_ALERT_COOLDOWN = 3600  # 1 hour per coin
NEWS_DEDUP_WINDOW    = 86400 # 24 hours

# ── Trading bot ──────────────────────────────────────────────────────────────
# instrument_name → notional buy amount in USD
TRADING_PAIRS = {
    "BTC_USDT": 20.0,
    "XRP_USDT": 20.0,
}
TRADING_CYCLE_INTERVAL = 60  # seconds between analysis cycles

# ── CoinGecko ─────────────────────────────────────────────────────────────────
COINGECKO_BASE = "https://api.coingecko.com/api/v3"

# ── CryptoPanic ───────────────────────────────────────────────────────────────
CRYPTOPANIC_BASE = "https://cryptopanic.com/api/v1"
CRYPTOPANIC_FILTER = "hot"   # options: hot | rising | important | saved | lol

# ── Logging ──────────────────────────────────────────────────────────────────
LOG_FILE         = os.path.join(os.path.dirname(__file__), "bot.log")
TRADING_LOG_FILE = os.path.join(os.path.dirname(__file__), "trading_bot.log")
