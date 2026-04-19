#!/usr/bin/env python3
"""
Crypto News Twitter Bot – main entry point.

Per-type daily caps: news 8, trending 4, price alerts 3, quote 1.

SCHEDULED (UK/London time):
  • 08:00  morning_recap
  • 12:00  opinion
  • 16:00  engagement
  • 19:00  evening_thread  (3 tweets)
  • 21:00  fear_greed

INTERVAL-DRIVEN (with daily caps):
  • Price alerts  – every 5 min check  | max 3/day | 90 min cooldown | 5%+ 1h or 6%+ 24h move
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
import socket
import sys
import time
import requests
import requests.exceptions
from schedule import Scheduler as _Scheduler
from zoneinfo import ZoneInfo

import ai_writer
import chart_generator
import fear_greed
import config
import news_monitor
import price_monitor
import telegram_client
import bluesky_client
import state
import twitter_client
import tweet_generators
import trending_monitor
import volume_anomaly_scanner
import reply_engine
import trend_spotter

_LONDON_TZ = ZoneInfo("Europe/London")

# Logger created at module level; handlers are attached in main() after arg
# parsing so we know whether stdout is being redirected.
logger = logging.getLogger("bot")

# ── Globals ───────────────────────────────────────────────────────────────────
DRY_RUN = False

# ── Quality gate — reject weak tweets before posting ─────────────────────────
_WEAK_TWEET_PATTERNS = [
    r"^market is\b",
    r"^the market\b",
    r"^things are\b",
    r"^it looks like\b",
    r"^we could see\b",
    r"^watch this\b",
    r"^keep an eye\b",
    r"^stay tuned\b",
    r"^not financial advice",
    r"^interesting to see",
    r"^worth noting",
]
_WEAK_TWEET_RE = [re.compile(p, re.IGNORECASE) for p in _WEAK_TWEET_PATTERNS]


def _quality_gate(text: str) -> tuple[bool, str]:
    """Score a tweet and reject if below quality threshold.

    Returns (passed, reason). Checks for:
    - Too short (under 40 chars)
    - Too generic (matches weak patterns)
    - No substance (just a vague statement)
    - Repeated words
    """
    stripped = text.strip()

    # Too short
    if len(stripped) < 40:
        return False, f"too short ({len(stripped)} chars)"

    # Weak opener patterns
    for pat in _WEAK_TWEET_RE:
        if pat.search(stripped):
            return False, f"weak opener: {pat.pattern}"

    # Too many repeated words (sign of AI rambling)
    words = stripped.lower().split()
    if len(words) > 5:
        unique = set(words)
        if len(unique) / len(words) < 0.4:
            return False, "too many repeated words"

    # No substance — all filler, no data
    filler_words = {"the", "is", "are", "was", "were", "a", "an", "and", "or",
                    "but", "in", "on", "at", "to", "for", "of", "it", "this",
                    "that", "with", "not", "be", "has", "have", "do", "does"}
    content_words = [w for w in words if w not in filler_words and len(w) > 2]
    if len(content_words) < 3:
        return False, "no substance — too few content words"

    return True, "ok"


# ── Time-aware market session context ────────────────────────────────────────

def _get_session_context() -> str:
    """Return a short market session label based on current UK time.

    Used to give tweets a 'live trader watching screens' feel.
    """
    now_uk = datetime.datetime.now(_LONDON_TZ)
    h = now_uk.hour

    if 0 <= h < 2:
        return "Late US session. Liquidity thinning."
    elif 2 <= h < 4:
        return "Dead zone between US close and Asia open."
    elif 4 <= h < 7:
        return "Asian session driving the move."
    elif 7 <= h < 8:
        return "Pre-London. Smart money positioning."
    elif 8 <= h < 9:
        return "London open. Volume arriving."
    elif 9 <= h < 12:
        return "London session active. European flow dominating."
    elif 12 <= h < 13:
        return "London/NY overlap approaching. Peak liquidity window."
    elif 13 <= h < 14:
        return "US pre-market. Futures setting the tone."
    elif 14 <= h < 16:
        return "US market open. Maximum volume."
    elif 16 <= h < 18:
        return "US session peak. Institutional flow heaviest."
    elif 18 <= h < 20:
        return "US afternoon. Momentum fading or accelerating."
    elif 20 <= h < 22:
        return "US closing. Watch for end-of-day positioning."
    else:
        return "After-hours. Thin liquidity, bigger moves."


# ── Posting guards ────────────────────────────────────────────────────────────
_QUIET_HOURS_START = 0   # midnight UK
_QUIET_HOURS_END   = 7   # 7am UK

_MIN_TWEET_GAP = 360     # 6 min minimum between any two posts (volume-tuned)
_TYPE_COOLDOWN = 3600    # 1 hour between same tweet type

# ── Anti-ban: random jitter before posting ───────────────────────────────────
_JITTER_MIN = 60         # 1 min minimum random delay
_JITTER_MAX = 300        # 5 min maximum random delay

# ── Tweet structure validation ───────────────────────────────────────────────
_MAX_STRUCT_RETRIES = 3
_BANNED_TWEET_PATTERNS = [
    r"risk[- ]?off",
    r"risk[- ]?on",
    r"weak participation",
    r"conviction is missing",
    r"sentiment",
    r"uncertainty",
    r"mixed signals",
]
_TOPIC_COOLDOWN_SECS = 7200  # 2 hours per topic group

_last_emit_time: float = 0.0
_last_emit_text: str = ""
_type_last_emit: dict[str, float] = {}
_recent_tweet_types: list[str] = []  # last 3 tweet types for variety enforcement
_recent_emit_texts: list[str] = []   # last 10 tweets for content dedup

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
    """True if >40% word overlap with any of the last 10 posts."""
    a = set(text.lower().split())
    if not a:
        return False
    for prev in _recent_emit_texts:
        b = set(prev.lower().split())
        if not b:
            continue
        overlap = len(a & b) / max(len(a), len(b))
        if overlap > 0.40:
            logger.info("Duplicate content blocked: %.1f%% overlap with recent tweet", overlap * 100)
            return True
    return False


def _is_duplicate_topic(topics: set[str]) -> bool:
    """True if >=2 shared topic keywords with any of the last 4 posts."""
    if not topics:
        return False
    for prev in _last_2_emit_topics[-4:]:
        if len(topics & prev) >= 2:
            logger.debug("Duplicate topic: shared %s with recent post",
                         topics & prev)
            return True
    return False


def _check_topic_cooldown(text: str) -> tuple[bool, str]:
    """Return (blocked, reason) if a topic group is in its 2-hour window."""
    now = time.monotonic()
    words = _extract_topics(text)
    for group, keywords in _TOPIC_GROUPS.items():
        if words & keywords:
            last = _topic_group_last_post.get(group, 0.0)
            if last > 0 and (now - last) < _TOPIC_COOLDOWN_SECS:
                remaining = int((_TOPIC_COOLDOWN_SECS - (now - last)) / 60)
                return True, f"{group} cooldown ({remaining}m left)"
    return False, ""


def _record_emit_state(text: str) -> None:
    """Update topic history, content history, and group cooldown timestamps."""
    global _last_2_emit_topics
    topics = _extract_topics(text)
    _last_2_emit_topics.append(topics)
    if len(_last_2_emit_topics) > 6:
        _last_2_emit_topics = _last_2_emit_topics[-6:]
    _recent_emit_texts.append(text)
    if len(_recent_emit_texts) > 10:
        _recent_emit_texts[:] = _recent_emit_texts[-10:]
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
    """Wrap a scheduled job so any unhandled exception is logged, not fatal.

    Tracks consecutive failures per job and fires a Telegram alert after 3 in
    a row, so silent-skip loops surface instead of vanishing into the log.
    """
    @functools.wraps(fn)
    def _wrapper():
        try:
            result = fn()
            _safe_failure_counts.pop(fn.__name__, None)
            return result
        except Exception:
            logger.exception(
                "Unhandled exception in scheduled job '%s' — job skipped, bot continues.",
                fn.__name__,
            )
            count = _safe_failure_counts.get(fn.__name__, 0) + 1
            _safe_failure_counts[fn.__name__] = count
            last_alert = _safe_last_alert.get(fn.__name__, 0.0)
            if count >= _SAFE_FAIL_THRESHOLD and (time.time() - last_alert) > _SAFE_ALERT_COOLDOWN:
                _safe_last_alert[fn.__name__] = time.time()
                try:
                    # Internal alert — route to private chat if configured so
                    # subscribers don't see repeated-failure warnings.
                    alert_chat = config.TELEGRAM_ALERT_CHAT_ID or None
                    telegram_client.send_telegram(
                        f"\u26a0\ufe0f CryptoVault alert\n\n"
                        f"Job `{fn.__name__}` has failed {count} times in a row. "
                        f"Check bot.log for traceback.",
                        chat_id=alert_chat,
                    )
                except Exception as alert_exc:
                    logger.warning("Failure-count Telegram alert failed: %s", alert_exc)
    return _wrapper


_safe_failure_counts: dict[str, int] = {}
_safe_last_alert: dict[str, float] = {}
_SAFE_FAIL_THRESHOLD = 3
_SAFE_ALERT_COOLDOWN = 3600  # 1h between alerts for the same job



_RETRY_EXCEPTIONS = (ConnectionError, socket.error, OSError, requests.exceptions.ConnectionError)


def _post_with_retry(text: str, image_path: str | None = None, retries: int = 3) -> bool:
    """Post a tweet, retrying on connection errors with a 10s backoff."""
    for attempt in range(1, retries + 1):
        try:
            return twitter_client.post_tweet(text, image_path=image_path)
        except _RETRY_EXCEPTIONS as exc:
            logger.warning("post_tweet connection error (attempt %d/%d): %s", attempt, retries, exc)
            if attempt < retries:
                time.sleep(10)
    return False


def _post_thread_with_retry(
    tweets: list[str],
    first_tweet_image_path: str | None = None,
    retries: int = 3,
) -> bool:
    """Post a thread, retrying on connection errors with a 10s backoff.

    On success, each tweet of the thread is also sent to the Telegram
    channel as a separate message (Telegram has no native thread concept).
    """
    posted = False
    for attempt in range(1, retries + 1):
        try:
            posted = twitter_client.post_thread(tweets, first_tweet_image_path=first_tweet_image_path)
            break
        except _RETRY_EXCEPTIONS as exc:
            logger.warning("post_thread connection error (attempt %d/%d): %s", attempt, retries, exc)
            if attempt < retries:
                time.sleep(10)
    if posted:
        for i, tweet in enumerate(tweets):
            try:
                img = first_tweet_image_path if i == 0 else None
                telegram_client.send_telegram(tweet, image_path=img)
            except Exception as exc:
                logger.warning("Telegram thread mirror failed on tweet %d (non-fatal): %s", i + 1, exc)
        try:
            bluesky_client.post_thread(tweets, first_image_path=first_tweet_image_path)
        except Exception as exc:
            logger.warning("Bluesky thread mirror failed (non-fatal): %s", exc)
    return posted


# ── Core emit ─────────────────────────────────────────────────────────────────
def _emit(
    text: str,
    tweet_type: str = "general",
    bypass_guard: bool = False,
    media_path: str | None = None,
    no_chart: bool = False,
) -> bool:
    """Post a tweet (or print in dry-run). Returns True if posted/printed.

    When no_chart=True, skip auto-generation of a default chart — used for
    tweets where any chart would be misleading (e.g. trending obscure coins
    where a BTC fallback chart would contradict the tweet content).
    """
    global _last_emit_time, _last_emit_text

    if not text or not text.strip():
        logger.warning("_emit called with empty text — skipping")
        return False

    if text and _last_emit_text and text.strip() == _last_emit_text.strip():
        logger.warning("Duplicate tweet blocked — identical to last post")
        return False

    # Nuclear filter: block AI reasoning artifacts and skip markers before anything else
    _NUCLEAR_BLOCK = ("SKIP", "---", "**Reasoning")
    for _pat in _NUCLEAR_BLOCK:
        if _pat in text:
            logger.warning(
                "_emit nuclear filter blocked [%s]: text contains %r — not posting",
                tweet_type, _pat,
            )
            return False

    # Quality gate — reject weak, generic, or substanceless tweets
    passed, reason = _quality_gate(text)
    if not passed:
        logger.warning("[QUALITY] Rejected [%s]: %s — %.60s", tweet_type, reason, text)
        return False

    # Strip any URLs that slipped through — analyst accounts don't post links
    import re as _re
    text = _re.sub(r'https?://\S+', '', text).strip()
    # Collapse triple+ line breaks to double max (keeps 3-line format clean)
    text = _re.sub(r'\n{3,}', '\n\n', text)
    # Trim truncated sentences — never post text that ends mid-sentence
    # Only trim if we keep at least 80% of the text (avoid destroying content)
    _ENDING_RE = _re.compile(r'.*[.!?)\"]', _re.DOTALL)
    m = _ENDING_RE.match(text)
    if m and len(m.group(0)) < len(text) and len(m.group(0)) > len(text) * 0.8:
        text = m.group(0).rstrip()

    # SAFETY: block AI refusal text from ever being posted
    _AI_REFUSAL = [
        "i need to", "i appreciate", "i cannot", "i'm unable",
        "as an ai", "my core directive", "conflicts with", "i must decline",
        "i can't generate", "i can't create", "against my guidelines",
        "i can't write", "i cannot write", "the instruction asks",
        "i'm not able to", "i won't be able", "i won't write",
        "this request", "i should not",
    ]
    if any(phrase in text.lower() for phrase in _AI_REFUSAL):
        logger.critical("[SAFETY] AI refusal leaked into tweet — BLOCKED: %.100s", text)
        return False

    if not state.can_tweet():
        logger.critical("Monthly tweet cap reached.")
        return False

    if DRY_RUN:
        img_note = f"  [image: {media_path}]" if media_path else ""
        print(f"\n{'─'*60}\n[DRY RUN] [{tweet_type}]{img_note}\n{text}\n{'─'*60}")
        _last_emit_text = text
        _last_emit_time = time.time()
        if tweet_type != "general":
            _type_last_emit[tweet_type] = _last_emit_time
        _recent_tweet_types.append(tweet_type)
        if len(_recent_tweet_types) > 3:
            _recent_tweet_types[:] = _recent_tweet_types[-3:]
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

    # Block consecutive tweets of the same type.
    # Exempt types that must always fire on schedule, and event-driven types
    # (news, price_alert) whose dedup is handled by topic/time cooldowns.
    _CONSECUTIVE_EXEMPT = frozenset({
        "morning_recap", "market_open", "engagement", "evening_thread", "fear_greed",
        "news", "price_alert",
    })
    if (
        _recent_tweet_types
        and _recent_tweet_types[-1] == tweet_type
        and tweet_type not in _CONSECUTIVE_EXEMPT
    ):
        logger.info("Skipping %s — same type as last tweet: %.60s", tweet_type, text)
        return False

    now = time.time()
    if _last_emit_time > 0 and (now - _last_emit_time) < _MIN_TWEET_GAP:
        mins_left = int((_MIN_TWEET_GAP - (now - _last_emit_time)) / 60)
        logger.info("Skipping — min gap (%dm left): %.60s", mins_left, text)
        return False

    # Anti-ban: random delay before posting to avoid robotic timing patterns
    jitter = random.randint(_JITTER_MIN, _JITTER_MAX)
    logger.info("[JITTER] Waiting %ds before posting [%s]", jitter, tweet_type)
    time.sleep(jitter)

    # Image: use pre-fetched media_path if provided; otherwise generate chart by type
    # Text-only variation for feed variety — not every tweet needs a chart
    _TEXT_ONLY_TYPES = frozenset({
        "opinion", "opinion_bomb", "engagement", "narrative", "hot_take",
    })
    img_path = media_path
    if img_path is None and no_chart:
        logger.info("[TEXT-ONLY] No chart for %s (caller requested no_chart)", tweet_type)
    elif img_path is None and tweet_type in _TEXT_ONLY_TYPES and random.random() < 0.50:
        logger.info("[TEXT-ONLY] No chart for %s (50%% text-only)", tweet_type)
        img_path = None
    elif img_path is None:
        try:
            img_path = _chart_for_tweet(text)
        except Exception as exc:
            logger.warning("Chart generation failed: %s", exc)

    posted = _post_with_retry(text, image_path=img_path)
    if posted:
        _last_emit_time = time.time()
        _last_emit_text = text
        if tweet_type != "general":
            _type_last_emit[tweet_type] = _last_emit_time
        _recent_tweet_types.append(tweet_type)
        if len(_recent_tweet_types) > 3:
            _recent_tweet_types[:] = _recent_tweet_types[-3:]
        state.record_tweet()
        state.increment_daily_count(tweet_type)
        ai_writer.record_recent_tweet(text)
        _record_emit_state(text)
        logger.info("Posted [%s]: %.80s", tweet_type, text)

        # Mirror to Telegram channel
        try:
            telegram_client.send_telegram(text, image_path=img_path)
        except Exception as exc:
            logger.warning("Telegram mirror failed (non-fatal): %s", exc)

        # Mirror to Bluesky
        try:
            bluesky_client.post_skeet(text, image_path=img_path)
        except Exception as exc:
            logger.warning("Bluesky mirror failed (non-fatal): %s", exc)

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


def _should_fire(slot: str, hour: int, *, minute: int | None = None) -> bool:
    now_uk = datetime.datetime.now(_LONDON_TZ)
    today = now_uk.date()
    if minute is not None:
        if now_uk.hour != hour or now_uk.minute != minute:
            logger.debug("_should_fire(%s): wrong time %02d:%02d (need %02d:%02d)",
                         slot, now_uk.hour, now_uk.minute, hour, minute)
            return False
    else:
        if now_uk.hour != hour:
            return False
    if _fired_today.get(slot) == today:
        logger.debug("_should_fire(%s): already fired in-memory for %s", slot, today)
        return False
    daily = state.get_daily_count(slot)
    if daily > 0:
        _fired_today[slot] = today
        logger.debug("_should_fire(%s): already fired in state (daily_count=%d) for %s",
                      slot, daily, today)
        return False
    # Immediately mark in-memory to prevent a second scheduler tick within
    # the same minute from passing the guard while the job is still running.
    _fired_today[slot] = today
    logger.info("_should_fire(%s): READY — hour=%d, marked in-memory to block duplicates", slot, hour)
    return True


def _mark_slot_fired(slot: str) -> None:
    """Mark a timed slot as fired for today (call after successful post).

    Persists to state file immediately so a duplicate check within the
    same minute (before the in-memory dict is consulted) is blocked.
    """
    _fired_today[slot] = datetime.datetime.now(_LONDON_TZ).date()
    if state.get_daily_count(slot) == 0:
        state.increment_daily_count(slot)


# ── Chart coin variety ────────────────────────────────────────────────────────
# For general tweets (opinion, engagement, quote), occasionally show a non-BTC
# chart so the feed doesn't look like a BTC-only account.
_CHART_COIN_CHOICES: list[tuple[str, str]] = [
    ("bitcoin", "BTC"),
    ("bitcoin", "BTC"),
    ("bitcoin", "BTC"),      # 60% BTC
    ("ethereum", "ETH"),
    ("solana", "SOL"),
]


def _pick_chart_coin() -> tuple[str, str]:
    """Return a (coin_id, symbol) for chart generation, weighted toward BTC."""
    return random.choice(_CHART_COIN_CHOICES)


_MACRO_RE = re.compile(
    r'\b(stablecoin|USDT|USDC|dollar|inflation|Fed|macro|treasury|DeFi|onchain)\b',
    re.IGNORECASE,
)


# ── Coin lookup for chart matching ────────────────────────────────────────────
# Maps keywords (uppercase) to (coingecko_id, symbol) for chart generation.
_COIN_CHART_MAP: dict[str, tuple[str, str]] = {
    "BTC": ("bitcoin", "BTC"), "BITCOIN": ("bitcoin", "BTC"),
    "ETH": ("ethereum", "ETH"), "ETHEREUM": ("ethereum", "ETH"),
    "SOL": ("solana", "SOL"), "SOLANA": ("solana", "SOL"),
    "BNB": ("binancecoin", "BNB"), "BINANCE COIN": ("binancecoin", "BNB"),
    "XRP": ("ripple", "XRP"), "RIPPLE": ("ripple", "XRP"),
    "ADA": ("cardano", "ADA"), "CARDANO": ("cardano", "ADA"),
    "DOGE": ("dogecoin", "DOGE"), "DOGECOIN": ("dogecoin", "DOGE"),
    "AVAX": ("avalanche-2", "AVAX"), "AVALANCHE": ("avalanche-2", "AVAX"),
    "DOT": ("polkadot", "DOT"), "POLKADOT": ("polkadot", "DOT"),
    "LINK": ("chainlink", "LINK"), "CHAINLINK": ("chainlink", "LINK"),
    "MATIC": ("matic-network", "MATIC"), "POLYGON": ("matic-network", "MATIC"),
    "UNI": ("uniswap", "UNI"), "UNISWAP": ("uniswap", "UNI"),
    "ATOM": ("cosmos", "ATOM"), "COSMOS": ("cosmos", "ATOM"),
    "LTC": ("litecoin", "LTC"), "LITECOIN": ("litecoin", "LTC"),
    "BCH": ("bitcoin-cash", "BCH"),
    "ALGO": ("algorand", "ALGO"), "ALGORAND": ("algorand", "ALGO"),
    "NEAR": ("near", "NEAR"),
    "FTM": ("fantom", "FTM"), "FANTOM": ("fantom", "FTM"),
    "APT": ("aptos", "APT"), "APTOS": ("aptos", "APT"),
    "ARB": ("arbitrum", "ARB"), "ARBITRUM": ("arbitrum", "ARB"),
    "OP": ("optimism", "OP"), "OPTIMISM": ("optimism", "OP"),
    "SUI": ("sui", "SUI"),
    "INJ": ("injective-protocol", "INJ"), "INJECTIVE": ("injective-protocol", "INJ"),
    "TIA": ("celestia", "TIA"), "CELESTIA": ("celestia", "TIA"),
    "SEI": ("sei-network", "SEI"),
    "TAO": ("bittensor", "TAO"), "BITTENSOR": ("bittensor", "TAO"),
    "HYPE": ("hyperliquid", "HYPE"), "HYPERLIQUID": ("hyperliquid", "HYPE"),
    "PEPE": ("pepe", "PEPE"),
    "SHIB": ("shiba-inu", "SHIB"),
    "WIF": ("dogwifcoin", "WIF"),
    "RENDER": ("render-token", "RENDER"), "RNDR": ("render-token", "RENDER"),
    "FET": ("fetch-ai", "FET"),
    "AAVE": ("aave", "AAVE"),
    "MKR": ("maker", "MKR"),
}


def _detect_coin_from_text(text: str) -> tuple[str, str] | None:
    """Scan text for coin mentions, return (coingecko_id, symbol) or None.

    Checks longer names first (e.g. 'ETHEREUM' before 'ETH') to avoid
    false-positive partial matches.  Uses word-boundary matching for
    short symbols (<=4 chars) to prevent matching 'OPTION' as 'OP'.
    Also handles $-prefixed tickers like $ETH, $BTC.
    """
    import re as _re
    upper = text.upper()
    for key in sorted(_COIN_CHART_MAP, key=len, reverse=True):
        if len(key) <= 4:
            # Match word boundary OR $-prefix (e.g. $ETH, $BTC)
            if _re.search(r'(?:\$|\b)' + _re.escape(key) + r'\b', upper):
                logger.debug("Coin detected: %s → %s", key, _COIN_CHART_MAP[key])
                return _COIN_CHART_MAP[key]
        else:
            if key in upper:
                logger.debug("Coin detected: %s → %s", key, _COIN_CHART_MAP[key])
                return _COIN_CHART_MAP[key]
    logger.debug("No coin detected in text: %.80s", text)
    return None


# ── Chart asset rotation + timeframe variation ───────────────────────────────
# Smart rotation: picks the biggest mover, falls back to weighted random
_CHART_ROTATION_FALLBACK = [
    ("bitcoin", "BTC"),
    ("ethereum", "ETH"),
    ("solana", "SOL"),
    ("ripple", "XRP"),
    ("cardano", "ADA"),
    ("avalanche-2", "AVAX"),
]
_chart_rotation_idx: int = 0
_last_chart_asset: str = ""
_last_chart_asset_streak: int = 0
_CHART_TIMEFRAMES = [7, 7, 7, 14, 30]  # 7D most common, 14D/30D occasional

_SMART_COINS = [
    ("BTCUSDT", "bitcoin", "BTC"),
    ("ETHUSDT", "ethereum", "ETH"),
    ("SOLUSDT", "solana", "SOL"),
    ("XRPUSDT", "ripple", "XRP"),
    ("BNBUSDT", "binancecoin", "BNB"),
    ("ADAUSDT", "cardano", "ADA"),
    ("AVAXUSDT", "avalanche-2", "AVAX"),
    ("DOGEUSDT", "dogecoin", "DOGE"),
    ("LINKUSDT", "chainlink", "LINK"),
]


def _get_biggest_mover() -> tuple[str, str] | None:
    """Find the coin with the largest 24h move from Binance."""
    try:
        import json as _json
        pairs = [c[0] for c in _SMART_COINS]
        resp = requests.get("https://api.binance.com/api/v3/ticker/24hr",
                           params={"symbols": _json.dumps(pairs, separators=(",", ":"))}, timeout=10)
        resp.raise_for_status()
        tickers = resp.json()
        # Find biggest absolute % move
        best = max(tickers, key=lambda t: abs(float(t["priceChangePercent"])))
        best_pair = best["symbol"]
        best_pct = float(best["priceChangePercent"])
        # Only use if move is significant (>2%)
        if abs(best_pct) > 2.0:
            for pair, coin_id, symbol in _SMART_COINS:
                if pair == best_pair:
                    logger.info("[SMART] Biggest mover: %s (%+.1f%%) — using for chart",
                               symbol, best_pct)
                    return coin_id, symbol
    except Exception as exc:
        logger.debug("Smart coin detection failed: %s", exc)
    return None


def _pick_default_chart_asset() -> tuple[str, str]:
    """Pick next asset: biggest mover first, then rotation fallback."""
    global _chart_rotation_idx, _last_chart_asset, _last_chart_asset_streak

    # Try smart detection first
    mover = _get_biggest_mover()
    if mover and mover[1] != _last_chart_asset:
        _last_chart_asset = mover[1]
        _last_chart_asset_streak = 1
        return mover

    # Fallback to weighted rotation
    for _ in range(len(_CHART_ROTATION_FALLBACK)):
        coin_id, symbol = _CHART_ROTATION_FALLBACK[_chart_rotation_idx % len(_CHART_ROTATION_FALLBACK)]
        _chart_rotation_idx += 1
        if symbol == _last_chart_asset and _last_chart_asset_streak >= 2:
            continue
        if symbol == _last_chart_asset:
            _last_chart_asset_streak += 1
        else:
            _last_chart_asset = symbol
            _last_chart_asset_streak = 1
        return coin_id, symbol
    # Fallback if all skipped (shouldn't happen)
    _last_chart_asset = "BTC"
    _last_chart_asset_streak = 1
    return "bitcoin", "BTC"


def _chart_for_tweet(
    tweet_text: str,
    coin_id: str | None = None,
    symbol: str | None = None,
) -> str | None:
    """Pick the right chart based on tweet content keywords.

    Chart policy (revised for variety):
      - Explicit coin passed           → line chart for that coin
      - Macro/geopolitical tweets      → rotate bar-change / comparison / text-only
      - Tweet mentions a specific coin → line chart for that coin (60%)
                                         or text-only (40%)
      - No coin detected               → text-only (was BTC-heavy rotation)

    The final no-coin fallback no longer generates a default BTC chart.
    Opinion and generic tweets post text-only, which cuts BTC-chart spam
    roughly in half without losing the charts that carry real signal.
    """
    # Explicit coin passed (price alerts, trending)
    if coin_id and symbol:
        logger.debug("Chart request: explicit coin_id=%s symbol=%s", coin_id, symbol)
        chart = chart_generator.generate_line_fill(coin_id, symbol, 1)
        if chart:
            logger.info("Chart generated for %s: %s", symbol, chart)
            return chart
        logger.warning("Chart generation failed for %s/%s — posting text-only", symbol, coin_id)
        return None

    # Macro/geopolitical tweets — rotate variety so it's not always the bar chart
    if _MACRO_RE.search(tweet_text):
        roll = random.random()
        if roll < 0.33:
            logger.info("Chart: macro tweet → text-only (variety)")
            return None
        if roll < 0.66:
            logger.info("Chart: macro tweet → comparison chart")
            return chart_generator.generate_comparison_chart(days=7)
        logger.info("Chart: macro tweet → bar-change chart")
        return chart_generator.generate_bar_change()

    # Auto-detect coin from tweet text — rotate across chart styles for variety
    # instead of always sending the same single-coin candle.
    detected = _detect_coin_from_text(tweet_text)
    if detected:
        roll = random.random()
        if roll < 0.30:
            # Text-only for variety
            logger.info("Chart: %s detected — text-only (variety)", detected[1])
            return None
        if roll < 0.50:
            # Multi-coin relative-strength overlay (the "7D RELATIVE STRENGTH" look)
            logger.info("Chart: %s detected — comparison chart", detected[1])
            chart = chart_generator.generate_comparison_chart(days=7)
            if chart:
                return chart
            # Fall through to single-coin chart if comparison fails
        # Default: single-coin candle of the detected asset
        days = random.choice(_CHART_TIMEFRAMES)
        chart = chart_generator.generate_line_fill(detected[0], detected[1], days)
        if chart:
            logger.info("Chart generated for %s (%dd): %s", detected[1], days, chart)
            return chart
        logger.warning("Chart generation failed for %s — posting text-only", detected[1])
        return None

    # No specific coin detected → text-only. Cuts BTC chart monotony since
    # the previous BTC-heavy rotated fallback has been retired.
    logger.info("Chart: no coin detected — posting text-only")
    return None


# ── Jobs ──────────────────────────────────────────────────────────────────────

_PRICE_ALERT_COOLDOWN_SECS = 5400  # 90 minutes between price alerts
_last_price_alert_time: float = 0.0
_last_price_alert_by_coin: dict[str, float] = {}  # per-coin 90-min cooldown


def run_price_check() -> None:
    global _last_price_alert_time
    if state.get_daily_count("price_alert") >= config.PRICE_ALERT_DAILY_CAP:
        logger.debug("Price alert daily cap reached — skipping check.")
        return
    now = time.time()
    if _last_price_alert_time > 0 and (now - _last_price_alert_time) < _PRICE_ALERT_COOLDOWN_SECS:
        mins_left = int((_PRICE_ALERT_COOLDOWN_SECS - (now - _last_price_alert_time)) / 60)
        logger.debug("Price alert cooldown — %dm left.", mins_left)
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
        coin_id = alert["coin_id"]
        last_coin_time = _last_price_alert_by_coin.get(coin_id, 0.0)
        if last_coin_time > 0 and (now - last_coin_time) < _PRICE_ALERT_COOLDOWN_SECS:
            mins_left = int((_PRICE_ALERT_COOLDOWN_SECS - (now - last_coin_time)) / 60)
            logger.debug("Price alert coin cooldown for %s — %dm left.", alert["symbol"], mins_left)
            continue
        tweet = ai_writer.generate_price_alert_tweet(alert)
        if not tweet:
            continue
        logger.info("Price alert: %s %+.1f%%", alert["symbol"], alert["pct_change"])
        chart_path: str | None = None
        if random.random() < 0.5:
            try:
                chart_path = _chart_for_tweet(tweet, coin_id=coin_id, symbol=alert["symbol"])
            except Exception as exc:
                logger.warning("Price alert chart generation failed: %s", exc)
        posted = _emit(tweet, tweet_type="price_alert", media_path=chart_path)
        if posted:
            _last_price_alert_time = time.time()
            _last_price_alert_by_coin[coin_id] = _last_price_alert_time
        time.sleep(3)


_last_news_emit_time: float = 0.0


def _news_chart_coin(story: dict) -> tuple[str, str]:
    """Return (coin_id, symbol) for the chart that best fits the story."""
    text = story.get("title", "") + " " + story.get("url", "")
    detected = _detect_coin_from_text(text)
    if detected:
        return detected
    return "bitcoin", "BTC"


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
    is_weekend = datetime.datetime.now().weekday() >= 5
    effective_cap = config.NEWS_DAILY_CAP + 4 if is_weekend else config.NEWS_DAILY_CAP
    for story in stories[:1]:   # max 1 per check-cycle (cap enforced across day)
        if state.get_daily_count("news") >= effective_cap:
            logger.info("News daily cap (%d) reached.", effective_cap)
            break
        # Score + generate commentary if not already done
        scored = news_monitor._ai_score_and_comment(story) if "score" not in story else story
        if scored is None:
            logger.debug("_ai_score_and_comment returned None for: %.60s", story['title'])
            continue

        # Feed into narrative clustering (all scored stories, regardless of score)
        news_monitor.feed_narrative(scored)

        # Skip low-quality stories — only post score 6+
        if scored.get("score", 0) < 6:
            logger.debug("Score %d too low (need 6+): %.60s",
                         scored.get("score", 0), scored.get("title", ""))
            continue

        # Geo/macro breaking news — single Claude tweet + branded dark graphic
        if (
            news_monitor.is_geo_macro_story(scored)
            and scored.get("score", 0) >= 6
            and state.get_daily_count("geo_news") < 3
        ):
            geo_tweet = None
            for attempt in range(_MAX_STRUCT_RETRIES):
                geo_tweet = ai_writer.generate_geo_tweet(scored)
                if not geo_tweet:
                    continue
                lines = [l.strip() for l in geo_tweet.strip().split("\n") if l.strip()]
                has_banned = any(re.search(p, geo_tweet.lower()) for p in _BANNED_TWEET_PATTERNS)
                valid_structure = len(lines) == 3 and "→" in lines[2]
                if valid_structure and not has_banned:
                    logger.info("Geo tweet passed validation")
                    break
                if len(lines) != 3:
                    logger.warning("Geo tweet has %d lines, expected 3 — retrying (attempt %d)", len(lines), attempt + 1)
                elif not lines[2].startswith("→"):
                    logger.warning("Geo tweet missing → line — retrying (attempt %d)", attempt + 1)
                if has_banned:
                    logger.warning("Geo tweet banned phrase detected — retrying (attempt %d)", attempt + 1)
                geo_tweet = None
            if geo_tweet:
                _gc_id, _gc_sym = _news_chart_coin(scored)
                chart_path: str | None = None
                if random.random() < 0.5:
                    logger.info("[CHART] Geo news chart: %s", _gc_sym)
                    try:
                        chart_path = chart_generator.generate_line_fill(_gc_id, _gc_sym, 7)
                    except Exception as exc:
                        logger.warning("Geo chart generation failed: %s", exc)
                logger.info("Geo news (score %d): %.80s",
                            scored.get("score", 0), scored.get("title", ""))
                posted = _emit(geo_tweet, tweet_type="geo_news", media_path=chart_path)
                if posted:
                    _last_news_emit_time = time.time()
                time.sleep(3)
                continue

        # Macro/geopolitical stories get a 3-tweet thread + BTC chart
        if news_monitor._is_macro_source(scored):
            tweets = ai_writer.generate_geopolitical_tweet(scored)
            if not tweets:
                continue
            img_path: str | None = None
            _nc_id, _nc_sym = _news_chart_coin(scored)
            logger.info("[CHART] News chart: %s", _nc_sym)
            try:
                img_path = chart_generator.generate_line_fill(_nc_id, _nc_sym, 7)
            except Exception as exc:
                logger.warning("News chart generation failed for geo thread: %s", exc)
            logger.info("Geo thread (score %d): %.80s",
                        scored.get("score", 0), scored.get("title", ""))
            if DRY_RUN:
                img_note = f"  [image: {img_path}]" if img_path else ""
                print(f"\n{'─'*60}\n[DRY RUN] [geo-thread]{img_note}")
                for i, t in enumerate(tweets, 1):
                    print(f"  [{i}] {t}")
                print("─" * 60)
                posted = True
            else:
                posted = _post_thread_with_retry(tweets, first_tweet_image_path=img_path)
            if posted:
                state.record_tweet()
                state.increment_daily_count("news")
                _last_news_emit_time = time.time()
            time.sleep(3)
            continue

        # Quote-style tweet for high-score stories with a direct power quote
        if (
            scored.get("score", 0) >= 8
            and ai_writer.story_has_power_quote(scored)
        ):
            quote_tweet = ai_writer.generate_quote_style_tweet(scored)
            if quote_tweet:
                img_path: str | None = None
                _nc_id, _nc_sym = _news_chart_coin(scored)
                logger.info("[CHART] News chart: %s", _nc_sym)
                try:
                    img_path = chart_generator.generate_line_fill(_nc_id, _nc_sym, 7)
                except Exception as exc:
                    logger.warning("News chart generation failed for quote tweet: %s", exc)
                logger.info("Quote tweet (score %d): %.80s",
                            scored.get("score", 0), scored.get("title", ""))
                posted = _emit(quote_tweet, tweet_type="news", media_path=img_path)
                if posted:
                    _last_news_emit_time = time.time()
                time.sleep(3)
                continue

        is_high_conviction = scored.get("score", 0) >= 8
        tweet = ai_writer.generate_news_tweet(scored, high_conviction=is_high_conviction)
        if not tweet:
            tweet = news_monitor.format_news_tweet(scored)
        if not tweet:
            continue
        img_path: str | None = None
        if random.random() < 0.5:
            _nc_id, _nc_sym = _news_chart_coin(scored)
            logger.info("[CHART] News chart: %s", _nc_sym)
            try:
                img_path = chart_generator.generate_line_fill(_nc_id, _nc_sym, 7)
            except Exception as exc:
                logger.warning("News chart generation failed: %s", exc)
        logger.info("News (score %d): %.80s",
                    scored.get("score", 0), scored.get("title", ""))
        posted = _emit(tweet, tweet_type="news", media_path=img_path)
        if posted:
            _last_news_emit_time = time.time()
        time.sleep(3)


_BOT_START_TIME: float = 0.0      # set in main() before entering the loop
_last_trending_run: float = 0.0   # set in main(); guards 2h min gap between trending runs
_trending_coin_cooldown: dict[str, float] = {}  # coin_id → timestamp, 6h per coin


def run_trending_check() -> None:
    """Post about a trending coin outside our main watchlist. Max 4/day.

    Rate-limited to at most once per 2 hours, plus a 6-hour per-coin cooldown
    to prevent the same trending coin from appearing twice.
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
    # Pick the first alert that isn't on per-coin cooldown
    alert = None
    for a in alerts:
        coin_id = a.get("id", a.get("symbol", ""))
        last_coin = _trending_coin_cooldown.get(coin_id, 0.0)
        if last_coin > 0 and (now - last_coin) < 21600:  # 6 hours
            logger.debug("Trending coin %s still on cooldown — skipping.", a.get("symbol"))
            continue
        alert = a
        break
    if not alert:
        logger.info("All trending coins on cooldown.")
        return
    tweet = trending_monitor.format_trending_tweet(alert)
    if tweet:
        img_path: str | None = None
        try:
            img_path = _chart_for_tweet(tweet, coin_id=alert["id"], symbol=alert["symbol"])
        except Exception as exc:
            logger.warning("Trending chart generation failed: %s", exc)
        logger.info("Trending: %s (%s, rank #%s)",
                    alert["symbol"], alert["source"],
                    alert.get("market_cap_rank", "?"))
        posted = _emit(tweet, tweet_type="trending", media_path=img_path)
        if posted:
            _trending_coin_cooldown[alert.get("id", alert.get("symbol", ""))] = now


