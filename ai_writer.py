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

# Coins to deprioritize (set by bot.py before generation)
_coins_to_avoid: set[str] = set()


def set_coins_to_avoid(coins: set[str]) -> None:
    """Set coins that should be deprioritized in the next generation."""
    global _coins_to_avoid
    _coins_to_avoid = coins


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


def _sentiment_nudge() -> str:
    """If recent tweets are too one-sided, return an instruction to balance."""
    import state
    recent = state.get_recent_sentiments()
    if len(recent) < 3:
        return ""
    last_five = recent[-5:]
    bearish_count = last_five.count("bearish")
    bullish_count = last_five.count("bullish")
    if bearish_count >= 3:
        return (
            "\n\nIMPORTANT: Your recent tweets have all been bearish. "
            "This tweet MUST take the opposite angle — find something bullish "
            "to say about the current data. Look for accumulation signals, "
            "support levels holding, or undervalued setups.\n"
        )
    if bullish_count >= 3:
        return (
            "\n\nIMPORTANT: Your recent tweets have all been bullish. "
            "This tweet MUST take the opposite angle — find something bearish "
            "to say about the current data. Look for risk-off signals, "
            "resistance rejections, or overheated indicators.\n"
        )
    return ""


def _call_claude(system_prompt: str, user_prompt: str, max_tokens: int = 300) -> str | None:
    """Make a Claude API call and return the text response."""
    client = _get_client()
    if client is None:
        return None
    # Inject sentiment balancing nudge into the user prompt
    nudge = _sentiment_nudge()
    if nudge:
        user_prompt = user_prompt + nudge
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
        # Ensure line breaks between thoughts — break wall-of-text paragraphs
        text = _ensure_line_breaks(text)
        return text
    except Exception as exc:
        logger.warning("Claude API call failed: %s", exc)
        return None


def _ensure_line_breaks(text: str) -> str:
    """If the tweet is a wall of text with 3+ sentences and no blank lines, insert them."""
    # Skip if already has blank lines (properly formatted)
    if "\n\n" in text:
        return text
    # Skip arrow/bullet-style tweets — they use single newlines intentionally
    if re.search(r'[\n].*→', text):
        return text
    # Split on sentence boundaries (. followed by space and uppercase letter)
    sentences = re.split(r'(?<=\.)\s+(?=[A-Z])', text)
    if len(sentences) < 3:
        return text
    # Group into blocks of 1-2 sentences, then join with blank lines
    blocks = []
    i = 0
    while i < len(sentences):
        if i + 1 < len(sentences) and len(sentences[i]) < 60:
            # Short sentence — group with the next one
            blocks.append(sentences[i] + " " + sentences[i + 1])
            i += 2
        else:
            blocks.append(sentences[i])
            i += 1
    result = "\n\n".join(blocks)
    # Only use the reformatted version if it stays within character limit
    if len(result) <= 280:
        return result
    return text


def _strip_hashtags(text: str) -> str:
    """Remove any hashtags the AI included despite instructions."""
    # Remove standalone hashtag words (e.g. #Bitcoin, #BTC)
    text = re.sub(r'\s*#\w+', '', text)
    # Clean up any trailing whitespace or blank lines left behind
    text = re.sub(r'\n\s*\n\s*$', '', text).strip()
    return text


# Phrases the AI falls back on too often — reject and retry
_BANNED_STARTS = [
    "worth noting", "it's worth noting", "interesting spot",
    "interesting to see", "fun fact", "here's the thing",
    "not gonna lie", "let's talk", "can we talk",
    "breaking:", "alert:", "just in:", "quick thought",
    "hot take:", "i'll say this",
]


