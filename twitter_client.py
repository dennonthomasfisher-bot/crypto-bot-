"""
Twitter/X client wrapper around Tweepy v4 (API v2).

Twitter API v2 free tier ("Essential" access) allows:
  - 1,500 tweets/month (write)
  - OAuth 1.0a User Context required for posting

To get credentials:
  1. Go to https://developer.twitter.com/en/portal/dashboard
  2. Create a new project + app
  3. Enable OAuth 1.0a with Read & Write permissions
  4. Generate Access Token & Secret
"""
from __future__ import annotations

import datetime
import logging
import os
import re

import tweepy

import config
import state

logger = logging.getLogger(__name__)

_client: tweepy.Client | None = None
_api_v1: tweepy.API | None = None


# ── Helpers ───────────────────────────────────────────────────────────────────

def _ensure_line_breaks(text: str) -> str:
    """Add a newline after sentence-ending periods before a capital letter.

    Consumes the space between sentences so the next line doesn't start with
    a leading indent.
    """
    return re.sub(r'\. (?=[A-Z])', '.\n', text)


# Symbols we'll convert into cashtags. Ordered roughly by market cap so the
# most-browsed tickers get picked first when multiple coins are mentioned.
_CASHTAG_SYMBOLS = [
    "BTC", "ETH", "SOL", "XRP", "BNB", "ADA", "DOGE", "AVAX", "DOT", "LINK",
    "TRX", "MATIC", "LTC", "BCH", "NEAR", "APT", "ARB", "OP", "SUI", "TIA",
    "INJ", "SEI", "TAO", "HYPE", "PEPE", "SHIB", "WIF", "UNI", "AAVE", "MKR",
    "RNDR", "RENDER", "FET", "ATOM", "ALGO", "FTM",
]

# Longer names → ticker, so "ETHEREUM" also triggers $ETH etc.
_CASHTAG_NAMES = {
    "BITCOIN": "BTC", "ETHEREUM": "ETH", "SOLANA": "SOL", "RIPPLE": "XRP",
    "CARDANO": "ADA", "DOGECOIN": "DOGE", "AVALANCHE": "AVAX",
    "POLKADOT": "DOT", "CHAINLINK": "LINK", "POLYGON": "MATIC",
    "LITECOIN": "LTC", "ARBITRUM": "ARB", "OPTIMISM": "OP",
    "BITTENSOR": "TAO", "HYPERLIQUID": "HYPE", "COSMOS": "ATOM",
}


def _detect_cashtags(text: str, max_n: int = 1) -> list[str]:
    """Return up to `max_n` cashtags for coins mentioned in `text`.

    X caps posts at one cashtag and rejects with 403 if more are present, so
    the default is 1. First-appearance wins (most relevant to the tweet).
    Deduplicates. Matches only whole words so "$0.26" or "OPTION" don't
    trigger $0 or $OP.
    """
    upper = text.upper()
    found: list[str] = []

    def _add(sym: str) -> None:
        if sym not in found and len(found) < max_n:
            found.append(sym)

    # Pass 1: full names first (they're unambiguous)
    for name, sym in _CASHTAG_NAMES.items():
        if name in upper:
            _add(sym)

    # Pass 2: ticker symbols with word-boundary matching
    for sym in _CASHTAG_SYMBOLS:
        if len(found) >= max_n:
            break
        # Match whole word, optionally $-prefixed (avoids matching "OPTION" for "OP")
        if re.search(r'(?:\$|\b)' + re.escape(sym) + r'\b', upper):
            _add(sym)

    return [f"${sym}" for sym in found]


def _append_cashtags(text: str, limit: int = 275) -> str:
    """Append cashtags on a new blank line if they fit under `limit`.

    Skips the append if the text already contains a $TICKER pattern (the AI
    sometimes generates cashtags naturally) to avoid duplicates.
    """
    # Skip if the text already has cashtags — look for $ followed by 2-5 uppercase letters
    if re.search(r'\$[A-Z]{2,5}\b', text):
        return text
    tags = _detect_cashtags(text)
    if not tags:
        return text
    suffix = "\n\n" + " ".join(tags)
    if len(text) + len(suffix) > limit:
        return text
    return text + suffix


# ── Client factories ──────────────────────────────────────────────────────────

