#!/usr/bin/env python3
"""
Crypto News Twitter Bot – main entry point.

Scheduled jobs (all times UK/London):
  • Price alerts    – every 5 min (only posts if 3%/1h or 7%/24h move)
  • News            – every 15 min (max 2 stories per run)
  • Trending        – every 2h (max 1 tweet per run)
  • Quote tweet     – every 2h (max 4/day, via tweet_generators)
  • Morning recap   – daily 08:00
  • Opinion         – daily 12:00
  • Hot take        – daily 14:00 + 20:00
  • Evening thread  – daily 18:00
  • Engagement      – daily 16:00
  • Fear & Greed    – daily 21:00

Usage:
    python bot.py            # run forever
    python bot.py --dry-run  # print to stdout instead of posting
"""

import argparse
import datetime
import logging
import os
import re
import random
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
import tweet_generators
import trending_monitor

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

# ── Posting guards ────────────────────────────────────────────────────────────
_QUIET_HOURS_START = 0   # midnight UK
_QUIET_HOURS_END   = 7   # 7am UK

_MIN_TWEET_GAP = 300     # 5 min minimum between any two posts
_TYPE_COOLDOWN = 3600    # 1 hour between same tweet type
_TOPIC_COOLDOWN_SECS = 7200  # 2 hours per topic group

_last_emit_time: float = 0.0
_last_emit_text: str = ""
_type_last_emit: dict[str, float] = {}

# ── Topic dedup ───────────────────────────────────────────────────────────────
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

_TOPIC_GROUPS: dict[str, frozenset[str]] = {
    "L2_DEFI":    frozenset({
        "l2", "layer2", "arbitrum", "optimism", "base", "zksync", "starknet",
        "defi", "dex", "tvl", "bridge", "yield", "liquidity",
    }),
    "REGULATION": frozenset({"sec", "regulation", "cftc", "legal", "lawsuit", "ban"}),
    "STABLECOIN": frozenset({"usdt", "usdc", "stablecoin", "dai", "peg"}),
}

_topic_group_last_post: dict[str, float] = {}
_last_2_emit_topics: list[set[str]] = []


def _is_quiet_hours() -> bool:
    now_uk = datetime.datetime.now(_LONDON_TZ)
    return _QUIET_HOURS_START <= now_uk.hour < _QUIET_HOURS_END


def _extract_topics(text: str) -> set[str]:
    words = {w.lower() for w in re.findall(r'\b\w+\b', text)}
    return words & _TOPIC_KEYWORDS


def _is_duplicate_content(text: str) -> bool:
    """True if >70% word overlap with last post."""
    if not _last_emit_text:
        return False
    a = set(text.lower().split())
    b = set(_last_emit_text.lower().split())
    if not b:
        return False
    return len(a & b) / max(len(a), len(b)) > 0.70


def _is_duplicate_topic(topics: set[str]) -> bool:
    """True if >=3 shared topic keywords with either of the last 2 posts."""
    if not topics:
        return False
    for prev in _last_2_emit_topics[-2:]:
        if len(topics & prev) >= 3:
            return True
    return False


def _check_topic_cooldown(text: str) -> tuple[bool, str]:
    """Return (blocked, reason) if a topic group is in its 2-hour window."""
    now = time.monotonic()
    words = _extract_topics(text)
    for group, keywords in _TOPIC_GROUPS.items():
        if words & keywords:
            last = _topic_group_last_post.get(group, 0.0)
            elapsed = now - last
            if elapsed < _TOPIC_COOLDOWN_SECS:
                remaining = int((_TOPIC_COOLDOWN_SECS - elapsed) / 60)
                return True, f"{group} cooldown ({remaining}m left)"
    return False, ""


def _record_emit_state(text: str) -> None:
    """Update topic history and group cooldown timestamps."""
    global _last_2_emit_topics
    topics = _extract_topics(text)
    _last_2_emit_topics.append(topics)
    if len(_last_2_emit_topics) > 2:
        _last_2_emit_topics = _last_2_emit_topics[-2:]
    now = time.monotonic()
    for group, keywords in _TOPIC_GROUPS.items():
        if topics & keywords:
            _topic_group_last_post[group] = now


_IMAGE_ODDS: dict[str, float] = {
    "price_alert":   1.0,
    "morning_recap": 1.0,
    "hot_take":      0.5,
    "news":          0.3,
}


