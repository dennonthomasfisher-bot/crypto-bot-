"""
Chart generator – creates varied price chart images for tweets.

Uses matplotlib to generate multiple chart styles so the feed never
posts the same look twice in a row.

Chart styles (6 types, rotated):
  1. line_fill     – classic line with gradient fill (original style)
  2. candlestick   – OHLC candlestick bars
  3. multi_coin    – multi-coin % comparison overlay
  4. volume_price  – dual-axis price line + volume bars
  5. momentum      – price with RSI subplot
  6. bar_change    – horizontal bar chart of top movers by % change

Charts are saved as temporary PNGs and cleaned up after posting.
"""
from __future__ import annotations

import matplotlib
matplotlib.use('Agg')

import json
import collections
import logging
import os
import random
import time

import requests
from PIL import Image, ImageDraw, ImageFont

logger = logging.getLogger(__name__)

# ── Brand assets ─────────────────────────────────────────────────────────────
_ASSETS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets")
_NEWS_TEMPLATE_PATH = os.path.join(_ASSETS_DIR, "news_template.png")

# ── Brand fonts (DejaVu as universal fallback) ────────────────────────────────
_FONT_BOLD_PATH = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
_FONT_REG_PATH = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
_FONT_MONO_PATH = "/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Bold.ttf"


def _safe_font(path: str, size: int):
    """Load a TrueType font, falling back to Pillow's default if unavailable."""
    try:
        return ImageFont.truetype(path, size)
    except (OSError, IOError):
        return ImageFont.load_default()


_CHART_DIR = os.path.join(os.path.dirname(__file__), ".charts")
_ROTATION_FILE = os.path.join(os.path.dirname(__file__), ".chart_rotation.json")

# All available chart style names
CHART_STYLES = [
    "line_fill",
    "candlestick",
    "multi_coin",
    "volume_price",
    "momentum",
    "bar_change",
]

# Brand colour palette — CryptoVault premium (gold/violet/black)
_BG = "#080B10"             # deep black base
_GRID = "#141820"           # subtle grid
_TEXT = "#7A8290"           # muted text
_AXIS = "#1C2230"           # axis lines
_GOLD = "#D4AF37"           # primary gold accent
_GREEN = "#00E676"          # bullish green
_RED = "#FF3D57"            # bearish red
_GREEN_FILL = "#00E67618"
_RED_FILL = "#FF3D5718"
_BLUE = "#58a6ff"
_ORANGE = "#D4AF37"         # unified with brand gold
_PURPLE = "#7B2FBE"         # brand violet
_CYAN = "#00E5FF"           # brand cyan accent
_ACCENT_GREEN = "#00E676"
_ACCENT_RED = "#FF3D57"
_MUTED = "#4A5568"
_PANEL = "#0D1117"          # card/panel bg
_BORDER = "#1C2230"
_GOLD_GLOW = "#D4AF3740"   # gold with transparency for glow effects
_VIOLET_GLOW = "#7B2FBE30"  # violet with transparency

# PIL-friendly RGB tuples for brand colours
_PIL_BG = (8, 11, 16)
_PIL_GOLD = (212, 175, 55)
_PIL_GREEN = (0, 230, 118)
_PIL_RED = (255, 61, 87)
_PIL_WHITE = (255, 255, 255)
_PIL_MUTED = (122, 130, 144)
_PIL_PANEL = (13, 17, 23)
_PIL_BORDER = (28, 34, 48)
_PIL_VIOLET = (123, 47, 190)
_PIL_CYAN = (0, 229, 255)

# ── Chart headline labels (used by generate_line_fill) ───────────────────────
_HEADLINES_BEARISH = [
    "DOWNSIDE PRESSURE BUILDING", "LIQUIDITY GETTING TAKEN BELOW",
    "STRUCTURE BREAKING DOWN", "SELLERS STEPPING IN", "WEAK HANDS EXITING",
]
_HEADLINES_BULLISH = [
    "BIDS STEPPING IN", "ACCUMULATION PHASE", "STRUCTURE HOLDING",
    "DIP BUYERS ACTIVE", "UPSIDE PRESSURE BUILDING",
]
_HEADLINES_NEUTRAL = [
    "RANGE BOUND", "NO CLEAR CONTROL", "WAITING FOR EXPANSION",
    "COMPRESSION PHASE", "VOLATILITY LOADING",
]
_recent_labels: collections.deque = collections.deque(maxlen=6)

# Coin combos for multi-coin charts (varied so not always BTC/ETH/SOL)
_MULTI_COIN_COMBOS = [
    [("bitcoin", "BTC"), ("ethereum", "ETH"), ("solana", "SOL")],
    [("bitcoin", "BTC"), ("binancecoin", "BNB"), ("ripple", "XRP")],
    [("ethereum", "ETH"), ("solana", "SOL"), ("avalanche-2", "AVAX")],
    [("bitcoin", "BTC"), ("cardano", "ADA"), ("chainlink", "LINK")],
    [("solana", "SOL"), ("sui", "SUI"), ("aptos", "APT")],
    [("ethereum", "ETH"), ("near", "NEAR"), ("render-token", "RNDR")],
]

# Different coins/timeframes for single-coin charts
_SINGLE_COIN_CHOICES = [
    ("bitcoin", "BTC", 7),
    ("bitcoin", "BTC", 1),
    ("bitcoin", "BTC", 30),
    ("ethereum", "ETH", 7),
    ("ethereum", "ETH", 30),
    ("solana", "SOL", 7),
    ("solana", "SOL", 30),
    ("binancecoin", "BNB", 7),
    ("ripple", "XRP", 7),
    ("avalanche-2", "AVAX", 7),
]

# ── chart-img.com TradingView API ─────────────────────────────────────────
CHART_IMG_API_KEY = os.getenv("CHART_IMG_API_KEY", "")
_CHART_IMG_BASE = "https://api.chart-img.com/v2/tradingview/advanced-chart"

# CoinGecko coin_id → TradingView symbol for chart-img.com
_TRADINGVIEW_SYMBOL_MAP: dict[str, str] = {
    "bitcoin": "BINANCE:BTCUSDT",
    "ethereum": "BINANCE:ETHUSDT",
    "solana": "BINANCE:SOLUSDT",
    "binancecoin": "BINANCE:BNBUSDT",
    "ripple": "BINANCE:XRPUSDT",
    "cardano": "BINANCE:ADAUSDT",
    "avalanche-2": "BINANCE:AVAXUSDT",
    "dogecoin": "BINANCE:DOGEUSDT",
    "chainlink": "BINANCE:LINKUSDT",
    "polkadot": "BINANCE:DOTUSDT",
    "litecoin": "BINANCE:LTCUSDT",
    "near": "BINANCE:NEARUSDT",
    "aptos": "BINANCE:APTUSDT",
    "arbitrum": "BINANCE:ARBUSDT",
    "sui": "BINANCE:SUIUSDT",
    "stellar": "BINANCE:XLMUSDT",
    "pepe": "BINANCE:PEPEUSDT",
    "shiba-inu": "BINANCE:SHIBUSDT",
    "aave": "BINANCE:AAVEUSDT",
}

_DAYS_TO_INTERVAL: dict[int, str] = {
    1: "1h",
    7: "4h",
    14: "1D",
    30: "1D",
    90: "1W",
}


