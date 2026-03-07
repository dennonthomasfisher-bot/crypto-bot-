"""
AI-powered tweet writer using Claude (Anthropic API).

Generates unique, natural-sounding crypto tweets using live market data.
Falls back gracefully if the API key is missing or calls fail.
"""
from __future__ import annotations

import logging
import random
import re

import config

logger = logging.getLogger(__name__)

_client = None
_available: bool | None = None  # None = not checked yet

# Track recent tweets to prevent repetition
_recent_tweets: list[str] = []
_MAX_RECENT = 10


def record_recent_tweet(text: str) -> None:
    """Store a recent tweet so the AI can avoid repeating itself."""
    _recent_tweets.append(text[:150])
    if len(_recent_tweets) > _MAX_RECENT:
        _recent_tweets.pop(0)


def _get_recent_context() -> str:
    """Build a 'do not repeat' block for prompts."""
    if not _recent_tweets:
        return ""
    recent = "\n".join(f"  - {t}" for t in _recent_tweets[-5:])
    return (
        f"\n\nRECENT TWEETS (do NOT repeat similar topics, angles, phrasing, or structure):\n{recent}\n\n"
        "CRITICAL: If your recent tweets are mostly about Bitcoin price commentary, "
        "do NOT write another Bitcoin price commentary. Write about something completely different — "
        "an altcoin, a narrative, a question, a macro take, or a contrarian opinion. "
        "Variety is more important than covering the latest BTC move."
    )


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
        # Strip hashtags — the AI sometimes adds them despite instructions
        text = _strip_hashtags(text)
        return text
    except Exception as exc:
        logger.warning("Claude API call failed: %s", exc)
        return None


def _strip_hashtags(text: str) -> str:
    """Remove any hashtags the AI included despite instructions."""
    # Remove standalone hashtag words (e.g. #Bitcoin, #BTC)
    text = re.sub(r'\s*#\w+', '', text)
    # Clean up any trailing whitespace or blank lines left behind
    text = re.sub(r'\n\s*\n\s*$', '', text).strip()
    return text


# ── System prompt for all tweet generation ──────────────────────────────────

_SYSTEM = """You are the voice behind @CoinWatchAlert on Twitter. You sound like a real trader sharing thoughts — not a bot, not a news feed, not a hype account.

ABSOLUTE RULES (break any of these and the tweet is rejected):
- Tweet MUST be under 275 characters
- ZERO hashtags. No #Bitcoin, no #BTC, no #Crypto, no hashtags of ANY kind. They destroy reach on X/Twitter. If you include even one hashtag, the tweet will be deleted.
- NO emojis like 🚀🔥💰📈. You can use 🟢 or 🔴 for price direction, that's it
- Always include the actual price data provided — never fabricate numbers
- Sound like you're texting a group chat of trader friends, not writing a headline
- Vary your openings — don't always start with "BTC" or the price
- No disclaimers, no "NFA", no "DYOR", no "not financial advice"
- No "to the moon", "WAGMI", "LFG", or crypto bro speak
- Write in a natural, conversational tone — confident but not arrogant
- Use line breaks sparingly for readability
- Do NOT wrap your response in quotes
- Never start tweets with a symbol like $BTC or #BTC
- NEVER use hashtags. This is repeated because it is critical."""


# ── Diverse content categories for quote tweets ─────────────────────────────
# Each category produces a genuinely different kind of tweet, not just
# a different angle on "BTC is at $X".

QUOTE_CATEGORIES = {
    "btc_price": {
        "label": "BTC price action",
        "instruction": (
            "Write a short BTC price action take. What level matters next? "
            "Include the price but focus on the setup, not just the number."
        ),
    },
    "alt_spotlight": {
        "label": "Altcoin spotlight",
        "instruction": (
            "Focus on the ALTCOINS, not Bitcoin. Which alt is doing something "
            "interesting? Lead with the alt, not BTC. If alts are boring, talk about "
            "why — dominance, rotation, risk-off. Do NOT lead with BTC price."
        ),
    },
    "macro_narrative": {
        "label": "Macro / narrative",
        "instruction": (
            "Write about the BIGGER PICTURE — macro, narratives, or a trend. "
            "Examples: ETF flows, institutional adoption, DXY correlation, "
            "regulation, halving cycle positioning. Don't just comment on today's price. "
            "Think bigger than a 24h candle."
        ),
    },
    "contrarian_take": {
        "label": "Contrarian / hot take",
        "instruction": (
            "Write a CONTRARIAN take. Disagree with something most of Crypto Twitter "
            "believes right now. Be provocative but back it with the data provided. "
            "End with something that invites debate."
        ),
    },
    "trader_question": {
        "label": "Question / poll",
        "instruction": (
            "Ask your followers a QUESTION. Make it specific and easy to reply to. "
            "Use the data to frame the question but end with something people can "
            "answer quickly. Examples: 'Are you adding here or waiting?', "
            "'What's your biggest bag right now?', 'Where do we close the week?'"
        ),
    },
    "market_structure": {
        "label": "Market structure / on-chain",
        "instruction": (
            "Write about market STRUCTURE — funding rates, exchange flows, leverage, "
            "liquidations, whale behavior, or on-chain signals. Don't just state the "
            "price — interpret what the structure is telling you."
        ),
    },
}


