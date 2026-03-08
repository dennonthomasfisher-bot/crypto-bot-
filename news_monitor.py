"""
News monitor – aggregates crypto news from multiple high-quality sources
and uses AI to filter noise and add commentary.

Sources:
  1. CryptoPanic API (hot/important stories) – existing
  2. CoinDesk RSS feed
  3. The Block RSS feed
  4. CoinTelegraph RSS feed

AI layer:
  - Scores story importance (1-10), only posts 7+
  - Writes a sharp 1-2 sentence take instead of just sharing headlines
"""

import hashlib
import logging
import re
import time
import xml.etree.ElementTree as ET

import requests

import config
import state

logger = logging.getLogger(__name__)

# Track consecutive failures per source
_consecutive_failures: dict[str, int] = {}

# ── RSS feed sources ─────────────────────────────────────────────────────────

_RSS_FEEDS = {
    "coindesk": "https://www.coindesk.com/arc/outboundfeeds/rss/",
    "theblock": "https://www.theblock.co/rss.xml",
    "cointelegraph": "https://cointelegraph.com/rss",
}

# Keywords that indicate high-quality crypto news (for RSS filtering)
_IMPORTANT_KEYWORDS = [
    "bitcoin", "btc", "ethereum", "eth", "sec", "etf", "regulation",
    "fed", "interest rate", "hack", "exploit", "billion", "million",
    "blackrock", "grayscale", "coinbase", "binance", "solana", "xrp",
    "halving", "whale", "institutional", "approval", "ban", "lawsuit",
    "stablecoin", "defi", "layer 2", "l2", "airdrop", "token",
    "market cap", "all-time high", "ath", "crash", "surge", "rally",
]

# Low-quality patterns to filter out
_NOISE_PATTERNS = [
    r"price prediction",
    r"price analysis.*\d{1,2}/\d{1,2}",
    r"sponsored",
    r"partner content",
    r"press release",
    r"ad\b",
    r"nft.*collection",
    r"meme.*coin.*launch",
    r"airdrop.*guide",
    r"how to buy",
]


def _story_hash(story: dict) -> str:
    """Stable identifier for a story based on its URL or title."""
    key = story.get("url") or story.get("title", "")
    return hashlib.sha256(key.encode()).hexdigest()


def _prune_old_hashes() -> None:
    """Remove hashes older than NEWS_DEDUP_WINDOW to keep memory bounded."""
    state.prune_old_news_hashes()


# ── CryptoPanic source ───────────────────────────────────────────────────────

def _fetch_cryptopanic() -> list[dict]:
    """Fetch the latest hot/important stories from CryptoPanic."""
    if not config.CRYPTOPANIC_API_KEY:
        return []

    url = f"{config.CRYPTOPANIC_BASE}/posts/"
    params = {
        "auth_token": config.CRYPTOPANIC_API_KEY,
        "filter": config.CRYPTOPANIC_FILTER,
        "public": "true",
        "kind": "news",
    }
    source = "cryptopanic"
    for attempt in range(3):
        try:
            resp = requests.get(url, params=params, timeout=15)
            if resp.status_code in (404, 502, 503):
                logger.warning("CryptoPanic returned %d — API may be down", resp.status_code)
                break
            if resp.status_code == 429:
                wait = 2 ** (attempt + 1)
                logger.warning("CryptoPanic rate limited (429), retrying in %ds…", wait)
                time.sleep(wait)
                continue
            resp.raise_for_status()
            _consecutive_failures[source] = 0
            results = resp.json().get("results", [])
            # Normalize to common format
            stories = []
            for r in results:
                stories.append({
                    "title": r.get("title", ""),
                    "url": r.get("url", ""),
                    "source": r.get("source", {}).get("title", "CryptoPanic"),
                    "origin": "cryptopanic",
                })
            return stories
        except requests.RequestException as exc:
            logger.warning("CryptoPanic fetch failed (attempt %d/3): %s", attempt + 1, exc)
            if attempt < 2:
                time.sleep(2 ** (attempt + 1))

    _consecutive_failures[source] = _consecutive_failures.get(source, 0) + 1
    if _consecutive_failures[source] >= 5:
        logger.warning("CryptoPanic has failed %d consecutive checks", _consecutive_failures[source])
    return []


