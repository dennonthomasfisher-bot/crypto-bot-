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

# ── Anthropic API (for AI-generated tweets) ─────────────────────────────────
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
PRICE_ALERT_1H_PCT  = 5.0   # 5 % move in 1 hour
PRICE_ALERT_24H_PCT = 10.0  # 10 % move in 24 hours

# ── Polling intervals (seconds) ───────────────────────────────────────────────
PRICE_CHECK_INTERVAL  = 600   # 10 minutes
NEWS_CHECK_INTERVAL   = 600   # 10 minutes

# ── Scheduled tweet intervals ────────────────────────────────────────────────
QUOTE_TWEET_INTERVAL  = 10800  # every 3 hours (was 1h — less spam, more quality)
QUOTE_TWEET_DAILY_CAP = 4      # max 4 quote tweets per day (was 8)
AUTO_REPLY_INTERVAL   = 3600   # every hour (was 30min)
AUTO_REPLY_DAILY_CAP  = 5      # max 5 auto-replies per day (was 8)

# Scheduled times (24h format, UK timezone)
MORNING_RECAP_TIME    = "08:00"    # morning recap tweet
OPINION_TWEET_TIME    = "12:00"    # opinion/analysis tweet
ENGAGEMENT_TWEET_TIME = "14:00"    # afternoon engagement/question tweet
THREAD_TIME           = "18:00"    # evening deep-dive thread
POLYMARKET_DAILY_TIME = "15:00"    # polymarket daily summary

# ── Engagement tracking ────────────────────────────────────────────────────
ENGAGEMENT_CHECK_INTERVAL = 3600   # check tweet metrics every hour

# ── Polymarket ───────────────────────────────────────────────────────────────
POLYMARKET_CHECK_INTERVAL     = 1800   # 30 minutes
POLYMARKET_MAX_MARKETS        = 5      # max crypto markets to track
POLYMARKET_ALERT_THRESHOLD_PCT = 5.0   # alert on 5+ percentage point move

# ── Growth engine ────────────────────────────────────────────────────────────
GROWTH_ENABLED           = True   # master switch for all growth features
INFLUENCER_MENTIONS      = True   # @mention big accounts (safe: 1/day max, natural context)
INFLUENCER_CALLOUT_TIME  = "16:00"  # one influencer callout per day at 4pm UK (peak CT hours)
CT_NARRATIVE_INTERVAL    = 14400    # CT narrative tweet every 4 hours
CT_NARRATIVE_DAILY_CAP   = 3       # max 3 narrative tweets per day
HOT_TAKE_TIME            = "20:00"  # one spicy hot take per day at 8pm UK (US afternoon)
HOT_TAKE_DAILY_CAP       = 1       # max 1 hot take per day

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