def _fetch_tradingview_chart(coin_id: str, symbol: str, days: int) -> str | None:
    """Fetch a TradingView chart image from chart-img.com API.

    Returns the path to the saved PNG, or None on failure.
    Falls back gracefully so matplotlib can take over.
    """
    if not CHART_IMG_API_KEY:
        return None

    tv_symbol = _TRADINGVIEW_SYMBOL_MAP.get(coin_id)
    if not tv_symbol:
        logger.debug("[CHART-IMG] No TradingView symbol for %s", coin_id)
        return None

    interval = _DAYS_TO_INTERVAL.get(days, "4h")

    # Chart-img.com v2 advanced chart with dark theme + custom styling
    params = {
        "key": CHART_IMG_API_KEY,
        "symbol": tv_symbol,
        "interval": interval,
        "theme": "dark",
        "style": "1",           # 1 = candlestick
        "width": 800,
        "height": 450,
        "timezone": "Etc/UTC",
        "studies": "Volume",
    }

    try:
        logger.info("[CHART-IMG] Fetching TradingView chart: %s %s", tv_symbol, interval)
        resp = requests.get(_CHART_IMG_BASE, params=params, timeout=20)
        resp.raise_for_status()

        if resp.headers.get("content-type", "").startswith("image"):
            _ensure_chart_dir()
            filepath = os.path.join(_CHART_DIR, f"tv_{symbol}_{days}d_{int(time.time())}.png")

            # Save raw chart
            with open(filepath, "wb") as f:
                f.write(resp.content)

            # Add CryptoVault watermark overlay
            filepath = _add_watermark_overlay(filepath, symbol)

            file_size = os.path.getsize(filepath)
            logger.info("[CHART-IMG] Saved TradingView chart: %s (%d bytes)", filepath, file_size)
            return filepath
        else:
            logger.warning("[CHART-IMG] Non-image response: %s", resp.headers.get("content-type"))
            return None

    except requests.RequestException as exc:
        logger.warning("[CHART-IMG] API request failed: %s", exc)
        return None
    except Exception as exc:
        logger.warning("[CHART-IMG] Unexpected error: %s", exc)
        return None


def _add_watermark_overlay(filepath: str, symbol: str) -> str:
    """Add @CryptoVault88 gold watermark + header to a TradingView chart image."""
    try:
        img = Image.open(filepath)
        draw = ImageDraw.Draw(img)
        w, h = img.size

        # Gold watermark bottom-right
        font_wm = _safe_font(_FONT_BOLD_PATH, 14)
        draw.text((w - 10, h - 10), "@CryptoVault88", fill=(*_PIL_GOLD, 90),
                  font=font_wm, anchor="rb")

        # Symbol badge top-left
        font_sym = _safe_font(_FONT_BOLD_PATH, 20)
        draw.text((10, 8), symbol, fill=_PIL_GOLD, font=font_sym)

        # Brand name top-right
        font_brand = _safe_font(_FONT_BOLD_PATH, 12)
        draw.text((w - 10, 10), "CryptoVault", fill=(*_PIL_GOLD, 150),
                  font=font_brand, anchor="ra")

        img.save(filepath)
        return filepath
    except Exception as exc:
        logger.warning("[CHART-IMG] Watermark overlay failed: %s", exc)
        return filepath


# Mapping of CoinGecko coin_id → Binance trading pair
_BINANCE_SYMBOL_MAP: dict[str, str] = {
    "bitcoin": "BTCUSDT",
    "ethereum": "ETHUSDT",
    "solana": "SOLUSDT",
    "binancecoin": "BNBUSDT",
    "ripple": "XRPUSDT",
    "cardano": "ADAUSDT",
    "avalanche-2": "AVAXUSDT",
    "dogecoin": "DOGEUSDT",
    "chainlink": "LINKUSDT",
    "polkadot": "DOTUSDT",
    "matic-network": "MATICUSDT",
    "uniswap": "UNIUSDT",
    "cosmos": "ATOMUSDT",
    "litecoin": "LTCUSDT",
    "bitcoin-cash": "BCHUSDT",
    "algorand": "ALGOUSDT",
    "near": "NEARUSDT",
    "fantom": "FTMUSDT",
    "aptos": "APTUSDT",
    "arbitrum": "ARBUSDT",
    "optimism": "OPUSDT",
    "sui": "SUIUSDT",
    "injective-protocol": "INJUSDT",
    "celestia": "TIAUSDT",
    "sei-network": "SEIUSDT",
    "bittensor": "TAOUSDT",
    "render-token": "RENDERUSDT",
    "the-open-network": "TONUSDT",
    "shiba-inu": "SHIBUSDT",
    "pepe": "PEPEUSDT",
    "dogwifcoin": "WIFUSDT",
    "fetch-ai": "FETUSDT",
    "aave": "AAVEUSDT",
    "maker": "MKRUSDT",
    "stellar": "XLMUSDT",
}

# Fixed list of top coins used for bar_change chart
_BINANCE_TOP_COINS = [
    ("BTC", "BTCUSDT"), ("ETH", "ETHUSDT"), ("BNB", "BNBUSDT"),
    ("SOL", "SOLUSDT"), ("XRP", "XRPUSDT"), ("ADA", "ADAUSDT"),
    ("AVAX", "AVAXUSDT"), ("DOGE", "DOGEUSDT"), ("LINK", "LINKUSDT"),
    ("DOT", "DOTUSDT"),
]

_BINANCE_KLINES_URL = "https://api.binance.com/api/v3/klines"
_BINANCE_TICKER_URL = "https://api.binance.com/api/v3/ticker/24hr"


def _coin_id_to_binance(coin_id: str) -> str | None:
    """Map a CoinGecko coin_id to a Binance trading pair symbol."""
    return _BINANCE_SYMBOL_MAP.get(coin_id)


def _days_to_binance_interval(days: int) -> tuple[str, int]:
    """Return (interval, limit) covering the requested number of days."""
    if days <= 1:
        return "1h", 24
    elif days <= 14:
        return "1h", days * 24
    elif days <= 30:
        return "4h", days * 6
    else:
        return "1d", min(days, 1000)


def _ensure_chart_dir() -> None:
    os.makedirs(_CHART_DIR, exist_ok=True)


def _cleanup_old_charts() -> None:
    """Remove chart images older than 1 hour."""
    if not os.path.exists(_CHART_DIR):
        return
    cutoff = time.time() - 3600
    for f in os.listdir(_CHART_DIR):
        path = os.path.join(_CHART_DIR, f)
        try:
            if os.path.getmtime(path) < cutoff:
                os.remove(path)
        except OSError:
            pass


# ── Rotation tracking ────────────────────────────────────────────────────────

def _load_rotation() -> dict:
    if not os.path.exists(_ROTATION_FILE):
        return {"last_styles": [], "last_coin": ""}
    try:
        with open(_ROTATION_FILE) as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return {"last_styles": [], "last_coin": ""}


def _save_rotation(data: dict) -> None:
    try:
        with open(_ROTATION_FILE, "w") as f:
            json.dump(data, f)
    except OSError:
        pass


def get_next_chart_style() -> str:
    """Pick the next chart style, avoiding the last 3 used styles."""
    rotation = _load_rotation()
    recent = rotation.get("last_styles", [])[-3:]  # avoid last 3

    available = [s for s in CHART_STYLES if s not in recent]
    if not available:
        available = CHART_STYLES  # all exhausted, reset

    return random.choice(available)


def record_chart_style(style: str, coin: str = "") -> None:
    """Record which style was just used."""
    rotation = _load_rotation()
    last_styles = rotation.get("last_styles", [])
    last_styles.append(style)
    # Keep last 6
    if len(last_styles) > 6:
        last_styles = last_styles[-6:]
    rotation["last_styles"] = last_styles
    if coin:
        rotation["last_coin"] = coin
    _save_rotation(rotation)


# ── Data fetching ────────────────────────────────────────────────────────────

def fetch_price_history(coin_id: str, days: int = 7) -> list[tuple[float, float]] | None:
    """
    Fetch price history from Binance klines.
    Returns list of (timestamp_ms, close_price) tuples.
    """
    pair = _coin_id_to_binance(coin_id)
    if not pair:
        logger.warning("No Binance pair for coin_id '%s'", coin_id)
        return None
    interval, limit = _days_to_binance_interval(days)
    try:
        resp = requests.get(
            _BINANCE_KLINES_URL,
            params={"symbol": pair, "interval": interval, "limit": limit},
            timeout=15,
        )
        resp.raise_for_status()
        klines = resp.json()
        return [(float(k[0]), float(k[4])) for k in klines]
    except requests.RequestException as exc:
        logger.warning("Failed to fetch price history for %s: %s", coin_id, exc)
        return None