# ── RSS feed sources ─────────────────────────────────────────────────────────

def _fetch_rss(name: str, feed_url: str) -> list[dict]:
    """Fetch and parse an RSS feed, returning normalized story dicts."""
    try:
        resp = requests.get(feed_url, timeout=15, headers={
            "User-Agent": "CoinWatchAlert/1.0 (crypto news bot)",
        })
        if resp.status_code != 200:
            logger.debug("RSS %s returned %d", name, resp.status_code)
            return []
        _consecutive_failures[name] = 0
    except requests.RequestException as exc:
        _consecutive_failures[name] = _consecutive_failures.get(name, 0) + 1
        if _consecutive_failures[name] <= 3:
            logger.debug("RSS %s fetch failed: %s", name, exc)
        return []

    try:
        root = ET.fromstring(resp.content)
    except ET.ParseError:
        logger.debug("RSS %s returned invalid XML", name)
        return []

    stories = []
    # Handle both RSS 2.0 (<channel><item>) and Atom (<entry>) formats
    items = root.findall(".//item") or root.findall(".//{http://www.w3.org/2005/Atom}entry")
    for item in items[:15]:  # Only check latest 15 items
        # RSS 2.0
        title_el = item.find("title")
        link_el = item.find("link")
        # Atom fallback
        if title_el is None:
            title_el = item.find("{http://www.w3.org/2005/Atom}title")
        if link_el is None:
            link_el = item.find("{http://www.w3.org/2005/Atom}link")

        title = title_el.text.strip() if title_el is not None and title_el.text else ""
        if link_el is not None:
            url = link_el.text.strip() if link_el.text else link_el.get("href", "")
        else:
            url = ""

        if not title:
            continue

        stories.append({
            "title": title,
            "url": url,
            "source": name.title(),
            "origin": name,
        })

    return stories


# ── Noise filter (pre-AI, fast) ──────────────────────────────────────────────

def _is_noise(story: dict) -> bool:
    """Quick check to reject obviously low-quality stories before AI scoring."""
    title = story.get("title", "").lower()

    # Reject noise patterns
    for pattern in _NOISE_PATTERNS:
        if re.search(pattern, title, re.IGNORECASE):
            return True

    # Too short to be meaningful
    if len(title) < 20:
        return True

    return False


def _has_important_keyword(story: dict) -> bool:
    """Check if story title contains important crypto keywords."""
    title = story.get("title", "").lower()
    return any(kw in title for kw in _IMPORTANT_KEYWORDS)


# ── AI importance scoring + commentary ───────────────────────────────────────

