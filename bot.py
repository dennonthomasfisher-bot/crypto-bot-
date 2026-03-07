#!/usr/bin/env python3
"""
Crypto News Twitter Bot – main entry point.

Runs recurring jobs on a schedule:
  • Price monitor     – every 10 minutes
  • News monitor      – every 10 minutes
  • Quote tweets      – every hour (cap 8/day, AI-generated)
  • Auto-replies      – every 30 minutes (cap 8/day, AI-powered)
  • Morning recap     – daily at 08:00 UK
  • Opinion tweet     – daily at 12:00 UK
  • Analysis thread   – daily at 18:00 UK (3-tweet deep dive)
  • Polymarket scan   – every 30 minutes
  • Polymarket daily  – daily at 15:00 UK
  • Engagement check  – every hour (tracks tweet performance)
  • Webhook alerts    – Discord & Telegram (optional)

Usage:
    python bot.py            # run forever (use Ctrl-C to stop)
    python bot.py --dry-run  # print tweets to stdout instead of posting
"""

from __future__ import annotations

import argparse
import logging
import os
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
import thread_poster
import engagement_tracker
import webhook_alerts
import growth_engine
import ai_writer

_PID_FILE = os.path.join(os.path.dirname(__file__), "bot.pid")
_STARTUP_COOLDOWN = 30  # seconds — skip immediate tweets if last run was <30s ago

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


def _emit(text: str, tweet_type: str = "general") -> None:
    """Post a tweet or print it (dry-run mode). Also sends to webhooks."""
    if DRY_RUN:
        print(f"\n{'─'*60}\n[DRY RUN] Would tweet:\n{text}\n{'─'*60}")
    else:
        success = twitter_client.post_tweet(text)
        if success:
            ai_writer.record_recent_tweet(text)
            webhook_alerts.broadcast(text)
            # Track for engagement analytics (we don't have the tweet ID here,
            # but engagement_tracker.update_metrics will fetch it later)


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
    try:
        tweet = tweet_generators.generate_morning_recap()
        if tweet:
            _emit(tweet)
        else:
            logger.info("Could not generate morning recap (no data).")
    except Exception as exc:
        logger.error("Morning recap error: %s", exc)


def run_opinion_tweet() -> None:
    logger.info("Generating opinion tweet…")
    try:
        tweet = tweet_generators.generate_opinion_tweet()
        if tweet:
            _emit(tweet)
        else:
            logger.info("Could not generate opinion tweet (no data).")
    except Exception as exc:
        logger.error("Opinion tweet error: %s", exc)


def run_polymarket_scan() -> None:
    logger.info("Running polymarket scan…")
    try:
        alerts = polymarket_monitor.scan_markets()
        for alert in alerts[:2]:
            tweet = polymarket_monitor.format_polymarket_alert(alert)
            logger.info("Polymarket alert: %.80s", alert.get("question", ""))
            _emit(tweet)
            time.sleep(2)
    except Exception as exc:
        logger.error("Polymarket scan error: %s", exc)


def run_polymarket_daily() -> None:
    logger.info("Generating polymarket daily summary…")
    try:
        tweet = polymarket_monitor.format_daily_summary()
        if tweet:
            _emit(tweet)
        else:
            logger.info("No polymarket data for daily summary.")
    except Exception as exc:
        logger.error("Polymarket daily error: %s", exc)


def run_engagement_tweet() -> None:
    logger.info("Generating engagement tweet…")
    try:
        tweet = tweet_generators.generate_engagement_tweet()
        if tweet:
            _emit(tweet)
        else:
            logger.info("Could not generate engagement tweet (no data).")
    except Exception as exc:
        logger.error("Engagement tweet error: %s", exc)


def run_thread() -> None:
    logger.info("Generating analysis thread…")
    try:
        thread_poster.post_thread(dry_run=DRY_RUN)
    except Exception as exc:
        logger.error("Thread posting error: %s", exc)


