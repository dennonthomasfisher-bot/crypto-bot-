#!/usr/bin/env python3
"""CryptoVault health monitor — runs every 5 minutes via launchd.

Checks the bot is alive, posting, and not in an error spike, and sends
Telegram alerts when something looks wrong. All alerts go to the Telegram
channel with a ⚠️ prefix so they're visually distinct from real posts.

Run manually with `python3 health_check.py` for a one-off check.
"""
from __future__ import annotations

import datetime
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover
    ZoneInfo = None

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

import config  # noqa: E402
import telegram_client  # noqa: E402

LONDON = ZoneInfo("Europe/London") if ZoneInfo else None
BOT_LOG = SCRIPT_DIR / "bot.log"
PID_FILE = SCRIPT_DIR / "bot.pid"
HEALTH_STATE = SCRIPT_DIR / ".health_state.json"
START_SCRIPT = SCRIPT_DIR / "start_bot.sh"

QUIET_START_HOUR = 0      # midnight UK — aligns with bot.py
QUIET_END_HOUR = 7        # 7am UK
NO_POST_THRESHOLD_SECS = 2 * 3600
ERROR_SPIKE_THRESHOLD = 20
DAILY_SUMMARY_HOUR_UK = 21


def _now_uk() -> datetime.datetime:
    return datetime.datetime.now(LONDON) if LONDON else datetime.datetime.now()


def _is_quiet_hours() -> bool:
    return QUIET_START_HOUR <= _now_uk().hour < QUIET_END_HOUR


def _read_state() -> dict:
    if not HEALTH_STATE.exists():
        return {}
    try:
        return json.loads(HEALTH_STATE.read_text())
    except Exception:
        return {}


def _write_state(state: dict) -> None:
    try:
        HEALTH_STATE.write_text(json.dumps(state))
    except Exception as exc:
        print(f"[health] state write failed: {exc}", file=sys.stderr)


def _alert(message: str) -> None:
    text = f"\u26a0\ufe0f CryptoVault Health\n\n{message}"
    try:
        telegram_client.send_telegram(text)
    except Exception as exc:
        print(f"[health] telegram alert failed: {exc}", file=sys.stderr)


def _check_bot_alive() -> bool:
    if not PID_FILE.exists():
        return False
    try:
        pid = int(PID_FILE.read_text().strip())
        os.kill(pid, 0)
        return True
    except (OSError, ValueError):
        return False


def _restart_bot() -> None:
    try:
        subprocess.run(
            ["bash", str(START_SCRIPT)],
            timeout=30,
            check=False,
            capture_output=True,
        )
    except Exception as exc:
        print(f"[health] restart subprocess failed: {exc}", file=sys.stderr)


_TS_RE = re.compile(r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}),\d+\s+(\S+)\s+(\S+)\s+(.*)$")


