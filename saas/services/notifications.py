"""
Notification delivery — webhooks, Telegram, email.
Delivers signals to subscribers in real-time.
"""
import json
import logging
import os
from datetime import datetime, timezone

import requests
from sqlalchemy.orm import Session

from saas.models.database import User, Signal, WebhookDelivery

logger = logging.getLogger(__name__)

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
SENDGRID_API_KEY = os.getenv("SENDGRID_API_KEY", "")
FROM_EMAIL = os.getenv("FROM_EMAIL", "signals@cryptosignals.io")


def deliver_signal(db: Session, signal: Signal, users: list[User]):
    """Send a signal to all eligible subscribers."""
    payload = _signal_to_payload(signal)

    for user in users:
        if user.webhook_url:
            _send_webhook(db, user, signal, payload)
        if user.telegram_chat_id and TELEGRAM_BOT_TOKEN:
            _send_telegram(user, signal)
        if user.email_alerts and SENDGRID_API_KEY:
            _send_email(user, signal)


def _signal_to_payload(signal: Signal) -> dict:
    return {
        "signal_id": signal.id,
        "timestamp": signal.created_at.isoformat() if signal.created_at else None,
        "pair": signal.pair,
        "direction": signal.direction,
        "confidence": signal.confidence,
        "entry_price": signal.entry_price,
        "stop_loss": signal.stop_loss,
        "take_profit": signal.take_profit,
        "indicators": {
            "rsi": signal.rsi,
            "ema_trend": signal.ema_trend,
            "momentum": signal.momentum,
            "bollinger": signal.bollinger_position,
            "volume": signal.volume_signal,
            "composite_score": signal.composite_score,
        },
    }


def _send_webhook(db: Session, user: User, signal: Signal, payload: dict):
    """POST signal data to user's webhook URL."""
    delivery = WebhookDelivery(
        user_id=user.id,
        signal_id=signal.id,
        payload=json.dumps(payload),
    )
    try:
        resp = requests.post(
            user.webhook_url,
            json=payload,
            headers={"Content-Type": "application/json",
                     "X-CryptoSignals-Event": "signal"},
            timeout=10,
        )
        delivery.status_code = resp.status_code
        delivery.success = 200 <= resp.status_code < 300
    except requests.RequestException as e:
        delivery.status_code = 0
        delivery.success = False
        logger.warning(f"Webhook delivery failed for user {user.id}: {e}")
    finally:
        db.add(delivery)
        db.commit()


def _send_telegram(user: User, signal: Signal):
    """Send signal alert via Telegram bot."""
    arrow = "\u2B06\uFE0F" if signal.direction == "BUY" else "\u2B07\uFE0F" if signal.direction == "SELL" else "\u27A1\uFE0F"
    text = (
        f"{arrow} *{signal.direction}* — {signal.pair}\n"
        f"Confidence: {signal.confidence:.0%}\n"
        f"Entry: ${signal.entry_price:,.2f}\n"
    )
    if signal.stop_loss:
        text += f"Stop Loss: ${signal.stop_loss:,.2f}\n"
    if signal.take_profit:
        text += f"Take Profit: ${signal.take_profit:,.2f}\n"
    text += f"Score: {signal.composite_score:+.3f}"

    try:
        requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
            json={
                "chat_id": user.telegram_chat_id,
                "text": text,
                "parse_mode": "Markdown",
            },
            timeout=10,
        )
    except requests.RequestException as e:
        logger.warning(f"Telegram delivery failed for user {user.id}: {e}")


def _send_email(user: User, signal: Signal):
    """Send signal alert via SendGrid email."""
    arrow = "BUY" if signal.direction == "BUY" else "SELL" if signal.direction == "SELL" else "HOLD"
    subject = f"[CryptoSignals] {arrow} {signal.pair} — {signal.confidence:.0%} confidence"

    html = f"""
    <h2>{arrow} Signal: {signal.pair}</h2>
    <table style="border-collapse: collapse;">
        <tr><td><b>Direction:</b></td><td>{signal.direction}</td></tr>
        <tr><td><b>Confidence:</b></td><td>{signal.confidence:.0%}</td></tr>
        <tr><td><b>Entry Price:</b></td><td>${signal.entry_price:,.2f}</td></tr>
        <tr><td><b>Stop Loss:</b></td><td>${signal.stop_loss:,.2f if signal.stop_loss else 'N/A'}</td></tr>
        <tr><td><b>Take Profit:</b></td><td>${signal.take_profit:,.2f if signal.take_profit else 'N/A'}</td></tr>
        <tr><td><b>Composite Score:</b></td><td>{signal.composite_score:+.3f}</td></tr>
    </table>
    <p style="color: #888; font-size: 12px;">This is not financial advice. Trade at your own risk.</p>
    """

    try:
        requests.post(
            "https://api.sendgrid.com/v3/mail/send",
            headers={
                "Authorization": f"Bearer {SENDGRID_API_KEY}",
                "Content-Type": "application/json",
            },
            json={
                "personalizations": [{"to": [{"email": user.email}]}],
                "from": {"email": FROM_EMAIL},
                "subject": subject,
                "content": [{"type": "text/html", "value": html}],
            },
            timeout=10,
        )
    except requests.RequestException as e:
        logger.warning(f"Email delivery failed for user {user.id}: {e}")
