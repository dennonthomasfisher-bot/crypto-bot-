"""
News monitor – polls CryptoPanic's free API for hot/important crypto news
and returns story objects that haven't been posted yet.

Free-tier CryptoPanic API: https://cryptopanic.com/developers/api/
  - No charge, rate-limit ~100 req/day on free tier.
  - Sign up at https://cryptopanic.com/accounts/signup/ to get an API key.
"""

import time
import hashlib
import logging
import requests

import config
import ai_writer

logger = logging.getLogger(__name__)

# Set of story hashes we've already posted (cleared after NEWS_DEDUP_WINDOW)
_posted_hashes: dict[str, float] = {}   # hash -> timestamp when posted


def _story_hash(story: dict) -> str:
    """Stable identifier for a story based on its URL."""
    return hashlib.md5(story.get("url", story.get("title", "")).encode()).hexdigest()


def _prune_old_hashes() -> None:
    """Remove hashes older than NEWS_DEDUP_WINDOW to keep memory bounded."""
    cutoff = time.time() - config.NEWS_DEDUP_WINDOW
    to_delete = [h for h, ts in _posted_hashes.items() if ts < cutoff]
    for h in to_delete:
        del _posted_hashes[h]


def _fetch_news() -> list[dict]:
    """
    Fetch the latest hot/important stories from CryptoPanic.
    Returns a list of story dicts, or [] on error.
    """
    if not config.CRYPTOPANIC_API_KEY:
        logger.warning(
            "CRYPTOPANIC_API_KEY not set – news monitoring disabled. "
            "Get a free key at https://cryptopanic.com/developers/api/"
        )
        return []

    url = f"{config.CRYPTOPANIC_BASE}/posts/"
    params = {
        "auth_token": config.CRYPTOPANIC_API_KEY,
        "filter":     config.CRYPTOPANIC_FILTER,
        "public":     "true",
        "kind":       "news",
    }
    try:
        resp = requests.get(url, params=params, timeout=15)
        resp.raise_for_status()
        return resp.json().get("results", [])
    except requests.RequestException as exc:
        logger.warning("CryptoPanic fetch failed: %s", exc)
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

    # Macro sources: only keep stories with a crypto/macro bridge keyword
    # This prevents random business news from cluttering the feed
    if _is_macro_source(story):
        if not any(kw in title for kw in _MACRO_CRYPTO_BRIDGE_KEYWORDS):
            return True

    return False


def _is_macro_source(story: dict) -> bool:
    """Check if story comes from a macro/geopolitical source."""
    return story.get("origin", "") in ("reuters_business", "cnbc_economy")


