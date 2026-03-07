"""
Growth engine – generates tweets designed to attract followers
on the free Twitter API tier (no search/read access needed).

Strategies:
  1. Influencer callouts – @mention big crypto accounts with data-driven takes
  2. CT narrative tweets – reference trending topics Crypto Twitter is buzzing about
  3. Hot coin spotlights – fast tweets when a coin is pumping/dumping hard
  4. Spicy hot takes – controversial but data-backed opinions that invite QTs
"""
from __future__ import annotations

import logging
import random
import time

import config
import ai_writer
import tweet_generators

logger = logging.getLogger(__name__)

# ── Influencer accounts to reference ────────────────────────────────────────
# Format: (handle, context/known-for)
# We @mention these in relevant tweets so their followers discover us.
INFLUENCERS = [
    ("@saborocket", "BTC chart analyst"),
    ("@CryptoCapo_", "macro crypto analyst"),
    ("@100trillionUSD", "Plan B, stock-to-flow model"),
    ("@MustStopMurad", "memecoin and narrative trader"),
    ("@WClementeIII", "on-chain analyst"),
    ("@CryptoBirb", "technical analysis"),
    ("@AltcoinSherpa", "altcoin trader"),
    ("@Ashcryptoreal", "crypto analyst"),
    ("@CryptoKaleo", "swing trader"),
    ("@EmberCN", "whale watcher and on-chain data"),
]

# Track recent mentions to avoid spamming the same account
_recent_mentions: dict[str, float] = {}
_MENTION_COOLDOWN = 86400  # 24 hours between mentioning same account

# ── CT narratives to reference ──────────────────────────────────────────────
CT_NARRATIVES = [
    "ETF flows",
    "exchange reserves",
    "whale accumulation",
    "funding rates",
    "BTC dominance cycle",
    "altseason signals",
    "stablecoin inflows",
    "miner capitulation",
    "DXY correlation",
    "halving cycle",
    "institutional adoption",
    "Layer 2 growth",
    "AI x crypto narrative",
    "RWA tokenization",
    "memecoin rotation",
]


def _get_available_influencer() -> tuple[str, str] | None:
    """Pick an influencer we haven't mentioned recently."""
    now = time.time()
    # Clean old entries
    expired = [k for k, v in _recent_mentions.items() if now - v > _MENTION_COOLDOWN]
    for k in expired:
        del _recent_mentions[k]

    available = [
        (handle, ctx) for handle, ctx in INFLUENCERS
        if handle not in _recent_mentions
    ]
    if not available:
        return None
    return random.choice(available)


def _record_mention(handle: str) -> None:
    _recent_mentions[handle] = time.time()


def generate_influencer_callout() -> str | None:
    """
    Generate a tweet that references a big crypto account.
    Uses AI to make it natural — not spammy tagging.
    """
    btc_data = tweet_generators._get_btc_data()
    if not btc_data:
        return None

    influencer = _get_available_influencer()
    if not influencer:
        logger.info("All influencers on cooldown, skipping callout.")
        return None

    handle, context = influencer
    price = btc_data.get("current_price", 0)
    pct_24h = btc_data.get("price_change_percentage_24h_in_currency") or 0
    pct_7d = btc_data.get("price_change_percentage_7d_in_currency") or 0

    prompt = f"""Write a tweet that naturally references {handle} (known for: {context}).

Current BTC data: ${price:,.0f} ({pct_24h:+.1f}% 24h, {pct_7d:+.1f}% 7d)

Examples of good callout styles:
- "{handle} called this level weeks ago. BTC at $67k and the thesis is playing out."
- "Watching the same setup {handle} flagged. $67k BTC with declining exchange reserves."
- "The {context} thesis from {handle} looking stronger by the day. BTC holding $67k."
- "Data backing up what {handle} has been saying — [specific insight]"

Rules:
- The mention must feel NATURAL, like you're referencing their analysis
- Include the actual price data
- Add your own take — don't just repeat theirs
- Under 275 characters
- NO hashtags
- Sound like a trader who follows them, not a fan account

Write the tweet now. Nothing else."""

    system = """You are @CoinWatchAlert, a data-driven crypto account. When referencing other accounts, you sound like a peer who respects their analysis — never sycophantic, never desperate for attention. You add your own insight on top of the reference."""

    tweet = ai_writer._call_claude(system, prompt)
    if tweet:
        _record_mention(handle)
    return tweet


