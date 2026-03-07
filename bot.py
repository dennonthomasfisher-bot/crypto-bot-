#!/usr/bin/env python3
"""
Crypto News Twitter Bot – main entry point.

Runs recurring jobs on a schedule:
  • Price monitor     – every PRICE_CHECK_INTERVAL seconds
  • News monitor      – every NEWS_CHECK_INTERVAL seconds
  • Quote tweets      – every 4 hours (cap 4/day)
  • Auto-replies      – every 30 minutes (cap 8/day)
  • Morning recap     – daily at 08:00 UK
  • Opinion tweet     – daily at 12:00 UK
  • Polymarket daily  – daily at 15:00 UK
  • Polymarket scan   – every 30 minutes (alerts on big moves)

Usage:
    python bot.py            # run forever (use Ctrl-C to stop)
    python bot.py --dry-run  # print tweets to stdout instead of posting
"""

import argparse
import logging
import signal
import sys
import time

import schedule

import config
import state
import price_monitor
import news_monitor
import twitter_client
import tweet_generators
import auto_replier
import polymarket_monitor

# ── Logging ──────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
    handlers=[
        logging.FileHandler(config.LOG_FILE),
        logging.StreamHandler(sys.stdout),
    ],
)
logger = logging.getLogger("bot")

# ── Globals ──────────────────────────────────────────────────────────────────
DRY_RUN = False


def _emit(text: str) -> None:
    """Post a tweet or print it (dry-run mode)."""
    if DRY_RUN:
        print(f"\n{'─'*60}\n[DRY RUN] Would tweet:\n{text}\n{'─'*60}")
    else:
        twitter_client.post_tweet(text)


# ── Jobs ─────────────────────────────────────────────────────────────────────

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
        time.sleep(2)


def run_news_check() -> None:
    logger.info("Running news check…")
    stories = news_monitor.check_news()
    if not stories:
        logger.info("No new hot stories.")
        return
    for story in stories[:3]:
        tweet = news_monitor.format_news_tweet(story)
        logger.info("News story: %.80s", story.get("title", ""))
        _emit(tweet)
        time.sleep(2)


def run_quote_tweet() -> None:
    if not tweet_generators.can_quote_tweet():
        logger.info("Quote tweet daily cap (%d) reached.", config.QUOTE_TWEET_DAILY_CAP)
        return
    logger.info("Generating quote tweet…")
    tweet = tweet_generators.generate_quote_tweet()
    if tweet:
        tweet_generators.record_quote_tweet()
        _emit(tweet)
    else:
        logger.info("Could not generate quote tweet (no data).")


def run_auto_replies() -> None:
    if not tweet_generators.can_auto_reply():
        logger.info("Auto-reply daily cap (%d) reached.", config.AUTO_REPLY_DAILY_CAP)
        return
    logger.info("Running auto-replies…")
    try:
        count = auto_replier.find_and_reply()
        logger.info("Auto-replied to %d tweets.", count)
    except Exception as exc:
        logger.error("Auto-reply error: %s", exc)


def run_morning_recap() -> None:
    logger.info("Generating morning recap…")
    tweet = tweet_generators.generate_morning_recap()
    if tweet:
        _emit(tweet)
    else:
        logger.info("Could not generate morning recap (no data).")


def run_opinion_tweet() -> None:
    logger.info("Generating opinion tweet…")
    tweet = tweet_generators.generate_opinion_tweet()
    if tweet:
        _emit(tweet)
    else:
        logger.info("Could not generate opinion tweet (no data).")


def run_polymarket_scan() -> None:
    logger.info("Running polymarket scan…")
    alerts = polymarket_monitor.scan_markets()
    for alert in alerts[:2]:
        tweet = polymarket_monitor.format_polymarket_alert(alert)
        logger.info("Polymarket alert: %.80s", alert.get("question", ""))
        _emit(tweet)
        time.sleep(2)


def run_polymarket_daily() -> None:
    logger.info("Generating polymarket daily summary…")
    tweet = polymarket_monitor.format_daily_summary([])
    if tweet:
        _emit(tweet)
    else:
        logger.info("No polymarket data for daily summary.")


# ── Scheduler setup ──────────────────────────────────────────────────────────

def setup_schedule() -> None:
    # Recurring interval jobs
    schedule.every(config.PRICE_CHECK_INTERVAL).seconds.do(run_price_check)
    schedule.every(config.NEWS_CHECK_INTERVAL).seconds.do(run_news_check)
    schedule.every(config.QUOTE_TWEET_INTERVAL).seconds.do(run_quote_tweet)
    schedule.every(config.AUTO_REPLY_INTERVAL).seconds.do(run_auto_replies)
    schedule.every(config.POLYMARKET_CHECK_INTERVAL).seconds.do(run_polymarket_scan)

    # Daily scheduled tweets (UK time)
    schedule.every().day.at(config.MORNING_RECAP_TIME).do(run_morning_recap)
    schedule.every().day.at(config.OPINION_TWEET_TIME).do(run_opinion_tweet)
    schedule.every().day.at(config.POLYMARKET_DAILY_TIME).do(run_polymarket_daily)

    logger.info(
        "Scheduled: price every %ds, news every %ds, quote tweets every %ds "
        "(cap %d/day), auto-replies every %ds (cap %d/day), "
        "morning recap at %s UK, opinion tweet at %s UK, "
        "polymarket check every %ds, polymarket daily at %s UK",
        config.PRICE_CHECK_INTERVAL,
        config.NEWS_CHECK_INTERVAL,
        config.QUOTE_TWEET_INTERVAL,
        config.QUOTE_TWEET_DAILY_CAP,
        config.AUTO_REPLY_INTERVAL,
        config.AUTO_REPLY_DAILY_CAP,
        config.MORNING_RECAP_TIME,
        config.OPINION_TWEET_TIME,
        config.POLYMARKET_CHECK_INTERVAL,
        config.POLYMARKET_DAILY_TIME,
    )


# ── Graceful shutdown ────────────────────────────────────────────────────────

def _shutdown(signum, frame):  # noqa: ARG001
    logger.info("Received signal %d – shutting down.", signum)
    sys.exit(0)


signal.signal(signal.SIGTERM, _shutdown)
signal.signal(signal.SIGINT,  _shutdown)


# ── Main ─────────────────────────────────────────────────────────────────────

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

    # Load persistent state (dedup tracking, tweet counter)
    state.load()

    # Validate Twitter credentials early (skipped in dry-run)
    if not DRY_RUN:
        try:
            twitter_client.get_client()
            logger.info("Twitter credentials OK.")
        except RuntimeError as exc:
            logger.critical("Cannot start: %s", exc)
            sys.exit(1)

    setup_schedule()

    # Run checks immediately on startup
    run_price_check()
    run_news_check()

    logger.info("Entering main loop (Ctrl-C or SIGTERM to stop).")
    while True:
        schedule.run_pending()
        time.sleep(10)


if __name__ == "__main__":
    main()
