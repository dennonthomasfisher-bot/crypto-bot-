"""
CoinMarketCap monitor – polls the CMC free API for top gainers/losers
and market breadth data to generate varied tweet content.

Free tier: 10,000 credits/month (~333 calls/day).
Sign up at https://coinmarketcap.com/api/ for a free API key.

Endpoints used (all free tier):
  - /v1/cryptocurrency/listings/latest  (1 credit)
  - /v1/global-metrics/quotes/latest    (1 credit)
"""
from __future__ import annotations

import logging
import time

import requests

import config
import ai_writer

logger = logging.getLogger(__name__)

# Cache to avoid tweeting about the same mover twice in a row
_recent_movers: set[str] = set()
_MAX_RECENT_MOVERS = 20


def _headers() -> dict:
    return {
        "X-CMC_PRO_API_KEY": config.CMC_API_KEY,
        "Accept": "application/json",
    }


def is_available() -> bool:
    """Check if CMC API key is configured."""
    return bool(config.CMC_API_KEY)


def fetch_top_coins() -> list[dict] | None:
    """
    Fetch top N coins by market cap from CoinMarketCap.
    Returns list of coin dicts with price, % changes, market cap, volume.
    """
    if not config.CMC_API_KEY:
        logger.info("CMC_API_KEY not set — CoinMarketCap features disabled.")
        return None

    url = f"{config.CMC_BASE}/v1/cryptocurrency/listings/latest"
    params = {
        "start": 1,
        "limit": config.CMC_TOP_N,
        "convert": "USD",
        "sort": "market_cap",
        "sort_dir": "desc",
    }

    for attempt in range(3):
        try:
            resp = requests.get(url, headers=_headers(), params=params, timeout=15)
            if resp.status_code == 429:
                wait = 2 ** (attempt + 1)
                logger.warning("CMC rate limited (429), retrying in %ds…", wait)
                time.sleep(wait)
                continue
            if resp.status_code == 401:
                logger.error("CMC API key invalid (401). Check CMC_API_KEY in .env")
                return None
            resp.raise_for_status()
            data = resp.json()
            return data.get("data", [])
        except requests.RequestException as exc:
            logger.warning("CMC fetch failed (attempt %d/3): %s", attempt + 1, exc)
            if attempt < 2:
                time.sleep(2 ** (attempt + 1))
    return None


def fetch_global_metrics() -> dict | None:
    """
    Fetch global crypto market metrics (total market cap, BTC dominance, etc.).
    """
    if not config.CMC_API_KEY:
        return None

    url = f"{config.CMC_BASE}/v1/global-metrics/quotes/latest"
    try:
        resp = requests.get(url, headers=_headers(), timeout=15)
        resp.raise_for_status()
        data = resp.json()
        return data.get("data", {})
    except requests.RequestException as exc:
        logger.warning("CMC global metrics fetch failed: %s", exc)
        return None


def get_top_gainers(coins: list[dict], n: int = 5) -> list[dict]:
    """Return top N gainers by 24h % change from the listings data."""
    valid = [c for c in coins if c.get("quote", {}).get("USD", {}).get("percent_change_24h") is not None]
    return sorted(valid, key=lambda c: c["quote"]["USD"]["percent_change_24h"], reverse=True)[:n]


def get_top_losers(coins: list[dict], n: int = 5) -> list[dict]:
    """Return top N losers by 24h % change from the listings data."""
    valid = [c for c in coins if c.get("quote", {}).get("USD", {}).get("percent_change_24h") is not None]
    return sorted(valid, key=lambda c: c["quote"]["USD"]["percent_change_24h"])[:n]


def get_big_movers(coins: list[dict], threshold: float | None = None) -> list[dict]:
    """Return coins moving more than threshold% in 24h (either direction)."""
    threshold = threshold or config.CMC_MOVER_THRESHOLD
    movers = []
    for c in coins:
        pct = c.get("quote", {}).get("USD", {}).get("percent_change_24h")
        if pct is not None and abs(pct) >= threshold:
            movers.append(c)
    return sorted(movers, key=lambda c: abs(c["quote"]["USD"]["percent_change_24h"]), reverse=True)


def get_market_breadth(coins: list[dict]) -> dict:
    """Calculate how many coins are green vs red."""
    green = red = flat = 0
    for c in coins:
        pct = c.get("quote", {}).get("USD", {}).get("percent_change_24h")
        if pct is None:
            continue
        if pct > 0.5:
            green += 1
        elif pct < -0.5:
            red += 1
        else:
            flat += 1
    return {"green": green, "red": red, "flat": flat, "total": green + red + flat}


# ── Tweet formatters ──────────────────────────────────────────────────────────

def _fmt_price(val: float) -> str:
    if val >= 1000:
        return f"${val:,.0f}"
    if val >= 1:
        return f"${val:,.2f}"
    if val >= 0.01:
        return f"${val:.4f}"
    return f"${val:.6f}"


def _fmt_pct(val: float) -> str:
    return f"{'+' if val > 0 else ''}{val:.1f}%"


