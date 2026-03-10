"""
image_generator.py – Create branded tweet images using PIL.

Generates a dark-themed info graphic for each tweet type:
  - Breaking news / hot take
  - Price alert
  - Morning recap

Returns a path to a temporary PNG file ready for media upload.
"""

import io
import os
import tempfile
import textwrap
from typing import Optional

from PIL import Image, ImageDraw, ImageFont

# ── Brand colours ─────────────────────────────────────────────────────────────
BG_DARK       = (10, 12, 18)          # near-black background
ACCENT_ORANGE = (247, 147, 26)        # Bitcoin orange
ACCENT_BLUE   = (88, 101, 242)        # Ethereum / info blue
ACCENT_GREEN  = (0, 200, 100)         # positive / up
ACCENT_RED    = (220, 50, 47)         # negative / down
ACCENT_YELLOW = (255, 196, 0)         # breaking / alert
TEXT_WHITE    = (255, 255, 255)
TEXT_GREY     = (160, 160, 180)
CARD_BG       = (20, 24, 36)          # slightly lighter than bg for card

# Coin accent colours
COIN_COLOURS = {
    "BTC":  ACCENT_ORANGE,
    "ETH":  (100, 120, 255),
    "BNB":  (240, 185, 11),
    "SOL":  (153, 69, 255),
    "XRP":  (0, 170, 228),
    "ADA":  (0, 51, 173),
    "DOGE": (194, 163, 1),
    "AVAX": (232, 65, 66),
    "DOT":  (230, 0, 122),
    "LINK": (55, 91, 210),
}

IMG_W, IMG_H = 1200, 630   # Twitter card optimal size


def _load_font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    """Try to load a system font; fall back to default."""
    candidates = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold else
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf" if bold else
        "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
        "/usr/share/fonts/truetype/freefont/FreeSansBold.ttf" if bold else
        "/usr/share/fonts/truetype/freefont/FreeSans.ttf",
    ]
    for path in candidates:
        if os.path.exists(path):
            try:
                return ImageFont.truetype(path, size)
            except OSError:
                continue
    return ImageFont.load_default()


def _draw_rounded_rect(draw: ImageDraw.Draw, xy, radius: int, fill):
    x0, y0, x1, y1 = xy
    draw.rectangle([x0 + radius, y0, x1 - radius, y1], fill=fill)
    draw.rectangle([x0, y0 + radius, x1, y1 - radius], fill=fill)
    draw.ellipse([x0, y0, x0 + 2*radius, y0 + 2*radius], fill=fill)
    draw.ellipse([x1 - 2*radius, y0, x1, y0 + 2*radius], fill=fill)
    draw.ellipse([x0, y1 - 2*radius, x0 + 2*radius, y1], fill=fill)
    draw.ellipse([x1 - 2*radius, y1 - 2*radius, x1, y1], fill=fill)


def _save_to_temp(img: Image.Image) -> str:
    """Save image to a temp PNG file and return the path."""
    tmp = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
    img.save(tmp.name, "PNG")
    return tmp.name


def _base_canvas() -> tuple[Image.Image, ImageDraw.Draw]:
    """Create a dark base canvas with subtle grid lines."""
    img = Image.new("RGB", (IMG_W, IMG_H), BG_DARK)
    draw = ImageDraw.Draw(img)

    # Subtle background grid
    for x in range(0, IMG_W, 60):
        draw.line([(x, 0), (x, IMG_H)], fill=(20, 24, 32), width=1)
    for y in range(0, IMG_H, 60):
        draw.line([(0, y), (IMG_W, y)], fill=(20, 24, 32), width=1)

    return img, draw


def _add_branding(draw: ImageDraw.Draw, accent=ACCENT_ORANGE):
    """Add CryptoWatchAlert branding bar at the bottom."""
    # Bottom bar
    draw.rectangle([(0, IMG_H - 56), (IMG_W, IMG_H)], fill=accent)
    font_brand = _load_font(22, bold=True)
    font_sub   = _load_font(18)
    draw.text((24, IMG_H - 42), "CryptoWatchAlert", font=font_brand, fill=(0, 0, 0))
    draw.text((IMG_W - 200, IMG_H - 42), "@CoinWatchAlert  •  X", font=font_sub, fill=(30, 30, 30))


def _add_badge(draw: ImageDraw.Draw, label: str, colour, x: int = 30, y: int = 30):
    """Add a coloured label badge (e.g. BREAKING, JUST IN)."""
    font = _load_font(26, bold=True)
    bbox = draw.textbbox((0, 0), label, font=font)
    w = bbox[2] - bbox[0] + 24
    h = bbox[3] - bbox[1] + 14
    _draw_rounded_rect(draw, (x, y, x + w, y + h), radius=8, fill=colour)
    draw.text((x + 12, y + 7), label, font=font, fill=(0, 0, 0))
    return x + w + 16, y + h // 2  # return x end, y center for next element


def generate_breaking_news_image(headline: str, subtext: str = "", accent=ACCENT_YELLOW) -> str:
    """
    Breaking-news style card.

    headline  – main news hook (large text)
    subtext   – 1-2 lines of supporting detail (smaller)
    """
    img, draw = _base_canvas()

    # Accent side bar
    draw.rectangle([(0, 0), (8, IMG_H - 56)], fill=accent)

    # Badge
    badge_x_end, _ = _add_badge(draw, "BREAKING", accent, x=24, y=28)

    # Headline (large, white, word-wrapped)
    font_h = _load_font(54, bold=True)
    font_s = _load_font(32)

    margin = 40
    max_w  = IMG_W - margin * 2

    wrapped = textwrap.fill(headline, width=32)
    draw.text((margin, 100), wrapped, font=font_h, fill=TEXT_WHITE)

    # Subtext
    if subtext:
        sub_wrapped = textwrap.fill(subtext, width=55)
        # Work out where headline ends
        h_bbox = draw.textbbox((margin, 100), wrapped, font=font_h)
        sub_y  = h_bbox[3] + 28
        draw.text((margin, sub_y), sub_wrapped, font=font_s, fill=TEXT_GREY)

    _add_branding(draw, accent=accent)
    return _save_to_temp(img)


