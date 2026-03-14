"""
ai_writer.py – Claude-powered tweet generation.

Provides three public functions:
  generate_news_tweet(story)         – factual news tweet for a single story
  generate_morning_recap(headlines)  – daily 08:00 UK market-briefing tweet
  generate_quote_tweet(original)     – analyst-voice quote-tweet with context

Voice: professional crypto news analyst (Bloomberg terminal, not Discord alpha).
Report facts and data. No buy/sell calls. No dismissive language. Present both
sides when relevant. Highlight what's notable without making directional calls.

Engagement tweets and contrarian takes are separate scheduled categories and
intentionally use a different voice — they are NOT generated here.

Requires ANTHROPIC_API_KEY in .env.
Falls back to a plain-text summary if the API call fails.
"""
from __future__ import annotations

import json
import logging
import os
import random
import re
import time
import urllib.error
import urllib.request
import anthropic

import config

logger = logging.getLogger(__name__)

_client: anthropic.Anthropic | None = None

# Map CryptoPanic currency codes → canonical hashtags
_HASHTAG_MAP = {
    "BTC": "#Bitcoin",
    "ETH": "#Ethereum",
}
_FALLBACK_HASHTAG = "#Crypto"

MODEL = "claude-haiku-4-5-20251001"

# ── Recent tweet history (persisted across restarts) ─────────────────────────
_RECENT_TWEETS_FILE = os.path.join(os.path.dirname(__file__), ".recent_tweets.json")
_recent_tweets: list[str] = []


def _load_recent_tweets() -> None:
    global _recent_tweets
    try:
        with open(_RECENT_TWEETS_FILE) as f:
            data = json.load(f)
        _recent_tweets = data if isinstance(data, list) else []
    except (FileNotFoundError, json.JSONDecodeError):
        _recent_tweets = []


_load_recent_tweets()


def record_recent_tweet(text: str) -> None:
    """Add tweet to recent history and persist to disk (max 10 entries)."""
    global _recent_tweets
    _recent_tweets.append(text)
    if len(_recent_tweets) > 10:
        _recent_tweets = _recent_tweets[-10:]
    try:
        with open(_RECENT_TWEETS_FILE, "w") as f:
            json.dump(_recent_tweets, f)
    except OSError as exc:
        logger.warning("Could not save recent tweets: %s", exc)


# ── BTC data cache (120-second TTL to avoid CoinGecko rate limits) ────────────
_btc_cache: dict = {"data": None, "ts": 0.0}


def _fetch_btc_data() -> dict:
    """
    Fetch BTC price/market data from CoinGecko.
    Returns cached data if less than 120 seconds old to avoid rate-limit 429s.
    Returns an empty dict on failure (callers must handle missing keys).
    """
    global _btc_cache
    now = time.time()
    if _btc_cache["data"] is not None and (now - _btc_cache["ts"]) < 120:
        return _btc_cache["data"]

    url = (
        "https://api.coingecko.com/api/v3/simple/price"
        "?ids=bitcoin&vs_currencies=usd"
        "&include_market_cap=true&include_24hr_vol=true&include_24hr_change=true"
    )
    try:
        with urllib.request.urlopen(url, timeout=10) as resp:
            raw = json.loads(resp.read())
        data = raw.get("bitcoin", {})
        _btc_cache["data"] = data
        _btc_cache["ts"] = now
        return data
    except Exception as exc:
        logger.warning("CoinGecko BTC fetch failed: %s", exc)
        if _btc_cache["data"]:
            age_mins = (now - _btc_cache["ts"]) / 60
            logger.warning("Using stale BTC cache (%.0f min old)", age_mins)
            return _btc_cache["data"]
        return {}

# ── Tweet length guard ────────────────────────────────────────────────────────
_TWEET_LIMIT = 275


def _truncate_tweet(text: str, limit: int = _TWEET_LIMIT) -> str:
    """Hard-truncate to `limit` chars, preferring sentence then word boundaries."""
    if len(text) <= limit:
        return text
    snippet = text[:limit]
    # Try last sentence boundary at least halfway through
    for sep in (". ", "! ", "? ", ".\n", "!\n", "?\n"):
        pos = snippet.rfind(sep)
        if pos > limit // 2:
            return snippet[: pos + 1].rstrip()
    # Fall back to word boundary
    pos = snippet.rfind(" ")
    if pos > 0:
        return snippet[:pos] + "…"
    return snippet[: limit - 1] + "…"


# Shared system prompt for all news/briefing/quote-tweet generation.
_ANALYST_SYSTEM = (
    "You are a crypto news analyst with a punchy, breaking-news voice — "
    "think Ash Crypto, Bitcoin Magazine, or Coin Bureau. "
    "Use short declarative sentences. Lead with the most shocking or notable fact. "
    "Use relevant emojis sparingly but effectively (🚨 for breaking, 🔥 for big moves, "
    "📊 for data, ⚡ for fast-moving stories). "
    "Use line breaks between key points — never write walls of text. "
    "Report facts and data — no buy/sell calls. "
    "Be direct, bold, and make people want to read more."
)


def _get_client() -> anthropic.Anthropic:
    global _client
    if _client is None:
        _client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)
    return _client


def _pick_hashtags(story: dict) -> str:
    """Return 1-2 hashtags appropriate for the story's currencies."""
    currencies = story.get("currencies") or []
    codes = [c["code"] for c in currencies if c.get("code") and c["code"] != "?"]
    tags = [_HASHTAG_MAP[code] for code in codes if code in _HASHTAG_MAP]
    if not tags:
        tags = [_FALLBACK_HASHTAG]
    return " ".join(tags[:2])


