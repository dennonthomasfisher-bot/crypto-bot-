#!/usr/bin/env python3
"""
Crypto News Twitter Bot – main entry point.

Runs recurring jobs:
  • Price monitor   – every PRICE_CHECK_INTERVAL seconds
  • News monitor    – every NEWS_CHECK_INTERVAL seconds
  • Quote tweeter   – every 4 hours (max 4 quote tweets/day)
  • Morning recap   – daily at 08:00 UK time

When a significant price move or hot news story is detected it posts a tweet.

Usage:
    python bot.py            # run forever (use Ctrl-C to stop)
    python bot.py --dry-run  # print tweets to stdout instead of posting
"""

import argparse
import datetime
import logging
import os
import re
import random
import re
import signal
import sys
import time
from zoneinfo import ZoneInfo

import schedule

import ai_writer
import config
import image_generator
import news_monitor
import price_monitor
import state
import twitter_client

_LONDON_TZ = ZoneInfo("Europe/London")

# ── Logging ───────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
    handlers=[
        logging.FileHandler(config.LOG_FILE),
        logging.StreamHandler(sys.stdout),
    ],
)
logger = logging.getLogger("bot")

# ── Globals ───────────────────────────────────────────────────────────────────
DRY_RUN = False

QUOTE_TWEET_DAILY_CAP = 4
_quote_tweet_count: int = 0
_quote_tweet_reset_date: datetime.date | None = None
_quoted_tweet_ids: set[str] = set()   # never quote the same tweet twice

# Posting guard: minimum seconds between consecutive _emit() calls (60 s).
_POSTING_GUARD_INTERVAL = 60
_last_emit_time: float = 0.0

# ── Topic-level dedup and group cooldowns ────────────────────────────────────
_TOPIC_KEYWORDS: frozenset[str] = frozenset({
    "btc", "bitcoin", "eth", "ethereum", "sol", "solana", "bnb", "xrp",
    "ada", "avax", "link", "dot", "matic", "polygon", "near", "atom",
    "defi", "nft", "layer2", "l2", "arbitrum", "optimism", "base",
    "stablecoin", "usdt", "usdc", "regulation", "sec", "fed", "etf",
    "halving", "dominance", "funding", "liquidation", "tvl", "whale",
    "breakout", "breakdown", "support", "resistance", "rally", "dump",
})
_TOPIC_GROUPS: dict[str, frozenset[str]] = {
    "L2_DEFI": frozenset({"defi", "layer2", "l2", "arbitrum", "optimism", "base", "tvl"}),
    "REGULATION": frozenset({"regulation", "sec", "fed", "etf"}),
    "STABLECOIN": frozenset({"stablecoin", "usdt", "usdc"}),
}
_TOPIC_COOLDOWN_SECS = 2 * 3600  # 2 hours per topic group
_topic_group_last_post: dict[str, float] = {}
_last_2_emit_topics: list[set[str]] = []


def _extract_topics(text: str) -> set[str]:
    """Extract known topic keywords from tweet text."""
    words = set(re.sub(r'[^a-z0-9]', ' ', text.lower()).split())
    return words & _TOPIC_KEYWORDS


def _check_topic_cooldown(topics: set[str]) -> bool:
    """Return True if OK to post (no active group cooldown). False = blocked."""
    now = time.time()
    for group, group_keywords in _TOPIC_GROUPS.items():
        if topics & group_keywords:
            last = _topic_group_last_post.get(group, 0)
            if (now - last) < _TOPIC_COOLDOWN_SECS:
                logger.info(
                    "Topic group cooldown active for %s (%.0f min left)",
                    group, (_TOPIC_COOLDOWN_SECS - (now - last)) / 60,
                )
                return False
    return True


def _is_duplicate_topic(topics: set[str]) -> bool:
    """Return True if new tweet shares ≥3 keywords with either of the last 2 posts."""
    if not topics:
        return False
    for prev_topics in _last_2_emit_topics[-2:]:
        if len(topics & prev_topics) >= 3:
            return True
    return False


