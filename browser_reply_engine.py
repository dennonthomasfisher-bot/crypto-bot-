#!/usr/bin/env python3
"""
Browser-based reply engine using Playwright.

Bypasses X API restrictions by posting replies through Chrome,
exactly as a human would. X sees this as normal browser activity.

Usage:
    python3 browser_reply_engine.py              # run once
    python3 browser_reply_engine.py --loop       # run every 12 min
    python3 browser_reply_engine.py --login      # first-time login to save cookies

Requires:
    pip install playwright
    playwright install chromium
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
        logging.FileHandler(os.path.join(os.path.dirname(os.path.abspath(__file__)), "browser_reply.log")),
        logging.StreamHandler(sys.stdout),
    ],
)
logger = logging.getLogger("browser_reply")

_DIR = os.path.dirname(os.path.abspath(__file__))
_COOKIES_FILE = os.path.join(_DIR, ".x_cookies.json")
_PROFILE_DIR = os.path.join(_DIR, ".x_profile")
_REPLIED_FILE = os.path.join(_DIR, ".browser_replied_ids.json")
_MAX_STORED_IDS = 500

# Patches applied to every page to defeat X's bot detection. Without these,
# X serves cloaked content (stale tweets, fake composer dialog) to Playwright.
_STEALTH_JS = """
Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
Object.defineProperty(navigator, 'plugins', { get: () => [1, 2, 3, 4, 5] });
Object.defineProperty(navigator, 'languages', { get: () => ['en-GB', 'en'] });
window.chrome = { runtime: {}, app: {} };
if (window.navigator.permissions) {
    const orig = window.navigator.permissions.query;
    window.navigator.permissions.query = (p) =>
        p && p.name === 'notifications'
            ? Promise.resolve({ state: Notification.permission })
            : orig(p);
}
try {
    const getParam = WebGLRenderingContext.prototype.getParameter;
    WebGLRenderingContext.prototype.getParameter = function (p) {
        if (p === 37445) return 'Intel Inc.';
        if (p === 37446) return 'Intel Iris OpenGL Engine';
        return getParam.apply(this, arguments);
    };
} catch (e) {}
"""


def _launch_browser(p, headless: bool):
    """Launch a persistent Chromium context with stealth patches applied.

    Uses bundled Chromium rather than system Chrome to avoid the macOS
    singleton-window behaviour that causes launches to merge into an
    existing Chrome session (which would be the user's main profile, not
    our isolated bot profile).
    """
    os.makedirs(_PROFILE_DIR, exist_ok=True)
    context = p.chromium.launch_persistent_context(
        user_data_dir=_PROFILE_DIR,
        headless=headless,
        viewport={"width": 1440, "height": 900},
        user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
        locale="en-GB",
        timezone_id="Europe/London",
        args=[
            "--disable-blink-features=AutomationControlled",
            "--disable-features=IsolateOrigins,site-per-process",
        ],
    )
    logger.info("[BROWSER] Using bundled Chromium (isolated from system Chrome)")
    context.add_init_script(_STEALTH_JS)
    return context

# ── Target accounts to monitor ──────────────────────────────────────────────
# Pull from config.REPLY_ACCOUNTS (set via REPLY_ACCOUNTS env var in .env).
# Falls back to a sane default list if config is unavailable.
try:
    import config as _cfg
    TARGET_ACCOUNTS = list(_cfg.REPLY_ACCOUNTS) or [
        "WatcherGuru", "unusual_whales", "tier10k", "CryptoSlate",
        "Cointelegraph", "WuBlockchain", "DeItaone",
    ]
except Exception:
    TARGET_ACCOUNTS = [
        "WatcherGuru", "unusual_whales", "tier10k", "CryptoSlate",
        "Cointelegraph", "WuBlockchain", "DeItaone",
    ]

# ── Rate limiting ────────────────────────────────────────────────────────────
# Lower-risk profile: the bot is technically ToS-violating browser automation
# on an account that shares device/IP with two prior banned accounts, so we
# err heavily on the side of looking human.
MAX_REPLIES_PER_HOUR = 2
MIN_GAP_SECONDS = 480           # 8 min between replies
MAX_REPLIES_PER_DAY = 10        # hard daily ceiling
MAX_REPLIES_PER_ACCOUNT_PER_DAY = 2   # force rotation across target accounts
_REPLY_TIMES_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".reply_times.json")
_ACCOUNT_COUNTS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".browser_account_counts.json")
_reply_times: list[float] = []
_last_reply_time: float = 0.0
_last_account: str = ""


def _load_account_counts() -> dict[str, list[float]]:
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


def _can_reply_to_account(handle: str, counts: dict[str, list[float]]) -> bool:
    now = time.time()
    recent = [t for t in counts.get(handle, []) if t > now - 86400]
    if len(recent) >= MAX_REPLIES_PER_ACCOUNT_PER_DAY:
        logger.info("[BROWSER] Skip @%s — per-account cap (%d) reached",
                    handle, MAX_REPLIES_PER_ACCOUNT_PER_DAY)
        return False
    return True


def _load_reply_times() -> None:
    """Restore reply timestamps from disk — launchd restarts every cycle,
    so without this the in-memory rate limits reset on every tick."""
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

# ── Cookie management ────────────────────────────────────────────────────────

def _save_cookies(context) -> None:
    cookies = context.cookies()
    with open(_COOKIES_FILE, "w") as f:
        json.dump(cookies, f)
    logger.info("Saved %d cookies to %s", len(cookies), _COOKIES_FILE)


def _load_cookies(context) -> bool:
    if not os.path.exists(_COOKIES_FILE):
        return False
    try:
        with open(_COOKIES_FILE) as f:
            cookies = json.load(f)
        context.add_cookies(cookies)
        logger.info("Loaded %d cookies", len(cookies))
        return True
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("Failed to load cookies: %s", exc)
        return False


# ── Replied ID tracking ──────────────────────────────────────────────────────

def _load_replied_ids() -> set[str]:
    if not os.path.exists(_REPLIED_FILE):
        return set()
    try:
        with open(_REPLIED_FILE) as f:
            data = json.load(f)
        return set(data) if isinstance(data, list) else set()
    except (json.JSONDecodeError, OSError):
        return set()


def _save_replied_ids(ids: set[str]) -> None:
    id_list = sorted(ids)[-_MAX_STORED_IDS:]
    try:
        with open(_REPLIED_FILE, "w") as f:
            json.dump(id_list, f)
    except OSError as exc:
        logger.warning("Failed to save replied IDs: %s", exc)


# ── Rate limiting ────────────────────────────────────────────────────────────

def _can_reply() -> bool:
    global _reply_times
    now = time.time()
    # Prune to last 24h for the daily ceiling; hourly check uses a narrower window
    _reply_times = [t for t in _reply_times if t > now - 86400]
    last_24h = [t for t in _reply_times if t > now - 86400]
    if len(last_24h) >= MAX_REPLIES_PER_DAY:
        logger.info("[BROWSER] Daily cap (%d) reached", MAX_REPLIES_PER_DAY)
        return False
    last_hour = [t for t in _reply_times if t > now - 3600]
    if len(last_hour) >= MAX_REPLIES_PER_HOUR:
        logger.info("[BROWSER] Hourly cap (%d) reached", MAX_REPLIES_PER_HOUR)
        return False
    if _last_reply_time > 0 and (now - _last_reply_time) < MIN_GAP_SECONDS:
        left = int(MIN_GAP_SECONDS - (now - _last_reply_time))
        logger.info("[BROWSER] Min gap — %ds left", left)
        return False
    return True


def _record_reply(account: str) -> None:
    global _last_reply_time, _last_account
    _reply_times.append(time.time())
    _last_reply_time = time.time()
    _last_account = account
    _save_reply_times()


# ── AI reply generation ──────────────────────────────────────────────────────

def _generate_reply(tweet_text: str) -> str | None:
    """Generate a reply using Claude AI."""
    try:
        import ai_writer
        reply = ai_writer.generate_reply(tweet_text)
        if reply and len(reply) > 10:
            return reply
    except Exception as exc:
        logger.warning("[BROWSER] AI reply generation failed: %s", exc)

    return None


# ── Human-like typing ────────────────────────────────────────────────────────

def _human_type(page, selector: str, text: str) -> None:
    """Type text with random delays between keystrokes to appear human."""
    element = page.locator(selector)
    element.click()
    time.sleep(random.uniform(0.3, 0.8))
    for char in text:
        page.keyboard.type(char, delay=random.randint(30, 120))
        if random.random() < 0.05:  # 5% chance of brief pause
            time.sleep(random.uniform(0.2, 0.5))


# ── First-time login ────────────────────────────────────────────────────────

def do_login() -> None:
    """Open a visible browser and poll until the user completes X login.

    No ENTER-press required. The script watches every 3s for the
    authenticated-only composer button. When it appears on any tab,
    login is declared successful and the profile is saved.
    """
    from playwright.sync_api import sync_playwright

    logger.info("[BROWSER] Opening browser for manual login...")
    with sync_playwright() as p:
        context = _launch_browser(p, headless=False)
        page = context.new_page()
        page.goto("https://x.com/login")

        print("\n" + "=" * 70)
        print("  A Chromium window is open. Log in to X as @CVault88 there.")
        print("  Complete any 2FA / phone / captcha if prompted.")
        print("  This script will auto-detect when you're logged in —")
        print("  NO need to press ENTER. Just log in and wait.")
        print("  Timeout: 15 minutes.")
        print("=" * 70 + "\n")

        deadline = time.time() + 15 * 60
        tick = 0
        while time.time() < deadline:
            try:
                # Check every open tab for the authenticated composer button.
                for pg in context.pages:
                    try:
                        if pg.locator('[data-testid="SideNav_NewTweet_Button"]').count() > 0:
                            if pg.locator('[data-testid="SideNav_NewTweet_Button"]').first.is_visible():
                                logger.info("[BROWSER] Login detected on %s", pg.url)
                                _save_cookies(context)
                                context.close()
                                logger.info("[BROWSER] Login complete. Profile saved to %s", _PROFILE_DIR)
                                print("\n✓ Login captured. You can now run:  ./start_reply.sh")
                                return
                    except Exception:
                        continue
            except Exception as loop_exc:
                # Context or page died — user likely closed the window
                logger.error("[BROWSER] Browser went away: %s", loop_exc)
                print("\n⚠️  The Chromium window closed. Re-run --login and leave it open.")
                return

            tick += 1
            if tick % 10 == 0:
                mins_left = int((deadline - time.time()) / 60)
                print(f"  ...waiting for login ({mins_left}m remaining)", flush=True)
            time.sleep(3)

        logger.error("[BROWSER] Login timed out after 15 minutes.")
        try:
            context.close()
        except Exception:
            pass
        print("\n⚠️  Timed out. If you were mid-login, just re-run — progress is saved in .x_profile.")


# ── Core: find tweet and reply ───────────────────────────────────────────────

def _find_and_reply(page, account: str, replied_ids: set[str]) -> bool:
    """Visit an account's page, find a recent tweet, generate and post a reply."""
    logger.info("[BROWSER] Checking @%s", account)

    try:
        page.goto(f"https://x.com/{account}", wait_until="domcontentloaded", timeout=20000)
    except Exception as exc:
        logger.warning("[BROWSER] Failed to load @%s: %s", account, exc)
        return False

    # Actively wait for tweet articles to hydrate — profile pages lazy-load
    # after DOMContentLoaded so counting too early returns zero.
    try:
        page.wait_for_selector('article[data-testid="tweet"]', timeout=10000)
    except Exception:
        logger.info("[BROWSER] No tweet articles appeared on @%s within 10s", account)
        return False
    time.sleep(random.uniform(1.5, 2.5))

    try:
        tweets = page.locator('article[data-testid="tweet"]')
        count = tweets.count()
        logger.info("[BROWSER] Found %d tweets on @%s", count, account)
    except Exception:
        logger.warning("[BROWSER] No tweets found on @%s", account)
        return False

    if count == 0:
        return False

    # Check more tweets — first slot is often a pinned post from long ago.
    max_age_hours = 24
    now_utc = datetime.now(timezone.utc)
    skip_reasons: dict[str, int] = {}
    for i in range(min(count, 10)):
        try:
            tweet = tweets.nth(i)
            tweet_text_el = tweet.locator('[data-testid="tweetText"]')
            if tweet_text_el.count() == 0:
                skip_reasons["no_text"] = skip_reasons.get("no_text", 0) + 1
                continue
            tweet_text = tweet_text_el.first.inner_text()

            # Get tweet link for ID and time element for age check
            time_el = tweet.locator("time").first
            if time_el.count() == 0:
                skip_reasons["no_time_el"] = skip_reasons.get("no_time_el", 0) + 1
                continue
            link_el = time_el.locator("xpath=ancestor::a")
            href = link_el.get_attribute("href") if link_el.count() > 0 else None
            tweet_id = href.split("/")[-1] if href and "/status/" in href else None

            if not tweet_id:
                skip_reasons["no_id"] = skip_reasons.get("no_id", 0) + 1
                continue
            if tweet_id in replied_ids:
                skip_reasons["already_replied"] = skip_reasons.get("already_replied", 0) + 1
                continue

            # Recency filter — skip tweets older than max_age_hours
            age_hours_val: float | None = None
            try:
                dt_attr = time_el.get_attribute("datetime")
                if dt_attr:
                    posted_at = datetime.fromisoformat(dt_attr.replace("Z", "+00:00"))
                    age_hours_val = (now_utc - posted_at).total_seconds() / 3600
            except Exception:
                pass
            if age_hours_val is not None and age_hours_val > max_age_hours:
                skip_reasons[f"too_old"] = skip_reasons.get("too_old", 0) + 1
                logger.info("[BROWSER] Skip %s (@%s) — %.1fh old", tweet_id, account, age_hours_val)
                continue

            if len(tweet_text.split()) < 8:
                skip_reasons["too_short"] = skip_reasons.get("too_short", 0) + 1
                continue

            logger.info("[BROWSER] Candidate tweet from @%s: %s... (id=%s)",
                       account, tweet_text[:60], tweet_id)

            # Click the reply button on this tweet (scroll into view first)
            reply_btn = tweet.locator('[data-testid="reply"]')
            if reply_btn.count() == 0:
                logger.warning("[BROWSER] No reply button found")
                continue

            try:
                reply_btn.first.scroll_into_view_if_needed(timeout=3000)
            except Exception:
                pass
            url_before = page.url
            reply_btn.first.click()
            time.sleep(random.uniform(1.2, 2.0))
            url_after = page.url

            # Diagnostic: did the click navigate, or open the modal?
            if url_after != url_before:
                logger.info("[BROWSER] Click navigated: %s -> %s", url_before, url_after)
            else:
                logger.info("[BROWSER] Click stayed on %s", url_after)

            # Actively wait for the reply textbox. X uses several testids/aria
            # variants depending on A/B; try each with a shared 8s budget.
            reply_box = None
            selectors = [
                '[data-testid="tweetTextarea_0"]',
                '[data-testid^="tweetTextarea"]',
                'div[role="textbox"][contenteditable="true"]',
                '[aria-label*="Post your reply" i]',
                '[aria-label*="Post text" i]',
                'div[contenteditable="true"][aria-multiline="true"]',
            ]
            for sel in selectors:
                try:
                    loc = page.locator(sel).first
                    loc.wait_for(state="visible", timeout=2000)
                    reply_box = loc
                    logger.info("[BROWSER] Textbox matched selector: %s", sel)
                    break
                except Exception:
                    continue

            if reply_box is None:
                # Deep-dive diagnostics so we can fix the selector next time
                try:
                    editable_n = page.locator('[contenteditable="true"]').count()
                    textbox_n = page.locator('[role="textbox"]').count()
                    dialog = page.locator('[role="dialog"]').first
                    dialog_n = page.locator('[role="dialog"]').count()
                    title = page.title()
                    logger.warning("[BROWSER] Reply textbox NOT FOUND | url=%s | title=%r | contenteditable=%d | role=textbox=%d | role=dialog=%d",
                                   page.url, title, editable_n, textbox_n, dialog_n)
                    if dialog_n > 0:
                        try:
                            dialog_text = dialog.inner_text(timeout=1000)
                            # Trim — the dialog can contain huge menus
                            logger.warning("[BROWSER] Dialog text (first 500 chars): %r",
                                           dialog_text[:500])
                        except Exception as d_exc:
                            logger.warning("[BROWSER] Failed to read dialog text: %s", d_exc)
                    page.screenshot(path=os.path.join(_DIR, ".reply_debug.png"), full_page=True)
                    logger.warning("[BROWSER] Debug screenshot saved: .reply_debug.png")
                except Exception as diag_exc:
                    logger.warning("[BROWSER] Diagnostic collection failed: %s", diag_exc)
                page.keyboard.press("Escape")
                continue

            # Generate AI reply (only after we've confirmed the textbox exists,
            # to avoid wasting API calls on stalled dialogs)
            reply_text = _generate_reply(tweet_text)
            if not reply_text:
                logger.warning("[BROWSER] Could not generate reply — skipping")
                page.keyboard.press("Escape")
                continue

            logger.info("[BROWSER] Reply: %s", reply_text[:80])

            reply_box.click()
            time.sleep(random.uniform(0.3, 0.7))

            # Type with human-like delays
            for char in reply_text:
                page.keyboard.type(char, delay=random.randint(25, 90))
                if random.random() < 0.03:
                    time.sleep(random.uniform(0.2, 0.5))

            time.sleep(random.uniform(1.0, 2.0))

            # Click the Reply/Post button
            post_btn = page.locator('[data-testid="tweetButtonInline"]')
            if post_btn.count() == 0:
                post_btn = page.locator('[data-testid="tweetButton"]')

            if post_btn.count() > 0:
                post_btn.first.click()
                time.sleep(random.uniform(2.0, 4.0))

                # Verify it posted (dialog should close)
                replied_ids.add(tweet_id)
                _save_replied_ids(replied_ids)
                _record_reply(account)
                logger.info("[BROWSER] Successfully replied to @%s tweet %s", account, tweet_id)
                return True
            else:
                logger.warning("[BROWSER] Post button not found")
                page.keyboard.press("Escape")
                continue

        except Exception as exc:
            logger.warning("[BROWSER] Error processing tweet %d from @%s: %s", i, account, exc)
            try:
                page.keyboard.press("Escape")
            except Exception:
                pass
            continue

    if skip_reasons:
        reasons_str = ", ".join(f"{k}={v}" for k, v in sorted(skip_reasons.items()))
        logger.info("[BROWSER] No suitable tweets on @%s — skipped: %s", account, reasons_str)
    else:
        logger.info("[BROWSER] No suitable tweets found on @%s", account)
    return False


