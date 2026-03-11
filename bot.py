#!/usr/bin/env python3
"""
Crypto News Twitter Bot – main entry point.

Daily cap: 15 tweets/day total.

SCHEDULED (UK/London time):
  • 08:00  morning_recap
  • 12:00  opinion
  • 16:00  engagement
  • 19:00  evening_thread  (3 tweets)
  • 21:00  fear_greed

INTERVAL-DRIVEN (with daily caps):
  • Price alerts  – every 5 min check  | max 3/day | 5%+ 1h or 6%+ 24h move
  • News          – every 15 min check | max 4/day | 60 min cooldown between posts
  • Trending coin – every 2 h check    | max 1/day
  • Quote tweet   – every 2 h check    | max 1/day

Usage:
    python bot.py            # run forever
    python bot.py --dry-run  # print to stdout instead of posting
"""
from __future__ import annotations

import argparse
import datetime
import functools
import logging
import os
import re
import random
import signal
import sys
import time
from schedule import Scheduler as _Scheduler
from zoneinfo import ZoneInfo

import ai_writer
import chart_generator
import config
import image_generator
import news_monitor
import price_monitor
import state
import twitter_client
import tweet_generators
import trending_monitor

_LONDON_TZ = ZoneInfo("Europe/London")

# Logger created at module level; handlers are attached in main() after arg
# parsing so we know whether stdout is being redirected.
logger = logging.getLogger("bot")

# ── Globals ───────────────────────────────────────────────────────────────────
DRY_RUN = False

# ── Posting guards ────────────────────────────────────────────────────────────
_QUIET_HOURS_START = 0   # midnight UK
_QUIET_HOURS_END   = 7   # 7am UK

_MIN_TWEET_GAP = 480     # 8 min minimum between any two posts
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


# ── Private scheduler (NOT the global schedule.default_scheduler) ──────────────
# Using an owned instance prevents double-registration if this module is ever
# imported alongside running as __main__ — both would share the global scheduler
# but have separate _schedule_configured flags, silently adding jobs twice.
_scheduler = _Scheduler()


def _safe(fn):
    """Wrap a scheduled job so any unhandled exception is logged, not fatal."""
    @functools.wraps(fn)
    def _wrapper():
        try:
            return fn()
        except Exception:
            logger.exception(
                "Unhandled exception in scheduled job '%s' — job skipped, bot continues.",
                fn.__name__,
            )
    return _wrapper


_IMAGE_ODDS: dict[str, float] = {
    "price_alert":   1.0,
    "morning_recap": 1.0,
    "hot_take":      0.5,
    # "news" intentionally omitted — OG images fetched directly from article
}


# ── Core emit ─────────────────────────────────────────────────────────────────
def _emit(
    text: str,
    tweet_type: str = "general",
    bypass_guard: bool = False,
    image_kwargs: dict | None = None,
    media_path: str | None = None,
) -> bool:
    """Post a tweet (or print in dry-run). Returns True if posted/printed.

    media_path: pre-fetched image file path (used for news OG images).
                Skips image_generator when set.  Caller must NOT delete this
                file — _emit handles cleanup.
    """
    global _last_emit_time, _last_emit_text

    if not text or not text.strip():
        logger.warning("_emit called with empty text — skipping")
        return False

    if not state.can_tweet():
        logger.critical("Monthly tweet cap reached.")
        return False

    if DRY_RUN:
        img_note = f"  [image: {media_path}]" if media_path else ""
        print(f"\n{'─'*60}\n[DRY RUN] [{tweet_type}]{img_note}\n{text}\n{'─'*60}")
        _last_emit_text = text
        _record_emit_state(text)
        return True

    # Daily tweet cap
    if state.get_total_daily_tweets() >= config.DAILY_TWEET_CAP:
        logger.warning("Daily tweet cap (%d) reached — skipping [%s].",
                       config.DAILY_TWEET_CAP, tweet_type)
        return False

    if not bypass_guard and _is_quiet_hours():
        logger.info("Quiet hours — skipping: %.60s", text)
        return False

    if _is_duplicate_content(text):
        logger.info("Skipping — duplicate content: %.60s", text)
        return False

    if ai_writer._is_too_similar(text):
        logger.info("Skipping — too similar to recent tweet (persisted check): %.60s", text)
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
    if _last_emit_time > 0 and (now - _last_emit_time) < _MIN_TWEET_GAP:
        mins_left = int((_MIN_TWEET_GAP - (now - _last_emit_time)) / 60)
        logger.info("Skipping — min gap (%dm left): %.60s", mins_left, text)
        return False

    # Image: use pre-fetched media_path if provided; otherwise try image_generator
    # (never use image_generator for news — OG images come via media_path instead)
    img_path = media_path
    if img_path is None and tweet_type != "news":
        if random.random() < _IMAGE_ODDS.get(tweet_type, 0.0):
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
        state.increment_daily_count(tweet_type)
        ai_writer.record_recent_tweet(text)
        _record_emit_state(text)
        logger.info("Posted [%s]: %.80s", tweet_type, text)

    if img_path and img_path is not media_path:
        # Only unlink images we generated ourselves; caller-provided are cleaned up here too
        pass
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
    if state.get_daily_count("price_alert") >= config.PRICE_ALERT_DAILY_CAP:
        logger.debug("Price alert daily cap reached — skipping check.")
        return
    logger.info("Running price check…")
    alerts = price_monitor.check_prices()
    if not alerts:
        logger.info("No significant price moves.")
        return
    for alert in alerts:
        if state.get_daily_count("price_alert") >= config.PRICE_ALERT_DAILY_CAP:
            logger.info("Price alert daily cap (%d) reached.", config.PRICE_ALERT_DAILY_CAP)
            break
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