def _record_emit_state(text: str) -> None:
    """Update topic tracking after a successful emit."""
    global _last_2_emit_topics
    topics = _extract_topics(text)
    _last_2_emit_topics.append(topics)
    if len(_last_2_emit_topics) > 2:
        _last_2_emit_topics = _last_2_emit_topics[-2:]
    now = time.time()
    for group, group_keywords in _TOPIC_GROUPS.items():
        if topics & group_keywords:
            _topic_group_last_post[group] = now


# ── Topic dedup and cooldown ──────────────────────────────────────────────────
# Every word in this set is a "topic token".  Tokens are matched case-insensitively.
_TOPIC_KEYWORDS: frozenset[str] = frozenset({
    "btc", "bitcoin",
    "eth", "ethereum",
    "sol", "solana",
    "bnb", "xrp", "ripple",
    "ada", "cardano",
    "doge", "dogecoin",
    "avax", "avalanche",
    "dot", "polkadot",
    "link", "chainlink",
    "l2", "layer2", "arbitrum", "optimism", "base", "zksync", "starknet",
    "defi", "dex", "cex", "tvl", "yield", "liquidity", "staking",
    "nft", "bridge", "stablecoin", "usdt", "usdc", "dai",
    "halving", "etf", "altcoin", "memecoin", "whale", "liquidation",
    "sec", "regulation", "cftc",
})

# Topic groups that share a 2-hour cooldown between each other.
_TOPIC_GROUPS: dict[str, frozenset[str]] = {
    "L2_DEFI":     frozenset({
        "l2", "layer2", "arbitrum", "optimism", "base", "zksync", "starknet",
        "defi", "dex", "tvl", "bridge", "yield", "liquidity",
    }),
    "REGULATION":  frozenset({"sec", "regulation", "cftc", "legal", "lawsuit", "ban"}),
    "STABLECOIN":  frozenset({"usdt", "usdc", "stablecoin", "dai", "peg"}),
}

_TOPIC_COOLDOWN_SECS: int = 2 * 3600           # 2 hours
_topic_group_last_post: dict[str, float] = {}  # group → monotonic timestamp
_last_2_emit_topics: list[set[str]] = []       # rolling window of last 2 posts


def _extract_topics(text: str) -> set[str]:
    """Return the set of _TOPIC_KEYWORDS tokens present in `text` (case-insensitive)."""
    words = {w.lower() for w in re.findall(r'\b\w+\b', text)}
    return words & _TOPIC_KEYWORDS


def _check_topic_cooldown(text: str) -> tuple[bool, str]:
    """Return (blocked, reason) if any topic group is still in its 2-hour window."""
    now = time.monotonic()
    words = _extract_topics(text)
    for group, keywords in _TOPIC_GROUPS.items():
        if words & keywords:
            last = _topic_group_last_post.get(group, 0.0)
            elapsed = now - last
            if elapsed < _TOPIC_COOLDOWN_SECS:
                remaining_min = int((_TOPIC_COOLDOWN_SECS - elapsed) / 60)
                return True, f"topic group {group} in cooldown ({remaining_min}m remaining)"
    return False, ""


