"""
Tweet generators for scheduled content.

Generates quote tweets (market analysis), opinion tweets, and morning
recap tweets using live price data and technical indicators.
"""
from __future__ import annotations

import datetime
import logging
import random
import time

import requests

import config
import state
import ai_writer
import defi_monitor

logger = logging.getLogger(__name__)

# ── Daily caps ───────────────────────────────────────────────────────────────
_quote_count = 0
_quote_day = 0
_reply_count = 0
_reply_day = 0
_narrative_count = 0
_narrative_day = 0


def _reset_daily_caps() -> None:
    """Reset daily counters if day has changed."""
    global _quote_count, _quote_day, _reply_count, _reply_day
    global _narrative_count, _narrative_day
    today = datetime.date.today().toordinal()
    if _quote_day != today:
        _quote_count = 0
        _quote_day = today
    if _reply_day != today:
        _reply_count = 0
        _reply_day = today
    if _narrative_day != today:
        _narrative_count = 0
        _narrative_day = today


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


def can_ct_narrative() -> bool:
    _reset_daily_caps()
    return _narrative_count < config.CT_NARRATIVE_DAILY_CAP


def record_ct_narrative() -> None:
    global _narrative_count
    _reset_daily_caps()
    _narrative_count += 1


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


def _fmt_pct(val: float) -> str:
    """Format a percentage with sign."""
    return f"{'+' if val > 0 else ''}{val:.1f}%"


def _fmt_price(val: float) -> str:
    """Format a USD price."""
    if val >= 1000:
        return f"${val:,.0f}"
    if val >= 1:
        return f"${val:,.2f}"
    return f"${val:.4f}"


def _pick_hashtags(symbols: list[str], extra: list[str] | None = None) -> str:
    """Deprecated — returns empty string. Hashtags hurt reach on X/Twitter."""
    return ""


# ── Quote tweet (market analysis) ────────────────────────────────────────────

# Each generator function produces a different style of tweet.
# The main generate_quote_tweet picks one at random.

def _quote_price_action(btc: dict, coins: list[dict]) -> str:
    """Price-focused analysis tweet with BTC + top mover."""
    price = btc["current_price"]
    pct_24h = btc.get("price_change_percentage_24h_in_currency") or 0
    pct_7d = btc.get("price_change_percentage_7d_in_currency") or 0

    if pct_24h > 2:
        emoji, mood = "🟢", "pushing higher"
    elif pct_24h < -2:
        emoji, mood = "🔴", "under pressure"
    elif abs(pct_24h) <= 0.5:
        emoji, mood = "➡️", "moving sideways"
    elif pct_24h > 0:
        emoji, mood = "🟢", "ticking up"
    else:
        emoji, mood = "🔴", "drifting lower"

    lines = [
        f"📊 BTC at {_fmt_price(price)} ({_fmt_pct(pct_24h)} 24h)",
        "",
    ]

    # Add context line about the 7d trend
    if abs(pct_7d) > 5:
        lines.append(f"📈 7-day: {_fmt_pct(pct_7d)} — {'strong momentum' if pct_7d > 0 else 'correction deepening'}")
    else:
        lines.append(f"📈 7-day: {_fmt_pct(pct_7d)} — {mood}")

    # Add a top mover if available
    if coins:
        non_btc = [c for c in coins if c["id"] != "bitcoin"]
        if non_btc:
            mover = max(non_btc, key=lambda c: abs(c.get("price_change_percentage_24h_in_currency") or 0))
            m_sym = config.COINS.get(mover["id"], mover["symbol"].upper())
            m_pct = mover.get("price_change_percentage_24h_in_currency") or 0
            m_price = mover.get("current_price", 0)
            if abs(m_pct) > 1:
                m_emoji = "🟢" if m_pct > 0 else "🔴"
                lines.extend(["", f"{m_emoji} {m_sym} at {_fmt_price(m_price)} ({_fmt_pct(m_pct)})"])

    return "\n".join(lines)