def run_volume_anomaly() -> None:
    """Scan for volume anomalies every 2 hours. Only post if anomaly detected."""
    if state.get_daily_count("volume_anomaly") >= 3:
        logger.debug("Volume anomaly daily cap (3) reached — skipping.")
        return
    logger.info("[ANOMALY] Running volume anomaly scan…")
    anomaly = volume_anomaly_scanner.scan()
    if not anomaly:
        logger.info("[ANOMALY] No anomalies detected this cycle.")
        return

    symbol = anomaly["symbol"]
    logger.info("[ANOMALY] %s: %.2fx volume, %.2f%% move",
                symbol, anomaly["vol_ratio"], anomaly["price_change"])

    tweet = ai_writer.generate_volume_anomaly_tweet(
        symbol=symbol,
        price=anomaly["price"],
        vol_ratio=anomaly["vol_ratio"],
        price_change=anomaly["price_change"],
    )
    if not tweet:
        logger.warning("[ANOMALY] Tweet generation failed for %s", symbol)
        return

    # Generate chart for the anomaly coin
    coin_map = {"BTC": "bitcoin", "ETH": "ethereum", "SOL": "solana",
                "XRP": "ripple", "BNB": "binancecoin", "ADA": "cardano",
                "AVAX": "avalanche-2", "DOGE": "dogecoin", "LINK": "chainlink",
                "DOT": "polkadot"}
    coin_id = coin_map.get(symbol, "bitcoin")
    chart_path: str | None = None
    try:
        chart_path = chart_generator.generate_line_fill(coin_id, symbol, 7)
    except Exception as exc:
        logger.warning("[ANOMALY] Chart generation failed for %s: %s", symbol, exc)

    posted = _emit(tweet, tweet_type="volume_anomaly", media_path=chart_path)
    if posted:
        state.increment_daily_count("volume_anomaly")
        logger.info("[ANOMALY] Posted volume anomaly tweet for %s", symbol)