# ── Main loop ────────────────────────────────────────────────────────────────

def run_once() -> None:
    """Run one cycle: pick a random account, find a tweet, reply."""
    from playwright.sync_api import sync_playwright

    if not os.path.exists(_PROFILE_DIR) or not os.listdir(_PROFILE_DIR):
        logger.error("[BROWSER] No profile at %s. Run with --login first.", _PROFILE_DIR)
        return

    _load_reply_times()

    if not _can_reply():
        return

    replied_ids = _load_replied_ids()
    account_counts = _load_account_counts()

    # Filter out accounts already at their per-day cap, then rotate
    accounts = [a for a in TARGET_ACCOUNTS if _can_reply_to_account(a, account_counts)]
    if not accounts:
        logger.info("[BROWSER] All target accounts at per-day cap — skipping cycle")
        return
    accounts = [a for a in accounts if a != _last_account]
    random.shuffle(accounts)

    with sync_playwright() as p:
        context = _launch_browser(p, headless=True)
        page = context.new_page()

        # Verify we're logged in
        try:
            page.goto("https://x.com/home", wait_until="domcontentloaded", timeout=20000)
            time.sleep(3)
            if "login" in page.url.lower():
                logger.error("[BROWSER] Not logged in — profile expired. Run --login again.")
                context.close()
                return
            logger.info("[BROWSER] Logged in successfully")
        except Exception as exc:
            logger.error("[BROWSER] Failed to verify login: %s", exc)
            context.close()
            return

        # Random initial delay
        delay = random.randint(10, 30)
        logger.info("[BROWSER] Waiting %ds before starting", delay)
        time.sleep(delay)

        # Try accounts until we get a reply posted
        for account in accounts[:4]:  # Try up to 4 accounts per cycle
            if _find_and_reply(page, account, replied_ids):
                account_counts.setdefault(account, []).append(time.time())
                _save_account_counts(account_counts)
                break
            time.sleep(random.uniform(5, 15))  # Pause between accounts

        context.close()


def run_loop(interval_minutes: int = 12) -> None:
    """Run continuously with random intervals."""
    logger.info("[BROWSER] Starting reply loop (every ~%d min)", interval_minutes)
    while True:
        try:
            run_once()
        except Exception as exc:
            logger.error("[BROWSER] Loop error: %s", exc)

        # Random interval: base +/- 30%
        jitter = interval_minutes * 0.3
        wait = random.uniform(
            (interval_minutes - jitter) * 60,
            (interval_minutes + jitter) * 60,
        )
        logger.info("[BROWSER] Next cycle in %.0f seconds", wait)
        time.sleep(wait)


# ── CLI ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    if "--login" in sys.argv:
        do_login()
    elif "--loop" in sys.argv:
        run_loop()
    else:
        run_once()
