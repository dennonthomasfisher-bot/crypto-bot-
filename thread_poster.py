"""
Thread posting – post multi-tweet threads for deeper AI analysis.

Uses Twitter API v2's reply-to functionality to chain tweets into threads.
Generates thread content via Claude for in-depth market breakdowns.
"""
from __future__ import annotations

import logging
import re
import time

import config
import ai_writer
import twitter_client
import tweet_generators
import state
import chart_generator

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

    # Fetch DeFi TVL for richer analysis
    defi_line = ""
    try:
        import defi_monitor
        tvl = defi_monitor.fetch_total_tvl()
        if tvl:
            defi_line = f"\nDeFi TVL: ${tvl.get('tvl', 0) / 1e9:.1f}B ({tvl.get('pct_24h', 0):+.1f}% 24h)"
    except Exception:
        pass

    system = """You are @CoinWatchAlert on Twitter. You sound like a real trader, not a bot.

Rules:
- Write EXACTLY 3 tweets, separated by ---
- Tweet 1: Hook — sharp observation with the key data point. End with 🧵
- Tweet 2: Analysis — the "why" behind the numbers, what most people are missing
- Tweet 3: Your take — what you're watching, what you'd do, what comes next
- Each tweet MUST be under 270 characters
- NO hashtags anywhere in the thread
- NO emojis except 🧵 on tweet 1, 📊/📈/🎯 for section labels, and 🟢/🔴 for price direction
- Use line breaks within each tweet — never a wall of text
- Use → arrows for listing data points
- Use real numbers — never fabricate
- Sound conversational, like you're explaining to a smart friend
- Do NOT wrap tweets in quotes
- Do NOT number the tweets"""

    prompt = f"""Write a 3-tweet thread analyzing the current crypto market:

BTC Price: ${price:,.0f}
24h Change: {pct_24h:+.1f}%
7d Change: {pct_7d:+.1f}%
Market Cap: {mcap_str}
{f"Top coins:{chr(10)}{coin_lines}" if coin_lines else ""}{defi_line}

If DeFi TVL data is provided, reference it in your analysis where relevant.

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

    # Strip hashtags and enforce character limits
    valid = []
    for t in tweets[:4]:
        t = re.sub(r'\s*#\w+', '', t).strip()
        if len(t) > 280:
            t = t[:277].rsplit(" ", 1)[0] + "..."
        valid.append(t)

    return valid


def post_thread(dry_run: bool = False) -> bool:
    """
    Generate and post a market analysis thread.
    Returns True if thread was posted successfully.
    """
    if not dry_run and config.is_quiet_hours():
        logger.info("Quiet hours (%d:00-%d:00 UK) — skipping thread",
                     config.QUIET_HOURS_START, config.QUIET_HOURS_END)
        return False

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

        # Post first tweet — attach BTC chart if available
        chart_path = chart_generator.generate_price_chart("bitcoin", "BTC", days=7)
        if chart_path:
            # Use media upload for lead tweet
            api_v1 = twitter_client._get_api_v1()
            if api_v1:
                try:
                    media = api_v1.media_upload(chart_path)
                    response = client.create_tweet(text=tweets[0], media_ids=[media.media_id])
                    logger.info("Thread lead tweet posted with chart")
                except Exception:
                    response = client.create_tweet(text=tweets[0])
            else:
                response = client.create_tweet(text=tweets[0])
        else:
            response = client.create_tweet(text=tweets[0])
        parent_id = response.data["id"]
        state.record_tweet()
        logger.info("Thread tweet 1/%d posted (id=%s, %d remaining)", len(tweets), parent_id, state.tweets_remaining())

        # Post subsequent tweets as replies
        for i, tweet_text in enumerate(tweets[1:], 2):
            time.sleep(2)  # pace the replies
            if not state.can_tweet():
                logger.warning("Monthly limit hit mid-thread at tweet %d", i)
                break
            response = client.create_tweet(text=tweet_text, in_reply_to_tweet_id=parent_id)
            parent_id = response.data["id"]
            state.record_tweet()
            logger.info("Thread tweet %d/%d posted (id=%s, %d remaining)", i, len(tweets), parent_id, state.tweets_remaining())

        return True

    except Exception as exc:
        logger.error("Thread posting failed: %s", exc)
        return False


# ── Weekly recap thread (Sunday) ────────────────────────────────────────────

def _call_claude_weekly_recap(btc_data: dict, coins_data: list[dict] | None = None) -> list[str] | None:
    """Generate a 4-5 tweet weekly recap thread via Claude."""
    price = btc_data.get("current_price", 0)
    pct_7d = btc_data.get("price_change_percentage_7d_in_currency") or 0
    mcap = btc_data.get("market_cap", 0)

    coin_lines = ""
    if coins_data:
        for c in coins_data[:12]:
            sym = c.get("symbol", "?").upper()
            cp = c.get("current_price", 0)
            cpct_7d = c.get("price_change_percentage_7d_in_currency") or 0
            cpct_24h = c.get("price_change_percentage_24h_in_currency") or 0
            coin_lines += f"  {sym}: ${cp:,.2f} (7d: {cpct_7d:+.1f}%, 24h: {cpct_24h:+.1f}%)\n"

    mcap_str = f"${mcap / 1e12:.2f}T" if mcap >= 1e12 else f"${mcap / 1e9:.0f}B"

    # Count weekly winners/losers
    if coins_data:
        green_7d = sum(1 for c in coins_data if (c.get("price_change_percentage_7d_in_currency") or 0) > 0)
        total = len(coins_data)
    else:
        green_7d = 0
        total = 0

    system = """You are @CoinWatchAlert on Twitter. Sunday is your weekly recap day — your followers count on this thread for a clean summary of what happened and what to watch next week.

