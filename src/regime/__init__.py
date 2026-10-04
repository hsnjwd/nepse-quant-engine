"""Market Regime Detection Engine for the NEPSE Quant Engine.

Automatically classifies the current market into one of several regimes
so that trading strategies can adapt dynamically.
"""

from __future__ import annotations

from src.regime.detector import MarketRegime, MarketRegimeDetector, REGIME_LABELS

__all__ = [
    "MarketRegime",
    "MarketRegimeDetector",
    "REGIME_LABELS",
]
