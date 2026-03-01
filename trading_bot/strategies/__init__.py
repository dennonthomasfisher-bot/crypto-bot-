"""strategies package – exposes the four signal-generating components."""
from .dca import DCAStrategy
from .momentum import momentum_signal
from .rsi import rsi_signal
from .sentiment import SentimentAnalyzer

__all__ = ["rsi_signal", "momentum_signal", "DCAStrategy", "SentimentAnalyzer"]
