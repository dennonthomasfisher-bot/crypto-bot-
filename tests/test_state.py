"""Tests for state module."""

import json
import os
import tempfile
import unittest
from unittest.mock import patch

import state


class TestState(unittest.TestCase):
    def setUp(self):
        self.tmpfile = tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False
        )
        self.tmpfile.close()
        self._orig_state_file = state._STATE_FILE
        state._STATE_FILE = self.tmpfile.name
        # Reset state
        state._state = {
            "price_alerts": {},
            "news_hashes": {},
            "tweet_count": 0,
            "tweet_month": "",
        }

    def tearDown(self):
        state._STATE_FILE = self._orig_state_file
        os.unlink(self.tmpfile.name)

    def test_save_and_load(self):
        state.record_price_alert("bitcoin", "1h")
        state.record_news_posted("abc123")

        # Reset in-memory state
        state._state = {
            "price_alerts": {},
            "news_hashes": {},
            "tweet_count": 0,
            "tweet_month": "",
        }

        state.load()
        self.assertIn("bitcoin", state._state["price_alerts"])
        self.assertIn("abc123", state._state["news_hashes"])

    def test_tweet_counter(self):
        self.assertTrue(state.can_tweet())
        self.assertEqual(state.tweets_remaining(), 1500)

        state.record_tweet()
        self.assertEqual(state.tweets_remaining(), 1499)

    def test_tweet_limit_enforced(self):
        state._state["tweet_count"] = 1500
        state._state["tweet_month"] = state._current_month()
        self.assertFalse(state.can_tweet())
        self.assertEqual(state.tweets_remaining(), 0)

    def test_tweet_counter_resets_monthly(self):
        state._state["tweet_count"] = 1500
        state._state["tweet_month"] = "2020-01"  # old month
        # Should reset for current month
        self.assertTrue(state.can_tweet())
        self.assertEqual(state.tweets_remaining(), 1500)

    def test_price_cooldown(self):
        self.assertTrue(state.price_cooldown_ok("bitcoin", "1h"))
        state.record_price_alert("bitcoin", "1h")
        self.assertFalse(state.price_cooldown_ok("bitcoin", "1h"))

    def test_news_dedup(self):
        self.assertFalse(state.news_already_posted("hash1"))
        state.record_news_posted("hash1")
        self.assertTrue(state.news_already_posted("hash1"))


if __name__ == "__main__":
    unittest.main()