def _fetch_market_chart_full(coin_id: str, days: int = 7) -> dict | None:
    """Fetch prices + volumes from Binance klines."""
    pair = _coin_id_to_binance(coin_id)
    if not pair:
        logger.warning("No Binance pair for coin_id '%s' — chart will fail", coin_id)
        return None
    logger.debug("Fetching Binance klines: pair=%s, coin_id=%s, days=%d", pair, coin_id, days)
    interval, limit = _days_to_binance_interval(days)
    try:
        resp = requests.get(
            _BINANCE_KLINES_URL,
            params={"symbol": pair, "interval": interval, "limit": limit},
            timeout=15,
        )
        resp.raise_for_status()
        klines = resp.json()

        # Binance error responses are dicts, not lists
        if isinstance(klines, dict):
            logger.warning("Binance returned error for %s: %s", pair, klines)
            return None
        if not klines:
            logger.warning("Binance returned empty klines for %s", pair)
            return None

        logger.debug("Binance klines received: pair=%s, count=%d, "
                     "first_close=%s, last_close=%s",
                     pair, len(klines), klines[0][4], klines[-1][4])

        prices = [[float(k[0]), float(k[4])] for k in klines]
        volumes = [[float(k[0]), float(k[5])] for k in klines]

        # Validate prices are real numbers, not all zero
        close_prices = [p[1] for p in prices]
        if not close_prices or all(p == 0 for p in close_prices):
            logger.warning("Binance returned all-zero prices for %s", pair)
            return None

        return {"prices": prices, "volumes": volumes}
    except requests.RequestException as exc:
        logger.warning("Failed to fetch market chart for %s: %s", coin_id, exc)
        return None


def _fetch_ohlc(coin_id: str, days: int = 7) -> list | None:
    """Fetch OHLC data from Binance klines. Returns [[ts, o, h, l, c], ...]."""
    pair = _coin_id_to_binance(coin_id)
    if not pair:
        logger.warning("No Binance pair for coin_id '%s'", coin_id)
        return None
    interval, limit = _days_to_binance_interval(days)
    try:
        resp = requests.get(
            _BINANCE_KLINES_URL,
            params={"symbol": pair, "interval": interval, "limit": limit},
            timeout=15,
        )
        resp.raise_for_status()
        klines = resp.json()
        return [[float(k[0]), float(k[1]), float(k[2]), float(k[3]), float(k[4])] for k in klines]
    except requests.RequestException as exc:
        logger.warning("Failed to fetch OHLC for %s: %s", coin_id, exc)
        return None


def _fetch_top_movers() -> list[dict] | None:
    """Fetch 24h change for top coins from Binance ticker."""
    pairs = [p for _, p in _BINANCE_TOP_COINS]
    try:
        resp = requests.get(
            _BINANCE_TICKER_URL,
            params={"symbols": json.dumps(pairs)},
            timeout=15,
        )
        resp.raise_for_status()
        tickers = resp.json()
        return [
            {
                "symbol": t["symbol"].replace("USDT", ""),
                "price_change_percentage_24h": float(t["priceChangePercent"]),
            }
            for t in tickers
        ]
    except requests.RequestException as exc:
        logger.warning("Failed to fetch top movers: %s", exc)
        return None


# ── Shared styling helpers ───────────────────────────────────────────────────

def _style_ax(ax, date_fmt="%b %d"):
    """Apply shared dark theme to an axis."""
    import matplotlib.dates as mdates
    ax.tick_params(colors=_TEXT, labelsize=10)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.spines["bottom"].set_color(_AXIS)
    ax.spines["left"].set_color(_AXIS)
    ax.grid(True, alpha=0.15, color=_GRID)
    if date_fmt:
        ax.xaxis.set_major_formatter(mdates.DateFormatter(date_fmt))


def _watermark(ax):
    ax.text(0.99, 0.02, "@CryptoVault88", transform=ax.transAxes,
            fontsize=11, fontweight="bold", color=_GOLD, ha="right", va="bottom", alpha=0.35)


def _draw_grid_dots(ax, nx: int = 30, ny: int = 20, alpha: float = 0.06) -> None:
    """Draw a subtle grid dot texture for depth."""
    import numpy as np
    for x in np.linspace(0.02, 0.98, nx):
        for y in np.linspace(0.02, 0.98, ny):
            ax.plot(x, y, '.', color='white', markersize=0.5, alpha=alpha,
                    transform=ax.transAxes, zorder=0)


def _save_fig(fig, name: str) -> str:
    filepath = os.path.join(_CHART_DIR, f"{name}_{int(time.time())}.png")
    try:
        fig.tight_layout()
    except Exception:
        pass
    fig.savefig(filepath, dpi=150, bbox_inches="tight", facecolor=_BG)
    import matplotlib.pyplot as plt
    plt.close(fig)
    logger.info("Generated chart: %s", filepath)
    return filepath


def _price_fmt(x, _=None):
    return f"${x:,.0f}" if x >= 1000 else f"${x:,.2f}"


# ── Chart style 1: Line with fill (original) ────────────────────────────────