def run_quote_tweet() -> None:
    """Market analysis tweet via tweet_generators (max 1/day)."""
    if state.get_daily_count("quote") >= config.QUOTE_TWEET_DAILY_CAP:
        logger.info("Quote tweet daily cap (%d) reached.", config.QUOTE_TWEET_DAILY_CAP)
        return
    tweet = tweet_generators.generate_quote_tweet()
    if tweet:
        media_path: str | None = None
        try:
            media_path = _chart_for_tweet(tweet)
        except Exception as exc:
            logger.warning("Quote tweet chart generation failed: %s", exc)
        _emit(tweet, tweet_type="quote", media_path=media_path)


def run_morning_recap() -> None:
    if not _should_fire("morning_recap", 8):
        return
    _mark_slot_fired("morning_recap")
    logger.info("Running morning recap…")
    tweet = tweet_generators.generate_morning_recap()
    logger.debug("morning recap tweet_generators result: %s", "OK" if tweet else "None")
    if not tweet:
        headlines = news_monitor.fetch_latest_headlines(3)
        logger.debug("morning recap headlines fallback: %d headlines", len(headlines) if headlines else 0)
        if headlines:
            tweet = ai_writer.generate_morning_recap(headlines)
    if tweet:
        chart_path = None
        morning_coins = tweet_generators._last_morning_coins
        if morning_coins:
            for attempt in range(3):
                chart_path = chart_generator.generate_morning_recap_chart(morning_coins)
                if chart_path:
                    break
                logger.warning("Chart generation attempt %d failed, retrying...", attempt + 1)
                time.sleep(5)
            if not chart_path:
                logger.warning("Morning recap chart failed after 3 attempts — trying bar_change fallback")
                try:
                    chart_path = chart_generator.generate_bar_change()
                except Exception as exc:
                    logger.warning("Bar change fallback also failed: %s", exc)
                if not chart_path:
                    logger.warning("All chart fallbacks exhausted, posting without image")
        _emit(tweet, bypass_guard=True, tweet_type="morning_recap", media_path=chart_path)
    else:
        logger.warning("Morning recap generation failed — will retry next minute.")


