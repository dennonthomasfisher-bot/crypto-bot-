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

# ── Format alternation for breaking news tweets ──────────────────────────────
# Alternates between paragraph style (0) and bullet style (1)



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
MAX_TWEET_LENGTH = 240  # stricter limit for price, news, narrative tweets


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


# ── AI refusal leak filter ───────────────────────────────────────────────────
_AI_REFUSAL_PHRASES = [
    "i need to",
    "i appreciate",
    "i cannot",
    "i can't",
    "i'm unable",
    "i am unable",
    "i can't help",
    "i can't assist",
    "as an ai",
    "my core directive",
    "conflicts with",
    "i must decline",
    "i can't generate",
    "i can't create",
    "i'm not able",
    "against my guidelines",
]

# Phrases that are especially dangerous at the start of a tweet
_AI_REFUSAL_START = [
    "i can't", "i cannot", "i'm unable", "i am unable",
    "i can't help", "i can't assist",
]


def _contains_ai_refusal(text: str) -> bool:
    """Return True if text contains leaked AI refusal language."""
    lower = text.lower()
    # Check start-of-string first (catches "I can't do that" openers)
    for phrase in _AI_REFUSAL_START:
        if lower.startswith(phrase):
            return True
    return any(phrase in lower for phrase in _AI_REFUSAL_PHRASES)


def _call_claude_safe(system: str, prompt: str, max_tokens: int = 120) -> str | None:
    """Call Claude and discard AI refusal responses. Retries once."""
    result = _call_claude(system, prompt, max_tokens)
    if result and _contains_ai_refusal(result):
        logger.warning("[SAFETY] AI refusal detected — discarding and retrying: %.80s", result)
        result = _call_claude(system, prompt, max_tokens)
        if result and _contains_ai_refusal(result):
            logger.warning("[SAFETY] AI refusal on retry — discarding entirely: %.80s", result)
            return None
    return result


# Shared system prompt for all news/briefing/quote-tweet generation.
_ANALYST_SYSTEM = (
    "You are CoinWatchAlert, an elite crypto market intelligence system. "
    "Your job is NOT to report news. Your job is to interpret market behaviour "
    "like a professional trader. Every tweet must feel like it gives the reader an edge.\n\n"

    "TWEET STRUCTURE (MANDATORY — exactly 3 lines, blank line between each):\n"
    "Line 1 — HOOK: Sharp, controversial, tension-based. Max 10-12 words. "
    "NEVER start with a coin name (ETH/BTC/SOL) or price. "
    "NEVER use 'X is Y' structure. Start with action, implication, or tension.\n"
    "Line 2 — WHAT'S HAPPENING: One factual sentence. What is actually going on beneath the surface.\n"
    "Line 3 — WHAT IT MEANS / WHAT HAPPENS NEXT: One opinionated sentence. Take a stance. "
    "Include a scenario (if X → then Y) or what smart money does here.\n"
    "EVERY TWEET MUST INCLUDE: what is happening, what it means, what likely happens next.\n"
    "OPTIONAL: Line 3 can end with a short punchy question if it adds tension.\n\n"

    "EDGE FRAMEWORK — include at least ONE of:\n"
    "- Liquidity (where money is sitting)\n"
    "- Positioning (who is trapped / winning)\n"
    "- Timing (why now matters)\n"
    "- Narrative shifts (early / mid / late stage)\n\n"

    "STYLE:\n"
    "- Tone: calm, sharp, confident, experienced trader\n"
    "- Short sentences. Max 15 words per sentence.\n"
    "- Slightly contrarian when appropriate\n"
    "- Each line is ONE sentence — never split across two lines\n\n"

    "FORBIDDEN:\n"
    "- Starting with coin names: 'ETH...', 'BTC...', 'SOL...'\n"
    "- Neutral reporting: 'is happening', 'is increasing', 'shows growth'\n"
    "- Hype words: 'bullish', 'bearish', 'looking strong', 'gaining momentum'\n"
    "- Weak phrasing: 'this signals', 'this suggests', 'worth watching', "
    "'remains to be seen', 'interesting to see'\n"
    "- Hedging: 'could', 'might', 'may', 'potentially', 'possibly', 'likely'\n"
    "- Raw price-only statements: never post 'BTC $66K (-2.3%)' without explaining what it means\n"
    "- News-style openings: '[COIN] BREAKS...', '[NAME] SAYS...'\n"
    "- Headline repetition. Generic observations. Fluff.\n"
    "- No hashtags. No URLs. No NFA. No 'via'.\n"
    "- Max 1 emoji, at the very start only. Allowed: ⚡🚨📉🔴🟢👀\n\n"

    "STRONG HOOKS (examples):\n"
    "- 'This level decides what happens next'\n"
    "- 'The reaction here matters more than the move'\n"
    "- 'Smart money is already positioned for this'\n"
    "- 'Something is building here and most traders don't see it'\n"
    "- 'This doesn't happen randomly'\n\n"

    "QUALITY CHECK:\n"
    "1. If line 1 starts with a coin name (ETH/BTC/SOL/ADA/etc) — REWRITE IT.\n"
    "2. If line 1 uses a weak 'X is Y' structure over 8 words — REWRITE IT.\n"
    "3. If the post lacks a clear takeaway — REWRITE IT.\n"
    "4. If it reads like a news headline — REFRAME as interpretation.\n"
    "5. Every sentence must answer: WHY does this matter? or WHAT happens next?\n\n"

    "ENGAGEMENT LAYER: When instructed, use one of these (never more than one):\n"
    "- TENSION: 'This doesn't look right' / 'Something is building here'\n"
    "- QUESTION: 'Does this hold or break?' / 'Are we early or late?'\n"
    "- CONTRARIAN: 'Most traders are positioned wrong here'\n\n"

    "DATA INTERPRETATION (MANDATORY):\n"
    "Data without interpretation is noise. Every number must answer: "
    "what does this mean right now? Never post raw price data without interpretation. "
    "Replace data-only lines like 'BTC $66K (-2.3%)' with insight-led lines like "
    "'Price is holding. Participation isn't. That divergence matters.' "
    "Structure: observation → what it means → implication. Never just report.\n\n"

    "GOAL: Make the reader feel 'I understand what's happening better than everyone else now.'\n\n"

    "CLOSING LINE (MANDATORY):\n"
    "Every tweet must end with a single punchy closing line on its own line. "
    "One sentence. Declarative, not a question. Forward-looking or consequential.\n"
    "Examples: 'Bitcoin stops being optional.' / 'This doesn't end quietly.' / "
    "'Watch what institutions do next week.' / 'Most people will miss the timing.' / "
    "'The setup is already in place.'"
)


_ENGAGEMENT_TACTICS = [
    "\nENGAGEMENT MODE: Use TENSION — introduce uncertainty or unease. "
    "Something feels off, something is building, this move is being misread.",
    "\nENGAGEMENT MODE: Use QUESTION — end line 3 with a short punchy question. "
    "'Does this hold or break?' / 'Are we early or late?'",
    "\nENGAGEMENT MODE: Use CONTRARIAN — challenge the crowd. "
    "'Most traders are positioned wrong here' / 'This is where people get trapped'",
]


_COIN_NAME_RE = re.compile(
    r'^[⚡🚨📉🔴🟢👀\s]*(BTC|ETH|SOL|BNB|XRP|ADA|DOGE|AVAX|DOT|LINK|'
    r'MATIC|UNI|ATOM|LTC|ALGO|NEAR|FTM|APT|ARB|SUI|INJ|TIA|SEI|TAO|'
    r'BITCOIN|ETHEREUM|SOLANA|CARDANO)\b',
    re.IGNORECASE,
)
_WEAK_OPENER_RE = re.compile(
    r'^[⚡🚨📉🔴🟢👀\s]*\w+\s+(is|are|has|have|was|were|shows?|remains?)\s',
    re.IGNORECASE,
)


_FORBIDDEN_PHRASES_RE = re.compile(
    r'bullish|bearish|looking strong|gaining momentum|'
    r'is happening|is increasing|shows growth|going crazy|'
    r'watch what happens|interesting to see|market reacting|'
    r'could go either way|wait and see',
    re.IGNORECASE,
)

