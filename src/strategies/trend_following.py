"""Trend Following marketplace strategy.

Goes long when price is above the 50-day SMA with an upward-sloping
20-day EMA, and exits when the trend breaks.
"""

from __future__ import annotations

from typing import Any

from src.indicators.moving_average import add_moving_averages
from src.strategies.base import BaseStrategy, SignalType


class TrendFollowingStrategy(BaseStrategy):
    """EMA/SMA trend-following strategy."""

    VERSION = "1.0.0"
    AUTHOR = "NEPSE Quant Engine"
    TAGS = ("trend_following", "ema", "sma")

    def __init__(self) -> None:
        """Initialise the strategy."""
        super().__init__(
            name="Trend Following",
            description=(
                "Long when price holds above the 50-day SMA with an "
                "upward EMA20; exit on trend break."
            ),
        )

    def generate_signal(self, df: Any) -> dict[str, Any]:
        """Generate a trend-following signal.

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
        prev = data.iloc[-2]
        price = self._price(data)

        sma50 = latest.get("SMA_50")
        ema20 = latest.get("EMA_20")
        prev_sma50 = prev.get("SMA_50")

        if (
            sma50 is not None
            and ema20 is not None
            and prev_sma50 is not None
            and price > sma50
            and ema20 > sma50
            and ema20 >= prev_sma50
        ):
            return self.build_signal_payload(
                SignalType.BUY,
                price,
                stop_loss=round(sma50 * 0.97, 2),
                targets=[round(price * 1.05, 2), round(price * 1.10, 2)],
                score=0.75,
                details={"reason": "uptrend_above_sma50"},
            )

        if sma50 is not None and price < sma50:
            return self.build_signal_payload(
                SignalType.SELL,
                price,
                score=0.6,
                details={"reason": "trend_break_below_sma50"},
            )

        return self.build_signal_payload(SignalType.HOLD, price, 0.3)

    @staticmethod
    def _price(df: Any) -> float:
        """Return the latest close price."""
        try:
            return float(df["Close"].iloc[-1])
        except (KeyError, IndexError, TypeError, ValueError):
            return 0.0
