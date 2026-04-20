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
# Set TWITTER_ENABLED=false to skip all X posting (e.g. account suspended)
# while leaving Telegram and Bluesky mirrors running normally.
TWITTER_ENABLED             = os.getenv("TWITTER_ENABLED", "true").lower() in ("1", "true", "yes")

# ── Anthropic / Claude API ────────────────────────────────────────────────────
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")

# ── Telegram ─────────────────────────────────────────────────────────────────
TELEGRAM_BOT_TOKEN    = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHANNEL_ID   = os.getenv("TELEGRAM_CHANNEL_ID", "")
TELEGRAM_ENABLED      = os.getenv("TELEGRAM_ENABLED", "true").lower() in ("1", "true", "yes")
# Optional: send health/internal alerts to a different chat (private DM or
# private group) so subscribers don't see "bot alive but idle" warnings in
# the public channel. Leave blank to fall back to TELEGRAM_CHANNEL_ID.
TELEGRAM_ALERT_CHAT_ID = os.getenv("TELEGRAM_ALERT_CHAT_ID", "")

# ── Bluesky ──────────────────────────────────────────────────────────────────
BLUESKY_HANDLE       = os.getenv("BLUESKY_HANDLE", "")
BLUESKY_APP_PASSWORD = os.getenv("BLUESKY_APP_PASSWORD", "")
BLUESKY_ENABLED      = os.getenv("BLUESKY_ENABLED", "true").lower() in ("1", "true", "yes")

# ── NewsAPI.org API key ──────────────────────────────────────────────────────
NEWS_API_KEY = os.getenv("NEWS_API_KEY", "")

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
PRICE_ALERT_1H_PCT  = 4.0   # 4 % move in 1 hour
PRICE_ALERT_24H_PCT = 5.0   # 5 % move in 24 hours

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
DAILY_TWEET_CAP        = int(os.getenv("DAILY_TWEET_CAP",        "25"))
QUOTE_TWEET_DAILY_CAP  = int(os.getenv("QUOTE_TWEET_DAILY_CAP",  "1"))
AUTO_REPLY_DAILY_CAP   = int(os.getenv("AUTO_REPLY_DAILY_CAP",   "5"))
CT_NARRATIVE_DAILY_CAP = int(os.getenv("CT_NARRATIVE_DAILY_CAP", "3"))
PRICE_ALERT_DAILY_CAP  = int(os.getenv("PRICE_ALERT_DAILY_CAP",  "3"))
NEWS_DAILY_CAP         = int(os.getenv("NEWS_DAILY_CAP",          "8"))
TRENDING_DAILY_CAP     = int(os.getenv("TRENDING_DAILY_CAP",      "4"))
REPLY_DAILY_CAP        = int(os.getenv("REPLY_DAILY_CAP",         "5"))

# ── Reply engine ─────────────────────────────────────────────────────────────
# Broad mix: news aggregators, on-chain data, technical analysts, founders.
_DEFAULT_REPLY_ACCOUNTS = ",".join([
    # News / aggregators
    "WatcherGuru", "tier10k", "DeItaone", "WuBlockchain",
    "Cointelegraph", "CoinDesk", "CryptoSlate",
    # On-chain / data / research
    "lookonchain", "MessariCrypto", "nic__carter", "DocumentingBTC",
    # Flow / whales
    "unusual_whales",
    # Technical / trader analysts
    "rektcapital", "CryptoMichNL", "Pentosh1",
    # Founders / execs (mega-reach)
    "saylor", "cz_binance", "VitalikButerin",
    # Viral / AI crypto
    "aixbt_agent",
    # Security / exploit alerts (high-signal)
    "PeckShieldAlert",
])
REPLY_ACCOUNTS = [a.strip() for a in os.getenv(
    "REPLY_ACCOUNTS", _DEFAULT_REPLY_ACCOUNTS,
).split(",") if a.strip()]
MAX_REPLIES_PER_HOUR    = int(os.getenv("MAX_REPLIES_PER_HOUR",    "3"))
REPLY_MIN_GAP_SECONDS   = int(os.getenv("REPLY_MIN_GAP_SECONDS",  "240"))