# Patterns indicating a tweet has forward implication (cause→effect)
_FORWARD_IMPLICATION_RE = re.compile(
    r'if .+ then|if .+ →|breaks? .+ heads?|holds? .+ expect|'
    r'loses? .+ drops?|reclaims? .+ targets?|'
    r'trap|liquidat|squeeze|flush|next|'
    r'sets up|leads to|opens the door|positions? for|'
    r'this means|what happens|expansion|compression',
    re.IGNORECASE,
)

# Vague consequence patterns — non-committal hedging disguised as outcome
_VAGUE_CONSEQUENCE_RE = re.compile(
    r'could move|could lead|may result|might see|'
    r'watch closely|keep an eye|stay tuned|remains unclear',
    re.IGNORECASE,
)


def _needs_regen(text: str) -> bool:
    """Return True if the tweet has weak opener, forbidden phrases, or lacks implication."""
    first_line = text.split("\n")[0].strip()

    # Check 1: coin name opener
    if _COIN_NAME_RE.match(first_line):
        logger.debug("Regen trigger: first line starts with coin name: %.60s", first_line)
        return True

    # Check 2: weak "X is Y" opener
    if _WEAK_OPENER_RE.match(first_line) and len(first_line.split()) > 8:
        logger.debug("Regen trigger: weak 'X is Y' opener: %.60s", first_line)
        return True

    # Check 3: forbidden phrases
    if _FORBIDDEN_PHRASES_RE.search(text):
        logger.debug("Regen trigger: forbidden phrase detected in: %.60s", text)
        return True

    # Check 4: vague consequences
    if _VAGUE_CONSEQUENCE_RE.search(text):
        logger.debug("Regen trigger: vague consequence detected: %.60s", text)
        return True

    # Check 5: forward implication — tweet must answer "what happens next?"
    # Only check multi-line tweets (single-line context sentences are exempt)
    if "\n" in text and not _FORWARD_IMPLICATION_RE.search(text):
        logger.debug("Regen trigger: no forward implication found: %.60s", text)
        return True

    return False


def _engagement_directive() -> str:
    """Return an engagement instruction ~30% of the time, empty string otherwise."""
    if random.random() < 0.30:
        return random.choice(_ENGAGEMENT_TACTICS)
    return ""


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


def _compute_price_context(coin_id: str, pct_change: float) -> dict[str, str]:
    """Derive trend/volume/structure context from recent price action.

    Returns dict with keys: trend, volume, structure.
    Uses heuristics based on the move magnitude — does NOT call external APIs
    to keep latency low. These provide structured hints to the prompt.
    """
    abs_pct = abs(pct_change)

    # Trend context based on move direction and magnitude
    if pct_change > 8:
        trend = "Strong breakout move — momentum accelerating"
    elif pct_change > 4:
        trend = "Pushing higher with follow-through"
    elif pct_change > 0:
        trend = "Grinding up — slow but persistent"
    elif pct_change > -4:
        trend = "Drifting lower — sellers in control but no panic"
    elif pct_change > -8:
        trend = "Sharp move down — testing support"
    else:
        trend = "Flush move — likely liquidation cascade"

    # Volume context (inferred from move magnitude — large moves = high volume)
    if abs_pct > 6:
        volume = "Volume expanding with the move"
    elif abs_pct > 3:
        volume = "Moderate volume — conviction building"
    else:
        volume = "Low volume — move lacks conviction"

    # Structure context
    if pct_change > 6:
        structure = "Breaking out of recent range"
    elif pct_change > 2:
        structure = "Testing upper range boundary"
    elif pct_change > -2:
        structure = "Range-bound — compression before expansion"
    elif pct_change > -6:
        structure = "Testing lower range support"
    else:
        structure = "Breaking below support — failed structure"

    return {"trend": trend, "volume": volume, "structure": structure}


