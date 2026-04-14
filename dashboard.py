#!/usr/bin/env python3
"""
CryptoVault Dashboard — live bot status viewer.

Run:  python3 dashboard.py
Open: http://localhost:8888
"""
from __future__ import annotations

import json
import os
import re
import subprocess
from datetime import datetime
from http.server import HTTPServer, SimpleHTTPRequestHandler

_DIR = os.path.dirname(os.path.abspath(__file__))
_LOG_FILE = os.path.join(_DIR, "bot.log")
_STATE_FILE = os.path.join(_DIR, ".bot_state.json")
_PID_FILE = os.path.join(_DIR, "bot.pid")
_ENV_FILE = os.path.join(_DIR, ".env")
PORT = 8888


def _bot_running() -> tuple[bool, int | None]:
    if not os.path.exists(_PID_FILE):
        return False, None
    try:
        with open(_PID_FILE) as f:
            pid = int(f.read().strip())
        os.kill(pid, 0)
        return True, pid
    except (ProcessLookupError, ValueError, PermissionError):
        return False, None


def _read_state() -> dict:
    if not os.path.exists(_STATE_FILE):
        return {}
    try:
        with open(_STATE_FILE) as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return {}


def _recent_log_lines(n: int = 50) -> list[str]:
    if not os.path.exists(_LOG_FILE):
        return []
    try:
        result = subprocess.run(
            ["tail", f"-{n}", _LOG_FILE],
            capture_output=True, text=True, timeout=5,
        )
        return result.stdout.strip().split("\n") if result.stdout.strip() else []
    except Exception:
        return []


def _parse_activity(lines: list[str]) -> list[dict]:
    """Extract recent bot activity from log lines."""
    activity = []
    for line in reversed(lines):
        if "[POST] Success" in line or "Posted [" in line:
            activity.append({"type": "posted", "line": line, "class": "success"})
        elif "[POST] BLOCKED" in line:
            activity.append({"type": "blocked", "line": line, "class": "warning"})
        elif "[POST] Twitter error" in line or "401 Unauthorized" in line:
            activity.append({"type": "error", "line": line, "class": "error"})
        elif "[REPLY] Successfully posted" in line:
            activity.append({"type": "reply", "line": line, "class": "success"})
        elif "[JITTER] Waiting" in line:
            activity.append({"type": "jitter", "line": line, "class": "info"})
        elif "News approved" in line or "Price alert:" in line:
            activity.append({"type": "alert", "line": line, "class": "info"})
        elif "[CHART-IMG]" in line or "[CHART]" in line:
            activity.append({"type": "chart", "line": line, "class": "info"})
        if len(activity) >= 20:
            break
    return activity


def _get_dashboard_data() -> dict:
    running, pid = _bot_running()
    state = _read_state()
    lines = _recent_log_lines(100)
    activity = _parse_activity(lines)

    # Count today's posts from log
    today = datetime.now().strftime("%Y-%m-%d")
    posts_today = sum(1 for l in lines if today in l and ("Posted [" in l or "[POST] Success" in l))
    replies_today = sum(1 for l in lines if today in l and "[REPLY] Successfully posted" in l)
    errors_today = sum(1 for l in lines if today in l and ("401 Unauthorized" in l or "Twitter error" in l))

    return {
        "running": running,
        "pid": pid,
        "posts_today": posts_today,
        "replies_today": replies_today,
        "errors_today": errors_today,
        "monthly_tweets": state.get("monthly_tweets", 0),
        "activity": activity,
        "timestamp": datetime.now().strftime("%H:%M:%S"),
    }