def run_opinion_tweet() -> None:
    if not _should_fire("opinion", 12):
        return
    _mark_slot_fired("opinion")  # mark BEFORE posting to prevent any duplicate
    logger.info("Running opinion tweet (12:00)…")
    tweet = None
    for attempt in range(_MAX_STRUCT_RETRIES):
        tweet = tweet_generators.generate_opinion_tweet()
        if not tweet:
            continue
        lines = [l.strip() for l in tweet.strip().split("\n") if l.strip()]
        has_banned = any(re.search(p, tweet.lower()) for p in _BANNED_TWEET_PATTERNS)
        valid_structure = len(lines) >= 1
        if valid_structure and not has_banned:
            logger.info("Opinion tweet passed validation")
            break
        if len(lines) < 1:
            logger.warning("Opinion tweet empty — retrying (attempt %d)", attempt + 1)
        if has_banned:
            logger.warning("Opinion tweet banned phrase detected — retrying (attempt %d)", attempt + 1)
        tweet = None
    if tweet:
        media_path: str | None = None
        try:
            media_path = _chart_for_tweet(tweet)
        except Exception as exc:
            logger.warning("Opinion chart generation failed: %s", exc)
        if not media_path:
            time.sleep(10)
            media_path = _chart_for_tweet(tweet)
        _emit(tweet, bypass_guard=True, tweet_type="hot_take", media_path=media_path)
    else:
        logger.error("Opinion tweet failed validation after %d attempts — skipping", _MAX_STRUCT_RETRIES)