def generate_ct_narrative_tweet() -> str | None:
    """
    Generate a tweet about a trending CT narrative.
    These perform well because people searching the topic find you.
    """
    btc_data = tweet_generators._get_btc_data()
    if not btc_data:
        return None

    narrative = random.choice(CT_NARRATIVES)
    price = btc_data.get("current_price", 0)
    pct_24h = btc_data.get("price_change_percentage_24h_in_currency") or 0

    prompt = f"""Write a tweet about "{narrative}" in the crypto market.

BTC: ${price:,.0f} ({pct_24h:+.1f}% 24h)

The tweet should:
- Take a clear stance on this narrative (bullish, bearish, or "everyone's wrong about this")
- Include the BTC price for context
- Sound like someone deep in Crypto Twitter sharing their read
- Be slightly provocative — the kind of tweet that gets quote-tweeted
- Under 275 characters
- NO hashtags

Good examples:
- "Everyone's talking about ETF flows but nobody's watching exchange reserves. BTC at $67k with supply drying up. Connect the dots."
- "CT obsessing over altseason while BTC dominance keeps climbing. $67k BTC and alts still bleeding. Maybe the rotation isn't coming."
- "The AI x crypto narrative is the most overhyped thing since NFTs. Change my mind. BTC at $67k doing just fine without it."

Write the tweet now. Nothing else."""

    system = """You are @CoinWatchAlert. You have strong opinions about crypto narratives. You cut through the noise with data. You're not afraid to disagree with the crowd. No hashtags, no emojis (except 🟢/🔴 for direction), no crypto bro speak."""

    return ai_writer._call_claude(system, prompt)


def generate_hot_take() -> str | None:
    """
    Generate a spicy, controversial take designed to get engagement.
    """
    btc_data = tweet_generators._get_btc_data()
    if not btc_data:
        return None

    price = btc_data.get("current_price", 0)
    pct_24h = btc_data.get("price_change_percentage_24h_in_currency") or 0
    pct_7d = btc_data.get("price_change_percentage_7d_in_currency") or 0

    angle = random.choice([
        "Make a bold price prediction for the next 30 days based on the data",
        "Disagree with a common crypto belief using the current data",
        "Call out something the majority of CT is getting wrong right now",
        "Compare this market cycle to a previous one and point out what's different",
        "Say something nice about a coin CT loves to hate (backed by data)",
        "Point out a red flag that nobody seems to be talking about",
        "Make the case that we're earlier/later in the cycle than people think",
    ])

    prompt = f"""Write a SPICY crypto hot take tweet.

BTC: ${price:,.0f} ({pct_24h:+.1f}% 24h, {pct_7d:+.1f}% 7d)

Angle: {angle}

The tweet should:
- Be provocative enough to make people want to quote-tweet with their response
- Back up the take with the real price data
- Sound confident, not hedging
- Under 275 characters
- NO hashtags
- End with something that dares disagreement ("prove me wrong", "tell me I'm wrong", etc.) — but vary the phrasing

Write the tweet now. Nothing else."""

    system = """You are @CoinWatchAlert. You have trader conviction. When you have a take, you commit to it with data. You don't hedge with "maybe" or "possibly". You invite debate because you're confident in your analysis. No hashtags, minimal emojis."""

    return ai_writer._call_claude(system, prompt)


def generate_coin_spotlight(coin_data: dict) -> str | None:
    """
    Generate a tweet spotlighting a coin that's having a big move.
    Called by price_monitor when a coin moves significantly.
    """
    symbol = coin_data.get("symbol", "?").upper()
    price = coin_data.get("current_price", 0)
    pct_24h = coin_data.get("price_change_percentage_24h_in_currency") or 0

    if abs(pct_24h) < 5:
        return None  # Only spotlight significant moves

    direction = "pumping" if pct_24h > 0 else "dumping"

    prompt = f"""Write a tweet about {symbol} {direction} hard right now.

{symbol}: ${price:,.2f} ({pct_24h:+.1f}% 24h)

The tweet should:
- Lead with the move — make it feel urgent/breaking
- Include your read on why or what happens next
- Sound like a trader who just spotted the move on their charts
- Under 275 characters
- NO hashtags

Write the tweet now. Nothing else."""

    system = """You are @CoinWatchAlert. When a coin is moving, you're one of the first to call it out with data. Quick, sharp, timely. No hashtags, no emojis spam."""

    return ai_writer._call_claude(system, prompt)