def _is_too_similar(new_tweet: str) -> bool:
    """Check if a new tweet is too similar to recent ones."""
    if not _recent_tweets:
        return False
    new_lower = new_tweet.lower()
    # Check for banned openings
    for phrase in _BANNED_STARTS:
        if new_lower.startswith(phrase):
            return True
    # Check for near-duplicate content with recent tweets
    new_words = set(new_lower.split())
    for recent in _recent_tweets[-5:]:
        recent_words = set(recent.lower().split())
        if not recent_words:
            continue
        overlap = len(new_words & recent_words) / max(len(new_words), len(recent_words))
        if overlap > 0.6:
            return True
    return False


# ── System prompt for all tweet generation ──────────────────────────────────

_SYSTEM = """You are the voice behind @CoinWatchAlert on Twitter. You sound like a real trader sharing thoughts — not a bot, not a news feed, not a hype account.

ABSOLUTE RULES (break any of these and the tweet is rejected):
- Tweet MUST be under 275 characters
- ZERO hashtags. No #Bitcoin, no #BTC, no #Crypto, no hashtags of ANY kind
- NO emojis like 🚀🔥💰📈. You can use 🟢 or 🔴 for price direction, that's it
- Always include the actual price data provided — never fabricate numbers
- No disclaimers, no "NFA", no "DYOR", no "not financial advice"
- No "to the moon", "WAGMI", "LFG", or crypto bro speak
- Do NOT wrap your response in quotes

BANNED OPENINGS — never start a tweet with any of these:
- "Worth noting" / "It's worth noting"
- "Interesting spot" / "Interesting to see"
- "Fun fact" / "Here's the thing"
- "Not gonna lie" / "I'll say this"
- "Let's talk about" / "Can we talk about"
- "Breaking:" / "Alert:" / "Just in:"
- "Quick thought" / "Hot take:"

FORMATTING — this is critical for readability:
- NEVER write a wall of text. Every tweet needs visual breathing room
- Use line breaks between thoughts — 2-3 short blocks separated by blank lines
- Vary the format depending on the type of content:

  For OPINIONS and TAKES — spaced short paragraphs:
    BTC holding 67.3k after that rejection at 68k.

    Structure still looks weak — lower highs on the 4h.

    Need to reclaim 68.5k or this heads to 65k.

  For MARKET DATA and RECAPS — arrow/bullet style:
    Market check:

    → BTC: $67.3k (+2.1%)
    → ETH: $1,970 (+1.8%)
    → SOL: $95.50 (+3.2%)

    7/10 coins green on the day

  For ALERTS — section headers with emoji labels:
    📊 SOL surging +8.2% in 24h

    📈 Breaking above the $95 range it's been stuck in all week

    🎯 Next resistance at $100 — watching closely

  For RAW COMMENTARY — direct line-by-line breakdown, no fluff:
    PI bleeding -10.1% in 24h down to $0.2028.
    Rank, $2.0B market cap.
    Week's still green (+21.3%) but today's selloff is sharp.
    Watch the $0.19 level—if that breaks, we could see worse.

- Short punchy lines > long run-on sentences
- One thought per line. If a line has a comma and a second idea, break it into two lines
- A tweet with line breaks gets 2x more engagement than a wall of text

CRITICAL FORMATTING: Every tweet MUST have blank lines between thoughts. Never write a tweet as one continuous paragraph. Break it into 2-4 short blocks separated by blank lines. Each block is 1-2 sentences max. Think of each blank line as a breath.

VOICE — sound like a real human trader:
- Vary your openings. Sometimes start with data, sometimes with an opinion, sometimes with a question
- Use contractions (don't, won't, can't) — real people don't write formally
- Be specific — name levels, name coins, name percentages
- One clear thought per tweet. Don't cram in everything
- Write like you're texting a group chat of trader friends"""


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
            "DO NOT MENTION BITCOIN AT ALL. Not even once. Write ONLY about altcoins. "
            "Pick one alt from the data (ETH, SOL, XRP, ADA, DOGE, AVAX, DOT, LINK, or BNB) "
            "and write about IT specifically — its price, its move, what's happening with it. "
            "Start your tweet with the altcoin name or symbol. "
            "Example openings: 'ETH holding 1970 while...', 'SOL quietly up 3% while...', "
            "'LINK at $14 and nobody's talking about it...'"
        ),
    },
    "macro_narrative": {
        "label": "Macro / narrative",
        "instruction": (
            "DO NOT write about any specific coin's price action. Write about the BIGGER "
            "PICTURE — pick ONE of these topics: ETF flows and what they signal, DXY/dollar "
            "weakness and its effect on crypto, regulatory developments, the halving cycle "
            "and where we are, institutional adoption trends, or stablecoin supply as a "
            "leading indicator. Your tweet should read like macro analysis, not a price update."
        ),
    },
    "contrarian_take": {
        "label": "Contrarian / hot take",
        "instruction": (
            "Write a CONTRARIAN take that goes AGAINST the current sentiment. If the market "
            "is down, be bullish. If it's up, warn about risks. Disagree with something "
            "most of Crypto Twitter believes. End with something that invites debate — "
            "a question or a dare. Be bold, take a stance."
        ),
    },
    "trader_question": {
        "label": "Question / poll",
        "instruction": (
            "Ask your followers a QUESTION — not about Bitcoin's price. Ask about their "
            "portfolio, their strategy, their biggest conviction, or a specific altcoin. "
            "Examples: 'What's your highest conviction alt right now?', "
            "'Anyone else loading up on L2s here?', 'ETH/BTC ratio at lows — who's buying?', "
            "'What's the most undervalued coin in the top 50?'. "
            "End with a clear question mark. Keep it under 200 chars."
        ),
    },
    "market_structure": {
        "label": "Market structure / on-chain",
        "instruction": (
            "Write about market STRUCTURE — funding rates, exchange flows, leverage, "
            "liquidations, whale behavior, or on-chain signals. Don't just state the "
            "price — interpret what the structure is telling you about what comes NEXT."
        ),
    },
    "eth_analysis": {
        "label": "Ethereum focus",
        "instruction": (
            "Write ONLY about Ethereum. DO NOT mention Bitcoin. Talk about ETH's price, "
            "the ETH/BTC ratio, L2 activity, staking yields, ETH burns, or ETH ETF flows. "
            "Start your tweet with 'ETH' or 'Ethereum'. "
            "Example: 'ETH at $1,970 and the ratio keeps bleeding. Either this is the "
            "buy of the cycle or ETH is losing its premium. I'm watching...'"
        ),
    },
    "defi_l2": {
        "label": "DeFi / L2 narrative",
        "instruction": (
            "Write about DeFi or Layer 2s — NOT about Bitcoin price. Talk about TVL, "
            "DEX volumes, Solana vs Ethereum fees, Base/Arbitrum/Optimism growth, "
            "or a specific DeFi trend. Make it feel insider-y, like you track on-chain data. "
            "Example: 'Solana DEX volume just flipped Ethereum for the 3rd day running. "
            "The fee argument is over.'"
        ),
    },
    "raw_commentary": {
        "label": "Raw market commentary",
        "instruction": (
            "Write a raw, punchy market commentary on whichever coin has the most "
            "interesting move right now. Use this EXACT format — each line is a separate "
            "thought with a line break between them, NO blank lines, just newlines:\n"
            "Line 1: The headline fact — coin name, direction, percentage, price.\n"
            "Line 2: Context — rank, market cap, or a key stat.\n"
            "Line 3: Wider context — how the week or month looks vs today.\n"
            "Line 4: Forward-looking take — a level to watch and what happens if it breaks.\n"
            "Example:\n"
            "PI bleeding -10.1% in 24h down to $0.2028.\n"
            "Rank, $2.0B market cap.\n"
            "Week's still green (+21.3%) but today's selloff is sharp.\n"
            "Watch the $0.19 level—if that breaks, we could see worse.\n\n"
            "NO emojis, NO bullet points, NO headers. Just direct lines. "
            "Sound like you're giving a friend the quick rundown. Be opinionated on the last line."
        ),
    },
}


