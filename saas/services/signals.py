"""
Signal generation engine — wraps the existing trading bot's analysis
and publishes signals to the database for subscribers.
"""
import sys
import os
from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

# Add parent dir so we can import the existing trading bot
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from saas.models.database import Signal, PriceAlert, SessionLocal
from saas.services.billing import PLANS

try:
    from trading_bot import TradingBot
    HAS_TRADING_BOT = True
except ImportError:
    HAS_TRADING_BOT = False

try:
    from price_monitor import check_prices
    HAS_PRICE_MONITOR = True
except ImportError:
    HAS_PRICE_MONITOR = False


def generate_signals(db: Session) -> list[Signal]:
    """
    Run the trading bot's analysis on all pairs and store new signals.
    Called on a schedule (every 1–5 minutes).
    """
    if not HAS_TRADING_BOT:
        return []

    bot = TradingBot()
    new_signals = []

    for pair in bot.pairs:
        try:
            analysis = bot.analyze_pair(pair)
            if not analysis:
                continue

            signal = Signal(
                pair=pair,
                direction=analysis.get("action", "HOLD").upper(),
                confidence=abs(analysis.get("composite_score", 0.0)),
                entry_price=analysis.get("price"),
                stop_loss=analysis.get("stop_loss"),
                take_profit=analysis.get("take_profit"),
                rsi=analysis.get("rsi"),
                ema_trend=analysis.get("ema_trend"),
                momentum=analysis.get("momentum"),
                bollinger_position=analysis.get("bollinger_position"),
                volume_signal=analysis.get("volume_signal"),
                composite_score=analysis.get("composite_score"),
                tier="free" if pair in PLANS["free"]["pairs"] else "pro",
            )
            db.add(signal)
            new_signals.append(signal)
        except Exception:
            continue

    if new_signals:
        db.commit()
    return new_signals


def get_signals_for_user(db: Session, user_plan: str, pair: str | None = None,
                         limit: int = 50, offset: int = 0) -> list[Signal]:
    """Fetch signals visible to a user's plan tier."""
    plan_config = PLANS.get(user_plan, PLANS["free"])
    delay_minutes = plan_config["signal_delay_minutes"]

    query = db.query(Signal)

    # Free users only see delayed signals
    if delay_minutes > 0:
        cutoff = datetime.now(timezone.utc) - timedelta(minutes=delay_minutes)
        query = query.filter(Signal.created_at <= cutoff)

    # Free users only see their allowed pairs
    allowed_pairs = plan_config["pairs"]
    if allowed_pairs != "all":
        query = query.filter(Signal.pair.in_(allowed_pairs))

    if pair:
        query = query.filter(Signal.pair == pair)

    return (
        query
        .order_by(Signal.created_at.desc())
        .offset(offset)
        .limit(limit)
        .all()
    )


def get_signal_stats(db: Session) -> dict:
    """Get overall signal performance stats."""
    total = db.query(Signal).count()
    wins = db.query(Signal).filter(Signal.outcome == "win").count()
    losses = db.query(Signal).filter(Signal.outcome == "loss").count()
    pending = db.query(Signal).filter(Signal.outcome == "pending").count()

    win_rate = (wins / (wins + losses) * 100) if (wins + losses) > 0 else 0

    return {
        "total_signals": total,
        "wins": wins,
        "losses": losses,
        "pending": pending,
        "win_rate_pct": round(win_rate, 1),
    }


def get_recent_alerts(db: Session, user_plan: str,
                      limit: int = 20) -> list[PriceAlert]:
    """Get recent price alerts for a user's tier."""
    query = db.query(PriceAlert)
    if user_plan == "free":
        query = query.filter(PriceAlert.tier == "free")
    return query.order_by(PriceAlert.created_at.desc()).limit(limit).all()