def _ai_score_and_comment(story: dict) -> dict | None:
    """
    Use Claude to score story importance (1-10) and write commentary.
    Returns story dict with 'score' and 'commentary' added, or None if low quality.
    """
    import ai_writer

    if not ai_writer.is_available():
        # Without AI, use keyword matching as a rough filter
        if _has_important_keyword(story):
            story["score"] = 7
            story["commentary"] = None  # Will fall back to headline-only
            return story
        return None

    title = story.get("title", "")
    source = story.get("source", "")

    system = """You are a crypto news editor for @CoinWatchAlert on Twitter. You decide which stories are worth tweeting and write sharp commentary.

SCORING (respond with a number 1-10):
- 10: Market-moving (ETF approval, major hack, regulatory bombshell, BTC ATH)
- 8-9: Very important (major exchange news, institutional moves, significant protocol updates)
- 6-7: Interesting (notable market moves, industry trends, notable partnerships)
- 4-5: Mildly interesting (minor updates, routine analysis)
- 1-3: Noise (price predictions, sponsored content, repetitive updates)

COMMENTARY:
- Write 1-2 punchy sentences about why this matters to traders
- Sound like a trader reacting to the news, not a journalist summarizing it
- Add context: what it means for price, market, or narrative
- NO hashtags, NO emojis except 🟢🔴 for direction
- If the story is noise (score < 7), just write "SKIP"

Format your response EXACTLY like this:
SCORE: [number]
TAKE: [your commentary or SKIP]"""

    prompt = f"""Rate this crypto news story and write commentary:

Headline: {title}
Source: {source}

Score it 1-10 and write your take."""

    from ai_writer import _call_claude
    result = _call_claude(system, prompt, max_tokens=150)
    if not result:
        # AI failed — fall back to keyword filter
        if _has_important_keyword(story):
            story["score"] = 7
            story["commentary"] = None
            return story
        return None

    # Parse score
    score = 5  # default
    score_match = re.search(r'SCORE:\s*(\d+)', result)
    if score_match:
        score = int(score_match.group(1))

    # Parse commentary
    take_match = re.search(r'TAKE:\s*(.+)', result, re.DOTALL)
    commentary = None
    if take_match:
        take = take_match.group(1).strip()
        if take.upper() != "SKIP" and len(take) > 10:
            commentary = take

    story["score"] = score
    story["commentary"] = commentary

    if score < 7:
        logger.info("News filtered (score %d/10): %.80s", score, title)
        return None

    logger.info("News approved (score %d/10): %.80s", score, title)
    return story


# ── Public API ───────────────────────────────────────────────────────────────

def check_news() -> list[dict]:
    """
    Aggregate news from all sources, deduplicate, filter noise, and
    score with AI. Returns list of high-quality story dicts.
    """
    _prune_old_hashes()

    # Collect from all sources
    all_stories = []
    all_stories.extend(_fetch_cryptopanic())
    for name, url in _RSS_FEEDS.items():
        all_stories.extend(_fetch_rss(name, url))

    if not all_stories:
        return []

    # Deduplicate and filter
    new_stories = []
    for story in all_stories:
        h = _story_hash(story)
        if state.news_already_posted(h):
            continue
        if _is_noise(story):
            continue

        # AI scoring — only let important stories through
        scored = _ai_score_and_comment(story)
        if scored:
            state.record_news_posted(h)
            new_stories.append(scored)

        # Cap at 2 stories per check to avoid flooding
        if len(new_stories) >= 2:
            break

    # Sort by score (highest first)
    new_stories.sort(key=lambda s: s.get("score", 0), reverse=True)
    return new_stories


def format_news_tweet(story: dict) -> str:
    """
    Turn a scored story dict into a ready-to-post tweet.
    Uses AI commentary if available, falls back to headline + URL.
    """
    title = story.get("title", "Breaking crypto news")
    url = story.get("url", "")
    source = story.get("source", "")
    commentary = story.get("commentary")

    if commentary:
        # AI commentary tweet: commentary + source credit + URL
        # Trim commentary to fit within 280 chars with URL
        url_len = len(url) + 2 if url else 0  # +2 for \n\n before URL
        source_tag = f"\n\n[{source}]" if source else ""
        source_len = len(source_tag)
        max_commentary = 280 - url_len - source_len
        if len(commentary) > max_commentary:
            commentary = commentary[:max_commentary - 3].rsplit(" ", 1)[0] + "..."

        parts = [commentary]
        if source:
            parts.append(f"[{source}]")
        if url:
            parts.append(url)
        return "\n\n".join(parts)
    else:
        # Fallback: headline + URL (old behavior)
        max_title_len = 180
        if len(title) > max_title_len:
            title = title[:max_title_len - 1] + "..."
        parts = [title]
        if url:
            parts.append("")
            parts.append(url)
        return "\n".join(parts)