def generate_price_tweet(alert: dict) -> str:
    """
    Write a price alert tweet in the spaced layout:

        ⚡ SYMBOL DIRECTION_EMOJI

        $PRICE | SIGN PCT% WINDOW

        [one sharp market context line from Claude]

        ⚠️ NFA
    """
    symbol    = alert["symbol"]
    pct       = alert["pct_change"]
    price     = alert["price_usd"]
    window    = alert["window"]
    sign      = "+" if pct > 0 else ""
    dir_emoji = "🟢" if pct > 0 else "🔴"

    from price_monitor import _format_price
    price_str = _format_price(price)

    header = f"⚡ {symbol} {dir_emoji}"
    data   = f"{price_str} | {sign}{pct:.1f}% {window}"

    if not config.ANTHROPIC_API_KEY:
        from price_monitor import format_price_tweet
        return format_price_tweet(alert)

    prompt = (
        f"{symbol} just moved {sign}{pct:.1f}% in {window}. Current price: {price_str}.\n\n"
        f"Write ONE sharp sentence of market context — name the specific level in play and what happens next. "
        f"No hedging ('could see', 'might', 'possibly'). No questions. Declarative only. "
        f"No buy/sell calls. No hashtags. No emojis. Max 120 chars for this one line.\n\n"
        f"Output ONLY that single sentence, nothing else."
    )

    context_line = ""
    try:
        message = _get_client().messages.create(
            model=MODEL,
            max_tokens=60,
            system=_ANALYST_SYSTEM,
            messages=[{"role": "user", "content": prompt}],
        )
        context_line = message.content[0].text.strip().strip('"').strip("'")
    except anthropic.APIError as exc:
        logger.warning("Claude API error generating price tweet: %s", exc)

    if context_line:
        tweet = f"{header}\n\n{data}\n\n{context_line}"
    else:
        tweet = f"{header}\n\n{data}"

    return _truncate_tweet(tweet)


def generate_price_alert_tweet(alert: dict) -> str | None:
    """
    Ask Claude to write a single declarative price-alert tweet.

    Rules: no questions, no first person, no hashtags, no line breaks,
    ends with ⚠️ NFA, max 220 chars, emojis only 🚀📉⚡👀.
    Returns None on API failure.
    """
    symbol = alert["symbol"]
    pct    = alert["pct_change"]
    price  = alert["price_usd"]
    window = alert["window"]
    sign   = "+" if pct > 0 else ""

    from price_monitor import _format_price
    price_str = _format_price(price)

    prompt = (
        f"{symbol} moved {sign}{pct:.1f}% in {window}. Current price: {price_str}.\n\n"
        f"Write ONE declarative tweet reporting this price move with sharp market context.\n\n"
        f"Rules:\n"
        f"- No questions. No first person (no 'I', 'we', 'our'). No hashtags.\n"
        f"- No line breaks — single continuous tweet.\n"
        f"- Emojis allowed: 🚀📉⚡👀 only.\n"
        f"- End with ⚠️ NFA\n"
        f"- Max 200 chars (excluding the ⚠️ NFA suffix).\n\n"
        f"Output ONLY the tweet text, nothing else."
    )

    if not config.ANTHROPIC_API_KEY:
        return None

    try:
        message = _get_client().messages.create(
            model=MODEL,
            max_tokens=100,
            system=_ANALYST_SYSTEM,
            messages=[{"role": "user", "content": prompt}],
        )
        tweet = message.content[0].text.strip().strip('"').strip("'")
    except anthropic.APIError as exc:
        logger.warning("Claude API error generating price alert tweet: %s", exc)
        return None

    tweet = _truncate_tweet(tweet, limit=220)
    if not tweet.rstrip().endswith("NFA"):
        tweet = tweet.rstrip()
        if len(tweet) + len(" ⚠️ NFA") <= 220:
            tweet = tweet + " ⚠️ NFA"
        else:
            tweet = tweet[: 220 - len(" ⚠️ NFA")].rstrip() + " ⚠️ NFA"
    return tweet


def generate_geo_tweet(story: dict) -> str | None:
    """
    Ask Claude to write a single breaking crypto/macro tweet for a geo/macro story.

    Format:
        One punchy declarative opener sentence.
        → short bullet
        → short bullet
        One line on the crypto/BTC angle.
        ⚠️ NFA

    No questions. No first person. No hashtags. Max 280 chars total.
    Emojis only from 🚀📉⚡👀. Returns None on failure.
    """
    title = story.get("title", "")
    if not title:
        return None

    if not config.ANTHROPIC_API_KEY:
        return None

    prompt = (
        f"Write a single breaking crypto/macro tweet about this news story.\n\n"
        f"Format:\n"
        f"One punchy declarative opener sentence.\n"
        f"Then 2 short bullet lines starting with →.\n"
        f"Then one line on the crypto/BTC angle.\n"
        f"No questions. No first person. No hashtags.\n"
        f"Ends with ⚠️ NFA.\n"
        f"Max 280 chars total.\n"
        f"Emojis only from 🚀📉⚡👀.\n\n"
        f"Story: {title}"
    )

    try:
        message = _get_client().messages.create(
            model=MODEL,
            max_tokens=120,
            system=_ANALYST_SYSTEM,
            messages=[{"role": "user", "content": prompt}],
        )
        tweet = message.content[0].text.strip().strip('"').strip("'")
    except anthropic.APIError as exc:
        logger.warning("Claude API error generating geo tweet: %s", exc)
        return None

    tweet = _truncate_tweet(tweet, limit=280)
    tweet = re.sub(r'(\s*⚠️\s*NFA\.?\s*)+$', '', tweet).strip()
    tweet = tweet + ' ⚠️ NFA'
    tweet = re.sub(r'[^\w\s\$\%\.\,\!\?\-\:\;—\→\@🚀📉⚡👀⚠️\n]', '', tweet).strip()
    return tweet


