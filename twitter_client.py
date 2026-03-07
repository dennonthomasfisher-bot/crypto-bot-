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

import logging
import tweepy

import config
import state

logger = logging.getLogger(__name__)

_client: tweepy.Client | None = None


def get_client() -> tweepy.Client:
    """Return a cached Tweepy v2 client (creates it on first call)."""
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


def post_tweet(text: str) -> bool:
    """
    Post a tweet. Returns True on success, False on failure.
    Tweets longer than 280 chars are truncated at a word boundary.
    """
    if len(text) > 280:
        text = text[:277].rsplit(" ", 1)[0] + "…"

    if not state.can_tweet():
        logger.warning(
            "Monthly tweet limit (%d) reached – skipping tweet",
            state.MONTHLY_TWEET_LIMIT,
        )
        return False

    try:
        client = get_client()
        response = client.create_tweet(text=text)
        tweet_id = response.data["id"]
        state.record_tweet()
        remaining = state.tweets_remaining()
        logger.info("Tweet posted (id=%s, %d remaining this month): %.60s…", tweet_id, remaining, text)
        return True
    except tweepy.errors.Forbidden as exc:
        logger.error("Twitter 403 Forbidden – check app permissions: %s", exc)
    except tweepy.errors.TooManyRequests:
        logger.warning("Twitter rate limit hit; will retry next cycle")
    except tweepy.TweepyException as exc:
        logger.error("Twitter error: %s", exc)
    return False
