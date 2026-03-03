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
import tweepy

import config

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


def post_tweet(text: str) -> bool:
    """
    Post a tweet. Returns True on success, False on failure.
    Tweets longer than 280 chars are truncated at a word boundary.
    """
    if len(text) > 280:
        text = text[:277].rsplit(" ", 1)[0] + "…"

    try:
        client = get_client()
        response = client.create_tweet(text=text)
        tweet_id = response.data["id"]
        logger.info("Tweet posted (id=%s): %.60s…", tweet_id, text)
        return True
    except tweepy.errors.Forbidden as exc:
        logger.error("Twitter 403 Forbidden – check app permissions: %s", exc)
    except tweepy.errors.TooManyRequests:
        logger.warning("Twitter rate limit hit; will retry next cycle")
    except tweepy.TweepyException as exc:
        logger.error("Twitter error: %s", exc)
    return False