def get_client() -> tweepy.Client:
    """Return a cached Tweepy v2 client (creates it on first call).
    Raises RuntimeError if credentials are missing."""
    global _client
    if _client is None:
        missing = [
            name
            for name, val in [
                ("TWITTER_API_KEY",             config.TWITTER_API_KEY),
                ("TWITTER_API_SECRET",          config.TWITTER_API_SECRET),
                ("TWITTER_ACCESS_TOKEN",        config.TWITTER_ACCESS_TOKEN),
                ("TWITTER_ACCESS_TOKEN_SECRET", config.TWITTER_ACCESS_TOKEN_SECRET),
            ]
            if not val
        ]
        if missing:
            raise RuntimeError(
                f"Missing Twitter credentials in .env: {', '.join(missing)}"
            )
        _client = tweepy.Client(
            bearer_token=config.TWITTER_BEARER_TOKEN or None,
            consumer_key=config.TWITTER_API_KEY,
            consumer_secret=config.TWITTER_API_SECRET,
            access_token=config.TWITTER_ACCESS_TOKEN,
            access_token_secret=config.TWITTER_ACCESS_TOKEN_SECRET,
            wait_on_rate_limit=True,
        )
    return _client


def _get_api_v1() -> tweepy.API | None:
    """Return a cached Tweepy v1.1 API client used for media uploads.
    Returns None on any initialisation failure."""
    global _api_v1
    if _api_v1 is None:
        try:
            auth = tweepy.OAuth1UserHandler(
                config.TWITTER_API_KEY,
                config.TWITTER_API_SECRET,
                config.TWITTER_ACCESS_TOKEN,
                config.TWITTER_ACCESS_TOKEN_SECRET,
            )
            _api_v1 = tweepy.API(auth)
        except Exception as exc:
            logger.warning("Could not initialise v1.1 API: %s", exc)
            return None
    return _api_v1


# Public alias so callers using either name work.
get_api_v1 = _get_api_v1


# ── Media ─────────────────────────────────────────────────────────────────────

def upload_media(image_path: str) -> str | None:
    """Upload an image via Twitter API v1.1. Returns media_id string, or None on failure."""
    api = _get_api_v1()
    if api is None:
        logger.warning("v1.1 API unavailable — skipping media upload")
        return None
    try:
        media = api.media_upload(filename=image_path)
        logger.info("Media uploaded (id=%s)", media.media_id_string)
        return media.media_id_string
    except tweepy.TweepyException as exc:
        logger.warning("Media upload failed: %s", exc)
        return None


# ── Posting ───────────────────────────────────────────────────────────────────

def post_tweet(
    text: str,
    image_path: str | None = None,
    in_reply_to_tweet_id: str | None = None,
    quote_tweet_id: str | None = None,
) -> bool:
    """Post a single tweet with optional image, reply target, or quote tweet.

    - Strips hashtags (they hurt reach on X/Twitter)
    - Cleans up newlines
    - Truncates at 275 chars at a word boundary
    - Checks monthly cap before posting
    - Records the tweet in state on success

    Returns True on success, False on failure.
    """
    logger.info("[POST] ENTRY: text=%r, reply_to=%s, quote=%s, image=%s",
                text[:50], in_reply_to_tweet_id, quote_tweet_id, bool(image_path))

    stripped = text.strip()
    if not stripped or len(stripped) < 20:
        logger.warning("[POST] BLOCKED — too short or empty (%d chars): %.60s",
                       len(stripped) if stripped else 0, text)
        return False
    if "$0 " in text or "$0," in text or "at $0." in text:
        logger.warning("[POST] BLOCKED — contains $0 price (API likely down): %.60s", text)
        return False

    if not state.can_tweet():
        logger.warning("[POST] BLOCKED by state.can_tweet() — monthly cap reached")
        return False

    # Strip hashtags
    text = re.sub(r'\s*#\w+', '', text).strip()
    # Remove trailing blank lines left by stripped hashtags
    text = re.sub(r'\n\s*\n\s*$', '', text).strip()
    # Improve readability with sentence-level line breaks
    text = _ensure_line_breaks(text)
    # Final alignment: every line flush-left, no leading indent on any line
    text = "\n".join(line.lstrip() for line in text.split("\n"))
    # Append cashtags for X discovery badges (e.g. "$BTC $ETH")
    text = _append_cashtags(text)

    if len(text) > 275:
        text = text[:272].rsplit(" ", 1)[0] + "…"

    media_ids = None
    if image_path:
        media_id = upload_media(image_path)
        if media_id:
            media_ids = [media_id]

    try:
        client = get_client()
        kwargs: dict = {"text": text}
        if media_ids:
            kwargs["media_ids"] = media_ids
        if in_reply_to_tweet_id:
            kwargs["in_reply_to_tweet_id"] = in_reply_to_tweet_id
        if quote_tweet_id:
            kwargs["quote_tweet_id"] = quote_tweet_id
        logger.info("[POST] create_tweet kwargs: %s",
                    {k: (v[:50] if isinstance(v, str) else v) for k, v in kwargs.items()})
        response = client.create_tweet(**kwargs)
        tweet_id = response.data["id"]
        state.record_tweet()
        logger.info("[POST] Success (id=%s, reply_to=%s, media=%s): %.80s",
                    tweet_id, in_reply_to_tweet_id, bool(media_ids), text)
        return True
    except tweepy.errors.Forbidden as exc:
        logger.error("[POST] Twitter 403 Forbidden (reply_to=%s): %s",
                     in_reply_to_tweet_id, exc)
    except tweepy.errors.TooManyRequests:
        logger.warning("[POST] Twitter rate limit hit (reply_to=%s)", in_reply_to_tweet_id)
    except tweepy.TweepyException as exc:
        logger.error("[POST] Twitter error (reply_to=%s): %s", in_reply_to_tweet_id, exc)
    return False