Rules:
- Write EXACTLY 4 tweets, separated by ---
- Tweet 1: Hook — "Weekly recap" opening with the headline stat of the week. End with 🧵
- Tweet 2: Winners & losers — use → arrows for listing coins, 🟢/🔴 for direction
- Tweet 3: The bigger picture — macro, sentiment, dominance, narratives
- Tweet 4: What to watch next week — use 🎯 for key levels/events
- Each tweet MUST be under 270 characters
- NO hashtags
- Use line breaks within each tweet — never a wall of text
- Allowed emojis: 🧵 📊 📈 🎯 🟢 🔴 → (arrows in text)
- Use real numbers from the data — never fabricate
- Sound like a trader wrapping up the week for friends
- Do NOT wrap tweets in quotes or number them"""

    prompt = f"""Write a 4-tweet weekly recap thread for this Sunday:

BTC Price: ${price:,.0f}
7d Change: {pct_7d:+.1f}%
Market Cap: {mcap_str}
Weekly market: {green_7d}/{total} coins green on the week

Top coins (7d performance):
{coin_lines}

Separate each tweet with --- on its own line.
Write the thread now. Nothing else."""

    from ai_writer import _call_claude
    result = _call_claude(system, prompt, max_tokens=800)
    if not result:
        return None

    tweets = [t.strip() for t in result.split("---") if t.strip()]
    if len(tweets) < 3:
        tweets = [t.strip() for t in result.split("\n\n") if t.strip() and len(t.strip()) > 30]

    if len(tweets) < 3:
        logger.warning("Weekly recap produced only %d tweets, skipping", len(tweets))
        return None

    valid = []
    for t in tweets[:5]:
        t = re.sub(r'\s*#\w+', '', t).strip()
        if len(t) > 280:
            t = t[:277].rsplit(" ", 1)[0] + "..."
        valid.append(t)

    return valid


def post_weekly_recap(dry_run: bool = False) -> bool:
    """
    Generate and post a weekly market recap thread (Sunday).
    Returns True if thread was posted successfully.
    """
    if not dry_run and config.is_quiet_hours():
        logger.info("Quiet hours (%d:00-%d:00 UK) — skipping weekly recap",
                     config.QUIET_HOURS_START, config.QUIET_HOURS_END)
        return False

    if not ai_writer.is_available():
        logger.info("AI not available — skipping weekly recap (requires Claude)")
        return False

    btc = tweet_generators._get_btc_data()
    if not btc:
        return False

    price = btc.get("current_price", 0)
    if not price or price <= 0:
        return False

    coins = tweet_generators._get_top_coins_data()
    tweets = _call_claude_weekly_recap(btc, coins)
    if not tweets:
        logger.warning("Could not generate weekly recap thread")
        return False

    if dry_run:
        print(f"\n{'─'*60}")
        print("[DRY RUN] Would post weekly recap thread:")
        for i, t in enumerate(tweets, 1):
            print(f"\n  Tweet {i}/{len(tweets)}:")
            print(f"  {t}")
        print(f"\n{'─'*60}")
        return True

    try:
        client = twitter_client.get_client()

        if not state.can_tweet():
            logger.warning("Monthly tweet limit reached — skipping weekly recap")
            return False

        response = client.create_tweet(text=tweets[0])
        parent_id = response.data["id"]
        state.record_tweet()
        logger.info("Weekly recap tweet 1/%d posted (id=%s, %d remaining)", len(tweets), parent_id, state.tweets_remaining())

        for i, tweet_text in enumerate(tweets[1:], 2):
            time.sleep(2)
            if not state.can_tweet():
                logger.warning("Monthly limit hit mid-thread at tweet %d", i)
                break
            response = client.create_tweet(text=tweet_text, in_reply_to_tweet_id=parent_id)
            parent_id = response.data["id"]
            state.record_tweet()
            logger.info("Weekly recap tweet %d/%d posted (id=%s, %d remaining)", i, len(tweets), parent_id, state.tweets_remaining())

        return True

    except Exception as exc:
        logger.error("Weekly recap posting failed: %s", exc)
        return False
