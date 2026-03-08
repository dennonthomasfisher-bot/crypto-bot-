"""
Chart generator – creates price chart images for tweets.

Uses matplotlib to generate clean, dark-themed price charts
that can be attached to tweets for higher engagement.

Charts are saved as temporary PNGs and cleaned up after posting.
"""
from __future__ import annotations

import logging
import os
import tempfile
import time

import requests

import config

logger = logging.getLogger(__name__)

_CHART_DIR = os.path.join(os.path.dirname(__file__), ".charts")


def _ensure_chart_dir() -> None:
    os.makedirs(_CHART_DIR, exist_ok=True)


def _cleanup_old_charts() -> None:
    """Remove chart images older than 1 hour."""
    if not os.path.exists(_CHART_DIR):
        return
    cutoff = time.time() - 3600
    for f in os.listdir(_CHART_DIR):
        path = os.path.join(_CHART_DIR, f)
        if os.path.getmtime(path) < cutoff:
            try:
                os.remove(path)
            except OSError:
                pass


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


def generate_price_chart(
    coin_id: str,
    symbol: str,
    days: int = 7,
) -> str | None:
    """
    Generate a price chart image for a coin.

    Returns the file path to the saved PNG, or None on failure.
    """
    try:
        import matplotlib
        matplotlib.use("Agg")  # non-interactive backend
        import matplotlib.pyplot as plt
        import matplotlib.dates as mdates
        from datetime import datetime, timezone
    except ImportError:
        logger.info("matplotlib not installed — chart generation disabled. Run: pip install matplotlib")
        return None

    prices = fetch_price_history(coin_id, days)
    if not prices or len(prices) < 10:
        return None

    _ensure_chart_dir()
    _cleanup_old_charts()

    # Parse data
    times = [datetime.fromtimestamp(p[0] / 1000, tz=timezone.utc) for p in prices]
    values = [p[1] for p in prices]

    # Determine color based on trend
    start_price = values[0]
    end_price = values[-1]
    is_up = end_price >= start_price
    line_color = "#00C853" if is_up else "#FF1744"
    fill_color = "#00C85320" if is_up else "#FF174420"

    # Create chart with dark theme
    fig, ax = plt.subplots(figsize=(10, 5))
    fig.patch.set_facecolor("#1a1a2e")
    ax.set_facecolor("#1a1a2e")

    # Plot price line with gradient fill
    ax.plot(times, values, color=line_color, linewidth=2.5, antialiased=True)
    ax.fill_between(times, values, min(values), color=fill_color)

    # Price annotations
    pct_change = ((end_price - start_price) / start_price) * 100
    price_str = f"${end_price:,.0f}" if end_price >= 1000 else f"${end_price:,.2f}"
    sign = "+" if pct_change > 0 else ""

    ax.set_title(
        f"{symbol}  {price_str}  ({sign}{pct_change:.1f}%)",
        color="white", fontsize=18, fontweight="bold", pad=15,
    )

    # Style axes
    ax.tick_params(colors="#888888", labelsize=10)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.spines["bottom"].set_color("#333333")
    ax.spines["left"].set_color("#333333")
    ax.yaxis.set_major_formatter(plt.FuncFormatter(
        lambda x, _: f"${x:,.0f}" if x >= 1000 else f"${x:,.2f}"
    ))

    # Date formatting
    if days <= 1:
        ax.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M"))
    elif days <= 7:
        ax.xaxis.set_major_formatter(mdates.DateFormatter("%b %d"))
    else:
        ax.xaxis.set_major_formatter(mdates.DateFormatter("%b %d"))

    ax.grid(True, alpha=0.15, color="#444444")

    # Watermark
    ax.text(
        0.99, 0.02, "@CoinWatchAlert",
        transform=ax.transAxes, fontsize=9, color="#555555",
        ha="right", va="bottom", alpha=0.7,
    )

    # Time period label
    period_labels = {1: "24H", 7: "7D", 14: "14D", 30: "30D", 90: "90D"}
    period = period_labels.get(days, f"{days}D")
    ax.text(
        0.01, 0.02, period,
        transform=ax.transAxes, fontsize=11, color="#888888",
        ha="left", va="bottom", fontweight="bold",
    )

    # Save
    filepath = os.path.join(_CHART_DIR, f"{symbol}_{days}d_{int(time.time())}.png")
    plt.tight_layout()
    plt.savefig(filepath, dpi=150, bbox_inches="tight", facecolor="#1a1a2e")
    plt.close(fig)

    logger.info("Generated chart: %s", filepath)
    return filepath


def generate_multi_coin_chart(
    coins: list[dict],
    days: int = 7,
) -> str | None:
    """
    Generate a comparison chart showing multiple coins' performance.
    coins: list of { "id": ..., "symbol": ... }

    Returns file path to PNG or None.
    """
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        import matplotlib.dates as mdates
        from datetime import datetime, timezone
    except ImportError:
        return None

    if not coins or len(coins) < 2:
        return None

    _ensure_chart_dir()
    _cleanup_old_charts()

    colors = ["#00C853", "#2979FF", "#FF6D00", "#AA00FF", "#FF1744", "#00E5FF"]

    fig, ax = plt.subplots(figsize=(10, 5))
    fig.patch.set_facecolor("#1a1a2e")
    ax.set_facecolor("#1a1a2e")

    for i, coin in enumerate(coins[:6]):
        prices = fetch_price_history(coin["id"], days)
        if not prices or len(prices) < 10:
            continue
        times = [datetime.fromtimestamp(p[0] / 1000, tz=timezone.utc) for p in prices]
        # Normalize to % change from start
        start = prices[0][1]
        normalized = [((p[1] - start) / start) * 100 for p in prices]
        color = colors[i % len(colors)]
        pct = normalized[-1]
        ax.plot(times, normalized, color=color, linewidth=2, label=f"{coin['symbol']} ({pct:+.1f}%)")

    ax.set_title(f"{days}-Day Performance", color="white", fontsize=16, fontweight="bold", pad=15)
    ax.axhline(y=0, color="#444444", linewidth=0.8, linestyle="--")
    ax.yaxis.set_major_formatter(plt.FuncFormatter(lambda x, _: f"{x:+.0f}%"))

    ax.tick_params(colors="#888888", labelsize=10)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.spines["bottom"].set_color("#333333")
    ax.spines["left"].set_color("#333333")
    ax.grid(True, alpha=0.15, color="#444444")
    ax.legend(loc="upper left", fontsize=9, facecolor="#1a1a2e", edgecolor="#333333", labelcolor="white")

    ax.text(0.99, 0.02, "@CoinWatchAlert", transform=ax.transAxes, fontsize=9, color="#555555", ha="right", va="bottom")

    filepath = os.path.join(_CHART_DIR, f"multi_{int(time.time())}.png")
    plt.tight_layout()
    plt.savefig(filepath, dpi=150, bbox_inches="tight", facecolor="#1a1a2e")
    plt.close(fig)

    logger.info("Generated multi-coin chart: %s", filepath)
    return filepath
