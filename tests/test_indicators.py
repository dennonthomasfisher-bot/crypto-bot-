"""Tests for indicators module."""

import unittest

import indicators


def _make_candles(closes: list[float], volumes: list[float] | None = None) -> list[dict]:
    """Helper to create candle dicts from close prices."""
    if volumes is None:
        volumes = [100.0] * len(closes)
    return [
        {"c": str(c), "v": str(v), "h": str(c * 1.01), "l": str(c * 0.99)}
        for c, v in zip(closes, volumes)
    ]


class TestEMA(unittest.TestCase):
    def test_basic_ema(self):
        values = list(range(1, 21))  # 1..20
        result = indicators.ema(values, 5)
        self.assertTrue(len(result) > 0)
        # EMA should trend upward with rising values
        self.assertGreater(result[-1], result[0])

    def test_insufficient_data(self):
        result = indicators.ema([1, 2, 3], 5)
        self.assertEqual(result, [])


class TestRSI(unittest.TestCase):
    def test_rising_prices_high_rsi(self):
        # Steadily rising prices → high RSI
        closes = [100 + i * 2 for i in range(30)]
        candles = _make_candles(closes)
        val = indicators.rsi(candles)
        self.assertIsNotNone(val)
        self.assertGreater(val, 70)

    def test_falling_prices_low_rsi(self):
        closes = [200 - i * 2 for i in range(30)]
        candles = _make_candles(closes)
        val = indicators.rsi(candles)
        self.assertIsNotNone(val)
        self.assertLess(val, 30)

    def test_insufficient_data(self):
        candles = _make_candles([100, 101, 102])
        val = indicators.rsi(candles)
        self.assertIsNone(val)


class TestRSISignal(unittest.TestCase):
    def test_oversold_buy_signal(self):
        closes = [200 - i * 2 for i in range(30)]
        candles = _make_candles(closes)
        sig = indicators.rsi_signal(candles)
        self.assertEqual(sig, +1.0)

    def test_overbought_sell_signal(self):
        closes = [100 + i * 2 for i in range(30)]
        candles = _make_candles(closes)
        sig = indicators.rsi_signal(candles)
        self.assertEqual(sig, -1.0)


class TestEMACrossover(unittest.TestCase):
    def test_bullish_crossover(self):
        # Rising prices → fast EMA above slow
        closes = [100 + i for i in range(60)]
        candles = _make_candles(closes)
        sig = indicators.ema_crossover_signal(candles)
        self.assertEqual(sig, +1.0)

    def test_bearish_crossover(self):
        closes = [200 - i for i in range(60)]
        candles = _make_candles(closes)
        sig = indicators.ema_crossover_signal(candles)
        self.assertEqual(sig, -1.0)


class TestBollingerBands(unittest.TestCase):
    def test_basic_bands(self):
        closes = [100 + (i % 5) for i in range(30)]
        candles = _make_candles(closes)
        bands = indicators.bollinger_bands(candles)
        self.assertIsNotNone(bands)
        upper, middle, lower = bands
        self.assertGreater(upper, middle)
        self.assertGreater(middle, lower)


class TestComputeSignals(unittest.TestCase):
    def test_returns_all_keys(self):
        closes = [100 + i for i in range(60)]
        candles = _make_candles(closes)
        result = indicators.compute_signals(candles)
        for key in ["rsi", "ema", "mom", "bb", "vol", "score", "signals", "total", "action"]:
            self.assertIn(key, result)
        self.assertEqual(result["total"], 5)
        self.assertIn(result["action"], ["BUY", "SELL", "HOLD"])


if __name__ == "__main__":
    unittest.main()
