"""
exchange.py – Crypto.com Exchange V1 API wrapper.

Public endpoints (no auth):
  - get_candlesticks(instrument_name, timeframe, depth)

Private endpoints (HMAC-SHA256 auth with CDX_API_KEY / CDX_API_SECRET):
  - get_balance()
  - place_order(instrument_name, side, quantity, order_type, price)
  - get_open_orders(instrument_name)
  - cancel_order(order_id)
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import time

import requests

import config

logger = logging.getLogger("exchange")

_SESSION = requests.Session()
_SESSION.headers.update({"Content-Type": "application/json"})

# ── Helpers ───────────────────────────────────────────────────────────────────

def _nonce() -> int:
    return int(time.time() * 1000)


def _sign(method: str, request_id: int, params: dict, nonce: int) -> str:
    """
    Crypto.com Exchange HMAC-SHA256 signature.

    sig = HMAC_SHA256(api_secret,
                      method + str(request_id) + api_key + param_str + str(nonce))
    where param_str = sorted key+value pairs concatenated as a single string.
    """
    param_str = "".join(
        f"{k}{v}"
        for k, v in sorted(params.items())
    )
    payload = method + str(request_id) + config.CDX_API_KEY + param_str + str(nonce)
    return hmac.new(
        config.CDX_API_SECRET.encode(),
        payload.encode(),
        hashlib.sha256,
    ).hexdigest()


def _public_get(endpoint: str, params: dict | None = None) -> dict | None:
    """GET a public (unauthenticated) endpoint."""
    url = f"{config.CDX_BASE}/{endpoint}"
    for attempt in range(3):
        try:
            resp = _SESSION.get(url, params=params or {}, timeout=15)
            if resp.status_code == 429:
                wait = 2 ** (attempt + 1)
                logger.warning("Rate limited (429) on %s — retrying in %ds", endpoint, wait)
                time.sleep(wait)
                continue
            resp.raise_for_status()
            return resp.json()
        except requests.RequestException as exc:
            logger.error("Request failed for %s: %s", endpoint, exc)
            if attempt < 2:
                time.sleep(2 ** (attempt + 1))
    return None


def _private_post(method: str, params: dict | None = None) -> dict | None:
    """POST a private (authenticated) endpoint."""
    if not config.CDX_API_KEY or not config.CDX_API_SECRET:
        logger.error("CDX_API_KEY / CDX_API_SECRET not set — cannot call private endpoint %s", method)
        return None

    url = f"{config.CDX_BASE}/{method}"
    request_id = int(time.time() * 1000)
    nonce = _nonce()
    params = params or {}

    sig = _sign(method.split("/")[-1], request_id, params, nonce)

    body = {
        "id": request_id,
        "method": method,
        "api_key": config.CDX_API_KEY,
        "params": params,
        "nonce": nonce,
        "sig": sig,
    }

    for attempt in range(3):
        try:
            resp = _SESSION.post(url, data=json.dumps(body), timeout=15)
            if resp.status_code == 429:
                wait = 2 ** (attempt + 1)
                logger.warning("Rate limited (429) on %s — retrying in %ds", method, wait)
                time.sleep(wait)
                continue
            resp.raise_for_status()
            data = resp.json()
            if data.get("code") != 0:
                logger.error("API error on %s: code=%s msg=%s",
                             method, data.get("code"), data.get("message"))
                return None
            return data
        except requests.RequestException as exc:
            logger.error("Request failed for %s: %s", method, exc)
            if attempt < 2:
                time.sleep(2 ** (attempt + 1))
    return None


# ── Public API ────────────────────────────────────────────────────────────────

def get_candlesticks(
    instrument_name: str,
    timeframe: str = "15m",
    depth: int = 200,
) -> list[dict]:
    """
    Fetch OHLCV candles from the public endpoint.

    Returns list of dicts with keys: t (timestamp ms), o, h, l, c, v
    Ordered oldest → newest.
    """
    data = _public_get(
        "public/get-candlestick",
        params={
            "instrument_name": instrument_name,
            "timeframe": timeframe,
            "depth": depth,
        },
    )
    if not data:
        return []
    result = data.get("result", {})
    candles = result.get("data", [])
    if not isinstance(candles, list):
        return []
    # Sort oldest first
    candles.sort(key=lambda c: c.get("t", 0))
    return candles


# ── Private API ───────────────────────────────────────────────────────────────

def get_balance() -> dict[str, float]:
    """
    Fetch spot account balances.
    Returns dict of currency → available amount.
    """
    data = _private_post("private/get-account-summary")
    if not data:
        return {}
    accounts = data.get("result", {}).get("accounts", [])
    return {
        acct["currency"]: float(acct.get("available", 0))
        for acct in accounts
        if float(acct.get("available", 0)) > 0
    }


def place_order(
    instrument_name: str,
    side: str,           # "BUY" or "SELL"
    quantity: float,
    order_type: str = "MARKET",
    price: float | None = None,
) -> dict | None:
    """
    Place an order.  Returns the API response or None on failure.
    side: "BUY" | "SELL"
    order_type: "MARKET" | "LIMIT"
    """
    params: dict = {
        "instrument_name": instrument_name,
        "side": side.upper(),
        "type": order_type.upper(),
        "quantity": str(quantity),
    }
    if order_type.upper() == "LIMIT" and price is not None:
        params["price"] = str(price)

    return _private_post("private/create-order", params)


def get_open_orders(instrument_name: str | None = None) -> list[dict]:
    """Return list of open orders, optionally filtered by instrument."""
    params: dict = {}
    if instrument_name:
        params["instrument_name"] = instrument_name
    data = _private_post("private/get-open-orders", params)
    if not data:
        return []
    return data.get("result", {}).get("order_list", [])


def cancel_order(order_id: str, instrument_name: str) -> bool:
    """Cancel an order by ID.  Returns True if successful."""
    data = _private_post("private/cancel-order", {
        "order_id": order_id,
        "instrument_name": instrument_name,
    })
    return data is not None