_last_news_emit_time: float = 0.0


def run_news_check() -> None:
    global _last_news_emit_time
    if state.get_daily_count("news") >= config.NEWS_DAILY_CAP:
        logger.debug("News daily cap reached — skipping check.")
        return
    now = time.time()
    if _last_news_emit_time > 0 and (now - _last_news_emit_time) < config.NEWS_COOLDOWN_SECS:
        mins_left = int((config.NEWS_COOLDOWN_SECS - (now - _last_news_emit_time)) / 60)
        logger.debug("News cooldown — %dm left.", mins_left)
        return
    logger.info("Running news check…")
    stories = news_monitor.check_news()
    if not stories:
        logger.info("No new stories.")
        return
    for story in stories[:1]:   # max 1 per check-cycle (cap enforced across day)
        if state.get_daily_count("news") >= config.NEWS_DAILY_CAP:
            logger.info("News daily cap (%d) reached.", config.NEWS_DAILY_CAP)
            break
        # Score + generate commentary if not already done
        scored = news_monitor._ai_score_and_comment(story) if "score" not in story else story
        if not scored:
            continue
        tweet = news_monitor.format_news_tweet(scored)
        if not tweet:
            continue
        # Generate branded news card; fall back to article OG image
        card_type = news_monitor.get_news_card_type(scored)
        img_path: str | None = None
        try:
            img_path = chart_generator.generate_news_card(
                headline=scored.get("title", ""),
                subtitle=scored.get("source", ""),
                card_type=card_type,
            )
        except Exception as exc:
            logger.warning("News card generation failed: %s — trying OG image", exc)
        if not img_path:
            img_path = news_monitor.fetch_og_image(scored.get("url", ""))
        logger.info("News (score %d, %s): %.80s",
                    scored.get("score", 0), card_type, scored.get("title", ""))
        posted = _emit(tweet, tweet_type="news", media_path=img_path)
        if posted:
            _last_news_emit_time = time.time()
        time.sleep(3)


_BOT_START_TIME: float = 0.0      # set in main() before entering the loop
_last_trending_run: float = 0.0   # set in main(); guards 2h min gap between trending runs


def run_trending_check() -> None:
    """Post about a trending coin outside our main watchlist. Max 1/day.

    Rate-limited to at most once per 2 hours by comparing wall-clock time
    against _last_trending_run (initialised to time.time() in main so the
    first window starts at startup, not the Unix epoch).
    """
    global _last_trending_run
    if state.get_daily_count("trending") >= config.TRENDING_DAILY_CAP:
        logger.debug("Trending daily cap reached — skipping check.")
        return
    now = time.time()
    if now - _last_trending_run < 7200:
        logger.debug(
            "Trending rate limit — %.0fm elapsed since last run (min 120m).",
            (now - _last_trending_run) / 60,
        )
        return
    _last_trending_run = now
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


def run_quote_tweet() -> None:
    """Market analysis tweet via tweet_generators (max 1/day)."""
    if state.get_daily_count("quote") >= config.QUOTE_TWEET_DAILY_CAP:
        logger.info("Quote tweet daily cap (%d) reached.", config.QUOTE_TWEET_DAILY_CAP)
        return
    tweet = tweet_generators.generate_quote_tweet()
    if tweet:
        _emit(tweet, tweet_type="quote")


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
    if not _should_fire("evening_thread", 19):
        return
    global _thread_topic_index
    topic = _evening_thread_topics[_thread_topic_index % len(_evening_thread_topics)]
    _thread_topic_index += 1
    logger.info("Running evening thread (19:00): %s", topic)
    tweets = ai_writer.generate_thread(topic, n_tweets=3)
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
            state.increment_daily_count("evening_thread", len(tweets))
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
_schedule_configured: bool = False