def _emit(text: str, tweet_type: str = "general") -> None:
    """Post a tweet or print it (dry-run mode). Also sends to webhooks."""
    global _last_emit_time, _last_emit_text

    if DRY_RUN:
        print(f"\n{'─'*60}\n[DRY RUN] Would tweet:\n{text}\n{'─'*60}")
        _last_emit_text = text
        # Track coins and sentiment even in dry-run for testing
        coins = _extract_coin_symbols(text)
        if coins:
            state.record_coins_mentioned(coins)
        state.record_sentiment(text)
        _record_emit_state(text)
    else:
        # Respect quiet hours — look more human, don't tweet at 3am
        if _is_quiet_hours():
            logger.info("Quiet hours (%d:00-%d:00 UK) — skipping tweet: %.60s",
                        config.QUIET_HOURS_START, config.QUIET_HOURS_END, text)
            return

        # Block duplicate/near-identical content
        if _is_duplicate_content(text):
            logger.info("Skipping tweet — too similar to last tweet: %.60s", text)
            return

        # Topic-level dedup — block if ≥3 shared keywords with last 2 posts
        topics = _extract_topics(text)
        if _is_duplicate_topic(topics):
            logger.info("Skipping tweet — duplicate topic vs recent posts: %.60s", text)
            return

        # Topic group cooldown — 2 hours per group (L2/DeFi, Regulation, Stablecoins)
        if not _check_topic_cooldown(topics):
            logger.info("Skipping tweet — topic group on cooldown: %.60s", text)
            return

        # Per-type cooldown — no tweet type fires more than once per 30 min
        if tweet_type != "general":
            last_type_time = _type_last_emit.get(tweet_type, 0)
            if last_type_time > 0 and (time.time() - last_type_time) < _TYPE_COOLDOWN:
                logger.info(
                    "Skipping %s tweet — type cooldown (%.0f min left): %.60s",
                    tweet_type, (_TYPE_COOLDOWN - (time.time() - last_type_time)) / 60, text,
                )
                return

        # Enforce minimum gap — SKIP instead of sleeping to prevent queue buildup
        now = time.time()
        gap = now - _last_emit_time
        if _last_emit_time > 0 and gap < _MIN_TWEET_GAP:
            logger.info(
                "Skipping tweet — only %.0fs since last tweet (min gap %ds): %.60s",
                gap, _MIN_TWEET_GAP, text,
            )
            return

        success = twitter_client.post_tweet(text)
        if success:
            _last_emit_time = time.time()
            state.record_last_emit_time()
            _last_emit_text = text
            if tweet_type != "general":
                _type_last_emit[tweet_type] = _last_emit_time
            ai_writer.record_recent_tweet(text)
            webhook_alerts.broadcast(text)
            # Record content category for variety tracking
            if tweet_type != "general":
                state.record_content_category(tweet_type)
            # Track coins mentioned for cross-source dedup
            coins = _extract_coin_symbols(text)
            if coins:
                state.record_coins_mentioned(coins)
            # Track sentiment for balancing
            state.record_sentiment(text)
            # Update topic dedup/cooldown state
            _record_emit_state(text)


def _record_emit_state(text: str) -> None:
    """Update rolling topic history and group cooldown timestamps after a post."""
    global _last_2_emit_topics
    topics = _extract_topics(text)
    _last_2_emit_topics.append(topics)
    if len(_last_2_emit_topics) > 2:
        _last_2_emit_topics = _last_2_emit_topics[-2:]
    now = time.monotonic()
    for group, keywords in _TOPIC_GROUPS.items():
        if topics & keywords:
            _topic_group_last_post[group] = now


# Image probability per tweet type:
#   price_alert / morning_recap → always
#   hot_take                    → 50% of the time
#   news                        → 30% of the time
_IMAGE_ODDS = {
    "price_alert":   1.0,
    "morning_recap": 1.0,
    "hot_take":      0.5,
    "news":          0.3,
}


