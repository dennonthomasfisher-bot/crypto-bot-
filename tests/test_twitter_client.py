"""Tests for twitter_client module."""

import unittest
from unittest.mock import patch, MagicMock

import twitter_client
import state


class TestPostTweet(unittest.TestCase):
    def setUp(self):
        state._state = {
            "price_alerts": {},
            "news_hashes": {},
            "tweet_count": 0,
            "tweet_month": "",
        }
        # Reset cached client
        twitter_client._client = None

    @patch("twitter_client.get_client")
    def test_truncates_long_tweets(self, mock_get_client):
        mock_client = MagicMock()
        mock_client.create_tweet.return_value = MagicMock(data={"id": "123"})
        mock_get_client.return_value = mock_client

        long_text = "A " * 200  # way over 280 chars
        twitter_client.post_tweet(long_text)

        call_args = mock_client.create_tweet.call_args
        posted_text = call_args[1]["text"] if "text" in call_args[1] else call_args[0][0]
        self.assertLessEqual(len(posted_text), 280)

    @patch("twitter_client.get_client")
    def test_blocks_when_limit_reached(self, mock_get_client):
        state._state["tweet_count"] = 1500
        state._state["tweet_month"] = state._current_month()

        result = twitter_client.post_tweet("test tweet")
        self.assertFalse(result)
        mock_get_client.assert_not_called()


if __name__ == "__main__":
    unittest.main()
