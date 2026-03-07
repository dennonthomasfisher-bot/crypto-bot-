"""
Thread posting – post multi-tweet threads for deeper AI analysis.

Uses Twitter API v2's reply-to functionality to chain tweets into threads.
Generates thread content via Claude for in-depth market breakdowns.
"""
from __future__ import annotations

import logging
import time

import ai_writer
import twitter_client
import tweet_generators
import state

logger = logging.getLogger(__name__)


def _call_claude_thread(price: float, pct_24h: float, pct_7d: float,
                        market_cap: float, coins_data: list[dict] | None = None) -> list[str] | None:
    """Generate a 3-4 tweet thread via Claude."""
    coin_lines = ""
    if coins_data:
        for c in coins_data[:8]:
            sym = c.get("symbol", "?").upper()
            cp = c.get("current_price", 0)
            cpct = c.get("price_change_percentage_24h_in_currency") or 0
            coin_lines += f"  {sym}: ${cp:,.2f} ({cpct:+.1f}%)\n"

    mcap_str = f"${market_cap / 1e12:.2f}T" if market_cap >= 1e12 else f"${market_cap / 1e9:.0f}B"

    system = """You are @CoinWatchAlert, a crypto Twitter account posting data-driven market threads.

Rules:
- Write EXACTLY 3 tweets, separated by ---
- Tweet 1: Hook — bold statement with the key data point (start with the price)
- Tweet 2: Analysis — deeper breakdown with supporting data
- Tweet 3: Outlook — your take + what to watch next, end with hashtags
- Each tweet MUST be under 270 characters
- Use real numbers from the data provided — never fabricate
- Sound like a sharp crypto analyst, not a hype account
- No "🚀", no "WAGMI", no "NFA", no "DYOR"
- Tweet 1 should end with "🧵👇" to signal a thread
- Only tweet 3 should have hashtags (include #Bitcoin #Crypto)
- Do NOT wrap tweets in quotes
- Do NOT number the tweets"""

    prompt = f"""Write a 3-tweet thread analyzing the current crypto market:

BTC Price: ${price:,.0f}
24h Change: {pct_24h:+.1f}%
7d Change: {pct_7d:+.1f}%
Market Cap: {mcap_str}
{f"Top coins:{chr(10)}{coin_lines}" if coin_lines else ""}

Separate each tweet with --- on its own line.
Write the thread now. Nothing else."""

    from ai_writer import _call_claude
    result = _call_claude(system, prompt, max_tokens=600)
    if not result:
        return None

    # Parse the thread
    tweets = [t.strip() for t in result.split("---") if t.strip()]
    if len(tweets) < 2:
        # Try splitting by double newlines if --- didn't work
        tweets = [t.strip() for t in result.split("\n\n") if t.strip() and len(t.strip()) > 30]

    if len(tweets) < 2:
        logger.warning("Thread generation produced only %d tweets, skipping", len(tweets))
        return None

    # Enforce character limits
    valid = []
    for t in tweets[:4]:
        if len(t) > 280:
            t = t[:277].rsplit(" ", 1)[0] + "..."
        valid.append(t)

    return valid


def post_thread(dry_run: bool = False) -> bool:
    """
    Generate and post a market analysis thread.
    Returns True if thread was posted successfully.
    """
    if not ai_writer.is_available():
        logger.info("AI not available — skipping thread (requires Claude)")
        return False

    btc = tweet_generators._get_btc_data()
    if not btc:
        return False

    price = btc.get("current_price", 0)
    if not price or price <= 0:
        return False

    pct_24h = btc.get("price_change_percentage_24h_in_currency") or 0
    pct_7d = btc.get("price_change_percentage_7d_in_currency") or 0
    mcap = btc.get("market_cap", 0)
    coins = tweet_generators._get_top_coins_data()

    tweets = _call_claude_thread(price, pct_24h, pct_7d, mcap, coins)
    if not tweets:
        logger.warning("Could not generate thread content")
        return False

    if dry_run:
        print(f"\n{'─'*60}")
        print("[DRY RUN] Would post thread:")
        for i, t in enumerate(tweets, 1):
            print(f"\n  Tweet {i}/{len(tweets)}:")
            print(f"  {t}")
        print(f"\n{'─'*60}")
        return True

    # Post the thread: first tweet, then replies
    try:
        client = twitter_client.get_client()

        if not state.can_tweet():
            logger.warning("Monthly tweet limit reached — skipping thread")
            return False

        # Post first tweet
        response = client.create_tweet(text=tweets[0])
        parent_id = response.data["id"]
        state.record_tweet()
        logger.info("Thread tweet 1/%d posted (id=%s)", len(tweets), parent_id)

        # Post subsequent tweets as replies
        for i, tweet_text in enumerate(tweets[1:], 2):
            time.sleep(2)  # pace the replies
            if not state.can_tweet():
                logger.warning("Monthly limit hit mid-thread at tweet %d", i)
                break
            response = client.create_tweet(text=tweet_text, in_reply_to_tweet_id=parent_id)
            parent_id = response.data["id"]
            state.record_tweet()
            logger.info("Thread tweet %d/%d posted (id=%s)", i, len(tweets), parent_id)

        return True

    except Exception as exc:
        logger.error("Thread posting failed: %s", exc)
        return False