def generate_price_tweet(alert: dict) -> str:
    """
    Write a price alert tweet in the spaced layout:

        ⚡ SYMBOL DIRECTION_EMOJI

        $PRICE | SIGN PCT% WINDOW

        [one sharp market context line from Claude]
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

    # Compute structured context for richer prompts
    ctx = _compute_price_context(alert.get("coin_id", "bitcoin"), pct)

    if not config.ANTHROPIC_API_KEY:
        from price_monitor import format_price_tweet
        return format_price_tweet(alert)

    prompt = (
        f"{symbol} just moved {sign}{pct:.1f}% in {window}. Price: {price_str}.\n"
        f"Market context: {ctx['trend']}. {ctx['volume']}. {ctx['structure']}.\n\n"
        f"Write ONE sentence: what this move means and what level decides what happens next. "
        f"Use the market context to add depth — don't ignore it. "
        f"Data without interpretation is noise — don't repeat the number, explain the implication. "
        f"Trader voice. Direct. No hedging. No questions. No emojis. No hashtags. "
        f"Max 120 chars.\n\n"
        f"Output ONLY that sentence."
    )

    context_line = ""
    for attempt in range(1, 4):
        try:
            message = _get_client().messages.create(
                model=MODEL,
                max_tokens=60,
                system=_ANALYST_SYSTEM,
                messages=[{"role": "user", "content": prompt}],
            )
            context_line = message.content[0].text.strip().strip('"').strip("'")
            if _contains_ai_refusal(context_line):
                logger.warning("[SAFETY] AI refusal in price tweet — retry %d", attempt)
                context_line = ""
                time.sleep(2)
                continue
            context_line = _strip_unwanted_lines(context_line)
            if context_line and _needs_regen(context_line) and attempt < 3:
                logger.info("[AI] Price tweet weak opener — retry %d", attempt)
                context_line = ""
                time.sleep(2)
                continue
            break
        except anthropic.APIError as exc:
            logger.warning("Claude API error generating price tweet (attempt %d): %s", attempt, exc)
            if attempt < 3:
                time.sleep(2)

    if context_line:
        tweet = f"{header}\n\n{data}\n\n{context_line}"
    else:
        tweet = f"{header}\n\n{data}"

    return _strip_nfa(_truncate_tweet(tweet))


def generate_price_alert_tweet(alert: dict) -> str | None:
    """
    Ask Claude to write a single declarative price-alert tweet.

    Rules: no questions, no first person, no hashtags, no line breaks,
    max 220 chars, emojis only 🚀📉⚡👀.
    Returns None on API failure.
    """
    symbol = alert["symbol"]
    pct    = alert["pct_change"]
    price  = alert["price_usd"]
    window = alert["window"]
    sign   = "+" if pct > 0 else ""

    from price_monitor import _format_price
    price_str = _format_price(price)

    # Compute structured context for richer prompts
    ctx = _compute_price_context(alert.get("coin_id", "bitcoin"), pct)

    prompt = (
        f"{symbol} moved {sign}{pct:.1f}% in {window}. Price: {price_str}.\n"
        f"Market context: {ctx['trend']}. {ctx['volume']}. {ctx['structure']}.\n\n"
        f"Write a 3-line price alert. Blank line between each.\n\n"
        f"Line 1: HOOK — tension or implication, not just 'COIN MOVES X%'. Max 1 emoji at start.\n"
        f"Line 2: What's happening — use the market context to explain the structure.\n"
        f"Line 3: What it means — ONE sentence, directional stance with a conditional (if X → then Y).\n\n"
        f"Rules:\n"
        f"- Data without interpretation is noise. Don't just report the move — explain what it means NOW.\n"
        f"- Observation → what it means → implication. Never just report.\n"
        f"- Trader voice. Short sentences. Never start with coin name.\n"
        f"- No questions. No hashtags. No URLs. No hedging.\n"
        f"- Max {MAX_TWEET_LENGTH} chars.\n\n"
        f"Output ONLY the tweet."
    )

    if not config.ANTHROPIC_API_KEY:
        return None

    tweet = None
    for attempt in range(1, 4):
        try:
            message = _get_client().messages.create(
                model=MODEL,
                max_tokens=100,
                system=_ANALYST_SYSTEM,
                messages=[{"role": "user", "content": prompt}],
            )
            tweet = message.content[0].text.strip().strip('"').strip("'")
            if _contains_ai_refusal(tweet):
                logger.warning("[SAFETY] AI refusal in price alert — retry %d", attempt)
                tweet = None
                time.sleep(2)
                continue
            tweet = _strip_unwanted_lines(tweet)
            if tweet and _needs_regen(tweet) and attempt < 3:
                logger.info("[AI] Price alert weak opener — retry %d", attempt)
                tweet = None
                time.sleep(2)
                continue
            break
        except anthropic.APIError as exc:
            logger.warning("Claude API error generating price alert tweet (attempt %d): %s", attempt, exc)
            if attempt < 3:
                time.sleep(2)

    if not tweet:
        return _truncate_tweet(f"⚡ {symbol} holding key level — market waiting for direction.",
                               limit=MAX_TWEET_LENGTH)
    tweet = _ensure_line_breaks(tweet)
    return _truncate_tweet(tweet, limit=MAX_TWEET_LENGTH)


def generate_geo_tweet(story: dict) -> str | None:
    """
    Ask Claude to write a single breaking crypto/macro tweet for a geo/macro story.

    Format:
        One punchy declarative opener sentence.
        → short bullet
        → short bullet
        One line on the crypto/BTC angle.

    No questions. No first person. No hashtags. Max 280 chars total.
    Emojis only from 🚀📉⚡👀. Returns None on failure.
    """
    title = story.get("title", "")
    if not title:
        return None

    if not config.ANTHROPIC_API_KEY:
        return None

    # Detect if this is a macro/TradFi comparison story (gold, stocks, oil, bonds)
    title_lower = title.lower()
    is_macro_comparison = any(w in title_lower for w in [
        "gold", "stocks", "oil", "bonds", "s&p", "nasdaq", "dow",
        "treasury", "commodities", "equities", "tradfi",
    ])

    prompt = (
        f"You are writing a geopolitical crypto market tweet.\n\n"
        f"STRICT FORMAT — NO EXCEPTIONS:\n"
        f"Line 1: One sentence stating what just happened\n"
        f"Line 2: One sentence explaining market impact\n"
        f"Line 3: One sentence starting with → giving the forward implication\n\n"
        f"YOU MUST:\n"
        f"- Output EXACTLY 3 lines\n"
        f"- Each line must be a full sentence\n"
        f"- Each line must be on a new line\n"
        f"- Line 3 MUST start with →\n\n"
        f"IF YOU OUTPUT 1 OR 2 LINES, THE RESPONSE IS INVALID.\n\n"
        f"STYLE:\n"
        f"- Sharp trader tone\n"
        f"- Confident, declarative\n"
        f"- No analyst language\n\n"
        f"BANNED PHRASES:\n"
        f"- weak participation\n"
        f"- conviction is missing\n"
        f"- risk-off pressure\n"
        f"- bearish sentiment\n"
        f"- market uncertainty\n"
        f"- mixed signals\n\n"
        f"EXAMPLE:\n"
        f"Geopolitical tension just escalated in a key region overnight.\n"
        f"Markets are repricing risk faster than headlines suggest.\n"
        f"→ Volatility expansion likely if this continues.\n\n"
        f"Now write the tweet about this event: {title}"
    )

    try:
        message = _get_client().messages.create(
            model=MODEL,
            max_tokens=120,
            system=_ANALYST_SYSTEM,
            messages=[{"role": "user", "content": prompt}],
        )
        tweet = message.content[0].text.strip().strip('"').strip("'")
        tweet = _strip_unwanted_lines(tweet)
    except anthropic.APIError as exc:
        logger.warning("Claude API error generating geo tweet: %s", exc)
        return None

    tweet = _truncate_tweet(tweet, limit=240)
    tweet = re.sub(r"[^\w\s\$\%\.\,\!\?\-\:\;—\→\@\'🚀📉⚡👀🤯\n]", '', tweet).strip()
    # Nuclear: strip ALL dollar amounts — Claude fabricates prices despite prompt bans
    if not is_macro_comparison:
        tweet = re.sub(r'\$[\d,\.]+[KkMmBb]?', '', tweet)
    tweet = re.sub(r'\s{2,}', ' ', tweet).strip()
    return tweet


# Regex to detect a direct quote: "..." with a known powerful person nearby
_QUOTE_PERSON_RE = re.compile(
    r'(Fink|Saylor|CZ|Vitalik|Musk|Dimon|Powell|Trump|Gensler|Atkins|Bukele|'
    r'Wood|Lutnick|Ramaswamy|Bezos|Zuckerberg|Cathie\s+Wood|Larry\s+Fink|'
    r'Michael\s+Saylor|Elon\s+Musk|Jamie\s+Dimon)',
    re.IGNORECASE,
)
_DIRECT_QUOTE_RE = re.compile(r'["\u201c](.+?)["\u201d]')


def story_has_power_quote(story: dict) -> bool:
    """Return True if a story contains a direct quote from a named powerful person."""
    title = story.get("title", "")
    commentary = story.get("commentary", "") or ""
    text = f"{title} {commentary}"
    return bool(_DIRECT_QUOTE_RE.search(text) and _QUOTE_PERSON_RE.search(text))


def generate_quote_style_tweet(story: dict) -> str | None:
    """
    Generate a quote-style tweet when a news story contains a direct quote
    from a named powerful person.

    Format:
        🚨 [PERSON] JUST SAID:
        "[exact short quote]"
        [1-2 sentence analyst take on what it means for crypto]

    No price figures. Max 220 chars. Returns None on failure.
    """
    title = story.get("title", "")
    commentary = story.get("commentary", "") or ""
    text = f"{title} {commentary}"

    # Extract person and quote
    person_match = _QUOTE_PERSON_RE.search(text)
    quote_match = _DIRECT_QUOTE_RE.search(text)
    if not person_match or not quote_match:
        return None

    person = person_match.group(0).upper()
    raw_quote = quote_match.group(1)

    if not config.ANTHROPIC_API_KEY:
        return None

    prompt = (
        f"Write a tweet in this EXACT format:\n\n"
        f'🚨 {person} JUST SAID:\n\n'
        f'\"[short version of this quote: {raw_quote}]\"\n\n'
        f"[What this means for crypto. One sentence. Direct.]\n\n"
        f"Rules:\n"
        f"- Quote max 80 chars. Capture the key phrase only.\n"
        f"- The take must connect to crypto impact. No hedging.\n"
        f"- No price figures. No hashtags. No URLs. No questions.\n"
        f"- Max 220 chars. Trader voice.\n\n"
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
        tweet = _strip_unwanted_lines(tweet)
    except anthropic.APIError as exc:
        logger.warning("Claude API error generating quote-style tweet: %s", exc)
        return None

    tweet = _truncate_tweet(tweet, limit=220)
    # Strip fabricated dollar amounts
    tweet = re.sub(r'\$[\d,\.]+[KkMmBb]?', '', tweet)
    tweet = re.sub(r'\s{2,}', ' ', tweet).strip()
    return tweet


def generate_news_tweet(story: dict, *, high_conviction: bool = False) -> str | None:
    """
    Ask Claude to write a news tweet for a single crypto story.

    If high_conviction is True (score >= 8), the prompt uses stronger
    directional language with explicit bullish/bearish bias and clear
    opportunity/risk framing.
    Falls back to a plain formatted string if the API call fails.
    """
    title = story.get("title", "")
    hashtags = _pick_hashtags(story)

    if not config.ANTHROPIC_API_KEY:
        logger.debug("ANTHROPIC_API_KEY not set – using plain news tweet format")
        return _plain_news_tweet(title, hashtags)

    # Fetch live BTC price for context
    btc_data = _fetch_btc_data()
    btc_price = btc_data.get("usd")
    pct_24h = btc_data.get("usd_24h_change")
    if btc_price and btc_price > 0:
        sign = "+" if pct_24h and pct_24h > 0 else ""
        pct_str = f" ({sign}{pct_24h:.1f}% 24h)" if pct_24h is not None else ""
        price_context = f"BTC is currently at ${btc_price:,.0f}{pct_str}."
    else:
        price_context = ""

    conviction_block = ""
    if high_conviction:
        conviction_block = (
            "\nHIGH CONVICTION MODE — this story scored 8+:\n"
            "- Take a clear directional stance (bullish or bearish). No neutrality.\n"
            "- Use stronger wording: 'likely', 'expect', 'this sets up for'. Reduce uncertainty.\n"
            "- Highlight risk OR opportunity — at least one must be present.\n"
            "- Mention what traders should watch or do next. Be specific about levels or catalysts.\n"
            "- Make the reader feel they NEED to pay attention right now.\n"
        )

    use_bullet = high_conviction

    if use_bullet:
        prompt = (
            f"Write a breaking crypto news tweet in ALL CAPS bullet style.\n\n"
            f"EXACT format (use → for bullets, blank line before bullets):\n"
            f"⚡ [HEADLINE IN ALL CAPS]\n\n"
            f"→ [KEY FACT — one short line in caps]\n"
            f"→ [IMPLICATION — one short line in caps]\n"
            f"→ [CRYPTO IMPACT — one short line in caps]\n\n"
            f"GOOD example:\n"
            f"⚡ SEC APPROVES SPOT ETH ETF\n\n"
            f"→ BLACKROCK AND FIDELITY FILINGS GREENLIT\n"
            f"→ ETH UP 8% IN MINUTES AFTER ANNOUNCEMENT\n"
            f"→ INSTITUTIONAL FLOODGATES NOW OPEN FOR ETH\n\n"
            f"Rules:\n"
            f"- ALL text in caps. Bullets use → prefix\n"
            f"- Never invent price levels. Only use numbers from the headline or: {price_context}\n"
            f"- Never include URLs, links, or source attributions\n"
            f"- Never start with 'BITCOIN' — vary the opening\n"
            f"- Where genuinely applicable, include a brief historical comparison e.g. 'LAST TIME WE SAW THIS WAS...' or 'SIMILAR TO THE 2021 DEFI RUN' — never forced\n"
            f"- Do not repeat the headline in different words. Extract the insight BEHIND the headline\n"
            f"- No hashtags. Max 240 chars total.\n"
            f"CLOSING LINE (mandatory, mixed case, NOT caps): One sharp analytical "
            f"sentence. Must include a forward-looking implication or directional bias. "
            f"No summaries. No hedging. Max 14 words.\n"
            f"{conviction_block}"
            f"{_engagement_directive()}\n"
            f"Headline: {title}\n\n"
            f"Output ONLY the tweet text, nothing else."
        )
    else:
        prompt = (
            f"Write a breaking crypto news tweet. Exactly 3 lines, blank line between each.\n\n"
            f"Line 1: THE HEADLINE — caps or near-caps, punchy, no fluff. Max 1 emoji at the very start.\n"
            f"Line 2: ONE concrete fact or number that matters. Not a restatement.\n"
            f"CLOSING LINE (mandatory, mixed case, NOT caps): One sharp analytical "
            f"sentence. Must include a forward-looking implication or directional bias. "
            f"No summaries. No hedging. Max 14 words.\n\n"
            f"GOOD example (each line is ONE sentence):\n"
            f"\"⚡ BTC REJECTED AT $70.6K\n\n"
            f"Bears have controlled every bounce for 5 days straight.\n\n"
            f"$68K breaks and this thing heads straight to $65K.\"\n\n"
            f"Rules:\n"
            f"- Never invent price levels. Only use numbers from the headline or: {price_context}\n"
            f"- Never include URLs, links, or source attributions\n"
            f"- Never use 'this signals', 'this suggests', 'this indicates'\n"
            f"- Never start with 'Bitcoin' — vary the opening\n"
            f"- Where genuinely applicable, include a brief historical comparison e.g. 'Last time we saw this was...' or 'Similar to the 2021 DeFi run' — never forced\n"
            f"- Do not repeat the headline in different words. Extract the insight BEHIND the headline\n"
            f"- No hashtags. Max 220 chars total.\n"
            f"{conviction_block}"
            f"{_engagement_directive()}\n"
            f"Headline: {title}\n\n"
            f"Output ONLY the tweet text, nothing else."
        )

    last_exc: anthropic.APIError | None = None
    for attempt in range(1, 4):
        try:
            message = _get_client().messages.create(
                model=MODEL,
                max_tokens=150,
                system=_ANALYST_SYSTEM,
                messages=[{"role": "user", "content": prompt}],
            )
            tweet = message.content[0].text.strip().strip('"').strip("'")
            if _contains_ai_refusal(tweet):
                logger.warning("[SAFETY] AI refusal in news tweet — retry %d", attempt)
                time.sleep(2)
                continue
            if "SKIP" in tweet:
                logger.info("Claude returned SKIP for news tweet — skipping story")
                return None
            tweet = _strip_unwanted_lines(tweet)
            tweet = re.sub(r'https?://\S+', '', tweet).strip()
            tweet = _ensure_line_breaks(tweet)
            tweet = _truncate_tweet(tweet, limit=MAX_TWEET_LENGTH)
            # Regen check: reject weak/banned openers on first attempt
            if _needs_regen(tweet) and attempt < 3:
                logger.info("[AI] Weak opener detected — regenerating (attempt %d)", attempt)
                time.sleep(2)
                continue
            logger.info("[AI] High-conviction tweet generated")
            return tweet
        except anthropic.APIError as exc:
            last_exc = exc
            logger.warning("Claude API error (attempt %d/3) generating news tweet: %s", attempt, exc)
            if attempt < 3:
                time.sleep(5)

    logger.warning("All 3 attempts failed – falling back to plain tweet")
    return _plain_news_tweet(title, hashtags)


def generate_morning_recap(headlines: list[str]) -> str:
    """
    Morning recap tweet — insight-driven, not a data dump.
    3-part structure: what happened → what it means → what to watch.
    Falls back to interpretation-style fallback if Claude fails.
    """
    _FALLBACK = "⚡ Market is compressing after overnight moves — expansion likely follows."

    if not headlines:
        return _FALLBACK

    numbered = "\n".join(f"{i+1}. {h}" for i, h in enumerate(headlines[:3]))

    if not config.ANTHROPIC_API_KEY:
        logger.debug("ANTHROPIC_API_KEY not set – using fallback")
        return _FALLBACK

    prompt = (
        "Write a morning crypto recap as a single insight-driven tweet. "
        "3 lines, blank line between each.\n\n"
        "Line 1 — WHAT HAPPENED: The dominant overnight move or theme only. "
        "Not a data dump — one sharp observation. Don't list coins.\n"
        "Line 2 — WHAT IT MEANS: Interpretation — strength/weakness, "
        "positioning shift, or conviction read.\n"
        "Line 3 — WHAT TO WATCH: Forward implication — a level, scenario, "
        "or condition that decides what happens next.\n\n"
        "Rules:\n"
        "- Do NOT output raw price bullets like 'BTC $66K (+0.4%)'. "
        "All data must be paired with meaning.\n"
        "- The tweet MUST answer 'What matters next?'\n"
        "- Use ONLY info from the headlines below — never invent prices\n"
        "- Max 240 chars total. No hashtags. No URLs. No hedging.\n"
        "- Max 1 emoji at start. Allowed: ⚡🚨📉🔴🟢👀\n\n"
        f"Headlines:\n{numbered}\n\n"
        "Output ONLY the tweet, nothing else."
    )

    for attempt in range(1, 4):
        try:
            message = _get_client().messages.create(
                model=MODEL,
                max_tokens=120,
                system=_ANALYST_SYSTEM,
                messages=[{"role": "user", "content": prompt}],
            )
            tweet = message.content[0].text.strip()
            if _contains_ai_refusal(tweet):
                logger.warning("[SAFETY] AI refusal in morning recap — retry %d", attempt)
                time.sleep(2)
                continue
            tweet = _strip_unwanted_lines(tweet)
            tweet = _ensure_line_breaks(tweet)
            tweet = _truncate_tweet(tweet, limit=MAX_TWEET_LENGTH)
            if _needs_regen(tweet) and attempt < 3:
                logger.info("[AI] Morning recap weak — retry %d", attempt)
                time.sleep(2)
                continue
            logger.info("[AI] Morning recap generated: %.80s", tweet)
            return tweet
        except anthropic.APIError as exc:
            logger.warning("Claude API error for morning recap (attempt %d): %s", attempt, exc)
            if attempt < 3:
                time.sleep(2)

    logger.warning("All 3 morning recap attempts failed — using fallback")
    return _FALLBACK


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
        f"Write a {n}-tweet thread about: {topic}\n\n"
        "Output exactly 3 lines, one tweet per line. No numbering.\n\n"
        "Tweet 1: Bold opening claim. ALL CAPS or near-caps first phrase. "
        "One concrete number. Make people stop scrolling.\n\n"
        "Tweet 2: The evidence. One specific data point, metric, or on-chain signal. "
        "Short sentences. Trader-to-trader voice.\n\n"
        "Tweet 3: The punchline. Directional call with a timeframe. "
        "Full conviction. End with something screenshot-worthy.\n\n"
        "Rules:\n"
        "- No numbering (no '1/', '2/', etc.)\n"
        "- No questions. No hedging. No 'signals', 'suggests', 'indicates'\n"
        "- No hashtags. No URLs\n"
        "- Each tweet under 220 chars\n"
        "- Max 1 emoji per tweet, at the start only. Allowed: ⚡🚨📉👀\n"
        "- Never start with 'Bitcoin'\n"
        "- Write like a trader, not a journalist\n"
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
        raw = _strip_unwanted_lines(raw)
        tweets = [line.strip() for line in raw.splitlines() if line.strip()]
        tweets = [_truncate_tweet(t, limit=220) if len(t) > 220 else t for t in tweets]
        tweets = [re.sub(r"[^\w\s\$\%\.\,\!\?\-\:\;—\@\'🚀📉⚡👀⚠️\n]", '', t).strip() for t in tweets]
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
        [Punchy fact or observation. Short. Emphasis word.]

        [Context or what it means. One sentence.]

        [The call. What happens next. Direct.]
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

    context_block = f"\nLive data:\n{price_line}"
    if context:
        context_block += f"\n{context}"

    prompt = (
        f"You are writing a crypto market tweet.\n\n"
        f"STRICT FORMAT — NO EXCEPTIONS:\n"
        f"Line 1: One sentence stating the market situation\n"
        f"Line 2: One sentence explaining what it means\n"
        f"Line 3: One sentence starting with → giving the forward implication or price level\n\n"
        f"YOU MUST:\n"
        f"- Output EXACTLY 3 lines\n"
        f"- Each line must be a full sentence\n"
        f"- Each line must be on a new line\n"
        f"- Line 3 MUST start with →\n\n"
        f"IF YOU OUTPUT 1 OR 2 LINES, THE RESPONSE IS INVALID.\n\n"
        f"STYLE:\n"
        f"- Sharp trader tone\n"
        f"- Confident, declarative\n"
        f"- No analyst language\n\n"
        f"BANNED PHRASES:\n"
        f"- weak participation\n"
        f"- conviction is missing\n"
        f"- risk-off pressure\n"
        f"- bearish sentiment\n"
        f"- market uncertainty\n"
        f"- mixed signals\n\n"
        f"EXAMPLE:\n"
        f"Resistance rejected three times and sellers aren't letting up.\n"
        f"Structure is weakening on every bounce attempt.\n"
        f"→ Lose $67,500 and this flushes to $65K.\n\n"
        f"Market context:{context_block}\n\n"
        f"Now write the tweet."
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
        text = _strip_unwanted_lines(text)
        text = _clean_tweet(text)
        text = _strip_hashtags(text)
        text = _ensure_line_breaks(text)
        text = _truncate_tweet(text, limit=220)
        if _needs_regen(text):
            logger.info("[AI] Hot take weak opener — regenerating once")
            try:
                message2 = _get_client().messages.create(
                    model=MODEL, max_tokens=150,
                    system=_ANALYST_SYSTEM,
                    messages=[{"role": "user", "content": prompt}],
                )
                text2 = message2.content[0].text.strip().strip('"').strip("'")
                text2 = _strip_unwanted_lines(text2)
                text2 = _clean_tweet(text2)
                text2 = _strip_hashtags(text2)
                text2 = _ensure_line_breaks(text2)
                text2 = _truncate_tweet(text2, limit=220)
                if not _needs_regen(text2):
                    text = text2
            except Exception:
                pass  # keep original if regen fails
        logger.info("[AI] High-conviction tweet generated")
        return text
    except Exception as exc:
        logger.warning("Claude API call failed: %s", exc)
        return None




def _ensure_line_breaks(text: str) -> str:
    """If the tweet is a wall of text with no blank lines, insert them — max 3 blocks.

    Only splits after complete sentences ending with . ! or ? followed by a space
    and an uppercase letter or emoji. Never splits mid-sentence.
    """
    # Already formatted — just normalise excessive breaks
    if "\n\n" in text:
        return re.sub(r'\n{3,}', '\n\n', text)
    # Skip arrow/bullet-style tweets
    if re.search(r'[\n].*[→●]', text):
        return text
    # Only split after sentence-ending punctuation (.!?) followed by space + uppercase/emoji
    # Negative lookbehind prevents splitting after abbreviations like "$1.5B" or "U.S."
    parts = re.split(r'(?<=[.!?])\s+(?=[A-Z⚡🚨📉🔴🟢👀])', text)
    if len(parts) < 3:
        return text
    # Merge into exactly 3 blocks: first sentence, second sentence, everything else joined
    blocks = [parts[0], parts[1], " ".join(parts[2:])]
    result = "\n\n".join(blocks)
    if len(result) <= 280:
        return result
    return text


def _strip_nfa(text: str) -> str:
    """Remove all NFA disclaimers (inline, trailing, standalone) from text."""
    # Remove any line containing "NFA" as a word (handles emojis preceding it)
    lines = text.splitlines()
    lines = [l for l in lines if not re.search(r'\bNFA\b', l)]
    text = "\n".join(lines)
    # Clean up any double blank lines left behind
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text.strip()


def _strip_unwanted_lines(text: str) -> str:
    """Remove junk lines (SKIP, ---, Reasoning, Why, ** headers), then strip NFA.
    Nuclear: if 'SKIP' appears ANYWHERE in the final text, return empty string."""
    lines = text.splitlines()
    cleaned = []
    for line in lines:
        stripped = line.lstrip()
        # Drop lines starting with SKIP, ---, Reasoning:, or **
        if stripped.startswith("SKIP") or stripped.startswith("---") or stripped.startswith("Reasoning:") or stripped.startswith("**"):
            continue
        # Drop lines containing Why: or Reasoning:
        if "Why:" in line or "Reasoning:" in line:
            continue
        # Drop lines containing --- (separator)
        if "---" in line:
            continue
        cleaned.append(line)
    text = "\n".join(cleaned).strip()
    text = _strip_nfa(text)
    # Nuclear SKIP check: if SKIP appears anywhere in the final text, reject entirely
    if "SKIP" in text:
        return ""
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

_SYSTEM = """You are @CoinWatchAlert — a sharp crypto trader account. Think Coin Bureau meets Zach XBT. You make calls, not commentary. People follow you to screenshot your predictions later.

