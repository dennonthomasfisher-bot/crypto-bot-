from typing import Optional
"""
ai_writer.py – Claude-powered tweet generation.

Provides three public functions:
  generate_news_tweet(story)         – concise tweet for a single news story
  generate_morning_recap(headlines)  – daily 08:00 UK market-summary tweet
  generate_quote_tweet(original)     – analyst-voice quote-tweet reply (⚠️ NFA)

Requires ANTHROPIC_API_KEY in .env.
Falls back to a plain-text summary if the API call fails.
"""

import logging
import time
import anthropic

import config

logger = logging.getLogger(__name__)

_client: Optional[anthropic.Anthropic] = None

MODEL = "claude-haiku-4-5-20251001"


def _get_client() -> anthropic.Anthropic:
    global _client
    if _client is None:
        _client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)
    return _client


def generate_news_tweet(story: dict) -> str:
    """
    Ask Claude to write a punchy tweet for a single crypto news story.
    Falls back to a plain formatted string if the API call fails.
    """
    title = story.get("title", "")
    url = story.get("url", "")

    if not config.ANTHROPIC_API_KEY:
        logger.debug("ANTHROPIC_API_KEY not set – using plain news tweet format")
        return _plain_news_tweet(title, url)

    prompt = (
        f"Write a punchy, engaging tweet (max 200 characters) summarising this crypto "
        f"news headline. Be direct and informative. "
        f"Do not use hashtags. Never include # symbols.\n\n"
        f"Headline: {title}\n\n"
        f"Output only the tweet text. No quotes, no commentary."
    )

    last_exc: Optional[anthropic.APIError] = None
    for attempt in range(1, 4):
        try:
            message = _get_client().messages.create(
                model=MODEL,
                max_tokens=120,
                messages=[{"role": "user", "content": prompt}],
            )
            tweet = message.content[0].text.strip()
            if len(tweet) > 200:
                tweet = tweet[:199].rsplit(" ", 1)[0] + "…"
            # Append URL on a new line if it fits
            candidate = f"{tweet}\n{url}" if url else tweet
            if len(candidate) <= 280:
                return candidate
            return tweet[:277 - len(url) - 1].rsplit(" ", 1)[0] + f"…\n{url}" if url else tweet
        except anthropic.APIError as exc:
            last_exc = exc
            logger.warning("Claude API error (attempt %d/3) generating news tweet: %s", attempt, exc)
            if attempt < 3:
                time.sleep(5)

    logger.warning("All 3 attempts failed – falling back to plain tweet")
    return _plain_news_tweet(title, url)


def generate_morning_recap(headlines: list[str]) -> str:
    """
    Ask Claude to write a punchy morning market-summary tweet (max 220 chars)
    based on the top 3 recent crypto headlines.
    Falls back to a plain bullet summary if the API call fails.
    """
    if not headlines:
        return "☀️ Good morning! Crypto markets are open. Stay sharp."

    numbered = "\n".join(f"{i+1}. {h}" for i, h in enumerate(headlines[:3]))

    if not config.ANTHROPIC_API_KEY:
        logger.debug("ANTHROPIC_API_KEY not set – using plain morning recap format")
        return _plain_morning_recap(headlines)

    prompt = (
        "Write a punchy morning crypto market summary tweet (max 220 characters). "
        "Start with ☀️. Summarise the key themes from the headlines in one sentence. "
        "Do not use hashtags. Never include # symbols. "
        "Output only the tweet text. No quotes, no commentary.\n\n"
        f"Today's top headlines:\n{numbered}"
    )

    try:
        message = _get_client().messages.create(
            model=MODEL,
            max_tokens=100,
            messages=[{"role": "user", "content": prompt}],
        )
        tweet = message.content[0].text.strip()
        return tweet[:220]
    except anthropic.APIError as exc:
        logger.warning("Claude API error generating morning recap: %s", exc)
        return _plain_morning_recap(headlines)


def generate_quote_tweet(original_text: str) -> str:
    """
    Ask Claude to write a smart quote-tweet reply in a crypto analyst voice.
    Max 220 chars, always ends with ⚠️ NFA.
    Falls back to a plain comment if the API call fails.
    """
    if not config.ANTHROPIC_API_KEY:
        logger.debug("ANTHROPIC_API_KEY not set – using plain quote tweet format")
        return _plain_quote_tweet(original_text)

    prompt = (
        "You are a sharp crypto market analyst. Write a quote-tweet reply to the tweet below. "
        "Be insightful, add genuine context or a contrarian angle. "
        "Max 220 characters total. End with ⚠️ NFA on the same line. "
        "Output only the reply text. No quotes, no commentary.\n\n"
        f"Tweet to quote:\n{original_text}"
    )

    try:
        message = _get_client().messages.create(
            model=MODEL,
            max_tokens=100,
            messages=[{"role": "user", "content": prompt}],
        )
        reply = message.content[0].text.strip()
        return reply[:220]
    except anthropic.APIError as exc:
        logger.warning("Claude API error generating quote tweet: %s", exc)
        return _plain_quote_tweet(original_text)


