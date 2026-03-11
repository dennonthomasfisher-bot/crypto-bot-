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
    """Add a newline after sentence-ending periods before a capital letter."""
    return re.sub(r'\.(?= [A-Z])', '.\n', text)


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

def post_tweet(text: str, image_path: str | None = None) -> bool:
    """Post a single tweet with an optional image attachment.

    - Strips hashtags (they hurt reach on X/Twitter)
    - Cleans up newlines
    - Truncates at 275 chars at a word boundary
    - Checks monthly cap before posting
    - Records the tweet in state on success

    Returns True on success, False on failure.
    """
    stripped = text.strip()
    if not stripped or len(stripped) < 20:
        logger.warning("Skipping tweet – too short or empty: %.60s", text)
        return False
    if "$0 " in text or "$0," in text or "at $0." in text:
        logger.warning("Skipping tweet – contains $0 price (API likely down): %.60s", text)
        return False

    if not state.can_tweet():
        logger.warning("Monthly tweet cap reached – skipping")
        return False

    # Strip hashtags
    text = re.sub(r'\s*#\w+', '', text).strip()
    # Remove trailing blank lines left by stripped hashtags
    text = re.sub(r'\n\s*\n\s*$', '', text).strip()
    # Improve readability with sentence-level line breaks
    text = _ensure_line_breaks(text)

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
        response = client.create_tweet(**kwargs)
        tweet_id = response.data["id"]
        state.record_tweet()
        logger.info("Tweet posted (id=%s, media=%s): %.80s", tweet_id, bool(media_ids), text)
        return True
    except tweepy.errors.Forbidden as exc:
        logger.error("Twitter 403 Forbidden – check app permissions: %s", exc)
    except tweepy.errors.TooManyRequests:
        logger.warning("Twitter rate limit hit; will retry next cycle")
    except tweepy.TweepyException as exc:
        logger.error("Twitter error: %s", exc)
    return False


def post_thread(tweets: list[str]) -> bool:
    """Post a list of tweets as a thread (each replying to the previous).
    Returns True if all tweets posted successfully, False on first failure."""
    if not tweets:
        return False

    client = get_client()
    previous_id: str | None = None

    for i, text in enumerate(tweets):
        if len(text) > 280:
            text = text[:277].rsplit(" ", 1)[0] + "…"
        try:
            kwargs: dict = {"text": text}
            if previous_id:
                kwargs["reply"] = {"in_reply_to_tweet_id": previous_id}
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