def _emit(text: str, bypass_guard: bool = False,
          tweet_type: str = "news", image_kwargs: dict | None = None) -> None:
    """Post a tweet (with image) or print it (dry-run mode).

    bypass_guard=True skips the minimum-interval posting guard.
    tweet_type: passed to image_generator to pick the right template.
    image_kwargs: extra keyword args forwarded to generate_image_for_tweet.
    """
    global _last_emit_time
    now = time.monotonic()
    if not bypass_guard and (now - _last_emit_time) < _POSTING_GUARD_INTERVAL:
        remaining = _POSTING_GUARD_INTERVAL - (now - _last_emit_time)
        logger.warning(
            "Posting guard active — skipping emit (%.0fs remaining). "
            "Use bypass_guard=True to override.",
            remaining,
        )
        return

    # Monthly tweet cap check (1,500/month on free tier)
    if not state.can_tweet():
        logger.critical(
            "Monthly tweet cap (%d) reached — skipping post until next month.",
            state.MONTHLY_TWEET_CAP,
        )
        return

    # Topic group cooldown — no two L2/DeFi/regulation/stablecoin posts within 2 hours
    cooldown_blocked, cooldown_reason = _check_topic_cooldown(text)
    if cooldown_blocked:
        logger.warning("COOLDOWN blocked tweet (%s): %.80s…", cooldown_reason, text)
        return

    # Topic dedup — reject if ≥3 topic keywords overlap with either of the last 2 posts
    dedup_blocked, dedup_reason = _is_duplicate_topic(text)
    if dedup_blocked:
        logger.warning("DEDUP blocked tweet (%s): %.80s…", dedup_reason, text)
        return

    _last_emit_time = now

    # Generate a matching image only when the dice roll says so
    img_path = None
    if random.random() < _IMAGE_ODDS.get(tweet_type, 0.3):
        img_path = image_generator.generate_image_for_tweet(
            tweet_text=text,
            tweet_type=tweet_type,
            **(image_kwargs or {}),
        )

    if DRY_RUN:
        img_note = f"[image: {img_path}]" if img_path else "[no image]"
        print(f"\n{'─'*60}\n[DRY RUN] Would tweet:\n{text}\n{img_note}\n{'─'*60}")
        _record_emit_state(text)
        # Clean up temp file in dry-run
        if img_path:
            import os
            try:
                os.unlink(img_path)
            except OSError:
                pass
    else:
        if twitter_client.post_tweet(text, image_path=img_path):
            state.record_tweet()
            _record_emit_state(text)


# ── Jobs ──────────────────────────────────────────────────────────────────────
def run_price_check() -> None:
    logger.info("Running price check…")
    alerts = price_monitor.check_prices()
    if not alerts:
        logger.info("No significant price moves detected.")
        return
    for alert in alerts:
        tweet = ai_writer.generate_price_tweet(alert)
        logger.info(
            "Price alert: %s %+.1f%% (%s)",
            alert["symbol"], alert["pct_change"], alert["window"],
        )
        from price_monitor import _format_price
        _emit(tweet, tweet_type="price_alert", image_kwargs={
            "symbol":     alert["symbol"],
            "price":      _format_price(alert["price_usd"]),
            "pct_change": alert["pct_change"],
            "window":     alert["window"],
        })
        time.sleep(2)   # small pause between tweets


def run_quote_tweet() -> None:
    """Search for an engaging crypto tweet and post a quote-tweet reply."""
    global _quote_tweet_count, _quote_tweet_reset_date

    today = datetime.date.today()
    if _quote_tweet_reset_date != today:
        _quote_tweet_count = 0
        _quote_tweet_reset_date = today

    if _quote_tweet_count >= QUOTE_TWEET_DAILY_CAP:
        logger.info(
            "Quote tweet daily cap (%d) reached – skipping until tomorrow.",
            QUOTE_TWEET_DAILY_CAP,
        )
        return

    logger.info("Running quote tweet search…")
    candidates = twitter_client.search_crypto_tweets(min_followers=5000, hours=2)

    if not candidates:
        logger.info("No candidate tweets found.")
        return

    for tweet in candidates:
        if tweet["id"] in _quoted_tweet_ids:
            continue

        reply = ai_writer.generate_quote_tweet(tweet["text"])
        logger.info(
            "Quote tweeting id=%s (likes=%d rt=%d followers=%d): %.60s…",
            tweet["id"], tweet["like_count"], tweet["retweet_count"],
            tweet["followers_count"], reply,
        )

        if DRY_RUN:
            print(
                f"\n{'─'*60}\n[DRY RUN] Would quote tweet {tweet['id']}:\n"
                f"Original: {tweet['text'][:100]}\nReply: {reply}\n{'─'*60}"
            )
        else:
            if not twitter_client.post_quote_tweet(reply, tweet["id"]):
                return   # API error – don't mark as quoted or increment counter

        _quoted_tweet_ids.add(tweet["id"])
        _quote_tweet_count += 1
        return   # one quote tweet per run

    logger.info("No unquoted candidates found this cycle.")


