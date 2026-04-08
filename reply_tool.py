#!/usr/bin/env python3
"""
reply_tool.py – Interactive Twitter/X reply generator for crypto content.

Workflow
────────
1. Fetch the 10 most recent tweets from four major crypto accounts.
2. Display them (account, text, likes).
3. You pick one by number.
4. Claude generates an analyst-voice reply (max 220 chars, no NFA, no !).
5. You confirm Y/N before it's posted.

Usage:
    cd ~/crypto-bot-
    python reply_tool.py
"""
from __future__ import annotations

import os
import sys
import textwrap

# ── Resolve paths ─────────────────────────────────────────────────────────────
ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)

import config as cfg

# ── Constants ─────────────────────────────────────────────────────────────────
TARGET_ACCOUNTS = ["CoinDesk", "Cointelegraph", "WatcherGuru", "BitcoinMagazine"]
TWEETS_PER_ACCT = 3          # fetch up to N per account (3 × 4 = 12 > 10 shown)
REPLY_MAX_CHARS = 220
ALLOWED_EMOJIS  = "🚀📉⚡👀"

CLAUDE_SYSTEM = f"""\
You are a professional crypto market analyst with 10 years of experience.
Write a concise, insightful reply to the tweet below.

Rules (strictly enforced):
- Maximum {REPLY_MAX_CHARS} characters (count carefully).
- No exclamation marks (!).
- Do NOT include "NFA", "DYOR", "not financial advice", or any disclaimer.
- Only use these emojis if they genuinely add value: {ALLOWED_EMOJIS}
- Write in first person, confident analyst voice.
- No hashtags unless already in the original tweet.
- Return ONLY the reply text — no quotes, no preamble.
"""

TWEET_LOG = os.path.join(ROOT, "posted_tweets.log")


# ── Dependency helpers ────────────────────────────────────────────────────────

def _require(pkg: str, import_name: str | None = None):
    """Import a package, showing a helpful error if missing."""
    try:
        import importlib
        return importlib.import_module(import_name or pkg)
    except ImportError:
        print(f"\n[ERROR] Required package '{pkg}' not installed.")
        print(f"        Run:  pip install {pkg}")
        sys.exit(1)


# ── Twitter fetch ─────────────────────────────────────────────────────────────

def _twitter_client():
    """Return a Tweepy v2 Client using credentials from config."""
    tweepy = _require("tweepy")
    missing = [k for k, v in [
        ("TWITTER_BEARER_TOKEN",        cfg.TWITTER_BEARER_TOKEN),
        ("TWITTER_API_KEY",             cfg.TWITTER_API_KEY),
        ("TWITTER_API_SECRET",          cfg.TWITTER_API_SECRET),
        ("TWITTER_ACCESS_TOKEN",        cfg.TWITTER_ACCESS_TOKEN),
        ("TWITTER_ACCESS_TOKEN_SECRET", cfg.TWITTER_ACCESS_TOKEN_SECRET),
    ] if not v]
    if missing:
        print(f"\n[ERROR] Missing Twitter credentials in .env: {', '.join(missing)}")
        sys.exit(1)
    return tweepy.Client(
        bearer_token=cfg.TWITTER_BEARER_TOKEN,
        consumer_key=cfg.TWITTER_API_KEY,
        consumer_secret=cfg.TWITTER_API_SECRET,
        access_token=cfg.TWITTER_ACCESS_TOKEN,
        access_token_secret=cfg.TWITTER_ACCESS_TOKEN_SECRET,
        wait_on_rate_limit=True,
    )


def fetch_recent_tweets() -> list[dict]:
    """
    Fetch up to TWEETS_PER_ACCT recent tweets from each TARGET_ACCOUNTS.
    Returns a list of dicts: {id, account, text, like_count, url}.
    """
    tweepy  = _require("tweepy")
    client  = _twitter_client()
    results = []

    for username in TARGET_ACCOUNTS:
        try:
            user_resp = client.get_user(
                username=username,
                user_fields=["public_metrics"],
            )
            if not user_resp.data:
                print(f"  @{username}: user not found")
                continue
            user_id = user_resp.data.id

            tweets_resp = client.get_users_tweets(
                id=user_id,
                max_results=TWEETS_PER_ACCT,
                tweet_fields=["public_metrics", "created_at"],
                exclude=["retweets", "replies"],
            )
            if not tweets_resp.data:
                continue

            for tw in tweets_resp.data:
                pm = tw.public_metrics or {}
                results.append({
                    "id":         str(tw.id),
                    "account":    username,
                    "text":       tw.text,
                    "like_count": pm.get("like_count", 0),
                    "url":        f"https://twitter.com/{username}/status/{tw.id}",
                })
        except tweepy.TweepyException as exc:
            print(f"  @{username}: Twitter error — {exc}")

    # Sort by likes descending, cap at 10
    results.sort(key=lambda t: t["like_count"], reverse=True)
    return results[:10]


