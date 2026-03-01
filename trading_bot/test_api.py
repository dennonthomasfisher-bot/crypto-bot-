#!/usr/bin/env python3
"""
test_api.py – Quick connectivity test for the Crypto.com Exchange v1 API.

Run from the trading_bot directory:
    python test_api.py

Fires a raw public/get-candlestick call with instrument_name=BTC_USDT and
timeframe=1m, prints the exact request body and the full response so any
parameter or format issues are immediately visible.
"""
import json
import logging
import time

import requests

logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
)
log = logging.getLogger("test_api")

BASE_URL = "https://api.crypto.com/exchange/v1"

# ── Build the exact request body the spec requires ────────────────────────────
body = {
    "id":     1,
    "method": "public/get-candlestick",
    "params": {
        "instrument_name": "BTC_USDT",
        "timeframe":       "1m",
    },
    "nonce": int(time.time() * 1000),
}

url = f"{BASE_URL}/public/get-candlestick"

log.info("URL   : %s", url)
log.info("BODY  : %s", json.dumps(body, indent=2))

resp = requests.post(url, json=body, headers={"Content-Type": "application/json"}, timeout=10)

log.info("HTTP status : %s", resp.status_code)
log.info("Raw response: %s", resp.text[:2000])

try:
    data = resp.json()
    log.info("Parsed JSON : %s", json.dumps(data, indent=2)[:2000])
    code = data.get("code")
    if code == 0:
        candles = data.get("result", {}).get("data", [])
        log.info("SUCCESS – received %d candles", len(candles))
        if candles:
            log.info("Latest candle: %s", candles[-1])
    else:
        log.error("API error code=%s  message=%s", code, data.get("message"))
except Exception as exc:
    log.error("Could not parse response as JSON: %s", exc)
