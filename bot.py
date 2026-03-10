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
import signal
import sys
import time
from zoneinfo import ZoneInfo

import schedule

import ai_writer
import config
import news_monitor
import price_monitor
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


def _emit(text: str, bypass_guard: bool = False) -> None:
    """Post a tweet or print it (dry-run mode).

    bypass_guard=True skips the minimum-interval posting guard, which is
    appropriate for scheduled threads like the morning recap that must fire
    regardless of recent activity.
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
    _last_emit_time = now
    if DRY_RUN:
        print(f"\n{'─'*60}\n[DRY RUN] Would tweet:\n{text}\n{'─'*60}")
    else:
        twitter_client.post_tweet(text)


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
        _emit(tweet)
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
    _emit(tweet, bypass_guard=True)


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
    # Morning recap: check every minute; fires once when UK clock reads 08:00
    schedule.every(1).minutes.do(run_morning_recap)
    logger.info(
        "Scheduled: price every %ds, news every %ds, "
        "quote tweets every 4h (cap %d/day), morning recap at 08:00 UK time",
        config.PRICE_CHECK_INTERVAL,
        config.NEWS_CHECK_INTERVAL,
        QUOTE_TWEET_DAILY_CAP,
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
