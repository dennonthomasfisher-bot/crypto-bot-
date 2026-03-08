"""
Central configuration for the crypto news Twitter bot, trading bot,
and all scheduled tweet generators.
"""

import os
from datetime import datetime, timezone, timedelta
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

# ── CoinMarketCap API (free tier – sign up at coinmarketcap.com/api) ──────────
CMC_API_KEY = os.getenv("CMC_API_KEY", "")

# ── Etherscan API (free tier – sign up at etherscan.io/apis) ─────────────────
ETHERSCAN_API_KEY = os.getenv("ETHERSCAN_API_KEY", "")

# ── Anthropic API (for AI-generated tweets) ─────────────────────────────────
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")

# ── Coins to monitor ─────────────────────────────────────────────────────────
# CoinGecko IDs → display symbols
COINS = {
    "bitcoin":          "BTC",
    "ethereum":         "ETH",
    "binancecoin":      "BNB",
    "solana":           "SOL",
    "ripple":           "XRP",
    "cardano":          "ADA",
    "dogecoin":         "DOGE",
    "avalanche-2":      "AVAX",
    "polkadot":         "DOT",
    "chainlink":        "LINK",
    "toncoin":          "TON",
    "shiba-inu":        "SHIB",
    "near":             "NEAR",
    "uniswap":          "UNI",
    "litecoin":         "LTC",
    "pepe":             "PEPE",
    "render-token":     "RNDR",
    "injective-protocol": "INJ",
    "sui":              "SUI",
    "aptos":            "APT",
}

# ── Alert thresholds ──────────────────────────────────────────────────────────
PRICE_ALERT_1H_PCT  = 5.0   # 5 % move in 1 hour
PRICE_ALERT_24H_PCT = 10.0  # 10 % move in 24 hours

# ── Polling intervals (seconds) ───────────────────────────────────────────────
PRICE_CHECK_INTERVAL  = 600   # 10 minutes
NEWS_CHECK_INTERVAL   = 600   # 10 minutes

# ── Scheduled tweet intervals (growth mode) ─────────────────────────────────
QUOTE_TWEET_INTERVAL  = 5400   # every 90 min (more frequent, monetisation needs volume)
QUOTE_TWEET_DAILY_CAP = 8      # max 8 quote tweets per day
AUTO_REPLY_INTERVAL   = 1200   # every 20 minutes (replies = fastest growth lever)
AUTO_REPLY_DAILY_CAP  = 30     # max 30 auto-replies per day

# Scheduled times (24h format, UK timezone)
MORNING_RECAP_TIME    = "07:30"    # morning recap tweet (earlier = first in feeds)
ENGAGEMENT_TWEET_TIME = "10:00"    # morning engagement tweet (catch US waking up)
ENGAGEMENT_TWEET_TIME_2 = "16:00"  # afternoon engagement tweet (US lunch peak)
OPINION_TWEET_TIME    = "12:00"    # opinion/analysis tweet
THREAD_TIME           = "18:00"    # evening deep-dive thread
THREAD_TIME_2         = "13:00"    # midday thread (US morning, peak CT hours)
POLYMARKET_DAILY_TIME = "15:00"    # polymarket daily summary

# ── Engagement tracking ────────────────────────────────────────────────────
ENGAGEMENT_CHECK_INTERVAL = 3600   # check tweet metrics every hour

# ── Polymarket ───────────────────────────────────────────────────────────────
POLYMARKET_CHECK_INTERVAL     = 3600   # 60 minutes
POLYMARKET_MAX_MARKETS        = 5      # max crypto markets to track
POLYMARKET_ALERT_THRESHOLD_PCT = 5.0   # alert on 5+ percentage point move

# ── CoinMarketCap ──────────────────────────────────────────────────────────
CMC_BASE             = "https://pro-api.coinmarketcap.com"
CMC_CHECK_INTERVAL   = 3600    # check CMC every hour
CMC_TOP_N            = 100     # fetch top 100 coins by market cap
CMC_MOVER_THRESHOLD  = 8.0     # tweet about coins moving 8%+ in 24h

# ── Growth engine ────────────────────────────────────────────────────────────
GROWTH_ENABLED           = True   # master switch for all growth features
INFLUENCER_MENTIONS      = False  # disabled: no @mentioning influencer accounts
INFLUENCER_CALLOUT_TIME  = "16:00"  # influencer callout at 4pm UK (peak CT)
CT_NARRATIVE_INTERVAL    = 10800    # CT narrative tweet every 3 hours
CT_NARRATIVE_DAILY_CAP   = 3       # max 3 narrative tweets per day
HOT_TAKE_TIME_1          = "11:30"  # hot take 1: 11:30am UK (US pre-market)
HOT_TAKE_TIME_2          = "14:00"  # hot take 2: 2pm UK (US morning)
HOT_TAKE_TIME_3          = "20:00"  # hot take 3: 8pm UK (US afternoon)
HOT_TAKE_DAILY_CAP       = 3       # max 3 hot takes per day

