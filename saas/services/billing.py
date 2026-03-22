"""
Stripe billing integration — subscription management.

Pricing tiers:
  - Free:       $0/mo  — delayed signals (15 min), 3 pairs, no webhooks
  - Pro:        $29/mo — real-time signals, all pairs, webhooks, API access
  - Enterprise: $99/mo — everything + priority signals, custom webhooks, phone support
"""
import os
from datetime import datetime, timezone

import stripe
from sqlalchemy.orm import Session

from saas.models.database import User, ReferralPayout

stripe.api_key = os.getenv("STRIPE_SECRET_KEY", "")

PLANS = {
    "free": {
        "name": "Free",
        "price_monthly": 0,
        "signal_delay_minutes": 15,
        "pairs": ["BTC_USDT", "ETH_USDT", "SOL_USDT"],
        "webhooks": False,
        "api_rate_limit": 100,       # requests/day
        "features": ["3 trading pairs", "15-min delayed signals", "Basic alerts"],
    },
    "pro": {
        "name": "Pro",
        "price_monthly": 29,
        "stripe_price_id": os.getenv("STRIPE_PRO_PRICE_ID", ""),
        "signal_delay_minutes": 0,
        "pairs": "all",
        "webhooks": True,
        "api_rate_limit": 10_000,    # requests/day
        "features": [
            "All trading pairs", "Real-time signals", "Webhook notifications",
            "Full API access", "Telegram alerts", "Trade history & analytics",
        ],
    },
    "enterprise": {
        "name": "Enterprise",
        "price_monthly": 99,
        "stripe_price_id": os.getenv("STRIPE_ENTERPRISE_PRICE_ID", ""),
        "signal_delay_minutes": 0,
        "pairs": "all",
        "webhooks": True,
        "api_rate_limit": 100_000,
        "features": [
            "Everything in Pro", "Priority signal delivery",
            "Custom webhook headers", "Dedicated support",
            "White-label API", "Multi-account management",
        ],
    },
}

REFERRAL_COMMISSION_PCT = 0.20  # 20% recurring commission


def create_checkout_session(user: User, plan: str, success_url: str,
                            cancel_url: str) -> str:
    """Create a Stripe Checkout session and return the URL."""
    plan_config = PLANS.get(plan)
    if not plan_config or plan == "free":
        raise ValueError(f"Invalid plan for checkout: {plan}")

    if not user.stripe_customer_id:
        customer = stripe.Customer.create(email=user.email, name=user.name)
        user.stripe_customer_id = customer.id

    session = stripe.checkout.Session.create(
        customer=user.stripe_customer_id,
        payment_method_types=["card"],
        line_items=[{
            "price": plan_config["stripe_price_id"],
            "quantity": 1,
        }],
        mode="subscription",
        success_url=success_url,
        cancel_url=cancel_url,
        metadata={"user_id": user.id, "plan": plan},
    )
    return session.url


def handle_webhook_event(db: Session, event: dict):
    """Process Stripe webhook events."""
    event_type = event.get("type", "")
    data = event.get("data", {}).get("object", {})

    if event_type == "checkout.session.completed":
        user_id = data.get("metadata", {}).get("user_id")
        plan = data.get("metadata", {}).get("plan", "pro")
        sub_id = data.get("subscription")
        if user_id:
            user = db.query(User).filter(User.id == user_id).first()
            if user:
                user.plan = plan
                user.stripe_subscription_id = sub_id
                _credit_referral(db, user)
                db.commit()

    elif event_type == "customer.subscription.deleted":
        sub_id = data.get("id")
        user = db.query(User).filter(
            User.stripe_subscription_id == sub_id
        ).first()
        if user:
            user.plan = "free"
            user.stripe_subscription_id = ""
            db.commit()

    elif event_type == "invoice.paid":
        sub_id = data.get("subscription")
        user = db.query(User).filter(
            User.stripe_subscription_id == sub_id
        ).first()
        if user and user.referred_by:
            _credit_referral(db, user)


def _credit_referral(db: Session, user: User):
    """Credit 20% commission to the referrer."""
    if not user.referred_by:
        return
    plan_price = PLANS.get(user.plan, {}).get("price_monthly", 0)
    if plan_price <= 0:
        return
    commission = plan_price * REFERRAL_COMMISSION_PCT
    referrer = db.query(User).filter(User.id == user.referred_by).first()
    if referrer:
        referrer.referral_earnings += commission
        payout = ReferralPayout(
            referrer_id=referrer.id,
            referred_id=user.id,
            amount=commission,
        )
        db.add(payout)
        db.commit()


def get_user_plan_config(user: User) -> dict:
    return PLANS.get(user.plan, PLANS["free"])
