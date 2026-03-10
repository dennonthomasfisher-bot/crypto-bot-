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

import json
import logging
import os
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
        # Return stale cache rather than zeros
        return _btc_cache["data"] or {}

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
    Ask Claude to write an analyst-voice price alert tweet.
    Adds market context rather than just restating the number.
    Falls back to the plain template if the API call fails.
    """
    symbol    = alert["symbol"]
    pct       = alert["pct_change"]
    price     = alert["price_usd"]
    window    = alert["window"]
    direction = "up" if pct > 0 else "down"
    sign      = "+" if pct > 0 else ""

    from price_monitor import _format_price
    price_str = _format_price(price)

    if not config.ANTHROPIC_API_KEY:
        from price_monitor import format_price_tweet
        return format_price_tweet(alert)

    prompt = (
        f"{symbol} is {direction} {sign}{pct:.1f}% in the last {window}. "
        f"Current price: {price_str}. "
        f"Write a punchy breaking-news style tweet (max 240 chars). "
        f"Start with an emoji + bold hook on line 1 (e.g. '⚡ {symbol} rips {sign}{pct:.1f}% in {window}'). "
        f"Line 2: add ONE key market context point (what level is in play, notable move relative to recent action). "
        f"No buy/sell calls. End with 1-2 relevant hashtags. "
        f"Output only the tweet text."
    )

    try:
        message = _get_client().messages.create(
            model=MODEL,
            max_tokens=120,
            system=_ANALYST_SYSTEM,
            messages=[{"role": "user", "content": prompt}],
        )
        return message.content[0].text.strip()
    except anthropic.APIError as exc:
        logger.warning("Claude API error generating price tweet: %s", exc)
        from price_monitor import format_price_tweet
        return format_price_tweet(alert)


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
        f"Write a breaking-news style tweet (max 240 chars) for this crypto headline.\n\n"
        f"Format:\n"
        f"Line 1: Hook — start with 'BREAKING:' or 'JUST IN:' or '🚨 LATEST:' then the core fact\n"
        f"Line 2 (optional): ONE supporting detail or why it matters\n\n"
        f"Rules: No buy/sell calls. Short punchy sentences. Use emojis meaningfully.\n"
        f"End with: {hashtags}\n\n"
        f"Headline: {title}\n\n"
        f"Output only the tweet text. No quotes."
    )

    last_exc: anthropic.APIError | None = None
    for attempt in range(1, 4):
        try:
            message = _get_client().messages.create(
                model=MODEL,
                max_tokens=120,
                system=_ANALYST_SYSTEM,
                messages=[{"role": "user", "content": prompt}],
            )
            tweet = message.content[0].text.strip()
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
        "Write a morning crypto market briefing tweet (max 240 characters).\n\n"
        "Format:\n"
        "Line 1: ☀️ MORNING BRIEF — [one-line summary of the dominant theme]\n"
        "Line 2+: 2-3 bullet points with key stories (use • or emoji as bullet)\n\n"
        "Short punchy sentences. No buy/sell signals.\n"
        "End with #Crypto\n\n"
        "Output only the tweet text. No quotes.\n\n"
        f"Today's top headlines:\n{numbered}"
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


def generate_thread(topic: str, n_tweets: int = 5) -> list[str]:
    """
    Ask Claude to write a Twitter thread on `topic`.

    Returns a list of tweet strings (each ≤280 chars), numbered 1/n … n/n.
    Falls back to an empty list on failure.
    """
    if not config.ANTHROPIC_API_KEY:
        logger.warning("ANTHROPIC_API_KEY not set – cannot generate thread")
        return []

    prompt = (
        f"Write a {n_tweets}-tweet Twitter thread about: {topic}\n\n"
        "Rules:\n"
        f"- Tweet 1 must be a strong hook that makes people want to read on. "
        f"Start it with a number or bold claim, not a question.\n"
        "- Each tweet must be under 270 characters (leave room for numbering).\n"
        "- Number each tweet like '1/' '2/' etc at the very start.\n"
        "- Use facts, data points, or specific examples — not vague statements.\n"
        "- Analyst voice: clear, direct, informative. No hype, no emojis except sparingly.\n"
        "- Do not make buy/sell calls.\n"
        f"- Final tweet ({n_tweets}/) should summarise the key takeaway.\n"
        "- Output only the tweets, one per line, nothing else."
    )

    try:
        message = _get_client().messages.create(
            model=MODEL,
            max_tokens=600,
            system=_ANALYST_SYSTEM,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = message.content[0].text.strip()
        tweets = [line.strip() for line in raw.splitlines() if line.strip()]
        # Hard-truncate any tweet that's over limit
        tweets = [t[:277].rsplit(" ", 1)[0] + "…" if len(t) > 280 else t for t in tweets]
        logger.info("Generated thread with %d tweets on: %s", len(tweets), topic)
        return tweets
    except anthropic.APIError as exc:
        logger.warning("Claude API error generating thread: %s", exc)
        return []


def generate_hot_take(context: str = "") -> str:
    """
    Generate a single punchy analyst-voice 'hot take' tweet on the current crypto
    landscape. Opinionated but grounded in data — no hype, no buy/sell calls.
    Returns an empty string on failure.
    """
    if not config.ANTHROPIC_API_KEY:
        logger.warning("ANTHROPIC_API_KEY not set – cannot generate hot take")
        return ""

    context_block = f"\nCurrent context:\n{context}" if context else ""

    prompt = (
        "Write a punchy analyst-voice hot take tweet (max 260 chars).\n\n"
        "Format:\n"
        "Line 1: 🔥 Bold claim or surprising data point — make it impossible to scroll past\n"
        "Line 2: ONE sentence of supporting evidence or context\n\n"
        "Be opinionated but factual. No buy/sell calls. No price targets.\n"
        "End with 1 relevant hashtag.\n"
        "Do NOT start with 'Hot take:'.\n"
        "Output only the tweet text."
        f"{context_block}"
    )

    try:
        message = _get_client().messages.create(
            model=MODEL,
            max_tokens=120,
            system=_ANALYST_SYSTEM,
            messages=[{"role": "user", "content": prompt}],
        )
        tweet = message.content[0].text.strip()
        return tweet[:260]
    except anthropic.APIError as exc:
        logger.warning("Claude API error generating hot take: %s", exc)
        return ""


def generate_quote_tweet(original_text: str) -> str:
    """
    Ask Claude to write a professional quote-tweet adding factual context.
    No directional calls; presents both sides where relevant.
    Max 220 chars, always ends with ⚠️ NFA.
    Falls back to a plain comment if the API call fails.
    """
    if not config.ANTHROPIC_API_KEY:
        logger.debug("ANTHROPIC_API_KEY not set – using plain quote tweet format")
        return _plain_quote_tweet(original_text)

    prompt = (
        "Write a quote-tweet reply to the tweet below. "
        "Add factual context, data, or relevant background that helps readers "
        "understand what is notable about this. "
        "Do not make directional calls or tell people what to do. "
        "If there are two sides to the story, acknowledge them. "
        "Max 220 characters total. End with ⚠️ NFA on the same line. "
        "Output only the reply text. No quotes, no commentary.\n\n"
        f"Tweet to quote:\n{original_text}"
    )

    try:
        message = _get_client().messages.create(
            model=MODEL,
            max_tokens=100,
            system=_ANALYST_SYSTEM,
            messages=[{"role": "user", "content": prompt}],
        )
        reply = message.content[0].text.strip()
        return reply[:220]
    except anthropic.APIError as exc:
        logger.warning("Claude API error generating quote tweet: %s", exc)
        return _plain_quote_tweet(original_text)


# ── Plain-text fallbacks ──────────────────────────────────────────────────────

def _plain_news_tweet(title: str, url: str, hashtags: str) -> str:
    max_title = 200
    if len(title) > max_title:
        title = title[:max_title - 1] + "…"
    parts = [f"📰 {title}", url, hashtags]
    return "\n".join(p for p in parts if p)


def _plain_morning_recap(headlines: list[str]) -> str:
    intro = "☀️ Morning crypto update:"
    items = " | ".join(h[:60] for h in headlines[:3])
    tweet = f"{intro} {items} #Crypto"
    return tweet[:220]


def _plain_quote_tweet(original_text: str) -> str:
    snippet = original_text[:80].rsplit(" ", 1)[0] + "…" if len(original_text) > 80 else original_text
    return f"Worth watching — {snippet} ⚠️ NFA"
