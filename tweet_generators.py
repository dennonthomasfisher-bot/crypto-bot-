"""
Tweet generators for scheduled content.

Generates quote tweets (market analysis), opinion tweets, and morning
recap tweets using live price data and technical indicators.
"""

import logging
import random
import time

import requests

import config
import indicators
import exchange_client

logger = logging.getLogger(__name__)

# ── Daily caps ───────────────────────────────────────────────────────────────
_quote_count = 0
_quote_day = 0
_reply_count = 0
_reply_day = 0


def _reset_daily_caps() -> None:
    """Reset daily counters if day has changed."""
    global _quote_count, _quote_day, _reply_count, _reply_day
    import datetime
    today = datetime.date.today().toordinal()
    if _quote_day != today:
        _quote_count = 0
        _quote_day = today
    if _reply_day != today:
        _reply_count = 0
        _reply_day = today


def can_quote_tweet() -> bool:
    _reset_daily_caps()
    return _quote_count < config.QUOTE_TWEET_DAILY_CAP


def record_quote_tweet() -> None:
    global _quote_count
    _reset_daily_caps()
    _quote_count += 1


def can_auto_reply() -> bool:
    _reset_daily_caps()
    return _reply_count < config.AUTO_REPLY_DAILY_CAP


def record_auto_reply() -> None:
    global _reply_count
    _reset_daily_caps()
    _reply_count += 1


# ── Price data helpers ───────────────────────────────────────────────────────