def post_thread(tweets: list[str], first_tweet_image_path: str | None = None) -> bool:
    """Post a list of tweets as a thread (each replying to the previous).

    first_tweet_image_path: optional image attached to the first tweet only.
    Returns True if all tweets posted successfully, False on first failure.
    """
    if not tweets:
        return False

    client = get_client()
    previous_id: str | None = None

    for i, text in enumerate(tweets):
        text = _ensure_line_breaks(text)
        text = "\n".join(line.lstrip() for line in text.split("\n"))
        # Cashtags only on the first tweet of a thread (discovery anchor);
        # continuation tweets don't need them and would look spammy.
        if i == 0:
            text = _append_cashtags(text)
        if len(text) > 280:
            text = text[:277].rsplit(" ", 1)[0] + "…"
        try:
            kwargs: dict = {"text": text}
            if previous_id:
                kwargs["in_reply_to_tweet_id"] = previous_id
            if i == 0 and first_tweet_image_path:
                media_id = upload_media(first_tweet_image_path)
                if media_id:
                    kwargs["media_ids"] = [media_id]
            response = client.create_tweet(**kwargs)
            previous_id = response.data["id"]
            logger.info(
                "Thread tweet %d/%d posted (id=%s): %.60s",
                i + 1, len(tweets), previous_id, text,
            )
        except tweepy.errors.Forbidden as exc:
            logger.error("Twitter 403 Forbidden on thread tweet %d: %s", i + 1, exc)
            return False
        except tweepy.errors.TooManyRequests:
            logger.warning("Twitter rate limit hit on thread tweet %d", i + 1)
            return False
        except tweepy.TweepyException as exc:
            logger.error("Twitter error on thread tweet %d: %s", i + 1, exc)
            return False

    return True


# ── Search ────────────────────────────────────────────────────────────────────

def search_recent_tweets(query: str, max_results: int = 10) -> list[dict]:
    """Search recent tweets using Twitter API v2.

    Returns a list of dicts with keys:
        id, text, author_id, like_count, retweet_count, followers_count, created_at

    Returns an empty list on any failure.
    """
    try:
        client = get_client()
        response = client.search_recent_tweets(
            query=query,
            max_results=max(10, min(max_results, 100)),
            tweet_fields=["public_metrics", "created_at", "author_id"],
            expansions=["author_id"],
            user_fields=["public_metrics"],
        )
    except tweepy.TweepyException as exc:
        logger.warning("Twitter search failed: %s", exc)
        return []

    if not response.data:
        return []

    followers_by_id: dict[str, int] = {}
    if response.includes and response.includes.get("users"):
        for user in response.includes["users"]:
            followers_by_id[str(user.id)] = user.public_metrics["followers_count"]

    results = []
    for tweet in response.data:
        author_id = str(tweet.author_id)
        pm = tweet.public_metrics or {}
        results.append({
            "id":              str(tweet.id),
            "text":            tweet.text,
            "author_id":       author_id,
            "like_count":      pm.get("like_count", 0),
            "retweet_count":   pm.get("retweet_count", 0),
            "followers_count": followers_by_id.get(author_id, 0),
            "created_at":      tweet.created_at,
        })

    return results