def _quote_on_chain(btc: dict, _coins: list[dict]) -> str:
    """On-chain metric focused tweet."""
    price = btc["current_price"]
    pct_24h = btc.get("price_change_percentage_24h_in_currency") or 0

    metrics = [
        (
            "Exchange reserves continue dropping",
            "coins leaving exchanges = reduced sell pressure. Holders aren't selling here.",
            ["Bitcoin", "BTC", "OnChain"],
        ),
        (
            "Long-term holder supply just hit a new high",
            "conviction remains strong despite the chop. Weak hands have been shaken out.",
            ["Bitcoin", "BTC", "OnChain"],
        ),
        (
            "Hash rate at all-time highs",
            "miners continue to invest in network security. Fundamentals remain solid.",
            ["Bitcoin", "BTC", "Mining"],
        ),
        (
            "Stablecoin supply on exchanges climbing",
            "dry powder building up. When buyers decide to deploy, supply is thin.",
            ["Bitcoin", "Crypto", "Stablecoins"],
        ),
        (
            "Active addresses showing steady growth",
            "network usage expanding quietly. Adoption doesn't wait for price.",
            ["Bitcoin", "BTC", "Adoption"],
        ),
        (
            "MVRV ratio in the neutral zone",
            "market not overheated, not capitulating. Classic accumulation territory.",
            ["Bitcoin", "BTC", "OnChain"],
        ),
    ]

    headline, body, tags = random.choice(metrics)

    lines = [
        f"BTC at {_fmt_price(price)} ({_fmt_pct(pct_24h)} 24h)",
        "",
        f"{headline} —",
        "",
        body,
    ]
    return "\n".join(lines)


def _quote_market_structure(btc: dict, coins: list[dict]) -> str:
    """Market structure / sentiment tweet."""
    price = btc["current_price"]
    pct_24h = btc.get("price_change_percentage_24h_in_currency") or 0
    mcap = btc.get("market_cap", 0)

    if pct_24h > 1:
        sentiment_takes = [
            "Funding rates normalizing after the flush — healthier setup for continuation.",
            "Spot buying leading this move. Leverage isn't driving it — that's bullish.",
            "Shorts getting squeezed while spot bids stack up. Classic accumulation.",
            "Market structure shifted bullish on the daily. Key support reclaimed.",
        ]
    elif pct_24h < -1:
        sentiment_takes = [
            "Leverage getting flushed — but that's how bottoms form. Watch for a reclaim.",
            "Cascading liquidations clearing out late longs. The reset was needed.",
            "Spot premium still positive despite the dip — real demand underneath.",
            "Fear rising but long-term holder behavior unchanged. They've seen this before.",
        ]
    else:
        sentiment_takes = [
            "Low volatility compression like this usually precedes a big move. Stay ready.",
            "Range-bound but volume building. Someone is quietly accumulating here.",
            "Boring markets make money for patient traders. Breakout loading.",
            "Tight range, declining volume — coiled spring. Direction TBD.",
        ]

    take = random.choice(sentiment_takes)

    mcap_str = ""
    if mcap > 0:
        mcap_str = f"Market cap: ${mcap / 1e12:.2f}T" if mcap >= 1e12 else f"Market cap: ${mcap / 1e9:.0f}B"

    lines = [
        f"BTC at {_fmt_price(price)} ({_fmt_pct(pct_24h)} 24h)",
    ]
    if mcap_str:
        lines.append(mcap_str)
    lines.extend([
        "",
        take,
    ])
    return "\n".join(lines)


def _quote_multi_coin(btc: dict, coins: list[dict]) -> str:
    """Multi-coin market snapshot."""
    price = btc["current_price"]
    pct_24h = btc.get("price_change_percentage_24h_in_currency") or 0

    lines = [
        "Market check:",
        "",
        f"→ BTC: {_fmt_price(price)} ({_fmt_pct(pct_24h)})",
    ]

    if coins:
        eth = next((c for c in coins if c["id"] == "ethereum"), None)
        if eth:
            e_price = eth.get("current_price", 0)
            e_pct = eth.get("price_change_percentage_24h_in_currency") or 0
            lines.append(f"→ ETH: {_fmt_price(e_price)} ({_fmt_pct(e_pct)})")

        non_btc_eth = [c for c in coins if c["id"] not in ("bitcoin", "ethereum")]
        movers = sorted(
            non_btc_eth,
            key=lambda c: abs(c.get("price_change_percentage_24h_in_currency") or 0),
            reverse=True,
        )[:2]

        for coin in movers:
            sym = config.COINS.get(coin["id"], coin["symbol"].upper())
            c_pct = coin.get("price_change_percentage_24h_in_currency") or 0
            c_price = coin.get("current_price", 0)
            emoji = "🟢" if c_pct > 0 else "🔴"
            lines.append(f"→ {emoji} {sym}: {_fmt_price(c_price)} ({_fmt_pct(c_pct)})")

    if coins:
        lines.append("")
        green = sum(1 for c in coins if (c.get("price_change_percentage_24h_in_currency") or 0) > 0)
        total = len(coins)
        lines.append(f"{green}/{total} coins green on the day")

    return "\n".join(lines)


