#!/usr/bin/env python3
"""
Bluesky reply engine — legitimate API-driven, no ToS risk.

Pulls recent posts from configured crypto/macro accounts via AT Protocol,
generates a reply with the shared `ai_writer.generate_reply` voice, and
posts it. Same rate-limit profile as the X browser reply engine.

Usage:
    python3 bluesky_reply_engine.py        # run once
    python3 bluesky_reply_engine.py --loop # run every ~20 min
"""
from __future__ import annotations

import json
import logging
import os
import random
import sys
import time
from datetime import datetime, timezone, timedelta

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    handlers=[
        logging.FileHandler(os.path.join(os.path.dirname(os.path.abspath(__file__)), "bluesky_reply.log")),
        logging.StreamHandler(sys.stdout),
    ],
)
logger = logging.getLogger("bluesky_reply")

_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _DIR)

import config  # noqa: E402
import ai_writer  # noqa: E402
import bluesky_client  # noqa: E402

_REPLIED_FILE = os.path.join(_DIR, ".bluesky_replied_uris.json")
_REPLY_TIMES_FILE = os.path.join(_DIR, ".bluesky_reply_times.json")
_MAX_STORED_IDS = 500

MAX_REPLIES_PER_HOUR = 3
MIN_GAP_SECONDS = 360            # 6 min between replies
MAX_REPLIES_PER_DAY = 15         # Bluesky is API-legit — no ban risk to push harder
MAX_REPLIES_PER_ACCOUNT_PER_DAY = 3   # rotation across target accounts
MAX_AGE_HOURS = 24
MIN_WORDS = 8

_ACCOUNT_COUNTS_FILE = os.path.join(_DIR, ".bluesky_account_counts.json")
_reply_times: list[float] = []
_last_reply_time: float = 0.0
_last_account: str = ""


# ── Persistence ──────────────────────────────────────────────────────────────

def _load_replied_uris() -> set[str]:
    if not os.path.exists(_REPLIED_FILE):
        return set()
    try:
        with open(_REPLIED_FILE) as f:
            data = json.load(f)
        return set(data) if isinstance(data, list) else set()
    except (json.JSONDecodeError, OSError):
        return set()


def _save_replied_uris(uris: set[str]) -> None:
    uri_list = sorted(uris)[-_MAX_STORED_IDS:]
    try:
        with open(_REPLIED_FILE, "w") as f:
            json.dump(uri_list, f)
    except OSError as exc:
        logger.warning("Failed to save replied URIs: %s", exc)


def _load_reply_times() -> None:
    global _reply_times, _last_reply_time
    if not os.path.exists(_REPLY_TIMES_FILE):
        return
    try:
        with open(_REPLY_TIMES_FILE) as f:
            data = json.load(f)
        now = time.time()
        _reply_times = [t for t in data if isinstance(t, (int, float)) and t > now - 86400]
        if _reply_times:
            _last_reply_time = max(_reply_times)
    except (json.JSONDecodeError, OSError):
        pass


def _save_reply_times() -> None:
    try:
        with open(_REPLY_TIMES_FILE, "w") as f:
            json.dump(_reply_times, f)
    except OSError as exc:
        logger.warning("Failed to save reply times: %s", exc)


def _load_account_counts() -> dict[str, list[float]]:
    """Load {handle: [timestamp, ...]} for per-account rate limiting."""
    if not os.path.exists(_ACCOUNT_COUNTS_FILE):
        return {}
    try:
        with open(_ACCOUNT_COUNTS_FILE) as f:
            data = json.load(f)
        if not isinstance(data, dict):
            return {}
        now = time.time()
        return {
            h: [t for t in ts if isinstance(t, (int, float)) and t > now - 86400]
            for h, ts in data.items()
        }
    except (json.JSONDecodeError, OSError):
        return {}


def _save_account_counts(counts: dict[str, list[float]]) -> None:
    try:
        with open(_ACCOUNT_COUNTS_FILE, "w") as f:
            json.dump(counts, f)
    except OSError as exc:
        logger.warning("Failed to save account counts: %s", exc)


# ── Rate limiting ────────────────────────────────────────────────────────────

def _today_start_utc() -> float:
    """Epoch for the start of the current UK calendar day."""
    try:
        from zoneinfo import ZoneInfo
        tz = ZoneInfo("Europe/London")
    except Exception:
        from datetime import timezone as _tz
        tz = _tz.utc
    now_local = datetime.now(tz)
    start_local = now_local.replace(hour=0, minute=0, second=0, microsecond=0)
    return start_local.timestamp()


def _can_reply() -> bool:
    now = time.time()
    today_start = _today_start_utc()
    today = [t for t in _reply_times if t >= today_start]
    if len(today) >= MAX_REPLIES_PER_DAY:
        logger.info("[BSKY] Daily cap (%d) reached — resets at UK midnight",
                    MAX_REPLIES_PER_DAY)
        return False
    last_hour = [t for t in _reply_times if t > now - 3600]
    if len(last_hour) >= MAX_REPLIES_PER_HOUR:
        logger.info("[BSKY] Hourly cap (%d) reached", MAX_REPLIES_PER_HOUR)
        return False
    if _last_reply_time > 0 and (now - _last_reply_time) < MIN_GAP_SECONDS:
        left = int(MIN_GAP_SECONDS - (now - _last_reply_time))
        logger.info("[BSKY] Min gap — %ds left", left)
        return False
    return True


