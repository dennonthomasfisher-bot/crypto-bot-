"""Tests for news_monitor module."""

import unittest
from unittest.mock import patch

import news_monitor
import state


SAMPLE_STORIES = [
    {
        "title": "Bitcoin hits new all-time high",
        "url": "https://example.com/btc-ath",
        "currencies": [{"code": "BTC"}, {"code": "ETH"}],
    },
    {
        "title": "Ethereum merge update",
        "url": "https://example.com/eth-merge",
        "currencies": [{"code": "ETH"}],
    },
]


class TestStoryHash(unittest.TestCase):
    def test_stable_hash(self):
        story = {"url": "https://example.com/test", "title": "Test"}
        h1 = news_monitor._story_hash(story)
        h2 = news_monitor._story_hash(story)
        self.assertEqual(h1, h2)

    def test_different_urls_different_hashes(self):
        s1 = {"url": "https://example.com/a"}
        s2 = {"url": "https://example.com/b"}
        self.assertNotEqual(
            news_monitor._story_hash(s1),
            news_monitor._story_hash(s2),
        )

    def test_uses_sha256(self):
        story = {"url": "https://example.com/test"}
        h = news_monitor._story_hash(story)
        # SHA256 hex digest is 64 chars (vs MD5's 32)
        self.assertEqual(len(h), 64)


class TestFormatNewsTweet(unittest.TestCase):
    def test_includes_title(self):
        tweet = news_monitor.format_news_tweet(SAMPLE_STORIES[0])
        self.assertIn("Bitcoin hits new all-time high", tweet)
        # Hashtags should NOT be present (they hurt reach)
        self.assertNotIn("#BTC", tweet)
        self.assertNotIn("#ETH", tweet)

    def test_long_title_truncated(self):
        story = {"title": "A" * 250, "url": "https://example.com", "currencies": []}
        tweet = news_monitor.format_news_tweet(story)
        # Total tweet should be under 280 if title is truncated
        lines = tweet.split("\n")
        title_line = lines[0]
        # Title should have been truncated
        self.assertLess(len(title_line), 210)

    def test_no_hashtags(self):
        story = {"title": "Breaking news", "url": "https://example.com", "currencies": []}
        tweet = news_monitor.format_news_tweet(story)
        # Hashtags should NOT be present (they hurt reach)
        self.assertNotIn("#Crypto", tweet)
        self.assertNotIn("#CryptoNews", tweet)


class TestCheckNews(unittest.TestCase):
    def setUp(self):
        state._state = {
            "price_alerts": {},
            "news_hashes": {},
            "tweet_count": 0,
            "tweet_month": "",
        }

    @patch("news_monitor._fetch_news")
    def test_returns_new_stories(self, mock_fetch):
        mock_fetch.return_value = SAMPLE_STORIES
        stories = news_monitor.check_news()
        self.assertEqual(len(stories), 2)

    @patch("news_monitor._fetch_news")
    def test_deduplicates(self, mock_fetch):
        mock_fetch.return_value = SAMPLE_STORIES
        # First call returns all
        stories1 = news_monitor.check_news()
        self.assertEqual(len(stories1), 2)
        # Second call with same stories returns none
        stories2 = news_monitor.check_news()
        self.assertEqual(len(stories2), 0)


if __name__ == "__main__":
    unittest.main()
