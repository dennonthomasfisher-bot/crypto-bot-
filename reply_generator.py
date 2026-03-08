#!/usr/bin/env python3
"""
Reply Generator CLI — paste a tweet URL or text, get an AI-generated reply
posted directly. Works on Free tier (only needs write access).

Usage:
    python reply_generator.py
    python reply_generator.py --dry-run     # preview without posting

Interactive mode:
    1. Paste a tweet URL (extracts tweet ID for in_reply_to)
       OR paste the tweet text directly
    2. AI generates a conversational reply
    3. You approve or regenerate before it posts
"""
from __future__ import annotations

import re
import sys
import logging

import tweepy

import config
import state
import twitter_client
import ai_writer
import tweet_generators

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger(__name__)

DRY_RUN = "--dry-run" in sys.argv


def _extract_tweet_id(url_or_id: str) -> str | None:
    """Extract tweet ID from a URL like https://x.com/user/status/123456."""
    url_or_id = url_or_id.strip()
    # Direct ID (all digits)
    if url_or_id.isdigit():
        return url_or_id
    # URL pattern
    match = re.search(r"(?:twitter\.com|x\.com)/\w+/status/(\d+)", url_or_id)
    if match:
        return match.group(1)
    return None


def _get_market_context() -> str:
    """Get current BTC price context for smarter replies."""
    try:
        btc_data = tweet_generators._get_btc_data()
        if btc_data:
            price = btc_data.get("current_price", 0)
            pct = btc_data.get("price_change_percentage_24h_in_currency") or 0
            pct_7d = btc_data.get("price_change_percentage_7d_in_currency") or 0
            return (
                f"BTC: ${price:,.0f} ({pct:+.1f}% 24h, {pct_7d:+.1f}% 7d)"
            )
    except Exception:
        pass
    return ""


def generate_reply(tweet_text: str) -> str | None:
    """Generate a reply using AI."""
    market = _get_market_context()

    system = (
        "You are @CoinWatchAlert replying to a crypto tweet. "
        "You sound like a real trader joining the conversation — not a bot.\n\n"
        "RULES:\n"
        "- Max 220 characters\n"
        "- Add genuine value — a data point, insight, or angle they missed\n"
        "- Reference something specific from their tweet\n"
        "- NO hashtags, NO emojis (except 🟢 🔴 for direction), NO exclamation marks\n"
        "- NO 'NFA', 'DYOR', 'great point', 'couldn't agree more', or sycophantic filler\n"
        "- Sound like a trader texting a group chat, not a corporate account\n"
        "- Use contractions (don't, won't) — real people don't write formally\n"
        "- If you disagree, be respectful but direct about it\n"
        "- Do NOT wrap your response in quotes"
    )

    prompt = (
        f"Tweet you're replying to:\n\"{tweet_text[:300]}\"\n\n"
        f"Market data: {market}\n\n"
        "Write a short, conversational reply (max 220 chars) that adds to "
        "the discussion. Just the reply text, nothing else."
    )

    return ai_writer._call_claude(system, prompt, max_tokens=120)


def post_reply(reply_text: str, tweet_id: str | None = None) -> bool:
    """Post the reply. If tweet_id is provided, posts as a reply to that tweet."""
    # Clean up
    reply_text = re.sub(r'\s*#\w+', '', reply_text).strip()
    reply_text = reply_text.replace("!", ".")
    if len(reply_text) > 280:
        reply_text = reply_text[:277].rsplit(" ", 1)[0] + "..."

    if not state.can_tweet():
        logger.error("Monthly tweet limit reached. Cannot post.")
        return False

    try:
        client = twitter_client.get_client()
        kwargs = {"text": reply_text}
        if tweet_id:
            kwargs["in_reply_to_tweet_id"] = tweet_id
        response = client.create_tweet(**kwargs)
        state.record_tweet()
        tid = response.data["id"]
        remaining = state.tweets_remaining()
        logger.info(
            "Reply posted (id=%s, %d remaining): %s",
            tid, remaining, reply_text,
        )
        return True
    except tweepy.errors.Forbidden as exc:
        logger.error("Twitter 403 Forbidden: %s", exc)
    except tweepy.TweepyException as exc:
        logger.error("Twitter error: %s", exc)
    return False


def run_interactive():
    """Interactive loop: paste tweet, generate reply, approve, post."""
    print("\n=== CoinWatchAlert Reply Generator ===")
    print("Paste a tweet URL or text. Type 'quit' to exit.\n")

    if DRY_RUN:
        print("[DRY RUN MODE — replies will not be posted]\n")

    while True:
        print("-" * 50)
        user_input = input("\nTweet URL or text: ").strip()
        if not user_input or user_input.lower() in ("quit", "exit", "q"):
            print("Done.")
            break

        # Check if it's a URL with a tweet ID
        tweet_id = _extract_tweet_id(user_input)

        if tweet_id:
            print(f"Tweet ID: {tweet_id}")
            tweet_text = input("Paste the tweet text: ").strip()
            if not tweet_text:
                print("Need the tweet text to generate a reply.")
                continue
        else:
            tweet_text = user_input
            tweet_id = None

        # Generate reply
        while True:
            print("\nGenerating reply...")
            reply = generate_reply(tweet_text)
            if not reply:
                print("Failed to generate reply. Check your ANTHROPIC_API_KEY.")
                break

            print(f"\n  >> {reply}")
            print(f"  ({len(reply)} chars)")

            action = input("\n[p]ost / [r]egenerate / [e]dit / [s]kip: ").strip().lower()

            if action == "p":
                if DRY_RUN:
                    print("[DRY RUN] Would have posted reply.")
                else:
                    if post_reply(reply, tweet_id):
                        print("Posted.")
                    else:
                        print("Failed to post.")
                break
            elif action == "r":
                continue
            elif action == "e":
                edited = input("Your edit: ").strip()
                if edited:
                    reply = edited
                    print(f"\n  >> {reply}")
                    print(f"  ({len(reply)} chars)")
                    confirm = input("\n[p]ost / [s]kip: ").strip().lower()
                    if confirm == "p":
                        if DRY_RUN:
                            print("[DRY RUN] Would have posted reply.")
                        else:
                            if post_reply(reply, tweet_id):
                                print("Posted.")
                            else:
                                print("Failed to post.")
                break
            else:
                print("Skipped.")
                break


if __name__ == "__main__":
    run_interactive()