def run_influencer_callout() -> None:
    if not config.GROWTH_ENABLED or not config.INFLUENCER_MENTIONS:
        return
    logger.info("Generating influencer callout tweet…")
    try:
        tweet = growth_engine.generate_influencer_callout()
        if tweet:
            _emit(tweet, "influencer_callout")
        else:
            logger.info("Could not generate influencer callout (no data or all on cooldown).")
    except Exception as exc:
        logger.error("Influencer callout error: %s", exc)


def run_ct_narrative() -> None:
    if not config.GROWTH_ENABLED:
        return
    if not tweet_generators.can_ct_narrative():
        logger.info("CT narrative daily cap (%d) reached.", config.CT_NARRATIVE_DAILY_CAP)
        return
    logger.info("Generating CT narrative tweet…")
    try:
        tweet = growth_engine.generate_ct_narrative_tweet()
        if tweet:
            tweet_generators.record_ct_narrative()
            _emit(tweet, "ct_narrative")
        else:
            logger.info("Could not generate CT narrative tweet.")
    except Exception as exc:
        logger.error("CT narrative error: %s", exc)


def run_hot_take() -> None:
    if not config.GROWTH_ENABLED:
        return
    logger.info("Generating hot take…")
    try:
        tweet = growth_engine.generate_hot_take()
        if tweet:
            _emit(tweet, "hot_take")
        else:
            logger.info("Could not generate hot take.")
    except Exception as exc:
        logger.error("Hot take error: %s", exc)


def run_engagement_check() -> None:
    """Fetch engagement metrics for recent tweets."""
    if DRY_RUN:
        return
    try:
        client = twitter_client.get_client()
        updated = engagement_tracker.update_metrics(client)
        if updated:
            logger.info("Updated engagement for %d tweets", updated)
            best_hours = engagement_tracker.get_best_posting_hours()
            if best_hours:
                logger.info("Best posting hours (UTC): %s", best_hours)
    except Exception as exc:
        logger.error("Engagement check error: %s", exc)


# ── Scheduler setup ──────────────────────────────────────────────────────────

def setup_schedule() -> None:
    # Recurring interval jobs
    schedule.every(config.PRICE_CHECK_INTERVAL).seconds.do(run_price_check)
    schedule.every(config.NEWS_CHECK_INTERVAL).seconds.do(run_news_check)
    schedule.every(config.QUOTE_TWEET_INTERVAL).seconds.do(run_quote_tweet)
    schedule.every(config.AUTO_REPLY_INTERVAL).seconds.do(run_auto_replies)
    schedule.every(config.POLYMARKET_CHECK_INTERVAL).seconds.do(run_polymarket_scan)

    # Engagement tracking
    schedule.every(config.ENGAGEMENT_CHECK_INTERVAL).seconds.do(run_engagement_check)

    # Daily scheduled tweets (UK time)
    schedule.every().day.at(config.MORNING_RECAP_TIME).do(run_morning_recap)
    schedule.every().day.at(config.OPINION_TWEET_TIME).do(run_opinion_tweet)
    schedule.every().day.at(config.ENGAGEMENT_TWEET_TIME).do(run_engagement_tweet)
    schedule.every().day.at(config.THREAD_TIME).do(run_thread)
    schedule.every().day.at(config.POLYMARKET_DAILY_TIME).do(run_polymarket_daily)

    # Growth engine jobs
    if config.GROWTH_ENABLED:
        schedule.every().day.at(config.INFLUENCER_CALLOUT_TIME).do(run_influencer_callout)
        schedule.every(config.CT_NARRATIVE_INTERVAL).seconds.do(run_ct_narrative)
        schedule.every().day.at(config.HOT_TAKE_TIME_1).do(run_hot_take)
        schedule.every().day.at(config.HOT_TAKE_TIME_2).do(run_hot_take)
        logger.info(
            "Growth engine ON: influencer callout at %s UK, CT narrative every %ds "
            "(cap %d/day), hot takes at %s & %s UK",
            config.INFLUENCER_CALLOUT_TIME,
            config.CT_NARRATIVE_INTERVAL,
            config.CT_NARRATIVE_DAILY_CAP,
            config.HOT_TAKE_TIME_1,
            config.HOT_TAKE_TIME_2,
        )
    else:
        logger.info("Growth engine disabled.")

    logger.info(
        "Scheduled: price every %ds, news every %ds, quote tweets every %ds "
        "(cap %d/day), auto-replies every %ds (cap %d/day), "
        "morning recap at %s UK, opinion tweet at %s UK, "
        "thread at %s UK, engagement every %ds, "
        "polymarket check every %ds, polymarket daily at %s UK",
        config.PRICE_CHECK_INTERVAL,
        config.NEWS_CHECK_INTERVAL,
        config.QUOTE_TWEET_INTERVAL,
        config.QUOTE_TWEET_DAILY_CAP,
        config.AUTO_REPLY_INTERVAL,
        config.AUTO_REPLY_DAILY_CAP,
        config.MORNING_RECAP_TIME,
        config.OPINION_TWEET_TIME,
        config.THREAD_TIME,
        config.ENGAGEMENT_CHECK_INTERVAL,
        config.POLYMARKET_CHECK_INTERVAL,
        config.POLYMARKET_DAILY_TIME,
    )

    # Log webhook status
    wh = webhook_alerts.status()
    active = [k for k, v in wh.items() if v]
    if active:
        logger.info("Webhook alerts active: %s", ", ".join(active))
    else:
        logger.info("No webhook alerts configured (Discord/Telegram optional)")