_DASHBOARD_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta http-equiv="refresh" content="30">
<title>CryptoVault Dashboard</title>
<style>
* { margin: 0; padding: 0; box-sizing: border-box; }
body {
    background: #080B10;
    color: #e0e0e0;
    font-family: 'SF Mono', 'Fira Code', 'Consolas', monospace;
    padding: 20px;
}
.header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: 20px 0;
    border-bottom: 1px solid #D4AF3730;
    margin-bottom: 20px;
}
.brand { color: #D4AF37; font-size: 28px; font-weight: bold; }
.status {
    padding: 8px 16px;
    border-radius: 20px;
    font-size: 14px;
    font-weight: bold;
}
.status.online { background: #00E67620; color: #00E676; border: 1px solid #00E67640; }
.status.offline { background: #FF3D5720; color: #FF3D57; border: 1px solid #FF3D5740; }
.timestamp { color: #4A5568; font-size: 12px; }
.grid {
    display: grid;
    grid-template-columns: repeat(4, 1fr);
    gap: 16px;
    margin-bottom: 24px;
}
.card {
    background: #0D1117;
    border: 1px solid #1C2230;
    border-radius: 12px;
    padding: 20px;
}
.card-label { color: #4A5568; font-size: 11px; text-transform: uppercase; letter-spacing: 1px; }
.card-value { color: #D4AF37; font-size: 36px; font-weight: bold; margin-top: 8px; }
.card-value.green { color: #00E676; }
.card-value.red { color: #FF3D57; }
.section-title {
    color: #D4AF37;
    font-size: 16px;
    font-weight: bold;
    margin-bottom: 12px;
    padding-bottom: 8px;
    border-bottom: 1px solid #1C2230;
}
.activity {
    background: #0D1117;
    border: 1px solid #1C2230;
    border-radius: 12px;
    padding: 16px;
    max-height: 500px;
    overflow-y: auto;
}
.activity-item {
    padding: 8px 12px;
    margin-bottom: 4px;
    border-radius: 6px;
    font-size: 12px;
    line-height: 1.5;
    word-break: break-all;
}
.activity-item.success { background: #00E67608; border-left: 3px solid #00E676; }
.activity-item.error { background: #FF3D5708; border-left: 3px solid #FF3D57; }
.activity-item.warning { background: #D4AF3708; border-left: 3px solid #D4AF37; }
.activity-item.info { background: #0D1117; border-left: 3px solid #1C2230; color: #7A8290; }
.schedule {
    background: #0D1117;
    border: 1px solid #1C2230;
    border-radius: 12px;
    padding: 16px;
    margin-bottom: 24px;
}
.schedule-row {
    display: flex;
    justify-content: space-between;
    padding: 8px 0;
    border-bottom: 1px solid #1C223030;
    font-size: 13px;
}
.schedule-time { color: #D4AF37; font-weight: bold; width: 60px; }
.schedule-name { color: #e0e0e0; flex: 1; }
.schedule-desc { color: #4A5568; text-align: right; }
.two-col { display: grid; grid-template-columns: 1fr 1fr; gap: 20px; }
</style>
</head>
<body>

<div class="header">
    <div class="brand">CryptoVault</div>
    <div>
        <span class="status __STATUS_CLASS__">__STATUS_TEXT__</span>
        <span class="timestamp">&nbsp; PID: __PID__ &nbsp;|&nbsp; Updated: __TIMESTAMP__</span>
    </div>
</div>

<div class="grid">
    <div class="card">
        <div class="card-label">Posts Today</div>
        <div class="card-value">__POSTS_TODAY__</div>
    </div>
    <div class="card">
        <div class="card-label">Replies Today</div>
        <div class="card-value green">__REPLIES_TODAY__</div>
    </div>
    <div class="card">
        <div class="card-label">Errors Today</div>
        <div class="card-value red">__ERRORS_TODAY__</div>
    </div>
    <div class="card">
        <div class="card-label">Monthly Total</div>
        <div class="card-value">__MONTHLY__</div>
    </div>
</div>

<div class="two-col">
<div>
    <div class="section-title">Schedule (UK Time)</div>
    <div class="schedule">
        <div class="schedule-row"><span class="schedule-time">08:00</span><span class="schedule-name">Morning Recap</span><span class="schedule-desc">Market overview</span></div>
        <div class="schedule-row"><span class="schedule-time">12:00</span><span class="schedule-name">Opinion Tweet</span><span class="schedule-desc">Hot take / directional call</span></div>
        <div class="schedule-row"><span class="schedule-time">16:00</span><span class="schedule-name">Engagement</span><span class="schedule-desc">US market open crowd</span></div>
        <div class="schedule-row"><span class="schedule-time">19:00</span><span class="schedule-name">Evening Thread</span><span class="schedule-desc">3-tweet thread</span></div>
        <div class="schedule-row"><span class="schedule-time">21:00</span><span class="schedule-name">Fear &amp; Greed</span><span class="schedule-desc">Index gauge visual</span></div>
        <div class="schedule-row" style="margin-top:12px; border-top: 1px solid #D4AF3720; padding-top: 12px;">
            <span class="schedule-time">5m</span><span class="schedule-name">Price Alerts</span><span class="schedule-desc">3/day cap</span></div>
        <div class="schedule-row"><span class="schedule-time">15m</span><span class="schedule-name">News Check</span><span class="schedule-desc">4/day cap</span></div>
        <div class="schedule-row"><span class="schedule-time">8m</span><span class="schedule-name">Reply Engine</span><span class="schedule-desc">5/day cap (GROWTH)</span></div>
        <div class="schedule-row"><span class="schedule-time">3h</span><span class="schedule-name">Narrative</span><span class="schedule-desc">Story clusters</span></div>
    </div>
</div>
<div>
    <div class="section-title">Live Activity</div>
    <div class="activity">
__ACTIVITY_ROWS__
    </div>
</div>
</div>

</body>
</html>"""


class DashboardHandler(SimpleHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/" or self.path == "/dashboard":
            data = _get_dashboard_data()
            html = _DASHBOARD_HTML
            html = html.replace("__STATUS_CLASS__", "online" if data["running"] else "offline")
            html = html.replace("__STATUS_TEXT__", "ONLINE" if data["running"] else "OFFLINE")
            html = html.replace("__PID__", str(data["pid"] or "—"))
            html = html.replace("__TIMESTAMP__", data["timestamp"])
            html = html.replace("__POSTS_TODAY__", str(data["posts_today"]))
            html = html.replace("__REPLIES_TODAY__", str(data["replies_today"]))
            html = html.replace("__ERRORS_TODAY__", str(data["errors_today"]))
            html = html.replace("__MONTHLY__", str(data["monthly_tweets"]))

            rows = ""
            for item in data["activity"]:
                short = item["line"][-120:] if len(item["line"]) > 120 else item["line"]
                rows += f'        <div class="activity-item {item["class"]}">{short}</div>\n'
            if not rows:
                rows = '        <div class="activity-item info">No activity yet — start the bot</div>\n'
            html = html.replace("__ACTIVITY_ROWS__", rows)

            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(html.encode())
        else:
            super().do_GET()

    def log_message(self, format, *args):
        pass  # Suppress access logs


if __name__ == "__main__":
    os.chdir(_DIR)
    server = HTTPServer(("0.0.0.0", PORT), DashboardHandler)
    print(f"CryptoVault Dashboard running at http://localhost:{PORT}")
    print("Press Ctrl+C to stop")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nDashboard stopped.")
