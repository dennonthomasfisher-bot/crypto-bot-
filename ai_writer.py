from typing import Optional
"""
ai_writer.py – Claude-powered tweet generation.

Provides public functions:
  generate_news_tweet(story)          – concise tweet for a single news story
  generate_morning_recap(headlines)   – daily 08:00 UK market-summary tweet
  generate_quote_tweet(original)      – analyst-voice quote-tweet reply
  generate_trending_tweet(coin, price)– trending-coin observation tweet
  generate_opinion_tweet()            – daily 12:00 UK macro observation tweet
  generate_account_reply(text, user)  – reply to a major crypto account tweet

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

# ── Global content rules injected into every prompt ──────────────────────────
GLOBAL_RULES = """\
Absolute rules — every violation disqualifies the output:
- NEVER use NFA, N.F.A, 'not financial advice', or any variant
- NEVER use the ⚠️ emoji
- NEVER use hashtags or # symbols
- NEVER use exclamation marks
- NEVER use 'I think', 'I believe', or sycophantic openers (Great, Interesting, etc.)
- NEVER include specific price targets or forecasted dollar levels
- NEVER make buy, sell, stack, or accumulate calls
- Max 220 characters total
- One clear thought — do not cram multiple ideas
- End cleanly — no trailing emojis, no disclaimers, no tags
- If using an emoji, use at most 1, placed naturally mid-sentence or at the start, never at the end
- Allowed emojis only: 🚀 📉 ⚡ 👀
- Tone: sharp, confident, analytical — short punchy sentences, real market participant voice
"""


def _get_client() -> anthropic.Anthropic:
    global _client
    if _client is None:
        _client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)
    return _client


def _truncate(text: str, limit: int = 220) -> str:
    """Clean word-boundary truncation."""
    if len(text) <= limit:
        return text
    return text[:limit - 1].rsplit(" ", 1)[0] + "…"


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
        f"Write a concise, analytical tweet summarising this crypto news headline.\n\n"
        f"Headline: {title}\n\n"
        f"{GLOBAL_RULES}\n"
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
            tweet = _truncate(message.content[0].text.strip())
            candidate = f"{tweet}\n{url}" if url else tweet
            if len(candidate) <= 280:
                return candidate
            return _truncate(tweet, 277 - len(url) - 1) + f"\n{url}" if url else tweet
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
        return "Morning crypto update: markets are open. Stay sharp."

    numbered = "\n".join(f"{i+1}. {h}" for i, h in enumerate(headlines[:3]))

    if not config.ANTHROPIC_API_KEY:
        logger.debug("ANTHROPIC_API_KEY not set – using plain morning recap format")
        return _plain_morning_recap(headlines)

    prompt = (
        "Write a morning crypto market summary tweet that captures the key theme "
        "from today's top headlines in one sharp sentence.\n\n"
        f"Today's top headlines:\n{numbered}\n\n"
        f"{GLOBAL_RULES}\n"
        "Output only the tweet text. No quotes, no commentary."
    )

    try:
        message = _get_client().messages.create(
            model=MODEL,
            max_tokens=100,
            messages=[{"role": "user", "content": prompt}],
        )
        return _truncate(message.content[0].text.strip())
    except anthropic.APIError as exc:
        logger.warning("Claude API error generating morning recap: %s", exc)
        return _plain_morning_recap(headlines)


def generate_quote_tweet(original_text: str) -> str:
    """
    Ask Claude to write a smart quote-tweet reply in a crypto analyst voice.
    Max 220 chars.
    Falls back to a plain comment if the API call fails.
    """
    if not config.ANTHROPIC_API_KEY:
        logger.debug("ANTHROPIC_API_KEY not set – using plain quote tweet format")
        return _plain_quote_tweet(original_text)

    prompt = (
        "You are a sharp crypto market analyst. Write a quote-tweet reply to the "
        "tweet below. Be insightful — add genuine context or a contrarian angle.\n\n"
        f"Tweet to quote:\n{original_text}\n\n"
        f"{GLOBAL_RULES}\n"
        "Output only the reply text. No quotes, no commentary."
    )

    try:
        message = _get_client().messages.create(
            model=MODEL,
            max_tokens=100,
            messages=[{"role": "user", "content": prompt}],
        )
        return _truncate(message.content[0].text.strip())
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
        the AI references only the real current price, never fabricated levels.
    """
    name   = coin.get("name", "Unknown")
    symbol = str(coin.get("symbol", "???")).upper()
    rank   = coin.get("market_cap_rank") or "?"

    if not config.ANTHROPIC_API_KEY:
        return f"⚡ {name} ({symbol}) appearing on CoinGecko trending — current price ${price_usd:,.4f}"

    prompt = (
        f"Write a tweet observing that {name} ({symbol}) is trending on CoinGecko.\n\n"
        f"Market cap rank: #{rank}\n"
        f"Current price:   ${price_usd:,.4f}\n\n"
        f"You may reference the current price (${price_usd:,.4f}) as a factual data point. "
        f"Do not invent or forecast any price levels beyond what is stated above.\n\n"
        f"{GLOBAL_RULES}\n"
        f"Output only the tweet text."
    )

    try:
        message = _get_client().messages.create(
            model=MODEL,
            max_tokens=100,
            messages=[{"role": "user", "content": prompt}],
        )
        return _truncate(message.content[0].text.strip())
    except anthropic.APIError as exc:
        logger.warning("Claude API error generating trending tweet: %s", exc)
        return f"⚡ {name} ({symbol}) appearing on CoinGecko trending — current price ${price_usd:,.4f}"


