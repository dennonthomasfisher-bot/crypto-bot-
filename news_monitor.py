"""
News monitor – polls RSS feeds and NewsAPI.org for hot/important crypto news
and returns story objects that haven't been posted yet.
"""
from __future__ import annotations

import json
import os
import re
import tempfile
import time
import hashlib
import logging
import requests
import feedparser
from bs4 import BeautifulSoup

import config
import ai_writer
import news_api

logger = logging.getLogger(__name__)

# ── Noise patterns (pre-AI fast filter) ──────────────────────────────────────
_NOISE_PATTERNS = [
    r'\bsponsored\b',
    r'\bpress release\b',
    r'\bpartnership\b.*\bannounce',
    r'\bgiveaway\b',
    r'\bairdrop\b',
    r'\bprice prediction\b',
    r'\bwill.*reach\b',
    r'\bcould.*hit\b',
    r'\btarget.*\$\d',
    r'\bshib\b.*\bmoon\b',
    r'top \d+ coins? to buy',
    r'best crypto.*\d{4}',
]

_IMPORTANT_KEYWORDS = [
    "etf", "sec", "cftc", "fed", "federal reserve", "interest rate",
    "hack", "exploit", "breach", "stolen", "liquidat",
    "bitcoin", "btc", "ethereum", "eth",
    "institutional", "blackrock", "fidelity", "coinbase",
    "ban", "regulation", "legal", "lawsuit", "arrest",
    "halving", "ath", "all-time high",
    "stablecoin", "usdt", "usdc",
    "defi", "tvl", "bridge",
    "exchange", "binance", "ftx", "kraken",
    "whale", "inflow", "outflow",
]

_MACRO_CRYPTO_BRIDGE_KEYWORDS = [
    "bitcoin", "crypto", "btc", "ethereum", "digital asset",
    "risk asset", "risk-off", "liquidity", "fed", "rate",
    "inflation", "dollar", "dxy", "yield", "treasury",
    "sanctions", "tariff",
]


def _is_priority_source(story: dict) -> bool:
    """Return True if the story comes from a priority source."""
    source = story.get("source", "").lower()
    return any(ps in source for ps in _PRIORITY_SOURCES)


def _pick_news_prefix(score: int, title: str) -> str:
    """
    Two-tier breaking news prefix.
    ⚡ BREAKING: — critical events (score 9+, hacks, regulatory actions, major exchange news)
    🚨 JUST IN:  — all other qualifying news (score 7-8)
    """
    title_lower = title.lower()
    is_critical = (
        score >= 9
        or any(w in title_lower for w in [
            "hack", "exploit", "stolen", "breach", "bankrupt", "collapse",
            "ban", "sec", "cftc", "lawsuit", "arrest", "sanction",
            "ath", "all-time high",
        ])
    )
    return "⚡ BREAKING:" if is_critical else "🚨 JUST IN:"

# Story hashes we've already seen (persisted across restarts)
_POSTED_HASHES_FILE = os.path.join(os.path.dirname(__file__), ".news_seen.json")
_posted_hashes: dict[str, float] = {}   # hash -> timestamp when first seen


def _load_posted_hashes() -> None:
    global _posted_hashes
    try:
        with open(_POSTED_HASHES_FILE) as f:
            data = json.load(f)
        _posted_hashes = data if isinstance(data, dict) else {}
    except (FileNotFoundError, json.JSONDecodeError):
        _posted_hashes = {}


def _save_posted_hashes() -> None:
    try:
        with open(_POSTED_HASHES_FILE, "w") as f:
            json.dump(_posted_hashes, f)
    except OSError as exc:
        logger.warning("Could not save news seen hashes: %s", exc)


_load_posted_hashes()


def _story_hash(story: dict) -> str:
    """Stable identifier for a story based on its URL."""
    return hashlib.md5(story.get("url", story.get("title", "")).encode()).hexdigest()


def _prune_old_hashes() -> None:
    """Remove hashes older than NEWS_DEDUP_WINDOW to keep memory bounded."""
    cutoff = time.time() - config.NEWS_DEDUP_WINDOW
    to_delete = [h for h, ts in _posted_hashes.items() if ts < cutoff]
    for h in to_delete:
        del _posted_hashes[h]
    if to_delete:
        _save_posted_hashes()


