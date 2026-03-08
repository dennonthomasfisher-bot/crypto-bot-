"""
Auto-replier – searches for popular crypto tweets and posts
relevant, data-driven replies to increase engagement.

Uses Twitter API v2 search to find recent tweets about crypto,
then replies with market data and genuine insights.
"""
from __future__ import annotations

import logging
import random
import re
import time

import tweepy

import config
import state
import twitter_client
import tweet_generators
import ai_writer

logger = logging.getLogger(__name__)

# Track replied tweet IDs to avoid double-replying
_replied_ids: set[str] = set()

_SEARCH_QUERIES = [
    "#Bitcoin -is:retweet -is:reply lang:en",
    "#Crypto market -is:retweet -is:reply lang:en",
    "#Ethereum -is:retweet -is:reply lang:en",
    "#Solana -is:retweet -is:reply lang:en",
    "#Altcoins -is:retweet -is:reply lang:en",
    "altcoin season -is:retweet -is:reply lang:en",
    "crypto portfolio -is:retweet -is:reply lang:en",
    "ETH BTC ratio -is:retweet -is:reply lang:en",
    "DeFi TVL -is:retweet -is:reply lang:en",
    "crypto narrative -is:retweet -is:reply lang:en",
    "BTC price prediction -is:retweet -is:reply lang:en",
    "bitcoin halving -is:retweet -is:reply lang:en",
    "crypto whale -is:retweet -is:reply lang:en",
    "buy the dip crypto -is:retweet -is:reply lang:en",
    "memecoin -is:retweet -is:reply lang:en",
    "bitcoin dominance -is:retweet -is:reply lang:en",
    "crypto bull run -is:retweet -is:reply lang:en",
    "SOL ecosystem -is:retweet -is:reply lang:en",
    "ETH staking -is:retweet -is:reply lang:en",
    "bitcoin ETF -is:retweet -is:reply lang:en",
]


def _build_reply(btc_data: dict | None, tweet_text: str) -> str:
    """Generate a contextual reply using current market data."""
    if btc_data is None:
        return random.choice([
            "Solid take. The next few weeks will be telling for direction.",
            "Watching the same setup. Volume will confirm the move.",
            "Agree — the structure here is worth watching closely.",
        ])

    price = btc_data.get("current_price", 0)
    pct_24h = btc_data.get("price_change_percentage_24h_in_currency") or 0
    pct_7d = btc_data.get("price_change_percentage_7d_in_currency") or 0
    sign_24h = "+" if pct_24h > 0 else ""
    price_str = f"${price:,.0f}" if price >= 1000 else f"${price:,.2f}"

    tweet_lower = tweet_text.lower()

    # Context-aware replies based on what the original tweet is about
    if any(w in tweet_lower for w in ["bull", "long", "moon", "pump", "rip"]):
        replies = [
            f"BTC at {price_str} ({sign_24h}{pct_24h:.1f}% 24h). On-chain supports the case — exchange reserves keep dropping.",
            f"Momentum building. {price_str} and 7d trend at {'+' if pct_7d > 0 else ''}{pct_7d:.1f}%. Spot demand doing the heavy lifting.",
            f"The structure looks constructive. {price_str} with funding rates clean. Room to move.",
        ]
    elif any(w in tweet_lower for w in ["bear", "short", "dump", "crash", "drop"]):
        replies = [
            f"BTC {price_str} ({sign_24h}{pct_24h:.1f}% 24h). Worth watching — but long-term holders aren't budging.",
            f"Caution makes sense at {price_str}. Though exchange reserves at lows suggest conviction underneath.",
            f"Valid concern. {price_str} and 7d at {'+' if pct_7d > 0 else ''}{pct_7d:.1f}%. Key support levels to watch below.",
        ]
    elif any(w in tweet_lower for w in ["eth", "ethereum", "altcoin", "sol", "xrp"]):
        replies = [
            f"Alts following BTC's lead at {price_str}. The rotation will come — watch BTC dominance for timing.",
            f"BTC at {price_str} sets the tone. When dominance peaks, alts usually catch a bid.",
            f"Good call. BTC {sign_24h}{pct_24h:.1f}% today. Alt season needs BTC to stabilize first.",
        ]
    else:
        replies = [
            f"BTC at {price_str} ({sign_24h}{pct_24h:.1f}% 24h). Interesting setup developing here.",
            f"The data at {price_str}: 24h {sign_24h}{pct_24h:.1f}%, 7d {'+' if pct_7d > 0 else ''}{pct_7d:.1f}%. Structure worth watching.",
            f"Good observation. BTC {price_str} with {sign_24h}{pct_24h:.1f}% on the day. Levels to watch ahead.",
        ]

    return random.choice(replies)


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


def find_and_reply() -> int:
    """
    Search for popular crypto tweets and reply to them.

    Returns count of replies posted.
    """
    if config.is_quiet_hours():
        logger.info("Quiet hours (%d:00-%d:00 UK) — skipping auto-replies",
                     config.QUIET_HOURS_START, config.QUIET_HOURS_END)
        return 0

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

        if not state.can_tweet():
            logger.warning("Monthly tweet limit reached — skipping auto-replies")
            break

        if tweet["id"] in _replied_ids:
            continue

        # Skip low-engagement tweets
        if tweet["likes"] < 2:
            continue

        # Try AI reply first, fall back to template
        reply_text = None
        if ai_writer.is_available() and btc_data:
            price = btc_data.get("current_price", 0)
            pct = btc_data.get("price_change_percentage_24h_in_currency") or 0
            reply_text = ai_writer.generate_reply(price, pct, tweet["text"])
        if not reply_text:
            reply_text = _build_reply(btc_data, tweet["text"])

        try:
            # Strip any hashtags before posting
            reply_text = re.sub(r'\s*#\w+', '', reply_text).strip()
            client = twitter_client.get_client()
            client.create_tweet(
                text=reply_text,
                in_reply_to_tweet_id=tweet["id"],
            )
            _replied_ids.add(tweet["id"])
            state.record_tweet()
            tweet_generators.record_auto_reply()
            replied += 1
            logger.info(
                "Auto-replied to tweet %s (%d remaining, likes=%d): %.80s",
                tweet["id"], state.tweets_remaining(), tweet["likes"], reply_text,
            )
            time.sleep(3)  # Pace replies

        except tweepy.errors.Forbidden:
            logger.warning("Cannot reply to tweet %s – forbidden", tweet["id"])
        except tweepy.TweepyException as exc:
            logger.warning("Reply failed for tweet %s: %s", tweet["id"], exc)

        if replied >= 3:
            break

    # Keep replied set bounded
    if len(_replied_ids) > 1000:
        _replied_ids.clear()

    return replied