def _get_btc_data() -> dict | None:
    """Fetch BTC market data from CoinGecko."""
    try:
        resp = requests.get(
            f"{config.COINGECKO_BASE}/coins/markets",
            params={
                "vs_currency": "usd",
                "ids": "bitcoin",
                "price_change_percentage": "1h,24h,7d",
            },
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()
        return data[0] if data else None
    except requests.RequestException as exc:
        logger.warning("CoinGecko fetch failed for BTC data: %s", exc)
        return None


def _get_top_coins_data() -> list[dict]:
    """Fetch market data for top coins."""
    try:
        coin_ids = ",".join(config.COINS.keys())
        resp = requests.get(
            f"{config.COINGECKO_BASE}/coins/markets",
            params={
                "vs_currency": "usd",
                "ids": coin_ids,
                "price_change_percentage": "1h,24h,7d",
                "order": "market_cap_desc",
            },
            timeout=15,
        )
        resp.raise_for_status()
        return resp.json()
    except requests.RequestException as exc:
        logger.warning("CoinGecko fetch failed for top coins: %s", exc)
        return []


# ── Quote tweet (market analysis) ────────────────────────────────────────────

_ANALYSIS_TEMPLATES = [
    "Bitcoin {trend_word} {key_level} while {context}.",
    "Bitcoin {trend_word} the {ma_level} as {context}.",
    "Bitcoin {volatility} into {pattern}; {outlook}.",
    "Bitcoin's {metric} just {metric_action}, {implication}.",
    "{price_context} — BTC {trend_word} {key_level}. {outlook}.",
    "Interesting setup: BTC {trend_word} {key_level} with {context}.",
    "Worth noting: Bitcoin's {metric} {metric_action}. {implication}.",
]

_TREND_WORDS = {
    "up": [
        "holding above", "pushing past", "reclaiming", "breaking above",
        "building momentum above", "firmly above", "defending",
    ],
    "down": [
        "falling below", "testing support at", "breaking below", "sliding under",
        "struggling to hold", "losing grip on", "pressing against",
    ],
    "flat": [
        "consolidating near", "ranging around", "hovering at", "trading flat near",
        "coiling tightly around", "stuck at", "pinned to",
    ],
}

_KEY_LEVELS = [
    "the 200-day moving average",
    "key resistance at prior consolidation",
    "the 200-day MA",
    "the 100-day MA",
    "its 50-day MA",
    "the weekly pivot",
    "a major volume node",
    "a key liquidity zone",
]

_CONTEXTS = [
    "spot ETF flows mixed",
    "institutional interest quietly growing",
    "macro uncertainty keeps traders cautious",
    "funding rates remain neutral",
    "volatility compressed to multi-week lows",
    "on-chain metrics show steady accumulation",
    "funding rates re-normalizing post-flush",
    "open interest climbs on derivatives",
    "whale wallets adding to positions",
    "stablecoin supply hitting new highs",
    "miners holding rather than selling",
]

_METRICS = [
    ("SOPR (Spent Output Profit Ratio)", "crossed back above 1", "suggesting holders are back in profit"),
    ("MVRV ratio", "entered the caution zone", "historically preceding volatility"),
    ("exchange reserves", "hit new lows", "indicating long-term holder conviction"),
    ("hash rate", "reached an all-time high", "strengthening network security"),
    ("realized cap", "ticked higher", "showing fresh capital entering the market"),
    ("NVT ratio", "moved to a new range", "signaling shifting network valuation"),
    ("stablecoin supply ratio", "compressed", "suggesting dry powder waiting on the sidelines"),
]

_VOLATILITY_WORDS = ["volatility compressed", "showing compression", "coiling tightly"]
_PATTERNS = ["narrow bands", "a tightening range", "a symmetrical triangle", "a multi-day wedge"]
_OUTLOOKS = [
    "institutional positioning suggests a directional move ahead",
    "watch for a breakout in either direction",
    "traders eyeing the next macro catalyst",
    "the longer this range holds, the bigger the eventual move",
    "patience rewarded — setups like this don't last forever",
]


def generate_quote_tweet() -> str | None:
    """
    Generate a market analysis tweet about Bitcoin.
    Returns tweet text or None if data unavailable.
    """
    btc = _get_btc_data()
    if not btc:
        return None

    price = btc.get("current_price", 0)
    if not price or price <= 0:
        logger.warning("BTC price is zero/missing — skipping quote tweet")
        return None

    pct_24h = btc.get("price_change_percentage_24h_in_currency") or 0

    if pct_24h > 1:
        direction = "up"
    elif pct_24h < -1:
        direction = "down"
    else:
        direction = "flat"

    template = random.choice(_ANALYSIS_TEMPLATES)
    # Pick a consistent metric tuple so fields match
    metric = random.choice(_METRICS)

    try:
        tweet = template.format(
            trend_word=random.choice(_TREND_WORDS[direction]),
            key_level=random.choice(_KEY_LEVELS),
            context=random.choice(_CONTEXTS),
            ma_level=random.choice(_KEY_LEVELS),
            volatility=random.choice(_VOLATILITY_WORDS),
            pattern=random.choice(_PATTERNS),
            outlook=random.choice(_OUTLOOKS),
            price_context=f"${price:,.0f}",
            weekly_context=random.choice(["approaching", "testing", "near"]) + " key resistance",
            metric=metric[0],
            metric_action=metric[1],
            implication=metric[2],
        )
    except (KeyError, IndexError):
        tweet = (
            f"Bitcoin trading at ${price:,.0f}, "
            f"{'+' if pct_24h > 0 else ''}{pct_24h:.1f}% in 24h. "
            f"{random.choice(_CONTEXTS).capitalize()}."
        )

    if len(tweet) > 250:
        tweet = tweet[:247] + "…"

    tweet += "\n#Bitcoin #BTC #Crypto"
    return tweet


# ── Morning recap ────────────────────────────────────────────────────────────

def generate_morning_recap() -> str | None:
    """
    Generate a morning market recap tweet with top movers.
    Returns tweet text or None if data unavailable.
    """
    coins = _get_top_coins_data()
    if not coins:
        return None

    btc = next((c for c in coins if c["id"] == "bitcoin"), None)
    if not btc:
        return None

    btc_price = btc.get("current_price", 0)
    if not btc_price or btc_price <= 0:
        logger.warning("BTC price is zero/missing — skipping morning recap")
        return None

    btc_24h = btc.get("price_change_percentage_24h_in_currency") or 0

    # Find biggest mover
    biggest = max(
        coins,
        key=lambda c: abs(c.get("price_change_percentage_24h_in_currency") or 0),
    )
    biggest_symbol = config.COINS.get(biggest["id"], biggest["symbol"].upper())
    biggest_pct = biggest.get("price_change_percentage_24h_in_currency") or 0

    lines = [
        "☀️ Crypto Morning Recap\n",
        f"BTC: ${btc_price:,.0f} ({'+' if btc_24h > 0 else ''}{btc_24h:.1f}% 24h)",
    ]

    # Add top 3 movers (excluding BTC if it's already shown)
    movers = sorted(
        [c for c in coins if c["id"] != "bitcoin"],
        key=lambda c: abs(c.get("price_change_percentage_24h_in_currency") or 0),
        reverse=True,
    )[:3]

    for coin in movers:
        sym = config.COINS.get(coin["id"], coin["symbol"].upper())
        pct = coin.get("price_change_percentage_24h_in_currency") or 0
        p = coin.get("current_price", 0)
        lines.append(f"{sym}: ${p:,.2f} ({'+' if pct > 0 else ''}{pct:.1f}%)")

    if abs(biggest_pct) > 5:
        arrow = "🚀" if biggest_pct > 0 else "📉"
        lines.append(f"\n{arrow} Biggest mover: #{biggest_symbol} {'+' if biggest_pct > 0 else ''}{biggest_pct:.1f}%")

    lines.append("\n#Crypto #Bitcoin #MorningRecap")

    tweet = "\n".join(lines)
    if len(tweet) > 280:
        tweet = tweet[:277].rsplit("\n", 1)[0] + "…"
    return tweet


# ── Opinion tweet ────────────────────────────────────────────────────────────

_OPINIONS = [
    "Market structure looks {sentiment} here. {reasoning}.",
    "Interesting divergence between {pair}. {observation}.",
    "On-chain data suggests {insight}. Worth watching.",
    "Key level to watch: ${level}. {scenario}",
    "My read on current price action: {sentiment}. {reasoning}.",
    "Something worth watching — {insight}. Could be significant.",
    "BTC at ${level} and the {pair} divergence is telling. {observation}.",
]

_BULLISH_REASONS = [
    "Accumulation addresses continue to grow steadily",
    "Exchange outflows hitting multi-month highs — coins moving to cold storage",
    "Long-term holders refusing to sell at these levels",
    "Funding rates normalized after the recent flush — healthy reset",
    "Smart money quietly positioning for the next leg up",
    "Derivatives market de-leveraged, clearing the way for a cleaner move",
    "Spot-driven rally is more sustainable than leverage-fueled pumps",
    "Supply on exchanges at multi-year lows — simple supply/demand math",
]

_BEARISH_REASONS = [
    "Distribution pattern forming on higher timeframes — caution warranted",
    "Exchange inflows spiking — profit-taking likely ahead",
    "Short-term holder cost basis acting as overhead resistance",
    "Leverage building up to uncomfortable levels across derivatives",
    "Macro headwinds could pressure risk assets broadly",
    "Bearish divergence on RSI while price makes new highs — classic warning",
    "Realized profits spiking — historically leads to cooling periods",
    "Market euphoria metrics elevated — usually a contrarian signal",
]


def generate_opinion_tweet() -> str | None:
    """
    Generate an opinion/analysis tweet.
    Returns tweet text or None if data unavailable.
    """
    btc = _get_btc_data()
    if not btc:
        return None

    price = btc.get("current_price", 0)
    if not price or price <= 0:
        logger.warning("BTC price is zero/missing — skipping opinion tweet")
        return None

    pct_24h = btc.get("price_change_percentage_24h_in_currency") or 0

    if pct_24h > 0:
        sentiment = "constructive"
        reasoning = random.choice(_BULLISH_REASONS)
    else:
        sentiment = "cautious"
        reasoning = random.choice(_BEARISH_REASONS)

    template = random.choice(_OPINIONS)

    try:
        tweet = template.format(
            sentiment=sentiment,
            reasoning=reasoning,
            pair="BTC spot and derivatives",
            observation=reasoning,
            insight=reasoning.lower(),
            level=f"{round(price, -2):,.0f}",
            scenario=f"A {'break above' if pct_24h > 0 else 'break below'} "
                     f"could trigger a {'squeeze' if pct_24h > 0 else 'cascade'}.",
        )
    except KeyError:
        tweet = f"BTC at ${price:,.0f}. {reasoning}."

    tweet += "\n#Bitcoin #Crypto #Trading"

    if len(tweet) > 280:
        tweet = tweet[:277].rsplit(" ", 1)[0] + "…"
    return tweet