def _quote_alt_focus(_btc: dict, coins: list[dict]) -> str:
    """Alt-focused tweet that leads with altcoin movers, not BTC."""
    if not coins:
        return _quote_multi_coin(_btc, coins)  # fallback

    non_btc = [c for c in coins if c["id"] != "bitcoin"]
    if not non_btc:
        return _quote_multi_coin(_btc, coins)

    # Find the biggest mover
    top = max(non_btc, key=lambda c: abs(c.get("price_change_percentage_24h_in_currency") or 0))
    sym = config.COINS.get(top["id"], top["symbol"].upper())
    pct = top.get("price_change_percentage_24h_in_currency") or 0
    price = top.get("current_price", 0)
    emoji = "🟢" if pct > 0 else "🔴"

    lines = [
        f"{emoji} {sym} {_fmt_pct(pct)} today — {'leading' if pct > 0 else 'lagging'} the market at {_fmt_price(price)}",
        "",
    ]

    # Add 1-2 more alts
    others = [c for c in non_btc if c["id"] != top["id"]]
    others_sorted = sorted(others, key=lambda c: abs(c.get("price_change_percentage_24h_in_currency") or 0), reverse=True)
    for coin in others_sorted[:2]:
        s = config.COINS.get(coin["id"], coin["symbol"].upper())
        p = coin.get("price_change_percentage_24h_in_currency") or 0
        pr = coin.get("current_price", 0)
        e = "🟢" if p > 0 else "🔴"
        lines.append(f"→ {e} {s}: {_fmt_price(pr)} ({_fmt_pct(p)})")

    green = sum(1 for c in non_btc if (c.get("price_change_percentage_24h_in_currency") or 0) > 0)
    lines.extend(["", f"Alts: {green}/{len(non_btc)} green"])

    return "\n".join(lines)


def _quote_narrative(_btc: dict, _coins: list[dict]) -> str:
    """Narrative / macro tweet that doesn't lead with a price."""
    price = _btc["current_price"]
    pct_7d = _btc.get("price_change_percentage_7d_in_currency") or 0

    narratives = [
        f"BTC at {_fmt_price(price)} with {_fmt_pct(pct_7d)} on the week.\n\nETF flows still the main driver — institutional demand hasn't slowed.",
        f"Market cap holding steady while volume drops.\n\nConsolidation at {_fmt_price(price)} BTC. The next macro catalyst decides direction.",
        f"Halving cycle comparison: we're tracking ahead of 2020 at this stage.\n\nBTC at {_fmt_price(price)}. History doesn't repeat but it rhymes.",
        f"DXY weakness + BTC at {_fmt_price(price)}.\n\nIf the dollar keeps fading, risk assets benefit. Watching the correlation closely.",
        f"Stablecoin market cap hitting new highs while BTC sits at {_fmt_price(price)}.\n\nDry powder waiting to deploy.",
    ]

    return random.choice(narratives)


# Weighted generators: alt-focused and multi-coin appear twice to reduce BTC dominance
_QUOTE_GENERATORS = [
    _quote_price_action,
    _quote_on_chain,
    _quote_market_structure,
    _quote_multi_coin,
    _quote_multi_coin,
    _quote_alt_focus,
    _quote_alt_focus,
    _quote_narrative,
]