# ── RSS feeds (primary source) ────────────────────────────────────────────────

_RSS_FEEDS = [
    "https://cointelegraph.com/rss",
    "https://decrypt.co/feed",
    "https://www.coinbureau.com/feed/",
]

# Sources that get a +1 score boost and are never deprioritised
_PRIORITY_SOURCES = [
    "cointelegraph", "coindesk", "coinmarketcap", "coin bureau",
    "blocknews", "decrypt",
]

_BREAKING_KEYWORDS = [
    "hack", "exploit", "stolen", "breach", "bankrupt", "collapse",
    "sec", "cftc", "ban", "lawsuit", "arrest", "sanction", "regulation",
    "ath", "all-time high", "etf", "halving",
]


def _fetch_news_rss() -> list[dict]:
    """
    Fetch stories from RSS feeds. Returns normalised story dicts.
    Tries each feed in order; returns combined results from all that succeed.
    """
    stories: list[dict] = []
    for feed_url in _RSS_FEEDS:
        try:
            feed = feedparser.parse(feed_url)
            if feed.bozo and not feed.entries:
                logger.debug("RSS feed parse error for %s: %s", feed_url, feed.bozo_exception)
                continue
            source = feed.feed.get("title", feed_url.split("/")[2])
            for entry in feed.entries[:15]:
                title = entry.get("title", "").strip()
                link  = entry.get("link", "")
                if not title or not link:
                    continue
                stories.append({
                    "title":  title,
                    "url":    link,
                    "source": source,
                    "origin": "rss",
                })
            logger.debug("RSS %s: %d entries", source, len(feed.entries))
        except Exception as exc:
            logger.warning("RSS fetch failed for %s: %s", feed_url, exc)

    return stories


def _fetch_news_newsapi() -> list[dict]:
    """
    Fetch stories from NewsAPI.org (fallback).
    Returns [] if the request fails.
    """
    articles = news_api.fetch_crypto_news()
    return [dict(a, origin="newsapi") for a in articles]


def _fetch_news() -> list[dict]:
    """
    Fetch the latest stories from both RSS feeds and NewsAPI, then combine
    and deduplicate by URL.
    """
    rss_stories = [dict(s, origin="rss") for s in news_api.fetch_rss_news()]
    newsapi_stories = [dict(s, origin="newsapi") for s in news_api.fetch_crypto_news()]

    seen_urls: set[str] = set()
    combined: list[dict] = []
    for story in rss_stories + newsapi_stories:
        url = story.get("url", "")
        if url and url in seen_urls:
            continue
        if url:
            seen_urls.add(url)
        combined.append(story)

    if not combined:
        logger.warning("No news sources available. RSS feeds and NewsAPI both returned nothing.")
    return combined


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


_GEO_MACRO_KEYWORDS = {
    "iran", "china", "russia", "fed", "trump", "tariff", "oil", "dollar",
    "sanctions", "war", "reserve", "opec", "yuan", "nato", "treasury",
    "inflation", "recession", "powell",
    "sec", "blackrock", "fidelity", "coinbase", "binance",
    "hack", "exploit", "breach", "bankrupt", "collapse", "arrest", "fraud",
    "rate cut", "rate hike", "fomc", "stablecoin bill", "crypto bill",
    "clarity act", "etf approved", "etf rejected", "executive order",
    "strategic reserve", "bitcoin reserve",
    "strait", "hormuz", "blockade", "missile", "nuclear", "ceasefire",
    "airstrike", "troops", "military", "pentagon", "conflict", "escalation",
    "warship",
}


def is_geo_macro_story(story: dict) -> bool:
    """Return True if the story title contains a geopolitical/macro keyword."""
    title = story.get("title", "").lower()
    return any(kw in title for kw in _GEO_MACRO_KEYWORDS)


def _is_macro_source(story: dict) -> bool:
    """Check if story comes from a macro/geopolitical source."""
    # RSS stories from crypto-native feeds are not macro sources.
    # CryptoPanic-labelled macro origins still apply.
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
    On unexpected errors returns the story with score=5 and empty commentary.
    """
    try:
        import ai_writer

        if not ai_writer.is_available():
            # Without AI, use keyword matching as a rough filter
            if _has_important_keyword(story):
                score = 8 if _is_priority_source(story) else 7
                story["score"] = score
                story["commentary"] = None  # Will fall back to headline-only
                return story
            story["score"] = 5
            story["commentary"] = ""
            return story

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

        system = f"""You are a crypto news editor for @CryptoVault88 on Twitter. You decide which stories are worth tweeting and write sharp, opinionated commentary that makes people follow you.

