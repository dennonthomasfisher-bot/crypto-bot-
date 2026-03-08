#!/usr/bin/env python3
"""
Reply Generator — lightweight Flask web app.
Open in browser, paste a tweet, get AI-generated replies to copy.

Usage:
    python reply_generator_app.py

Then open http://localhost:5050 in your browser.
"""
from __future__ import annotations

import os
import re
import json
import logging
from flask import Flask, request, jsonify, Response

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)

# ── Anthropic client ──────────────────────────────────────────────────────────

ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")

def _call_claude(system: str, prompt: str) -> str | None:
    try:
        import anthropic
        client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
        resp = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=200,
            system=system,
            messages=[{"role": "user", "content": prompt}],
        )
        return resp.content[0].text.strip().strip('"').strip("'")
    except Exception as exc:
        logger.error("Claude error: %s", exc)
        return None


def _get_btc_price() -> str:
    try:
        import requests
        r = requests.get(
            "https://api.coingecko.com/api/v3/simple/price",
            params={"ids": "bitcoin", "vs_currencies": "usd",
                    "include_24hr_change": "true"},
            timeout=5,
        )
        data = r.json().get("bitcoin", {})
        price = data.get("usd", 0)
        pct = data.get("usd_24h_change", 0)
        return f"BTC: ${price:,.0f} ({pct:+.1f}% 24h)"
    except Exception:
        return ""


def generate_reply(tweet_text: str, style: str = "conversational") -> str | None:
    market = _get_btc_price()

    styles = {
        "conversational": (
            "Reply like a trader chatting with a friend. Add a data point or "
            "angle they missed. Agree and build on their point."
        ),
        "contrarian": (
            "Push back respectfully. Take the opposite side of their argument. "
            "Be bold but back it up with data or logic."
        ),
        "data": (
            "Reply with pure data — a stat, a level, a percentage that adds "
            "context to what they said. Minimal opinion, max information."
        ),
        "question": (
            "Reply with a thought-provoking question that challenges their "
            "take or digs deeper. Make them want to respond."
        ),
    }

    system = (
        "You are @CoinWatchAlert replying to a crypto tweet. "
        "You sound like a real trader — not a bot.\n\n"
        "RULES:\n"
        "- Max 220 characters\n"
        "- NO hashtags, NO emojis (except 🟢 🔴), NO exclamation marks\n"
        "- NO 'NFA', 'DYOR', 'great point', sycophantic filler\n"
        "- Use contractions — sound human\n"
        "- Do NOT wrap response in quotes\n\n"
        f"STYLE: {styles.get(style, styles['conversational'])}"
    )

    prompt = (
        f"Tweet:\n\"{tweet_text[:300]}\"\n\n"
        f"Market: {market}\n\n"
        "Write the reply. Nothing else."
    )

    return _call_claude(system, prompt)


# ── HTML page ─────────────────────────────────────────────────────────────────

HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Reply Generator</title>
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body {
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
    background: #0a0a0a; color: #e7e9ea; min-height: 100vh;
    padding: 20px; max-width: 600px; margin: 0 auto;
  }
  h1 { font-size: 20px; margin-bottom: 20px; color: #fff; }
  textarea {
    width: 100%; background: #16181c; border: 1px solid #2f3336;
    border-radius: 12px; color: #e7e9ea; padding: 14px; font-size: 15px;
    resize: vertical; min-height: 100px; font-family: inherit;
  }
  textarea:focus { outline: none; border-color: #1d9bf0; }
  .styles { display: flex; gap: 8px; margin: 12px 0; flex-wrap: wrap; }
  .style-btn {
    padding: 8px 16px; border-radius: 20px; border: 1px solid #2f3336;
    background: #16181c; color: #8b98a5; cursor: pointer; font-size: 13px;
    transition: all 0.2s;
  }
  .style-btn:hover { border-color: #1d9bf0; color: #1d9bf0; }
  .style-btn.active { background: #1d9bf0; color: #fff; border-color: #1d9bf0; }
  .gen-btn {
    width: 100%; padding: 12px; border-radius: 20px; border: none;
    background: #1d9bf0; color: #fff; font-size: 15px; font-weight: 700;
    cursor: pointer; margin: 12px 0; transition: background 0.2s;
  }
  .gen-btn:hover { background: #1a8cd8; }
  .gen-btn:disabled { background: #2f3336; color: #555; cursor: not-allowed; }
  .reply-card {
    background: #16181c; border: 1px solid #2f3336; border-radius: 12px;
    padding: 14px; margin: 8px 0; position: relative;
  }
  .reply-text { font-size: 15px; line-height: 1.5; white-space: pre-wrap; }
  .reply-meta { color: #555; font-size: 12px; margin-top: 8px; }
  .copy-btn {
    position: absolute; top: 10px; right: 10px; background: #2f3336;
    border: none; color: #8b98a5; padding: 6px 12px; border-radius: 16px;
    font-size: 12px; cursor: pointer;
  }
  .copy-btn:hover { background: #3a3d41; color: #fff; }
  .copied { background: #00ba7c !important; color: #fff !important; }
  .spinner { display: none; text-align: center; padding: 20px; color: #555; }
  .replies-area { margin-top: 16px; }
  .count { color: #555; font-size: 13px; text-align: center; margin: 8px 0; }
</style>
</head>
<body>
<h1>Reply Generator</h1>

<textarea id="tweet" placeholder="Paste the tweet you want to reply to..."></textarea>

<div class="styles">
  <button class="style-btn active" data-style="conversational">Conversational</button>
  <button class="style-btn" data-style="contrarian">Contrarian</button>
  <button class="style-btn" data-style="data">Data-driven</button>
  <button class="style-btn" data-style="question">Question</button>
</div>

<button class="gen-btn" id="generate">Generate Replies</button>

<div class="spinner" id="spinner">Generating...</div>
<div class="replies-area" id="replies"></div>

<script>
let style = 'conversational';

document.querySelectorAll('.style-btn').forEach(btn => {
  btn.addEventListener('click', () => {
    document.querySelectorAll('.style-btn').forEach(b => b.classList.remove('active'));
    btn.classList.add('active');
    style = btn.dataset.style;
  });
});

document.getElementById('generate').addEventListener('click', async () => {
  const tweet = document.getElementById('tweet').value.trim();
  if (!tweet) return;

  const btn = document.getElementById('generate');
  const spinner = document.getElementById('spinner');
  const repliesDiv = document.getElementById('replies');

  btn.disabled = true;
  spinner.style.display = 'block';
  repliesDiv.innerHTML = '';

  // Generate 3 replies in parallel
  try {
    const promises = [0, 1, 2].map(() =>
      fetch('/generate', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ tweet, style })
      }).then(r => r.json())
    );

    const results = await Promise.all(promises);
    const replies = results.filter(r => r.reply).map(r => r.reply);

    if (replies.length === 0) {
      repliesDiv.innerHTML = '<p style="color:#555;text-align:center">Failed to generate. Check API key.</p>';
    } else {
      repliesDiv.innerHTML = replies.map((r, i) => `
        <div class="reply-card">
          <button class="copy-btn" onclick="copyReply(this, ${i})">Copy</button>
          <div class="reply-text" id="reply-${i}">${escapeHtml(r)}</div>
          <div class="reply-meta">${r.length} chars</div>
        </div>
      `).join('');
    }
  } catch (err) {
    repliesDiv.innerHTML = '<p style="color:#f44;text-align:center">Error: ' + err.message + '</p>';
  }

  btn.disabled = false;
  spinner.style.display = 'none';
});

function copyReply(btn, idx) {
  const text = document.getElementById('reply-' + idx).innerText;
  navigator.clipboard.writeText(text);
  btn.textContent = 'Copied';
  btn.classList.add('copied');
  setTimeout(() => { btn.textContent = 'Copy'; btn.classList.remove('copied'); }, 1500);
}

function escapeHtml(str) {
  const div = document.createElement('div');
  div.textContent = str;
  return div.innerHTML;
}

// Allow Ctrl+Enter to generate
document.getElementById('tweet').addEventListener('keydown', e => {
  if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) {
    document.getElementById('generate').click();
  }
});
</script>
</body>
</html>"""


# ── Routes ────────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    return Response(HTML, content_type="text/html")


@app.route("/generate", methods=["POST"])
def api_generate():
    data = request.get_json(force=True)
    tweet = data.get("tweet", "").strip()
    style = data.get("style", "conversational")
    if not tweet:
        return jsonify({"error": "No tweet text"}), 400
    reply = generate_reply(tweet, style)
    return jsonify({"reply": reply})


if __name__ == "__main__":
    if not ANTHROPIC_API_KEY:
        print("WARNING: ANTHROPIC_API_KEY not set. Load your .env first:")
        print("  export $(cat .env | grep -v '#' | xargs)")
        print()
    print("Reply Generator running at http://localhost:5050")
    app.run(host="0.0.0.0", port=5050, debug=False)
