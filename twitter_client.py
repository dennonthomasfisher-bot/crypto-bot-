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

import datetime
import logging
import os
import tweepy

import config

logger = logging.getLogger(__name__)

_client: tweepy.Client | None = None
_api_v1: tweepy.API | None = None


def get_api_v1() -> tweepy.API:
    """Return a cached Tweepy v1.1 API client used for media uploads."""
    global _api_v1
    if _api_v1 is None:
        auth = tweepy.OAuth1UserHandler(
            config.TWITTER_API_KEY,
            config.TWITTER_API_SECRET,
            config.TWITTER_ACCESS_TOKEN,
            config.TWITTER_ACCESS_TOKEN_SECRET,
        )
        _api_v1 = tweepy.API(auth)
    return _api_v1


def upload_media(image_path: str) -> str | None:
    """
    Upload an image file via Twitter API v1.1 and return the media_id string.
    Returns None on failure.
    """
    try:
        api = get_api_v1()
        media = api.media_upload(filename=image_path)
        logger.info("Media uploaded (id=%s)", media.media_id_string)
        return media.media_id_string
    except tweepy.TweepyException as exc:
        logger.warning("Media upload failed: %s", exc)
        return None
    finally:
        # Clean up temp file
        try:
            if os.path.exists(image_path):
                os.unlink(image_path)
        except OSError:
            pass


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


def search_crypto_tweets(min_followers: int = 5000, hours: int = 2) -> list[dict]:
    """
    Search Twitter for recent crypto tweets from accounts with >min_followers followers.

    Returns a list of dicts sorted by combined engagement (likes + retweets) descending:
        {id, text, author_id, like_count, retweet_count, followers_count, created_at}

    Returns [] on any API error or if no results pass the follower filter.
    """
    since = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(hours=hours)
    query = (
        "(bitcoin OR ethereum OR #BTC OR #ETH OR \"crypto market\") "
        "-is:retweet -is:reply lang:en"
    )
    try:
        client = get_client()
        response = client.search_recent_tweets(
            query=query,
            max_results=50,
            start_time=since,
            tweet_fields=["public_metrics", "created_at", "author_id"],
            expansions=["author_id"],
            user_fields=["public_metrics"],
        )
    except tweepy.TweepyException as exc:
        logger.warning("Twitter search failed: %s", exc)
        return []

    if not response.data:
        return []

    # Build author_id → follower_count lookup from the includes
    followers_by_id: dict[str, int] = {}
    if response.includes and response.includes.get("users"):
        for user in response.includes["users"]:
            followers_by_id[str(user.id)] = user.public_metrics["followers_count"]

    results = []
    for tweet in response.data:
        author_id = str(tweet.author_id)
        followers = followers_by_id.get(author_id, 0)
        if followers < min_followers:
            continue
        pm = tweet.public_metrics or {}
        results.append({
            "id": str(tweet.id),
            "text": tweet.text,
            "author_id": author_id,
            "like_count": pm.get("like_count", 0),
            "retweet_count": pm.get("retweet_count", 0),
            "followers_count": followers,
            "created_at": tweet.created_at,
        })

    results.sort(key=lambda t: t["like_count"] + t["retweet_count"], reverse=True)
    return results


def post_quote_tweet(text: str, quote_tweet_id: str) -> bool:
    """
    Post a quote tweet. Returns True on success, False on failure.
    Text longer than 280 chars is truncated at a word boundary.
    """
    if len(text) > 280:
        text = text[:277].rsplit(" ", 1)[0] + "…"
    try:
        client = get_client()
        response = client.create_tweet(text=text, quote_tweet_id=quote_tweet_id)
        tweet_id = response.data["id"]
        logger.info(
            "Quote tweet posted (id=%s quoting=%s): %.60s…",
            tweet_id, quote_tweet_id, text,
        )
        return True
    except tweepy.errors.Forbidden as exc:
        logger.error("Twitter 403 Forbidden posting quote tweet: %s", exc)
    except tweepy.errors.TooManyRequests:
        logger.warning("Twitter rate limit hit posting quote tweet; will retry next cycle")
    except tweepy.TweepyException as exc:
        logger.error("Twitter error posting quote tweet: %s", exc)
    return False


def post_thread(tweets: list[str]) -> bool:
    """
    Post a list of tweets as a thread (each reply to the previous).
    Returns True if all tweets posted successfully, False if any failed.
    Stops posting on first failure.
    """
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
                "Thread tweet %d/%d posted (id=%s): %.60s…",
                i + 1, len(tweets), previous_id, text,
            )
        except tweepy.errors.Forbidden as exc:
            logger.error("Twitter 403 Forbidden posting thread tweet %d: %s", i + 1, exc)
            return False
        except tweepy.errors.TooManyRequests:
            logger.warning("Twitter rate limit hit on thread tweet %d", i + 1)
            return False
        except tweepy.TweepyException as exc:
            logger.error("Twitter error on thread tweet %d: %s", i + 1, exc)
            return False

    return True


def post_tweet(text: str, image_path: str | None = None) -> bool:
    """
    Post a tweet with an optional image attachment.
    Returns True on success, False on failure.
    Tweets longer than 280 chars are truncated at a word boundary.
    """
    if len(text) > 280:
        text = text[:277].rsplit(" ", 1)[0] + "…"

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
        logger.info("Tweet posted (id=%s, media=%s): %.60s…", tweet_id, bool(media_ids), text)
        return True
    except tweepy.errors.Forbidden as exc:
        logger.error("Twitter 403 Forbidden – check app permissions: %s", exc)
    except tweepy.errors.TooManyRequests:
        logger.warning("Twitter rate limit hit; will retry next cycle")
    except tweepy.TweepyException as exc:
        logger.error("Twitter error: %s", exc)
    return False