def generate_trending_tweet(coin: dict, price_usd: float) -> str:
    """
    Generate a tweet about a CoinGecko trending coin.

    Parameters
    ----------
    coin : dict
        Item from CoinGecko /search/trending response; expected keys:
        name, symbol, market_cap_rank.
    price_usd : float
        Current price in USD fetched from CoinGecko – passed explicitly so
        the AI only references realistic price levels, never fabricated ones.

    Returns
    -------
    str  Tweet string (≤ 200 chars), or a plain fallback string on AI error.
    """
    name   = coin.get("name", "Unknown")
    symbol = str(coin.get("symbol", "???")).upper()
    rank   = coin.get("market_cap_rank") or "?"

    if not config.ANTHROPIC_API_KEY:
        return f"🔥 {name} ({symbol}) is spiking on CoinGecko trending — current price ${price_usd:,.4f}"

    prompt = (
        f"Write a punchy tweet about a coin surging on CoinGecko's trending chart.\n\n"
        f"Coin:            {name} ({symbol})\n"
        f"Market cap rank: #{rank}\n"
        f"Current price:   ${price_usd:,.4f}\n"
        f"Status:          Spiking on CoinGecko trending search right now\n\n"
        f"Rules:\n"
        f"- Max 200 characters\n"
        f"- Start with 🔥\n"
        f"- Include the exact current price (${price_usd:,.4f})\n"
        f"- Current price is ${price_usd:,.4f}. Only mention price targets within 20% of this. Do not invent price levels.\n"
        f"- State one specific thing to watch: a key level near the current price, a catalyst, or a pattern\n"
        f"- Direct and opinionated — write like a sharp market observer, not a press release\n"
        f"- Do not use hashtags. Never include # symbols.\n\n"
        f"Output only the tweet text."
    )

    try:
        message = _get_client().messages.create(
            model=MODEL,
            max_tokens=100,
            messages=[{"role": "user", "content": prompt}],
        )
        result = message.content[0].text.strip()
        if len(result) > 200:
            result = result[:199].rsplit(" ", 1)[0] + "…"
        return result
    except anthropic.APIError as exc:
        logger.warning("Claude API error generating trending tweet: %s", exc)
        return f"🔥 {name} ({symbol}) is spiking on CoinGecko trending — current price ${price_usd:,.4f}"


def generate_opinion_tweet() -> str:
    """
    Generate a bold, conviction-style opinion tweet about BTC, ETH, or macro
    crypto.  Fired once daily at 12:00 UK time.  Sounds like a sharp trader
    making a conviction call, not a journalist reporting news.
    """
    _OPINION_FALLBACK = (
        "BTC structure is tightening. Every squeeze like this has resolved to the upside "
        "in a bull cycle. Bias stays long until proven otherwise. ⚠️ NFA"
    )

    if not config.ANTHROPIC_API_KEY:
        return _OPINION_FALLBACK

    prompt = (
        "You are a seasoned crypto trader with strong conviction. "
        "Write a bold, opinionated midday tweet expressing a clear market view on "
        "Bitcoin, Ethereum, or macro crypto conditions. "
        "Take a definitive stance — bullish, bearish, or a specific structural call. "
        "Sound like a sharp, confident trader, not a journalist. "
        "Max 220 characters. End with ⚠️ NFA. "
        "Do not use hashtags. Never include # symbols. "
        "Output only the tweet text. No quotes, no commentary."
    )

    try:
        message = _get_client().messages.create(
            model=MODEL,
            max_tokens=100,
            messages=[{"role": "user", "content": prompt}],
        )
        result = message.content[0].text.strip()
        return result[:220]
    except anthropic.APIError as exc:
        logger.warning("Claude API error generating opinion tweet: %s", exc)
        return _OPINION_FALLBACK


# ── Plain-text fallbacks ──────────────────────────────────────────────────────

def _plain_news_tweet(title: str, url: str) -> str:
    max_title = 200
    if len(title) > max_title:
        title = title[:max_title - 1] + "…"
    parts = [f"📰 {title}", url]
    return "\n".join(p for p in parts if p)


def _plain_morning_recap(headlines: list[str]) -> str:
    intro = "☀️ Morning crypto update:"
    items = " | ".join(h[:60] for h in headlines[:3])
    tweet = f"{intro} {items}"
    return tweet[:220]


def _plain_quote_tweet(original_text: str) -> str:
    snippet = original_text[:80].rsplit(" ", 1)[0] + "…" if len(original_text) > 80 else original_text
    return f"Worth watching — {snippet} ⚠️ NFA"
