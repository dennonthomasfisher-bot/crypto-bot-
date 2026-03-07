from typing import Optional
#!/usr/bin/env python3
"""
Crypto News Twitter Bot – main entry point.

Runs recurring jobs:
  • Price monitor        – every PRICE_CHECK_INTERVAL seconds
  • Quote tweeter        – every 4 hours (max 4 quote tweets/day)
  • Morning recap        – daily at 08:00 UK time
  • Opinion tweet        – daily at 12:00 UK time
  • Polymarket check     – every 30 minutes (alerts on 10%+ odds moves)
  • Polymarket daily     – daily at 15:00 UK time (top market snapshot)

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

import account_monitor
import ai_writer
import bot_state
import config
import polymarket
import posting_guard
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
_quote_tweet_reset_date: Optional[datetime.date] = None
_quoted_tweet_ids: set[str] = set()   # never quote the same tweet twice

def _emit(text: str, bypass_guard: bool = False) -> None:
    """Post a tweet or print it (dry-run mode).

    Minimum-interval enforcement is handled by posting_guard.py.
    Pass bypass_guard=True for scheduled events (e.g. morning recap)
    that must always fire regardless of the posting interval.
    """
    if not posting_guard.allow_post(bypass=bypass_guard):
        return

    if DRY_RUN:
        print(f"\n{'─'*60}\n[DRY RUN] Would tweet:\n{text}\n{'─'*60}")
    else:
        twitter_client.post_tweet(text)

    posting_guard.record_post()


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


_morning_recap_last_date: Optional[datetime.date] = None
_opinion_tweet_last_date: Optional[datetime.date] = None
_polymarket_daily_last_date: Optional[datetime.date] = None


def run_morning_recap() -> None:
    """Post a morning market summary at 08:00 UK time (handles GMT/BST automatically).

    Uses story titles persisted in bot_state.json (last 24 h) as the headline
    source so the recap always has content regardless of when stories were
    ingested in the current process.
    """
    global _morning_recap_last_date
    now_uk = datetime.datetime.now(_LONDON_TZ)
    today = now_uk.date()
    if now_uk.hour != 8 or _morning_recap_last_date == today:
        return
    _morning_recap_last_date = today
    logger.info("Running morning recap…")

    headlines = bot_state.get_recent_headlines(hours=24)
    if not headlines:
        logger.info("No headlines available for morning recap.")
        return

    tweet = ai_writer.generate_morning_recap(headlines[:3])
    logger.info("Morning recap: %.80s", tweet)
    _emit(tweet, bypass_guard=True)


def run_opinion_tweet() -> None:
    """Post a bold market opinion tweet at 12:00 UK time, once per day."""
    global _opinion_tweet_last_date
    now_uk = datetime.datetime.now(_LONDON_TZ)
    today = now_uk.date()
    if now_uk.hour != 12 or _opinion_tweet_last_date == today:
        return
    _opinion_tweet_last_date = today
    logger.info("Running opinion tweet…")
    tweet = ai_writer.generate_opinion_tweet()
    logger.info("Opinion tweet: %.80s", tweet)
    _emit(tweet, bypass_guard=True)


def run_auto_reply() -> None:
    """
    Check target accounts for new tweets and post a reply to each fresh one.
    Capped at account_monitor.AUTO_REPLY_DAILY_CAP replies per day total.
    One reply per account per cycle; 5-second pause between replies.
    """
    logger.info("Running auto-reply check…")
    candidates = account_monitor.get_reply_candidates()
    if not candidates:
        logger.info("No auto-reply candidates this cycle.")
        return

    for tweet in candidates:
        if bot_state.get_auto_reply_count_today() >= account_monitor.AUTO_REPLY_DAILY_CAP:
            logger.info("Auto-reply daily cap reached mid-cycle – stopping.")
            break

        reply = ai_writer.generate_account_reply(tweet["text"], tweet["author_username"])
        logger.info(
            "Auto-reply to @%s (tweet_id=%s): %.80s",
            tweet["author_username"], tweet["id"], reply,
        )

        if DRY_RUN:
            print(
                f"\n{'─'*60}\n[DRY RUN] Would reply to @{tweet['author_username']} "
                f"({tweet['id']}):\nOriginal: {tweet['text'][:100]}\n"
                f"Reply: {reply}\n{'─'*60}"
            )
            bot_state.record_reply(tweet["id"])
        else:
            if twitter_client.post_reply(reply, tweet["id"]):
                bot_state.record_reply(tweet["id"])
                time.sleep(5)


def run_polymarket_check() -> None:
    """
    Fetch Polymarket crypto markets and post a tweet for any that have moved
    10+ percentage points since the last snapshot.  Runs every 30 minutes.
    """
    logger.info("Running Polymarket odds check…")
    alerts = polymarket.get_polymarket_alerts()
    if not alerts:
        logger.info("No significant Polymarket odds moves this cycle.")
        return
    for alert in alerts:
        tweet = ai_writer.generate_polymarket_tweet(alert)
        logger.info(
            "Polymarket alert: '%s' YES %.0f%% (was %.0f%%, Δ%.0fpp %s): %.80s",
            alert["question"][:50],
            alert["yes_price"] * 100,
            alert["yes_prev"] * 100,
            alert["shift"] * 100,
            alert["direction"],
            tweet,
        )
        _emit(tweet)
        time.sleep(2)


def run_polymarket_daily() -> None:
    """
    Post the single most liquid active crypto prediction market at 15:00 UK time,
    once per day.
    """
    global _polymarket_daily_last_date
    now_uk = datetime.datetime.now(_LONDON_TZ)
    today = now_uk.date()
    if now_uk.hour != 15 or _polymarket_daily_last_date == today:
        return
    _polymarket_daily_last_date = today
    logger.info("Running Polymarket daily post…")

    markets = polymarket.get_top_markets(n=5)
    if not markets:
        logger.info("No Polymarket markets available for daily post.")
        return

    # Pick the most liquid market
    market = markets[0]
    tweet = ai_writer.generate_polymarket_tweet(market)
    logger.info("Polymarket daily: %.80s", tweet)
    _emit(tweet, bypass_guard=True)


# ── Scheduler setup ───────────────────────────────────────────────────────────
def setup_schedule() -> None:
    schedule.every(config.PRICE_CHECK_INTERVAL).seconds.do(run_price_check)
    schedule.every(4).hours.do(run_quote_tweet)
    schedule.every(30).minutes.do(run_auto_reply)
    schedule.every(30).minutes.do(run_polymarket_check)
    # Minute-level checks for time-of-day scheduled posts
    schedule.every(1).minutes.do(run_morning_recap)    # fires at 08:00 UK
    schedule.every(1).minutes.do(run_opinion_tweet)    # fires at 12:00 UK
    schedule.every(1).minutes.do(run_polymarket_daily) # fires at 15:00 UK
    logger.info(
        "Scheduled: price every %ds, "
        "quote tweets every 4h (cap %d/day), "
        "auto-replies every 30min (cap %d/day), "
        "polymarket check every 30min, "
        "morning recap at 08:00 UK, opinion tweet at 12:00 UK, "
        "polymarket daily at 15:00 UK",
        config.PRICE_CHECK_INTERVAL,
        QUOTE_TWEET_DAILY_CAP,
        account_monitor.AUTO_REPLY_DAILY_CAP,
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

    # Run price check immediately on startup so you don't wait 10 min
    run_price_check()

    logger.info("Entering main loop (Ctrl-C or SIGTERM to stop).")
    while True:
        schedule.run_pending()
        time.sleep(10)


if __name__ == "__main__":
    main()