def generate_quote_tweet() -> str | None:
    """
    Generate a market tweet with enforced content variety.
    Tries AI (Claude) first with category rotation, falls back to templates.
    """
    btc = _get_btc_data()
    if not btc:
        return None

    price = btc.get("current_price", 0)
    if not price or price <= 0:
        logger.warning("BTC price is zero/missing — skipping quote tweet")
        return None

    pct_24h = btc.get("price_change_percentage_24h_in_currency") or 0
    pct_7d = btc.get("price_change_percentage_7d_in_currency") or 0
    mcap = btc.get("market_cap", 0)
    coins = _get_top_coins_data()

    # Try AI-generated tweet first (with category rotation)
    if ai_writer.is_available():
        result = ai_writer.generate_quote_tweet(price, pct_24h, pct_7d, mcap, coins)
        ai_tweet, category = result
        if ai_tweet and len(ai_tweet) <= 280:
            logger.info("Using AI-generated quote tweet (category: %s)", category)
            state.record_content_category(category)
            return ai_tweet
        elif ai_tweet:
            logger.info("AI tweet too long (%d chars), falling back to template", len(ai_tweet))

    # Template fallback — pick a different style than last time
    last_style = state.get_last_quote_style()
    candidates = [g for g in _QUOTE_GENERATORS if g.__name__ != last_style]
    if not candidates:
        candidates = _QUOTE_GENERATORS
    generator = random.choice(candidates)
    state.record_quote_style(generator.__name__)
    state.record_content_category("template_" + generator.__name__)

    try:
        tweet = generator(btc, coins)
    except Exception as exc:
        logger.warning("Quote generator %s failed: %s", generator.__name__, exc)
        tweet = f"BTC at {_fmt_price(price)} ({_fmt_pct(pct_24h)} 24h)"

    if len(tweet) > 280:
        tweet = tweet[:277].rsplit("\n", 1)[0] + "..."
    return tweet


# ── Morning recap ────────────────────────────────────────────────────────────

