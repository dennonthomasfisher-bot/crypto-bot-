"""strategies package – exposes the three signal-generating components."""
from .momentum import momentum_signal
from .rsi import rsi_signal
from .sentiment import SentimentAnalyzer

__all__ = ["rsi_signal", "momentum_signal", "SentimentAnalyzer"]