def _pick_quote_category(recent_categories: list[str]) -> str:
    """Pick a content category that hasn't been used recently.

    Non-BTC categories are weighted 2x to reduce Bitcoin dominance in the feed.
    BTC-focused categories ('btc_price', 'market_structure') get weight 1,
    everything else gets weight 2.
    """
    _BTC_HEAVY = {"btc_price", "market_structure"}
    _HIGH_ENGAGE = {"contrarian_take", "trader_question", "raw_commentary"}
    all_cats = list(QUOTE_CATEGORIES.keys())
    # Exclude categories used in the last 3 posts
    recent_set = set(recent_categories[-3:])
    available = [c for c in all_cats if c not in recent_set]
    if not available:
        last = recent_categories[-1] if recent_categories else ""
        available = [c for c in all_cats if c != last]
    if not available:
        available = all_cats
    # Weight: BTC-heavy=1, normal=2, high-engagement=3
    weights = []
    for c in available:
        if c in _BTC_HEAVY:
            weights.append(1)
        elif c in _HIGH_ENGAGE:
            weights.append(3)
        else:
            weights.append(2)
    return random.choices(available, weights=weights, k=1)[0]


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

    # Categories where BTC should NOT be the focus
    _NO_BTC_CATEGORIES = {"alt_spotlight", "eth_analysis", "defi_l2", "trader_question", "macro_narrative"}

    if category in _NO_BTC_CATEGORIES:
        # Build alt-only data — exclude BTC entirely from visible data
        alt_lines = ""
        if coins_data:
            for c in coins_data[:8]:
                sym = c.get("symbol", "?").upper()
                if sym == "BTC":
                    continue  # hide BTC from the data
                cp = c.get("current_price", 0)
                cpct = c.get("price_change_percentage_24h_in_currency") or 0
                alt_lines += f"  {sym}: ${cp:,.2f} ({cpct:+.1f}%)\n"
        data_block = f"Altcoin data:\n{alt_lines}" if alt_lines else "No alt data available."
        btc_rule = "\n- DO NOT mention Bitcoin or BTC in this tweet. This tweet is NOT about Bitcoin."
    else:
        data_block = f"""BTC Price: ${price:,.0f} | 24h: {pct_24h:+.1f}% | 7d: {pct_7d:+.1f}% | MCap: {mcap_str}
{f"Coins:{chr(10)}{coin_lines}" if coin_lines else ""}"""
        btc_rule = ""

    # Build coin avoidance hint if set
    avoid_line = ""
    if _coins_to_avoid:
        avoid_list = ", ".join(sorted(_coins_to_avoid))
        avoid_line = (
            f"\n- AVOID writing about these coins (already tweeted recently): {avoid_list}. "
            "Pick a DIFFERENT coin or angle instead."
        )

    prompt = f"""Write a crypto tweet. Your SPECIFIC assignment: {cat_info['instruction']}

Market data:
{data_block}

CRITICAL RULES:
- NO hashtags. Zero
- Under 275 characters{btc_rule}{avoid_line}
- Never start with "Worth noting", "Interesting spot", or similar filler phrases
- One sharp thought, not a summary of everything
{_get_recent_context()}
Write the tweet now. Nothing else."""

    # Try up to 3 times to get a non-repetitive, on-topic tweet
    for attempt in range(3):
        tweet = _call_claude(_SYSTEM, prompt)
        if not tweet:
            continue
        # Hard reject: if category bans BTC but tweet leads with Bitcoin
        if category in _NO_BTC_CATEGORIES:
            first_30 = tweet[:30].lower()
            if first_30.startswith(("btc ", "bitcoin", "$btc")):
                logger.info("AI tweet rejected (attempt %d/3): leads with BTC in non-BTC category", attempt + 1)
                continue
        if not _is_too_similar(tweet):
            return tweet, category
        logger.info("AI tweet rejected (attempt %d/3): too similar or banned phrase", attempt + 1)
    return tweet, category  # return last attempt even if not ideal