def run_opinion_bomb() -> None:
    if not _should_fire("opinion_bomb", 13):
        return
    _mark_slot_fired("opinion_bomb")
    logger.info("[opinion_bomb] Running 13:00 opinion bomb…")
    tweet = ai_writer.generate_opinion_bomb()
    if not tweet:
        logger.warning("[opinion_bomb] Generation failed — skipping.")
        return
    _emit(tweet, bypass_guard=True, tweet_type="opinion_bomb")
    logger.info("[opinion_bomb] Posted: %.80s", tweet)


def run_engagement_tweet() -> None:
    if not _should_fire("engagement", 16):
        return
    _mark_slot_fired("engagement")
    logger.info("Running engagement tweet (16:00)…")
    tweet = None
    for attempt in range(_MAX_STRUCT_RETRIES):
        tweet = tweet_generators.generate_engagement_tweet()
        if not tweet:
            continue
        lines = [l.strip() for l in tweet.strip().split("\n") if l.strip()]
        has_banned = any(re.search(p, tweet.lower()) for p in _BANNED_TWEET_PATTERNS)
        valid_structure = len(lines) >= 1
        if valid_structure and not has_banned:
            logger.info("Engagement tweet passed validation")
            break
        if len(lines) < 1:
            logger.warning("Engagement tweet empty — retrying (attempt %d)", attempt + 1)
        if has_banned:
            logger.warning("Engagement tweet banned phrase detected — retrying (attempt %d)", attempt + 1)
        tweet = None
    if tweet:
        media_path: str | None = None
        try:
            media_path = _chart_for_tweet(tweet)
        except Exception as exc:
            logger.warning("Engagement chart generation failed: %s", exc)
        if not media_path:
            time.sleep(10)
            media_path = _chart_for_tweet(tweet)
        _emit(tweet, bypass_guard=True, tweet_type="engagement", media_path=media_path)
    else:
        logger.error("Engagement tweet failed validation after %d attempts — skipping", _MAX_STRUCT_RETRIES)