def generate_morning_recap() -> str | None:
    """
    Generate a morning market recap tweet with top movers.
    Tries AI first, falls back to template.
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

    # Try AI first
    if ai_writer.is_available():
        ai_tweet = ai_writer.generate_morning_recap(btc, coins)
        if ai_tweet and len(ai_tweet) <= 280:
            logger.info("Using AI-generated morning recap")
            return ai_tweet

    btc_24h = btc.get("price_change_percentage_24h_in_currency") or 0
    eth = next((c for c in coins if c["id"] == "ethereum"), None)

    lines = [
        "GM. Quick market check:",
        "",
        f"→ BTC: {_fmt_price(btc_price)} ({_fmt_pct(btc_24h)} 24h)",
    ]

    if eth:
        eth_price = eth.get("current_price", 0)
        eth_24h = eth.get("price_change_percentage_24h_in_currency") or 0
        lines.append(f"→ ETH: {_fmt_price(eth_price)} ({_fmt_pct(eth_24h)})")

    # Top 3 movers (excluding BTC/ETH)
    movers = sorted(
        [c for c in coins if c["id"] not in ("bitcoin", "ethereum")],
        key=lambda c: abs(c.get("price_change_percentage_24h_in_currency") or 0),
        reverse=True,
    )[:3]

    if movers:
        lines.append("")
        lines.append("📊 Movers:")
        for coin in movers:
            sym = config.COINS.get(coin["id"], coin["symbol"].upper())
            pct = coin.get("price_change_percentage_24h_in_currency") or 0
            p = coin.get("current_price", 0)
            emoji = "🟢" if pct > 0 else "🔴"
            lines.append(f"→ {emoji} {sym}: {_fmt_price(p)} ({_fmt_pct(pct)})")

    green = sum(1 for c in coins if (c.get("price_change_percentage_24h_in_currency") or 0) > 0)
    lines.append("")
    lines.append(f"{green}/{len(coins)} coins green")

    tweet = "\n".join(lines)
    if len(tweet) > 280:
        tweet = tweet[:277].rsplit("\n", 1)[0] + "..."
    return tweet


# ── Opinion tweet ────────────────────────────────────────────────────────────

_BULLISH_TAKES = [
    "Accumulation addresses growing steadily — smart money loading quietly.",
    "Exchange outflows hitting multi-month highs. Coins moving to cold storage. Bullish.",
    "Long-term holders refusing to sell at these levels. They know something.",
    "Funding rates normalized after the flush — healthy foundation for the next leg.",
    "Derivatives de-leveraged, clearing the path for a spot-driven move up.",
    "Supply on exchanges at multi-year lows. Simple supply and demand.",
    "Spot ETF flows quietly building. Institutional demand is real.",
    "Network fundamentals strongest they've ever been. Price catches up eventually.",
]

_BEARISH_TAKES = [
    "Distribution pattern forming on the daily. Caution warranted here.",
    "Exchange inflows spiking — profit-taking could accelerate.",
    "Leverage building to uncomfortable levels. A flush may be needed.",
    "Macro headwinds could pressure all risk assets. Don't ignore the correlation.",
    "Bearish divergence on RSI. Price making highs, momentum fading.",
    "Realized profit-taking elevated. Historically leads to cooling periods.",
    "Market euphoria metrics climbing — usually a contrarian signal.",
    "Short-term holder cost basis acting as overhead resistance. Needs time.",
]

_NEUTRAL_TAKES = [
    "Low volatility compression usually precedes a big move. Stay ready.",
    "Range-bound markets test patience. But compression leads to expansion.",
    "Volume declining in the range — a breakout is loading. Direction unknown.",
    "Neither bulls nor bears in control. The next catalyst will decide it.",
    "Consolidation at these levels is constructive. Building a base.",
    "Market waiting for a macro trigger. Positioning light across the board.",
]


def generate_opinion_tweet() -> str | None:
    """
    Generate an opinion/analysis tweet.
    Tries AI first, falls back to template.
    """
    btc = _get_btc_data()
    if not btc:
        return None

    price = btc.get("current_price", 0)
    if not price or price <= 0:
        logger.warning("BTC price is zero/missing — skipping opinion tweet")
        return None

    pct_24h = btc.get("price_change_percentage_24h_in_currency") or 0
    pct_7d = btc.get("price_change_percentage_7d_in_currency") or 0

    coins = _get_top_coins_data()

    # Try AI first — enrich with DeFi TVL data when available
    if ai_writer.is_available():
        # Fetch DeFi TVL context for richer analysis
        defi_context = None
        try:
            tvl_data = defi_monitor.fetch_total_tvl()
            if tvl_data:
                defi_context = f"DeFi TVL: ${tvl_data.get('tvl', 0) / 1e9:.1f}B ({tvl_data.get('pct_24h', 0):+.1f}% 24h)"
        except Exception:
            pass
        ai_tweet = ai_writer.generate_opinion_tweet(price, pct_24h, pct_7d, coins,
                                                     defi_context=defi_context)
        if ai_tweet and len(ai_tweet) <= 280:
            logger.info("Using AI-generated opinion tweet")
            return ai_tweet

    if pct_24h > 1.5:
        take = random.choice(_BULLISH_TAKES)
        outlook = "Leaning bullish"
    elif pct_24h < -1.5:
        take = random.choice(_BEARISH_TAKES)
        outlook = "Risk elevated"
    else:
        take = random.choice(_NEUTRAL_TAKES)
        outlook = "Neutral — waiting"

    lines = [
        f"BTC at {_fmt_price(price)} — {_fmt_pct(pct_24h)} today, {_fmt_pct(pct_7d)} this week.",
        "",
        take,
        "",
        f"My read: {outlook}.",
    ]

    tweet = "\n".join(lines)
    if len(tweet) > 280:
        tweet = tweet[:277].rsplit("\n", 1)[0] + "..."
    return tweet


# ── Engagement tweet (question / discussion) ────────────────────────────────

_FALLBACK_QUESTIONS = [
    "BTC at {price} — are you adding here or waiting for a deeper pullback?",
    "Honest question: what's your biggest bag right now besides BTC?",
    "{price} BTC. Where do you think we close the week? Drop your number.",
    "Alts bleeding while BTC holds {price}. Rotation coming or more pain?",
    "What's your move at {price} BTC — accumulate, hold, or trim?",
    "BTC {pct_24h} today. Is this the dip you buy or the start of something worse?",
]


def generate_engagement_tweet() -> str | None:
    """
    Generate a question/discussion tweet designed to get replies.
    Tries AI first, falls back to templates.
    """
    btc = _get_btc_data()
    if not btc:
        return None

    price = btc.get("current_price", 0)
    if not price or price <= 0:
        return None

    pct_24h = btc.get("price_change_percentage_24h_in_currency") or 0
    pct_7d = btc.get("price_change_percentage_7d_in_currency") or 0
    coins = _get_top_coins_data()

    # Try AI first
    if ai_writer.is_available():
        ai_tweet = ai_writer.generate_engagement_tweet(price, pct_24h, pct_7d, coins)
        if ai_tweet and len(ai_tweet) <= 280:
            logger.info("Using AI-generated engagement tweet")
            return ai_tweet

    # Template fallback
    template = random.choice(_FALLBACK_QUESTIONS)
    return template.format(
        price=_fmt_price(price),
        pct_24h=_fmt_pct(pct_24h),
        pct_7d=_fmt_pct(pct_7d),
    )
