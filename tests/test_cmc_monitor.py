"""Tests for cmc_monitor module."""

import unittest

import cmc_monitor


SAMPLE_CMC_COINS = [
    {
        "id": 1,
        "name": "Bitcoin",
        "symbol": "BTC",
        "cmc_rank": 1,
        "quote": {"USD": {
            "price": 67000,
            "percent_change_24h": 2.5,
            "percent_change_7d": 5.0,
            "market_cap": 1300000000000,
        }},
    },
    {
        "id": 1027,
        "name": "Ethereum",
        "symbol": "ETH",
        "cmc_rank": 2,
        "quote": {"USD": {
            "price": 3500,
            "percent_change_24h": -1.2,
            "percent_change_7d": 3.0,
            "market_cap": 420000000000,
        }},
    },
    {
        "id": 5426,
        "name": "Solana",
        "symbol": "SOL",
        "cmc_rank": 5,
        "quote": {"USD": {
            "price": 150,
            "percent_change_24h": 12.5,
            "percent_change_7d": 20.0,
            "market_cap": 65000000000,
        }},
    },
    {
        "id": 74,
        "name": "Dogecoin",
        "symbol": "DOGE",
        "cmc_rank": 8,
        "quote": {"USD": {
            "price": 0.15,
            "percent_change_24h": -9.5,
            "percent_change_7d": -12.0,
            "market_cap": 20000000000,
        }},
    },
    {
        "id": 52,
        "name": "XRP",
        "symbol": "XRP",
        "cmc_rank": 4,
        "quote": {"USD": {
            "price": 1.35,
            "percent_change_24h": 0.3,
            "percent_change_7d": 1.0,
            "market_cap": 70000000000,
        }},
    },
]


class TestGetTopGainers(unittest.TestCase):
    def test_returns_sorted_by_gain(self):
        gainers = cmc_monitor.get_top_gainers(SAMPLE_CMC_COINS, 3)
        self.assertEqual(len(gainers), 3)
        # SOL (+12.5%) should be first
        self.assertEqual(gainers[0]["symbol"], "SOL")
        # BTC (+2.5%) should be second
        self.assertEqual(gainers[1]["symbol"], "BTC")

    def test_respects_limit(self):
        gainers = cmc_monitor.get_top_gainers(SAMPLE_CMC_COINS, 1)
        self.assertEqual(len(gainers), 1)


class TestGetTopLosers(unittest.TestCase):
    def test_returns_sorted_by_loss(self):
        losers = cmc_monitor.get_top_losers(SAMPLE_CMC_COINS, 2)
        self.assertEqual(len(losers), 2)
        # DOGE (-9.5%) should be first
        self.assertEqual(losers[0]["symbol"], "DOGE")


class TestGetBigMovers(unittest.TestCase):
    def test_finds_movers_above_threshold(self):
        movers = cmc_monitor.get_big_movers(SAMPLE_CMC_COINS, threshold=8.0)
        symbols = [m["symbol"] for m in movers]
        self.assertIn("SOL", symbols)    # +12.5%
        self.assertIn("DOGE", symbols)   # -9.5%
        self.assertNotIn("BTC", symbols)  # +2.5% < 8%

    def test_empty_when_no_movers(self):
        movers = cmc_monitor.get_big_movers(SAMPLE_CMC_COINS, threshold=50.0)
        self.assertEqual(len(movers), 0)


class TestGetMarketBreadth(unittest.TestCase):
    def test_counts_correctly(self):
        breadth = cmc_monitor.get_market_breadth(SAMPLE_CMC_COINS)
        # BTC (+2.5), SOL (+12.5) = 2 green
        # ETH (-1.2), DOGE (-9.5) = 2 red
        # XRP (+0.3) = flat (< 0.5)
        self.assertEqual(breadth["green"], 2)
        self.assertEqual(breadth["red"], 2)
        self.assertEqual(breadth["flat"], 1)
        self.assertEqual(breadth["total"], 5)


class TestFormatMoversTweet(unittest.TestCase):
    def test_generates_tweet(self):
        tweet = cmc_monitor.format_movers_tweet(SAMPLE_CMC_COINS)
        self.assertIsNotNone(tweet)
        self.assertLessEqual(len(tweet), 280)
        self.assertIn("SOL", tweet)  # top gainer
        # No hashtags
        self.assertNotIn("#", tweet)

    def test_empty_list(self):
        tweet = cmc_monitor.format_movers_tweet([])
        self.assertIsNone(tweet)


class TestFormatSpotlightTweet(unittest.TestCase):
    def setUp(self):
        # Reset both local cache and global state cooldowns between tests
        cmc_monitor._recent_movers.clear()
        from state import _state
        _state["price_alerts"] = {}

    def test_generates_template_tweet(self):
        coin = SAMPLE_CMC_COINS[2]  # SOL
        tweet = cmc_monitor.format_spotlight_tweet(coin)
        self.assertIsNotNone(tweet)
        self.assertIn("SOL", tweet)
        self.assertNotIn("#", tweet)

    def test_skips_recent_mover(self):
        coin = SAMPLE_CMC_COINS[2]  # SOL
        # First call should work
        tweet1 = cmc_monitor.format_spotlight_tweet(coin)
        self.assertIsNotNone(tweet1)
        # Second call should return None (already tweeted)
        tweet2 = cmc_monitor.format_spotlight_tweet(coin)
        self.assertIsNone(tweet2)


if __name__ == "__main__":
    unittest.main()