def run_market_open() -> None:
    if not _should_fire("market_open", 9, minute=30):
        return
    _mark_slot_fired("market_open")
    logger.info("Running market open tweet (09:30)…")
    btc = tweet_generators._fetch_binance_coin("bitcoin")
    eth = tweet_generators._fetch_binance_coin("ethereum")
    if not btc:
        logger.warning("Market open: no BTC data from Binance — skipping.")
        return
    btc_price = float(btc.get("current_price", 0))
    btc_pct = float(btc.get("price_change_percentage_24h", 0))
    eth_price = float(eth.get("current_price", 0)) if eth else 0.0
    eth_pct = float(eth.get("price_change_percentage_24h", 0)) if eth else 0.0

    tweet = ai_writer.generate_market_open_tweet(
        btc_price=btc_price,
        btc_pct=btc_pct,
        eth_price=eth_price,
        eth_pct=eth_pct,
    )

    if not tweet or len(tweet) < 20:
        tweet = (
            f"Range unclear — market waiting for direction.\n"
            f"BTC ${btc_price:,.0f} ({btc_pct:+.1f}%) | ETH ${eth_price:,.0f} ({eth_pct:+.1f}%)\n"
            f"Expansion follows compression."
        )

    media_path: str | None = None
    try:
        media_path = _chart_for_tweet(tweet)
    except Exception as exc:
        logger.warning("Market open chart failed: %s", exc)
    _emit(tweet, bypass_guard=True, tweet_type="market_open", media_path=media_path)


def run_rotation_check() -> None:
    if not _should_fire("rotation_check", 10, minute=15):
        return
    _mark_slot_fired("rotation_check")
    logger.info("Running 10:15 rotation check…")

    symbols = ["SOL", "BNB", "XRP", "ADA", "AVAX", "LINK", "INJ", "FET", "RENDER"]
    coins = []
    for symbol in symbols:
        data = tweet_generators._fetch_binance_coin(symbol.lower())
        if not data:
            continue
        coins.append({
            "symbol": symbol,
            "pct": float(data.get("price_change_percentage_24h", 0)),
        })

    if not coins:
        logger.warning("Rotation check: no coin data available — skipping.")
        return

    tweet = ai_writer.generate_rotation_tweet(coins)
    if not tweet:
        tweet = (
            "Rotation building under the surface.\n"
            "Strength is not where most are looking.\n"
            "Watch where momentum concentrates."
        )

    _emit(tweet, bypass_guard=True, tweet_type="rotation")


def _fetch_binance_tickers() -> list[dict]:
    """Fetch 24hr ticker data from Binance for the top 5 coins."""
    symbols = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT", "BNBUSDT"]
    names = {"BTCUSDT": "BTC", "ETHUSDT": "ETH", "SOLUSDT": "SOL",
             "XRPUSDT": "XRP", "BNBUSDT": "BNB"}
    results = []
    for sym in symbols:
        try:
            resp = requests.get(
                "https://api.binance.com/api/v3/ticker/24hr",
                params={"symbol": sym},
                timeout=10,
            )
            resp.raise_for_status()
            data = resp.json()
            price = float(data["lastPrice"])
            pct = float(data["priceChangePercent"])
            results.append({"name": names[sym], "price": price, "pct": pct})
        except Exception as exc:
            logger.warning("Binance ticker fetch failed for %s: %s", sym, exc)
    return results


def _build_market_check_tweet(label: str) -> str:
    """Build a market check tweet from live Binance data."""
    tickers = _fetch_binance_tickers()
    if not tickers:
        return ""
    lines = []
    green_count = 0
    for t in tickers:
        icon = "+" if t["pct"] >= 0 else "-"
        sign = "+" if t["pct"] >= 0 else ""
        if t["pct"] >= 0:
            green_count += 1
        if t["price"] >= 100:
            price_str = f"${t['price']:,.0f}"
        else:
            price_str = f"${t['price']:.2f}"
        lines.append(f"{icon} {t['name']} {price_str} ({sign}{t['pct']:.1f}%)")
    total = len(tickers)

    # Get BTC price for the → level line
    btc_ticker = next((t for t in tickers if t["name"] == "BTC"), None)
    btc_p = btc_ticker["price"] if btc_ticker else 0

    if green_count >= 4:
        summary = "Buyers running it. Broad strength across the board."
        trigger = f"→ Hold ${btc_p:,.0f} and this keeps grinding higher." if btc_p else "→ Strength continues until structure breaks."
    elif green_count == 3:
        summary = "Split tape. No clear winner yet."
        trigger = f"→ ${btc_p:,.0f} decides direction from here." if btc_p else "→ Next 4h candle decides."
    elif green_count == 2:
        summary = "Most of the board is red. Bounces getting sold."
        trigger = f"→ Lose ${btc_p:,.0f} and downside accelerates." if btc_p else "→ Sellers stay in control until proven otherwise."
    else:
        summary = "Sellers dominating. No real bids showing up."
        trigger = f"→ Below ${btc_p:,.0f} opens the flush." if btc_p else "→ Risk-off until proven otherwise."

    tweet = label + "\n\n" + "\n".join(lines) + "\n\n" + summary + "\n" + trigger
    return tweet


def run_midmorning_check() -> None:
    if not _should_fire("midmorning_check", 11):
        return
    _mark_slot_fired("midmorning_check")  # mark BEFORE posting to prevent any duplicate
    logger.info("Running 11:00 market check…")
    tweet = _build_market_check_tweet("11:00 market check")
    if tweet:
        _emit(tweet, bypass_guard=True, tweet_type="market_open", media_path=_chart_for_tweet(tweet))
    else:
        logger.warning("11:00 market check failed — skipping.")


def run_afternoon_take() -> None:
    if not _should_fire("afternoon_take", 14):
        return
    _mark_slot_fired("afternoon_take")  # mark BEFORE posting to prevent any duplicate
    logger.info("Running 14:00 market check…")
    tweet = _build_market_check_tweet("14:00 market check")
    if tweet:
        _emit(tweet, bypass_guard=True, tweet_type="market_open", media_path=_chart_for_tweet(tweet))
    else:
        logger.warning("14:00 market check failed — skipping.")


# ── Narrative check ───────────────────────────────────────────────────────────

_NARRATIVE_DAILY_CAP = 2
_narrative_cooldown: dict[str, float] = {}  # theme → last-posted timestamp
_NARRATIVE_COOLDOWN_SECS = 24 * 3600  # 24 hours per theme