def _pick_quote_category(recent_categories: list[str]) -> str:
    """Pick a content category that hasn't been used recently."""
    all_cats = list(QUOTE_CATEGORIES.keys())
    # Exclude categories used in the last 3 posts
    recent_set = set(recent_categories[-3:])
    available = [c for c in all_cats if c not in recent_set]
    if not available:
        # All used recently — just exclude the very last one
        last = recent_categories[-1] if recent_categories else ""
        available = [c for c in all_cats if c != last]
    if not available:
        available = all_cats
    return random.choice(available)


def generate_quote_tweet(price: float, pct_24h: float, pct_7d: float,
                         market_cap: float, coins_data: list[dict] | None = None,
                         forced_category: str | None = None) -> tuple[str | None, str]:
    """Generate an AI-written market tweet.

    Returns (tweet_text, category) so the caller can record the category.
    """
    import state

    # Build coin context
    coin_lines = ""
    if coins_data:
        for c in coins_data[:5]:
            sym = c.get("symbol", "?").upper()
            cp = c.get("current_price", 0)
            cpct = c.get("price_change_percentage_24h_in_currency") or 0
            coin_lines += f"  {sym}: ${cp:,.2f} ({cpct:+.1f}%)\n"

    mcap_str = f"${market_cap / 1e12:.2f}T" if market_cap >= 1e12 else f"${market_cap / 1e9:.0f}B"

    # Pick a category that's different from recent ones
    recent_cats = state.get_recent_categories()
    category = forced_category or _pick_quote_category(recent_cats)
    cat_info = QUOTE_CATEGORIES[category]

    prompt = f"""Write a crypto tweet. Your SPECIFIC assignment: {cat_info['instruction']}

Live market data (use what's relevant to your angle):
BTC Price: ${price:,.0f} | 24h: {pct_24h:+.1f}% | 7d: {pct_7d:+.1f}% | MCap: {mcap_str}
{f"Coins:{chr(10)}{coin_lines}" if coin_lines else ""}

IMPORTANT RULES:
- NO hashtags. Zero. They kill reach
- Sound like a real trader, not a news bot or price ticker
- Under 275 characters
- Do NOT just restate the BTC price and add a generic comment
- If your assignment is about alts or narratives, LEAD with that — not "BTC at $X"
{_get_recent_context()}
Write the tweet now. Nothing else."""

    tweet = _call_claude(_SYSTEM, prompt)
    return tweet, category


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
{_get_recent_context()}
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
{_get_recent_context()}
Write the tweet now. Nothing else."""

    return _call_claude(_SYSTEM, prompt)


def generate_engagement_tweet(price: float, pct_24h: float, pct_7d: float,
                              coins_data: list[dict] | None = None) -> str | None:
    """Generate a question/discussion tweet designed to get replies."""
    coin_context = ""
    if coins_data:
        for c in coins_data[:5]:
            sym = c.get("symbol", "?").upper()
            cpct = c.get("price_change_percentage_24h_in_currency") or 0
            coin_context += f"  {sym}: {cpct:+.1f}%\n"

    style = random.choice([
        "Ask a direct question about what traders are doing (buying, selling, holding)",
        "Present two scenarios and ask which one people think plays out",
        "Make a slightly controversial take and invite disagreement",
        "Ask about a specific level — will BTC hold it or lose it?",
        "Ask what coin people are most bullish on right now and why",
    ])

    prompt = f"""Write a crypto tweet that's designed to get people to REPLY.

BTC Price: ${price:,.0f}
24h Change: {pct_24h:+.1f}%
7d Change: {pct_7d:+.1f}%
{f"Coin performance:{chr(10)}{coin_context}" if coin_context else ""}

Style: {style}

Key rules:
- End with a clear question that people can answer quickly
- Include real price data so it feels timely
- Keep it under 250 characters — shorter tweets get more replies
- Sound like a trader polling their community, not a survey bot
- NO hashtags
- Make it easy to reply — yes/no questions or "A or B" choices work great
{_get_recent_context()}
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