def generate_price_alert_image(
    symbol: str,
    price: str,
    pct_change: float,
    window: str = "1h",
) -> str:
    """
    Price alert card showing coin, price, and % change.
    Green for up, red for down.
    """
    img, draw = _base_canvas()

    is_up    = pct_change >= 0
    colour   = ACCENT_GREEN if is_up else ACCENT_RED
    arrow    = "▲" if is_up else "▼"
    coin_col = COIN_COLOURS.get(symbol, ACCENT_ORANGE)

    # Coin accent circle
    cx, cy, r = 120, 200, 80
    draw.ellipse([(cx - r, cy - r), (cx + r, cy + r)], fill=coin_col)
    font_coin = _load_font(42, bold=True)
    bbox = draw.textbbox((0, 0), symbol, font=font_coin)
    tw = bbox[2] - bbox[0]
    draw.text((cx - tw // 2, cy - 26), symbol, font=font_coin, fill=(0, 0, 0))

    # Badge
    badge_label = "PRICE ALERT"
    _add_badge(draw, badge_label, colour, x=24, y=28)

    # Price
    font_price = _load_font(80, bold=True)
    font_pct   = _load_font(56, bold=True)
    font_label = _load_font(28)

    draw.text((240, 120), price, font=font_price, fill=TEXT_WHITE)
    draw.text((240, 220), f"{arrow} {abs(pct_change):.1f}%  ({window})",
              font=font_pct, fill=colour)
    draw.text((240, 300), f"{symbol} — {window} price move", font=font_label, fill=TEXT_GREY)

    _add_branding(draw, accent=colour)
    return _save_to_temp(img)


def generate_morning_recap_image(headlines: list[str]) -> str:
    """
    Morning recap card — sunrise theme, lists top headlines.
    """
    img, draw = _base_canvas()

    # Gradient-ish top strip
    for y in range(120):
        alpha = int(255 * (1 - y / 120))
        r = int(ACCENT_ORANGE[0] * alpha / 255)
        g = int(ACCENT_ORANGE[1] * alpha / 255)
        b = int(ACCENT_ORANGE[2] * alpha / 255)
        draw.rectangle([(0, y), (IMG_W, y + 1)], fill=(r, g, b))

    _add_badge(draw, "MORNING BRIEF", ACCENT_ORANGE, x=24, y=28)

    font_h   = _load_font(38, bold=True)
    font_sub = _load_font(28)

    draw.text((30, 130), "Today's Top Crypto Stories", font=font_h, fill=TEXT_WHITE)

    y = 200
    for i, headline in enumerate(headlines[:4], 1):
        wrapped = textwrap.fill(f"{i}.  {headline}", width=60)
        draw.text((40, y), wrapped, font=font_sub, fill=TEXT_WHITE if i <= 2 else TEXT_GREY)
        bbox = draw.textbbox((40, y), wrapped, font=font_sub)
        y = bbox[3] + 20

    _add_branding(draw, accent=ACCENT_ORANGE)
    return _save_to_temp(img)


def generate_hot_take_image(tweet_text: str, accent=ACCENT_BLUE) -> str:
    """
    Hot take / opinion card.
    """
    img, draw = _base_canvas()

    # Quote mark decoration
    font_quote = _load_font(200, bold=True)
    draw.text((IMG_W - 160, -30), "\u201c", font=font_quote, fill=(30, 35, 55))

    _add_badge(draw, "ANALYST TAKE", accent, x=24, y=28)

    font_body  = _load_font(44, bold=True)
    font_small = _load_font(26)

    # Truncate and wrap
    short = tweet_text[:220]
    wrapped = textwrap.fill(short, width=38)
    draw.text((40, 100), wrapped, font=font_body, fill=TEXT_WHITE)

    draw.text((40, IMG_H - 90), "Data-driven. No financial advice.",
              font=font_small, fill=TEXT_GREY)

    _add_branding(draw, accent=accent)
    return _save_to_temp(img)


def generate_image_for_tweet(tweet_text: str, tweet_type: str = "news",
                              symbol: str = "", price: str = "",
                              pct_change: float = 0.0, window: str = "1h",
                              headlines: Optional[list[str]] = None) -> Optional[str]:
    """
    Dispatcher — returns a temp image path based on tweet_type.

    tweet_type: 'price_alert' | 'morning_recap' | 'hot_take' | 'news'
    Returns None on any error so callers can post text-only as fallback.
    """
    try:
        if tweet_type == "price_alert":
            return generate_price_alert_image(symbol, price, pct_change, window)
        if tweet_type == "morning_recap":
            return generate_morning_recap_image(headlines or [])
        if tweet_type == "hot_take":
            return generate_hot_take_image(tweet_text)
        # Default: breaking news
        lines = tweet_text.split("\n", 1)
        headline = lines[0].lstrip("🚨🔥⚡️🛑⚠️ ").replace("BREAKING:", "").replace("JUST IN:", "").strip()
        subtext  = lines[1].strip() if len(lines) > 1 else ""
        return generate_breaking_news_image(headline, subtext)
    except Exception:
        import logging
        logging.getLogger(__name__).warning("Image generation failed", exc_info=True)
        return None