def run_narrative_check() -> None:
    """Check for emerging narratives every 2 hours. Max 2 per day, 24h per theme."""
    if state.get_daily_count("narrative") >= _NARRATIVE_DAILY_CAP:
        logger.debug("Narrative daily cap (%d) reached — skipping.", _NARRATIVE_DAILY_CAP)
        return
    narrative = news_monitor.check_narratives()
    if not narrative:
        return

    theme = narrative["theme"]

    # 24-hour per-theme cooldown
    last_posted = _narrative_cooldown.get(theme, 0)
    if time.time() - last_posted < _NARRATIVE_COOLDOWN_SECS:
        hours_left = int((_NARRATIVE_COOLDOWN_SECS - (time.time() - last_posted)) / 3600)
        logger.debug("Narrative '%s' on cooldown — %dh left.", theme, hours_left)
        return

    logger.info("Emerging narrative detected: %s (%d stories, score %.1f)",
                theme, narrative["story_count"], narrative.get("narrative_score", 0))
    tweet = None
    for attempt in range(_MAX_STRUCT_RETRIES):
        tweet = ai_writer.generate_narrative_tweet(
            theme=narrative["theme"],
            story_count=narrative["story_count"],
            summaries=narrative["summaries"],
        )
        if not tweet:
            continue
        lines = [l.strip() for l in tweet.strip().split("\n") if l.strip()]
        has_banned = any(re.search(p, tweet.lower()) for p in _BANNED_TWEET_PATTERNS)
        valid_structure = len(lines) == 3
        if valid_structure and not has_banned:
            logger.info("Narrative tweet passed validation")
            break
        if len(lines) != 3:
            logger.warning("Narrative tweet has %d lines, expected 3 — retrying (attempt %d)", len(lines), attempt + 1)
        if has_banned:
            logger.warning("Narrative tweet banned phrase detected — retrying (attempt %d)", attempt + 1)
        tweet = None
    if not tweet:
        logger.error("Narrative tweet failed validation after %d attempts — skipping", _MAX_STRUCT_RETRIES)
        return
    _narrative_chart = _chart_for_tweet(tweet) if random.random() < 0.5 else None
    posted = _emit(tweet, tweet_type="narrative", media_path=_narrative_chart)
    if posted:
        state.increment_daily_count("narrative")
        _narrative_cooldown[theme] = time.time()
        logger.info("Narrative tweet posted for: %s", theme)



_evening_thread_topics = [
    "ETF market saturation — what happens when every institution already has exposure",
    "Layer-2 dominance shift — Arbitrum, Base, and Optimism are rewriting the execution layer",
    "Stablecoin regulation just changed the game — here's who wins and who loses",
    "Bitcoin post-halving supply dynamics — the 2024 halving impact is still unfolding",
    "Why institutional adoption hasn't moved price the way everyone expected",
    "The real cost of crypto regulation compliance — and who can't afford it",
    "DeFi yields collapsed — what replaced them and why it matters more",
    "Solana vs Ethereum — the 2026 developer war and what the data actually shows",
    "How AI and crypto are converging — and where the real alpha is forming",
    "Why most crypto VCs are underwater and what that means for the next cycle",
    "The death of the altcoin season narrative — rotation is dead, selection is everything",
    "How macro drives crypto in 2026 — rates, inflation, and geopolitics are the only chart that matters",
    "Why on-chain metrics stopped predicting price — and what replaced them",
    "The stablecoin yield wars — what they mean for BTC flows and positioning",
    "Why Bitcoin dominance keeps rising — and what it takes to reverse it",
]
_thread_topic_index: int = state.get_thread_topic_index()


def run_evening_thread() -> None:
    if not _should_fire("evening_thread", 19):
        return
    _mark_slot_fired("evening_thread")
    global _thread_topic_index
    topic = _evening_thread_topics[_thread_topic_index % len(_evening_thread_topics)]
    _thread_topic_index += 1
    state.set_thread_topic_index(_thread_topic_index)
    logger.info("Running evening thread (19:00): %s", topic)

    # Fetch live price data to prevent Claude from hallucinating prices
    price_context = ""
    try:
        import requests as _req
        resp = _req.get("https://api.binance.com/api/v3/ticker/24hr",
                       params={"symbols": '["BTCUSDT","ETHUSDT","SOLUSDT","XRPUSDT","BNBUSDT"]'},
                       timeout=10)
        if resp.ok:
            lines = []
            for t in resp.json():
                sym = t["symbol"].replace("USDT", "")
                price = float(t["lastPrice"])
                pct = float(t["priceChangePercent"])
                if price >= 1000:
                    lines.append(f"{sym}: ${price:,.0f} ({pct:+.1f}% 24h)")
                else:
                    lines.append(f"{sym}: ${price:,.2f} ({pct:+.1f}% 24h)")
            price_context = "\n".join(lines)
            logger.info("Thread price context: %s", price_context.replace("\n", " | "))
    except Exception as exc:
        logger.warning("Failed to fetch thread price context: %s", exc)

    tweets = ai_writer.generate_thread(topic, n_tweets=3, price_context=price_context)
    if not tweets:
        logger.warning("Evening thread failed — skipping.")
        return

    def _generate_chart_for_topic() -> str | None:
        return _chart_for_tweet(topic)

    img_path: str | None = None
    try:
        for attempt in range(3):
            try:
                img_path = _generate_chart_for_topic()
            except Exception as e:
                logger.warning(f"Chart attempt {attempt+1} error: {e}")
                img_path = None
            if img_path:
                break
            logger.warning("Chart generation attempt %d failed, retrying...", attempt + 1)
            time.sleep(5)
        if not img_path:
            logger.warning("Thread chart: None after 3 attempts, posting without image")
    except Exception as exc:
        logger.warning("Evening thread chart generation failed: %s", exc)

    logger.info("Thread chart: %s", img_path)

    if DRY_RUN:
        img_note = f"  [chart: {img_path}]" if img_path else "  [no chart]"
        print(f"\n{'─'*60}\n[DRY RUN] Evening thread ({len(tweets)} tweets){img_note}:")
        for i, t in enumerate(tweets, 1):
            print(f"  [{i}] {t}")
        print('─'*60)
    else:
        ok = _post_thread_with_retry(tweets, first_tweet_image_path=img_path)
        if ok:
            state.record_tweet(len(tweets))
            state.increment_daily_count("evening_thread", len(tweets))
            logger.info("Evening thread posted (%d tweets).", len(tweets))
        else:
            logger.error("Evening thread failed.")

    if img_path:
        try:
            os.unlink(img_path)
        except OSError:
            pass


# ── Reply bot ─────────────────────────────────────────────────────────────────
_REPLY_ACCOUNTS = config.REPLY_ACCOUNTS  # configured via .env REPLY_ACCOUNTS
_REPLY_COOLDOWN = 5400   # 90 minutes between replies
_last_reply_time: float = 0.0


def run_reply_check() -> None:
    """Search recent tweets from target accounts and reply to the highest-engagement
    one not yet replied to. Max REPLY_DAILY_CAP/day, 90-min cooldown between replies."""
    global _last_reply_time
    logger.info("[REPLY] Running reply check (targets=%d, daily cap=%d)",
                len(_REPLY_ACCOUNTS), config.REPLY_DAILY_CAP)

    if state.get_daily_count("reply") >= config.REPLY_DAILY_CAP:
        logger.info("[REPLY] Daily cap (%d) reached — skipping.", config.REPLY_DAILY_CAP)
        return

    now = time.time()
    if _last_reply_time > 0 and (now - _last_reply_time) < _REPLY_COOLDOWN:
        mins_left = int((_REPLY_COOLDOWN - (now - _last_reply_time)) / 60)
        logger.debug("Reply cooldown — %dm left.", mins_left)
        return

    replied_ids = state.get_replied_ids()
    candidates: list[dict] = []

    for account in _REPLY_ACCOUNTS:
        tweets = twitter_client.search_recent_tweets(
            query=f"from:{account} -is:retweet -is:reply",
            max_results=10,
        )
        for tweet in tweets:
            if tweet["id"] not in replied_ids:
                candidates.append(tweet)

    if not candidates:
        logger.info("Reply check: no new tweets from target accounts.")
        return

    # Pick highest engagement (likes + retweets)
    best = max(candidates, key=lambda t: t["like_count"] + t["retweet_count"])
    logger.info("Reply target (likes=%d, rts=%d): %.80s",
                best["like_count"], best["retweet_count"], best["text"])

    reply = ai_writer.generate_reply(best["text"])
    if not reply:
        logger.warning("Reply generation failed — skipping.")
        return

    posted = twitter_client.post_tweet(reply, in_reply_to_tweet_id=best["id"])
    if posted:
        state.add_replied_id(best["id"])
        state.increment_daily_count("reply")
        _last_reply_time = time.time()
        logger.info("Reply posted to tweet %s: %.80s", best["id"], reply)
    else:
        logger.warning("Reply post failed for tweet %s.", best["id"])


