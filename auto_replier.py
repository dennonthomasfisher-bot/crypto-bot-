"""
Auto-replier – searches for popular crypto tweets and posts
relevant replies to increase engagement.

Uses Twitter API v2 search to find recent tweets about crypto,
then replies with market data or analysis.
"""
from __future__ import annotations

import logging
import random
import time

import tweepy

import config
import twitter_client
import tweet_generators

logger = logging.getLogger(__name__)

# Track replied tweet IDs to avoid double-replying
_replied_ids: set[str] = set()

_SEARCH_QUERIES = [
    "#Bitcoin -is:retweet -is:reply lang:en",
    "#Crypto -is:retweet -is:reply lang:en",
    "#BTC price -is:retweet lang:en",
    "bitcoin prediction -is:retweet lang:en",
    "crypto market -is:retweet -is:reply lang:en",
]

_REPLY_TEMPLATES = [
    "Great point! BTC is currently at ${price:,.0f} ({change:+.1f}% 24h). {insight}",
    "Interesting take. The on-chain data {supports_or_challenges} this view. BTC ${price:,.0f}.",
    "Worth noting: BTC at ${price:,.0f}, {change:+.1f}% in the last 24h. {context}",
    "The charts are {sentiment} here. BTC ${price:,.0f}. {outlook}",
]

_INSIGHTS = [
    "Watching the 200-day MA closely.",
    "Volume profile suggests accumulation.",
    "Key support holding for now.",
    "Resistance ahead at prior highs.",
    "Macro backdrop still uncertain.",
]

_SUPPORTS = ["supports", "aligns with", "confirms"]
_CHALLENGES = ["challenges", "contrasts with", "goes against"]


def _search_tweets(query: str, max_results: int = 10) -> list[dict]:
    """Search for recent tweets matching query."""
    try:
        client = twitter_client.get_client()
        response = client.search_recent_tweets(
            query=query,
            max_results=max_results,
            tweet_fields=["author_id", "public_metrics", "created_at"],
        )
        if response.data:
            return [
                {
                    "id": str(t.id),
                    "text": t.text,
                    "author_id": str(t.author_id),
                    "likes": t.public_metrics.get("like_count", 0) if t.public_metrics else 0,
                    "retweets": t.public_metrics.get("retweet_count", 0) if t.public_metrics else 0,
                }
                for t in response.data
            ]
    except tweepy.errors.Forbidden:
        logger.warning("Twitter search forbidden – may need elevated access")
    except tweepy.TweepyException as exc:
        logger.warning("Twitter search failed: %s", exc)
    return []


def _generate_reply(btc_data: dict | None) -> str:
    """Generate a contextual reply using current market data."""
    if btc_data is None:
        return random.choice([
            "Interesting perspective on the market!",
            "Good analysis. The charts are telling a story here.",
            "Worth watching how this plays out. Key levels ahead.",
        ])

    price = btc_data.get("current_price", 0)
    change = btc_data.get("price_change_percentage_24h_in_currency") or 0
    sentiment = "bullish" if change > 0 else "cautious"
    supports_or_challenges = random.choice(_SUPPORTS if change > 0 else _CHALLENGES)

    template = random.choice(_REPLY_TEMPLATES)
    try:
        return template.format(
            price=price,
            change=change,
            insight=random.choice(_INSIGHTS),
            supports_or_challenges=supports_or_challenges,
            context=random.choice(_INSIGHTS),
            sentiment=sentiment,
            outlook=random.choice(_INSIGHTS),
        )
    except KeyError:
        return f"BTC at ${price:,.0f} ({change:+.1f}% 24h). {random.choice(_INSIGHTS)}"


def find_and_reply() -> int:
    """
    Search for popular crypto tweets and reply to them.

    Returns count of replies posted.
    """
    if not tweet_generators.can_auto_reply():
        logger.info("Auto-reply daily cap (%d) reached.", config.AUTO_REPLY_DAILY_CAP)
        return 0

    btc_data = tweet_generators._get_btc_data()

    query = random.choice(_SEARCH_QUERIES)
    tweets = _search_tweets(query, max_results=10)

    if not tweets:
        logger.info("No tweets found for auto-reply.")
        return 0

    # Sort by engagement, reply to most popular
    tweets.sort(key=lambda t: t["likes"] + t["retweets"] * 2, reverse=True)

    replied = 0
    for tweet in tweets:
        if not tweet_generators.can_auto_reply():
            break

        if tweet["id"] in _replied_ids:
            continue

        # Skip low-engagement tweets
        if tweet["likes"] < 5:
            continue

        reply_text = _generate_reply(btc_data)

        try:
            client = twitter_client.get_client()
            client.create_tweet(
                text=reply_text,
                in_reply_to_tweet_id=tweet["id"],
            )
            _replied_ids.add(tweet["id"])
            tweet_generators.record_auto_reply()
            replied += 1
            logger.info(
                "Auto-replied to tweet %s (likes=%d): %.60s",
                tweet["id"], tweet["likes"], reply_text,
            )
            time.sleep(3)  # Pace replies

        except tweepy.errors.Forbidden:
            logger.warning("Cannot reply to tweet %s – forbidden", tweet["id"])
        except tweepy.TweepyException as exc:
            logger.warning("Reply failed for tweet %s: %s", tweet["id"], exc)

        if replied >= 2:
            break

    # Keep replied set bounded
    if len(_replied_ids) > 1000:
        _replied_ids.clear()

    return replied