def generate_opinion_tweet(price: float, pct_24h: float, pct_7d: float,
                           coins_data: list[dict] | None = None,
                           defi_context: str | None = None) -> str | None:
    """Generate an AI-written opinion/analysis tweet."""
    # 50% chance to write about alts/market instead of BTC
    focus_alt = random.random() < 0.5 and coins_data
    defi_line = f"\n{defi_context}" if defi_context else ""

    if focus_alt:
        alt_lines = ""
        for c in (coins_data or [])[:6]:
            sym = c.get("symbol", "?").upper()
            if sym == "BTC":
                continue
            cp = c.get("current_price", 0)
            cpct = c.get("price_change_percentage_24h_in_currency") or 0
            alt_lines += f"  {sym}: ${cp:,.2f} ({cpct:+.1f}%)\n"

        prompt = f"""Write an opinionated tweet about altcoins or the broader crypto market — NOT about Bitcoin price.

Altcoin data:
{alt_lines}
BTC context (for reference only, don't lead with it): ${price:,.0f} ({pct_24h:+.1f}% 24h){defi_line}

Pick ONE altcoin or ONE market theme (rotation, dominance, DeFi, L2s) and give a strong opinion.
If DeFi TVL data is provided, you can reference it to back up your take.
Start with the altcoin name or the theme — NOT with BTC.

NO hashtags. Sound human. Under 275 characters.
{_get_recent_context()}
Write the tweet now. Nothing else."""
    else:
        prompt = f"""Write an opinionated crypto tweet using this data:

BTC Price: ${price:,.0f}
24h Change: {pct_24h:+.1f}%
7d Change: {pct_7d:+.1f}%{defi_line}

Take a clear stance. Say something a real trader would say to their followers.
Include specific reasoning — what you're watching, what concerns you, what excites you.
If DeFi TVL data is provided, you can reference it to support your analysis.
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
        "Ask followers to drop their entry price for BTC or a specific alt",
        "Present a 'would you rather' — e.g. hold 1 BTC or 100 ETH at current prices",
        "Ask what the most underrated narrative in crypto is right now",
        "Ask if they'd buy this dip or wait — give two price targets to choose from",
        "Ask what their portfolio allocation looks like right now (% BTC, % alts, % stables)",
        "Ask what coin they're secretly accumulating that nobody talks about",
        "Ask if the market feels more like 2021 or 2019 right now and why",
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
    import random
    style = random.choice([
        "Add a data point they missed — a specific level, percentage, or metric that adds context",
        "Agree and build on their point with your own angle — show you understand the setup",
        "Push back gently with data — take the other side and explain why",
        "Ask a sharp follow-up question that shows expertise and invites them to engage back",
        "Connect their point to a bigger narrative (ETF flows, dominance, macro) they didn't mention",
    ])

    prompt = f"""Write a reply to this crypto tweet:

"{original_tweet[:200]}"

Current BTC data: ${btc_price:,.0f} ({pct_24h:+.1f}% 24h)

STYLE: {style}

Rules for the reply:
- Keep it under 220 characters
- Add genuine value — you're building authority as @CoinWatchAlert
- NEVER be generic ("great point", "solid take", "interesting") — every reply must contain substance
- Sound like a fellow trader who adds to conversations, not someone farming engagement
- NO hashtags in replies
- Be conversational — use contractions, sound human
- If you disagree, be respectful but firm

Write the reply now. Nothing else."""

    system = """You are @CoinWatchAlert replying to crypto traders. You're building a reputation as the account that always adds value in replies. Your replies make people check your profile. You reference data, levels, and insights — never generic filler. People follow accounts that consistently add to conversations."""

    return _call_claude(system, prompt, max_tokens=150)