# ── Bluesky reply engine ─────────────────────────────────────────────────────
# Bluesky is much smaller than X — handles below are the top active crypto /
# crypto-adjacent accounts. Dead or renamed handles just get skipped by the
# engine (harmless). Using .bsky.social unless the account has a custom domain.
_DEFAULT_BLUESKY_REPLY_ACCOUNTS = ",".join([
    # Crypto media (most reliable feed sources)
    "decrypt.co",
    "coindesk.com",
    "theblock.co",
    "dlnews.com",
    "blockworks.co",
    "thedefiant.io",
    # Research / analysts
    "nic.xyz",                       # Nic Carter
    "molly.wiki",                    # Molly White
    "hasufl.bsky.social",            # Hasu
    "laurashin.bsky.social",         # Laura Shin (Unchained)
    "patio11.bsky.social",           # Patrick McKenzie (finance/tech)
    "notboring.co",                  # Packy McCormick
    # Macro / finance writers
    "krugman.bsky.social",           # Paul Krugman
    "nouriel.bsky.social",           # Nouriel Roubini
    "matthewcklein.bsky.social",     # Matt Klein (FT Alphaville alum)
    # Trader / on-chain
    "lookonchain.bsky.social",
    "dylanleclair.bsky.social",
    # Founders / industry
    "balajis.com",                   # Balaji Srinivasan
    "vitalik.bsky.social",           # fallback for Vitalik
])
BLUESKY_REPLY_ACCOUNTS = [a.strip() for a in os.getenv(
    "BLUESKY_REPLY_ACCOUNTS", _DEFAULT_BLUESKY_REPLY_ACCOUNTS,
).split(",") if a.strip()]

# ── CoinMarketCap API ─────────────────────────────────────────────────────────
CMC_API_KEY        = os.getenv("CMC_API_KEY", "")
CMC_BASE           = "https://pro-api.coinmarketcap.com"
CMC_TOP_N          = int(os.getenv("CMC_TOP_N", "100"))
CMC_MOVER_THRESHOLD = float(os.getenv("CMC_MOVER_THRESHOLD", "8.0"))

# ── Breakout monitor ──────────────────────────────────────────────────────────
BREAKOUT_COOLDOWN = int(os.getenv("BREAKOUT_COOLDOWN", "14400"))  # 4 hours per level

# ── Crypto.com Exchange (trading bot) ────────────────────────────────────────
CDX_API_KEY    = os.getenv("CDX_API_KEY", "")
CDX_API_SECRET = os.getenv("CDX_API_SECRET", "")
CDX_BASE       = "https://api.crypto.com/exchange/v1"

TRADING_PAIRS          = ["BTC_USDT", "ETH_USDT", "SOL_USDT", "XRP_USDT",
                           "ADA_USDT", "AVAX_USDT", "DOGE_USDT", "DOT_USDT"]
TRADING_TIMEFRAME      = os.getenv("TRADING_TIMEFRAME", "15m")
TRADING_CAPITAL        = float(os.getenv("TRADING_CAPITAL", "200.0"))
TRADING_MAX_PER_TRADE  = float(os.getenv("TRADING_MAX_PER_TRADE", "25.0"))
TRADING_MAX_POS_PCT    = float(os.getenv("TRADING_MAX_POS_PCT", "0.10"))   # 10 %
TRADING_STOP_LOSS_PCT  = float(os.getenv("TRADING_STOP_LOSS_PCT", "0.035"))  # 3.5 %
TRADING_TAKE_PROFIT_PCT = float(os.getenv("TRADING_TAKE_PROFIT_PCT", "0.060"))  # 6.0 %
TRADING_POLL_INTERVAL  = int(os.getenv("TRADING_POLL_INTERVAL", "60"))
TRADING_LOG_FILE       = os.path.join(os.path.dirname(os.path.abspath(__file__)), "trading_bot.log")