def setup_schedule() -> None:
    global _schedule_configured
    if _schedule_configured:
        logger.warning("setup_schedule() called more than once — ignoring duplicate.")
        return
    _schedule_configured = True

    # Interval-driven jobs — each wrapped in _safe so one failure can't kill the loop
    _scheduler.every(5).minutes.do(_safe(run_price_check))
    _scheduler.every(15).minutes.do(_safe(run_news_check))
    _scheduler.every(2).hours.do(_safe(run_trending_check))
    _scheduler.every(2).hours.do(_safe(run_quote_tweet))

    # Time-of-day jobs (checked every minute; _should_fire enforces once/day)
    _scheduler.every(1).minutes.do(_safe(run_morning_recap))
    _scheduler.every(1).minutes.do(_safe(run_opinion_tweet))
    _scheduler.every(1).minutes.do(_safe(run_engagement_tweet))
    _scheduler.every(1).minutes.do(_safe(run_evening_thread))
    _scheduler.every(1).minutes.do(_safe(run_fear_greed_tweet))

    logger.info(
        "Scheduled: price/5m (max 3/day) | news/15m (max 4/day, 60m cooldown) | "
        "trending/2h (max 1/day) | quote/2h (max 1/day) | "
        "08:00 recap | 12:00 opinion | 16:00 engagement | "
        "19:00 thread (3 tweets) | 21:00 fear-greed  (UK time)"
    )


# ── Graceful shutdown ─────────────────────────────────────────────────────────
def _shutdown(signum, frame):  # noqa: ARG001
    logger.info("Received signal %d – shutting down.", signum)
    sys.exit(0)


signal.signal(signal.SIGTERM, _shutdown)
signal.signal(signal.SIGINT,  _shutdown)



# ── Main ──────────────────────────────────────────────────────────────────────
def main() -> None:
    global DRY_RUN, _BOT_START_TIME, _last_trending_run

    # ── Single-instance guard (pid file) ───────────────────────────────────────
    _LOCK_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "bot.pid")
    if os.path.exists(_LOCK_FILE):
        with open(_LOCK_FILE) as f:
            old_pid = f.read().strip()
        if old_pid:
            try:
                os.kill(int(old_pid), 0)
                print(f"Already running (PID {old_pid}). Exiting.")
                sys.exit(1)
            except (ProcessLookupError, ValueError):
                pass
    with open(_LOCK_FILE, "w") as f:
        f.write(str(os.getpid()))
    import atexit
    atexit.register(lambda: os.unlink(_LOCK_FILE) if os.path.exists(_LOCK_FILE) else None)

    parser = argparse.ArgumentParser(description="Crypto News Twitter Bot")
    parser.add_argument("--dry-run", action="store_true",
                        help="Print tweets instead of posting")
    args = parser.parse_args()
    DRY_RUN = args.dry_run
    _BOT_START_TIME = time.time()
    _last_trending_run = _BOT_START_TIME  # first trending window starts now (2h grace)

    # ── Logging setup ──────────────────────────────────────────────────────────
    # Always write to the log file.
    # Only attach a StreamHandler when stdout is an interactive terminal OR in
    # dry-run mode, so that `nohup python3 bot.py >> bot.log 2>&1` does
    # not write every line twice (once from FileHandler, once from the stdout
    # redirect hitting the same file).
    _fmt = "%(asctime)s  %(levelname)-8s  %(name)s  %(message)s"
    _handlers: list[logging.Handler] = [logging.FileHandler(config.LOG_FILE)]
    if DRY_RUN or sys.stdout.isatty():
        _handlers.append(logging.StreamHandler(sys.stdout))
    logging.basicConfig(level=logging.INFO, format=_fmt, handlers=_handlers)

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
    logger.info("Scheduler: %d jobs registered (expected 9).", len(_scheduler.jobs))

    # Immediate startup checks — _scheduler.every() fires AFTER the interval,
    # so these are the only same-cycle executions (no duplicate firing).
    try:
        run_price_check()
    except Exception:
        logger.exception("Startup price check raised — continuing.")

    try:
        run_news_check()
    except Exception:
        logger.exception("Startup news check raised — continuing.")

    logger.info("Entering main loop (Ctrl-C or SIGTERM to stop).")
    try:
        while True:
            try:
                _scheduler.run_pending()
            except Exception:
                logger.exception("Unexpected error inside run_pending — continuing.")
            time.sleep(10)
    except Exception:
        logger.exception("Fatal unhandled exception — bot is stopping.")
        sys.exit(1)


if __name__ == "__main__":
    main()
