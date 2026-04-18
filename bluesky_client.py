"""
Bluesky client — posts skeets (and threads) to the configured account.

Uses the raw AT Protocol HTTP API via `requests` (no atproto library dep).
Session is cached per-process with lazy refresh on 401. Every call is wrapped
so a Bluesky failure never crashes the main bot.
"""
from __future__ import annotations

import datetime
import logging
import mimetypes
import os
import threading
import time

import requests

import config

logger = logging.getLogger(__name__)

_API_BASE = "https://bsky.social/xrpc"
_session_lock = threading.Lock()
_session: dict | None = None  # {"access": str, "refresh": str, "did": str, "created_at": float}
_SESSION_MAX_AGE = 90 * 60  # refresh proactively every 90 min (JWT lasts ~2h)


def _enabled() -> bool:
    return bool(
        config.BLUESKY_ENABLED
        and config.BLUESKY_HANDLE
        and config.BLUESKY_APP_PASSWORD
    )


def _login() -> dict | None:
    """Create a new Bluesky session. Returns session dict or None on failure."""
    try:
        resp = requests.post(
            f"{_API_BASE}/com.atproto.server.createSession",
            json={
                "identifier": config.BLUESKY_HANDLE,
                "password": config.BLUESKY_APP_PASSWORD,
            },
            timeout=15,
        )
        if resp.status_code != 200:
            logger.warning("Bluesky login failed: %d %s", resp.status_code, resp.text[:200])
            return None
        data = resp.json()
        logger.info("Bluesky session created for %s", data.get("handle"))
        return {
            "access": data["accessJwt"],
            "refresh": data["refreshJwt"],
            "did": data["did"],
            "created_at": time.time(),
        }
    except Exception as exc:
        logger.warning("Bluesky login exception: %s", exc)
        return None