# ── Claude reply generation ───────────────────────────────────────────────────

def generate_reply(tweet: dict) -> str:
    """Call Claude to generate an analyst-voice reply to tweet['text']."""
    if not cfg.ANTHROPIC_API_KEY:
        print("\n[ERROR] ANTHROPIC_API_KEY not set in .env")
        sys.exit(1)

    anthropic = _require("anthropic")
    client    = anthropic.Anthropic(api_key=cfg.ANTHROPIC_API_KEY)

    prompt = (
        f"Original tweet by @{tweet['account']}:\n"
        f"\"{tweet['text']}\"\n\n"
        f"Write your reply:"
    )

    message = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=300,
        system=CLAUDE_SYSTEM,
        messages=[{"role": "user", "content": prompt}],
    )
    reply = message.content[0].text.strip()

    # Hard-trim if Claude exceeded the limit
    if len(reply) > REPLY_MAX_CHARS:
        reply = reply[: REPLY_MAX_CHARS - 1].rsplit(" ", 1)[0] + "…"

    return reply


# ── Tweet posting ─────────────────────────────────────────────────────────────

def post_reply(tweet_id: str, text: str) -> bool:
    """Post a reply to tweet_id. Returns True on success."""
    tweepy = _require("tweepy")
    client = _twitter_client()
    try:
        resp = client.create_tweet(text=text, in_reply_to_tweet_id=tweet_id)
        new_id = resp.data["id"]
        # Persist to tweet log
        import datetime
        with open(TWEET_LOG, "a") as f:
            ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            f.write(f"[{ts}] REPLY to {tweet_id} → {new_id}: {text}\n")
        return True
    except tweepy.TweepyException as exc:
        print(f"\n[ERROR] Failed to post reply: {exc}")
        return False


# ── Display helpers ───────────────────────────────────────────────────────────

def _print_tweet(n: int, tw: dict) -> None:
    wrapped = textwrap.fill(tw["text"], width=72, subsequent_indent="              ")
    print(f"\n  [{n:2d}]  @{tw['account']:20s}  ❤ {tw['like_count']}")
    print(f"        {wrapped}")


# ── Main interactive loop ─────────────────────────────────────────────────────

def main() -> None:
    print("\n─── Crypto Reply Tool ───────────────────────────────────────────────")
    print("Fetching recent tweets from:", ", ".join(f"@{a}" for a in TARGET_ACCOUNTS))

    tweets = fetch_recent_tweets()
    if not tweets:
        print("\nNo tweets fetched. Check your Twitter credentials and network.")
        sys.exit(1)

    print(f"\nFound {len(tweets)} tweet(s):\n")
    for i, tw in enumerate(tweets, 1):
        _print_tweet(i, tw)

    # Pick a tweet
    print()
    while True:
        raw = input(f"Pick a tweet to reply to [1–{len(tweets)}], or q to quit: ").strip()
        if raw.lower() == "q":
            print("Aborted.")
            sys.exit(0)
        if raw.isdigit() and 1 <= int(raw) <= len(tweets):
            chosen = tweets[int(raw) - 1]
            break
        print(f"  Enter a number between 1 and {len(tweets)}.")

    print(f"\nGenerating reply for @{chosen['account']}…")
    reply = generate_reply(chosen)

    print(f"\n─── Generated Reply ({len(reply)} chars) ────────────────────────────────")
    print(f"\n  {reply}\n")
    print(f"─────────────────────────────────────────────────────────────────────")

    confirm = input("Post this reply? [Y/n]: ").strip().lower()
    if confirm in ("", "y", "yes"):
        ok = post_reply(chosen["id"], reply)
        if ok:
            print(f"\nPosted successfully.")
        else:
            print(f"\nPost failed — see error above.")
    else:
        print("Cancelled — reply not posted.")


if __name__ == "__main__":
    main()
