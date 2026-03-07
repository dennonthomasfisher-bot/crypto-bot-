"""
AI-powered tweet writer using Claude (Anthropic API).

Generates unique, natural-sounding crypto tweets using live market data.
Falls back gracefully if the API key is missing or calls fail.
"""
from __future__ import annotations

import logging
import random

import config

logger = logging.getLogger(__name__)

_client = None
_available: bool | None = None  # None = not checked yet


def _get_client():
    """Lazy-init the Anthropic client."""
    global _client, _available
    if _available is False:
        return None
    if _client is not None:
        return _client
    if not config.ANTHROPIC_API_KEY:
        logger.info("ANTHROPIC_API_KEY not set — AI tweet generation disabled, using templates.")
        _available = False
        return None
    try:
        import anthropic
        _client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)
        _available = True
        logger.info("Anthropic API client initialized.")
        return _client
    except ImportError:
        logger.warning("anthropic package not installed — run: pip install anthropic")
        _available = False
        return None
    except Exception as exc:
        logger.warning("Failed to init Anthropic client: %s", exc)
        _available = False
        return None


def is_available() -> bool:
    """Check if AI writing is available."""
    _get_client()
    return _available is True


def _call_claude(system_prompt: str, user_prompt: str, max_tokens: int = 300) -> str | None:
    """Make a Claude API call and return the text response."""
    client = _get_client()
    if client is None:
        return None
    try:
        response = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=max_tokens,
            system=system_prompt,
            messages=[{"role": "user", "content": user_prompt}],
        )
        text = response.content[0].text.strip()
        # Remove quotes if Claude wrapped the tweet in them
        if text.startswith('"') and text.endswith('"'):
            text = text[1:-1]
        if text.startswith("'") and text.endswith("'"):
            text = text[1:-1]
        return text
    except Exception as exc:
        logger.warning("Claude API call failed: %s", exc)
        return None


# ── System prompt for all tweet generation ──────────────────────────────────

_SYSTEM = """You are the voice behind @CoinWatchAlert, a crypto Twitter account known for sharp, data-driven market analysis. Your tweets are concise, confident, and backed by real numbers.

Rules:
- Tweet MUST be under 270 characters (leave room for hashtags)
- Always include the actual price data provided — never make up numbers
- Sound like a knowledgeable crypto analyst, not a hype account
- No generic filler — every sentence should add value
- Use line breaks for readability
- End with 2-4 relevant hashtags on a new line (always include #Bitcoin or #Crypto)
- Never use "🚀" emoji or say "to the moon" or "WAGMI"
- No disclaimers, no "NFA", no "DYOR"
- Write in a direct, assertive tone — like a trader's note, not a news article
- Do NOT wrap your response in quotes"""


def generate_quote_tweet(price: float, pct_24h: float, pct_7d: float,
                         market_cap: float, coins_data: list[dict] | None = None) -> str | None:
    """Generate an AI-written market analysis tweet."""
    # Build context for Claude
    coin_lines = ""
    if coins_data:
        for c in coins_data[:5]:
            sym = c.get("symbol", "?").upper()
            cp = c.get("current_price", 0)
            cpct = c.get("price_change_percentage_24h_in_currency") or 0
            coin_lines += f"  {sym}: ${cp:,.2f} ({cpct:+.1f}%)\n"

    mcap_str = f"${market_cap / 1e12:.2f}T" if market_cap >= 1e12 else f"${market_cap / 1e9:.0f}B"

    prompt = f"""Write a crypto market analysis tweet using this live data:

BTC Price: ${price:,.0f}
24h Change: {pct_24h:+.1f}%
7d Change: {pct_7d:+.1f}%
Market Cap: {mcap_str}
{f"Top coins:{chr(10)}{coin_lines}" if coin_lines else ""}

Pick ONE angle (don't try to cover everything):
- Price action & key levels
- On-chain insight (exchange flows, holder behavior, hash rate)
- Market structure / sentiment read
- Multi-coin snapshot with BTC + top movers

Write the tweet now. Nothing else."""

    return _call_claude(_SYSTEM, prompt)


def generate_opinion_tweet(price: float, pct_24h: float, pct_7d: float) -> str | None:
    """Generate an AI-written opinion/analysis tweet."""
    prompt = f"""Write an opinionated crypto analysis tweet using this data:

BTC Price: ${price:,.0f}
24h Change: {pct_24h:+.1f}%
7d Change: {pct_7d:+.1f}%

Take a clear stance — bullish, bearish, or neutral with conviction.
Include specific reasoning (on-chain, technical, macro).
Start with the price, then give your take.

Write the tweet now. Nothing else."""

    return _call_claude(_SYSTEM, prompt)


def generate_morning_recap(btc_data: dict, top_coins: list[dict]) -> str | None:
    """Generate an AI-written morning market recap."""
    btc_price = btc_data.get("current_price", 0)
    btc_24h = btc_data.get("price_change_percentage_24h_in_currency") or 0

    coin_lines = ""
    for c in top_coins[:6]:
        sym = c.get("symbol", "?").upper()
        cp = c.get("current_price", 0)
        cpct = c.get("price_change_percentage_24h_in_currency") or 0
        coin_lines += f"  {sym}: ${cp:,.2f} ({cpct:+.1f}%)\n"

    green = sum(1 for c in top_coins if (c.get("price_change_percentage_24h_in_currency") or 0) > 0)

    prompt = f"""Write a morning crypto market recap tweet using this data:

BTC: ${btc_price:,.0f} ({btc_24h:+.1f}% 24h)
Top coins:
{coin_lines}
Market: {green}/{len(top_coins)} coins green

Format as a clean briefing:
- Start with "GM" or a morning greeting
- Show BTC + ETH prices
- Highlight 1-2 biggest movers
- One-line market summary
- End with hashtags including #Crypto and #Bitcoin

Write the tweet now. Nothing else."""

    return _call_claude(_SYSTEM, prompt)


def generate_reply(btc_price: float, pct_24h: float, original_tweet: str) -> str | None:
    """Generate an AI-written reply to a crypto tweet."""
    prompt = f"""Write a reply to this crypto tweet:

"{original_tweet[:200]}"

Current BTC data: ${btc_price:,.0f} ({pct_24h:+.1f}% 24h)

Rules for the reply:
- Keep it under 200 characters
- Add value — include a data point or insight
- Don't be generic or sycophantic
- Sound like a fellow trader adding to the conversation
- NO hashtags in replies
- Be conversational, not formal

Write the reply now. Nothing else."""

    system = """You are @CoinWatchAlert replying to other crypto traders. Your replies are brief, data-informed, and add to the conversation. Never be generic — always reference either the data or a specific point from their tweet."""

    return _call_claude(system, prompt, max_tokens=150)
