#!/usr/bin/env python3
"""
Crypto News Twitter Bot – main entry point.

Runs recurring jobs on a schedule:
  • Price monitor       – every 10 minutes
  • News monitor        – every 10 minutes
  • Quote tweets        – every 3 hours (cap 4/day, AI-generated)
  • Auto-replies        – every 30 min (cap 10/day, AI-powered)
  • Morning recap       – daily at 08:00 UK
  • Fear & Greed Index  – daily at 09:00 & 21:00 UK
  • Engagement tweet    – daily at 10:00 UK
  • Chart tweet         – daily at 11:00 UK (price chart image)
  • Opinion tweet       – daily at 12:00 UK
  • DeFi tweet          – daily at 13:00 UK (TVL data from DefiLlama)
  • Analysis thread     – daily at 18:00 UK (3-tweet deep dive)
  • Polymarket scan     – every 60 minutes
  • Polymarket daily    – daily at 15:00 UK
  • Liquidation data    – every hour
  • Breakout alerts     – every 5 minutes
  • Trending coins      – every 2 hours (outside watchlist movers)
  • Event calendar      – every hour (token unlocks + FOMC/CPI)
  • Whale monitor       – every hour (large BTC/ETH transactions)
  • Whale wallet tracker – every 15 min (Etherscan, cap 4/day)
  • Weekly recap thread – every Sunday at 17:00 UK
  • Engagement check    – every hour
  • Follower tracking   – daily at 07:00 UK
  • Reply analysis      – every 2 hours (audience sentiment)
  • Reply-back          – every 30 min (cap 5/day, respond to replies on our tweets)
  • Quiet hours         – no tweets 11pm-7am UK

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

import requests
import schedule

from datetime import datetime, timezone, timedelta

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
import cmc_monitor
import ai_writer
import fear_greed
import liquidation_monitor
import breakout_monitor
import trending_monitor
import follower_tracker
import event_calendar
import defi_monitor
import whale_monitor
import reply_analyzer
import chart_generator
import reply_back
import whale_wallet_tracker

_PID_FILE = os.path.join(os.path.dirname(__file__), "bot.pid")
_STARTUP_COOLDOWN = 300  # seconds — skip immediate tweets if last run was <5 min ago

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


_last_emit_time: float = 0
_last_emit_text: str = ""
_MIN_TWEET_GAP = 420  # minimum 7 minutes between any two tweets (frequent but not spammy)
_type_last_emit: dict[str, float] = {}  # per-type cooldown timestamps
_TYPE_COOLDOWN = 1200  # 20 min minimum between tweets of the same type


def _is_quiet_hours() -> bool:
    """Return True if current UK time is within quiet hours (no tweeting)."""
    return config.is_quiet_hours()


def _is_duplicate_content(new_text: str) -> bool:
    """Check if new tweet is too similar to the last emitted tweet."""
    if not _last_emit_text:
        return False
    new_words = set(new_text.lower().split())
    old_words = set(_last_emit_text.lower().split())
    if not new_words or not old_words:
        return False
    overlap = len(new_words & old_words) / max(len(new_words), len(old_words))
    if overlap > 0.5:
        return True
    # Also block if both tweets start with same coin reference
    new_start = new_text[:30].lower()
    old_start = _last_emit_text[:30].lower()
    btc_markers = ("btc", "bitcoin", "$btc")
    if any(m in new_start for m in btc_markers) and any(m in old_start for m in btc_markers):
        return True
    return False


def _extract_coin_symbols(text: str) -> list[str]:
    """Extract coin symbols mentioned in tweet text by matching against config.COINS."""
    upper_text = text.upper()
    # Build a reverse lookup: symbol -> symbol (and also match coingecko id words)
    found = []
    for cg_id, symbol in config.COINS.items():
        # Match symbol (e.g. "BTC", "ETH") as a whole word
        import re
        if re.search(r'\b' + re.escape(symbol) + r'\b', upper_text):
            found.append(symbol)
        # Also match full names like "Bitcoin", "Ethereum"
        elif cg_id.replace("-", " ").lower() in text.lower():
            found.append(symbol)
    return found


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


# ── Jobs ─────────────────────────────────────────────────────────────────────

def run_price_check() -> None:
    logger.info("Running price check…")
    alerts = price_monitor.check_prices()
    if not alerts:
        logger.info("No significant price moves detected.")
        return
    # Only post the single most significant alert per check to avoid spam
    best = max(alerts, key=lambda a: abs(a["pct_change"]))
    tweet = price_monitor.format_price_tweet(best)
    logger.info(
        "Price alert: %s %+.1f%% (%s)",
        best["symbol"], best["pct_change"], best["window"],
    )
    _emit(tweet)


def run_news_check() -> None:
    logger.info("Running news check…")
    stories = news_monitor.check_news()
    if not stories:
        logger.info("No new important stories.")
        return
    # Post top stories (check_news already caps at 2)
    for story in stories[:2]:
        tweet = news_monitor.format_news_tweet(story)
        score = story.get("score", "?")
        logger.info("News story (score %s/10): %.80s", score, story.get("title", ""))
        _emit(tweet)


def run_quote_tweet() -> None:
    if not tweet_generators.can_quote_tweet():
        logger.info("Quote tweet daily cap (%d) reached.", config.QUOTE_TWEET_DAILY_CAP)
        return
    logger.info("Generating quote tweet…")
    # Check recently mentioned coins and nudge AI to avoid them
    recent_coins = state.get_recently_mentioned_coins(hours=2)
    if recent_coins:
        avoid_hint = ", ".join(sorted(recent_coins))
        logger.info("Quote tweet: deprioritizing recently mentioned coins: %s", avoid_hint)
        ai_writer.set_coins_to_avoid(recent_coins)
    tweet = tweet_generators.generate_quote_tweet()
    # Clear the avoidance hint after generation
    ai_writer.set_coins_to_avoid(set())
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
    for attempt in range(3):
        try:
            count = auto_replier.find_and_reply()
            logger.info("Auto-replied to %d tweets.", count)
            return
        except ConnectionError as exc:
            wait = 2 ** (attempt + 1)
            logger.warning("Auto-reply connection error (attempt %d/3): %s — retrying in %ds", attempt + 1, exc, wait)
            time.sleep(wait)
        except Exception as exc:
            logger.error("Auto-reply error: %s", exc)
            return
    logger.error("Auto-reply failed after 3 connection retries.")


_last_morning_recap_date: str = ""

def run_morning_recap() -> None:
    global _last_morning_recap_date
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    if _last_morning_recap_date == today:
        logger.debug("Morning recap already posted today — skipping.")
        return
    logger.info("Generating morning recap…")
    try:
        tweet = tweet_generators.generate_morning_recap()
        if tweet:
            _emit(tweet, "morning_recap")
            _last_morning_recap_date = today
        else:
            logger.info("Could not generate morning recap (no data).")
    except Exception as exc:
        logger.error("Morning recap error: %s", exc)


def run_opinion_tweet() -> None:
    logger.info("Generating opinion tweet…")
    try:
        tweet = tweet_generators.generate_opinion_tweet()
        if tweet:
            _emit(tweet, "opinion")
        else:
            logger.info("Could not generate opinion tweet (no data).")
    except Exception as exc:
        logger.error("Opinion tweet error: %s", exc)


def run_polymarket_scan() -> None:
    logger.debug("Running polymarket scan…")
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
            _emit(tweet, "engagement")
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


def run_cmc_check() -> None:
    """Fetch CoinMarketCap data and tweet about interesting movers."""
    if not cmc_monitor.is_available():
        return
    logger.info("Running CoinMarketCap check…")
    try:
        coins = cmc_monitor.fetch_top_coins()
        if not coins:
            logger.info("No CMC data available.")
            return

        # Check for big movers first — these are the most interesting
        big_movers = cmc_monitor.get_big_movers(coins)
        if big_movers:
            # Filter out coins already tweeted about in the last 2 hours
            recent_coins = state.get_recently_mentioned_coins(hours=2)
            filtered_movers = [
                m for m in big_movers
                if m.get("symbol", "").upper() not in recent_coins
            ]
            mover = filtered_movers[0] if filtered_movers else None
            if not mover:
                logger.info("CMC big movers all recently mentioned — skipping spotlight.")
            else:
                tweet = cmc_monitor.format_spotlight_tweet(mover)
                if tweet:
                    _emit(tweet, "cmc_spotlight")
                    return  # one tweet per check is enough

        # Otherwise, post a market breadth or top movers tweet (alternating)
        import random
        if random.random() < 0.5:
            tweet = cmc_monitor.format_breadth_tweet(coins)
        else:
            tweet = cmc_monitor.format_movers_tweet(coins)

        if tweet:
            _emit(tweet, "cmc_overview")
    except Exception as exc:
        logger.error("CMC check error: %s", exc)


def run_fear_greed() -> None:
    """Post the Fear & Greed Index reading."""
    logger.info("Fetching Fear & Greed Index…")
    try:
        data = fear_greed.fetch_fear_greed()
        if not data:
            logger.info("No Fear & Greed data available.")
            return
        if not fear_greed.should_post(data):
            logger.info("Fear & Greed skipped (cooldown or unchanged, value=%d).", data["value"])
            return
        tweet = fear_greed.format_fear_greed_tweet(data)
        if tweet:
            _emit(tweet, "fear_greed")
            fear_greed.record_posted(data)
            logger.info("Fear & Greed posted: %d (%s)", data["value"], data["classification"])
    except Exception as exc:
        logger.error("Fear & Greed error: %s", exc)


_last_liquidation_date: str = ""
_liquidation_today: int = 0
_LIQUIDATION_DAILY_CAP = 2  # max 2 liquidation tweets per day

def run_liquidation_check() -> None:
    """Check liquidation/derivatives data and tweet if significant."""
    global _last_liquidation_date, _liquidation_today
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    if _last_liquidation_date != today:
        _last_liquidation_date = today
        _liquidation_today = 0
    if _liquidation_today >= _LIQUIDATION_DAILY_CAP:
        logger.debug("Liquidation daily cap (%d) reached.", _LIQUIDATION_DAILY_CAP)
        return
    logger.info("Checking liquidation data…")
    try:
        data = liquidation_monitor.fetch_liquidation_data()
        if not data:
            logger.info("No liquidation data available.")
            return
        tweet = liquidation_monitor.format_liquidation_tweet(data)
        if tweet:
            _emit(tweet, "liquidation")
            _liquidation_today += 1
    except Exception as exc:
        logger.error("Liquidation check error: %s", exc)


def run_breakout_check() -> None:
    """Check for key level breakouts — post at most 1 per check, with chart."""
    try:
        alerts = breakout_monitor.check_breakouts()
        if alerts:
            alert = alerts[0]  # most important breakout only
            tweet = breakout_monitor.format_breakout_tweet(alert)
            if tweet:
                logger.info(
                    "Breakout alert: %s %s $%s",
                    alert["symbol"], alert["direction"], alert["level"],
                )
                # Try to attach a chart image
                chart_path = None
                if not DRY_RUN:
                    try:
                        chart_path = chart_generator.generate_price_chart(
                            alert["coin_id"], alert["symbol"], days=7
                        )
                    except Exception as chart_exc:
                        logger.debug("Chart generation failed for breakout: %s", chart_exc)
                if chart_path and not DRY_RUN:
                    twitter_client.post_tweet_with_media(tweet, chart_path)
                else:
                    _emit(tweet, "breakout")
    except Exception as exc:
        logger.error("Breakout check error: %s", exc)


def run_weekly_recap() -> None:
    """Post the Sunday weekly market recap thread."""
    logger.info("Generating weekly recap thread…")
    try:
        thread_poster.post_weekly_recap(dry_run=DRY_RUN)
    except Exception as exc:
        logger.error("Weekly recap error: %s", exc)


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


def run_trending_check() -> None:
    """Check for trending coins outside our watchlist."""
    logger.info("Checking trending coins…")
    try:
        alerts = trending_monitor.check_trending()
        if not alerts:
            logger.info("No new trending coins.")
            return
        alert = alerts[0]  # one trending tweet per check
        tweet = trending_monitor.format_trending_tweet(alert)
        if tweet:
            logger.info("Trending coin: %s (%s)", alert["symbol"], alert["source"])
            _emit(tweet, "trending")
    except Exception as exc:
        logger.error("Trending check error: %s", exc)


def run_event_check() -> None:
    """Check for upcoming token unlocks and macro events."""
    logger.info("Checking event calendar…")
    try:
        events = event_calendar.check_events()
        if not events:
            return
        event = events[0]  # one event tweet per check
        tweet = event_calendar.format_event_tweet(event)
        if tweet:
            logger.info("Event alert: %s", event.get("event_key", ""))
            _emit(tweet, "event")
    except Exception as exc:
        logger.error("Event check error: %s", exc)


def run_defi_tweet() -> None:
    """Generate a DeFi-focused tweet using TVL data."""
    logger.info("Generating DeFi tweet…")
    try:
        tweet = defi_monitor.generate_defi_tweet()
        if tweet:
            _emit(tweet, "defi")
        else:
            logger.info("No DeFi data available for tweet.")
    except Exception as exc:
        logger.error("DeFi tweet error: %s", exc)


def run_whale_check() -> None:
    """Check for whale transactions."""
    try:
        alerts = whale_monitor.check_whale_activity()
        if not alerts:
            return
        alert = alerts[0]
        tweet = whale_monitor.format_whale_tweet(alert)
        if tweet:
            logger.info("Whale alert: %s %s", alert["symbol"],
                        f"${alert.get('value_usd', 0) / 1e6:.0f}M")
            _emit(tweet, "whale")
    except Exception as exc:
        logger.error("Whale check error: %s", exc)


def run_whale_wallet_check() -> None:
    """Check tracked whale wallets for large movements."""
    if DRY_RUN:
        return
    if not config.ETHERSCAN_API_KEY:
        return
    logger.info("Checking whale wallets…")
    for attempt in range(3):
        try:
            tweets = whale_wallet_tracker.check_whale_wallets()
            for tweet in tweets:
                _emit(tweet, "whale_wallet")
            if tweets:
                logger.info("Whale wallet alerts: posted %d tweets.", len(tweets))
            return
        except (requests.ConnectionError, requests.Timeout) as exc:
            wait = 2 ** (attempt + 1)
            logger.warning("Whale wallet connection error (attempt %d/3): %s — retrying in %ds", attempt + 1, exc, wait)
            time.sleep(wait)
        except Exception as exc:
            logger.error("Whale wallet check error: %s", exc)
            return
    logger.error("Whale wallet check failed after 3 connection retries.")


def run_follower_check() -> None:
    """Record daily follower count with weekly comparison."""
    if DRY_RUN:
        return
    try:
        client = twitter_client.get_client()
        result = follower_tracker.record_count(client)
        if result:
            logger.info("Followers: %d (%+d today)", result["count"], result["change"])
            summary = follower_tracker.get_growth_summary()
            if summary:
                logger.info("7d growth: %+d (%+.1f%%) | 30d growth: %+d (%+.1f%%)",
                            summary.get("weekly_change", 0),
                            summary.get("weekly_pct", 0),
                            summary.get("monthly_change", 0),
                            summary.get("monthly_pct", 0))
                if summary.get("best_day"):
                    logger.info("Best day: %s (%+d) | Worst day: %s (%+d)",
                                summary["best_day"].get("date", "?"),
                                summary["best_day"].get("change", 0),
                                summary.get("worst_day", {}).get("date", "?"),
                                summary.get("worst_day", {}).get("change", 0))
    except Exception as exc:
        logger.error("Follower check error: %s", exc)


def run_reply_analysis() -> None:
    """Analyze sentiment of replies to recent tweets."""
    if DRY_RUN:
        return
    try:
        client = twitter_client.get_client()
        # Get recent tweet IDs from engagement tracker
        recent = engagement_tracker._data.get("tweets", [])
        recent_ids = [t["id"] for t in recent[-20:] if t.get("id")]
        if recent_ids:
            count = reply_analyzer.analyze_recent_tweets(client, recent_ids)
            if count:
                logger.info("Analyzed replies for %d tweets", count)
                mood = reply_analyzer.get_audience_mood()
                if mood:
                    logger.info("Audience mood: %s", mood)
    except Exception as exc:
        logger.error("Reply analysis error: %s", exc)


def run_reply_back() -> None:
    """Check replies to our tweets and respond to quality ones."""
    if DRY_RUN:
        return
    logger.info("Running reply-back check…")
    for attempt in range(3):
        try:
            count = reply_back.check_and_reply()
            logger.info("Reply-back: responded to %d replies.", count)
            return
        except (requests.ConnectionError, requests.Timeout) as exc:
            wait = 2 ** (attempt + 1)
            logger.warning("Reply-back connection error (attempt %d/3): %s — retrying in %ds", attempt + 1, exc, wait)
            time.sleep(wait)
        except Exception as exc:
            logger.error("Reply-back error: %s", exc)
            return
    logger.error("Reply-back failed after 3 connection retries.")


def run_chart_tweet() -> None:
    """Generate and post a chart tweet for BTC or top mover."""
    logger.info("Generating chart tweet…")
    try:
        import random
        # 50/50: BTC chart or multi-coin comparison
        if random.random() < 0.5:
            chart_path = chart_generator.generate_price_chart("bitcoin", "BTC", days=7)
            if chart_path:
                tweet = "BTC 7-day chart. Structure speaks for itself."
                twitter_client.post_tweet_with_media(tweet, chart_path)
                logger.info("Posted BTC chart tweet")
        else:
            coins = [
                {"id": "bitcoin", "symbol": "BTC"},
                {"id": "ethereum", "symbol": "ETH"},
                {"id": "solana", "symbol": "SOL"},
            ]
            chart_path = chart_generator.generate_multi_coin_chart(coins, days=7)
            if chart_path:
                tweet = "BTC vs ETH vs SOL — 7-day performance side by side."
                twitter_client.post_tweet_with_media(tweet, chart_path)
                logger.info("Posted multi-coin chart tweet")
    except Exception as exc:
        logger.error("Chart tweet error: %s", exc)


# ── Scheduler setup ──────────────────────────────────────────────────────────

def setup_schedule() -> None:
    # Clear any previously registered jobs (prevents accumulation on restart)
    schedule.clear()

    # Recurring interval jobs
    schedule.every(config.PRICE_CHECK_INTERVAL).seconds.do(run_price_check)
    schedule.every(config.NEWS_CHECK_INTERVAL).seconds.do(run_news_check)
    schedule.every(config.QUOTE_TWEET_INTERVAL).seconds.do(run_quote_tweet)
    schedule.every(config.AUTO_REPLY_INTERVAL).seconds.do(run_auto_replies)
    schedule.every(config.POLYMARKET_CHECK_INTERVAL).seconds.do(run_polymarket_scan)

    # CoinMarketCap data (broader market coverage)
    if cmc_monitor.is_available():
        schedule.every(config.CMC_CHECK_INTERVAL).seconds.do(run_cmc_check)
        logger.info("CoinMarketCap monitor ON: checking every %ds", config.CMC_CHECK_INTERVAL)
    else:
        logger.info(
            "CoinMarketCap disabled (no CMC_API_KEY). "
            "Get a free key at https://coinmarketcap.com/api/"
        )

    # Engagement tracking
    schedule.every(config.ENGAGEMENT_CHECK_INTERVAL).seconds.do(run_engagement_check)

    # Fear & Greed Index (twice daily)
    schedule.every().day.at(config.FEAR_GREED_TIME_1).do(run_fear_greed)
    schedule.every().day.at(config.FEAR_GREED_TIME_2).do(run_fear_greed)
    logger.info(
        "Fear & Greed Index: posting at %s & %s UK",
        config.FEAR_GREED_TIME_1, config.FEAR_GREED_TIME_2,
    )

    # Liquidation / derivatives data
    schedule.every(config.LIQUIDATION_CHECK_INTERVAL).seconds.do(run_liquidation_check)
    logger.info("Liquidation monitor ON: checking every %ds", config.LIQUIDATION_CHECK_INTERVAL)

    # Breakout alerts (key level crossings)
    schedule.every(config.BREAKOUT_CHECK_INTERVAL).seconds.do(run_breakout_check)
    logger.info("Breakout monitor ON: checking every %ds", config.BREAKOUT_CHECK_INTERVAL)

    # Daily scheduled tweets (UK time)
    schedule.every().day.at(config.MORNING_RECAP_TIME).do(run_morning_recap)
    schedule.every().day.at(config.OPINION_TWEET_TIME).do(run_opinion_tweet)
    schedule.every().day.at(config.ENGAGEMENT_TWEET_TIME).do(run_engagement_tweet)
    schedule.every().day.at(config.ENGAGEMENT_TWEET_TIME_2).do(run_engagement_tweet)
    schedule.every().day.at(config.THREAD_TIME).do(run_thread)
    schedule.every().day.at(config.THREAD_TIME_2).do(run_thread)
    schedule.every().day.at(config.POLYMARKET_DAILY_TIME).do(run_polymarket_daily)

    # Weekly Sunday recap thread
    schedule.every().sunday.at(config.WEEKLY_RECAP_TIME).do(run_weekly_recap)
    logger.info("Weekly recap thread: every Sunday at %s UK", config.WEEKLY_RECAP_TIME)

    # Growth engine jobs
    if config.GROWTH_ENABLED:
        if config.INFLUENCER_MENTIONS:
            schedule.every().day.at(config.INFLUENCER_CALLOUT_TIME).do(run_influencer_callout)
            logger.info("Influencer callout ON: daily at %s UK", config.INFLUENCER_CALLOUT_TIME)
        else:
            logger.info("Influencer mentions disabled.")
        schedule.every(config.CT_NARRATIVE_INTERVAL).seconds.do(run_ct_narrative)
        schedule.every().day.at(config.HOT_TAKE_TIME_1).do(run_hot_take)
        schedule.every().day.at(config.HOT_TAKE_TIME_2).do(run_hot_take)
        schedule.every().day.at(config.HOT_TAKE_TIME_3).do(run_hot_take)
        logger.info(
            "Growth engine ON: CT narrative every %ds "
            "(cap %d/day), hot takes at %s & %s UK",
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

    # ── New features ────────────────────────────────────────────────────────
    # Trending coin detection
    schedule.every(config.TRENDING_CHECK_INTERVAL).seconds.do(run_trending_check)
    logger.info("Trending monitor ON: checking every %ds", config.TRENDING_CHECK_INTERVAL)

    # Event calendar (token unlocks + macro events)
    schedule.every(config.EVENT_CHECK_INTERVAL).seconds.do(run_event_check)
    logger.info("Event calendar ON: checking every %ds", config.EVENT_CHECK_INTERVAL)

    # DeFi TVL tweet (daily)
    schedule.every().day.at(config.DEFI_TWEET_TIME).do(run_defi_tweet)
    logger.info("DeFi tweet: daily at %s UK", config.DEFI_TWEET_TIME)

    # Whale monitoring
    schedule.every(config.WHALE_CHECK_INTERVAL).seconds.do(run_whale_check)
    logger.info("Whale monitor ON: checking every %ds", config.WHALE_CHECK_INTERVAL)

    # Whale wallet tracker (Etherscan-based)
    if config.ETHERSCAN_API_KEY:
        schedule.every(config.WHALE_WALLET_CHECK_INTERVAL).seconds.do(run_whale_wallet_check)
        logger.info("Whale wallet tracker ON: every %ds (cap %d/day)", config.WHALE_WALLET_CHECK_INTERVAL, config.WHALE_WALLET_DAILY_CAP)
    else:
        logger.info("Whale wallet tracker OFF: set ETHERSCAN_API_KEY in .env to enable")

    # Chart tweet (daily)
    schedule.every().day.at(config.CHART_TWEET_TIME).do(run_chart_tweet)
    logger.info("Chart tweet: daily at %s UK", config.CHART_TWEET_TIME)

    # Follower tracking (daily)
    schedule.every().day.at(config.FOLLOWER_CHECK_TIME).do(run_follower_check)
    logger.info("Follower tracking: daily at %s UK", config.FOLLOWER_CHECK_TIME)

    # Reply sentiment analysis
    schedule.every(config.REPLY_ANALYSIS_INTERVAL).seconds.do(run_reply_analysis)
    logger.info("Reply analysis ON: every %ds", config.REPLY_ANALYSIS_INTERVAL)

    # Reply-back monitor (respond to replies on our tweets)
    schedule.every(config.REPLY_BACK_INTERVAL).seconds.do(run_reply_back)
    logger.info("Reply-back ON: every %ds (cap %d/day)", config.REPLY_BACK_INTERVAL, config.REPLY_BACK_DAILY_CAP)

    # Log webhook status
    wh = webhook_alerts.status()
    active = [k for k, v in wh.items() if v]
    if active:
        logger.info("Webhook alerts active: %s", ", ".join(active))
    else:
        logger.info("No webhook alerts configured (Discord/Telegram optional)")


# ── Graceful shutdown ────────────────────────────────────────────────────────

def _acquire_pid() -> None:
    """Write PID file, killing any stale previous instance."""
    if os.path.exists(_PID_FILE):
        try:
            old_pid = int(open(_PID_FILE).read().strip())
            if old_pid == os.getpid():
                pass  # same process
            else:
                # Check if the old process is actually running
                os.kill(old_pid, 0)
                logger.warning(
                    "Old instance still running (PID %d) — sending SIGTERM.",
                    old_pid,
                )
                os.kill(old_pid, signal.SIGTERM)
                # Give it a moment to shut down
                import time as _time
                _time.sleep(2)
                try:
                    os.kill(old_pid, 0)
                    # Still alive — force kill
                    logger.warning("Old instance didn't stop — sending SIGKILL.")
                    os.kill(old_pid, signal.SIGKILL)
                    _time.sleep(1)
                except OSError:
                    pass  # it's gone
                logger.info("Old instance stopped.")
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
    follower_tracker.load()
    reply_analyzer.load()

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

    # On startup, only post ONE tweet (a quote tweet) to show the feed is alive.
    # Price/news checks will run on their normal schedule within minutes.
    # This prevents the old behavior of dumping 3+ tweets on every restart.
    last_run = state.get_last_run_time()
    elapsed = time.time() - last_run if last_run else _STARTUP_COOLDOWN + 1
    if elapsed >= _STARTUP_COOLDOWN:
        logger.info("Startup: posting one quote tweet (last run %.0fs ago).", elapsed)
        run_quote_tweet()
        state.record_last_run_time()
    else:
        logger.info(
            "Skipping startup tweet — last run was only %.0fs ago (cooldown %ds).",
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