def generate_news_tweet(story: dict) -> str:
    """
    Ask Claude to write a factual news tweet for a single crypto story.
    Reports what happened; no directional calls or hype.
    Ends with 1-2 relevant hashtags (#Bitcoin, #Ethereum, or #Crypto).
    Falls back to a plain formatted string if the API call fails.
    """
    title = story.get("title", "")
    url = story.get("url", "")
    hashtags = _pick_hashtags(story)

    if not config.ANTHROPIC_API_KEY:
        logger.debug("ANTHROPIC_API_KEY not set – using plain news tweet format")
        return _plain_news_tweet(title, url, hashtags)

    prompt = (
        f"Write a 1-2 sentence analyst comment for this crypto news headline.\n\n"
        f"Rules:\n"
        f"- State the market implication directly — bullish or bearish, with a specific figure (price, %, volume, TVL)\n"
        f"- No questions. No hedging ('could see', 'might', 'possibly'). One declarative statement.\n"
        f"- Short punchy sentences. No buy/sell calls. No hashtags.\n"
        f"- Total ≤ 160 characters\n\n"
        f"Headline: {title}\n\n"
        f"Output ONLY the comment, no quotes, no prefix."
    )

    last_exc: anthropic.APIError | None = None
    for attempt in range(1, 4):
        try:
            message = _get_client().messages.create(
                model=MODEL,
                max_tokens=100,
                system=_ANALYST_SYSTEM,
                messages=[{"role": "user", "content": prompt}],
            )
            comment = message.content[0].text.strip().strip('"').strip("'")
            # Assemble: emoji+headline \n\n comment \n\n url \n\n NFA
            prefix = "📰"
            headline = f"{prefix} {title}"
            url_block = f"\n\n{url}" if url else ""
            candidate = f"{headline}\n\n{comment}{url_block}\n\n⚠️ NFA"
            return _truncate_tweet(candidate)
        except anthropic.APIError as exc:
            last_exc = exc
            logger.warning("Claude API error (attempt %d/3) generating news tweet: %s", attempt, exc)
            if attempt < 3:
                time.sleep(5)

    logger.warning("All 3 attempts failed – falling back to plain tweet")
    return _plain_news_tweet(title, url, hashtags)


def generate_morning_recap(headlines: list[str]) -> str:
    """
    Ask Claude to write a factual morning market briefing tweet (max 220 chars)
    based on the top 3 recent crypto headlines.
    Falls back to a plain bullet summary if the API call fails.
    """
    if not headlines:
        return "☀️ Good morning! Crypto markets are open. Stay sharp. #Crypto"

    numbered = "\n".join(f"{i+1}. {h}" for i, h in enumerate(headlines[:3]))

    if not config.ANTHROPIC_API_KEY:
        logger.debug("ANTHROPIC_API_KEY not set – using plain morning recap format")
        return _plain_morning_recap(headlines)

    prompt = (
        "Write one single sentence crypto market tweet based on these headlines. "
        "Pick the most important story, state what it means for the market directionally. "
        "No hashtags. No line breaks. No bullet points. No questions. "
        "Ends with ⚠️ NFA. Max 220 chars. Emojis only from 🚀📉⚡👀. "
        "Write it now, nothing else.\n\n"
        f"Headlines:\n{numbered}"
    )

    try:
        message = _get_client().messages.create(
            model=MODEL,
            max_tokens=100,
            system=_ANALYST_SYSTEM,
            messages=[{"role": "user", "content": prompt}],
        )
        tweet = message.content[0].text.strip()
        return tweet[:220]
    except anthropic.APIError as exc:
        logger.warning("Claude API error generating morning recap: %s", exc)
        return _plain_morning_recap(headlines)