SCORING (respond with a number 1-10):
- 10: Market-moving (ETF approval/rejection, major hack/exploit, regulatory bombshell, BTC ATH, war/sanctions, Fed surprise)
- 8-9: Very important (major exchange news, institutional moves, rate decisions, breaking regulatory action, SEC/CFTC enforcement, geopolitical shifts)
- 8-10: Political figures + crypto (Trump, White House, Biden, president + Bitcoin/crypto mention)
- 7-9: Executive quotes (Fink, Saylor, CZ, Vitalik, Musk, Dimon, Wood + crypto/BTC take)
- 8-9: Regulatory clarity/bills (crypto regulation bill, law, legislation, bipartisan crypto act)
- 7: Notable (significant market moves, industry trends, macro data, major on-chain events)
- 5-6: Moderately interesting (prediction markets, mid-tier exchange updates, partnership announcements)
- 4: Minor but tweetable (small project updates, minor data points)
- 1-3: Noise (price predictions, sponsored content, repetitive updates, small company treasury adoption)

CALIBRATION EXAMPLES:
- "BTC drops 10% in 24 hours" → score 7-8
- "ETH outperforming BTC this week" → score 7-8
- "Kraken files for IPO" → score 7-8
- "Polymarket odds shift on election" → score 6-7
- "ECB announces rate decision" / "SEC charges crypto firm" / "Fed signals policy shift" → score 8-10
- "Small company adds BTC to treasury" → score 3-4
- "Crypto influencer makes price prediction" → score 1-2

AUTOMATIC MINIMUM SCORES — apply these before giving your final score:
- Breaking regulatory news from major bodies (SEC, CFTC, ECB, Fed, ban, lawsuit, arrest, sanction): minimum 8
- Major exchange news (hack, exploit, breach, insolvency, bankruptcy, IPO): minimum 9
- Bitcoin ETF news, BTC/ETH ATH, halving: minimum 8
- Major institutional move (BlackRock, Fidelity, MicroStrategy, sovereign fund): minimum 8
- Fed/Powell/interest rates + crypto or Bitcoin mention: minimum 9
- Trump/White House + crypto or Bitcoin mention: minimum 9
- SEC Chair/Gensler/Atkins + crypto: minimum 9
- Named executive quote (Fink/Saylor/CZ/Vitalik/Musk) + crypto: minimum 8
- Bitcoin ETF flows/inflows/outflows: minimum 8
- Crypto regulation bill/law/legislation: minimum 8
- "Strategic Bitcoin Reserve" any mention: minimum 9
- Hack/exploit over $10M: minimum 9
- Gold/commodities + fear/crash/market cap: minimum 8
- Stock market crash/drop + crypto mention: minimum 8
- "Fear" + market context (VIX, fear index, all-time highs): minimum 7
- Oil price spike + macro implications: minimum 7
- Story comparing crypto market cap to traditional assets: minimum 9
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
            story["score"] = 5
            story["commentary"] = ""
            return story

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

        # Boost priority-source stories by +1 (cap at 10)
        if _is_priority_source(story):
            score = min(10, score + 1)

        # Boost major institutional/regulatory keywords +2
        _major_keywords = {"BlackRock", "Fidelity", "ETF", "SEC", "Fed", "Coinbase"}
        if any(kw in title for kw in _major_keywords):
            score = min(10, score + 2)

        # Boost crisis/exploit keywords +3
        _crisis_keywords = {"hack", "exploit", "bankrupt", "arrest"}
        if any(kw in title.lower() for kw in _crisis_keywords):
            score = min(10, score + 3)

        story["score"] = score
        story["commentary"] = commentary

        if score < 4:
            logger.info("News filtered (score %d/10): %.80s", score, title)
            return None

        logger.info("News approved (score %d/10): %.80s", score, title)
        return story

    except Exception as exc:
        logger.warning("_ai_score_and_comment failed for '%.60s': %s",
                       story.get("title", ""), exc)
        story["score"] = 5
        story["commentary"] = ""
        return story


