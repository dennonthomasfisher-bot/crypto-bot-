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


# ── Legacy API (kept for breakout_monitor compatibility) ─────────────────────

def generate_price_chart(coin_id: str, symbol: str, days: int = 7) -> str | None:
    """Legacy wrapper — generates a line_fill chart."""
    return generate_line_fill(coin_id, symbol, days)
