"""
Authentication service — JWT tokens + API key validation.
"""
import os
from datetime import datetime, timedelta, timezone

import bcrypt
import jwt
from sqlalchemy.orm import Session

from saas.models.database import User, ApiKey, hash_api_key, generate_api_key

JWT_SECRET = os.getenv("JWT_SECRET", "change-me-in-production")
JWT_ALGORITHM = "HS256"
JWT_EXPIRY_HOURS = 24


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def verify_password(password: str, hashed: str) -> bool:
    return bcrypt.checkpw(password.encode(), hashed.encode())


def create_jwt(user_id: str, plan: str) -> str:
    payload = {
        "sub": user_id,
        "plan": plan,
        "exp": datetime.now(timezone.utc) + timedelta(hours=JWT_EXPIRY_HOURS),
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)


def decode_jwt(token: str) -> dict | None:
    try:
        return jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
    except jwt.PyJWTError:
        return None


def register_user(db: Session, email: str, password: str, name: str = "",
                  referral_code: str = "") -> tuple[User, str]:
    """Register a new user. Returns (user, api_key_plaintext)."""
    user = User(
        email=email.lower().strip(),
        password_hash=hash_password(password),
        name=name,
    )

    # Handle referral
    if referral_code:
        referrer = db.query(User).filter(User.referral_code == referral_code).first()
        if referrer:
            user.referred_by = referrer.id

    db.add(user)
    db.flush()

    # Generate first API key
    raw_key = generate_api_key()
    api_key = ApiKey(
        user_id=user.id,
        key_hash=hash_api_key(raw_key),
        key_prefix=raw_key[:11],
    )
    db.add(api_key)
    db.commit()
    db.refresh(user)
    return user, raw_key


def authenticate_user(db: Session, email: str, password: str) -> User | None:
    user = db.query(User).filter(User.email == email.lower().strip()).first()
    if user and verify_password(password, user.password_hash):
        return user
    return None


def validate_api_key(db: Session, raw_key: str) -> User | None:
    """Validate an API key and return the associated user."""
    key_h = hash_api_key(raw_key)
    api_key = db.query(ApiKey).filter(
        ApiKey.key_hash == key_h,
        ApiKey.is_active == True,
    ).first()
    if not api_key:
        return None
    api_key.last_used_at = datetime.now(timezone.utc)
    db.commit()
    return db.query(User).filter(User.id == api_key.user_id).first()