# ── Core emit ─────────────────────────────────────────────────────────────────
def _emit(
    text: str,
    tweet_type: str = "general",
    bypass_guard: bool = False,
    image_kwargs: dict | None = None,
) -> bool:
    """Post a tweet (or print in dry-run). Returns True if posted/printed."""
    global _last_emit_time, _last_emit_text

    if not text or not text.strip():
        logger.warning("_emit called with empty text — skipping")
        return False

    if not state.can_tweet():
        logger.critical("Monthly tweet cap reached.")
        return False

    if DRY_RUN:
        print(f"\n{'─'*60}\n[DRY RUN] [{tweet_type}]\n{text}\n{'─'*60}")
        _last_emit_text = text
        _record_emit_state(text)
        return True

    if not bypass_guard and _is_quiet_hours():
        logger.info("Quiet hours — skipping: %.60s", text)
        return False

    if _is_duplicate_content(text):
        logger.info("Skipping — duplicate content: %.60s", text)
        return False

    topics = _extract_topics(text)
    if _is_duplicate_topic(topics):
        logger.info("Skipping — duplicate topic: %.60s", text)
        return False

    blocked, reason = _check_topic_cooldown(text)
    if blocked:
        logger.info("Skipping — %s: %.60s", reason, text)
        return False

    if tweet_type != "general":
        last_type = _type_last_emit.get(tweet_type, 0.0)
        if last_type > 0 and (time.time() - last_type) < _TYPE_COOLDOWN:
            mins_left = int((_TYPE_COOLDOWN - (time.time() - last_type)) / 60)
            logger.info("Skipping %s — type cooldown (%dm left): %.60s",
                        tweet_type, mins_left, text)
            return False

    now = time.time()
    if not bypass_guard and _last_emit_time > 0 and (now - _last_emit_time) < _MIN_TWEET_GAP:
        mins_left = int((_MIN_TWEET_GAP - (now - _last_emit_time)) / 60)
        logger.info("Skipping — min gap (%dm left): %.60s", mins_left, text)
        return False

    img_path = None
    if random.random() < _IMAGE_ODDS.get(tweet_type, 0.3):
        try:
            img_path = image_generator.generate_image_for_tweet(
                tweet_text=text,
                tweet_type=tweet_type,
                **(image_kwargs or {}),
            )
        except Exception as exc:
            logger.warning("Image generation failed: %s", exc)

    posted = twitter_client.post_tweet(text, image_path=img_path)
    if posted:
        _last_emit_time = time.time()
        _last_emit_text = text
        if tweet_type != "general":
            _type_last_emit[tweet_type] = _last_emit_time
        state.record_tweet()
        ai_writer.record_recent_tweet(text)
        _record_emit_state(text)
        logger.info("Posted [%s]: %.80s", tweet_type, text)

    if img_path:
        try:
            os.unlink(img_path)
        except OSError:
            pass

    return bool(posted)


# ── Daily slot guard ──────────────────────────────────────────────────────────
_fired_today: dict[str, datetime.date] = {}


def _should_fire(slot: str, hour: int) -> bool:
    now_uk = datetime.datetime.now(_LONDON_TZ)
    today = now_uk.date()
    if now_uk.hour != hour:
        return False
    if _fired_today.get(slot) == today:
        return False
    _fired_today[slot] = today
    return True


# ── Jobs ──────────────────────────────────────────────────────────────────────

def run_price_check() -> None:
    logger.info("Running price check…")
    alerts = price_monitor.check_prices()
    if not alerts:
        logger.info("No significant price moves.")
        return
    for alert in alerts:
        tweet = ai_writer.generate_price_tweet(alert)
        if not tweet:
            continue
        from price_monitor import _format_price
        logger.info("Price alert: %s %+.1f%%", alert["symbol"], alert["pct_change"])
        _emit(tweet, tweet_type="price_alert", image_kwargs={
            "symbol":     alert["symbol"],
            "price":      _format_price(alert["price_usd"]),
            "pct_change": alert["pct_change"],
            "window":     alert["window"],
        })
        time.sleep(3)


def run_news_check() -> None:
    logger.info("Running news check…")
    stories = news_monitor.check_news()
    if not stories:
        logger.info("No new stories.")
        return
    for story in stories[:2]:
        tweet = news_monitor.format_news_tweet(story)
        if not tweet:
            continue
        logger.info("News: %.80s", story.get("title", ""))
        _emit(tweet, tweet_type="news")
        time.sleep(3)


def run_trending_check() -> None:
    """Post about a trending coin outside our main watchlist. Max 1 tweet per run."""
    logger.info("Running trending check…")
    alerts = trending_monitor.check_trending()
    if not alerts:
        logger.info("No trending alerts.")
        return
    alert = alerts[0]
    tweet = trending_monitor.format_trending_tweet(alert)
    if tweet:
        logger.info("Trending: %s (%s)", alert["symbol"], alert["source"])
        _emit(tweet, tweet_type="trending")


_quote_tweet_count: int = 0
_quote_tweet_reset_date: datetime.date | None = None
_QUOTE_TWEET_DAILY_CAP = 4


def run_quote_tweet() -> None:
    """Market analysis tweet via tweet_generators (max 4/day)."""
    global _quote_tweet_count, _quote_tweet_reset_date
    today = datetime.date.today()
    if _quote_tweet_reset_date != today:
        _quote_tweet_count = 0
        _quote_tweet_reset_date = today
    if _quote_tweet_count >= _QUOTE_TWEET_DAILY_CAP:
        logger.info("Quote tweet daily cap (%d) reached.", _QUOTE_TWEET_DAILY_CAP)
        return
    tweet = tweet_generators.generate_quote_tweet()
    if tweet:
        if _emit(tweet, tweet_type="quote"):
            _quote_tweet_count += 1


