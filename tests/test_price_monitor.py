"""Tests for price_monitor module."""

import unittest
from unittest.mock import patch, MagicMock

import price_monitor
import state


SAMPLE_MARKET_DATA = [
    {
        "id": "bitcoin",
        "symbol": "btc",
        "current_price": 65000.0,
        "price_change_percentage_1h_in_currency": 6.5,
        "price_change_percentage_24h_in_currency": 3.0,
    },
    {
        "id": "ethereum",
        "symbol": "eth",
        "current_price": 3500.0,
        "price_change_percentage_1h_in_currency": 1.2,
        "price_change_percentage_24h_in_currency": -12.0,
    },
    {
        "id": "solana",
        "symbol": "sol",
        "current_price": 150.0,
        "price_change_percentage_1h_in_currency": None,
        "price_change_percentage_24h_in_currency": None,
    },
]


class TestFormatPriceTweet(unittest.TestCase):
    def test_up_move(self):
        alert = {
            "coin_id": "bitcoin",
            "symbol": "BTC",
            "price_usd": 65000.0,
            "pct_change": 6.5,
            "window": "1h",
            "direction": "up",
        }
        tweet = price_monitor.format_price_tweet(alert)
        self.assertIn("#BTC", tweet)
        self.assertIn("+6.5%", tweet)
        self.assertIn("$65,000.00", tweet)
        self.assertIn("1h", tweet)
        # Should NOT contain hardcoded #Bitcoin for BTC tweets – uses symbol
        self.assertNotIn("#Bitcoin", tweet)

    def test_down_move(self):
        alert = {
            "coin_id": "ethereum",
            "symbol": "ETH",
            "price_usd": 3500.0,
            "pct_change": -12.0,
            "window": "24h",
            "direction": "down",
        }
        tweet = price_monitor.format_price_tweet(alert)
        self.assertIn("#ETH", tweet)
        self.assertIn("-12.0%", tweet)
        self.assertIn("🔴", tweet)


class TestCheckPrices(unittest.TestCase):
    def setUp(self):
        # Reset state for each test
        state._state = {
            "price_alerts": {},
            "news_hashes": {},
            "tweet_count": 0,
            "tweet_month": "",
        }

    @patch("price_monitor._fetch_prices")
    def test_triggers_1h_alert(self, mock_fetch):
        mock_fetch.return_value = SAMPLE_MARKET_DATA
        alerts = price_monitor.check_prices()
        # BTC has 6.5% 1h move (>=5%), should alert
        btc_alerts = [a for a in alerts if a["symbol"] == "BTC"]
        self.assertEqual(len(btc_alerts), 1)
        self.assertEqual(btc_alerts[0]["window"], "1h")

    @patch("price_monitor._fetch_prices")
    def test_triggers_24h_alert(self, mock_fetch):
        mock_fetch.return_value = SAMPLE_MARKET_DATA
        alerts = price_monitor.check_prices()
        # ETH has -12% 24h move (>=10%), should alert
        eth_alerts = [a for a in alerts if a["symbol"] == "ETH"]
        self.assertEqual(len(eth_alerts), 1)
        self.assertEqual(eth_alerts[0]["window"], "24h")

    @patch("price_monitor._fetch_prices")
    def test_skips_none_values(self, mock_fetch):
        mock_fetch.return_value = SAMPLE_MARKET_DATA
        alerts = price_monitor.check_prices()
        # SOL has None for both, should not alert
        sol_alerts = [a for a in alerts if a["symbol"] == "SOL"]
        self.assertEqual(len(sol_alerts), 0)

    @patch("price_monitor._fetch_prices")
    def test_empty_response(self, mock_fetch):
        mock_fetch.return_value = None
        alerts = price_monitor.check_prices()
        self.assertEqual(alerts, [])


if __name__ == "__main__":
    unittest.main()
