"""Tests for tweet_generators module."""

import unittest
from unittest.mock import patch

import config
import tweet_generators


SAMPLE_BTC = {
    "id": "bitcoin",
    "symbol": "btc",
    "current_price": 68000,
    "price_change_percentage_24h_in_currency": 2.5,
    "price_change_percentage_1h_in_currency": 0.3,
    "price_change_percentage_7d_in_currency": 5.1,
}

SAMPLE_COINS = [
    SAMPLE_BTC,
    {
        "id": "ethereum",
        "symbol": "eth",
        "current_price": 3500,
        "price_change_percentage_24h_in_currency": -3.2,
        "price_change_percentage_1h_in_currency": -0.5,
    },
    {
        "id": "solana",
        "symbol": "sol",
        "current_price": 150,
        "price_change_percentage_24h_in_currency": 8.5,
        "price_change_percentage_1h_in_currency": 1.2,
    },
    {
        "id": "ripple",
        "symbol": "xrp",
        "current_price": 1.36,
        "price_change_percentage_24h_in_currency": -1.0,
        "price_change_percentage_1h_in_currency": 0.1,
    },
]


class TestQuoteTweet(unittest.TestCase):
    @patch("tweet_generators._get_btc_data")
    def test_generates_tweet(self, mock_btc):
        mock_btc.return_value = SAMPLE_BTC
        tweet = tweet_generators.generate_quote_tweet()
        self.assertIsNotNone(tweet)
        self.assertLessEqual(len(tweet), 280)
        self.assertIn("#Bitcoin", tweet)

    @patch("tweet_generators._get_btc_data")
    def test_returns_none_on_no_data(self, mock_btc):
        mock_btc.return_value = None
        tweet = tweet_generators.generate_quote_tweet()
        self.assertIsNone(tweet)


class TestMorningRecap(unittest.TestCase):
    @patch("tweet_generators._get_top_coins_data")
    def test_generates_recap(self, mock_coins):
        mock_coins.return_value = SAMPLE_COINS
        tweet = tweet_generators.generate_morning_recap()
        self.assertIsNotNone(tweet)
        self.assertLessEqual(len(tweet), 280)
        self.assertIn("Morning Recap", tweet)
        self.assertIn("BTC", tweet)

    @patch("tweet_generators._get_top_coins_data")
    def test_returns_none_on_no_data(self, mock_coins):
        mock_coins.return_value = []
        tweet = tweet_generators.generate_morning_recap()
        self.assertIsNone(tweet)


class TestOpinionTweet(unittest.TestCase):
    @patch("tweet_generators._get_btc_data")
    def test_generates_opinion(self, mock_btc):
        mock_btc.return_value = SAMPLE_BTC
        tweet = tweet_generators.generate_opinion_tweet()
        self.assertIsNotNone(tweet)
        self.assertLessEqual(len(tweet), 280)
        self.assertIn("#Bitcoin", tweet)


class TestDailyCaps(unittest.TestCase):
    def setUp(self):
        tweet_generators._quote_count = 0
        tweet_generators._quote_day = 0
        tweet_generators._reply_count = 0
        tweet_generators._reply_day = 0

    def test_can_quote_tweet_initially(self):
        self.assertTrue(tweet_generators.can_quote_tweet())

    def test_cap_enforced(self):
        for _ in range(config.QUOTE_TWEET_DAILY_CAP):
            tweet_generators.record_quote_tweet()
        self.assertFalse(tweet_generators.can_quote_tweet())


if __name__ == "__main__":
    unittest.main()
