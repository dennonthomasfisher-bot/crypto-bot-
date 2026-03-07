"""Tests for trading_bot module."""

import json
import os
import tempfile
import unittest

import trading_bot


class TestPositionManagement(unittest.TestCase):
    def setUp(self):
        self.tmpfile = tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False,
        )
        self.tmpfile.close()
        self._orig = trading_bot._POSITIONS_FILE
        trading_bot._POSITIONS_FILE = self.tmpfile.name
        # Start with empty positions
        with open(self.tmpfile.name, "w") as f:
            json.dump({}, f)

    def tearDown(self):
        trading_bot._POSITIONS_FILE = self._orig
        os.unlink(self.tmpfile.name)

    def test_no_open_position(self):
        self.assertFalse(trading_bot.has_open_position("BTC_USDT"))

    def test_open_and_check(self):
        trading_bot.open_position("BTC_USDT", 68000.0, 0.0003, 20.0)
        self.assertTrue(trading_bot.has_open_position("BTC_USDT"))
        self.assertFalse(trading_bot.has_open_position("ETH_USDT"))

    def test_close_position(self):
        trading_bot.open_position("XRP_USDT", 1.36, 14.7, 20.0)
        pos = trading_bot.close_position("XRP_USDT")
        self.assertIsNotNone(pos)
        self.assertAlmostEqual(pos["entry"], 1.36)
        self.assertFalse(trading_bot.has_open_position("XRP_USDT"))

    def test_close_nonexistent(self):
        pos = trading_bot.close_position("DOGE_USDT")
        self.assertIsNone(pos)

    def test_persists_to_disk(self):
        trading_bot.open_position("BTC_USDT", 68000.0, 0.0003, 20.0)
        with open(self.tmpfile.name) as f:
            data = json.load(f)
        self.assertIn("BTC_USDT", data)


if __name__ == "__main__":
    unittest.main()