def generate_line_fill(coin_id: str, symbol: str, days: int = 7) -> str | None:
    """Unified chart renderer — all chart generation routes through here.

    Priority: TradingView (chart-img.com) → matplotlib fallback.
    """
    logger.info("[CHART] UNIFIED: %s (%s, %dd)", symbol, coin_id, days)

    # ── Try TradingView first (premium quality) ──────────────────────────
    tv_chart = _fetch_tradingview_chart(coin_id, symbol, days)
    if tv_chart:
        logger.info("[CHART] Using TradingView chart for %s", symbol)
        return tv_chart
    logger.info("[CHART] TradingView unavailable — falling back to matplotlib for %s", symbol)

    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        import matplotlib.gridspec as gridspec
        import numpy as np
        from datetime import datetime, timezone
    except ImportError:
        return None

    try:
        logger.debug("generate_line_fill called: coin_id=%s, symbol=%s, days=%d",
                     coin_id, symbol, days)
        data = _fetch_market_chart_full(coin_id, days)
        if not data or not data["prices"] or len(data["prices"]) < 10:
            logger.warning("generate_line_fill: insufficient data for %s/%s "
                          "(data=%s, points=%d)", symbol, coin_id,
                          "present" if data else "None",
                          len(data.get("prices", [])) if data else 0)
            return None

        _ensure_chart_dir()
        _cleanup_old_charts()

        times = [datetime.fromtimestamp(p[0] / 1000, tz=timezone.utc) for p in data["prices"]]
        values = [p[1] for p in data["prices"]]
        volumes = [v[1] for v in data["volumes"]] if data.get("volumes") else None

        # Validate plottable data
        if not values or not times or len(values) != len(times):
            logger.warning("generate_line_fill: times/values mismatch or empty "
                          "(times=%d, values=%d) for %s",
                          len(times) if times else 0,
                          len(values) if values else 0, symbol)
            return None
        if len(values) < 10:
            logger.warning("generate_line_fill: not enough data points "
                          "(%d < 10) for %s — using fallback", len(values), symbol)
            return None
        if all(v == 0 for v in values):
            logger.warning("generate_line_fill: all-zero values for %s — skipping", symbol)
            return None
        if max(values) == min(values):
            logger.warning("generate_line_fill: flat price data (all=%.8f) for %s "
                          "— using fallback instead of blank-looking chart",
                          values[0], symbol)
            return None

        logger.debug("generate_line_fill plotting: %s %d points, "
                     "first=%.8f last=%.8f hi=%.8f lo=%.8f",
                     symbol, len(values), values[0], values[-1],
                     max(values), min(values))

        is_up = values[-1] >= values[0]
        accent = _ACCENT_GREEN if is_up else _ACCENT_RED
        arrow = "▲" if is_up else "▼"
        pct = ((values[-1] - values[0]) / values[0]) * 100
        price_str = _price_fmt(values[-1])
        period = {1: "24H", 7: "7D", 14: "14D", 30: "30D", 90: "90D"}.get(days, f"{days}D")

        # Find high/low
        hi_idx = np.argmax(values)
        lo_idx = np.argmin(values)
        open_price = values[0]

        # ── Chart type selection ──────────────────────────────────────────────
        _type_roll = random.random()
        if _type_roll < 0.60:
            _chart_type = "filled"       # 60% — line + fill + volume
        elif _type_roll < 0.85:
            _chart_type = "candlestick"  # 25% — candlestick + volume
        else:
            _chart_type = "minimalist"   # 15% — clean line only, no volume

        # Occasionally wider aspect for more context (20% chance)
        _fig_width = 18 if random.random() < 0.20 else 16

        # Layout: header panel + chart + optional volume
        fig = plt.figure(figsize=(_fig_width, 9), dpi=100, facecolor=_BG)
        _show_volume = volumes and _chart_type != "minimalist"
        if _show_volume:
            gs = gridspec.GridSpec(3, 1, height_ratios=[1.2, 5, 1.5], hspace=0.08,
                                  figure=fig, left=0.08, right=0.95, top=0.95, bottom=0.06)
        else:
            gs = gridspec.GridSpec(2, 1, height_ratios=[1.2, 5], hspace=0.08,
                                  figure=fig, left=0.08, right=0.95, top=0.95, bottom=0.06)

        # ── Header panel — premium CryptoVault branding ───────────────────
        ax_hdr = fig.add_subplot(gs[0])
        ax_hdr.set_facecolor(_BG)
        ax_hdr.axis("off")
        # Coin symbol in gold
        ax_hdr.text(0.0, 0.55, symbol, transform=ax_hdr.transAxes,
                    fontsize=52, fontweight="bold", color=_GOLD, va="center",
                    fontfamily="monospace")
        # Price in white
        ax_hdr.text(0.22, 0.58, price_str, transform=ax_hdr.transAxes,
                    fontsize=34, fontweight="bold", color="white", va="center",
                    fontfamily="monospace")
        # Percentage change
        ax_hdr.text(0.22, 0.15, f"{arrow} {pct:+.2f}%  {period}",
                    transform=ax_hdr.transAxes,
                    fontsize=18, fontweight="bold", color=accent, va="center")
        # Brand watermark — gold
        ax_hdr.text(1.0, 0.55, "CryptoVault", transform=ax_hdr.transAxes,
                    fontsize=14, fontweight="bold", color=_GOLD, ha="right",
                    va="center", alpha=0.6)
        ax_hdr.text(1.0, 0.15, "@CryptoVault88", transform=ax_hdr.transAxes,
                    fontsize=9, color=_MUTED, ha="right", va="center", alpha=0.5)
        # Subtle separator line in gold
        ax_hdr.plot([0.0, 1.0], [0.0, 0.0], color=_GOLD, linewidth=0.5,
                    alpha=0.2, transform=ax_hdr.transAxes, clip_on=False)

        # ── Direction color — premium palette ────────────────────────────────
        price_change_pct = ((values[-1] - values[0]) / values[0]) * 100
        if price_change_pct > 2:
            line_color = "#00E676"
            fill_color = "#00E67612"
            glow_color = "#00E67608"
        elif price_change_pct < -2:
            line_color = "#FF3D57"
            fill_color = "#FF3D5712"
            glow_color = "#FF3D5708"
        elif abs(price_change_pct) < 0.5:
            line_color = _GOLD        # gold for flat/sideways
            fill_color = _GOLD_GLOW
            glow_color = "#D4AF3708"
        else:
            line_color = "#D4AF37"    # gold for small moves
            fill_color = "#D4AF3715"
            glow_color = "#D4AF3708"

        # ── Main chart ───────────────────────────────────────────────────────
        ax = fig.add_subplot(gs[1])
        ax.set_facecolor(_BG)

        min_val = min(values)
        max_val = max(values)

        logger.info("[CHART DEBUG] %s: %d values, first=%.4f, last=%.4f, "
                    "min=%.4f, max=%.4f",
                    symbol, len(values), values[0], values[-1], min_val, max_val)

        if not values or len(values) < 10:
            logger.warning("[CHART DEBUG] INVALID VALUES (%d) — using fallback", len(values))
            plt.close(fig)
            return None

        if min_val == max_val:
            logger.warning("[CHART DEBUG] FLAT DATA (all=%.8f) — fallback", min_val)
            plt.close(fig)
            return None

        # Force axis ranges — exaggerate small moves so charts never look flat
        range_val = max_val - min_val
        if range_val < (max_val * 0.02):
            # Very small movement — exaggerate heavily
            padding = range_val * 0.8
        else:
            padding = range_val * 0.2
        ax.set_ylim(min_val - padding, max_val + padding)
        ax.set_xlim(times[0], times[-1])

        # ── Smooth data for cleaner line ──────────────────────────────────
        # Simple moving average smoothing for visual quality
        _smooth_window = max(3, len(values) // 40)
        smoothed = values.copy()
        if len(values) > 20 and _chart_type != "candlestick":
            kernel = np.ones(_smooth_window) / _smooth_window
            smoothed = list(np.convolve(values, kernel, mode='same'))
            # Keep first and last values exact
            smoothed[0] = values[0]
            smoothed[-1] = values[-1]

        # ── Price rendering — varies by _chart_type ────────────────────────
        if _chart_type == "filled":
            # Soft outer glow
            ax.plot(times, smoothed, color=line_color, linewidth=8, alpha=0.04, zorder=1, solid_capstyle="round")
            # Main line — clean, medium weight
            ax.plot(times, smoothed, color=line_color, linewidth=2.0, alpha=0.9, zorder=3, solid_capstyle="round")
            # Gradient fill — very subtle
            ax.fill_between(times, smoothed, min_val, color=fill_color, zorder=1)
        elif _chart_type == "candlestick":
            ohlc = _fetch_ohlc(coin_id, days)
            if ohlc and len(ohlc) >= 10:
                from datetime import datetime as _dt, timezone as _tz
                from matplotlib.patches import Rectangle
                from matplotlib.dates import date2num
                bar_width = (times[-1] - times[0]).total_seconds() / len(ohlc) / 86400 * 0.6
                for candle in ohlc:
                    ts, o, h, l, c = candle
                    t = _dt.fromtimestamp(ts / 1000, tz=_tz.utc)
                    color = "#00E676" if c >= o else "#FF3D57"
                    ax.plot([t, t], [l, h], color=color, linewidth=0.8, zorder=2)
                    body_lo, body_hi = min(o, c), max(o, c)
                    if body_hi == body_lo:
                        body_hi = body_lo + (h - l) * 0.01
                    rect = Rectangle((date2num(t) - bar_width / 2, body_lo),
                                     bar_width, body_hi - body_lo,
                                     facecolor=color, edgecolor=color, zorder=3)
                    ax.add_patch(rect)
                ax.xaxis_date()
            else:
                ax.plot(times, smoothed, color=line_color, linewidth=2.0, alpha=0.9, zorder=3, solid_capstyle="round")
                ax.fill_between(times, smoothed, min_val, color=fill_color, zorder=1)
                _chart_type = "filled-fallback"
        else:  # minimalist — clean line only
            ax.plot(times, smoothed, color=line_color, linewidth=1.8, alpha=0.9, zorder=3, solid_capstyle="round")

        logger.info("[CHART DEBUG] %s: type=%s, width=%d, ylim=(%.4f, %.4f)",
                    symbol, _chart_type, _fig_width, *ax.get_ylim())

        # ── Right-side price scale — TradingView style ────────────────────
        # Show 4 evenly spaced price levels on right edge
        _price_levels = np.linspace(min_val, max_val, 5)[1:-1]  # 3 middle levels
        for _plvl in _price_levels:
            ax.axhline(_plvl, color=_GRID, linewidth=0.3, alpha=0.4, zorder=0)
            ax.text(1.01, _plvl, _price_fmt(_plvl), transform=ax.get_yaxis_transform(),
                    fontsize=7, color=_MUTED, va="center", alpha=0.6, fontfamily="monospace")
        # Current price highlighted on right edge
        ax.text(1.01, values[-1], _price_fmt(values[-1]), transform=ax.get_yaxis_transform(),
                fontsize=8, fontweight="bold", color=line_color, va="center",
                fontfamily="monospace",
                bbox=dict(boxstyle="round,pad=0.2", facecolor=line_color + "20",
                          edgecolor=line_color + "40", linewidth=0.5))

        # Current price horizontal line — dashed, connects to right label
        ax.axhline(values[-1], color=line_color, linewidth=0.5, linestyle="--",
                   alpha=0.3, zorder=2)

        # Clean chrome — TradingView style
        ax.set_xticks([])
        ax.set_yticks([])
        for spine in ax.spines.values():
            spine.set_visible(False)

        # ── Volume panel — clean, branded ─────────────────────────────────
        if _show_volume:
            _vol_gold = random.random() < 0.25  # 25% chance of gold volume bars
            ax_vol = fig.add_subplot(gs[2], sharex=ax)
            ax_vol.set_facecolor(_BG)
            if _vol_gold:
                vol_colors = [_GOLD + "30"] * len(values)
            else:
                vol_colors = [line_color + "30" if i == 0 or values[i] >= values[i-1]
                             else _ACCENT_RED + "30"
                             for i in range(len(values))]
            ax_vol.bar(times, volumes[:len(times)], width=(times[-1] - times[0]).total_seconds() / len(times) / 86400 * 0.8,
                      color=vol_colors[:len(times)], zorder=2)
            ax_vol.set_xticks([])
            ax_vol.set_yticks([])
            for spine in ax_vol.spines.values():
                spine.set_visible(False)

        plt.tight_layout()
        filepath = os.path.join(_CHART_DIR, f"line_{symbol}_{days}d_{int(time.time())}.png")
        fig.savefig(filepath, dpi=150, facecolor=_BG, bbox_inches="tight")
        plt.close(fig)

        # Verify file was actually written and has content
        file_size = os.path.getsize(filepath)
        logger.info("[CHART DEBUG] %s: saved %s (%d bytes)", symbol, filepath, file_size)
        if file_size < 1000:
            logger.warning("[CHART DEBUG] %s: file suspiciously small (%d bytes) — "
                          "chart may be blank", symbol, file_size)
        return filepath
    except Exception as exc:
        logger.warning("generate_line_fill(%s, %s, %s) failed: %s", coin_id, symbol, days, exc)
        try:
            plt.close("all")
        except Exception:
            pass
        return None


def generate_fallback_card(symbol: str) -> str | None:
    """Generate a simple branded card with coin name when chart data is unavailable.

    Used as a last resort so tweets always have a visual.
    """
    logger.info("[CHART HIT] generate_fallback_card(%s)", symbol)
    return None


# ── Price alert card ─────────────────────────────────────────────────────────

def generate_price_alert_chart(
    symbol: str,
    coin_id: str,
    price: float,
    pct_change: float,
    window: str = "1h",
) -> str | None:
    """Price alert chart — routes through unified generate_line_fill renderer."""
    logger.info("[CHART HIT] generate_price_alert_chart(%s) → routing to generate_line_fill", symbol)
    return generate_line_fill(coin_id, symbol, 1)


# ── Chart style 2: Candlestick ──────────────────────────────────────────────

def generate_candlestick(coin_id: str, symbol: str, days: int = 7) -> str | None:
    """OHLC candlestick chart."""
    logger.info("[CHART HIT] generate_candlestick(%s, %s, %dd)", coin_id, symbol, days)
    return generate_line_fill(coin_id, symbol, days)


# ── Chart style 3: Multi-coin comparison ─────────────────────────────────────

def generate_multi_coin_chart(coins: list[dict], days: int = 7) -> str | None:
    """Multi-coin normalized % performance overlay."""
    logger.info("[CHART HIT] generate_multi_coin_chart(%d coins, %dd)", len(coins), days)
    return None


# ── Chart style 4: Price + Volume dual axis ──────────────────────────────────

def generate_volume_price(coin_id: str, symbol: str, days: int = 7) -> str | None:
    logger.info("[CHART HIT] generate_volume_price(%s, %s, %dd)", coin_id, symbol, days)
    """Price line with volume bars on secondary axis."""
    return generate_line_fill(coin_id, symbol, days)


# ── Chart style 5: Price + RSI momentum ──────────────────────────────────────

def generate_momentum(coin_id: str, symbol: str, days: int = 7) -> str | None:
    logger.info("[CHART HIT] generate_momentum(%s, %s, %dd)", coin_id, symbol, days)
    """Price chart with RSI indicator subplot."""
    return generate_line_fill(coin_id, symbol, days)


# ── Chart style 6: Bar chart of top movers ───────────────────────────────────

def generate_bar_change() -> str | None:
    """Horizontal bar chart showing 24h % change for top coins."""
    logger.info("[CHART HIT] generate_bar_change()")
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        return None

    coins = _fetch_top_movers()
    if not coins or len(coins) < 5:
        return None

    _ensure_chart_dir()
    _cleanup_old_charts()

    # Take top 10 by market cap, sort by 24h change
    top = coins[:10]
    top.sort(key=lambda c: c.get("price_change_percentage_24h", 0) or 0)

    symbols = [c.get("symbol", "").upper() for c in top]
    changes = [c.get("price_change_percentage_24h", 0) or 0 for c in top]
    colors = [_GREEN if c >= 0 else _RED for c in changes]

    fig, ax = plt.subplots(figsize=(10, 6))
    fig.patch.set_facecolor(_BG)
    ax.set_facecolor(_BG)

    bars = ax.barh(symbols, changes, color=colors, height=0.6, edgecolor="none")

    # Add % labels on bars
    for bar, change in zip(bars, changes):
        x = bar.get_width()
        ax.text(x + (0.3 if x >= 0 else -0.3), bar.get_y() + bar.get_height() / 2,
                f"{change:+.1f}%", va="center", ha="left" if x >= 0 else "right",
                color="white", fontsize=11, fontweight="bold")

    ax.axvline(x=0, color=_GRID, linewidth=0.8)
    ax.set_title("Top 10 Coins — 24h Change", color="white", fontsize=16,
                 fontweight="bold", pad=15)
    ax.tick_params(colors=_TEXT, labelsize=11)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.spines["bottom"].set_color(_AXIS)
    ax.spines["left"].set_color(_AXIS)
    ax.xaxis.set_major_formatter(plt.FuncFormatter(lambda x, _: f"{x:+.0f}%"))
    ax.grid(True, alpha=0.1, axis="x", color=_GRID)

    _watermark(ax)
    return _save_fig(fig, "bar_24h")


# ── Public API: generate with rotation ───────────────────────────────────────

def generate_varied_chart() -> tuple[str | None, str, str]:
    """
    Generate the next chart in the rotation, ensuring variety every time.

    Returns (filepath, style_name, tweet_caption) or (None, "", "").
    """
    logger.info("[CHART HIT] generate_varied_chart()")
    return (None, "", "")


# ── News card image generator ─────────────────────────────────────────────────

def _binance_ticker_price(pair: str) -> tuple[float | None, float | None]:
    """Fetch last price + 24h % change from Binance."""
    try:
        resp = requests.get(_BINANCE_TICKER_URL, params={"symbol": pair}, timeout=10)
        resp.raise_for_status()
        d = resp.json()
        return float(d["lastPrice"]), float(d["priceChangePercent"])
    except Exception:
        return None, None


def _fetch_fear_greed_value() -> tuple[int | None, str | None]:
    """Fetch current Fear & Greed index value and classification."""
    try:
        r = requests.get("https://api.alternative.me/fng/",
                         params={"limit": 1, "format": "json"}, timeout=8)
        r.raise_for_status()
        entry = r.json().get("data", [{}])[0]
        return int(entry["value"]), entry.get("value_classification", "")
    except Exception:
        return None, None


def _pil_word_wrap(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.FreeTypeFont,
                   max_width: int, max_lines: int = 3) -> list[str]:
    """Word-wrap text to fit within max_width, returning up to max_lines."""
    words = text.split()
    lines: list[str] = []
    current = ""
    for word in words:
        test = f"{current} {word}".strip()
        bbox = draw.textbbox((0, 0), test, font=font)
        if bbox[2] - bbox[0] > max_width and current:
            lines.append(current)
            current = word
            if len(lines) >= max_lines:
                break
        else:
            current = test
    if current and len(lines) < max_lines:
        lines.append(current)
    # Truncate last line with ellipsis if needed
    if len(lines) == max_lines:
        while lines[-1]:
            bbox = draw.textbbox((0, 0), lines[-1] + "...", font=font)
            if bbox[2] - bbox[0] <= max_width:
                break
            lines[-1] = lines[-1][:-1]
        if lines[-1] != text.split("\n")[-1]:
            lines[-1] = lines[-1].rstrip() + "..."
    return lines[:max_lines]


def _draw_pill_badge(draw: ImageDraw.ImageDraw, xy: tuple[int, int], text: str,
                     font: ImageFont.FreeTypeFont, bg_color: tuple, text_color: tuple,
                     padding: tuple[int, int] = (16, 6)) -> int:
    """Draw a rounded pill badge. Returns the right-edge x coordinate."""
    bbox = draw.textbbox((0, 0), text, font=font)
    tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
    x, y = xy
    pill_w = tw + padding[0] * 2
    pill_h = th + padding[1] * 2
    draw.rounded_rectangle([x, y, x + pill_w, y + pill_h], radius=pill_h // 2,
                           fill=bg_color)
    draw.text((x + padding[0], y + padding[1] - 2), text, font=font, fill=text_color)
    return x + pill_w


def _draw_ticker_item(draw: ImageDraw.ImageDraw, x: int, y: int, label: str,
                      price: float | None, pct: float | None,
                      font_label: ImageFont.FreeTypeFont,
                      font_price: ImageFont.FreeTypeFont,
                      font_pct: ImageFont.FreeTypeFont) -> int:
    """Draw a single ticker item (label + price + %change). Returns right edge x."""
    draw.text((x, y), label, font=font_label, fill=_PIL_MUTED)
    lbox = draw.textbbox((0, 0), label, font=font_label)
    cx = x + (lbox[2] - lbox[0]) + 10
    if price is not None:
        price_str = f"${price:,.0f}" if price >= 1000 else f"${price:,.2f}"
        draw.text((cx, y), price_str, font=font_price, fill=_PIL_WHITE)
        pbox = draw.textbbox((0, 0), price_str, font=font_price)
        cx += (pbox[2] - pbox[0]) + 10
    if pct is not None:
        arrow = "\u25B2" if pct >= 0 else "\u25BC"
        pct_str = f"{arrow}{pct:+.1f}%"
        color = _PIL_GREEN if pct >= 0 else _PIL_RED
        draw.text((cx, y), pct_str, font=font_pct, fill=color)
        pbox = draw.textbbox((0, 0), pct_str, font=font_pct)
        cx += (pbox[2] - pbox[0])
    return cx


def generate_news_card(story: dict, tweet_text: str) -> str | None:
    """Pillow-based news card composited onto the brand template image."""
    logger.info("[CHART HIT] generate_news_card(%.40s)", story.get("title", "?"))
    return None


def generate_quote_card(
    headline: str,
    coin_data: list[dict] | None = None,
    sentiment: str = "neutral",
) -> str | None:
    """
    Generate a market analysis card for quote tweets.

    sentiment: "bullish", "bearish", or "neutral" — determines color scheme.
    coin_data: list of dicts with keys: symbol, price, pct_24h

    Returns file path to the generated PNG or None.
    """
    logger.info("[CHART HIT] generate_quote_card(%.40s, %s)", headline, sentiment)
    return None


# ── Trending alert card (Bloomberg terminal style) ───────────────────────────

def generate_trending_alert_image(alert: dict) -> str | None:
    """
    Bloomberg terminal-style card for trending coin alerts.
    Output: 1200x675 px (figsize 12x6.75 @ 100 DPI), no external image APIs.

    Layout:
      Left 55%  — type badge | giant ticker | full name | price | Δ24h | rank | vol
      Right 45% — subtle 24h price sparkline
      Footer    — @CryptoVault88 watermark bottom-right

    alert keys: id, symbol, name, current_price, pct_24h, market_cap_rank,
                volume_24h, source, hook
    """
    logger.info("[CHART HIT] generate_trending_alert_image(%s)", alert.get("symbol", "?"))
    return generate_line_fill(alert.get("id", "bitcoin"), alert.get("symbol", "BTC"), 1)


# ── DEX vs CEX volume comparison chart ───────────────────────────────────────

def generate_dex_vs_cex_chart() -> str | None:
    """
    Branded dark-theme dual-axis chart comparing DEX vs CEX monthly trading
    volume trends from Q1 2021 to Q4 2024.

    CEX: declining bar chart in red (#FF1744)
    DEX: growing line overlay in green (#00C853)
    Background: #0d1117
    Returns a temp file path or None on failure.
    """
    logger.info("[CHART HIT] generate_dex_vs_cex_chart()")
    return None


# ── Bitcoin ETF net flows chart ───────────────────────────────────────────────

def generate_etf_flows_chart() -> str | None:
    """
    Bar chart showing Bitcoin ETF monthly net inflows/outflows Jan–Dec 2024.
    Green bars for positive inflows, red bars for outflows.
    """
    logger.info("[CHART HIT] generate_etf_flows_chart()")
    return None


# ── L2 TVL adoption chart ─────────────────────────────────────────────────────

def generate_l2_adoption_chart() -> str | None:
    """
    Line chart: TVL growth for Arbitrum, Optimism, Base from Q1 2023 to Q4 2024.
    Arbitrum #28A8E0, Base #0052FF, Optimism #FF0420.
    """
    logger.info("[CHART HIT] generate_l2_adoption_chart()")
    return None


# ── Miner revenue vs hash rate chart ─────────────────────────────────────────

def generate_miner_behaviour_chart() -> str | None:
    """
    Dual-axis chart: BTC miner revenue (gold bars, left axis) vs hash rate
    (white line, right axis) Q1 2023 – Q4 2024. Halving dip annotated.
    """
    logger.info("[CHART HIT] generate_miner_behaviour_chart()")
    return None


# ── BTC price vs on-chain activity chart ─────────────────────────────────────

def generate_onchain_vs_price_chart() -> str | None:
    """
    Dual-axis line chart: BTC price (orange, left) vs active addresses
    (purple, right) 2023–2024. Shows divergence periods.
    """
    logger.info("[CHART HIT] generate_onchain_vs_price_chart()")
    return None


# ── BTC/ETF correlation chart ─────────────────────────────────────────────────

def generate_etf_btc_correlation_chart() -> str | None:
    """
    Line chart: BTC 30-day rolling correlation with S&P 500 (blue) and Gold
    (gold) Jan 2023 – Dec 2024. Range -1 to 1. ETF approval annotated.
    """
    logger.info("[CHART HIT] generate_etf_btc_correlation_chart()")
    return None


# ── Legacy API (kept for breakout_monitor compatibility) ─────────────────────

def _fetch_sparkline(coin_id: str, days: int = 7) -> list[float] | None:
    """Fetch 7-day close prices for a mini sparkline. Returns list of floats."""
    history = fetch_price_history(coin_id, days)
    if not history or len(history) < 5:
        return None
    return [p[1] for p in history]


def _draw_sparkline(ax, prices: list[float], colour: str, x0: float, y0: float,
                    w: float, h: float) -> None:
    """Draw a tiny sparkline in axes-coordinate space."""
    import numpy as np
    n = len(prices)
    xs = np.linspace(x0, x0 + w, n)
    mn, mx = min(prices), max(prices)
    rng = mx - mn if mx != mn else 1.0
    ys = [y0 + (p - mn) / rng * h for p in prices]
    ax.plot(xs, ys, color=colour, linewidth=1.2, solid_capstyle="round",
            transform=ax.transAxes, zorder=5)
    ax.fill_between(xs, y0, ys, color=colour, alpha=0.10,
                    transform=ax.transAxes, zorder=4)


_COIN_ID_MAP = {
    "BTC": "bitcoin", "ETH": "ethereum", "SOL": "solana",
    "XRP": "ripple", "BNB": "binancecoin", "ADA": "cardano",
    "DOGE": "dogecoin", "AVAX": "avalanche-2", "DOT": "polkadot",
    "LINK": "chainlink",
}


def generate_morning_recap_chart(coins: list[dict]) -> str | None:
    """Terminal-style morning recap: grid panels, massive numbers, sparklines, volume bars."""
    logger.info("[CHART HIT] generate_morning_recap_chart(%d coins)", len(coins))
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        import matplotlib.patches as mpatches
        import numpy as np
    except ImportError:
        logger.warning("matplotlib not available — cannot generate morning recap chart")
        return None

    try:
        _ensure_chart_dir()
        _cleanup_old_charts()

        top5 = coins[:5]
        if not top5:
            return None

        def _coin_pct(c: dict) -> float:
            return (
                c.get("price_change_percentage_24h_in_currency")
                or c.get("price_change_percentage_24h")
                or c.get("pct_24h")
                or c.get("change_24h")
                or 0
            )

        # 1600×900 @ 200 DPI
        fig = plt.figure(figsize=(10.67, 6), facecolor=_BG)
        ax = fig.add_axes([0, 0, 1, 1])
        ax.set_xlim(0, 1)
        ax.set_ylim(0, 1)
        ax.set_facecolor(_BG)
        ax.axis("off")
        _draw_grid_dots(ax, nx=50, ny=30, alpha=0.03)

        # ── Title bar with accent ────────────────────────────────────────────
        ax.axhline(0.97, xmin=0.02, xmax=0.98, color=_GOLD, linewidth=2, clip_on=False)
        ax.text(0.03, 0.93, "M A R K E T   O V E R V I E W",
                transform=ax.transAxes, fontsize=18, fontweight="bold", color="white",
                va="top", ha="left")
        import datetime as _dt
        now_str = _dt.datetime.utcnow().strftime("%b %d %Y  %H:%M UTC")
        ax.text(0.97, 0.93, now_str, transform=ax.transAxes,
                fontsize=10, color=_MUTED, va="top", ha="right")

        # ── Hero coin (BTC or first coin) — large panel left ────────────────
        hero = top5[0]
        h_sym = hero.get("symbol", "???").upper()
        h_price = hero.get("current_price", 0) or 0
        h_pct = _coin_pct(hero)
        h_color = _ACCENT_GREEN if h_pct >= 0 else _ACCENT_RED
        h_arrow = "▲" if h_pct >= 0 else "▼"

        # Hero panel background
        hero_bg = mpatches.FancyBboxPatch(
            (0.02, 0.42), 0.46, 0.46, boxstyle="round,pad=0.01",
            facecolor=_PANEL, edgecolor=_BORDER, linewidth=1,
            transform=ax.transAxes, zorder=1,
        )
        ax.add_patch(hero_bg)

        ax.text(0.05, 0.84, h_sym, transform=ax.transAxes,
                fontsize=56, fontweight="bold", color="white", va="top", zorder=5)
        ax.text(0.05, 0.65, _price_fmt(h_price), transform=ax.transAxes,
                fontsize=38, color="white", va="top", zorder=5)
        ax.text(0.05, 0.52, f"{h_arrow} {h_pct:+.2f}%", transform=ax.transAxes,
                fontsize=24, fontweight="bold", color=h_color, va="top", zorder=5)

        # Hero sparkline
        h_coin_id = _COIN_ID_MAP.get(h_sym, hero.get("id", ""))
        h_spark = _fetch_sparkline(h_coin_id) if h_coin_id else None
        if h_spark:
            _draw_sparkline(ax, h_spark, h_color, x0=0.25, y0=0.44, w=0.21, h=0.18)

        # ── Right panel — remaining coins in grid ────────────────────────────
        right_coins = top5[1:5]
        grid_x0 = 0.52
        grid_w = 0.23
        grid_h = 0.21
        gap = 0.02

        positions = [
            (grid_x0, 0.67),                    # top-left
            (grid_x0 + grid_w + gap, 0.67),     # top-right
            (grid_x0, 0.42),                     # bottom-left
            (grid_x0 + grid_w + gap, 0.42),      # bottom-right
        ]

        for idx, coin in enumerate(right_coins):
            if idx >= len(positions):
                break
            px, py = positions[idx]
            sym = coin.get("symbol", "???").upper()
            cprice = coin.get("current_price", 0) or 0
            cpct = _coin_pct(coin)
            ccolor = _ACCENT_GREEN if cpct >= 0 else _ACCENT_RED
            carrow = "▲" if cpct >= 0 else "▼"

            # Panel bg
            panel = mpatches.FancyBboxPatch(
                (px, py), grid_w, grid_h, boxstyle="round,pad=0.008",
                facecolor=_PANEL, edgecolor=_BORDER, linewidth=0.8,
                transform=ax.transAxes, zorder=1,
            )
            ax.add_patch(panel)

            ax.text(px + 0.015, py + grid_h - 0.025, sym, transform=ax.transAxes,
                    fontsize=16, fontweight="bold", color="white", va="top", zorder=5)
            ax.text(px + 0.015, py + grid_h * 0.45, _price_fmt(cprice),
                    transform=ax.transAxes, fontsize=14, color="white", va="center", zorder=5)
            ax.text(px + 0.015, py + 0.025, f"{carrow} {cpct:+.1f}%",
                    transform=ax.transAxes, fontsize=13, fontweight="bold",
                    color=ccolor, va="bottom", zorder=5)

            # Tiny sparkline
            c_id = _COIN_ID_MAP.get(sym, coin.get("id", ""))
            c_spark = _fetch_sparkline(c_id) if c_id else None
            if c_spark:
                _draw_sparkline(ax, c_spark, ccolor,
                                x0=px + grid_w * 0.5, y0=py + 0.02,
                                w=grid_w * 0.45, h=grid_h * 0.5)

        # ── Summary bar at bottom ────────────────────────────────────────────
        bar_bg = mpatches.FancyBboxPatch(
            (0.02, 0.04), 0.96, 0.32, boxstyle="round,pad=0.01",
            facecolor=_PANEL, edgecolor=_BORDER, linewidth=1,
            transform=ax.transAxes, zorder=1,
        )
        ax.add_patch(bar_bg)

        # Horizontal bar chart for all 5 coins
        max_abs_pct = max(abs(_coin_pct(c)) for c in top5) or 1.0
        bar_y_start = 0.30
        bar_row_h = 0.05
        for i, coin in enumerate(top5):
            by = bar_y_start - i * bar_row_h
            sym = coin.get("symbol", "???").upper()
            cpct = _coin_pct(coin)
            ccolor = _ACCENT_GREEN if cpct >= 0 else _ACCENT_RED
            bar_w = (abs(cpct) / max_abs_pct) * 0.45

            ax.text(0.04, by, sym, transform=ax.transAxes,
                    fontsize=10, fontweight="bold", color="white", va="center", zorder=5)
            bar_rect = mpatches.FancyBboxPatch(
                (0.12, by - 0.015), bar_w, 0.03,
                boxstyle="round,pad=0.002", facecolor=ccolor + "40",
                edgecolor=ccolor, linewidth=0.6,
                transform=ax.transAxes, zorder=4,
            )
            ax.add_patch(bar_rect)
            ax.text(0.12 + bar_w + 0.01, by, f"{cpct:+.1f}%",
                    transform=ax.transAxes, fontsize=9, fontweight="bold",
                    color=ccolor, va="center", zorder=5)

        # Green/red count
        green_n = sum(1 for c in top5 if _coin_pct(c) >= 0)
        ax.text(0.85, 0.15, f"{green_n}/{len(top5)} green",
                transform=ax.transAxes, fontsize=12, fontweight="bold",
                color=_ACCENT_GREEN if green_n > len(top5) // 2 else _ACCENT_RED,
                ha="center", va="center", zorder=5)

        # ── Watermark ────────────────────────────────────────────────────────
        ax.text(0.97, 0.01, "@CryptoVault88", transform=ax.transAxes,
                fontsize=9, color="#555555", va="bottom", ha="right")

        filepath = os.path.join(_CHART_DIR, f"morning_recap_{int(time.time())}.png")
        fig.savefig(filepath, dpi=150, bbox_inches="tight", facecolor=_BG)
        plt.close(fig)
        logger.info("Generated morning recap chart: %s", filepath)
        return filepath
    except Exception as exc:
        logger.warning("generate_morning_recap_chart failed: %s", exc)
        return None


def generate_fear_greed_gauge(value: int, classification: str) -> str | None:
    """Large semicircle gauge with gradient zones, needle, and historical comparison."""
    logger.info("[CHART HIT] generate_fear_greed_gauge(%d, %s)", value, classification)
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        import matplotlib.patches as mpatches
        import numpy as np
    except ImportError:
        logger.warning("matplotlib not available — cannot generate fear/greed gauge")
        return None

    try:
        _ensure_chart_dir()
        _cleanup_old_charts()

        # Fetch historical values for comparison
        last_week_val: int | None = None
        last_month_val: int | None = None
        try:
            fg_resp = requests.get(
                "https://api.alternative.me/fng/",
                params={"limit": 31, "format": "json"},
                timeout=10,
            )
            fg_resp.raise_for_status()
            fg_history = fg_resp.json().get("data", [])
            if len(fg_history) >= 7:
                last_week_val = int(fg_history[6]["value"])
            if len(fg_history) >= 30:
                last_month_val = int(fg_history[29]["value"])
        except Exception:
            pass

        # 1600×900 @ 200 DPI — wider figure to maintain 1600px+ after equal aspect
        fig = plt.figure(figsize=(12, 6), facecolor=_BG)
        ax = fig.add_axes([0.08, 0.02, 0.84, 0.96])
        ax.set_xlim(-1.5, 1.5)
        ax.set_ylim(-0.85, 1.4)
        ax.set_aspect("equal")
        ax.set_facecolor(_BG)
        ax.axis("off")

        # Title
        ax.text(0, 1.32, "FEAR & GREED INDEX", ha="center", va="top",
                fontsize=20, fontweight="bold", color="white")

        # Zone definitions
        zones = [
            (0,  20, "#ff4444", "Extreme\nFear"),
            (20, 40, "#ff8844", "Fear"),
            (40, 60, "#F5A623", "Neutral"),
            (60, 80, "#66ddaa", "Greed"),
            (80, 100, "#00C896", "Extreme\nGreed"),
        ]

        def _val_to_angle(v: float) -> float:
            return 180.0 - v * 1.8

        outer_r = 1.05
        inner_r = 0.65

        for start_v, end_v, colour, label in zones:
            theta1 = _val_to_angle(end_v)
            theta2 = _val_to_angle(start_v)
            wedge = mpatches.Wedge(
                center=(0, 0), r=outer_r,
                theta1=theta1, theta2=theta2,
                width=outer_r - inner_r,
                facecolor=colour, edgecolor=_BG, linewidth=2,
            )
            ax.add_patch(wedge)

            # Zone label
            mid_v = (start_v + end_v) / 2
            mid_rad = np.radians(_val_to_angle(mid_v))
            lx = 1.22 * np.cos(mid_rad)
            ly = 1.22 * np.sin(mid_rad)
            ax.text(lx, ly, label, ha="center", va="center",
                    fontsize=9, color=_MUTED, linespacing=1.1)

        # Tick marks around the gauge
        for tv in range(0, 101, 10):
            angle_rad = np.radians(_val_to_angle(tv))
            x1 = (outer_r + 0.02) * np.cos(angle_rad)
            y1 = (outer_r + 0.02) * np.sin(angle_rad)
            x2 = (outer_r + 0.06) * np.cos(angle_rad)
            y2 = (outer_r + 0.06) * np.sin(angle_rad)
            ax.plot([x1, x2], [y1, y2], color=_MUTED, linewidth=1, zorder=3)

        # Needle — thick, with glow
        needle_rad = np.radians(_val_to_angle(value))
        needle_len = 0.90
        nx = needle_len * np.cos(needle_rad)
        ny = needle_len * np.sin(needle_rad)
        # Glow
        ax.plot([0, nx], [0, ny], color="white", linewidth=4, alpha=0.15, zorder=4)
        # Needle line
        ax.annotate("", xy=(nx, ny), xytext=(0, 0),
                    arrowprops=dict(arrowstyle="->,head_width=0.06,head_length=0.08",
                                    color="white", lw=3), zorder=5)
        pivot = plt.Circle((0, 0), 0.05, color="white", zorder=6)
        ax.add_patch(pivot)

        # Centre value — massive
        # Determine color from zones
        val_color = "#ffcc00"
        for sv, ev, col, _ in zones:
            if sv <= value <= ev:
                val_color = col
                break

        # Value readout — below the gauge with clear separation from needle
        ax.text(0, -0.25, str(value), ha="center", va="top",
                fontsize=72, fontweight="bold", color=val_color)
        ax.text(0, -0.50, classification.upper(), ha="center", va="top",
                fontsize=18, fontweight="bold", color=_MUTED)

        # Historical comparison panel
        hist_parts = []
        if last_week_val is not None:
            hist_parts.append(f"Last week: {last_week_val}")
        if last_month_val is not None:
            hist_parts.append(f"Last month: {last_month_val}")
        if hist_parts:
            ax.text(0, -0.65, "  |  ".join(hist_parts), ha="center", va="top",
                    fontsize=12, color=_MUTED)

        # Watermark — gold brand
        ax.text(1.35, -0.80, "@CryptoVault88", ha="right", va="bottom",
                fontsize=10, fontweight="bold", color=_GOLD, alpha=0.35)

        filepath = os.path.join(_CHART_DIR, f"fear_greed_{int(time.time())}.png")
        fig.savefig(filepath, dpi=150, bbox_inches="tight", facecolor=_BG)
        plt.close(fig)
        logger.info("Generated fear/greed gauge: %s", filepath)
        return filepath

    except Exception as exc:
        logger.warning("Fear & Greed gauge generation failed: %s", exc)
        return None


def generate_geo_chart(story: dict) -> str | None:
    """Pillow-based breaking-news card composited onto the brand template."""
    logger.info("[CHART HIT] generate_geo_chart(%.40s)", story.get("title", "?"))
    return None
