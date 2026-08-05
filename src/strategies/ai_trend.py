"""AI Trend marketplace strategy.

Uses ML prediction confidence (when available) combined with trend
confirmation.  Falls back to a pure technical trend signal when the ML
model is unavailable, so the strategy always runs.
"""

from __future__ import annotations

from typing import Any

from src.indicators.moving_average import add_moving_averages
from src.strategies.base import BaseStrategy, SignalType


class AITrendStrategy(BaseStrategy):
    """ML-assisted trend strategy with graceful fallback."""

    VERSION = "1.0.0"
    AUTHOR = "NEPSE Quant Engine"
    TAGS = ("ai", "trend", "ml")

    def __init__(self) -> None:
        """Initialise the strategy."""
        super().__init__(
            name="AI Trend",
            description=(
                "Trend strategy boosted by machine-learning prediction "
                "confidence when a model is available."
            ),
        )

    def generate_signal(self, df: Any) -> dict[str, Any]:
        """Generate an AI-assisted trend signal.

        Args:
            df: OHLCV DataFrame.

        Returns:
            Standardized signal payload.
        """
        data = add_moving_averages(df)
        if len(data) < 60:
            return self.build_signal_payload(
                SignalType.HOLD, self._price(data), 0.0
            )

        latest = data.iloc[-1]
        price = self._price(data)
        sma50 = latest.get("SMA_50")
        ema20 = latest.get("EMA_20")

        technical = (
            sma50 is not None
            and ema20 is not None
            and price > sma50
            and ema20 > sma50
        )

        ml_score = self._ml_confidence(df)

        if technical and ml_score is not None and ml_score >= 0.55:
            return self.build_signal_payload(
                SignalType.BUY,
                price,
                stop_loss=round(sma50 * 0.97, 2) if sma50 else None,
                targets=[round(price * 1.05, 2), round(price * 1.10, 2)],
                score=0.8,
                details={
                    "reason": "ai_trend_confirmed",
                    "ml_confidence": round(ml_score, 3),
                },
            )

        if technical:
            return self.build_signal_payload(
                SignalType.BUY,
                price,
                stop_loss=round(sma50 * 0.97, 2) if sma50 else None,
                targets=[round(price * 1.05, 2)],
                score=0.65,
                details={"reason": "technical_trend_only"},
            )

        if sma50 is not None and price < sma50:
            return self.build_signal_payload(
                SignalType.SELL,
                price,
                score=0.55,
                details={"reason": "trend_broken"},
            )

        return self.build_signal_payload(SignalType.HOLD, price, 0.3)

    # ------------------------------------------------------------------

    @staticmethod
    def _ml_confidence(df: Any) -> float | None:
        """Return ML buy-confidence if a prediction engine is available."""
        try:
            from src.ml.prediction_engine import PredictionEngine

            engine = PredictionEngine()
            result = engine.predict(df, model_name="numpy_logistic")
            if result.signal == "BUY":
                return result.probability
            if result.signal == "SELL":
                return 1.0 - result.probability
            return result.probability
        except Exception:
            return None

    @staticmethod
    def _price(df: Any) -> float:
        """Return the latest close price."""
        try:
            return float(df["Close"].iloc[-1])
        except (KeyError, IndexError, TypeError, ValueError):
            return 0.0