def _parse_line(line: str):
    m = _TS_RE.match(line)
    if not m:
        return None
    try:
        ts = datetime.datetime.strptime(m.group(1), "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return None
    if LONDON:
        ts = ts.replace(tzinfo=LONDON)
    return ts, m.group(2), m.group(3), m.group(4)


def _tail_lines(n: int) -> list[str]:
    if not BOT_LOG.exists():
        return []
    try:
        out = subprocess.check_output(["tail", "-n", str(n), str(BOT_LOG)], text=True)
        return out.splitlines()
    except Exception:
        return []


def _last_post_age_seconds() -> float | None:
    lines = _tail_lines(2000)
    last_ts = None
    for line in lines:
        parsed = _parse_line(line)
        if not parsed:
            continue
        ts, level, logger_name, msg = parsed
        if level == "INFO" and logger_name == "bot" and msg.startswith("Posted ["):
            last_ts = ts
    if last_ts is None:
        return None
    return (_now_uk() - last_ts).total_seconds()


def _recent_warning_error_stats(window_secs: int = 300) -> tuple[int, list[str]]:
    lines = _tail_lines(2000)
    cutoff = _now_uk() - datetime.timedelta(seconds=window_secs)
    count = 0
    msgs: dict[str, int] = {}
    for line in lines:
        parsed = _parse_line(line)
        if not parsed:
            continue
        ts, level, _logger, msg = parsed
        if ts < cutoff:
            continue
        if level in ("WARNING", "ERROR", "CRITICAL"):
            count += 1
            key = msg[:80]
            msgs[key] = msgs.get(key, 0) + 1
    top = sorted(msgs.items(), key=lambda x: -x[1])[:3]
    return count, [f"{n}\u00d7 {m}" for m, n in top]


def _daily_counts(day_prefix: str) -> dict[str, int]:
    if not BOT_LOG.exists():
        return {}
    try:
        out = subprocess.check_output(
            ["grep", "-E", f"^{day_prefix}", str(BOT_LOG)],
            text=True,
        )
    except subprocess.CalledProcessError:
        return {}
    posted = blocked = errors = warnings = 0
    for line in out.splitlines():
        if " INFO " in line and "Posted [" in line:
            posted += 1
        elif "Daily tweet cap" in line and "reached" in line:
            blocked += 1
        elif " ERROR " in line or " CRITICAL " in line:
            errors += 1
        elif " WARNING " in line:
            warnings += 1
    return {
        "posted": posted,
        "blocked": blocked,
        "warnings": warnings,
        "errors": errors,
    }


def _maybe_daily_summary(state: dict) -> None:
    now = _now_uk()
    today = now.strftime("%Y-%m-%d")
    if state.get("last_summary_day") == today:
        return
    if now.hour != DAILY_SUMMARY_HOUR_UK:
        return
    counts = _daily_counts(today)
    msg = (
        f"Daily summary ({today})\n\n"
        f"\u2022 Posted: {counts.get('posted', 0)}\n"
        f"\u2022 Blocked by cap: {counts.get('blocked', 0)}\n"
        f"\u2022 Warnings: {counts.get('warnings', 0)}\n"
        f"\u2022 Errors: {counts.get('errors', 0)}"
    )
    _alert(msg)
    state["last_summary_day"] = today


def main() -> int:
    state = _read_state()
    now_epoch = time.time()

    # 1. Liveness — alert + auto-restart if dead
    if not _check_bot_alive():
        last = state.get("last_dead_alert", 0.0)
        if now_epoch - last > 900:
            _alert("Bot process not found. Attempting restart.")
            state["last_dead_alert"] = now_epoch
        _restart_bot()
        _write_state(state)
        return 0

    # 2. Posting heartbeat — alert if silent for 2h+ outside quiet hours
    if not _is_quiet_hours():
        age = _last_post_age_seconds()
        if age is None or age > NO_POST_THRESHOLD_SECS:
            last = state.get("last_idle_alert", 0.0)
            if now_epoch - last > 3600:
                readable = "never" if age is None else f"{int(age // 60)} min ago"
                _alert(
                    f"Bot is alive but last post was {readable}.\n"
                    f"Likely: daily cap reached, all cooldowns active, or no triggers fired."
                )
                state["last_idle_alert"] = now_epoch

    # 3. Error spike — alert if > 20 WARNING/ERROR in last 5 min
    count, top = _recent_warning_error_stats()
    if count > ERROR_SPIKE_THRESHOLD:
        last = state.get("last_error_alert", 0.0)
        if now_epoch - last > 1800:
            top_str = "\n".join(f"  {t}" for t in top) if top else "(none parsed)"
            _alert(f"Error spike: {count} WARNING/ERROR in last 5 min.\nTop:\n{top_str}")
            state["last_error_alert"] = now_epoch

    # 4. Daily summary at 21:00 UK
    _maybe_daily_summary(state)

    _write_state(state)
    return 0


if __name__ == "__main__":
    sys.exit(main())