def generate_opinion_tweet() -> str:
    """
    Generate a macro observation tweet about BTC, ETH, or crypto market
    conditions.  Fired once daily at 12:00 UK time.  Analytical tone only —
    no price targets, no trade calls, no directional predictions.
    """
    _OPINION_FALLBACK = (
        "BTC open interest is elevated while spot volume stays muted — "
        "leverage-driven moves with low conviction tend to unwind fast."
    )

    if not config.ANTHROPIC_API_KEY:
        return _OPINION_FALLBACK

    prompt = (
        "You are a seasoned crypto market analyst. Write a midday analytical "
        "observation about Bitcoin, Ethereum, or macro crypto conditions.\n\n"
        "Focus only on: market structure, sentiment, on-chain data, or macro context. "
        "Observe what IS happening — not what will happen.\n\n"
        "BANNED topics — do not write about these under any circumstances:\n"
        "- Bitcoin moving averages or the 200-day MA\n"
        "- Institutional flows or ETF inflows/outflows\n"
        "These are overused. Pick a fresh angle instead. Choose exactly one from:\n"
        "macro correlation (crypto vs equities, DXY, rates), on-chain metrics "
        "(active addresses, exchange reserves, SOPR, miner flows), market "
        "microstructure (bid-ask spreads, order book depth, liquidation levels), "
        "altcoin rotation patterns, stablecoin flows (USDT/USDC mint/burn), "
        "funding rates, or derivatives positioning (open interest, put/call ratio, "
        "perp basis). Every opinion tweet must cover a different topic.\n\n"
        f"{GLOBAL_RULES}\n"
        "Output only the tweet text. No quotes, no commentary."
    )

    try:
        message = _get_client().messages.create(
            model=MODEL,
            max_tokens=120,
            messages=[{"role": "user", "content": prompt}],
        )
        return _truncate(message.content[0].text.strip())
    except anthropic.APIError as exc:
        logger.warning("Claude API error generating opinion tweet: %s", exc)
        return _OPINION_FALLBACK


