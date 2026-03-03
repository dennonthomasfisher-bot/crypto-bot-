#!/usr/bin/env python3
"""
Crypto News Twitter Bot – main entry point.

Runs two recurring jobs:
  • Price monitor  – every PRICE_CHECK_INTERVAL seconds
  • News monitor   – every NEWS_CHECK_INTERVAL seconds

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


def _emit(text: str) -> None:
    """Post a tweet or print it (dry-run mode)."""
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
        tweet = price_monitor.format_price_tweet(alert)
        logger.info(
            "Price alert: %s %+.1f%% (%s)",
            alert["symbol"], alert["pct_change"], alert["window"],
        )
        _emit(tweet)
        time.sleep(2)   # small pause between tweets


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
    _emit(tweet)


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
    # Morning recap: check every minute; fires once when UK clock reads 08:00
    schedule.every(1).minutes.do(run_morning_recap)
    logger.info(
        "Scheduled: price every %ds, news every %ds, morning recap at 08:00 UK time",
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