def run_morning_recap() -> None:
    if not _should_fire("morning_recap", 8):
        return
    logger.info("Running morning recap…")
    tweet = tweet_generators.generate_morning_recap()
    if not tweet:
        headlines = news_monitor.fetch_latest_headlines(3)
        if headlines:
            tweet = ai_writer.generate_morning_recap(headlines)
    if tweet:
        _emit(tweet, bypass_guard=True, tweet_type="morning_recap")
    else:
        logger.warning("Morning recap failed — skipping.")


def run_opinion_tweet() -> None:
    if not _should_fire("opinion", 12):
        return
    logger.info("Running opinion tweet (12:00)…")
    tweet = tweet_generators.generate_opinion_tweet()
    if tweet:
        _emit(tweet, bypass_guard=True, tweet_type="hot_take")
    else:
        logger.warning("Opinion tweet failed — skipping.")


def run_hot_take(hour: int) -> None:
    slot = f"hot_take_{hour}"
    if not _should_fire(slot, hour):
        return
    logger.info("Running hot take (%02d:00)…", hour)
    tweet = ai_writer.generate_hot_take()
    if tweet:
        _emit(tweet, bypass_guard=True, tweet_type="hot_take")
    else:
        logger.warning("Hot take generation failed — skipping.")


def run_engagement_tweet() -> None:
    if not _should_fire("engagement", 16):
        return
    logger.info("Running engagement tweet (16:00)…")
    tweet = tweet_generators.generate_engagement_tweet()
    if tweet:
        _emit(tweet, bypass_guard=True, tweet_type="engagement")
    else:
        logger.warning("Engagement tweet failed — skipping.")


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
    if not _should_fire("evening_thread", 18):
        return
    global _thread_topic_index
    topic = _evening_thread_topics[_thread_topic_index % len(_evening_thread_topics)]
    _thread_topic_index += 1
    logger.info("Running evening thread: %s", topic)
    tweets = ai_writer.generate_thread(topic, n_tweets=5)
    if not tweets:
        logger.warning("Evening thread failed — skipping.")
        return
    if DRY_RUN:
        print(f"\n{'─'*60}\n[DRY RUN] Evening thread ({len(tweets)} tweets):")
        for i, t in enumerate(tweets, 1):
            print(f"  [{i}] {t}")
        print('─'*60)
    else:
        ok = twitter_client.post_thread(tweets)
        if ok:
            state.record_tweet(len(tweets))
            logger.info("Evening thread posted (%d tweets).", len(tweets))
        else:
            logger.error("Evening thread failed.")


def run_fear_greed_tweet() -> None:
    if not _should_fire("fear_greed", 21):
        return
    logger.info("Running 21:00 Fear & Greed tweet…")
    context = (
        "Evening UK session. Summarise today's dominant market sentiment — "
        "fear, greed, or neutral — and what's driving it. "
        "Reference at least one concrete data point."
    )
    tweet = ai_writer.generate_hot_take(context=context)
    if tweet:
        _emit(tweet, bypass_guard=True, tweet_type="hot_take")
    else:
        logger.warning("Fear & Greed generation failed — skipping.")


# ── Scheduler ─────────────────────────────────────────────────────────────────
def setup_schedule() -> None:
    schedule.every(5).minutes.do(run_price_check)
    schedule.every(15).minutes.do(run_news_check)
    schedule.every(2).hours.do(run_trending_check)
    schedule.every(2).hours.do(run_quote_tweet)

    schedule.every(1).minutes.do(run_morning_recap)
    schedule.every(1).minutes.do(run_opinion_tweet)
    schedule.every(1).minutes.do(lambda: run_hot_take(14))
    schedule.every(1).minutes.do(run_engagement_tweet)
    schedule.every(1).minutes.do(run_evening_thread)
    schedule.every(1).minutes.do(lambda: run_hot_take(20))
    schedule.every(1).minutes.do(run_fear_greed_tweet)

    logger.info(
        "Scheduled: price/5m | news/15m | trending/2h | quote/2h | "
        "08:00 recap | 12:00 opinion | 14:00 hot-take | 16:00 engagement | "
        "18:00 thread | 20:00 hot-take | 21:00 fear-greed  (UK time)"
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
    parser.add_argument("--dry-run", action="store_true",
                        help="Print tweets instead of posting")
    args = parser.parse_args()
    DRY_RUN = args.dry_run

    if DRY_RUN:
        logger.info("DRY RUN mode — no tweets will be posted.")

    logger.info("Crypto bot starting up…")

    if not DRY_RUN:
        try:
            twitter_client.get_client()
            logger.info("Twitter credentials OK.")
        except RuntimeError as exc:
            logger.critical("Cannot start: %s", exc)
            sys.exit(1)

    setup_schedule()

    run_price_check()
    run_news_check()

    logger.info("Entering main loop (Ctrl-C or SIGTERM to stop).")
    while True:
        schedule.run_pending()
        time.sleep(10)


if __name__ == "__main__":
    main()
