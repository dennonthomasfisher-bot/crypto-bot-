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

import json
import logging
import os
import random
import time

import requests

import config

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

# Dark theme constants
_BG = "#1a1a2e"
_GRID = "#444444"
_TEXT = "#888888"
_AXIS = "#333333"
_GREEN = "#00C853"
_RED = "#FF1744"
_GREEN_FILL = "#00C85330"
_RED_FILL = "#FF174430"
_BLUE = "#2979FF"
_ORANGE = "#FF6D00"
_PURPLE = "#AA00FF"
_CYAN = "#00E5FF"

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
    Fetch price history from CoinGecko.
    Returns list of (timestamp_ms, price) tuples.
    """
    try:
        resp = requests.get(
            f"{config.COINGECKO_BASE}/coins/{coin_id}/market_chart",
            params={"vs_currency": "usd", "days": days},
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()
        prices = data.get("prices", [])
        return [(p[0], p[1]) for p in prices]
    except requests.RequestException as exc:
        logger.warning("Failed to fetch price history for %s: %s", coin_id, exc)
        return None


def _fetch_market_chart_full(coin_id: str, days: int = 7) -> dict | None:
    """Fetch prices + volumes from CoinGecko market_chart."""
    try:
        resp = requests.get(
            f"{config.COINGECKO_BASE}/coins/{coin_id}/market_chart",
            params={"vs_currency": "usd", "days": days},
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()
        return {
            "prices": data.get("prices", []),
            "volumes": data.get("total_volumes", []),
        }
    except requests.RequestException as exc:
        logger.warning("Failed to fetch market chart for %s: %s", coin_id, exc)
        return None


def _fetch_ohlc(coin_id: str, days: int = 7) -> list | None:
    """Fetch OHLC data from CoinGecko. Returns [[ts, o, h, l, c], ...]."""
    try:
        resp = requests.get(
            f"{config.COINGECKO_BASE}/coins/{coin_id}/ohlc",
            params={"vs_currency": "usd", "days": days},
            timeout=15,
        )
        resp.raise_for_status()
        return resp.json()
    except requests.RequestException as exc:
        logger.warning("Failed to fetch OHLC for %s: %s", coin_id, exc)
        return None


def _fetch_top_movers() -> list[dict] | None:
    """Fetch top coins with 24h change for bar chart."""
    try:
        resp = requests.get(
            f"{config.COINGECKO_BASE}/coins/markets",
            params={
                "vs_currency": "usd",
                "order": "market_cap_desc",
                "per_page": 20,
                "price_change_percentage": "24h",
            },
            timeout=15,
        )
        resp.raise_for_status()
        return resp.json()
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


def _save_fig(fig, name: str) -> str:
    filepath = os.path.join(_CHART_DIR, f"{name}_{int(time.time())}.png")
    try:
        fig.tight_layout()
    except Exception:
        pass  # Some figures (e.g. news cards with manual axes) don't support tight_layout
    fig.savefig(filepath, dpi=150, bbox_inches="tight", facecolor=_BG)
    import matplotlib.pyplot as plt
    plt.close(fig)
    logger.info("Generated chart: %s", filepath)
    return filepath


def _price_fmt(x, _=None):
    return f"${x:,.0f}" if x >= 1000 else f"${x:,.2f}"


# ── Chart style 1: Line with fill (original) ────────────────────────────────

def generate_line_fill(coin_id: str, symbol: str, days: int = 7) -> str | None:
    """Classic line chart with gradient fill underneath."""
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        from datetime import datetime, timezone
    except ImportError:
        return None

    prices = fetch_price_history(coin_id, days)
    if not prices or len(prices) < 10:
        return None

    _ensure_chart_dir()
    _cleanup_old_charts()

    times = [datetime.fromtimestamp(p[0] / 1000, tz=timezone.utc) for p in prices]
    values = [p[1] for p in prices]
    is_up = values[-1] >= values[0]
    color = _GREEN if is_up else _RED
    fill = _GREEN_FILL if is_up else _RED_FILL

    fig, ax = plt.subplots(figsize=(10, 5))
    fig.patch.set_facecolor(_BG)
    ax.set_facecolor(_BG)

    ax.plot(times, values, color=color, linewidth=2.5)
    ax.fill_between(times, values, min(values), color=fill)

    pct = ((values[-1] - values[0]) / values[0]) * 100
    price_str = _price_fmt(values[-1])
    ax.set_title(f"{symbol}  {price_str}  ({pct:+.1f}%)",
                 color="white", fontsize=18, fontweight="bold", pad=15)

    _style_ax(ax, "%H:%M" if days <= 1 else "%b %d")
    ax.yaxis.set_major_formatter(plt.FuncFormatter(_price_fmt))

    period = {1: "24H", 7: "7D", 14: "14D", 30: "30D", 90: "90D"}.get(days, f"{days}D")
    ax.text(0.01, 0.02, period, transform=ax.transAxes, fontsize=11,
            color=_TEXT, ha="left", va="bottom", fontweight="bold")
    _watermark(ax)

    return _save_fig(fig, f"line_{symbol}_{days}d")


# ── Price alert card ─────────────────────────────────────────────────────────

def generate_price_alert_chart(
    symbol: str,
    coin_id: str,
    price: float,
    pct_change: float,
    window: str = "1h",
) -> str | None:
    """
    Professional dark-theme price alert card.
    Header row: symbol (left) + % change (right).
    Sub-header: current price.
    Body: clean 24h price line with gradient fill.
    Footer: timeframe label (left) + watermark (right).
    """
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        import matplotlib.gridspec as gridspec
        from datetime import datetime, timezone
    except ImportError:
        return None

    plt.close("all")

    _BG_CARD = "#0d1117"
    _BORDER  = "#21262d"
    _LABEL   = "#8b949e"

    days = 1  # always fetch 24h history for alert cards
    prices = fetch_price_history(coin_id, days)
    if not prices or len(prices) < 10:
        return None

    _ensure_chart_dir()
    _cleanup_old_charts()

    times  = [datetime.fromtimestamp(p[0] / 1000, tz=timezone.utc) for p in prices]
    values = [p[1] for p in prices]
    is_up  = pct_change >= 0
    line_color = _GREEN if is_up else _RED
    fill_color = _GREEN_FILL if is_up else _RED_FILL
    pct_color  = _GREEN if is_up else _RED
    pct_sign   = "+" if is_up else ""

    # Price formatting
    if price >= 1000:
        price_str = f"${price:,.0f}"
    elif price >= 1:
        price_str = f"${price:.2f}".rstrip("0").rstrip(".")
        if "." not in price_str:
            price_str = f"${float(price_str[1:]):.0f}"
    else:
        price_str = f"${price:.4f}".rstrip("0")

    fig = plt.figure(figsize=(10, 5), facecolor=_BG_CARD)
    gs  = gridspec.GridSpec(3, 1, height_ratios=[1, 0.4, 4], hspace=0.05)

    # ── Header: symbol left, pct right ──────────────────────────────────────
    ax_hdr = fig.add_subplot(gs[0])
    ax_hdr.set_facecolor(_BG_CARD)
    ax_hdr.axis("off")
    # border line underneath header
    ax_hdr.axhline(0, color=_BORDER, linewidth=1, xmin=0, xmax=1)
    ax_hdr.text(0.01, 0.55, symbol, transform=ax_hdr.transAxes,
                fontsize=28, fontweight="bold", color="white", va="center")
    ax_hdr.text(0.99, 0.55, f"{pct_sign}{pct_change:.1f}%",
                transform=ax_hdr.transAxes,
                fontsize=24, fontweight="bold", color=pct_color,
                va="center", ha="right")

    # ── Sub-header: price ────────────────────────────────────────────────────
    ax_price = fig.add_subplot(gs[1])
    ax_price.set_facecolor(_BG_CARD)
    ax_price.axis("off")
    ax_price.text(0.01, 0.5, price_str, transform=ax_price.transAxes,
                  fontsize=20, color="white", va="center")

    # ── Chart ────────────────────────────────────────────────────────────────
    ax = fig.add_subplot(gs[2])
    ax.set_facecolor(_BG_CARD)

    # Subtle border
    for spine in ax.spines.values():
        spine.set_edgecolor(_BORDER)
        spine.set_linewidth(1)

    ax.plot(times, values, color=line_color, linewidth=2.0, zorder=3)
    ax.fill_between(times, values, min(values), color=fill_color, zorder=2)

    ax.tick_params(colors=_LABEL, labelsize=9)
    ax.xaxis.set_major_formatter(
        plt.matplotlib.dates.DateFormatter("%H:%M" if days <= 1 else "%b %d")
    )
    ax.yaxis.set_major_formatter(plt.FuncFormatter(_price_fmt))
    ax.grid(True, alpha=0.1, color=_BORDER)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)

    # Timeframe label bottom-left
    ax.text(0.01, 0.04, window, transform=ax.transAxes,
            fontsize=11, color=_LABEL, va="bottom", fontweight="bold")

    # Watermark bottom-right
    ax.text(0.99, 0.04, "@CoinWatchAlert", transform=ax.transAxes,
            fontsize=9, color="#555555", ha="right", va="bottom", alpha=0.7)

    fig.patch.set_linewidth(1.5)
    fig.patch.set_edgecolor(_BORDER)

    filepath = os.path.join(_CHART_DIR, f"alert_{symbol}_{int(time.time())}.png")
    try:
        fig.savefig(filepath, dpi=150, bbox_inches="tight",
                    facecolor=_BG_CARD, edgecolor=_BORDER)
    except Exception as exc:
        logger.warning("Failed to save price alert chart: %s", exc)
        plt.close(fig)
        return None
    plt.close(fig)
    plt.close("all")
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

def generate_news_card(
    headline: str,
    subtitle: str = "",
    price_data: dict | None = None,
    card_type: str = "breaking",
) -> str | None:
    """
    Generate a visually striking news-card image for tweets.

    Card types: "breaking" (red accent), "latest" (blue accent),
                "alert" (orange accent), "bullish" (green accent),
                "bearish" (red accent)

    price_data example: {"BTC": ("$68,900", "+2.1%"), "ETH": ("$2,024", "+0.8%")}

    Returns file path to the generated PNG or None on failure.
    """
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        import matplotlib.patches as mpatches
        from textwrap import wrap
    except ImportError:
        return None

    _ensure_chart_dir()
    _cleanup_old_charts()

    # Card color schemes
    _CARD_THEMES = {
        "breaking": {"accent": "#FF1744", "badge": "BREAKING", "badge_bg": "#FF1744"},
        "latest": {"accent": "#2979FF", "badge": "LATEST", "badge_bg": "#2979FF"},
        "alert": {"accent": "#FF6D00", "badge": "ALERT", "badge_bg": "#FF6D00"},
        "bullish": {"accent": "#00C853", "badge": "BULLISH", "badge_bg": "#00C853"},
        "bearish": {"accent": "#FF1744", "badge": "BEARISH", "badge_bg": "#FF1744"},
    }
    theme = _CARD_THEMES.get(card_type, _CARD_THEMES["breaking"])

    fig = plt.figure(figsize=(12, 6.75))  # 16:9 aspect ratio
    fig.patch.set_facecolor("#0d1117")
    ax = fig.add_axes([0, 0, 1, 1])
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)
    ax.set_facecolor("#0d1117")
    ax.axis("off")

    # Background gradient effect using rectangles
    for i in range(20):
        alpha = 0.02 * (20 - i) / 20
        rect = mpatches.FancyBboxPatch(
            (0, 0), 1, 1,
            boxstyle="round,pad=0",
            facecolor=theme["accent"],
            alpha=alpha,
        )
        ax.add_patch(rect)

    # Try to add a mini price chart in the background
    try:
        prices = fetch_price_history("bitcoin", 1)
        if prices and len(prices) > 10:
            x_vals = list(range(len(prices)))
            y_vals = [p[1] for p in prices]
            # Normalize to fit in background
            x_norm = [x / max(x_vals) for x in x_vals]
            y_min, y_max = min(y_vals), max(y_vals)
            y_range = y_max - y_min if y_max != y_min else 1
            y_norm = [0.05 + 0.35 * (y - y_min) / y_range for y in y_vals]
            ax.plot(x_norm, y_norm, color=theme["accent"], alpha=0.12, linewidth=3)
            ax.fill_between(x_norm, y_norm, 0, color=theme["accent"], alpha=0.04)
    except Exception:
        pass

    # Accent bar on the left
    left_bar = mpatches.FancyBboxPatch(
        (0, 0), 0.012, 1,
        boxstyle="round,pad=0",
        facecolor=theme["accent"],
        alpha=0.9,
    )
    ax.add_patch(left_bar)

    # Badge (BREAKING / LATEST / etc.)
    badge = mpatches.FancyBboxPatch(
        (0.04, 0.82), 0.22, 0.1,
        boxstyle="round,pad=0.015",
        facecolor=theme["badge_bg"],
        alpha=0.95,
    )
    ax.add_patch(badge)
    ax.text(
        0.15, 0.87, theme["badge"],
        fontsize=28, fontweight="bold", color="white",
        ha="center", va="center",
        fontfamily="sans-serif",
    )

    # Headline text — wrap long headlines, hard cap at 2 lines
    wrapped = wrap(headline, width=34)
    if len(wrapped) > 2:
        wrapped = wrapped[:2]
        wrapped[-1] = wrapped[-1][:30].rstrip() + "…"
    headline_text = "\n".join(wrapped)
    y_start = 0.72
    ax.text(
        0.04, y_start, headline_text,
        fontsize=32, fontweight="bold", color="white",
        va="top", ha="left",
        fontfamily="sans-serif",
        linespacing=1.35,
    )

    # Subtitle
    if subtitle:
        sub_wrapped = wrap(subtitle, width=50)
        sub_text = "\n".join(sub_wrapped[:2])
        ax.text(
            0.04, 0.38, sub_text,
            fontsize=18, color="#aaaaaa",
            va="top", ha="left",
            fontfamily="sans-serif",
            linespacing=1.3,
        )

    # Price data boxes
    if price_data:
        x_pos = 0.04
        for symbol, (price_str, pct_str) in list(price_data.items())[:4]:
            # Price box background
            box = mpatches.FancyBboxPatch(
                (x_pos, 0.06), 0.2, 0.2,
                boxstyle="round,pad=0.015",
                facecolor="#1a1a2e",
                edgecolor="#333333",
                linewidth=1,
                alpha=0.9,
            )
            ax.add_patch(box)

            # Symbol
            ax.text(
                x_pos + 0.1, 0.21, symbol,
                fontsize=13, fontweight="bold", color="#888888",
                ha="center", va="center",
            )
            # Price
            ax.text(
                x_pos + 0.1, 0.16, price_str,
                fontsize=16, fontweight="bold", color="white",
                ha="center", va="center",
            )
            # Percentage
            is_positive = "+" in pct_str
            pct_color = _GREEN if is_positive else _RED
            ax.text(
                x_pos + 0.1, 0.10, pct_str,
                fontsize=13, fontweight="bold", color=pct_color,
                ha="center", va="center",
            )
            x_pos += 0.23

    # Watermark
    ax.text(
        0.97, 0.03, "@CoinWatchAlert",
        fontsize=11, color="#555555",
        ha="right", va="bottom", alpha=0.8,
    )

    # Timestamp
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).strftime("%b %d, %Y  %H:%M UTC")
    ax.text(
        0.97, 0.92, now,
        fontsize=11, color="#666666",
        ha="right", va="center",
    )

    return _save_fig(fig, f"news_{card_type}")


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
    legend = ax1.legend(lines1 + lines2, labels1 + labels2,
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


# ── Legacy API (kept for breakout_monitor compatibility) ─────────────────────

def generate_price_chart(coin_id: str, symbol: str, days: int = 7) -> str | None:
    """Legacy wrapper — generates a line_fill chart."""
    return generate_line_fill(coin_id, symbol, days)


def generate_morning_recap_chart(coins: list[dict]) -> str | None:
    """Generate a dark-theme market overview bar chart for the morning recap tweet."""
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

        fig, ax = plt.subplots(figsize=(10, 6))
        fig.patch.set_facecolor("#0d1117")
        ax.set_facecolor("#0d1117")

        # Determine max bar length for scaling
        def _coin_pct(c: dict) -> float:
            return (
                c.get("price_change_percentage_24h_in_currency")
                or c.get("price_change_percentage_24h")
                or c.get("pct_24h")
                or c.get("change_24h")
                or 0
            )

        max_abs_pct = max(abs(_coin_pct(c)) for c in top5) or 1.0

        y_positions = list(range(len(top5) - 1, -1, -1))  # top coin at top

        for i, (coin, ypos) in enumerate(zip(top5, y_positions)):
            symbol = coin.get("symbol", "???").upper()
            price_raw = coin.get("current_price", 0) or 0
            pct = _coin_pct(coin)

            # Price formatting
            if price_raw >= 1000:
                price_str = f"${price_raw:,.0f}"
            elif price_raw >= 1:
                price_str = f"${price_raw:.2f}".rstrip('0').rstrip('.')
                if '.' not in price_str:
                    price_str = f"${float(price_str[1:]):.0f}"
            else:
                price_str = f"${price_raw:.4f}".rstrip('0')

            colour = _GREEN if pct >= 0 else _RED
            bar_width = (abs(pct) / max_abs_pct) * 0.72  # max 72% of x-axis

            # Horizontal bar starting at x=0.26 (after label area)
            bar_left = 0.26
            rect = mpatches.FancyBboxPatch(
                (bar_left, ypos - 0.32),
                bar_width,
                0.64,
                boxstyle="round,pad=0.01",
                facecolor=colour + "40",  # ~25% opacity fill
                edgecolor=colour,
                linewidth=1.2,
                transform=ax.transData,
            )
            ax.add_patch(rect)

            # Coin symbol — bold white, left edge
            ax.text(
                0.01, ypos,
                symbol,
                color="white", fontsize=13, fontweight="bold",
                va="center", ha="left",
                transform=ax.transData,
            )

            # Price — muted, just right of symbol
            ax.text(
                0.13, ypos,
                price_str,
                color="#8b949e", fontsize=11,
                va="center", ha="left",
                transform=ax.transData,
            )

            # % change at end of bar
            pct_label = f"{'+' if pct >= 0 else ''}{pct:.1f}%"
            ax.text(
                bar_left + bar_width + 0.012, ypos,
                pct_label,
                color=colour, fontsize=12, fontweight="bold",
                va="center", ha="left",
                transform=ax.transData,
            )

        # Subtle horizontal grid lines between rows
        for ypos in y_positions:
            ax.axhline(y=ypos - 0.5, color="#21262d", linewidth=0.8, zorder=0)

        # Axes config
        ax.set_xlim(0, 1)
        ax.set_ylim(-0.7, len(top5) - 0.3)
        ax.axis("off")

        # Title — "MARKET OVERVIEW", top left
        fig.text(
            0.04, 0.93,
            "M A R K E T   O V E R V I E W",
            color="white", fontsize=14, fontweight="bold",
            ha="left", va="top",
        )

        # Date/time — top right
        now_str = __import__("datetime").datetime.utcnow().strftime("%b %d %Y  %H:%M UTC")
        fig.text(
            0.96, 0.93,
            now_str,
            color="#8b949e", fontsize=10,
            ha="right", va="top",
        )

        # Watermark bottom right
        fig.text(
            0.96, 0.03,
            "@CoinWatchAlert",
            color="#8b949e", fontsize=9,
            ha="right", va="bottom",
        )

        filepath = os.path.join(_CHART_DIR, f"morning_recap_{int(time.time())}.png")
        try:
            fig.tight_layout(rect=[0, 0.06, 1, 0.90])
        except Exception:
            pass
        fig.savefig(filepath, dpi=150, bbox_inches="tight", facecolor="#0d1117")
        plt.close(fig)
        logger.info("Generated morning recap chart: %s", filepath)
        return filepath
    except Exception as exc:
        logger.warning("generate_morning_recap_chart failed: %s", exc)
        return None


def generate_fear_greed_gauge(value: int, classification: str) -> str | None:
    """Render a semicircular Fear & Greed gauge and return the saved PNG path."""
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

        fig, ax = plt.subplots(figsize=(10, 6))
        fig.patch.set_facecolor("#0d1117")
        ax.set_facecolor("#0d1117")
        ax.set_xlim(-1.2, 1.2)
        ax.set_ylim(-0.35, 1.2)
        ax.set_aspect("equal")
        ax.axis("off")

        # Zone definitions: (start_val, end_val, colour, label)
        zones = [
            (0,  25, "#FF1744", "Extreme Fear"),
            (25, 45, "#FF6D00", "Fear"),
            (45, 55, "#FFD600", "Neutral"),
            (55, 75, "#76FF03", "Greed"),
            (75, 100, "#00E676", "Extreme Greed"),
        ]

        def _val_to_angle(v: float) -> float:
            """Map 0–100 → 180°–0° (left to right across the top semicircle)."""
            return 180.0 - v * 1.8

        outer_r = 0.9
        inner_r = 0.55

        for start_v, end_v, colour, label in zones:
            theta1 = _val_to_angle(end_v)   # matplotlib: CCW, so end_v gives lower angle
            theta2 = _val_to_angle(start_v)
            wedge = mpatches.Wedge(
                center=(0, 0),
                r=outer_r,
                theta1=theta1,
                theta2=theta2,
                width=outer_r - inner_r,
                facecolor=colour,
                edgecolor="#0d1117",
                linewidth=1.5,
            )
            ax.add_patch(wedge)

            # Zone label at arc midpoint
            mid_v = (start_v + end_v) / 2
            mid_angle_rad = np.radians(_val_to_angle(mid_v))
            label_r = 1.05
            lx = label_r * np.cos(mid_angle_rad)
            ly = label_r * np.sin(mid_angle_rad) + 0.08
            ax.text(
                lx, ly, label,
                ha="center", va="center",
                fontsize=9, color="white",
                rotation=0,
            )

        # Needle
        needle_angle_rad = np.radians(_val_to_angle(value))
        needle_len = 0.75
        nx = needle_len * np.cos(needle_angle_rad)
        ny = needle_len * np.sin(needle_angle_rad)
        ax.annotate(
            "",
            xy=(nx, ny),
            xytext=(0, 0),
            arrowprops=dict(
                arrowstyle="->,head_width=0.04,head_length=0.06",
                color="white",
                lw=2.5,
            ),
        )
        # Needle pivot dot
        pivot = plt.Circle((0, 0), 0.04, color="white", zorder=5)
        ax.add_patch(pivot)

        # Centre text: big value number
        ax.text(
            0, -0.05, str(value),
            ha="center", va="top",
            fontsize=48, fontweight="bold", color="white",
        )
        ax.text(
            0, -0.22, classification,
            ha="center", va="top",
            fontsize=16, color="#8b949e",
        )

        # Watermark
        ax.text(
            1.18, -0.32, "@CoinWatchAlert",
            ha="right", va="bottom",
            fontsize=9, color="#8b949e",
            transform=ax.transData,
        )

        filepath = os.path.join(_CHART_DIR, f"fear_greed_{int(time.time())}.png")
        fig.savefig(filepath, dpi=150, bbox_inches="tight", facecolor="#0d1117")
        plt.close(fig)
        logger.info("Generated fear/greed gauge: %s", filepath)
        return filepath

    except Exception as exc:
        logger.warning("Fear & Greed gauge generation failed: %s", exc)
        return None


def generate_geo_chart(story: dict) -> str | None:
    """
    Create a dark breaking-news graphic for a geopolitical/macro story.

    Layout:
        "BREAKING" in red top-left (bold)
        Story title wrapped in white, centred
        Subtle red horizontal divider
        @CoinWatchAlert watermark bottom-right

    Saves to .charts/geo_{timestamp}.png. Returns path or None on failure.
    """
    import textwrap
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        logger.warning("matplotlib not available — cannot generate geo chart")
        return None

    try:
        os.makedirs(_CHART_DIR, exist_ok=True)

        bg = "#0d1117"
        fig, ax = plt.subplots(figsize=(10, 6))
        fig.patch.set_facecolor(bg)
        ax.set_facecolor(bg)
        ax.axis("off")

        # "BREAKING" label top-left
        ax.text(
            0.03, 0.93, "BREAKING",
            transform=ax.transAxes,
            fontsize=20, fontweight="bold", color="#FF1744",
            va="top", ha="left",
        )

        # Red horizontal divider below "BREAKING"
        ax.plot([0.05, 0.95], [0.72, 0.72], transform=fig.transFigure,
                color='#FF1744', linewidth=1, clip_on=False)

        # Story title — wrapped, centred
        title = story.get("title", "")
        wrapped = "\n".join(textwrap.wrap(title, width=52))
        ax.text(
            0.5, 0.54, wrapped,
            transform=ax.transAxes,
            fontsize=18, color="white",
            va="center", ha="center",
            multialignment="center",
            wrap=True,
        )

        # Watermark bottom-right
        ax.text(
            0.97, 0.04, "@CoinWatchAlert",
            transform=ax.transAxes,
            fontsize=10, color="#8b949e",
            va="bottom", ha="right",
        )

        filepath = os.path.join(_CHART_DIR, f"geo_{int(time.time())}.png")
        fig.savefig(filepath, dpi=150, bbox_inches="tight", facecolor=bg)
        plt.close(fig)
        logger.info("Generated geo chart: %s", filepath)
        return filepath

    except Exception as exc:
        logger.warning("Geo chart generation failed: %s", exc)
        return None