def generate_thread(topic: str, n_tweets: int = 3) -> list[str]:
    """
    Ask Claude to write a 3-tweet Twitter thread on `topic`.

    Each tweet stands alone but flows into the next. No numbering prefixes.
    Tweet 1: bold thesis + data point. Tweet 2: evidence/numbers.
    Tweet 3: directional conclusion with timeframe.

    Returns a list of tweet strings (each ≤200 chars).
    Falls back to an empty list on failure.
    """
    if not config.ANTHROPIC_API_KEY:
        logger.warning("ANTHROPIC_API_KEY not set – cannot generate thread")
        return []

    n = max(3, n_tweets)  # minimum 3

    prompt = (
        f"Write a {n}-tweet Twitter thread about: {topic}\n\n"
        "Format — output exactly 3 lines, one tweet per line, nothing else:\n\n"
        "Tweet 1: Bold opening statement of the core thesis with one data point. "
        "Hooks the reader. Ends naturally — no numbering prefix, no question.\n\n"
        "Tweet 2: Supporting evidence. Specific numbers, comparisons, or on-chain/volume data. "
        "Makes the thesis concrete. No numbering prefix.\n\n"
        "Tweet 3: Start with 'Bottom line:' or 'The takeaway:' then give a directional call "
        "or prediction with a timeframe (e.g. 'by Q3', 'within 90 days', 'this cycle'). "
        "Declarative, committed stance. No numbering prefix.\n\n"
        "Hard rules:\n"
        "- No numbering (no '1/', '2/', '3/', '1.', etc.)\n"
        "- No questions anywhere\n"
        "- No hedging ('could', 'might', 'may', 'possibly')\n"
        "- No hashtags\n"
        "- Each tweet under 200 characters\n"
        "- Emojis only from 🚀📉⚡👀. No other emojis.\n"
        "- Analyst tone: direct and declarative throughout\n"
        "- Output ONLY the 3 tweet lines, nothing else"
    )

    try:
        message = _get_client().messages.create(
            model=MODEL,
            max_tokens=400,
            system=_ANALYST_SYSTEM,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = message.content[0].text.strip()
        tweets = [line.strip() for line in raw.splitlines() if line.strip()]
        tweets = [_truncate_tweet(t, limit=200) if len(t) > 200 else t for t in tweets]
        tweets = [re.sub(r'[^\w\s\$\%\.\,\!\?\-\:\;—\@🚀📉⚡👀⚠️\n]', '', t).strip() for t in tweets]
        logger.info("Generated thread with %d tweets on: %s", len(tweets), topic)
        return tweets
    except anthropic.APIError as exc:
        logger.warning("Claude API error generating thread: %s", exc)
        return []


def generate_hot_take(context: str = "") -> str | None:
    """
    Generate a punchy opinion/hot-take tweet.

    Fetches real BTC price data first. Returns None (skipping the tweet) if
    price data is unavailable — never generates content with fabricated prices.

    Layout:
        [Strong opener — bold claim or data point]

        [Supporting point — evidence or context]

        [Closing conviction — implication or stance]

        ⚠️ NFA
    """
    if not config.ANTHROPIC_API_KEY:
        logger.warning("ANTHROPIC_API_KEY not set – cannot generate hot take")
        return None

    # Require real price data — never let Claude fabricate price levels.
    btc_data = _fetch_btc_data()
    price = btc_data.get("usd")
    if not price or price <= 0:
        logger.warning("generate_hot_take: no real BTC price available — skipping to avoid fabricated prices")
        return None

    pct_24h = btc_data.get("usd_24h_change")
    sign = "+" if pct_24h and pct_24h > 0 else ""
    pct_str = f" ({sign}{pct_24h:.1f}% 24h)" if pct_24h is not None else ""
    price_line = f"BTC: ${price:,.0f}{pct_str}"

    context_block = f"\nCurrent context:\n{price_line}"
    if context:
        context_block += f"\n{context}"

    prompt = (
        "Write a single flowing opinion tweet: a bold directional call that names a specific "
        "price level or timeframe and states clearly what happens next. "
        "Back it with one concrete data point from the price data provided — never invent numbers. "
        "No questions. No hedging ('could see', 'might', 'possibly'). No line breaks. "
        "Do NOT start with 'Hot take:'. Take a clear side — bullish or bearish. "
        "Under 240 chars. No hashtags. No emojis except 🟢🔴. No buy/sell calls."
        f"{context_block}"
    )

    try:
        message = _get_client().messages.create(
            model=MODEL,
            max_tokens=150,
            system=_ANALYST_SYSTEM,
            messages=[{"role": "user", "content": prompt}],
        )
        text = message.content[0].text.strip()
        if text.startswith('"') and text.endswith('"'):
            text = text[1:-1]
        if text.startswith("'") and text.endswith("'"):
            text = text[1:-1]
        text = _clean_tweet(text)
        text = _strip_hashtags(text)
        # Ensure double blank lines between sections
        text = _ensure_line_breaks(text)
        text = _truncate_tweet(text)
        return text
    except Exception as exc:
        logger.warning("Claude API call failed: %s", exc)
        return None




def _ensure_line_breaks(text: str) -> str:
    """If the tweet is a wall of text with no blank lines, insert them between sentences."""
    # Skip if already has blank lines (properly formatted)
    if "\n\n" in text:
        return text
    # Skip arrow/bullet-style tweets — they use single newlines intentionally
    if re.search(r'[\n].*→', text):
        return text
    # Split on sentence boundaries (. or ? or ! followed by space and uppercase letter)
    sentences = re.split(r'(?<=[.!?])\s+(?=[A-Z])', text)
    if len(sentences) < 2:
        return text
    # Each sentence gets its own block separated by blank lines
    result = "\n\n".join(sentences)
    # Only use the reformatted version if it stays within character limit
    if len(result) <= 280:
        return result
    return text


def _clean_tweet(text: str) -> str:
    """Collapse all whitespace (spaces, newlines, tabs) into single spaces."""
    return " ".join(text.split())


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
    "quick thought", "hot take:", "i'll say this",
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

_SYSTEM = """You are the voice behind @CoinWatchAlert on Twitter. You sound like a sharp trader who calls shots — not a bot, not a news feed, not a hype account. People follow you because you make BOLD CALLS that they can come back and check.

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
- "Quick thought" / "Hot take:"
- "Worth keeping an eye on" / "Keep an eye on"
- "Something to watch" / "One to watch"

BANNED PHRASES — never use these weak, passive phrases ANYWHERE in a tweet:
- "worth watching" / "worth keeping an eye on" / "keeping an eye on"
- "let's see what happens" / "we'll see" / "time will tell"
- "could go either way" / "remains to be seen"
- "interesting to see how this plays out"
- "early interest, but let's see"
These are the phrases of a NEWS FEED, not a trader. A trader makes a CALL.

VOICE — this is what makes people follow you:
- MAKE CALLS. Don't say "worth watching" — say "this breaks $X or it dumps to $Y"
- Use "if X then Y" frameworks: "If BTC loses 65k, 60k is next. If it holds, 70k by Friday."
- Take a side. Every tweet should have a DIRECTION — bullish or bearish, never neutral
- Be specific with targets: price levels, timeframes, percentages
- Sound CONFIDENT. No hedging with "maybe", "possibly", "might"
- When you're right, you want people to screenshot the tweet. Write like that.
- Challenge the crowd: "Everyone's calling for 100k. Show me the volume to back it up."
- Use contractions (don't, won't, can't) — real people don't write formally
- Write like you're texting a group chat of trader friends who respect your calls

FORMATTING — this is critical for readability:
- NEVER write a wall of text. Every tweet needs visual breathing room
- Use line breaks between thoughts — 2-3 short blocks separated by blank lines
- Short punchy lines > long run-on sentences
- One thought per line. If a line has a comma and a second idea, break it into two lines

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

  For RAW COMMENTARY — direct line-by-line breakdown:
    PI bleeding -10.1% in 24h down to $0.2028.
    $2.0B market cap and still no real utility.
    Week's green (+21.3%) but today's selling says someone knows something.
    Below $0.19 and this goes to $0.15. I'm not buying.

CRITICAL FORMATTING: Every tweet MUST have blank lines between thoughts. Never write a tweet as one continuous paragraph. Break it into 2-4 short blocks separated by blank lines."""


# ── Diverse content categories for quote tweets ─────────────────────────────
# Each category produces a genuinely different kind of tweet, not just
# a different angle on "BTC is at $X".

QUOTE_CATEGORIES = {
    "btc_price": {
        "label": "BTC price action",
        "instruction": (
            "Write a BTC price action CALL. State the price, then make a directional prediction. "
            "Use 'if X then Y' format: 'If BTC holds $65k, $70k is next. Lose it and we see $60k.' "
            "Pick a side — bullish or bearish. Name specific levels. No fence-sitting."
        ),
    },
    "alt_spotlight": {
        "label": "Altcoin spotlight",
        "instruction": (
            "DO NOT MENTION BITCOIN AT ALL. Pick one alt from the data and make a CALL on it. "
            "Don't just describe the move — say where it's going next and why. "
            "Start with the coin name. Be specific with targets. "
            "Example: 'SOL at $95 and about to test $100. If it breaks, $120 is in play this month. "
            "Volume says this one's real.' "
            "NOT: 'SOL is up 3%. Worth keeping an eye on.' — that's weak, nobody follows for that."
        ),
    },
    "macro_narrative": {
        "label": "Macro / narrative",
        "instruction": (
            "DO NOT write about any specific coin's price action. Write about the BIGGER "
            "PICTURE — pick ONE topic: ETF flows, DXY, regulation, halving cycle, institutional "
            "adoption, stablecoin supply. But don't just describe it — make a PREDICTION about "
            "what it means for the market. Take a stance. "
            "Example: 'Stablecoin supply hitting ATH while everyone's bearish. "
            "Last time this set up, BTC rallied 40% in 3 months. Same setup, same trade.'"
        ),
    },
    "contrarian_take": {
        "label": "Contrarian / hot take",
        "instruction": (
            "Write a CONTRARIAN take that goes AGAINST the current sentiment. If the market "
            "is down, be bullish with a specific target. If it's up, call the top with a level. "
            "Disagree with something most of Crypto Twitter believes. "
            "End with a dare or challenge: 'Screenshot this.' / 'Bookmark this tweet.' / "
            "'Come back in 30 days.' Be bold — this is the tweet people remember."
        ),
    },
    "trader_conviction": {
        "label": "Conviction call",
        "instruction": (
            "Make a CONVICTION CALL that forces followers to take notice. Not vague — specific. "
            "State your position and a price target with a timeframe. "
            "Examples: 'BTC at $67k and I'm adding here. Target $75k before end of month.', "
            "'ETH under $2k is a gift. $2,800 by Q2 or I'm wrong — screenshot this.', "
            "'My highest conviction alt right now: SOL. $150 is the next stop.' "
            "No questions. Take a side. Keep it under 200 chars."
        ),
    },
    "market_structure": {
        "label": "Market structure / on-chain",
        "instruction": (
            "Write about market STRUCTURE — funding rates, exchange flows, leverage, "
            "liquidations, whale behavior. But end with a CALL based on what you see. "
            "Example: 'Exchange outflows just hit a 6-month high while funding is negative. "
            "Last time this happened BTC rallied 20% in 2 weeks. I'm not fighting this.'"
        ),
    },
    "eth_analysis": {
        "label": "Ethereum focus",
        "instruction": (
            "Write ONLY about Ethereum. DO NOT mention Bitcoin. Make a directional call on ETH. "
            "Start with 'ETH' or 'Ethereum'. Include a price target or clear thesis. "
            "Example: 'ETH at $1,970 and the ratio keeps bleeding. ETH under $2k with "
            "the Dencun upgrade live is a gift. Target: $2,800 by Q2.' "
            "NOT: 'ETH at $1,970 and the ratio keeps bleeding. Interesting to watch.' — that's boring."
        ),
    },
    "defi_l2": {
        "label": "DeFi / L2 narrative",
        "instruction": (
            "Write about DeFi or Layer 2s — NOT about Bitcoin price. Make a bold claim about "
            "where the space is heading. Call a winner or call something dead. "
            "Example: 'Base is doing more daily txns than Arbitrum and Optimism combined. "
            "If you're not paying attention to Coinbase's L2 play, you're going to miss the trade.' "
            "NOT: 'L2 activity is growing. Worth monitoring.' — nobody follows for that."
        ),
    },
    "raw_commentary": {
        "label": "Raw market commentary",
        "instruction": (
            "Write a raw, punchy market commentary on whichever coin has the most "
            "interesting move right now. Use this EXACT format:\n"
            "Line 1: The headline fact — coin name, direction, percentage, price.\n"
            "Line 2: Context — rank, market cap, or a key stat.\n"
            "Line 3: Wider context — how the week or month looks vs today.\n"
            "Line 4: YOUR CALL — not 'watch this level', but 'I'm buying here' or 'this dumps to $X'.\n"
            "Example:\n"
            "PI bleeding -10.1% in 24h down to $0.2028.\n"
            "$2.0B market cap and still no real utility.\n"
            "Week's green (+21.3%) but today's selling says someone knows something.\n"
            "Below $0.19 and this goes to $0.15. I'm not touching it.\n\n"
            "NO emojis, NO bullet points, NO headers. Just direct lines. "
            "The last line MUST be a clear call — buy, sell, avoid, or a price target."
        ),
    },
}


def _pick_quote_category(recent_categories: list[str]) -> str:
    """Pick a content category that hasn't been used recently.

    Non-BTC categories are weighted 2x to reduce Bitcoin dominance in the feed.
    """
    btc_focused = {"btc_price", "market_structure"}
    weights = {k: 1 if k in btc_focused else 2 for k in QUOTE_CATEGORIES}
    # Down-weight categories used in last 3 posts
    for cat in recent_categories[-3:]:
        if cat in weights:
            weights[cat] = max(1, weights[cat] - 1)
    categories = list(weights.keys())
    weighted = [cat for cat in categories for _ in range(weights[cat])]
    return random.choice(weighted)


# ── Core availability + raw Claude call ──────────────────────────────────────

def is_available() -> bool:
    """Return True if Anthropic API key is configured."""
    return bool(config.ANTHROPIC_API_KEY)


def _call_claude(
    system: str,
    prompt: str,
    max_tokens: int = 120,
) -> str | None:
    """Make a raw Claude API call. Returns text or None on failure."""
    if not is_available():
        return None
    try:
        message = _get_client().messages.create(
            model=MODEL,
            max_tokens=max_tokens,
            system=system,
            messages=[{"role": "user", "content": prompt}],
        )
        text = message.content[0].text.strip()
        if text.startswith('"') and text.endswith('"'):
            text = text[1:-1]
        if text.startswith("'") and text.endswith("'"):
            text = text[1:-1]
        return text or None
    except Exception as exc:
        logger.warning("Claude API call failed: %s", exc)
        return None


# ── Tweet generators used by tweet_generators.py ──────────────────────────────

def generate_quote_tweet(
    price: float,
    pct_24h: float,
    pct_7d: float,
    mcap: float,
    coins: list[dict],
) -> tuple[str | None, str]:
    """
    Generate a market analysis tweet using QUOTE_CATEGORIES rotation.
    Returns (tweet_text, category_name). Falls back to ("", "failed").
    Used by tweet_generators.generate_quote_tweet().
    """
    if not is_available():
        return None, "unavailable"

    recent_cats = []
    try:
        import state as _state
        recent_cats = _state.get_recent_content_categories(6)
    except Exception:
        pass

    category_key = _pick_quote_category(recent_cats)
    category = QUOTE_CATEGORIES[category_key]

    # Build coin context string
    coin_lines = []
    for c in coins[:6]:
        sym = c.get("symbol", "").upper()
        p = c.get("current_price", 0)
        pct = c.get("price_change_percentage_24h_in_currency") or 0
        sign = "+" if pct > 0 else ""
        if p >= 1000:
            p_str = f"${p:,.0f}"
        elif p >= 1:
            p_str = f"${p:,.2f}"
        else:
            p_str = f"${p:.4f}"
        coin_lines.append(f"{sym}: {p_str} ({sign}{pct:.1f}%)")
    coin_context = "\n".join(coin_lines)

    mcap_str = ""
    if mcap >= 1e12:
        mcap_str = f"BTC market cap: ${mcap / 1e12:.2f}T"
    elif mcap >= 1e9:
        mcap_str = f"BTC market cap: ${mcap / 1e9:.0f}B"

    sign_24h = "+" if pct_24h > 0 else ""
    sign_7d = "+" if pct_7d > 0 else ""

    prompt = (
        f"BTC price: ${price:,.0f} ({sign_24h}{pct_24h:.1f}% 24h, {sign_7d}{pct_7d:.1f}% 7d)\n"
        f"{mcap_str}\n\n"
        f"Market data:\n{coin_context}\n\n"
        f"Task: {category['instruction']}\n\n"
        f"Output a single line with no line breaks. Keep under 220 characters. NO hashtags."
    )

    tweet = _call_claude(_SYSTEM, prompt, max_tokens=150)
    if not tweet:
        return None, category_key

    tweet = _clean_tweet(tweet)
    tweet = _strip_hashtags(tweet)
    tweet = tweet.replace('\n', ' ').replace('\r', ' ')
    tweet = _truncate_tweet(tweet, limit=220)
    tweet = re.sub(r'(\s*⚠️\s*NFA\.?\s*)+$', '', tweet).strip()
    tweet = tweet + ' ⚠️ NFA'
    tweet = re.sub(r'[^\w\s\$\%\.\,\!\?\-\:\;—\→\@🚀📉⚡👀⚠️\n]', '', tweet).strip()

    if _is_too_similar(tweet):
        logger.info("Quote tweet too similar to recent — retrying with different category")
        return None, category_key

    return tweet, category_key


def generate_opinion_tweet(
    price: float,
    pct_24h: float,
    pct_7d: float,
    coins: list[dict],
    defi_context: str | None = None,
) -> str | None:
    """
    Generate an opinionated market take. Used by tweet_generators.generate_opinion_tweet().
    """
    if not is_available():
        return None

    sign_24h = "+" if pct_24h > 0 else ""
    sign_7d = "+" if pct_7d > 0 else ""

    coin_lines = []
    for c in coins[:4]:
        sym = c.get("symbol", "").upper()
        p = c.get("current_price", 0)
        pct = c.get("price_change_percentage_24h_in_currency") or 0
        sign = "+" if pct > 0 else ""
        if p >= 1000:
            p_str = f"${p:,.0f}"
        elif p >= 1:
            p_str = f"${p:,.2f}"
        else:
            p_str = f"${p:.4f}"
        coin_lines.append(f"{sym}: {p_str} ({sign}{pct:.1f}%)")

    defi_line = f"\n{defi_context}" if defi_context else ""

    prompt = (
        f"Write one single sentence crypto opinion tweet. "
        f"BTC is at ${price:,.0f} ({pct_24h:+.1f}% 24h, {pct_7d:+.1f}% 7d). "
        "Take a clear directional stance — bullish or bearish. "
        "No hedging. No questions. No hashtags. "
        "Do not use first person language — no 'I'm', 'my', 'I think', 'I'm betting'. "
        "State the market call as a fact, not a personal position. "
        "Single line only. No line breaks whatsoever. "
        "The tweet MUST end with the exact string: ⚠️ NFA — both the emoji and the word NFA must be present. "
        "Max 220 chars. Emojis only from 🚀📉⚡👀. "
        "Write it now, nothing else."
    )

    tweet = _call_claude(_SYSTEM, prompt, max_tokens=150)
    if not tweet:
        return None

    tweet = _clean_tweet(tweet)
    tweet = _strip_hashtags(tweet)
    tweet = re.sub(r'[^\w\s\$\%\.\,\!\?\-\:\;\—\@🚀📉⚡👀⚠️]', '', tweet)
    tweet = _truncate_tweet(tweet)
    if not tweet.endswith("⚠️ NFA"):
        tweet = tweet.rstrip() + " ⚠️ NFA"

    if _is_too_similar(tweet):
        return None

    return tweet


def generate_engagement_tweet(
    price: float,
    pct_24h: float,
    pct_7d: float,
    coins: list[dict],
) -> str | None:
    """
    Generate a bold, opinionated engagement tweet. Used by tweet_generators.generate_engagement_tweet().
    """
    if not is_available():
        return None

    prompt = (
        f"Write a single tweet about the current crypto market. Be direct and specific. "
        "State a price level, trend, or market structure observation. "
        "No questions. No personal pronouns. No hashtags. No line breaks. "
        "Must end with ⚠️ NFA. Under 220 characters. "
        "Use only these emojis if any: 🚀📉⚡👀. "
        f"Context: BTC ${price:,.0f} ({pct_24h:+.1f}% 24h, {pct_7d:+.1f}% 7d)."
    )

    tweet = _call_claude("You are @CoinWatchAlert, a crypto market signal account. Write factual market observations about price levels, trends, and structure. No financial advice. No buy/sell calls. Direct and concise.", prompt, max_tokens=120)
    if not tweet:
        return None

    tweet = _clean_tweet(tweet)
    tweet = _strip_hashtags(tweet)
    tweet = _truncate_tweet(tweet, limit=200)
    if not tweet.endswith("⚠️ NFA"):
        tweet = tweet.rstrip() + " ⚠️ NFA"

    return tweet


def generate_morning_recap_from_market(
    btc: dict, coins: list[dict], context: str | None = None
) -> str | None:
    """
    Generate a morning recap tweet from live market data (for tweet_generators.py).
    Different from generate_morning_recap() which takes headlines.
    context: pre-formatted single-line string with real numbers built by tweet_generators.
    """
    if not is_available():
        return None

    price = btc.get("current_price", 0)
    if not price or price <= 0:
        return None

    context_block = context or ""
    green = sum(1 for c in coins if (c.get("price_change_percentage_24h_in_currency") or 0) > 0)
    total = len(coins)

    prompt = (
        "Format this EXACTLY as shown — use real line breaks, not spaces:\n"
        "Line 1: BTC ${btc_price} ({btc_pct}) {emoji}\n"
        "Line 2: ETH ${eth_price} ({eth_pct}) {emoji}\n"
        "[blank line]\n"
        "Line 3: {top_coin} top gainer +{pct}% ⚡  (omit this line entirely if no top gainer data)\n"
        f"Line 4: {green}/{total} coins green\n"
        "[blank line]\n"
        "Line 5: {market_read}. ⚠️ NFA\n\n"
        f"Use the data: {context_block}\n\n"
        "Output only the formatted lines with blank lines between sections. Nothing else."
    )

    tweet = _call_claude(_ANALYST_SYSTEM, prompt, max_tokens=200)
    if not tweet:
        return None

    tweet = tweet.strip()
    return _truncate_tweet(tweet)


# ── Plain-text fallbacks ──────────────────────────────────────────────────────

def _plain_news_tweet(title: str, url: str, hashtags: str) -> str:
    max_title = 200
    if len(title) > max_title:
        title = title[:max_title - 1] + "…"
    parts = [f"📰 {title}", url, hashtags]
    return _truncate_tweet("\n".join(p for p in parts if p))


def _plain_morning_recap(headlines: list[str]) -> str:
    intro = "☀️ Morning crypto update:"
    items = " | ".join(h[:60] for h in headlines[:3])
    return f"{intro} {items}"[:220]


def generate_reply(tweet_text: str) -> str | None:
    """
    Generate a sharp analyst reply to a tweet from a target account.

    Adds a specific data point or price level. No sycophancy. No "great point".
    Takes a clear stance. Under 200 chars. No hashtags.
    """
    if not is_available():
        return None

    prompt = (
        f"Reply to this tweet with a sharp analyst take:\n\n\"{tweet_text}\"\n\n"
        "Rules:\n"
        "- Add one specific data point, price level, or on-chain stat they didn't mention\n"
        "- Take a clear stance — agree with evidence or push back with a counter-call\n"
        "- No sycophancy. Never start with 'Great point', 'Exactly', 'Well said', "
        "'Agree', 'This', or any variation\n"
        "- No questions. Declarative statements only\n"
        "- Under 200 characters. No hashtags."
    )

    result = _call_claude(_SYSTEM, prompt, max_tokens=120)
    if not result:
        return None
    result = _clean_tweet(result)
    result = _strip_hashtags(result)
    return _truncate_tweet(result, limit=200)


def generate_quote_retweet(original_text: str) -> str:
    """Add analyst context to someone else's tweet (requires Twitter Basic tier)."""
    if not config.ANTHROPIC_API_KEY:
        snippet = original_text[:80].rsplit(" ", 1)[0] + "…" if len(original_text) > 80 else original_text
        return f"Context: {snippet}"

    prompt = (
        "Add a sharp analyst take to this tweet. "
        "Make a directional call or state a clear implication — no questions, no hedging ('could see', 'might', 'possibly'). "
        "One or two sentences max. No hashtags. Under 220 chars.\n\n"
        f"Tweet: {original_text}"
    )
    result = _call_claude(_ANALYST_SYSTEM, prompt, max_tokens=100)
    if result:
        return _clean_tweet(result)[:220]
    snippet = original_text[:80].rsplit(" ", 1)[0] + "…" if len(original_text) > 80 else original_text
    return f"Context: {snippet}"


def generate_geopolitical_tweet(story: dict) -> list[str]:
    """
    Generate a 3-tweet thread for a macro/geopolitical news story.

    Tweet 1: The event + immediate market impact. Bold, declarative, data point if possible.
    Tweet 2: The crypto/hard asset connection — why this moves BTC, oil, gold. Specific levels.
    Tweet 3: "Bottom line:" — directional call with timeframe.

    No numbering. No questions. Under 200 chars each. Returns [] on failure.
    """
    if not config.ANTHROPIC_API_KEY:
        logger.warning("ANTHROPIC_API_KEY not set – cannot generate geopolitical thread")
        return []

    title = story.get("title", "")
    source = story.get("source", "")
    commentary = story.get("commentary", "")

    context_block = f"Headline: {title}\nSource: {source}"
    if commentary:
        context_block += f"\nAnalyst note: {commentary}"

    prompt = (
        f"Write a 3-tweet thread about this macro/geopolitical story:\n\n"
        f"{context_block}\n\n"
        "Format — output exactly 3 lines, one tweet per line, nothing else:\n\n"
        "Tweet 1: The geopolitical event and its immediate market impact. "
        "Bold and declarative. Include a data point (price level, %, move) if possible. "
        "No numbering prefix.\n\n"
        "Tweet 2: The crypto and hard asset connection — why this moves BTC, gold, or oil. "
        "State specific price levels or on-chain context. No numbering prefix.\n\n"
        "Tweet 3: Start with 'Bottom line:' then give a directional call with a timeframe "
        "(e.g. 'by end of week', 'this quarter', 'within 30 days'). Committed stance. "
        "No numbering prefix.\n\n"
        "Hard rules:\n"
        "- No numbering (no '1/', '2/', '3/', '1.', etc.)\n"
        "- No questions anywhere\n"
        "- No hedging ('could', 'might', 'may', 'possibly')\n"
        "- No hashtags\n"
        "- Each tweet under 200 characters\n"
        "- Emojis only from 🚀📉⚡👀. No other emojis.\n"
        "- Analyst tone: direct and declarative throughout\n"
        "- Output ONLY the 3 tweet lines, nothing else"
    )

    try:
        message = _get_client().messages.create(
            model=MODEL,
            max_tokens=400,
            system=_ANALYST_SYSTEM,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = message.content[0].text.strip()
        tweets = [line.strip() for line in raw.splitlines() if line.strip()]
        tweets = [_truncate_tweet(t, limit=200) if len(t) > 200 else t for t in tweets]
        logger.info("Generated geopolitical thread (%d tweets): %.80s", len(tweets), title)
        return tweets
    except anthropic.APIError as exc:
        logger.warning("Claude API error generating geopolitical thread: %s", exc)
        return []