# ── Fear & Greed Index ──────────────────────────────────────────────────────
FEAR_GREED_TIME_1    = "09:00"  # morning post (after recap)
FEAR_GREED_TIME_2    = "21:00"  # evening post

# ── Liquidation / Derivatives ──────────────────────────────────────────────
LIQUIDATION_CHECK_INTERVAL = 3600   # check every hour
LIQUIDATION_MIN_USD        = 50_000_000  # only tweet if >$50M liquidated

# ── Breakout alerts (key level crossings) ──────────────────────────────────
BREAKOUT_CHECK_INTERVAL    = 300   # check every 5 minutes
BREAKOUT_COOLDOWN          = 7200  # 2 hour cooldown per level

# ── Weekly recap thread ──────────────────────────────────────────────────────
WEEKLY_RECAP_DAY   = "sunday"
WEEKLY_RECAP_TIME  = "17:00"  # 5pm UK on Sunday

# ── Dedup window ──────────────────────────────────────────────────────────────
PRICE_ALERT_COOLDOWN = 3600  # 1 hour per coin+window
COIN_GLOBAL_COOLDOWN = 7200  # 2 hours – no coin tweeted about twice regardless of source
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
CRYPTOPANIC_BASE = "https://cryptopanic.com/api/developer/v2"
CRYPTOPANIC_FILTER = "hot"   # options: hot | rising | important | saved | lol

# ── New monitors ─────────────────────────────────────────────────────────────
TRENDING_CHECK_INTERVAL   = 1800   # check trending coins every 30 minutes
TRENDING_SURGE_PCT        = 20.0   # 20%+ move in 24h to flag as surging
EVENT_CHECK_INTERVAL      = 3600   # check event calendar every hour
TOKEN_UNLOCK_MIN_VALUE    = 10_000_000  # only tweet unlocks worth >$10M
TOKEN_UNLOCK_WINDOW       = 7 * 86400   # scan 7 days ahead for unlocks
DEFI_TWEET_TIME           = "13:00"  # DeFi tweet at 1pm UK
WHALE_CHECK_INTERVAL      = 3600   # check whale activity every hour
CHART_TWEET_TIME          = "11:00"  # chart tweet at 11am UK
FOLLOWER_CHECK_TIME       = "09:00"  # daily follower count at 9am UK
REPLY_ANALYSIS_INTERVAL   = 7200   # analyze replies every 2 hours

# ── Reply-back (respond to replies on our tweets) ────────────────────────
REPLY_BACK_INTERVAL       = 1200   # check every 20 minutes
REPLY_BACK_DAILY_CAP      = 15     # max 15 reply-backs per day (builds community)

# ── Whale wallet tracker (Etherscan-based) ───────────────────────────────
WHALE_WALLET_CHECK_INTERVAL = 900    # check every 15 minutes
WHALE_WALLET_DAILY_CAP      = 4      # max 4 whale wallet alerts per day
WHALE_WALLET_MIN_USD        = 1_000_000  # only tweet moves >$1M

# ── Quiet hours (no tweets posted during these hours, UK time) ───────────────
QUIET_HOURS_START = 0   # midnight UK
QUIET_HOURS_END   = 7   # 7am UK (catch late-night US audience 11pm-midnight UK)


def is_quiet_hours() -> bool:
    """Return True if current UK time is within quiet hours (shared helper)."""
    now_utc = datetime.now(timezone.utc)
    year = now_utc.year
    # BST: last Sunday of March to last Sunday of October
    mar31 = datetime(year, 3, 31, tzinfo=timezone.utc)
    bst_start = mar31 - timedelta(days=(mar31.weekday() + 1) % 7)
    oct31 = datetime(year, 10, 31, tzinfo=timezone.utc)
    bst_end = oct31 - timedelta(days=(oct31.weekday() + 1) % 7)
    uk_offset = timedelta(hours=1) if bst_start <= now_utc < bst_end else timedelta(hours=0)
    uk_hour = (now_utc + uk_offset).hour
    if QUIET_HOURS_START > QUIET_HOURS_END:
        return uk_hour >= QUIET_HOURS_START or uk_hour < QUIET_HOURS_END
    return QUIET_HOURS_START <= uk_hour < QUIET_HOURS_END

# ── Logging ──────────────────────────────────────────────────────────────────
LOG_FILE         = os.path.join(os.path.dirname(__file__), "bot.log")
TRADING_LOG_FILE = os.path.join(os.path.dirname(__file__), "trading_bot.log")