ABSOLUTE RULES:
- Under 275 characters per tweet
- ZERO hashtags. Zero URLs. Zero 'via' attributions
- Max 1 emoji per tweet, at the very start. Allowed: ⚡🚨📉🔴🟢👀
- Only use price data provided — never fabricate numbers
- No "WAGMI", "LFG", "NFA", or crypto bro speak
- Do NOT wrap your response in quotes

NEVER START WITH:
- "Bitcoin" / "Worth noting" / "Interesting" / "Fun fact" / "Hot take:" / "Let's talk" / "Quick thought"

NEVER USE (banned phrases):
- "this signals" / "this suggests" / "this indicates" / "worth watching"
- "remains to be seen" / "time will tell" / "could go either way"
- "could" / "might" / "may" / "potentially" / "possibly" / "likely"
These are news feed phrases. You're a trader. Make a CALL.

VOICE:
- Write like a trader texting a group chat. Short sentences. Max 15 words each.
- MAKE CALLS: "Breaks $X or dumps to $Y" — not "worth watching"
- Take a side. Every tweet has a DIRECTION. Never neutral.
- Be specific: price levels, timeframes, percentages
- Use contractions (don't, won't, can't)
- Write tweets people want to screenshot

FORMATTING:
- 2-3 short blocks separated by blank lines. Never walls of text.
- One thought per line. Short > long.

  OPINIONS:
    ⚡ BTC HOLDING $67.3K AFTER THAT 68K REJECTION

    Structure still weak — lower highs on the 4h.

    Reclaim 68.5k or this heads to 65k.

  RECAPS:
    ● BTC $67,300 (+2.1%) 🟢
    ● ETH $1,970 (+1.8%) 🟢
    ● SOL $95.50 (+3.2%) 🟢

    Market tone: cautious risk-on

  RAW COMMENTARY:
    PI bleeding -10.1% to $0.2028.
    $2B market cap and still no real utility.
    Below $0.19 and this goes to $0.15."""


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
        f"BTC: ${price:,.0f} ({sign_24h}{pct_24h:.1f}% 24h, {sign_7d}{pct_7d:.1f}% 7d)\n"
        f"{mcap_str}\n\n"
        f"Market:\n{coin_context}\n\n"
        f"Task: {category['instruction']}\n\n"
        f"Write exactly 3 lines with a blank line between each:\n"
        f"Line 1: THE TAKE in caps or bold phrasing. Key data point.\n"
        f"Line 2: One fact backing it. Short sentence.\n"
        f"Line 3: The call — ONE complete sentence with direction and conviction. Never split across two lines.\n\n"
        f"Trader voice. No hashtags. No URLs. No hedging. Under 260 chars."
    )

    tweet = _call_claude_safe(_ANALYST_SYSTEM, prompt, max_tokens=180)
    if not tweet:
        return None, category_key

    tweet = tweet.strip().strip('"').strip("'")
    tweet = _strip_unwanted_lines(tweet)
    tweet = _strip_hashtags(tweet)
    tweet = _truncate_tweet(tweet, limit=260)
    tweet = re.sub(r"[^\w\s\$\%\.\,\!\?\-\:\;—\→\@\'🚀📉⚡👀\n]", '', tweet).strip()

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
        f"BTC at ${price:,.0f} ({sign_24h}{pct_24h:.1f}% 24h, {sign_7d}{pct_7d:.1f}% 7d).\n\n"
        f"Write a 1-2 sentence opinion. Direct statement, not analysis.\n\n"
        f"Sound like a trader making a call, not an analyst explaining.\n"
        f"Slight edge or controversy preferred. Make the reader feel challenged.\n"
        f"Confident. Decisive. No hedging. No neutral tone.\n\n"
        f"BANNED: suggests, could, might, possibly, appears, seems, indicates, "
        f"bullish, bearish, looking strong, gaining momentum\n\n"
        f"Style examples:\n"
        f"'Most people in this market are exit liquidity.'\n"
        f"'If you're waiting for confirmation, you're already late.'\n"
        f"'Retail only shows up when risk is highest.'\n\n"
        f"Rules:\n"
        f"- 1-2 sentences MAX. No explanations.\n"
        f"- Never start with 'Bitcoin' or a coin name.\n"
        f"- No questions. No hashtags. No URLs.\n"
        f"- Max 1 emoji at the start. Max 200 chars.\n"
        f"Output ONLY the tweet, nothing else."
    )

    for attempt in range(1, 4):
        tweet = _call_claude_safe(_ANALYST_SYSTEM, prompt, max_tokens=180)
        if not tweet:
            if attempt < 3:
                time.sleep(2)
                continue
            return None

        tweet = tweet.strip().strip('"').strip("'")
        tweet = _strip_unwanted_lines(tweet)
        tweet = _strip_hashtags(tweet)
        tweet = re.sub(r"[^\w\s\$\%\.\,\!\?\-\:\;\—\@\'🚀📉⚡👀\n]", '', tweet)
        tweet = _truncate_tweet(tweet, limit=280)

        if _needs_regen(tweet) and attempt < 3:
            logger.info("[AI] Opinion tweet weak — retry %d", attempt)
            time.sleep(2)
            continue

        if _is_too_similar(tweet):
            return None

        logger.info("[AI] Opinion tweet generated")
        return tweet

    return None


def generate_market_open_tweet(
    btc_price: float, btc_pct: float, eth_price: float, eth_pct: float
) -> str | None:
    """Generate a high-conviction market open tweet with sharp trader voice."""
    if not is_available():
        return None

    prompt = (
        f"Write a sharp one-liner market open tweet.\n\n"
        f"Format: ⚡ [asset name] [price level] — [what it means]\n\n"
        f"Live data:\n"
        f"BTC ${btc_price:,.0f} ({btc_pct:+.1f}%)\n"
        f"ETH ${eth_price:,.0f} ({eth_pct:+.1f}%)\n\n"
        f"Examples:\n"
        f"'⚡ BTC holding $68k — compression building, expansion incoming'\n"
        f"'⚡ ETH reclaimed $2,400 — first sign of strength in weeks'\n"
        f"'⚡ BTC $67.2k — sellers exhausted, no follow-through on the drop'\n\n"
        f"Rules:\n"
        f"- Exactly ONE line. Starts with ⚡\n"
        f"- Must include asset name and price level from the live data\n"
        f"- Must include a clear read on what it means\n"
        f"- No generic 'Markets open'. No fluff. No hedging.\n"
        f"- Confident trader voice. Max 140 chars.\n\n"
        f"Return ONLY the one-liner. Nothing else."
    )

    result = _call_claude_safe(_ANALYST_SYSTEM, prompt, max_tokens=80)
    if not result:
        return None
    result = result.strip().strip('"').strip("'")
    if len(result) > 140:
        result = result[:137] + "..."
    return result


def generate_rotation_tweet(coins: list[dict]) -> str | None:
    """Generate a rotation/flow tweet showing where strength is moving."""
    if not is_available():
        return None

    coin_lines = "\n".join([f"{c['symbol']}: {c['pct']:+.1f}%" for c in coins[:6]])

    prompt = (
        f"Write a high-conviction crypto rotation tweet in a sharp trader voice.\n\n"
        f"You have this live price data:\n{coin_lines}\n\n"
        f"Rules:\n"
        f"- Max 240 characters\n"
        f"- EXACTLY 3 lines\n"
        f"- No emojis\n"
        f"- No generic phrases\n\n"
        f"Structure:\n"
        f"Line 1: Where is strength rotating TO right now "
        f"(name 1-2 coins showing relative strength)\n"
        f"Line 2: Which coins are lagging or showing weakness\n"
        f"Line 3: What this rotation signals — positioning or flow implication\n\n"
        f"Tone: sounds like smart money tracking flow, not retail watching prices.\n\n"
        f"Hard constraint: MUST reference specific coins from the data. "
        f"MUST imply where money is moving.\n\n"
        f"Return ONLY the tweet. No explanation."
    )

    result = _call_claude_safe(_ANALYST_SYSTEM, prompt, max_tokens=120)
    if not result or len(result) < 20:
        return None
    result = result.strip().replace("\\n", "\n")
    if len(result) > 240:
        result = result[:237] + "..."
    return result


def generate_opinion_bomb() -> str | None:
    """Generate a single-sentence conviction tweet. No data, no chart, pure edge.

    Must challenge conventional thinking or reframe market behaviour.
    Exactly 1 sentence. No questions. No explanations.
    """
    if not is_available():
        return None

    prompt = (
        "Write exactly ONE sentence about crypto markets.\n\n"
        "It must:\n"
        "- Challenge conventional thinking or reframe market behaviour\n"
        "- Sound like pure conviction from a trader who has seen everything\n"
        "- Be a direct statement, not analysis or explanation\n"
        "- Make the reader stop scrolling and think\n\n"
        "BANNED: suggests, could, might, possibly, appears, seems, indicates, "
        "bullish, bearish, looking strong, gaining momentum\n\n"
        "Style examples:\n"
        "'Volume spikes before price — always.'\n"
        "'Most altcoins are just liquidity traps.'\n"
        "'Breakdowns happen quietly first.'\n"
        "'The best trades feel wrong when you enter them.'\n"
        "'Leverage doesn't create winners — it accelerates outcomes.'\n\n"
        "Rules:\n"
        "- Exactly 1 sentence. No second sentence. No explanation.\n"
        "- No data references. No price levels. No coin names.\n"
        "- No questions. No hashtags. No URLs.\n"
        "- Max 1 emoji at start. Max 140 chars.\n"
        "Output ONLY the sentence, nothing else."
    )

    for attempt in range(1, 4):
        tweet = _call_claude_safe(_ANALYST_SYSTEM, prompt, max_tokens=60)
        if not tweet:
            if attempt < 3:
                time.sleep(2)
                continue
            return None

        tweet = tweet.strip().strip('"').strip("'")
        tweet = _strip_unwanted_lines(tweet)
        tweet = _strip_hashtags(tweet)
        tweet = _truncate_tweet(tweet, limit=140)

        if _needs_regen(tweet) and attempt < 3:
            logger.info("[AI] Opinion bomb weak — retry %d", attempt)
            time.sleep(2)
            continue

        logger.info("[AI] Opinion bomb generated: %s", tweet)
        return tweet

    return None


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
        f"BTC at ${price:,.0f} ({pct_24h:+.1f}% 24h, {pct_7d:+.1f}% 7d).\n\n"
        f"Write EXACTLY 3 lines separated by newlines.\n\n"
        f"Line 1: ⚡ + DECLARATION. Write as if you already know the likely outcome. "
        f"Slightly provocative — make people want to reply.\n"
        f"Line 2: Specific market insight with context. What it actually means.\n"
        f"Line 3: Start with → then a SPECIFIC price level or trigger. "
        f"e.g. '→ $68,200 is the line — break it or fade back to $66K'\n\n"
        f"BANNED: weak participation, conviction is missing, risk-off pressure, "
        f"bearish sentiment, market uncertainty, mixed signals, watch the reaction, "
        f"market is compressing (without a level)\n\n"
        f"Rules:\n"
        f"- Write like you already have edge, not like you're reporting.\n"
        f"- Short confident sentences. No filler.\n"
        f"- Never start with 'Bitcoin'. Vary the opening.\n"
        f"- No hashtags. No URLs. No hedging.\n"
        f"- Each line must be a complete sentence. Do NOT merge lines into one.\n"
        f"- Final line MUST start with → and have a numeric level or conditional trigger.\n"
        f"- Max 1 emoji at the start. Max 280 chars.\n\n"
        f"Return exactly 3 lines separated by newline characters. Do not combine into one line."
    )

    tweet = _call_claude_safe(_ANALYST_SYSTEM, prompt, max_tokens=180)
    if not tweet:
        return None

    tweet = tweet.strip().strip('"').strip("'")
    tweet = _strip_unwanted_lines(tweet)
    tweet = _strip_hashtags(tweet)
    tweet = _truncate_tweet(tweet, limit=280)

    return tweet


def generate_morning_recap_from_market(
    btc: dict, coins: list[dict], context: str | None = None
) -> str | None:
    """
    Morning recap from live market data — insight-driven, not a data dump.
    3-part structure: what happened → what it means → what to watch.
    """
    _FALLBACK = "⚡ Market is compressing after overnight moves — expansion likely follows."

    if not is_available():
        return None

    price = btc.get("current_price", 0)
    if not price or price <= 0:
        return None

    context_block = context or ""
    green = sum(1 for c in coins if (c.get("price_change_percentage_24h_in_currency") or 0) > 0)
    total = len(coins)

    prompt = (
        "Write a morning recap as a single insight-driven tweet. "
        "3 lines, blank line between each.\n\n"
        "Line 1 — WHAT HAPPENED: The dominant overnight move. "
        "One sharp observation, not a list of coins.\n"
        "Line 2 — WHAT IT MEANS: Interpretation — who's winning, "
        f"where conviction sits. ({green}/{total} coins green.)\n"
        "Line 3 — WHAT TO WATCH: Forward implication — a level, scenario, "
        "or condition. Answer: what matters next?\n\n"
        "Rules:\n"
        "- Do NOT output raw price bullets. All data must be paired with meaning.\n"
        "- Use ONLY real data from below — never invent.\n"
        "- Max 240 chars. No hashtags. No URLs. No hedging.\n"
        "- Max 1 emoji at start. Allowed: ⚡🚨📉🔴🟢👀\n\n"
        f"Data: {context_block}\n\n"
        "Output ONLY the tweet, nothing else."
    )

    for attempt in range(1, 4):
        tweet = _call_claude_safe(_ANALYST_SYSTEM, prompt, max_tokens=150)
        if not tweet:
            if attempt < 3:
                time.sleep(2)
                continue
            return _FALLBACK

        tweet = tweet.strip()
        tweet = _strip_unwanted_lines(tweet)
        tweet = _ensure_line_breaks(tweet)
        tweet = _truncate_tweet(tweet, limit=MAX_TWEET_LENGTH)

        if _needs_regen(tweet) and attempt < 3:
            logger.info("[AI] Morning recap (market) weak — retry %d", attempt)
            time.sleep(2)
            continue

        logger.info("[AI] Morning recap (market) generated: %.80s", tweet)
        return tweet

    logger.warning("All 3 morning recap (market) attempts failed — using fallback")
    return _FALLBACK


# ── Plain-text fallbacks ──────────────────────────────────────────────────────

def _plain_news_tweet(title: str, hashtags: str) -> str:
    return _truncate_tweet("⚡ This isn't the news that matters — watch the reaction.")


def _plain_morning_recap(headlines: list[str]) -> str:
    return "⚡ Market is compressing after overnight moves — expansion likely follows."


_REPLY_SYSTEM = (
    "You are CoinWatchAlert replying to a high-visibility crypto tweet. "
    "Tone: calm, analytical, slightly cryptic. Observational not reactive. "
    "Confident not excited. You see what others miss.\n\n"
    "EACH REPLY MUST CONTAIN ONE OF:\n"
    "- CONTRAST: 'Everyone's watching X, but Y is moving'\n"
    "- REVERSAL: 'Looks obvious — usually isn't'\n"
    "- HIDDEN SIGNAL: 'Focus on reaction / liquidity / positioning, not headline'\n\n"
    "FORBIDDEN WORDS: bullish, bearish, moon, huge, massive, big move, going crazy\n\n"
    "RULES:\n"
    "- 1-2 lines max. Under 200 chars.\n"
    "- Never start with 'Great point', 'Exactly', 'Agree', 'This', 'So true'\n"
    "- No hashtags. No URLs. No hedging.\n"
    "- Add NEW insight — never repeat the original tweet.\n\n"
    "EXAMPLES:\n"
    "- 'Everyone's watching price. Liquidity's doing something else.'\n"
    "- 'Panic always looks obvious in hindsight.'\n"
    "- 'This is where people confuse momentum with strength.'\n"
    "- 'The reaction matters more than the move itself.'\n\n"
    "GOAL: Make readers curious enough to click the profile."
)


def generate_reply(tweet_text: str) -> str | None:
    """
    Generate a calm, analytical reply to a high-visibility crypto tweet.

    Observational, slightly cryptic. 1-2 lines. Under 200 chars.
    """
    if not is_available():
        return None

    question_directive = ""
    if random.random() < 0.25:
        question_directive = (
            "\n- End with a short cryptic question "
            "(e.g. 'Who's actually accumulating here?' / 'Is this the move or the setup?')"
        )

    prompt = (
        f"Reply to this tweet:\n\n\"{tweet_text}\"\n\n"
        "Rules:\n"
        "- 1-2 lines only. Under 200 chars. Every word must earn its place.\n"
        "- Use one of: CONTRAST, REVERSAL, or HIDDEN SIGNAL framing\n"
        "- Calm and analytical, not reactive or excited\n"
        "- Never use: bullish, bearish, moon, huge, massive, big move, going crazy\n"
        f"- Never start with 'Great point', 'Exactly', 'Agree', 'This'{question_directive}\n"
        "Output ONLY the reply text, nothing else."
    )

    result = _call_claude_safe(_REPLY_SYSTEM, prompt, max_tokens=120)
    if not result:
        return None
    result = _strip_unwanted_lines(result)
    result = _clean_tweet(result)
    result = _strip_hashtags(result)
    return _truncate_tweet(result, limit=200)


def generate_quote_retweet(original_text: str) -> str:
    """Add analyst context to someone else's tweet (requires Twitter Basic tier)."""
    if not config.ANTHROPIC_API_KEY:
        snippet = original_text[:80].rsplit(" ", 1)[0] + "…" if len(original_text) > 80 else original_text
        return f"Context: {snippet}"

    prompt = (
        "Add your take to this tweet. Make a call — direction, level, or conviction. "
        "No hedging. No questions. No hashtags. No URLs. Trader voice. "
        "Two sentences max. Under 220 chars.\n\n"
        f"Tweet: {original_text}"
    )
    result = _call_claude_safe(_ANALYST_SYSTEM, prompt, max_tokens=100)
    if result:
        result = _strip_unwanted_lines(result)
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
        f"Write a 3-tweet thread. One tweet per line, no numbering.\n\n"
        f"{context_block}\n\n"
        "Tweet 1: HOOK — what does this event MEAN, not just what happened. Tension or implication.\n\n"
        "Tweet 2: The crypto connection. Interpret through positioning/liquidity lens. Not just 'BTC reacts'.\n\n"
        "Tweet 3: 'Bottom line:' + directional call with conviction.\n\n"
        "Rules:\n"
        "- Data without interpretation is noise. Every fact must answer: what does this mean right now?\n"
        "- No numbering. No questions. No hedging.\n"
        "- No hashtags. No URLs. No 'via' credits.\n"
        "- Never start with 'Bitcoin'.\n"
        "- Each tweet under 200 chars.\n"
        "- Max 1 emoji per tweet at start. Allowed: ⚡🚨📉👀\n"
        "- Trader voice. Short sentences.\n"
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
        raw = _strip_unwanted_lines(raw)
        tweets = [line.strip() for line in raw.splitlines() if line.strip()]
        tweets = [_truncate_tweet(t, limit=200) if len(t) > 200 else t for t in tweets]
        logger.info("Generated geopolitical thread (%d tweets): %.80s", len(tweets), title)
        return tweets
    except anthropic.APIError as exc:
        logger.warning("Claude API error generating geopolitical thread: %s", exc)
        return []


# ── Volume anomaly tweet ──────────────────────────────────────────────────────

def generate_volume_anomaly_tweet(
    symbol: str, price: float, vol_ratio: float, price_change: float
) -> str | None:
    """Generate an insight-driven tweet about a volume anomaly detection."""
    if not config.ANTHROPIC_API_KEY:
        logger.warning("ANTHROPIC_API_KEY not set – cannot generate volume anomaly tweet")
        return None

    from price_monitor import _format_price
    price_str = _format_price(price)

    # Context framing based on vol_ratio and price direction
    if vol_ratio > 3:
        vol_context = "Aggressive volume expansion — 3x+ the 7-day average"
    else:
        vol_context = f"Volume running {vol_ratio:.1f}x the 7-day average"

    if price_change < -1:
        direction_context = "Price dipping on heavy volume — classic absorption pattern"
    elif price_change < 0.5:
        direction_context = "Price flat despite volume surge — someone is positioning quietly"
    else:
        direction_context = "Slight upward drift with volume confirmation — early accumulation signal"

    prompt = (
        f"{symbol} is showing a volume anomaly. Price: {price_str}.\n"
        f"Volume: {vol_context}.\n"
        f"Price change: {price_change:+.2f}% (barely moved).\n"
        f"Pattern: {direction_context}.\n\n"
        "Write a 3-line tweet. Blank line between each.\n\n"
        "Line 1 — HOOK: Something is happening beneath the surface. Tension, not hype.\n"
        "Line 2 — WHAT'S HAPPENING: Volume anomaly — big participation, small move. "
        "Frame as early signal, not confirmation.\n"
        "Line 3 — WHAT IT MEANS: Who is likely positioning and what it sets up.\n\n"
        "Rules:\n"
        "- Calm, observational, early-signal framing. Not hype.\n"
        "- This is a detection, not a call. Frame as 'something worth watching develops.'\n"
        "- Never start with the coin name.\n"
        f"- Max {MAX_TWEET_LENGTH} chars. No hashtags. No URLs.\n"
        "- Max 1 emoji at start. Allowed: ⚡👀\n"
        "Output ONLY the tweet."
    )

    for attempt in range(1, 4):
        try:
            message = _get_client().messages.create(
                model=MODEL,
                max_tokens=120,
                system=_ANALYST_SYSTEM,
                messages=[{"role": "user", "content": prompt}],
            )
            tweet = message.content[0].text.strip().strip('"').strip("'")
            if _contains_ai_refusal(tweet):
                logger.warning("[SAFETY] AI refusal in volume anomaly tweet — retry %d", attempt)
                time.sleep(2)
                continue
            tweet = _strip_unwanted_lines(tweet)
            tweet = _ensure_line_breaks(tweet)
            tweet = _truncate_tweet(tweet, limit=MAX_TWEET_LENGTH)
            if _needs_regen(tweet) and attempt < 3:
                logger.info("[AI] Volume anomaly tweet weak — retry %d", attempt)
                time.sleep(2)
                continue
            logger.info("[AI] Volume anomaly tweet generated for %s: %.80s", symbol, tweet)
            return tweet
        except Exception as exc:
            logger.warning("Claude API error for volume anomaly tweet (attempt %d): %s",
                           attempt, exc)
            if attempt < 3:
                time.sleep(2)

    logger.warning("All 3 volume anomaly tweet attempts failed")
    return None


# ── Narrative tweet ───────────────────────────────────────────────────────────

def generate_narrative_tweet(
    theme: str, story_count: int, summaries: list[str]
) -> str | None:
    """Generate a tweet about an emerging narrative detected from multiple sources."""
    if not config.ANTHROPIC_API_KEY:
        logger.warning("ANTHROPIC_API_KEY not set – cannot generate narrative tweet")
        return None

    summary_block = "\n".join(f"- {s}" for s in summaries[:5])

    prompt = (
        f"You are writing a crypto market tweet.\n\n"
        f"STRICT FORMAT — NO EXCEPTIONS:\n"
        f"Line 1: One sentence stating the market situation\n"
        f"Line 2: One sentence explaining what it means\n"
        f"Line 3: One sentence starting with → giving the forward implication or price level\n\n"
        f"YOU MUST:\n"
        f"- Output EXACTLY 3 lines\n"
        f"- Each line must be a full sentence\n"
        f"- Each line must be on a new line\n"
        f"- Line 3 MUST start with →\n\n"
        f"IF YOU OUTPUT 1 OR 2 LINES, THE RESPONSE IS INVALID.\n\n"
        f"STYLE:\n"
        f"- Sharp trader tone\n"
        f"- Confident, declarative\n"
        f"- No analyst language\n\n"
        f"BANNED PHRASES:\n"
        f"- weak participation\n"
        f"- conviction is missing\n"
        f"- risk-off pressure\n"
        f"- bearish sentiment\n"
        f"- market uncertainty\n"
        f"- mixed signals\n\n"
        f"EXAMPLE:\n"
        f"A new narrative is forming around AI tokens and most are late.\n"
        f"Three separate sources flagged the same theme in under 6 hours.\n"
        f"→ Early-stage rotation — smart money positions before confirmation.\n\n"
        f"Narrative topic: '{theme}' ({story_count} stories detected)\n"
        f"Headlines:\n{summary_block}\n\n"
        f"Now write the tweet."
    )

    for attempt in range(1, 4):
        try:
            message = _get_client().messages.create(
                model=MODEL,
                max_tokens=150,
                system=_ANALYST_SYSTEM,
                messages=[{"role": "user", "content": prompt}],
            )
            text = message.content[0].text.strip().strip('"').strip("'")
            if _contains_ai_refusal(text):
                logger.warning("[SAFETY] AI refusal in narrative tweet — retry %d", attempt)
                time.sleep(2)
                continue
            text = _strip_unwanted_lines(text)
            text = _clean_tweet(text)
            text = _strip_hashtags(text)
            text = _ensure_line_breaks(text)
            text = _truncate_tweet(text, limit=MAX_TWEET_LENGTH)
            if _needs_regen(text) and attempt < 3:
                logger.info("[AI] Narrative tweet weak — retry %d", attempt)
                time.sleep(2)
                continue
            logger.info("[AI] Narrative tweet generated for '%s': %.80s", theme, text)
            return text
        except Exception as exc:
            logger.warning("Claude API error for narrative tweet (attempt %d): %s", attempt, exc)
            if attempt < 3:
                time.sleep(2)

    logger.warning("All 3 narrative attempts failed — using fallback")
    return _truncate_tweet("⚡ Market is compressing — expansion comes next.",
                           limit=MAX_TWEET_LENGTH)