def _get_session(force_refresh: bool = False) -> dict | None:
    """Return a valid session, refreshing if forced, missing, or >90 min old."""
    global _session
    with _session_lock:
        stale = (
            _session is not None
            and (time.time() - _session.get("created_at", 0)) > _SESSION_MAX_AGE
        )
        if _session is None or force_refresh or stale:
            if stale:
                logger.info("Bluesky session stale (>%dmin) — refreshing",
                            _SESSION_MAX_AGE // 60)
            _session = _login()
        return _session


def _upload_image(image_path: str, session: dict) -> dict | None:
    """Upload an image blob. Returns blob reference dict or None.

    Retries once with a fresh session if the first attempt returns 401
    (JWT expired), since overnight idle gaps can outlive the ~2h token life.
    """
    try:
        with open(image_path, "rb") as f:
            data = f.read()
        mime = mimetypes.guess_type(image_path)[0] or "image/png"
        if len(data) > 950_000:
            logger.warning("Bluesky: image too large (%d bytes), skipping", len(data))
            return None

        def _do_upload(sess: dict) -> requests.Response:
            return requests.post(
                f"{_API_BASE}/com.atproto.repo.uploadBlob",
                headers={
                    "Authorization": f"Bearer {sess['access']}",
                    "Content-Type": mime,
                },
                data=data,
                timeout=30,
            )

        resp = _do_upload(session)
        if resp.status_code == 401:
            logger.info("Bluesky blob upload 401 — refreshing session and retrying")
            new_sess = _get_session(force_refresh=True)
            if new_sess is None:
                return None
            resp = _do_upload(new_sess)

        if resp.status_code != 200:
            logger.warning("Bluesky blob upload failed: %d %s", resp.status_code, resp.text[:200])
            return None
        return resp.json().get("blob")
    except Exception as exc:
        logger.warning("Bluesky blob upload exception: %s", exc)
        return None


def _create_post(
    text: str,
    session: dict,
    image_blob: dict | None = None,
    reply_to: dict | None = None,
) -> dict | None:
    """Create a post record. Returns {"uri","cid"} or None."""
    record: dict = {
        "$type": "app.bsky.feed.post",
        "text": text,
        "createdAt": datetime.datetime.now(datetime.timezone.utc).isoformat().replace("+00:00", "Z"),
    }
    if image_blob:
        record["embed"] = {
            "$type": "app.bsky.embed.images",
            "images": [{"alt": "", "image": image_blob}],
        }
    if reply_to:
        record["reply"] = reply_to

    try:
        resp = requests.post(
            f"{_API_BASE}/com.atproto.repo.createRecord",
            headers={"Authorization": f"Bearer {session['access']}"},
            json={
                "repo": session["did"],
                "collection": "app.bsky.feed.post",
                "record": record,
            },
            timeout=15,
        )
        if resp.status_code == 401:
            # Token expired — retry once with fresh session
            new_sess = _get_session(force_refresh=True)
            if new_sess is None:
                return None
            resp = requests.post(
                f"{_API_BASE}/com.atproto.repo.createRecord",
                headers={"Authorization": f"Bearer {new_sess['access']}"},
                json={
                    "repo": new_sess["did"],
                    "collection": "app.bsky.feed.post",
                    "record": record,
                },
                timeout=15,
            )
        if resp.status_code != 200:
            logger.warning("Bluesky createRecord failed: %d %s", resp.status_code, resp.text[:200])
            return None
        data = resp.json()
        return {"uri": data["uri"], "cid": data["cid"]}
    except Exception as exc:
        logger.warning("Bluesky createRecord exception: %s", exc)
        return None


def _truncate_for_bsky(text: str, limit: int = 300) -> str:
    """Bluesky caps posts at 300 graphemes. Simple char-count truncation is
    safe for our ASCII-heavy tweets; long posts are rare."""
    if len(text) <= limit:
        return text
    return text[: limit - 1].rsplit(" ", 1)[0] + "…"


def post_skeet(text: str, image_path: str | None = None) -> bool:
    """Post a single skeet with optional image. Returns True on success."""
    if not _enabled():
        logger.debug("Bluesky disabled (check BLUESKY_ENABLED/HANDLE/APP_PASSWORD)")
        return False
    text = _truncate_for_bsky(text)
    session = _get_session()
    if session is None:
        logger.warning("Bluesky skeet skipped — could not obtain session")
        return False

    image_blob = None
    if image_path and os.path.isfile(image_path):
        image_blob = _upload_image(image_path, session)
        if image_blob is None:
            logger.warning("Bluesky image upload returned None — posting text-only")

    result = _create_post(text, session, image_blob=image_blob)
    if result:
        logger.info("Bluesky skeet posted: %.60s", text)
        return True
    logger.warning("Bluesky skeet failed — _create_post returned None: %.60s", text)
    return False


def post_thread(tweets: list[str], first_image_path: str | None = None) -> bool:
    """Post a list of tweets as a proper Bluesky thread (each replies to the
    previous). First tweet gets the optional image. Returns True if all posted."""
    if not _enabled() or not tweets:
        return False
    session = _get_session()
    if session is None:
        return False

    first_blob = None
    if first_image_path and os.path.isfile(first_image_path):
        first_blob = _upload_image(first_image_path, session)

    root: dict | None = None   # {"uri","cid"} of the first post
    parent: dict | None = None # {"uri","cid"} of the previous post

    for i, text in enumerate(tweets):
        text = _truncate_for_bsky(text)
        reply_to = None
        if root and parent:
            reply_to = {"root": root, "parent": parent}
        image_blob = first_blob if i == 0 else None
        result = _create_post(text, session, image_blob=image_blob, reply_to=reply_to)
        if result is None:
            logger.warning("Bluesky thread failed on tweet %d/%d", i + 1, len(tweets))
            return False
        if root is None:
            root = result
        parent = result

    logger.info("Bluesky thread posted (%d tweets)", len(tweets))
    return True