def generate_account_reply(tweet_text: str, author_username: str) -> str:
    """
    Generate a sharp, in-depth reply to a tweet from a major crypto account.

    Tone: experienced trader/analyst — adds genuine insight, references market
    data or on-chain context where relevant, sounds human.
    Length: 2-3 sentences, max 280 characters.
    """
    _FALLBACK = (
        "Watching this closely — every macro shift right now gets amplified "
        "by thin liquidity. Worth keeping on the radar. 👀"
    )

    if not config.ANTHROPIC_API_KEY:
        return _FALLBACK

    prompt = (
        f"You are a sharp, experienced crypto analyst replying to a tweet from "
        f"@{author_username}. Write a 2-3 sentence reply that adds genuine "
        f"analytical value — reference market data or on-chain context where "
        f"relevant. Build on the tweet rather than just agreeing with it.\n\n"
        f"Tweet to reply to:\n{tweet_text}\n\n"
        f"{GLOBAL_RULES}\n"
        f"Additional rule for replies: max 280 characters (not 220).\n"
        f"Output only the reply text. No quotes, no commentary."
    )

    try:
        message = _get_client().messages.create(
            model=MODEL,
            max_tokens=120,
            messages=[{"role": "user", "content": prompt}],
        )
        return _truncate(message.content[0].text.strip(), limit=280)
    except anthropic.APIError as exc:
        logger.warning("Claude API error generating account reply: %s", exc)
        return _FALLBACK


def generate_polymarket_tweet(market: dict) -> str:
    """
    Generate an analytical tweet about a Polymarket prediction market.

    Interprets what the current odds imply for market sentiment and what
    it means for the crypto space. 1-2 sentences, max 220 chars.

    market dict keys: question, yes_price, volume, [yes_prev, shift, direction]
    """
    question  = market.get("question", "")
    yes_pct   = round(market["yes_price"] * 100)
    volume    = market.get("volume", 0)

    if volume >= 1_000_000:
        vol_str = f"${volume / 1_000_000:.1f}M"
    elif volume >= 1_000:
        vol_str = f"${volume / 1_000:.0f}K"
    else:
        vol_str = "low"

    shift_context = ""
    if "shift" in market:
        shift_pct = round(market["shift"] * 100)
        direction = market.get("direction", "")
        shift_context = (
            f"\nThis market's YES probability just moved {direction} "
            f"by {shift_pct} percentage points."
        )

    # Plain fallback (no API key or on error)
    no_str = round(100 - market["yes_price"] * 100)
    _FALLBACK = (
        f"Polymarket is pricing '{question}' at {yes_pct}% YES / {no_str}% NO "
        f"on {vol_str} volume."
    )
    if len(_FALLBACK) > 220:
        _FALLBACK = _truncate(_FALLBACK)

    if not config.ANTHROPIC_API_KEY:
        return _FALLBACK

    prompt = (
        f"A Polymarket prediction market is currently priced as follows:\n\n"
        f"Question: {question}\n"
        f"YES probability: {yes_pct}%\n"
        f"Volume: {vol_str}{shift_context}\n\n"
        f"Write 1-2 sentences analysing what these odds are pricing in and "
        f"what the implied probability signals about market sentiment or "
        f"the likely outcome for crypto.\n\n"
        f"{GLOBAL_RULES}\n"
        f"Output only the tweet text. No quotes, no commentary."
    )

    try:
        message = _get_client().messages.create(
            model=MODEL,
            max_tokens=120,
            messages=[{"role": "user", "content": prompt}],
        )
        return _truncate(message.content[0].text.strip())
    except anthropic.APIError as exc:
        logger.warning("Claude API error generating polymarket tweet: %s", exc)
        return _FALLBACK


# ── Plain-text fallbacks ──────────────────────────────────────────────────────

def _plain_news_tweet(title: str, url: str) -> str:
    max_title = 200
    if len(title) > max_title:
        title = title[:max_title - 1] + "…"
    parts = [f"📰 {title}", url]
    return "\n".join(p for p in parts if p)


def _plain_morning_recap(headlines: list[str]) -> str:
    intro = "Morning crypto update:"
    items = " | ".join(h[:60] for h in headlines[:3])
    return _truncate(f"{intro} {items}")


def _plain_quote_tweet(original_text: str) -> str:
    snippet = original_text[:80].rsplit(" ", 1)[0] + "…" if len(original_text) > 80 else original_text
    return f"Worth watching — {snippet}"
