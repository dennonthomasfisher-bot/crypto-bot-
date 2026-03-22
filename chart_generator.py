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
import logging
import os
import random
import time

import requests

logger = logging.getLogger(__name__)

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

# Bloomberg terminal dark theme
_BG = "#0d1117"
_GRID = "#21262d"
_TEXT = "#8b949e"
_AXIS = "#30363d"
_GREEN = "#00ff88"
_RED = "#ff4444"
_GREEN_FILL = "#00ff8825"
_RED_FILL = "#ff444425"
_BLUE = "#58a6ff"
_ORANGE = "#d29922"
_PURPLE = "#bc8cff"
_CYAN = "#39d2c0"
_ACCENT_GREEN = "#00ff88"
_ACCENT_RED = "#ff4444"
_MUTED = "#8b949e"
_PANEL = "#161b22"
_BORDER = "#30363d"

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
    "sui": "SUIUSDT",
    "aptos": "APTUSDT",
    "near": "NEARUSDT",
    "render-token": "RENDERUSDT",
    "the-open-network": "TONUSDT",
    "shiba-inu": "SHIBUSDT",
    "pepe": "PEPEUSDT",
    "uniswap": "UNIUSDT",
    "aave": "AAVEUSDT",
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
        return {
            "prices": [[float(k[0]), float(k[4])] for k in klines],
            "volumes": [[float(k[0]), float(k[5])] for k in klines],
        }
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
    ax.text(0.99, 0.02, "@CoinWatchAlert", transform=ax.transAxes,
            fontsize=9, color="#555555", ha="right", va="bottom", alpha=0.7)


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
    fig.savefig(filepath, dpi=200, bbox_inches="tight", facecolor=_BG)
    import matplotlib.pyplot as plt
    plt.close(fig)
    logger.info("Generated chart: %s", filepath)
    return filepath


def _price_fmt(x, _=None):
    return f"${x:,.0f}" if x >= 1000 else f"${x:,.2f}"


# ── Chart style 1: Line with fill (original) ────────────────────────────────

def generate_line_fill(coin_id: str, symbol: str, days: int = 7) -> str | None:
    """Professional price chart: area fill, high/low markers, volume mini-panel."""
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
        data = _fetch_market_chart_full(coin_id, days)
        if not data or not data["prices"] or len(data["prices"]) < 10:
            return None

        _ensure_chart_dir()
        _cleanup_old_charts()

        times = [datetime.fromtimestamp(p[0] / 1000, tz=timezone.utc) for p in data["prices"]]
        values = [p[1] for p in data["prices"]]
        volumes = [v[1] for v in data["volumes"]] if data.get("volumes") else None

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

        # Layout: header panel + chart + volume
        fig = plt.figure(figsize=(10.67, 6), facecolor=_BG)  # 1600x900 @150dpi
        if volumes:
            gs = gridspec.GridSpec(3, 1, height_ratios=[1.2, 5, 1.5], hspace=0.08,
                                  figure=fig, left=0.08, right=0.95, top=0.95, bottom=0.06)
        else:
            gs = gridspec.GridSpec(2, 1, height_ratios=[1.2, 5], hspace=0.08,
                                  figure=fig, left=0.08, right=0.95, top=0.95, bottom=0.06)

        # ── Header panel ─────────────────────────────────────────────────────
        ax_hdr = fig.add_subplot(gs[0])
        ax_hdr.set_facecolor(_BG)
        ax_hdr.axis("off")
        ax_hdr.text(0.0, 0.5, symbol, transform=ax_hdr.transAxes,
                    fontsize=48, fontweight="bold", color="white", va="center")
        ax_hdr.text(0.22, 0.55, price_str, transform=ax_hdr.transAxes,
                    fontsize=32, color="white", va="center")
        ax_hdr.text(0.22, 0.15, f"{arrow} {pct:+.2f}%  {period}",
                    transform=ax_hdr.transAxes,
                    fontsize=18, fontweight="bold", color=accent, va="center")
        ax_hdr.text(1.0, 0.5, "@CoinWatchAlert", transform=ax_hdr.transAxes,
                    fontsize=9, color="#555555", ha="right", va="center")

        # ── Main chart ───────────────────────────────────────────────────────
        ax = fig.add_subplot(gs[1])
        ax.set_facecolor(_BG)
        _draw_grid_dots(ax, nx=40, ny=20, alpha=0.04)

        # Area fill with gradient effect (layered fills)
        ax.plot(times, values, color=accent, linewidth=2.0, zorder=5)
        base = min(values)
        ax.fill_between(times, values, base, color=accent, alpha=0.12, zorder=2)
        ax.fill_between(times, values, base, color=accent, alpha=0.06, zorder=1)

        # Open price horizontal line
        ax.axhline(open_price, color=_MUTED, linewidth=0.8, linestyle="--", alpha=0.5, zorder=3)
        ax.text(times[-1], open_price, f" OPEN {_price_fmt(open_price)}",
                fontsize=8, color=_MUTED, va="bottom", zorder=6)

        # High/low markers
        ax.plot(times[hi_idx], values[hi_idx], 'o', color=_ACCENT_GREEN,
                markersize=7, zorder=7)
        ax.annotate(f"H {_price_fmt(values[hi_idx])}", (times[hi_idx], values[hi_idx]),
                    textcoords="offset points", xytext=(8, 8),
                    fontsize=9, fontweight="bold", color=_ACCENT_GREEN, zorder=7)
        ax.plot(times[lo_idx], values[lo_idx], 'o', color=_ACCENT_RED,
                markersize=7, zorder=7)
        ax.annotate(f"L {_price_fmt(values[lo_idx])}", (times[lo_idx], values[lo_idx]),
                    textcoords="offset points", xytext=(8, -12),
                    fontsize=9, fontweight="bold", color=_ACCENT_RED, zorder=7)

        # Thin accent border
        for spine in ["left", "bottom"]:
            ax.spines[spine].set_color(_BORDER)
            ax.spines[spine].set_linewidth(0.8)
        ax.spines["top"].set_visible(False)
        ax.spines["right"].set_visible(False)
        ax.tick_params(colors=_MUTED, labelsize=9)
        ax.yaxis.set_major_formatter(plt.FuncFormatter(_price_fmt))
        import matplotlib.dates as mdates
        ax.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M" if days <= 1 else "%b %d"))
        ax.grid(True, alpha=0.08, color=_GRID)

        # ── Volume panel ─────────────────────────────────────────────────────
        if volumes and len(gs) > 2:
            ax_vol = fig.add_subplot(gs[2], sharex=ax)
            ax_vol.set_facecolor(_BG)
            vol_colors = [_ACCENT_GREEN + "60" if i == 0 or values[i] >= values[i-1]
                         else _ACCENT_RED + "60"
                         for i in range(len(values))]
            ax_vol.bar(times, volumes[:len(times)], width=(times[-1] - times[0]).total_seconds() / len(times) / 86400 * 0.8,
                      color=vol_colors[:len(times)], zorder=2)
            ax_vol.set_ylabel("Vol", fontsize=8, color=_MUTED)
            ax_vol.tick_params(colors=_MUTED, labelsize=7)
            ax_vol.spines["top"].set_visible(False)
            ax_vol.spines["right"].set_visible(False)
            ax_vol.spines["left"].set_color(_BORDER)
            ax_vol.spines["bottom"].set_color(_BORDER)
            ax_vol.grid(True, alpha=0.06, color=_GRID)

        filepath = os.path.join(_CHART_DIR, f"line_{symbol}_{days}d_{int(time.time())}.png")
        fig.savefig(filepath, dpi=200, bbox_inches="tight", facecolor=_BG)
        plt.close(fig)
        logger.info("Generated chart: %s", filepath)
        return filepath
    except Exception as exc:
        logger.warning("generate_line_fill(%s, %s, %s) failed: %s", coin_id, symbol, days, exc)
        return None


# ── Price alert card ─────────────────────────────────────────────────────────