def run_fear_greed_tweet() -> None:
    if not _should_fire("fear_greed", 21):
        return
    _mark_slot_fired("fear_greed")
    logger.info("Running 21:00 Fear & Greed tweet…")
    data = fear_greed.fetch_fear_greed()
    if not data:
        logger.warning("Fear & Greed fetch failed — skipping.")
        return
    if not fear_greed.should_post(data["value"]):
        logger.info("Fear & Greed: cooldown or duplicate value — skipping.")
        return
    result = fear_greed.format_fear_greed_tweet(data)
    if not result:
        logger.warning("Fear & Greed formatting failed — skipping.")
        return
    tweet, img_path = result
    posted = _emit(tweet, bypass_guard=True, tweet_type="fear_greed", media_path=img_path)
    if posted:
        fear_greed.record_posted(data)


def run_trend_spotter() -> None:
    """Check for trending crypto topics and post if something is hot."""
    logger.info("[TREND] Running trend spotter...")
    tweet, coin_id, symbol = trend_spotter.generate_trend_tweet()
    if not tweet:
        return

    img_path = None
    if coin_id and symbol:
        try:
            img_path = chart_generator.generate_line_fill(coin_id, symbol, 1)
        except Exception as exc:
            logger.warning("[TREND] Chart generation failed: %s", exc)

    # If no chart (either no coin_id or generation failed), post text-only.
    # A BTC fallback chart for a tweet about ARB or an obscure trending coin
    # is worse than no chart — it contradicts the tweet.
    _emit(tweet, tweet_type="trend", media_path=img_path, no_chart=(img_path is None))


def run_weekly_recap() -> None:
    """Sunday 10:00 UK — weekly recap thread with comparison chart."""
    now_uk = datetime.datetime.now(_LONDON_TZ)
    if now_uk.strftime("%A") != "Sunday":
        return
    if not _should_fire("weekly_recap", 10):
        return
    _mark_slot_fired("weekly_recap")
    logger.info("Running Sunday weekly recap…")

    # Fetch live prices for context
    price_context = ""
    try:
        resp = requests.get("https://api.binance.com/api/v3/ticker/24hr",
                           params={"symbols": '["BTCUSDT","ETHUSDT","SOLUSDT","XRPUSDT","BNBUSDT"]'},
                           timeout=10)
        if resp.ok:
            lines = []
            for t in resp.json():
                sym = t["symbol"].replace("USDT", "")
                price = float(t["lastPrice"])
                pct = float(t["priceChangePercent"])
                lines.append(f"{sym}: ${price:,.0f} ({pct:+.1f}% 24h)" if price >= 1000
                           else f"{sym}: ${price:,.2f} ({pct:+.1f}% 24h)")
            price_context = "\n".join(lines)
    except Exception:
        pass

    tweets = ai_writer.generate_weekly_recap(price_context)
    if not tweets:
        logger.warning("Weekly recap failed.")
        return

    # Generate comparison chart for the thread
    img_path = None
    try:
        img_path = chart_generator.generate_comparison_chart(7)
    except Exception as exc:
        logger.warning("Comparison chart failed: %s", exc)

    if DRY_RUN:
        print(f"\n{'─'*60}\n[DRY RUN] Weekly Recap ({len(tweets)} tweets):")
        for i, t in enumerate(tweets, 1):
            print(f"  [{i}] {t}")
        print('─'*60)
    else:
        ok = _post_thread_with_retry(tweets, first_tweet_image_path=img_path)
        if ok:
            state.record_tweet(len(tweets))
            logger.info("Weekly recap posted (%d tweets).", len(tweets))


# ── Scheduler ─────────────────────────────────────────────────────────────────
_schedule_configured: bool = False


def setup_schedule() -> None:
    global _schedule_configured
    if _schedule_configured:
        logger.warning("setup_schedule() called more than once — ignoring duplicate.")
        return
    _schedule_configured = True

    # ── GROWTH SCHEDULE — quality over quantity ──────────────────────────
    #
    # Strategy: 5 high-quality scheduled posts + reactive alerts + replies
    # Reply engine is the #1 growth tool — max it out
    #
    # Scheduled posts (UK time):
    #   08:00  Morning recap    — strong opening, catches early scrollers
    #   12:00  Opinion tweet    — hot take, lunch crowd engagement
    #   16:00  Engagement tweet — US market open, biggest CT audience
    #   19:00  Evening thread   — 3-tweet thread, gets bookmarks/shares
    #   21:00  Fear & Greed     — data visual, highly shareable
    #
    # Interval (reactive):
    #   Price alerts  — every 5 min check, 3/day cap (only big moves)
    #   News          — every 15 min check, 4/day cap (only high-score)
    #   Reply engine  — every 8 min, 5/day cap (GROWTH ENGINE)
    #   Narrative     — every 3 hours (clustering stories)

    # Interval-driven jobs
    _scheduler.every(5).minutes.do(_safe(run_price_check))
    _scheduler.every(10).minutes.do(_safe(run_news_check))
    _scheduler.every(3).hours.do(_safe(run_narrative_check))
    _scheduler.every(30).minutes.do(_safe(run_trend_spotter))
    _scheduler.every(30).minutes.do(_safe(run_reply_check))

    # Time-of-day jobs — 5 high-impact posts only
    _scheduler.every(1).minutes.do(_safe(run_morning_recap))
    _scheduler.every(1).minutes.do(_safe(run_opinion_tweet))
    _scheduler.every(1).minutes.do(_safe(run_engagement_tweet))
    _scheduler.every(1).minutes.do(_safe(run_evening_thread))
    _scheduler.every(1).minutes.do(_safe(run_fear_greed_tweet))
    _scheduler.every(1).minutes.do(_safe(run_weekly_recap))

    n_jobs = len(_scheduler.get_jobs())
    logger.info(
        "Scheduled %d jobs (GROWTH MODE): price/5m | news/15m | "
        "narrative/3h | 08:00 recap | 12:00 opinion | 16:00 engagement | "
        "19:00 thread | 21:00 fear-greed | Sun 10:00 weekly-recap  (UK time)",
        n_jobs,
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
        try:
            with open(_LOCK_FILE) as f:
                old_pid_str = f.read().strip()
            if old_pid_str:
                old_pid = int(old_pid_str)
                os.kill(old_pid, 0)  # signal 0 = check existence
                # If we reach here, the process is alive (or permission denied = also alive)
                logger.critical("Bot already running (PID %d). Exiting to prevent duplicates.", old_pid)
                print(f"ERROR: Bot already running (PID {old_pid}). Exiting.")
                sys.exit(1)
        except ProcessLookupError:
            logger.info("Stale bot.pid (PID %s not running) — taking over.", old_pid_str)
        except PermissionError:
            # os.kill raises PermissionError if PID exists but belongs to another user
            logger.critical("Bot PID %s is alive (owned by another user). Exiting.", old_pid_str)
            print(f"ERROR: Bot PID {old_pid_str} is alive (permission denied). Exiting.")
            sys.exit(1)
        except (ValueError, OSError):
            logger.info("Invalid or stale bot.pid — overwriting.")
    with open(_LOCK_FILE, "w") as f:
        f.write(str(os.getpid()))
    logger.info("PID lock acquired: %d → %s", os.getpid(), _LOCK_FILE)
    import atexit
    def _cleanup_pid():
        try:
            with open(_LOCK_FILE) as f:
                if f.read().strip() == str(os.getpid()):
                    os.unlink(_LOCK_FILE)
        except OSError:
            pass
    atexit.register(_cleanup_pid)

    parser = argparse.ArgumentParser(description="Crypto News Twitter Bot")
    parser.add_argument("--dry-run", action="store_true",
                        help="Print tweets instead of posting")
    parser.add_argument("--preview", action="store_true",
                        help="Generate and print tonight's evening thread, then exit")
    args = parser.parse_args()
    DRY_RUN = args.dry_run

    if args.preview:
        topic = _evening_thread_topics[_thread_topic_index % len(_evening_thread_topics)]
        print(f"\nEvening thread preview — topic: {topic}\n{'─'*60}")
        tweets = ai_writer.generate_thread(topic, n_tweets=3)
        if not tweets:
            print("Failed to generate thread (is ANTHROPIC_API_KEY set?)")
            sys.exit(1)
        for i, t in enumerate(tweets, 1):
            print(f"\n[{i}] {t}")
        print(f"\n{'─'*60}")
        sys.exit(0)
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

    if not DRY_RUN and config.TWITTER_ENABLED:
        try:
            twitter_client.get_client()
            logger.info("Twitter credentials OK.")
        except RuntimeError as exc:
            logger.critical("Cannot start: %s", exc)
            sys.exit(1)
    elif not config.TWITTER_ENABLED:
        logger.warning("[X DISABLED] TWITTER_ENABLED=false — Telegram & Bluesky only.")

    # Startup confirmation (log only — not posted to public Telegram channel
    # to avoid "bot started" spam that subscribers see on every restart).
    logger.info("Bot startup complete; mirrors active (Twitter/Telegram/Bluesky).")

    setup_schedule()
    jobs = _scheduler.get_jobs()
    logger.info("Scheduler: %d jobs registered. Listing all:", len(jobs))
    for i, job in enumerate(jobs, 1):
        logger.info("  [%d] %s", i, job)
    if len(jobs) != len(set(str(j) for j in jobs)):
        logger.warning("DUPLICATE JOBS DETECTED — check setup_schedule()")

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
