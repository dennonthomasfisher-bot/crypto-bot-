"""
Database models and setup for the CryptoSignals SaaS platform.
Uses SQLite for simplicity — swap to PostgreSQL for production scale.
"""
import os
import uuid
import hashlib
import secrets
from datetime import datetime, timezone

from sqlalchemy import (
    create_engine, Column, String, Float, Integer, Boolean,
    DateTime, Text, ForeignKey, Enum as SAEnum, Index,
)
from sqlalchemy.orm import declarative_base, sessionmaker, relationship

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///cryptosignals.db")

engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False} if "sqlite" in DATABASE_URL else {},
)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
Base = declarative_base()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def generate_api_key() -> str:
    return "cs_" + secrets.token_urlsafe(32)


def hash_api_key(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()


# ── Users ────────────────────────────────────────────────────────────────────

class User(Base):
    __tablename__ = "users"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    email = Column(String, unique=True, nullable=False, index=True)
    password_hash = Column(String, nullable=False)
    name = Column(String, default="")
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    # Subscription
    plan = Column(String, default="free")  # free | pro | enterprise
    stripe_customer_id = Column(String, default="")
    stripe_subscription_id = Column(String, default="")
    plan_expires_at = Column(DateTime, nullable=True)

    # Referral
    referral_code = Column(String, unique=True, default=lambda: secrets.token_urlsafe(6))
    referred_by = Column(String, ForeignKey("users.id"), nullable=True)
    referral_earnings = Column(Float, default=0.0)

    # Notification preferences
    webhook_url = Column(String, default="")
    telegram_chat_id = Column(String, default="")
    email_alerts = Column(Boolean, default=True)

    api_keys = relationship("ApiKey", back_populates="user")
    referrals = relationship("User", backref="referrer", remote_side=[id])


# ── API Keys ─────────────────────────────────────────────────────────────────

class ApiKey(Base):
    __tablename__ = "api_keys"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id = Column(String, ForeignKey("users.id"), nullable=False)
    key_hash = Column(String, nullable=False, index=True)
    key_prefix = Column(String, nullable=False)  # first 8 chars for display
    label = Column(String, default="default")
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    last_used_at = Column(DateTime, nullable=True)
    is_active = Column(Boolean, default=True)

    user = relationship("User", back_populates="api_keys")


# ── Trading Signals ──────────────────────────────────────────────────────────

class Signal(Base):
    __tablename__ = "signals"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), index=True)

    # Signal data
    pair = Column(String, nullable=False)          # e.g. BTC_USDT
    direction = Column(String, nullable=False)      # BUY | SELL | HOLD
    confidence = Column(Float, default=0.0)         # 0.0 – 1.0
    entry_price = Column(Float, nullable=True)
    stop_loss = Column(Float, nullable=True)
    take_profit = Column(Float, nullable=True)

    # Technical indicators snapshot
    rsi = Column(Float, nullable=True)
    ema_trend = Column(String, nullable=True)
    momentum = Column(Float, nullable=True)
    bollinger_position = Column(String, nullable=True)
    volume_signal = Column(String, nullable=True)
    composite_score = Column(Float, nullable=True)

    # Outcome tracking
    outcome = Column(String, default="pending")     # pending | win | loss
    exit_price = Column(Float, nullable=True)
    pnl_pct = Column(Float, nullable=True)

    # Tier visibility
    tier = Column(String, default="pro")            # free | pro | enterprise

    __table_args__ = (
        Index("ix_signals_pair_created", "pair", "created_at"),
    )


# ── Price Alerts ─────────────────────────────────────────────────────────────

class PriceAlert(Base):
    __tablename__ = "price_alerts"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    coin = Column(String, nullable=False)
    symbol = Column(String, nullable=False)
    alert_type = Column(String, nullable=False)     # pump | dump | breakout | whale
    change_pct = Column(Float, nullable=True)
    price = Column(Float, nullable=True)
    message = Column(Text, default="")
    tier = Column(String, default="free")


# ── Webhook Deliveries ───────────────────────────────────────────────────────

class WebhookDelivery(Base):
    __tablename__ = "webhook_deliveries"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id = Column(String, ForeignKey("users.id"), nullable=False)
    signal_id = Column(String, nullable=True)
    payload = Column(Text, nullable=False)
    status_code = Column(Integer, nullable=True)
    delivered_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    success = Column(Boolean, default=False)


# ── Referral Payouts ─────────────────────────────────────────────────────────

class ReferralPayout(Base):
    __tablename__ = "referral_payouts"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    referrer_id = Column(String, ForeignKey("users.id"), nullable=False)
    referred_id = Column(String, ForeignKey("users.id"), nullable=False)
    amount = Column(Float, nullable=False)
    currency = Column(String, default="usd")
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))


def init_db():
    Base.metadata.create_all(bind=engine)
