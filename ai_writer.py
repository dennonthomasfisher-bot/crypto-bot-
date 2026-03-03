"""
ai_writer.py – Claude-powered tweet generation.

Provides two public functions:
  generate_news_tweet(story)         – concise tweet for a single news story
  generate_morning_recap(headlines)  – daily 08:00 UK market-summary tweet

Requires ANTHROPIC_API_KEY in .env.
Falls back to a plain-text summary if the API call fails.
"""

import logging
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


def generate_news_tweet(story: dict) -> str:
    """
    Ask Claude to write a punchy tweet for a single crypto news story.
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
        f"Write a punchy, engaging tweet (max 240 characters) summarising this crypto "
        f"news headline. Be direct and informative. End with these hashtags: {hashtags}\n\n"
        f"Headline: {title}\n\n"
        f"Output only the tweet text. No quotes, no commentary."
    )

    try:
        message = _get_client().messages.create(
            model=MODEL,
            max_tokens=120,
            messages=[{"role": "user", "content": prompt}],
        )
        tweet = message.content[0].text.strip()
        # Append URL on a new line if it fits
        candidate = f"{tweet}\n{url}" if url else tweet
        if len(candidate) <= 280:
            return candidate
        return tweet[:277 - len(url) - 1].rsplit(" ", 1)[0] + f"…\n{url}" if url else tweet
    except anthropic.APIError as exc:
        logger.warning("Claude API error generating news tweet: %s", exc)
        return _plain_news_tweet(title, url, hashtags)


def generate_morning_recap(headlines: list[str]) -> str:
    """
    Ask Claude to write a punchy morning market-summary tweet (max 220 chars)
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
        "Write a punchy morning crypto market summary tweet (max 220 characters). "
        "Start with ☀️. Summarise the key themes from the headlines in one sentence. "
        "End with 1-2 relevant hashtags (#Bitcoin, #Ethereum, or #Crypto). "
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
