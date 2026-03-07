"""
Crypto.com Exchange API client with retry logic and connection handling.

Handles the common ConnectionResetError and timeout issues seen with
the crypto.com API by implementing exponential backoff retries.
"""
from __future__ import annotations

import hmac
import hashlib
import json
import time
import logging
from typing import Any

import requests

import config

logger = logging.getLogger(__name__)

_BASE_URL = "https://api.crypto.com/exchange/v1/"


def _sign_request(method: str, params: dict, nonce: int) -> str:
    """Create HMAC-SHA256 signature for authenticated requests."""
    param_str = ""
    if params:
        sorted_keys = sorted(params.keys())
        param_str = "".join(f"{k}{params[k]}" for k in sorted_keys)

    sig_payload = f"{method}{nonce}{config.EXCHANGE_API_KEY}{param_str}{nonce}"
    return hmac.new(
        config.EXCHANGE_API_SECRET.encode(),
        sig_payload.encode(),
        hashlib.sha256,
    ).hexdigest()


def _request(
    method: str,
    params: dict | None = None,
    authenticated: bool = False,
    max_retries: int = 3,
    timeout: int = 10,
) -> dict | None:
    """
    Make an API request with retry logic for connection errors.

    Retries on ConnectionResetError, ConnectionError, and timeouts
    with exponential backoff (2s, 4s, 8s).
    """
    params = params or {}

    for attempt in range(max_retries):
        try:
            if authenticated:
                nonce = int(time.time() * 1000)
                sig = _sign_request(method, params, nonce)
                payload = {
                    "id": nonce,
                    "method": method,
                    "api_key": config.EXCHANGE_API_KEY,
                    "params": params,
                    "sig": sig,
                    "nonce": nonce,
                }
            else:
                payload = {
                    "id": int(time.time() * 1000),
                    "method": method,
                    "params": params,
                }

            resp = requests.post(
                _BASE_URL,
                json=payload,
                timeout=timeout,
            )

            if resp.status_code == 429:
                wait = 2 ** (attempt + 1)
                logger.warning("Exchange rate limited (429), retrying in %ds…", wait)
                time.sleep(wait)
                continue

            resp.raise_for_status()
            data = resp.json()

            if data.get("code") != 0:
                logger.error(
                    "Exchange API error: method=%s code=%s msg=%s",
                    method, data.get("code"), data.get("msg"),
                )
                return None

            return data.get("result", {})

        except (ConnectionResetError, ConnectionAbortedError) as exc:
            wait = 2 ** (attempt + 1)
            logger.error(
                "Request failed for %s: %s. Retrying in %ds… (attempt %d/%d)",
                method, exc, wait, attempt + 1, max_retries,
            )
            time.sleep(wait)

        except requests.exceptions.ReadTimeout:
            wait = 2 ** (attempt + 1)
            logger.error(
                "Request failed for %s: Read timed out. (read timeout=%d). "
                "Retrying in %ds… (attempt %d/%d)",
                method, timeout, wait, attempt + 1, max_retries,
            )
            time.sleep(wait)

        except requests.RequestException as exc:
            logger.error(
                "Request failed for %s: %s (attempt %d/%d)",
                method, exc, attempt + 1, max_retries,
            )
            if attempt < max_retries - 1:
                time.sleep(2 ** (attempt + 1))

    logger.error("All %d attempts failed for %s", max_retries, method)
    return None


# ── Public endpoints ─────────────────────────────────────────────────────────

def get_ticker(instrument: str) -> dict | None:
    """Get current ticker for an instrument (e.g., 'BTC_USDT')."""
    return _request("public/get-ticker", {"instrument_name": instrument})


def get_candlestick(
    instrument: str,
    timeframe: str = "1h",
    count: int = 100,
) -> list[dict] | None:
    """
    Get candlestick data for technical analysis.

    timeframe: 1m, 5m, 15m, 30m, 1h, 4h, 6h, 12h, 1D, 7D, 14D, 1M
    """
    result = _request(
        "public/get-candlestick",
        {
            "instrument_name": instrument,
            "timeframe": timeframe,
            "count": count,
        },
        timeout=15,
    )
    if result is None:
        return None
    return result.get("data", [])


# ── Authenticated endpoints ──────────────────────────────────────────────────

def get_account_balance() -> list[dict] | None:
    """Get account balances."""
    result = _request("private/get-account-summary", authenticated=True)
    if result is None:
        return None
    return result.get("accounts", [])


def market_buy(
    instrument: str,
    notional: float,
    dry_run: bool = False,
) -> dict | None:
    """
    Place a market buy order by notional (USD) amount.

    Returns order info dict or None on failure.
    """
    if dry_run:
        logger.info("[DRY RUN] MARKET BUY %s notional=$%.2f", instrument, notional)
        return {"dry_run": True, "instrument": instrument, "notional": notional}

    return _request(
        "private/create-order",
        {
            "instrument_name": instrument,
            "side": "BUY",
            "type": "MARKET",
            "notional": str(notional),
        },
        authenticated=True,
    )


def market_sell(
    instrument: str,
    quantity: float,
    dry_run: bool = False,
) -> dict | None:
    """
    Place a market sell order by quantity.

    Returns order info dict or None on failure.
    """
    if dry_run:
        logger.info("[DRY RUN] MARKET SELL %s qty=%.8f", instrument, quantity)
        return {"dry_run": True, "instrument": instrument, "quantity": quantity}

    return _request(
        "private/create-order",
        {
            "instrument_name": instrument,
            "side": "SELL",
            "type": "MARKET",
            "quantity": str(quantity),
        },
        authenticated=True,
    )