def _can_reply_to_account(handle: str, counts: dict[str, list[float]]) -> bool:
    """Per-account daily cap — forces rotation instead of one account
    monopolising the reply slots."""
    now = time.time()
    recent = [t for t in counts.get(handle, []) if t > now - 86400]
    if len(recent) >= MAX_REPLIES_PER_ACCOUNT_PER_DAY:
        logger.info("[BSKY] Skip @%s — per-account cap (%d) reached",
                    handle, MAX_REPLIES_PER_ACCOUNT_PER_DAY)
        return False
    return True


def _record_reply(handle: str) -> None:
    global _last_reply_time, _last_account
    _reply_times.append(time.time())
    _last_reply_time = time.time()
    _last_account = handle
    _save_reply_times()


# ── Candidate selection ──────────────────────────────────────────────────────

def _extract_candidate(feed_item: dict) -> dict | None:
    """Pull the fields we need from a feed item. Returns None for reposts /
    reply-to-someone-else (we want the account's own original posts)."""
    try:
        post = feed_item.get("post") or {}
        record = post.get("record") or {}
        # Skip replies — we want original posts, not thread replies
        if record.get("reply"):
            return None
        text = record.get("text") or ""
        if len(text.split()) < MIN_WORDS:
            return None
        created_at = record.get("createdAt")
        uri = post.get("uri")
        cid = post.get("cid")
        if not uri or not cid:
            return None
        return {
            "uri": uri,
            "cid": cid,
            "text": text,
            "created_at": created_at,
        }
    except Exception:
        return None


def _find_and_reply(handle: str, replied_uris: set[str]) -> bool:
    logger.info("[BSKY] Checking @%s", handle)
    feed = bluesky_client.get_author_feed(handle, limit=15)
    if not feed:
        logger.info("[BSKY] No feed / handle unreachable: @%s", handle)
        return False

    now_utc = datetime.now(timezone.utc)
    max_age = timedelta(hours=MAX_AGE_HOURS)

    for item in feed:
        candidate = _extract_candidate(item)
        if candidate is None:
            continue
        if candidate["uri"] in replied_uris:
            continue

        # Age filter
        try:
            created = datetime.fromisoformat(candidate["created_at"].replace("Z", "+00:00"))
            age = now_utc - created
            if age > max_age:
                continue
            age_hours = age.total_seconds() / 3600
        except Exception:
            age_hours = -1

        text_snippet = candidate["text"][:80].replace("\n", " ")
        logger.info("[BSKY] Candidate @%s (%.1fh): %s", handle, age_hours, text_snippet)

        # Generate reply
        reply_text = ai_writer.generate_reply(candidate["text"])
        if not reply_text:
            logger.warning("[BSKY] generate_reply returned empty — skipping")
            continue

        logger.info("[BSKY] Reply: %.100s", reply_text)

        # Post it
        ok = bluesky_client.post_reply(
            text=reply_text,
            parent_uri=candidate["uri"],
            parent_cid=candidate["cid"],
        )
        if ok:
            replied_uris.add(candidate["uri"])
            _save_replied_uris(replied_uris)
            _record_reply(handle)
            logger.info("[BSKY] Successfully replied to @%s", handle)
            return True
        logger.warning("[BSKY] post_reply returned False for @%s", handle)

    logger.info("[BSKY] No eligible candidates on @%s", handle)
    return False


# ── Main ─────────────────────────────────────────────────────────────────────

def run_once() -> None:
    _load_reply_times()
    if not _can_reply():
        return

    accounts = list(config.BLUESKY_REPLY_ACCOUNTS)
    if not accounts:
        logger.info("[BSKY] No BLUESKY_REPLY_ACCOUNTS configured")
        return

    account_counts = _load_account_counts()
    # Filter out accounts already at their per-day cap
    accounts = [a for a in accounts if _can_reply_to_account(a, account_counts)]
    if not accounts:
        logger.info("[BSKY] All target accounts at per-day cap — skipping cycle")
        return
    # Skip the last account we replied to so we rotate
    accounts = [a for a in accounts if a != _last_account]
    random.shuffle(accounts)

    replied_uris = _load_replied_uris()
    # Try up to 5 accounts per cycle — more than that and we'd hit the gap anyway
    for handle in accounts[:5]:
        if _find_and_reply(handle, replied_uris):
            # Record the per-account hit too
            account_counts.setdefault(handle, []).append(time.time())
            _save_account_counts(account_counts)
            return
        time.sleep(random.uniform(2, 5))


def run_loop(interval_minutes: int = 20) -> None:
    logger.info("[BSKY] Starting reply loop (~every %d min)", interval_minutes)
    while True:
        try:
            run_once()
        except Exception as exc:
            logger.error("[BSKY] Loop error: %s", exc)
        jitter = interval_minutes * 0.3
        wait = random.uniform(
            (interval_minutes - jitter) * 60,
            (interval_minutes + jitter) * 60,
        )
        logger.info("[BSKY] Next cycle in %.0fs", wait)
        time.sleep(wait)


if __name__ == "__main__":
    if "--loop" in sys.argv:
        run_loop()
    else:
        run_once()