# ── Scheduled content state ───────────────────────────────────────────────────
# Each scheduled slot fires once per day; track last-fired date to prevent double-posts.
_fired_today: dict[str, datetime.date] = {}


def _should_fire(slot: str, hour: int) -> bool:
    """Return True if `slot` should fire now (UK hour matches and hasn't fired today)."""
    now_uk = datetime.datetime.now(_LONDON_TZ)
    today = now_uk.date()
    if now_uk.hour != hour:
        return False
    if _fired_today.get(slot) == today:
        return False
    _fired_today[slot] = today
    return True


def run_hot_take(hour: int) -> None:
    """Post a punchy analyst observation. Fires at `hour` UK time."""
    slot = f"hot_take_{hour}"
    if not _should_fire(slot, hour):
        return
    logger.info("Running hot take (%02d:00)…", hour)
    tweet = ai_writer.generate_hot_take()
    if tweet:
        logger.info("Hot take: %.80s", tweet)
        _emit(tweet, bypass_guard=True, tweet_type="hot_take")
    else:
        logger.warning("Hot take generation returned empty — skipping.")


_evening_thread_topics = [
    "Why stablecoin market cap growth matters more than Bitcoin price right now",
    "The real story behind declining CEX trading volumes and what it means for DeFi",
    "Layer 2 adoption metrics: which numbers actually matter and which are misleading",
    "How ETF inflows are reshaping Bitcoin's correlation with macro assets",
    "The gap between on-chain activity and price action — and what historically follows",
    "Why miner behaviour post-halving is different this cycle than previous ones",
]
_thread_topic_index: int = 0


def run_evening_thread() -> None:
    """Post a 5-tweet deep-dive thread at 18:00 UK time."""
    if not _should_fire("evening_thread", 18):
        return
    global _thread_topic_index
    topic = _evening_thread_topics[_thread_topic_index % len(_evening_thread_topics)]
    _thread_topic_index += 1
    logger.info("Running evening thread on: %s", topic)
    tweets = ai_writer.generate_thread(topic, n_tweets=5)
    if not tweets:
        logger.warning("Evening thread generation failed — skipping.")
        return
    if DRY_RUN:
        print(f"\n{'─'*60}\n[DRY RUN] Evening thread ({len(tweets)} tweets):")
        for i, t in enumerate(tweets, 1):
            print(f"  [{i}] {t}")
        print('─'*60)
    else:
        ok = twitter_client.post_thread(tweets)
        if ok:
            logger.info("Evening thread posted (%d tweets).", len(tweets))
        else:
            logger.error("Evening thread failed mid-way through.")


def run_fear_greed_tweet() -> None:
    """Post a market-sentiment hot take at 21:00 UK time."""
    if not _should_fire("fear_greed", 21):
        return
    logger.info("Running 21:00 Fear & Greed tweet…")
    context = "Evening UK session. Summarise the day's dominant market sentiment — fear, greed, or neutral — and what's driving it. Reference at least one concrete data point."
    tweet = ai_writer.generate_hot_take(context=context)
    if tweet:
        logger.info("Fear & Greed tweet: %.80s", tweet)
        _emit(tweet, bypass_guard=True, tweet_type="hot_take")
    else:
        logger.warning("Fear & Greed tweet generation returned empty — skipping.")