# ── Public API ───────────────────────────────────────────────────────────────

def check_news() -> list[dict]:
    """
    Return list of new story dicts that haven't been posted yet.
    Side-effect: marks returned stories as seen (persisted to disk).
    """
    _prune_old_hashes()
    stories = _fetch_news()
    new_stories = []
    added = False
    for story in stories:
        h = _story_hash(story)
        if h not in _posted_hashes:
            new_stories.append(story)
            _posted_hashes[h] = time.time()
            added = True
    if added:
        _save_posted_hashes()
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

    Layout (spaced sections):
        🟢 HEADLINE

        [1-2 sentence analyst comment]
    """
    title = story.get("title", "Breaking crypto news")
    commentary = story.get("commentary")
    score = story.get("score", 7)

    prefix = _pick_news_prefix(score, title)
    headline = f"{prefix} {title}"

    if commentary:
        # Trim commentary so full tweet fits within 275 chars
        max_commentary = 273 - len(headline) - 2  # "\n\n"
        if len(commentary) > max_commentary:
            commentary = commentary[:max_commentary - 1].rsplit(" ", 1)[0] + "…"
        return f"{headline}\n\n{commentary}"
    else:
        if len(headline) > 273:
            headline = headline[:272].rsplit(" ", 1)[0] + "…"
        return headline


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


def fetch_og_image(url: str) -> str | None:
    """
    Fetch the og:image from an article URL.
    Downloads the image to a temp file and returns its path.
    Returns None on any failure.
    """
    if not url:
        return None
    try:
        headers = {"User-Agent": "Mozilla/5.0 (compatible; CryptoBot/1.0)"}
        page = requests.get(url, timeout=10, headers=headers)
        page.raise_for_status()
        soup = BeautifulSoup(page.text, "html.parser")
        tag = soup.find("meta", property="og:image") or soup.find(
            "meta", attrs={"property": "og:image"}
        )
        img_url = tag.get("content") if tag else None
        if not img_url:
            return None
        img_resp = requests.get(img_url, timeout=10, headers=headers)
        img_resp.raise_for_status()
        ct = img_resp.headers.get("Content-Type", "image/jpeg")
        ext = ".png" if "png" in ct else ".jpg"
        with tempfile.NamedTemporaryFile(delete=False, suffix=ext) as f:
            f.write(img_resp.content)
            return f.name
    except Exception as exc:
        logger.debug("OG image fetch failed for %s: %s", url, exc)
        return None


# ── Narrative clustering ──────────────────────────────────────────────────────

_NARRATIVE_COINS = {
    "BTC", "ETH", "SOL", "BNB", "XRP", "ADA", "DOGE", "AVAX", "DOT", "LINK",
    "MATIC", "UNI", "ATOM", "LTC", "BCH", "ALGO", "NEAR", "FTM", "APT", "ARB",
    "OP", "SUI", "INJ", "TIA", "SEI", "TAO", "HYPE",
}

_NARRATIVE_THEMES = {
    "institutional": ["institutional", "blackrock", "fidelity", "grayscale", "goldman",
                      "jpmorgan", "morgan stanley", "citadel", "pension fund"],
    "regulatory": ["sec", "cftc", "regulation", "lawsuit", "ban", "compliance",
                   "enforcement", "sanctions", "legal"],
    "ETF": ["etf", "spot etf", "bitcoin etf", "ethereum etf", "etf approval",
            "etf inflow", "etf outflow"],
    "stablecoin": ["stablecoin", "usdt", "usdc", "dai", "tether", "circle",
                   "depeg", "peg"],
    "DeFi": ["defi", "tvl", "dex", "amm", "liquidity pool", "yield",
             "lending", "aave", "uniswap", "compound"],
    "AI crypto": ["ai crypto", "artificial intelligence", "ai token", "ai agent",
                  "machine learning", "neural", "gpt", "llm"],
    "RWA": ["rwa", "real world asset", "tokenized", "tokenization",
            "treasury", "t-bill"],
    "Layer2": ["layer 2", "layer2", "l2", "rollup", "zk-rollup", "optimistic",
               "base chain", "arbitrum", "optimism"],
}

# Full-name expansions for coin detection in headlines
_COIN_NAMES = {
    "bitcoin": "BTC", "ethereum": "ETH", "solana": "SOL", "bnb": "BNB",
    "ripple": "XRP", "xrp": "XRP", "cardano": "ADA", "dogecoin": "DOGE",
    "avalanche": "AVAX", "polkadot": "DOT", "chainlink": "LINK",
    "polygon": "MATIC", "uniswap": "UNI", "cosmos": "ATOM",
    "litecoin": "LTC", "algorand": "ALGO", "near protocol": "NEAR",
    "fantom": "FTM", "aptos": "APT", "arbitrum": "ARB",
    "optimism": "OP", "sui": "SUI", "injective": "INJ",
    "celestia": "TIA", "sei": "SEI", "bittensor": "TAO",
    "hyperliquid": "HYPE",
}


class NarrativeCluster:
    """Groups recent stories by coin/theme in a rolling 6-hour window."""

    WINDOW_SECS = 6 * 3600  # 6 hours
    MIN_STORIES = 4          # minimum to flag as emerging

    def __init__(self):
        # theme_or_coin -> list of (timestamp, story_dict)
        self._buckets: dict[str, list[tuple[float, dict]]] = {}

    def add_story(self, story: dict) -> None:
        """Extract topics from a story and file it into buckets."""
        now = time.time()
        topics = self._extract_topics(story)
        for topic in topics:
            self._buckets.setdefault(topic, []).append((now, story))
        self._prune()

    def _extract_topics(self, story: dict) -> list[str]:
        """Return list of coin tickers and theme names found in the story."""
        text = (story.get("title", "") + " " + story.get("summary", "")).lower()
        topics: list[str] = []

        # Check coin tickers (exact word boundary)
        for ticker in _NARRATIVE_COINS:
            if re.search(rf'\b{ticker.lower()}\b', text):
                topics.append(ticker)

        # Check full coin names
        for name, ticker in _COIN_NAMES.items():
            if name in text and ticker not in topics:
                topics.append(ticker)

        # Check macro themes
        for theme, keywords in _NARRATIVE_THEMES.items():
            if any(kw in text for kw in keywords):
                topics.append(theme)

        return topics

    def _prune(self) -> None:
        """Remove entries older than the rolling window."""
        cutoff = time.time() - self.WINDOW_SECS
        for topic in list(self._buckets):
            self._buckets[topic] = [
                (ts, s) for ts, s in self._buckets[topic] if ts > cutoff
            ]
            if not self._buckets[topic]:
                del self._buckets[topic]

    def get_emerging(self) -> dict | None:
        """Return the strongest emerging narrative, or None.

        Uses weighted score: (story_count * 1.5) + (unique_sources * 2) + avg_story_score.
        Returns dict with: theme, story_count, sources, summaries, narrative_score.
        """
        self._prune()
        best_topic: str | None = None
        best_score = 0.0

        for topic, entries in self._buckets.items():
            if len(entries) < self.MIN_STORIES:
                continue
            # Compute unique sources
            unique_sources = set()
            total_story_score = 0
            for _, story in entries:
                unique_sources.add(story.get("source", "Unknown"))
                total_story_score += story.get("score", 5)
            avg_score = total_story_score / len(entries) if entries else 5
            narrative_score = (len(entries) * 1.5) + (len(unique_sources) * 2) + avg_score
            if narrative_score > best_score:
                best_score = narrative_score
                best_topic = topic

        if not best_topic:
            return None

        entries = self._buckets[best_topic]
        seen_titles: set[str] = set()
        sources: list[str] = []
        summaries: list[str] = []
        for _, story in entries:
            title = story.get("title", "")
            if title in seen_titles:
                continue
            seen_titles.add(title)
            src = story.get("source", "Unknown")
            if src not in sources:
                sources.append(src)
            summaries.append(title)

        return {
            "theme": best_topic,
            "story_count": len(entries),
            "narrative_score": round(best_score, 1),
            "sources": sources,
            "summaries": summaries[:5],  # cap at 5 for prompt brevity
        }


# Module-level singleton
_narrative_cluster = NarrativeCluster()


def feed_narrative(story: dict) -> None:
    """Feed a scored story into the narrative clustering engine."""
    _narrative_cluster.add_story(story)


def check_narratives() -> dict | None:
    """Return the strongest emerging narrative if one exists.

    Returns dict: {theme, story_count, sources, summaries} or None.
    """
    return _narrative_cluster.get_emerging()