# ── Graceful shutdown ────────────────────────────────────────────────────────

def _acquire_pid() -> None:
    """Write PID file, exiting if another instance is running."""
    if os.path.exists(_PID_FILE):
        try:
            old_pid = int(open(_PID_FILE).read().strip())
            # Check if the old process is actually running
            os.kill(old_pid, 0)
            logger.critical(
                "Another instance is already running (PID %d). "
                "Stop it first, or delete %s if it is stale.",
                old_pid, _PID_FILE,
            )
            sys.exit(1)
        except (OSError, ValueError):
            # Process not running or invalid PID — stale file
            logger.info("Removing stale PID file (old process gone).")
    with open(_PID_FILE, "w") as f:
        f.write(str(os.getpid()))


def _release_pid() -> None:
    """Remove PID file on shutdown."""
    try:
        os.remove(_PID_FILE)
    except OSError:
        pass


def _shutdown(signum, frame):  # noqa: ARG001
    logger.info("Received signal %d – shutting down.", signum)
    _release_pid()
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

    # Ensure only one instance runs at a time
    _acquire_pid()

    # Load persistent state
    state.load()
    engagement_tracker.load()

    # Validate Twitter credentials early (skipped in dry-run)
    if not DRY_RUN:
        try:
            twitter_client.get_client()
            logger.info("Twitter credentials OK.")
        except RuntimeError as exc:
            logger.critical("Cannot start: %s", exc)
            _release_pid()
            sys.exit(1)

    setup_schedule()

    # Run price/news checks on startup, but only if we haven't run recently
    # (prevents tweet spam from rapid restarts)
    last_run = state.get_last_run_time()
    elapsed = time.time() - last_run if last_run else _STARTUP_COOLDOWN + 1
    if elapsed >= _STARTUP_COOLDOWN:
        logger.info("Running startup checks (last run %.0fs ago).", elapsed)
        run_price_check()
        run_news_check()
        # Always try a quote tweet on startup so the feed stays active
        run_quote_tweet()
        state.record_last_run_time()
    else:
        logger.info(
            "Skipping startup checks — last run was only %.0fs ago (cooldown %ds).",
            elapsed, _STARTUP_COOLDOWN,
        )

    logger.info("Entering main loop (Ctrl-C or SIGTERM to stop).")
    try:
        while True:
            schedule.run_pending()
            time.sleep(10)
    finally:
        _release_pid()


if __name__ == "__main__":
    main()