def format_movers_tweet(coins: list[dict]) -> str | None:
    """Generate a tweet about today's biggest movers from the top 100."""
    gainers = get_top_gainers(coins, 3)
    losers = get_top_losers(coins, 2)

    if not gainers:
        return None

    lines = ["Top 100 movers today:", ""]

    for c in gainers:
        sym = c["symbol"]
        pct = c["quote"]["USD"]["percent_change_24h"]
        price = c["quote"]["USD"]["price"]
        lines.append(f"🟢 {sym}: {_fmt_price(price)} ({_fmt_pct(pct)})")

    if losers:
        for c in losers[:2]:
            sym = c["symbol"]
            pct = c["quote"]["USD"]["percent_change_24h"]
            price = c["quote"]["USD"]["price"]
            lines.append(f"🔴 {sym}: {_fmt_price(price)} ({_fmt_pct(pct)})")

    breadth = get_market_breadth(coins)
    lines.extend(["", f"{breadth['green']}/{breadth['total']} coins green"])

    tweet = "\n".join(lines)
    if len(tweet) > 280:
        tweet = tweet[:277].rsplit("\n", 1)[0] + "…"
    return tweet


def format_spotlight_tweet(coin: dict) -> str | None:
    """Generate an AI tweet spotlighting a coin having a big day."""
    sym = coin["symbol"]
    name = coin["name"]
    quote = coin.get("quote", {}).get("USD", {})
    pct_24h = quote.get("percent_change_24h", 0)
    pct_7d = quote.get("percent_change_7d", 0)
    price = quote.get("price", 0)
    mcap = quote.get("market_cap", 0)
    rank = coin.get("cmc_rank", 0)

    # Skip if already tweeted about this coin recently
    if sym in _recent_movers:
        return None
    _recent_movers.add(sym)
    if len(_recent_movers) > _MAX_RECENT_MOVERS:
        _recent_movers.pop()

    direction = "pumping" if pct_24h > 0 else "dumping"
    mcap_str = f"${mcap / 1e9:.1f}B" if mcap >= 1e9 else f"${mcap / 1e6:.0f}M"

    if not ai_writer.is_available():
        emoji = "🟢" if pct_24h > 0 else "🔴"
        return (
            f"{emoji} {sym} {_fmt_pct(pct_24h)} today — {_fmt_price(price)}\n"
            f"\n"
            f"Rank #{rank} | MCap: {mcap_str} | 7d: {_fmt_pct(pct_7d)}"
        )

    prompt = f"""Write a tweet about {name} ({sym}) {direction} today.

{sym}: {_fmt_price(price)} ({_fmt_pct(pct_24h)} 24h, {_fmt_pct(pct_7d)} 7d)
Rank: #{rank} | Market cap: {mcap_str}

The tweet should:
- Lead with {sym}, NOT Bitcoin
- Sound like a trader who just spotted this move
- Include the actual numbers
- Under 275 characters
- NO hashtags
{ai_writer._get_recent_context()}
Write the tweet now. Nothing else."""

    system = """You are @CoinWatchAlert. You cover the WHOLE crypto market, not just Bitcoin. When an altcoin is moving, you call it out fast with data. No hashtags, no filler."""

    return ai_writer._call_claude(system, prompt)


def format_breadth_tweet(coins: list[dict]) -> str | None:
    """Generate a market breadth tweet showing overall market health."""
    breadth = get_market_breadth(coins)
    if breadth["total"] < 50:
        return None

    gainers = get_top_gainers(coins, 1)
    losers = get_top_losers(coins, 1)

    if not ai_writer.is_available():
        top_g = gainers[0] if gainers else None
        top_l = losers[0] if losers else None
        lines = [f"Market breadth: {breadth['green']}/{breadth['total']} coins green today", ""]
        if top_g:
            sym = top_g["symbol"]
            pct = top_g["quote"]["USD"]["percent_change_24h"]
            lines.append(f"Leading: {sym} {_fmt_pct(pct)}")
        if top_l:
            sym = top_l["symbol"]
            pct = top_l["quote"]["USD"]["percent_change_24h"]
            lines.append(f"Lagging: {sym} {_fmt_pct(pct)}")
        return "\n".join(lines)

    # Build data for AI
    top_3_gain = ", ".join(f"{c['symbol']} {_fmt_pct(c['quote']['USD']['percent_change_24h'])}" for c in gainers[:3])
    top_2_lose = ", ".join(f"{c['symbol']} {_fmt_pct(c['quote']['USD']['percent_change_24h'])}" for c in losers[:2])

    prompt = f"""Write a tweet about today's crypto market breadth.

Market data (top 100 coins):
- Green: {breadth['green']} | Red: {breadth['red']} | Flat: {breadth['flat']}
- Top gainers: {top_3_gain}
- Top losers: {top_2_lose}

The tweet should:
- Lead with the market breadth stat, not BTC
- Mention 1-2 interesting movers by name
- Give your read on what this means (risk-on? rotation? sell-off?)
- Under 275 characters
- NO hashtags
{ai_writer._get_recent_context()}
Write the tweet now. Nothing else."""

    system = """You are @CoinWatchAlert. You watch the WHOLE market — all 100 top coins — not just BTC. When you talk about market breadth, you read the room. No hashtags, no filler phrases like "Worth noting"."""

    return ai_writer._call_claude(system, prompt)
