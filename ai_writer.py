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

_SYSTEM = """You are the voice behind @CoinWatchAlert on Twitter. You sound like a real trader sharing thoughts — not a bot, not a news feed, not a hype account.

Rules:
- Tweet MUST be under 275 characters
- NO hashtags. Ever. Zero. They kill reach on X/Twitter now
- NO emojis like 🚀🔥💰📈. You can use 🟢 or 🔴 for price direction, that's it
- Always include the actual price data provided — never fabricate numbers
- Sound like you're texting a group chat of trader friends, not writing a headline
- Vary your openings — don't always start with "BTC" or the price
- No disclaimers, no "NFA", no "DYOR", no "not financial advice"
- No "to the moon", "WAGMI", "LFG", or crypto bro speak
- Write in a natural, conversational tone — confident but not arrogant
- Use line breaks sparingly for readability
- Do NOT wrap your response in quotes
- Never start tweets with a symbol like $BTC or #BTC"""


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

    prompt = f"""Write a crypto market tweet using this live data:

BTC Price: ${price:,.0f}
24h Change: {pct_24h:+.1f}%
7d Change: {pct_7d:+.1f}%
Market Cap: {mcap_str}
{f"Top coins:{chr(10)}{coin_lines}" if coin_lines else ""}

Pick ONE angle (don't try to cover everything):
- Price action and what level matters next
- What the smart money is doing (exchange flows, accumulation patterns)
- Sentiment and positioning (funding, leverage, fear/greed)
- Quick multi-coin check if alts are doing something interesting

Remember: NO hashtags, sound like a human trader, not a news bot.

Write the tweet now. Nothing else."""

    return _call_claude(_SYSTEM, prompt)


def generate_opinion_tweet(price: float, pct_24h: float, pct_7d: float) -> str | None:
    """Generate an AI-written opinion/analysis tweet."""
    prompt = f"""Write an opinionated crypto tweet using this data:

BTC Price: ${price:,.0f}
24h Change: {pct_24h:+.1f}%
7d Change: {pct_7d:+.1f}%

Take a clear stance. Say something a real trader would say to their followers.
Include specific reasoning — what you're watching, what concerns you, what excites you.
Don't just restate the numbers. Interpret them.

NO hashtags. Sound human.

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

    prompt = f"""Write a morning crypto recap tweet using this data:

BTC: ${btc_price:,.0f} ({btc_24h:+.1f}% 24h)
Top coins:
{coin_lines}
Market: {green}/{len(top_coins)} coins green

Keep it clean and scannable:
- Quick morning greeting (GM, morning, etc.)
- BTC + ETH prices with direction
- Mention 1-2 interesting movers
- One-line vibe check on the market

NO hashtags. Sound like a trader checking in with their followers.

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