def generate_price_alert_chart(
    symbol: str,
    coin_id: str,
    price: float,
    pct_change: float,
    window: str = "1h",
) -> str | None:
    """Terminal-style price alert: massive symbol, area chart, high/low, volume."""
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        import matplotlib.gridspec as gridspec
        import numpy as np
        from datetime import datetime, timezone
    except ImportError:
        return None

    plt.close("all")

    data = _fetch_market_chart_full(coin_id, 1)
    if not data or not data["prices"] or len(data["prices"]) < 2:
        return generate_line_fill(coin_id, symbol, 1)

    _ensure_chart_dir()
    _cleanup_old_charts()

    times  = [datetime.fromtimestamp(p[0] / 1000, tz=timezone.utc) for p in data["prices"]]
    values = [p[1] for p in data["prices"]]
    volumes = [v[1] for v in data["volumes"]] if data.get("volumes") else None

    is_up  = pct_change >= 0
    accent = _ACCENT_GREEN if is_up else _ACCENT_RED
    arrow  = "▲" if is_up else "▼"
    pct_sign = "+" if is_up else ""
    price_str = _price_fmt(price)

    hi_idx = np.argmax(values)
    lo_idx = np.argmin(values)

    fig = plt.figure(figsize=(10.67, 6), facecolor=_BG)
    gs = gridspec.GridSpec(3, 1, height_ratios=[1.5, 5, 1.2], hspace=0.08,
                          figure=fig, left=0.08, right=0.95, top=0.95, bottom=0.06)

    # ── Header ───────────────────────────────────────────────────────────────
    ax_hdr = fig.add_subplot(gs[0])
    ax_hdr.set_facecolor(_BG)
    ax_hdr.axis("off")
    # Thin accent line at top
    ax_hdr.axhline(1.0, color=accent, linewidth=3, transform=ax_hdr.transAxes, clip_on=False)
    ax_hdr.text(0.0, 0.6, symbol, transform=ax_hdr.transAxes,
                fontsize=56, fontweight="bold", color="white", va="center")
    ax_hdr.text(0.30, 0.65, price_str, transform=ax_hdr.transAxes,
                fontsize=36, color="white", va="center")
    ax_hdr.text(0.30, 0.2, f"{arrow} {pct_sign}{pct_change:.2f}%  ({window})",
                transform=ax_hdr.transAxes,
                fontsize=20, fontweight="bold", color=accent, va="center")
    ax_hdr.text(1.0, 0.5, "PRICE ALERT", transform=ax_hdr.transAxes,
                fontsize=14, fontweight="bold", color=accent, ha="right", va="center", alpha=0.6)

    # ── Chart ────────────────────────────────────────────────────────────────
    ax = fig.add_subplot(gs[1])
    ax.set_facecolor(_BG)
    _draw_grid_dots(ax, alpha=0.04)

    ax.plot(times, values, color=accent, linewidth=2.0, zorder=5)
    base = min(values)
    ax.fill_between(times, values, base, color=accent, alpha=0.12, zorder=2)

    # Open price line
    ax.axhline(values[0], color=_MUTED, linewidth=0.8, linestyle="--", alpha=0.5, zorder=3)

    # High/low markers
    ax.plot(times[hi_idx], values[hi_idx], 'o', color=_ACCENT_GREEN, markersize=7, zorder=7)
    ax.annotate(f"H {_price_fmt(values[hi_idx])}", (times[hi_idx], values[hi_idx]),
                textcoords="offset points", xytext=(8, 8),
                fontsize=9, fontweight="bold", color=_ACCENT_GREEN, zorder=7)
    ax.plot(times[lo_idx], values[lo_idx], 'o', color=_ACCENT_RED, markersize=7, zorder=7)
    ax.annotate(f"L {_price_fmt(values[lo_idx])}", (times[lo_idx], values[lo_idx]),
                textcoords="offset points", xytext=(8, -12),
                fontsize=9, fontweight="bold", color=_ACCENT_RED, zorder=7)

    for spine in ["left", "bottom"]:
        ax.spines[spine].set_color(_BORDER)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.tick_params(colors=_MUTED, labelsize=9)
    ax.yaxis.set_major_formatter(plt.FuncFormatter(_price_fmt))
    import matplotlib.dates as mdates
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M"))
    ax.grid(True, alpha=0.08, color=_GRID)

    # ── Volume panel ─────────────────────────────────────────────────────────
    ax_vol = fig.add_subplot(gs[2], sharex=ax)
    ax_vol.set_facecolor(_BG)
    if volumes:
        vol_colors = [accent + "60" if i == 0 or values[i] >= values[i-1]
                     else _ACCENT_RED + "60" for i in range(len(values))]
        ax_vol.bar(times, volumes[:len(times)],
                  width=(times[-1] - times[0]).total_seconds() / len(times) / 86400 * 0.8,
                  color=vol_colors[:len(times)], zorder=2)
    ax_vol.tick_params(colors=_MUTED, labelsize=7)
    ax_vol.spines["top"].set_visible(False)
    ax_vol.spines["right"].set_visible(False)
    ax_vol.spines["left"].set_color(_BORDER)
    ax_vol.spines["bottom"].set_color(_BORDER)
    ax_vol.text(0.99, 0.05, "@CoinWatchAlert", transform=ax_vol.transAxes,
                fontsize=9, color="#555555", ha="right", va="bottom")

    filepath = os.path.join(_CHART_DIR, f"alert_{symbol}_{int(time.time())}.png")
    fig.savefig(filepath, dpi=200, bbox_inches="tight", facecolor=_BG)
    plt.close(fig)
    logger.info("Generated price alert chart: %s", filepath)
    return filepath


# ── Chart style 2: Candlestick ──────────────────────────────────────────────

def generate_candlestick(coin_id: str, symbol: str, days: int = 7) -> str | None:
    """OHLC candlestick chart."""
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        from datetime import datetime, timezone
    except ImportError:
        return None

    ohlc = _fetch_ohlc(coin_id, days)
    if not ohlc or len(ohlc) < 5:
        return None

    _ensure_chart_dir()
    _cleanup_old_charts()

    fig, ax = plt.subplots(figsize=(10, 5))
    fig.patch.set_facecolor(_BG)
    ax.set_facecolor(_BG)

    times = [datetime.fromtimestamp(c[0] / 1000, tz=timezone.utc) for c in ohlc]
    opens = [c[1] for c in ohlc]
    highs = [c[2] for c in ohlc]
    lows = [c[3] for c in ohlc]
    closes = [c[4] for c in ohlc]

    # Calculate bar width based on time gaps
    if len(times) >= 2:
        import matplotlib.dates as mdates
        avg_gap = (mdates.date2num(times[-1]) - mdates.date2num(times[0])) / len(times)
        width = avg_gap * 0.7
    else:
        width = 0.02

    for i in range(len(times)):
        color = _GREEN if closes[i] >= opens[i] else _RED
        # Wick
        ax.plot([times[i], times[i]], [lows[i], highs[i]], color=color, linewidth=1)
        # Body
        body_low = min(opens[i], closes[i])
        body_high = max(opens[i], closes[i])
        body_height = body_high - body_low
        if body_height < 0.001 * body_low:
            body_height = 0.001 * body_low  # minimum visible body
        rect = plt.Rectangle(
            (mdates.date2num(times[i]) - width / 2, body_low),
            width, body_height,
            facecolor=color if closes[i] >= opens[i] else _RED,
            edgecolor=color, linewidth=0.8,
            alpha=0.9,
        )
        ax.add_patch(rect)

    pct = ((closes[-1] - opens[0]) / opens[0]) * 100
    price_str = _price_fmt(closes[-1])
    ax.set_title(f"{symbol}  {price_str}  ({pct:+.1f}%)",
                 color="white", fontsize=18, fontweight="bold", pad=15)

    ax.yaxis.set_major_formatter(plt.FuncFormatter(_price_fmt))
    _style_ax(ax, "%H:%M" if days <= 1 else "%b %d")
    ax.autoscale_view()

    period = {1: "24H", 7: "7D", 14: "14D", 30: "30D"}.get(days, f"{days}D")
    ax.text(0.01, 0.97, f"OHLC {period}", transform=ax.transAxes, fontsize=11,
            color=_TEXT, ha="left", va="top", fontweight="bold")
    _watermark(ax)

    return _save_fig(fig, f"candle_{symbol}_{days}d")


# ── Chart style 3: Multi-coin comparison ─────────────────────────────────────

def generate_multi_coin_chart(coins: list[dict], days: int = 7) -> str | None:
    """Multi-coin normalized % performance overlay."""
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        from datetime import datetime, timezone
    except ImportError:
        return None

    if not coins or len(coins) < 2:
        return None

    _ensure_chart_dir()
    _cleanup_old_charts()

    colors = [_GREEN, _BLUE, _ORANGE, _PURPLE, _RED, _CYAN]
    fig, ax = plt.subplots(figsize=(10, 5))
    fig.patch.set_facecolor(_BG)
    ax.set_facecolor(_BG)

    plotted = 0
    for i, coin in enumerate(coins[:6]):
        cid = coin.get("id", coin[0]) if isinstance(coin, (list, tuple)) else coin["id"]
        sym = coin.get("symbol", coin[1]) if isinstance(coin, (list, tuple)) else coin["symbol"]
        prices = fetch_price_history(cid, days)
        if not prices or len(prices) < 10:
            continue
        times = [datetime.fromtimestamp(p[0] / 1000, tz=timezone.utc) for p in prices]
        start = prices[0][1]
        normalized = [((p[1] - start) / start) * 100 for p in prices]
        color = colors[i % len(colors)]
        pct = normalized[-1]
        ax.plot(times, normalized, color=color, linewidth=2.5,
                label=f"{sym} ({pct:+.1f}%)")
        plotted += 1

    if plotted < 2:
        import matplotlib.pyplot as plt
        plt.close(fig)
        return None

    ax.set_title(f"{days}-Day Performance Comparison",
                 color="white", fontsize=16, fontweight="bold", pad=15)
    ax.axhline(y=0, color=_GRID, linewidth=0.8, linestyle="--")
    ax.yaxis.set_major_formatter(plt.FuncFormatter(lambda x, _: f"{x:+.0f}%"))

    _style_ax(ax, "%b %d")
    ax.legend(loc="upper left", fontsize=10, facecolor=_BG,
              edgecolor=_AXIS, labelcolor="white")
    _watermark(ax)

    return _save_fig(fig, f"multi_{days}d")