_morning_recap_last_date: datetime.date | None = None


def run_morning_recap() -> None:
    """Post a morning market summary at 08:00 UK time (handles GMT/BST automatically)."""
    global _morning_recap_last_date
    now_uk = datetime.datetime.now(_LONDON_TZ)
    today = now_uk.date()
    if now_uk.hour != 8 or _morning_recap_last_date == today:
        return
    _morning_recap_last_date = today
    logger.info("Running morning recap…")
    headlines = news_monitor.fetch_latest_headlines(3)
    if not headlines:
        logger.info("No headlines available for morning recap.")
        return
    tweet = ai_writer.generate_morning_recap(headlines)
    logger.info("Morning recap: %.80s", tweet)
    _emit(tweet, bypass_guard=True, tweet_type="morning_recap",
          image_kwargs={"headlines": headlines})


def run_news_check() -> None:
    logger.info("Running news check…")
    stories = news_monitor.check_news()
    if not stories:
        logger.info("No new hot stories.")
        return
    # Post at most 3 news stories per cycle to avoid flooding
    for story in stories[:3]:
        tweet = news_monitor.format_news_tweet(story)
        logger.info("News story: %.80s", story.get("title", ""))
        _emit(tweet)
        time.sleep(2)


# ── Scheduler setup ───────────────────────────────────────────────────────────
def setup_schedule() -> None:
    schedule.every(config.PRICE_CHECK_INTERVAL).seconds.do(run_price_check)
    schedule.every(config.NEWS_CHECK_INTERVAL).seconds.do(run_news_check)
    # Quote tweets disabled until following grows — standalone content only
    # schedule.every(4).hours.do(run_quote_tweet)

    # Scheduled content — checked every minute, fires once per slot per day (UK time)
    schedule.every(1).minutes.do(run_morning_recap)
    schedule.every(1).minutes.do(lambda: run_hot_take(14))
    schedule.every(1).minutes.do(run_evening_thread)
    schedule.every(1).minutes.do(lambda: run_hot_take(20))
    schedule.every(1).minutes.do(run_fear_greed_tweet)

    logger.info(
        "Scheduled: price every %ds | news every %ds | "
        "08:00 morning recap | 14:00 hot take | 18:00 thread | "
        "20:00 hot take | 21:00 Fear & Greed  (all UK time)",
        config.PRICE_CHECK_INTERVAL,
        config.NEWS_CHECK_INTERVAL,
    )


# ── Graceful shutdown ─────────────────────────────────────────────────────────
def _shutdown(signum, frame):  # noqa: ARG001
    logger.info("Received signal %d – shutting down.", signum)
    sys.exit(0)


signal.signal(signal.SIGTERM, _shutdown)
signal.signal(signal.SIGINT,  _shutdown)


# ── Main ──────────────────────────────────────────────────────────────────────
def main() -> None:
    global DRY_RUN

    parser = argparse.ArgumentParser(description="Crypto News Twitter Bot")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print tweets to stdout instead of posting to Twitter",
    )
    args = parser.parse_args()
    DRY_RUN = args.dry_run

    if DRY_RUN:
        logger.info("DRY RUN mode – no tweets will be posted.")

    logger.info("Crypto bot starting up…")

    # Validate Twitter credentials early (skipped in dry-run)
    if not DRY_RUN:
        try:
            twitter_client.get_client()
            logger.info("Twitter credentials OK.")
        except RuntimeError as exc:
            logger.critical("Cannot start: %s", exc)
            sys.exit(1)

    setup_schedule()

    # Run both checks immediately on startup so you don't wait 5-10 min
    run_price_check()
    run_news_check()

    logger.info("Entering main loop (Ctrl-C or SIGTERM to stop).")
    while True:
        schedule.run_pending()
        time.sleep(10)


if __name__ == "__main__":
    main()