def _has_important_keyword(story: dict) -> bool:
    """Check if story title contains important keywords."""
    title = story.get("title", "").lower()
    # Macro sources need a bridge keyword to confirm crypto relevance
    if _is_macro_source(story):
        return any(kw in title for kw in _MACRO_CRYPTO_BRIDGE_KEYWORDS)
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

    is_macro = _is_macro_source(story)
    source_context = ""
    if is_macro:
        source_context = """
NOTE: This is a MACRO/GEOPOLITICAL story, not crypto-native news.
You MUST connect it to crypto impact. How does this affect BTC, risk assets, liquidity?
If you can't connect it to crypto in a meaningful way, score it low.
When you CAN connect it — this is GOLD content. Macro-to-crypto takes are what
separate a real trader account from a generic crypto news feed."""

    system = f"""You are a crypto news editor for @CoinWatchAlert on Twitter. You decide which stories are worth tweeting and write sharp, opinionated commentary that makes people follow you.

SCORING (respond with a number 1-10):
- 10: Market-moving (ETF approval, major hack, regulatory bombshell, BTC ATH, war/sanctions, Fed surprise)
- 8-9: Very important (major exchange news, institutional moves, rate decisions, geopolitical shifts, tariffs)
- 6-7: Interesting (notable market moves, industry trends, macro data, notable partnerships)
- 4-5: Mildly interesting (minor updates, routine analysis)
- 1-3: Noise (price predictions, sponsored content, repetitive updates)
{source_context}
COMMENTARY:
- Write 1-2 punchy sentences with a CLEAR TAKE — bullish or bearish, not neutral
- Include at least ONE specific number: a price level ($X), percentage (X%), dollar amount ($XB), or metric
- Say what this means for price action. Make a call or prediction with a specific target
- For macro news: ALWAYS bridge to crypto — "This means X for BTC because Y"
- Sound like a trader reacting to the news, not a journalist summarizing it
- NEVER write passive commentary like "worth watching" or "interesting development"
- Instead: "This is bullish for BTC because..." or "If this escalates, risk-off sends BTC to $X"
- NO hashtags, NO emojis except 🟢🔴 for direction
- If the story is noise (score < 7), just write "SKIP"

Format your response EXACTLY like this:
SCORE: [number]
TAKE: [your commentary or SKIP]"""

    prompt = f"""Rate this {'macro/geopolitical' if is_macro else 'crypto'} news story and write commentary:

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
    Return list of new story dicts that haven't been posted yet.
    Side-effect: marks returned stories as posted.
    """
    _prune_old_hashes()
    stories = _fetch_news()
    new_stories = []
    for story in stories:
        h = _story_hash(story)
        if h not in _posted_hashes:
            new_stories.append(story)
            _posted_hashes[h] = time.time()
    return new_stories


def fetch_latest_headlines(n: int = 3) -> list[str]:
    """
    Return up to `n` titles from the most recent CryptoPanic stories.
    Does NOT mark stories as posted – safe to call for recap generation.
    """
    stories = _fetch_news()
    return [s["title"] for s in stories[:n] if s.get("title")]


def format_news_tweet(story: dict) -> str:
    """
    Turn a scored story dict into a ready-to-post tweet.
    Uses breaking news style with AI commentary.
    """
    title = story.get("title", "Breaking crypto news")
    url = story.get("url", "")
    source = story.get("source", "")
    commentary = story.get("commentary")
    score = story.get("score", 7)

    prefix = _pick_news_prefix(score, title)

    if commentary:
        # Breaking style: PREFIX + commentary + source + URL
        tweet_start = f"{prefix} {commentary}"
        url_len = len(url) + 2 if url else 0
        source_tag = f"\n\n[{source}]" if source else ""
        source_len = len(source_tag)
        max_len = 275 - url_len - source_len
        if len(tweet_start) > max_len:
            tweet_start = tweet_start[:max_len - 1].rsplit(" ", 1)[0] + "…"

        parts = [tweet_start]
        if source:
            parts.append(f"[{source}]")
        if url:
            parts.append(url)
        return "\n\n".join(parts)
    else:
        # Fallback: PREFIX + headline + URL
        headline = f"{prefix} {title}"
        url_len = len(url) + 2 if url else 0
        max_headline = 275 - url_len
        if len(headline) > max_headline:
            headline = headline[:max_headline - 1].rsplit(" ", 1)[0] + "…"
        parts = [headline]
        if url:
            parts.append("")
            parts.append(url)
        return "\n".join(parts)


def get_news_card_type(story: dict) -> str:
    """Determine the card type for news image generation."""
    score = story.get("score", 7)
    title_lower = story.get("title", "").lower()
    if score >= 9 or any(w in title_lower for w in ["breaking", "hack", "crash"]):
        return "breaking"
    if any(w in title_lower for w in ["bull", "rally", "surge", "soar", "pump", "recover"]):
        return "bullish"
    if any(w in title_lower for w in ["bear", "crash", "dump", "drop", "fall", "plunge"]):
        return "bearish"
    if any(w in title_lower for w in ["alert", "warning", "risk", "liquidat"]):
        return "alert"
    return "latest"