# ── Chart style 4: Price + Volume dual axis ──────────────────────────────────

def generate_volume_price(coin_id: str, symbol: str, days: int = 7) -> str | None:
    """Price line with volume bars on secondary axis."""
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        from datetime import datetime, timezone
    except ImportError:
        return None

    data = _fetch_market_chart_full(coin_id, days)
    if not data or len(data["prices"]) < 10:
        return None

    _ensure_chart_dir()
    _cleanup_old_charts()

    prices_raw = data["prices"]
    volumes_raw = data["volumes"]

    times = [datetime.fromtimestamp(p[0] / 1000, tz=timezone.utc) for p in prices_raw]
    prices = [p[1] for p in prices_raw]
    volumes = [v[1] for v in volumes_raw[:len(times)]]

    is_up = prices[-1] >= prices[0]
    line_color = _GREEN if is_up else _RED

    fig, ax1 = plt.subplots(figsize=(10, 5))
    fig.patch.set_facecolor(_BG)
    ax1.set_facecolor(_BG)

    # Volume bars on background axis
    ax2 = ax1.twinx()
    # Downsample volumes into ~30 bars for cleaner look
    n = len(volumes)
    bar_count = min(30, n)
    chunk = max(1, n // bar_count)
    bar_times = []
    bar_vols = []
    bar_colors = []
    for i in range(0, n - chunk + 1, chunk):
        bar_times.append(times[i + chunk // 2])
        bar_vols.append(sum(volumes[i:i + chunk]) / chunk)
        # Color based on price direction in that chunk
        if prices[min(i + chunk, n - 1)] >= prices[i]:
            bar_colors.append(_GREEN + "60")
        else:
            bar_colors.append(_RED + "60")

    if bar_times:
        import matplotlib.dates as mdates
        if len(bar_times) >= 2:
            bw = (mdates.date2num(bar_times[-1]) - mdates.date2num(bar_times[0])) / len(bar_times) * 0.8
        else:
            bw = 0.5
        ax2.bar(bar_times, bar_vols, width=bw, color=bar_colors, alpha=0.4)
        ax2.set_ylim(0, max(bar_vols) * 3)  # keep volume bars in bottom third
        ax2.tick_params(labelright=False, right=False)
        ax2.set_yticks([])

    # Price line on top
    ax1.plot(times, prices, color=line_color, linewidth=2.5, zorder=5)

    pct = ((prices[-1] - prices[0]) / prices[0]) * 100
    price_str = _price_fmt(prices[-1])
    ax1.set_title(f"{symbol}  {price_str}  ({pct:+.1f}%)  |  Volume",
                  color="white", fontsize=16, fontweight="bold", pad=15)

    ax1.yaxis.set_major_formatter(plt.FuncFormatter(_price_fmt))
    _style_ax(ax1, "%H:%M" if days <= 1 else "%b %d")
    _watermark(ax1)

    period = {1: "24H", 7: "7D", 14: "14D", 30: "30D"}.get(days, f"{days}D")
    ax1.text(0.01, 0.97, f"Price + Volume {period}", transform=ax1.transAxes,
             fontsize=11, color=_TEXT, ha="left", va="top", fontweight="bold")

    return _save_fig(fig, f"vol_{symbol}_{days}d")


# ── Chart style 5: Price + RSI momentum ──────────────────────────────────────

def generate_momentum(coin_id: str, symbol: str, days: int = 7) -> str | None:
    """Price chart with RSI indicator subplot."""
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        import numpy as np
        from datetime import datetime, timezone
    except ImportError:
        return None

    prices_data = fetch_price_history(coin_id, days)
    if not prices_data or len(prices_data) < 20:
        return None

    _ensure_chart_dir()
    _cleanup_old_charts()

    times = [datetime.fromtimestamp(p[0] / 1000, tz=timezone.utc) for p in prices_data]
    prices = np.array([p[1] for p in prices_data])

    # Calculate RSI (14-period)
    deltas = np.diff(prices)
    period = 14
    gains = np.where(deltas > 0, deltas, 0)
    losses = np.where(deltas < 0, -deltas, 0)

    # Use exponential moving average for RSI
    avg_gain = np.zeros_like(gains, dtype=float)
    avg_loss = np.zeros_like(losses, dtype=float)
    avg_gain[:period] = np.mean(gains[:period])
    avg_loss[:period] = np.mean(losses[:period])
    for i in range(period, len(gains)):
        avg_gain[i] = (avg_gain[i - 1] * (period - 1) + gains[i]) / period
        avg_loss[i] = (avg_loss[i - 1] * (period - 1) + losses[i]) / period

    with np.errstate(divide='ignore', invalid='ignore'):
        rs = avg_gain / np.where(avg_loss == 0, 1e-10, avg_loss)
        rsi = 100 - (100 / (1 + rs))

    rsi_times = times[1:]  # RSI is one shorter than prices

    is_up = prices[-1] >= prices[0]
    color = _GREEN if is_up else _RED

    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(10, 6), height_ratios=[3, 1],
                                    sharex=True)
    fig.patch.set_facecolor(_BG)
    ax1.set_facecolor(_BG)
    ax2.set_facecolor(_BG)

    # Price
    ax1.plot(times, prices, color=color, linewidth=2.5)
    pct = ((prices[-1] - prices[0]) / prices[0]) * 100
    price_str = _price_fmt(float(prices[-1]))
    ax1.set_title(f"{symbol}  {price_str}  ({pct:+.1f}%)  |  RSI",
                  color="white", fontsize=16, fontweight="bold", pad=15)
    ax1.yaxis.set_major_formatter(plt.FuncFormatter(_price_fmt))
    _style_ax(ax1, date_fmt=None)
    _watermark(ax1)

    # RSI
    ax2.plot(rsi_times, rsi, color=_BLUE, linewidth=1.5)
    ax2.axhline(y=70, color=_RED, linewidth=0.8, linestyle="--", alpha=0.6)
    ax2.axhline(y=30, color=_GREEN, linewidth=0.8, linestyle="--", alpha=0.6)
    ax2.fill_between(rsi_times, rsi, 70, where=(rsi >= 70), color=_RED_FILL, alpha=0.5)
    ax2.fill_between(rsi_times, rsi, 30, where=(rsi <= 30), color=_GREEN_FILL, alpha=0.5)
    ax2.set_ylim(0, 100)
    ax2.set_ylabel("RSI", color=_TEXT, fontsize=10)
    _style_ax(ax2, "%H:%M" if days <= 1 else "%b %d")

    current_rsi = float(rsi[-1]) if len(rsi) > 0 else 50
    rsi_label = "Overbought" if current_rsi >= 70 else "Oversold" if current_rsi <= 30 else "Neutral"
    ax2.text(0.01, 0.85, f"RSI: {current_rsi:.0f} ({rsi_label})",
             transform=ax2.transAxes, fontsize=10, color="white",
             ha="left", va="top", fontweight="bold")

    fig.subplots_adjust(hspace=0.05)
    return _save_fig(fig, f"rsi_{symbol}_{days}d")


# ── Chart style 6: Bar chart of top movers ───────────────────────────────────

def generate_bar_change() -> str | None:
    """Horizontal bar chart showing 24h % change for top coins."""
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
    style = get_next_chart_style()
    rotation = _load_rotation()
    last_coin = rotation.get("last_coin", "bitcoin")

    filepath = None
    caption = ""

    if style == "line_fill":
        # Pick a coin different from last
        choices = [c for c in _SINGLE_COIN_CHOICES if c[0] != last_coin]
        if not choices:
            choices = _SINGLE_COIN_CHOICES
        coin_id, symbol, days = random.choice(choices)
        filepath = generate_line_fill(coin_id, symbol, days)
        period = {1: "24H", 7: "7D", 30: "30D"}.get(days, f"{days}D")
        caption = f"{symbol} {period} chart. Structure speaks for itself."

    elif style == "candlestick":
        choices = [c for c in _SINGLE_COIN_CHOICES if c[0] != last_coin and c[2] <= 14]
        if not choices:
            choices = [c for c in _SINGLE_COIN_CHOICES if c[2] <= 14]
        coin_id, symbol, days = random.choice(choices)
        filepath = generate_candlestick(coin_id, symbol, days)
        caption = f"{symbol} candlestick chart. Wicks tell the real story."

    elif style == "multi_coin":
        combo = random.choice(_MULTI_COIN_COMBOS)
        coins = [{"id": c[0], "symbol": c[1]} for c in combo]
        days = random.choice([7, 14, 30])
        filepath = generate_multi_coin_chart(coins, days)
        syms = " vs ".join(c[1] for c in combo)
        caption = f"{syms} — {days}-day performance side by side."
        coin_id = combo[0][0]
        symbol = combo[0][1]

    elif style == "volume_price":
        choices = [c for c in _SINGLE_COIN_CHOICES if c[0] != last_coin]
        if not choices:
            choices = _SINGLE_COIN_CHOICES
        coin_id, symbol, days = random.choice(choices)
        filepath = generate_volume_price(coin_id, symbol, days)
        caption = f"{symbol} price action with volume. Follow the money."

    elif style == "momentum":
        choices = [c for c in _SINGLE_COIN_CHOICES if c[0] != last_coin and c[2] >= 7]
        if not choices:
            choices = [c for c in _SINGLE_COIN_CHOICES if c[2] >= 7]
        coin_id, symbol, days = random.choice(choices)
        filepath = generate_momentum(coin_id, symbol, days)
        caption = f"{symbol} price + RSI. Know when the crowd is overextended."

    elif style == "bar_change":
        filepath = generate_bar_change()
        caption = "Top 10 coins — who's winning and losing today."
        coin_id = ""
        symbol = ""

    if filepath:
        record_chart_style(style, locals().get("coin_id", ""))
        logger.info("Chart rotation: used style '%s' for %s", style, locals().get("symbol", "market"))
        return filepath, style, caption
    else:
        # Fallback — try line_fill with BTC as safe default
        logger.info("Chart style '%s' failed — falling back to BTC line chart", style)
        filepath = generate_line_fill("bitcoin", "BTC", 7)
        if filepath:
            record_chart_style("line_fill", "bitcoin")
            return filepath, "line_fill", "BTC 7-day chart. Structure speaks for itself."
        return None, "", ""


# ── News card image generator ─────────────────────────────────────────────────

def generate_news_card(story: dict, tweet_text: str) -> str | None:
    """Bold headline news card — no chart, large quote-mark graphic, sentiment indicator."""
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        import matplotlib.patches as mpatches
        import textwrap
        from datetime import datetime, timezone
    except ImportError:
        return None

    _ensure_chart_dir()
    _cleanup_old_charts()

    title  = story.get("title", "")
    source = story.get("source", "Crypto News")
    score  = story.get("score", 0)
    combined = (title + " " + tweet_text).lower()

    # Detect sentiment from score or keywords
    bearish_kw = ("crash", "dump", "hack", "exploit", "stolen", "ban", "lawsuit",
                  "arrest", "collapse", "bankrupt", "liquidat", "sell", "down", "drop", "fall")
    is_bearish = score < 0 or any(kw in combined for kw in bearish_kw)
    sentiment_color = _ACCENT_RED if is_bearish else _ACCENT_GREEN
    sentiment_label = "BEARISH" if is_bearish else "BULLISH"
    sentiment_dot = "●"

    # Detect primary coin for price ticker
    def _ticker(pair: str) -> tuple[float | None, float | None]:
        try:
            resp = requests.get(_BINANCE_TICKER_URL, params={"symbol": pair}, timeout=10)
            resp.raise_for_status()
            d = resp.json()
            return float(d["lastPrice"]), float(d["priceChangePercent"])
        except Exception:
            return None, None

    if any(kw in combined for kw in ("ethereum", " eth ")):
        p_sym, p_pair = "ETH", "ETHUSDT"
    elif any(kw in combined for kw in ("solana", " sol ")):
        p_sym, p_pair = "SOL", "SOLUSDT"
    elif any(kw in combined for kw in ("ripple", " xrp ")):
        p_sym, p_pair = "XRP", "XRPUSDT"
    else:
        p_sym, p_pair = "BTC", "BTCUSDT"

    p_price, p_pct = _ticker(p_pair)

    # ── Draw card (1600×900 @ 200 DPI) ───────────────────────────────────────
    fig = plt.figure(figsize=(10.67, 6), facecolor=_BG)
    ax = fig.add_axes([0, 0, 1, 1])
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)
    ax.set_facecolor(_BG)
    ax.axis("off")
    _draw_grid_dots(ax, nx=50, ny=30, alpha=0.03)

    # Thin accent line at top
    ax.axhline(0.97, xmin=0.02, xmax=0.98, color=sentiment_color, linewidth=3, clip_on=False)

    # Large faded quote-mark graphic in background
    ax.text(0.06, 0.82, "\u201C", transform=ax.transAxes,
            fontsize=180, color=sentiment_color, alpha=0.08,
            va="top", ha="left", fontweight="bold")

    # Sentiment indicator top-right
    ax.text(0.96, 0.91, f"{sentiment_dot} {sentiment_label}", transform=ax.transAxes,
            fontsize=14, fontweight="bold", color=sentiment_color,
            ha="right", va="center")

    # Source tag top-left
    source_bg = mpatches.FancyBboxPatch(
        (0.03, 0.88), 0.22, 0.06, boxstyle="round,pad=0.008",
        facecolor=_PANEL, edgecolor=_BORDER, linewidth=0.8,
        transform=ax.transAxes, clip_on=False,
    )
    ax.add_patch(source_bg)
    ax.text(0.14, 0.91, source, transform=ax.transAxes,
            fontsize=11, color=_MUTED, ha="center", va="center")

    # ── Headline — 3 lines, large, white, centered ──────────────────────────
    wrapped = textwrap.fill(title, width=38)
    lines = wrapped.split("\n")[:3]
    headline_text = "\n".join(lines)
    ax.text(0.50, 0.72, headline_text, transform=ax.transAxes,
            fontsize=30, fontweight="bold", color="white",
            ha="center", va="top", linespacing=1.4)

    # ── Price ticker bar at bottom ───────────────────────────────────────────
    bar_bg = mpatches.FancyBboxPatch(
        (0.03, 0.06), 0.94, 0.16, boxstyle="round,pad=0.01",
        facecolor=_PANEL, edgecolor=_BORDER, linewidth=1,
        transform=ax.transAxes,
    )
    ax.add_patch(bar_bg)

    if p_price is not None:
        price_str = _price_fmt(p_price)
        pct_str = f"{p_pct:+.2f}%" if p_pct is not None else ""
        pct_color = _ACCENT_GREEN if (p_pct or 0) >= 0 else _ACCENT_RED
        arrow = "▲" if (p_pct or 0) >= 0 else "▼"
        ax.text(0.06, 0.14, p_sym, transform=ax.transAxes,
                fontsize=24, fontweight="bold", color="white", va="center")
        ax.text(0.16, 0.14, price_str, transform=ax.transAxes,
                fontsize=20, color="white", va="center")
        ax.text(0.36, 0.14, f"{arrow} {pct_str}", transform=ax.transAxes,
                fontsize=16, fontweight="bold", color=pct_color, va="center")

    # Timestamp + watermark
    now_str = datetime.now(timezone.utc).strftime("%b %d %Y  %H:%M UTC")
    ax.text(0.97, 0.14, now_str, transform=ax.transAxes,
            fontsize=9, color="#555555", ha="right", va="center")
    ax.text(0.97, 0.02, "@CoinWatchAlert", transform=ax.transAxes,
            fontsize=9, color="#555555", ha="right", va="bottom")

    filepath = os.path.join(_CHART_DIR, f"news_card_{int(time.time())}.png")
    try:
        fig.savefig(filepath, dpi=200, bbox_inches="tight", facecolor=_BG)
    except Exception as exc:
        logger.warning("generate_news_card save failed: %s", exc)
        plt.close(fig)
        return None
    plt.close(fig)
    logger.info("Generated news card: %s", filepath)
    return filepath


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
    if sentiment == "bullish":
        card_type = "bullish"
    elif sentiment == "bearish":
        card_type = "bearish"
    else:
        card_type = "latest"

    price_data = {}
    if coin_data:
        for c in coin_data[:4]:
            sym = c.get("symbol", "?").upper()
            price = c.get("current_price", 0)
            pct = c.get("price_change_percentage_24h_in_currency") or 0
            price_str = f"${price:,.0f}" if price >= 1000 else f"${price:,.2f}"
            pct_str = f"{pct:+.1f}%"
            price_data[sym] = (price_str, pct_str)

    return generate_news_card(
        headline=headline,
        subtitle="",
        price_data=price_data,
        card_type=card_type,
    )


# ── Trending alert card (Bloomberg terminal style) ───────────────────────────

def generate_trending_alert_image(alert: dict) -> str | None:
    """
    Bloomberg terminal-style card for trending coin alerts.
    Output: 1200x675 px (figsize 12x6.75 @ 100 DPI), no external image APIs.

    Layout:
      Left 55%  — type badge | giant ticker | full name | price | Δ24h | rank | vol
      Right 45% — subtle 24h price sparkline
      Footer    — @CoinWatchAlert watermark bottom-right

    alert keys: id, symbol, name, current_price, pct_24h, market_cap_rank,
                volume_24h, source, hook
    """
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        import matplotlib.gridspec as gridspec
        from datetime import datetime, timezone
    except ImportError:
        return None

    _ensure_chart_dir()
    _cleanup_old_charts()

    coin_id = alert.get("id", "")
    symbol  = alert.get("symbol", "?")
    name    = alert.get("name", "")
    price   = alert.get("current_price", 0)
    pct_24h = alert.get("pct_24h", 0)
    rank    = alert.get("market_cap_rank")
    volume  = alert.get("volume_24h", 0)

    is_up  = pct_24h >= 0
    color  = _GREEN if is_up else _RED
    arrow  = "▲" if is_up else "▼"
    sign   = "+" if is_up else ""

    # Fetch sparkline (best-effort; card renders without it)
    sparkline = fetch_price_history(coin_id, days=1) if coin_id else None
    has_spark = bool(sparkline and len(sparkline) > 5)

    fig = plt.figure(figsize=(12, 6.75), facecolor=_BG)

    if has_spark:
        gs     = gridspec.GridSpec(1, 2, width_ratios=[55, 45],
                                   left=0, right=1, top=1, bottom=0,
                                   wspace=0)
        ax_l   = fig.add_subplot(gs[0])
        ax_r   = fig.add_subplot(gs[1])
    else:
        ax_l   = fig.add_axes([0, 0, 1, 1])
        ax_r   = None

    for ax in filter(None, [ax_l, ax_r]):
        ax.set_facecolor(_BG)
        ax.axis("off")

    # ── Left panel — text content ──────────────────────────────────────────
    # All coords are in ax_l axes space (0–1)

    badge = "TRENDING" if alert.get("source") == "trending" else "PRICE MOVER"
    ax_l.text(0.06, 0.93, badge,
              fontsize=13, color=color, fontweight="bold",
              transform=ax_l.transAxes, va="top")

    now_str = datetime.now(timezone.utc).strftime("%b %d, %Y  %H:%M UTC")
    ax_l.text(0.06, 0.86, now_str,
              fontsize=11, color="#555555",
              transform=ax_l.transAxes, va="top")

    # Giant ticker — Bloomberg style
    ax_l.text(0.06, 0.78, symbol,
              fontsize=82, color="white", fontweight="bold",
              fontfamily="monospace",
              transform=ax_l.transAxes, va="top")

    # Separator line under ticker
    ax_l.axhline(y=0.52, xmin=0.06, xmax=0.92,
                 color=color, linewidth=1.2, alpha=0.35)

    # Full name
    ax_l.text(0.06, 0.49, name,
              fontsize=15, color="#888888",
              transform=ax_l.transAxes, va="top")

    # Price
    price_str = _price_fmt(price)
    ax_l.text(0.06, 0.40, price_str,
              fontsize=40, color="white", fontweight="bold",
              transform=ax_l.transAxes, va="top")

    # 24h change
    pct_label = f"{arrow}  {sign}{pct_24h:.2f}%   (24h)"
    ax_l.text(0.06, 0.26, pct_label,
              fontsize=24, color=color, fontweight="bold",
              transform=ax_l.transAxes, va="top")

    # Stats row — rank and volume
    parts = []
    if rank:
        parts.append(f"Rank  #{rank}")
    if volume >= 1e9:
        parts.append(f"Vol  ${volume / 1e9:.1f}B")
    elif volume >= 1e6:
        parts.append(f"Vol  ${volume / 1e6:.0f}M")
    if parts:
        ax_l.text(0.06, 0.14, "   ·   ".join(parts),
                  fontsize=14, color="#666666",
                  transform=ax_l.transAxes, va="top")

    # ── Right panel — sparkline ────────────────────────────────────────────
    if has_spark and ax_r is not None:
        from datetime import datetime as _dt, timezone as _tz
        times  = [_dt.fromtimestamp(p[0] / 1000, tz=_tz.utc) for p in sparkline]
        values = [p[1] for p in sparkline]

        ax_r.set_facecolor(_BG)
        ax_r.set_xlim(times[0], times[-1])
        ax_r.margins(y=0.15)

        ax_r.plot(times, values, color=color, linewidth=2.5, solid_capstyle="round")
        ax_r.fill_between(times, values, min(values),
                          color=color, alpha=0.08)

        # Subtle grid only — no axes labels
        ax_r.yaxis.set_visible(False)
        ax_r.xaxis.set_visible(False)
        for spine in ax_r.spines.values():
            spine.set_visible(False)
        ax_r.grid(True, axis="y", alpha=0.06, color=_GRID)

        # Current price label at the end of the line
        ax_r.annotate(
            price_str,
            xy=(times[-1], values[-1]),
            xytext=(-8, 6), textcoords="offset points",
            fontsize=11, color=color, fontweight="bold", ha="right",
        )

    # Watermark — figure-level so it's always bottom-right
    fig.text(0.98, 0.03, "@CoinWatchAlert",
             fontsize=11, color="#444444",
             ha="right", va="bottom", alpha=0.85)

    filepath = os.path.join(_CHART_DIR, f"trending_{symbol}_{int(time.time())}.png")
    fig.savefig(filepath, dpi=100, facecolor=_BG)
    plt.close(fig)
    logger.info("Generated trending card: %s", filepath)
    return filepath


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
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        import numpy as np
    except ImportError:
        return None

    try:
        _ensure_chart_dir()
        _cleanup_old_charts()

        _DARK_BG = "#0d1117"

        # Quarterly labels Q1 2021 – Q4 2024 (16 quarters)
        labels = [
            "Q1'21", "Q2'21", "Q3'21", "Q4'21",
            "Q1'22", "Q2'22", "Q3'22", "Q4'22",
            "Q1'23", "Q2'23", "Q3'23", "Q4'23",
            "Q1'24", "Q2'24", "Q3'24", "Q4'24",
        ]

        # CEX monthly volumes ($B) — declining from ~$2T to ~$800B
        cex = [1900, 2000, 1750, 1600, 1500, 1350, 1200, 1100,
               1000, 950, 900, 850, 840, 820, 810, 800]

        # DEX monthly volumes ($B) — growing from ~$50B to ~$200B
        dex = [40, 50, 60, 80, 95, 110, 120, 130,
               140, 155, 165, 175, 185, 190, 195, 200]

        x = np.arange(len(labels))

        fig, ax1 = plt.subplots(figsize=(12, 6))
        fig.patch.set_facecolor(_DARK_BG)
        ax1.set_facecolor(_DARK_BG)

        # CEX bars on left axis
        bars = ax1.bar(x, cex, color=_RED, alpha=0.75, width=0.6, label="CEX Volume")
        ax1.set_ylabel("CEX Monthly Volume ($B)", color=_RED, fontsize=11)
        ax1.tick_params(axis="y", colors=_RED, labelsize=10)
        ax1.tick_params(axis="x", colors="#888888", labelsize=9)
        ax1.set_xticks(x)
        ax1.set_xticklabels(labels, rotation=45, ha="right")
        ax1.set_ylim(0, max(cex) * 1.25)
        ax1.spines["top"].set_visible(False)
        ax1.spines["right"].set_color("#333333")
        ax1.spines["bottom"].set_color("#333333")
        ax1.spines["left"].set_color(_RED + "88")
        ax1.grid(True, axis="y", alpha=0.08, color="#444444")

        # DEX line on right axis
        ax2 = ax1.twinx()
        ax2.set_facecolor(_DARK_BG)
        ax2.plot(x, dex, color=_GREEN, linewidth=2.5, marker="o",
                 markersize=5, label="DEX Volume", zorder=5)
        ax2.fill_between(x, dex, alpha=0.12, color=_GREEN)
        ax2.set_ylabel("DEX Monthly Volume ($B)", color=_GREEN, fontsize=11)
        ax2.tick_params(axis="y", colors=_GREEN, labelsize=10)
        ax2.set_ylim(0, max(dex) * 3.5)
        ax2.spines["top"].set_visible(False)
        ax2.spines["right"].set_color(_GREEN + "88")
        ax2.spines["bottom"].set_color("#333333")
        ax2.spines["left"].set_color("#333333")

        ax1.set_title("DEX vs CEX Monthly Trading Volume  (2021 – 2024)",
                      color="white", fontsize=15, fontweight="bold", pad=16)

        # Combined legend
        lines1, labels1 = ax1.get_legend_handles_labels()
        lines2, labels2 = ax2.get_legend_handles_labels()
        ax1.legend(lines1 + lines2, labels1 + labels2,
                   loc="upper right", fontsize=10,
                   facecolor="#1a1a2e", edgecolor="#333333",
                   labelcolor="white")

        # Watermark bottom-right
        fig.text(0.98, 0.02, "@CoinWatchAlert",
                 fontsize=10, color="#555555",
                 ha="right", va="bottom", alpha=0.8)

        filepath = os.path.join(_CHART_DIR, f"dex_vs_cex_{int(time.time())}.png")
        try:
            fig.tight_layout()
        except Exception:
            pass
        fig.savefig(filepath, dpi=150, bbox_inches="tight", facecolor=_DARK_BG)
        import matplotlib.pyplot as _plt
        _plt.close(fig)
        logger.info("Generated DEX vs CEX chart: %s", filepath)
        return filepath
    except Exception as exc:
        logger.warning("generate_dex_vs_cex_chart failed: %s", exc)
        return generate_line_fill("bitcoin", "BTC", 7)


# ── Bitcoin ETF net flows chart ───────────────────────────────────────────────

def generate_etf_flows_chart() -> str | None:
    """
    Bar chart showing Bitcoin ETF monthly net inflows/outflows Jan–Dec 2024.
    Green bars for positive inflows, red bars for outflows.
    """
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        import numpy as np
    except ImportError:
        return None

    try:
        _ensure_chart_dir()
        _cleanup_old_charts()

        _DARK_BG = "#0d1117"

        months = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
                  "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
        flows = [4, 6, 8, -1, 2, 3, 3, 3, 3, 10, 12, 15]  # $B

        x = np.arange(len(months))
        colors = [_GREEN if v >= 0 else _RED for v in flows]

        fig, ax = plt.subplots(figsize=(12, 6))
        fig.patch.set_facecolor(_DARK_BG)
        ax.set_facecolor(_DARK_BG)

        bars = ax.bar(x, flows, color=colors, alpha=0.85, width=0.6)
        for bar, val in zip(bars, flows):
            ax.text(
                bar.get_x() + bar.get_width() / 2,
                val + (0.3 if val >= 0 else -0.5),
                f"${val:+.0f}B",
                ha="center", va="bottom" if val >= 0 else "top",
                fontsize=9, color="white", fontweight="bold",
            )

        ax.axhline(y=0, color="#444444", linewidth=1.0)
        ax.set_xticks(x)
        ax.set_xticklabels(months, color="#888888", fontsize=10)
        ax.tick_params(axis="y", colors="#888888", labelsize=10)
        ax.set_ylabel("Net Inflow ($B)", color="#888888", fontsize=11)
        ax.set_title("Bitcoin ETF Net Flows 2024 ($B)",
                     color="white", fontsize=15, fontweight="bold", pad=16)
        ax.spines["top"].set_visible(False)
        ax.spines["right"].set_visible(False)
        ax.spines["bottom"].set_color("#333333")
        ax.spines["left"].set_color("#333333")
        ax.grid(True, axis="y", alpha=0.08, color="#444444")

        fig.text(0.98, 0.02, "@CoinWatchAlert",
                 fontsize=10, color="#555555",
                 ha="right", va="bottom", alpha=0.8)

        filepath = os.path.join(_CHART_DIR, f"etf_flows_{int(time.time())}.png")
        try:
            fig.tight_layout()
        except Exception:
            pass
        fig.savefig(filepath, dpi=150, bbox_inches="tight", facecolor=_DARK_BG)
        import matplotlib.pyplot as _plt
        _plt.close(fig)
        logger.info("Generated ETF flows chart: %s", filepath)
        return filepath
    except Exception as exc:
        logger.warning("generate_etf_flows_chart failed: %s", exc)
        return generate_line_fill("bitcoin", "BTC", 7)


# ── L2 TVL adoption chart ─────────────────────────────────────────────────────

def generate_l2_adoption_chart() -> str | None:
    """
    Line chart: TVL growth for Arbitrum, Optimism, Base from Q1 2023 to Q4 2024.
    Arbitrum #28A8E0, Base #0052FF, Optimism #FF0420.
    """
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        import numpy as np
    except ImportError:
        return None

    try:
        _ensure_chart_dir()
        _cleanup_old_charts()

        _DARK_BG = "#0d1117"
        _ARB  = "#28A8E0"
        _BASE = "#0052FF"
        _OPT  = "#FF0420"

        quarters = ["Q1'23", "Q2'23", "Q3'23", "Q4'23",
                    "Q1'24", "Q2'24", "Q3'24", "Q4'24"]
        arbitrum = [2.0, 4.5, 6.0, 8.5, 11.0, 14.0, 16.5, 18.0]
        optimism = [1.0, 1.5, 2.0, 3.0,  4.0,  4.5,  5.5,  6.0]
        base     = [0.0, 0.0, 0.5, 1.5,  3.0,  5.0,  7.0,  8.0]

        x = np.arange(len(quarters))

        fig, ax = plt.subplots(figsize=(12, 6))
        fig.patch.set_facecolor(_DARK_BG)
        ax.set_facecolor(_DARK_BG)

        for data, color, label in [
            (arbitrum, _ARB,  "Arbitrum"),
            (optimism, _OPT,  "Optimism"),
            (base,     _BASE, "Base"),
        ]:
            ax.plot(x, data, color=color, linewidth=2.5, marker="o", markersize=5, label=label)
            ax.fill_between(x, data, alpha=0.10, color=color)

        ax.set_xticks(x)
        ax.set_xticklabels(quarters, color="#888888", fontsize=10)
        ax.tick_params(axis="y", colors="#888888", labelsize=10)
        ax.set_ylabel("TVL ($B)", color="#888888", fontsize=11)
        ax.set_title("L2 TVL Growth 2023–2024 ($B)",
                     color="white", fontsize=15, fontweight="bold", pad=16)
        ax.spines["top"].set_visible(False)
        ax.spines["right"].set_visible(False)
        ax.spines["bottom"].set_color("#333333")
        ax.spines["left"].set_color("#333333")
        ax.grid(True, axis="y", alpha=0.08, color="#444444")
        ax.legend(loc="upper left", fontsize=10,
                  facecolor="#1a1a2e", edgecolor="#333333", labelcolor="white")

        fig.text(0.98, 0.02, "@CoinWatchAlert",
                 fontsize=10, color="#555555",
                 ha="right", va="bottom", alpha=0.8)

        filepath = os.path.join(_CHART_DIR, f"l2_adoption_{int(time.time())}.png")
        try:
            fig.tight_layout()
        except Exception:
            pass
        fig.savefig(filepath, dpi=150, bbox_inches="tight", facecolor=_DARK_BG)
        import matplotlib.pyplot as _plt
        _plt.close(fig)
        logger.info("Generated L2 adoption chart: %s", filepath)
        return filepath
    except Exception as exc:
        logger.warning("generate_l2_adoption_chart failed: %s", exc)
        return generate_line_fill("bitcoin", "BTC", 7)


# ── Miner revenue vs hash rate chart ─────────────────────────────────────────

def generate_miner_behaviour_chart() -> str | None:
    """
    Dual-axis chart: BTC miner revenue (gold bars, left axis) vs hash rate
    (white line, right axis) Q1 2023 – Q4 2024. Halving dip annotated.
    """
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        import numpy as np
    except ImportError:
        return None

    try:
        _ensure_chart_dir()
        _cleanup_old_charts()

        _DARK_BG = "#0d1117"
        _GOLD = "#F7931A"

        quarters = ["Q1'23", "Q2'23", "Q3'23", "Q4'23",
                    "Q1'24", "Q2'24", "Q3'24", "Q4'24"]
        revenue  = [1.8, 2.0, 2.2, 3.5, 4.2, 1.8, 3.0, 4.5]  # $B/quarter
        hashrate = [295, 320, 360, 420, 490, 540, 580, 630]    # EH/s

        x = np.arange(len(quarters))

        fig, ax1 = plt.subplots(figsize=(12, 6))
        fig.patch.set_facecolor(_DARK_BG)
        ax1.set_facecolor(_DARK_BG)

        ax1.bar(x, revenue, color=_GOLD, alpha=0.75, width=0.5, label="Miner Revenue ($B)")
        ax1.set_ylabel("Miner Revenue ($B)", color=_GOLD, fontsize=11)
        ax1.tick_params(axis="y", colors=_GOLD, labelsize=10)
        ax1.tick_params(axis="x", colors="#888888", labelsize=10)
        ax1.set_xticks(x)
        ax1.set_xticklabels(quarters, color="#888888")
        ax1.set_ylim(0, max(revenue) * 1.5)
        ax1.spines["top"].set_visible(False)
        ax1.spines["right"].set_color("#333333")
        ax1.spines["bottom"].set_color("#333333")
        ax1.spines["left"].set_color(_GOLD + "88")
        ax1.grid(True, axis="y", alpha=0.08, color="#444444")

        # Halving marker between Q1'24 (index 4) and Q2'24 (index 5)
        ax1.axvline(x=4.5, color=_RED, linewidth=1.2, linestyle="--", alpha=0.6)
        ax1.text(4.6, max(revenue) * 1.38, "Halving\nApr 2024",
                 color=_RED, fontsize=9, va="top", alpha=0.85)

        ax2 = ax1.twinx()
        ax2.set_facecolor(_DARK_BG)
        ax2.plot(x, hashrate, color="white", linewidth=2.5, marker="o",
                 markersize=5, label="Hash Rate (EH/s)", zorder=5)
        ax2.set_ylabel("Hash Rate (EH/s)", color="white", fontsize=11)
        ax2.tick_params(axis="y", colors="white", labelsize=10)
        ax2.set_ylim(0, max(hashrate) * 1.4)
        ax2.spines["top"].set_visible(False)
        ax2.spines["right"].set_color("#888888")
        ax2.spines["bottom"].set_color("#333333")
        ax2.spines["left"].set_color("#333333")

        ax1.set_title("Miner Revenue vs Hash Rate Post-Halving",
                      color="white", fontsize=15, fontweight="bold", pad=16)

        lines1, labels1 = ax1.get_legend_handles_labels()
        lines2, labels2 = ax2.get_legend_handles_labels()
        ax1.legend(lines1 + lines2, labels1 + labels2,
                   loc="upper left", fontsize=10,
                   facecolor="#1a1a2e", edgecolor="#333333", labelcolor="white")

        fig.text(0.98, 0.02, "@CoinWatchAlert",
                 fontsize=10, color="#555555",
                 ha="right", va="bottom", alpha=0.8)

        filepath = os.path.join(_CHART_DIR, f"miner_behaviour_{int(time.time())}.png")
        try:
            fig.tight_layout()
        except Exception:
            pass
        fig.savefig(filepath, dpi=150, bbox_inches="tight", facecolor=_DARK_BG)
        import matplotlib.pyplot as _plt
        _plt.close(fig)
        logger.info("Generated miner behaviour chart: %s", filepath)
        return filepath
    except Exception as exc:
        logger.warning("generate_miner_behaviour_chart failed: %s", exc)
        return generate_line_fill("bitcoin", "BTC", 7)


# ── BTC price vs on-chain activity chart ─────────────────────────────────────

def generate_onchain_vs_price_chart() -> str | None:
    """
    Dual-axis line chart: BTC price (orange, left) vs active addresses
    (purple, right) 2023–2024. Shows divergence periods.
    """
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        import numpy as np
    except ImportError:
        return None

    try:
        _ensure_chart_dir()
        _cleanup_old_charts()

        _DARK_BG   = "#0d1117"
        _ORANGE_L  = "#FF6D00"
        _PURPLE_L  = "#AA00FF"

        quarters  = ["Q1'23", "Q2'23", "Q3'23", "Q4'23",
                     "Q1'24", "Q2'24", "Q3'24", "Q4'24"]
        price     = [23, 27, 28, 37, 52, 64, 58, 90]      # $k averages
        addresses = [0.85, 0.90, 0.88, 0.95, 1.02, 0.92, 0.88, 0.95]  # M/day

        x = np.arange(len(quarters))

        fig, ax1 = plt.subplots(figsize=(12, 6))
        fig.patch.set_facecolor(_DARK_BG)
        ax1.set_facecolor(_DARK_BG)

        ax1.plot(x, price, color=_ORANGE_L, linewidth=2.5, marker="o",
                 markersize=5, label="BTC Price ($k)")
        ax1.fill_between(x, price, alpha=0.08, color=_ORANGE_L)
        ax1.set_ylabel("BTC Price ($k)", color=_ORANGE_L, fontsize=11)
        ax1.tick_params(axis="y", colors=_ORANGE_L, labelsize=10)
        ax1.tick_params(axis="x", colors="#888888", labelsize=10)
        ax1.set_xticks(x)
        ax1.set_xticklabels(quarters, color="#888888")
        ax1.set_ylim(0, max(price) * 1.3)
        ax1.spines["top"].set_visible(False)
        ax1.spines["right"].set_color("#333333")
        ax1.spines["bottom"].set_color("#333333")
        ax1.spines["left"].set_color(_ORANGE_L + "88")
        ax1.grid(True, axis="y", alpha=0.08, color="#444444")

        ax2 = ax1.twinx()
        ax2.set_facecolor(_DARK_BG)
        ax2.plot(x, addresses, color=_PURPLE_L, linewidth=2.5, marker="s",
                 markersize=5, label="Active Addresses (M/day)", zorder=5)
        ax2.fill_between(x, addresses, alpha=0.08, color=_PURPLE_L)
        ax2.set_ylabel("Active Addresses (M/day)", color=_PURPLE_L, fontsize=11)
        ax2.tick_params(axis="y", colors=_PURPLE_L, labelsize=10)
        ax2.set_ylim(0, max(addresses) * 2.2)
        ax2.spines["top"].set_visible(False)
        ax2.spines["right"].set_color(_PURPLE_L + "88")
        ax2.spines["bottom"].set_color("#333333")
        ax2.spines["left"].set_color("#333333")

        ax1.set_title("BTC Price vs On-Chain Activity 2023–2024",
                      color="white", fontsize=15, fontweight="bold", pad=16)

        lines1, labels1 = ax1.get_legend_handles_labels()
        lines2, labels2 = ax2.get_legend_handles_labels()
        ax1.legend(lines1 + lines2, labels1 + labels2,
                   loc="upper left", fontsize=10,
                   facecolor="#1a1a2e", edgecolor="#333333", labelcolor="white")

        fig.text(0.98, 0.02, "@CoinWatchAlert",
                 fontsize=10, color="#555555",
                 ha="right", va="bottom", alpha=0.8)

        filepath = os.path.join(_CHART_DIR, f"onchain_vs_price_{int(time.time())}.png")
        try:
            fig.tight_layout()
        except Exception:
            pass
        fig.savefig(filepath, dpi=150, bbox_inches="tight", facecolor=_DARK_BG)
        import matplotlib.pyplot as _plt
        _plt.close(fig)
        logger.info("Generated on-chain vs price chart: %s", filepath)
        return filepath
    except Exception as exc:
        logger.warning("generate_onchain_vs_price_chart failed: %s", exc)
        return generate_line_fill("bitcoin", "BTC", 7)


# ── BTC/ETF correlation chart ─────────────────────────────────────────────────

def generate_etf_btc_correlation_chart() -> str | None:
    """
    Line chart: BTC 30-day rolling correlation with S&P 500 (blue) and Gold
    (gold) Jan 2023 – Dec 2024. Range -1 to 1. ETF approval annotated.
    """
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        import numpy as np
    except ImportError:
        return None

    try:
        _ensure_chart_dir()
        _cleanup_old_charts()

        _DARK_BG    = "#0d1117"
        _SP500_COL  = "#2979FF"
        _GOLD_COL   = "#F7931A"

        quarters   = ["Q1'23", "Q2'23", "Q3'23", "Q4'23",
                      "Q1'24", "Q2'24", "Q3'24", "Q4'24"]
        corr_sp500 = [0.35, 0.20, 0.15, 0.28, 0.62, 0.70, 0.58, 0.65]
        corr_gold  = [0.10, -0.05, 0.08, 0.15, 0.30, 0.25, 0.20, 0.28]

        x = np.arange(len(quarters))

        fig, ax = plt.subplots(figsize=(12, 6))
        fig.patch.set_facecolor(_DARK_BG)
        ax.set_facecolor(_DARK_BG)

        ax.plot(x, corr_sp500, color=_SP500_COL, linewidth=2.5, marker="o",
                markersize=5, label="BTC / S&P 500")
        ax.fill_between(x, corr_sp500, alpha=0.10, color=_SP500_COL)
        ax.plot(x, corr_gold, color=_GOLD_COL, linewidth=2.5, marker="o",
                markersize=5, label="BTC / Gold")
        ax.fill_between(x, corr_gold, alpha=0.10, color=_GOLD_COL)

        ax.axhline(y=0, color="#444444", linewidth=1.0, linestyle="--")
        # ETF approval annotation (Jan 2024 = between Q4'23 and Q1'24, index 3.5)
        ax.axvline(x=3.5, color=_GREEN, linewidth=1.2, linestyle="--", alpha=0.6)
        ax.text(3.6, 0.88, "ETF\nApproval", color=_GREEN, fontsize=9, va="top", alpha=0.85)

        ax.set_ylim(-1, 1)
        ax.set_xticks(x)
        ax.set_xticklabels(quarters, color="#888888", fontsize=10)
        ax.tick_params(axis="y", colors="#888888", labelsize=10)
        ax.set_ylabel("30-Day Rolling Correlation", color="#888888", fontsize=11)
        ax.set_title("BTC Correlation: S&P 500 vs Gold (2023–2024)",
                     color="white", fontsize=15, fontweight="bold", pad=16)
        ax.spines["top"].set_visible(False)
        ax.spines["right"].set_visible(False)
        ax.spines["bottom"].set_color("#333333")
        ax.spines["left"].set_color("#333333")
        ax.grid(True, axis="y", alpha=0.08, color="#444444")
        ax.legend(loc="upper left", fontsize=10,
                  facecolor="#1a1a2e", edgecolor="#333333", labelcolor="white")

        fig.text(0.98, 0.02, "@CoinWatchAlert",
                 fontsize=10, color="#555555",
                 ha="right", va="bottom", alpha=0.8)

        filepath = os.path.join(_CHART_DIR, f"etf_correlation_{int(time.time())}.png")
        try:
            fig.tight_layout()
        except Exception:
            pass
        fig.savefig(filepath, dpi=150, bbox_inches="tight", facecolor=_DARK_BG)
        import matplotlib.pyplot as _plt
        _plt.close(fig)
        logger.info("Generated ETF/BTC correlation chart: %s", filepath)
        return filepath
    except Exception as exc:
        logger.warning("generate_etf_btc_correlation_chart failed: %s", exc)
        return generate_line_fill("bitcoin", "BTC", 7)


# ── Legacy API (kept for breakout_monitor compatibility) ─────────────────────

def generate_price_chart(coin_id: str, symbol: str, days: int = 7) -> str | None:
    """Legacy wrapper — generates a line_fill chart."""
    return generate_line_fill(coin_id, symbol, days)


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
        ax.axhline(0.97, xmin=0.02, xmax=0.98, color=_ACCENT_GREEN, linewidth=2, clip_on=False)
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
        ax.text(0.97, 0.01, "@CoinWatchAlert", transform=ax.transAxes,
                fontsize=9, color="#555555", va="bottom", ha="right")

        filepath = os.path.join(_CHART_DIR, f"morning_recap_{int(time.time())}.png")
        fig.savefig(filepath, dpi=200, bbox_inches="tight", facecolor=_BG)
        plt.close(fig)
        logger.info("Generated morning recap chart: %s", filepath)
        return filepath
    except Exception as exc:
        logger.warning("generate_morning_recap_chart failed: %s", exc)
        return None


def generate_fear_greed_gauge(value: int, classification: str) -> str | None:
    """Large semicircle gauge with gradient zones, needle, and historical comparison."""
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
        ax = fig.add_axes([0.08, 0.05, 0.84, 0.9])
        ax.set_xlim(-1.5, 1.5)
        ax.set_ylim(-0.6, 1.4)
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
            (40, 60, "#ffcc00", "Neutral"),
            (60, 80, "#88ff44", "Greed"),
            (80, 100, "#00ff88", "Extreme\nGreed"),
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

        ax.text(0, -0.10, str(value), ha="center", va="top",
                fontsize=72, fontweight="bold", color=val_color)
        ax.text(0, -0.30, classification.upper(), ha="center", va="top",
                fontsize=18, fontweight="bold", color=_MUTED)

        # Historical comparison panel
        hist_parts = []
        if last_week_val is not None:
            hist_parts.append(f"Last week: {last_week_val}")
        if last_month_val is not None:
            hist_parts.append(f"Last month: {last_month_val}")
        if hist_parts:
            ax.text(0, -0.48, "  |  ".join(hist_parts), ha="center", va="top",
                    fontsize=12, color=_MUTED)

        # Watermark
        ax.text(1.35, -0.55, "@CoinWatchAlert", ha="right", va="bottom",
                fontsize=9, color="#555555")

        filepath = os.path.join(_CHART_DIR, f"fear_greed_{int(time.time())}.png")
        fig.savefig(filepath, dpi=200, bbox_inches="tight", facecolor=_BG)
        plt.close(fig)
        logger.info("Generated fear/greed gauge: %s", filepath)
        return filepath

    except Exception as exc:
        logger.warning("Fear & Greed gauge generation failed: %s", exc)
        return None


def generate_geo_chart(story: dict) -> str | None:
    """Breaking-news terminal card: bold headline, JUST IN badge, live tickers, sparkline."""
    import textwrap
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        import matplotlib.patches as mpatches
    except ImportError:
        logger.warning("matplotlib not available — cannot generate geo chart")
        return None

    def _binance_ticker(symbol: str) -> tuple[float | None, float | None]:
        try:
            r = requests.get(_BINANCE_TICKER_URL, params={"symbol": symbol}, timeout=5)
            r.raise_for_status()
            d = r.json()
            return float(d["lastPrice"]), float(d["priceChangePercent"])
        except Exception:
            return None, None

    btc_price, btc_pct = _binance_ticker("BTCUSDT")
    eth_price, eth_pct = _binance_ticker("ETHUSDT")

    def _fmt(price, pct):
        if price is None:
            return "N/A", "--", _MUTED
        p_str = _price_fmt(price)
        arrow = "▲" if (pct or 0) >= 0 else "▼"
        pct_str = f"{arrow} {pct:+.2f}%" if pct is not None else "--"
        color = _ACCENT_GREEN if (pct or 0) >= 0 else _ACCENT_RED
        return p_str, pct_str, color

    btc_p, btc_l, btc_c = _fmt(btc_price, btc_pct)
    eth_p, eth_l, eth_c = _fmt(eth_price, eth_pct)

    try:
        os.makedirs(_CHART_DIR, exist_ok=True)

        # 1600×900 @ 200 DPI
        fig = plt.figure(figsize=(10.67, 6), facecolor=_BG)
        ax = fig.add_axes([0, 0, 1, 1])
        ax.set_xlim(0, 1)
        ax.set_ylim(0, 1)
        ax.set_facecolor(_BG)
        ax.axis("off")
        _draw_grid_dots(ax, nx=50, ny=30, alpha=0.03)

        # Red/orange accent bar at top
        ax.axhspan(0.97, 1.0, xmin=0, xmax=0.5, facecolor="#ff4444")
        ax.axhspan(0.97, 1.0, xmin=0.5, xmax=1.0, facecolor="#ff6600")

        # "JUST IN" badge
        badge = mpatches.FancyBboxPatch(
            (0.03, 0.88), 0.13, 0.06, boxstyle="round,pad=0.01",
            facecolor="#ff4444", edgecolor="none",
            transform=ax.transAxes, clip_on=False,
        )
        ax.add_patch(badge)
        ax.text(0.095, 0.91, "JUST IN", transform=ax.transAxes,
                fontsize=12, fontweight="bold", color="white",
                va="center", ha="center")

        source = story.get("source", "Breaking")
        ax.text(0.18, 0.91, source, transform=ax.transAxes,
                fontsize=11, color=_MUTED, va="center", ha="left")

        # Large faded alert icon in background
        ax.text(0.88, 0.75, "⚠", transform=ax.transAxes,
                fontsize=100, color="#ff4444", alpha=0.06,
                va="center", ha="center")

        # Headline — 3 lines, massive
        title = story.get("title", "")
        wrapped = textwrap.fill(title, width=42)
        lines = wrapped.split("\n")[:3]
        ax.text(0.03, 0.78, "\n".join(lines), transform=ax.transAxes,
                fontsize=26, fontweight="bold", color="white",
                va="top", ha="left", linespacing=1.35)

        # ── Ticker panels at bottom ──────────────────────────────────────────
        panels = [
            ("BTC", btc_p, btc_l, btc_c, "bitcoin"),
            ("ETH", eth_p, eth_l, eth_c, "ethereum"),
        ]
        panel_w, panel_h = 0.30, 0.28
        panel_y = 0.06
        panel_gap = 0.03

        for i, (sym, price_s, pct_s, color, coin_id) in enumerate(panels):
            px = 0.03 + i * (panel_w + panel_gap)
            rect = mpatches.FancyBboxPatch(
                (px, panel_y), panel_w, panel_h, boxstyle="round,pad=0.01",
                facecolor=_PANEL, edgecolor=_BORDER, linewidth=1,
                transform=ax.transAxes,
            )
            ax.add_patch(rect)

            ax.text(px + 0.015, panel_y + panel_h - 0.03, sym,
                    transform=ax.transAxes, fontsize=20, fontweight="bold",
                    color="white", va="top", zorder=5)
            ax.text(px + 0.015, panel_y + panel_h * 0.45, price_s,
                    transform=ax.transAxes, fontsize=16, color="white",
                    va="center", zorder=5)
            ax.text(px + 0.015, panel_y + 0.025, pct_s,
                    transform=ax.transAxes, fontsize=14, fontweight="bold",
                    color=color, va="bottom", zorder=5)

            # Sparkline
            spark = _fetch_sparkline(coin_id)
            if spark:
                _draw_sparkline(ax, spark, color,
                                x0=px + panel_w * 0.5, y0=panel_y + 0.02,
                                w=panel_w * 0.45, h=panel_h * 0.5)

        # Timestamp + watermark
        import datetime as _dt
        now_str = _dt.datetime.utcnow().strftime("%b %d %Y  %H:%M UTC")
        ax.text(0.97, 0.12, now_str, transform=ax.transAxes,
                fontsize=9, color="#555555", ha="right", va="center")
        ax.text(0.97, 0.02, "@CoinWatchAlert", transform=ax.transAxes,
                fontsize=9, color="#555555", ha="right", va="bottom")

        filepath = os.path.join(_CHART_DIR, f"geo_{int(time.time())}.png")
        fig.savefig(filepath, dpi=200, bbox_inches="tight", facecolor=_BG)
        plt.close(fig)
        logger.info("Generated geo chart: %s", filepath)
        return filepath

    except Exception as exc:
        logger.warning("Geo chart generation failed: %s", exc)
        return None
