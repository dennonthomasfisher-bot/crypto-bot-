"""
exchange.py – Crypto.com Exchange REST API v1 client.

Reference: https://exchange-docs.crypto.com/exchange/v1/rest-ws/index.html

All public endpoints use POST with no auth.
All private endpoints use POST with HMAC-SHA256 authentication.
"""
from __future__ import annotations

import hashlib
import hmac
import logging
import time
from typing import Any, Dict, List, Optional

import requests

logger = logging.getLogger(__name__)

BASE_URL = "https://api.crypto.com/v2"


class CryptoComClient:
    """
    Thin wrapper around the Crypto.com Exchange v1 REST API.

    Parameters
    ----------
    api_key:    Obtained from the Crypto.com Exchange dashboard.
    api_secret: Paired secret for HMAC signing.
    dry_run:    When True, order methods log their intent but never hit the API.
    """

    def __init__(self, api_key: str, api_secret: str, dry_run: bool = True) -> None:
        self.api_key = api_key
        self.api_secret = api_secret
        self.dry_run = dry_run
        self._request_id = 0
        self._session = requests.Session()
        self._session.headers.update({"Content-Type": "application/json"})

    # ── Internal helpers ──────────────────────────────────────────────────────

    def _next_id(self) -> int:
        self._request_id += 1
        return self._request_id

    def _sign(self, method: str, req_id: int, nonce: int, params: Dict) -> str:
        """
        Produce HMAC-SHA256 signature per Crypto.com spec:
          sig_payload = method + id + api_key + sorted(params as key+value) + nonce
        """
        param_str = "".join(f"{k}{params[k]}" for k in sorted(params))
        sig_payload = method + str(req_id) + self.api_key + param_str + str(nonce)
        return hmac.new(
            self.api_secret.encode("utf-8"),
            sig_payload.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()

    def _post(
        self,
        method: str,
        params: Optional[Dict[str, Any]] = None,
        public: bool = False,
    ) -> Dict:
        params = params or {}
        req_id = self._next_id()
        nonce = int(time.time() * 1000)

        body: Dict[str, Any] = {
            "id": req_id,
            "method": method,
            "nonce": nonce,
            "params": params,
        }

        if not public:
            body["api_key"] = self.api_key
            body["sig"] = self._sign(method, req_id, nonce, params)

        url = f"{BASE_URL}/{method}"
        try:
            resp = self._session.post(url, json=body, timeout=10)
            if not resp.ok:
                logger.error(
                    "HTTP %s calling %s – body: %s",
                    resp.status_code, method, resp.text[:500],
                )
                return {}
            data = resp.json()
        except requests.RequestException as exc:
            logger.error("HTTP error calling %s: %s", method, exc)
            return {}

        code = data.get("code", -1)
        if code != 0:
            logger.warning(
                "API error code=%s msg=%s (method=%s)",
                code, data.get("message", ""), method,
            )
            return {}

        return data.get("result", {})

    # ── Public market-data endpoints ──────────────────────────────────────────

    def get_candlestick(
        self, instrument: str, timeframe: str = "1h"
    ) -> List[Dict]:
        """
        Fetch OHLCV candles for `instrument`.

        Returned list is ordered oldest → newest.
        Each dict has keys: t (timestamp ms), o, h, l, c, v (all strings).

        Note: the v2 API only accepts instrument_name and timeframe; there is
        no count/limit parameter — the endpoint returns a fixed window of data.
        """
        result = self._post(
            "public/get-candlestick",
            {"instrument_name": instrument, "timeframe": timeframe},
            public=True,
        )
        return result.get("data", [])

    def get_ticker(self, instrument: str) -> Optional[Dict]:
        """Return the latest ticker snapshot for `instrument`, or None on error."""
        result = self._post(
            "public/get-ticker",
            {"instrument_name": instrument},
            public=True,
        )
        items = result.get("data", [])
        return items[0] if items else None

    def get_instruments(self) -> List[Dict]:
        """Return the full list of tradeable instruments."""
        result = self._post("public/get-instruments", public=True)
        return result.get("data", {}).get("instruments", [])

    # ── Private account endpoints ─────────────────────────────────────────────

    def get_balance(self) -> Dict[str, float]:
        """
        Return a mapping of {currency_code: quantity} for all non-zero balances.
        Example: {"USDT": 1200.50, "BTC": 0.012}
        """
        result = self._post("private/user-balance")
        balances: Dict[str, float] = {}
        for item in result.get("position_balances", []):
            qty = float(item.get("quantity", 0))
            if qty > 0:
                balances[item["instrument_name"]] = qty
        return balances

    def get_usdt_balance(self) -> float:
        """Convenience shortcut – returns available USDT balance."""
        return self.get_balance().get("USDT", 0.0)

    # ── Private order endpoints ───────────────────────────────────────────────

    def create_market_buy(self, instrument: str, notional_usd: float) -> Optional[Dict]:
        """
        Place a market BUY for `notional_usd` worth of `instrument`.

        In dry-run mode the order is logged but never sent to the exchange.
        Returns the API result dict (or a synthetic one in dry-run).
        """
        if self.dry_run:
            logger.info(
                "[DRY RUN] MARKET BUY  %-15s  notional=$%.2f",
                instrument, notional_usd,
            )
            return {"order_id": f"dry_buy_{int(time.time())}", "dry_run": True}

        return self._post("private/create-order", {
            "instrument_name": instrument,
            "side": "BUY",
            "type": "MARKET",
            "notional": str(round(notional_usd, 2)),
        })

    def create_market_sell(self, instrument: str, quantity: float) -> Optional[Dict]:
        """
        Place a market SELL for `quantity` units of the base asset.

        In dry-run mode the order is logged but never sent to the exchange.
        """
        if self.dry_run:
            logger.info(
                "[DRY RUN] MARKET SELL %-15s  qty=%.8f",
                instrument, quantity,
            )
            return {"order_id": f"dry_sell_{int(time.time())}", "dry_run": True}

        return self._post("private/create-order", {
            "instrument_name": instrument,
            "side": "SELL",
            "type": "MARKET",
            "quantity": str(quantity),
        })

    def get_open_orders(self, instrument: Optional[str] = None) -> List[Dict]:
        """Return all open orders, optionally filtered by instrument."""
        params: Dict[str, Any] = {}
        if instrument:
            params["instrument_name"] = instrument
        result = self._post("private/get-open-orders", params)
        return result.get("order_list", [])

    def cancel_order(self, instrument: str, order_id: str) -> Dict:
        """Cancel a specific open order."""
        if self.dry_run:
            logger.info("[DRY RUN] CANCEL order %s on %s", order_id, instrument)
            return {"dry_run": True}
        return self._post("private/cancel-order", {
            "instrument_name": instrument,
            "order_id": order_id,
        })
