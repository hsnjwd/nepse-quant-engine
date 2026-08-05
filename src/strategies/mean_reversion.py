"""Mean Reversion marketplace strategy.

Buys when price pulls back to the lower Bollinger Band with an RSI
recovery, and holds until price returns toward the middle band.
"""

from __future__ import annotations

from typing import Any

import pandas as pd

from src.indicators.momentum import add_momentum_indicators
from src.indicators.volatility import add_volatility_indicators
from src.strategies.base import BaseStrategy, SignalType


class MeanReversionStrategy(BaseStrategy):
    """Mean-reversion strategy using Bollinger Bands and RSI."""

    VERSION = "1.0.0"
    AUTHOR = "NEPSE Quant Engine"
    TAGS = ("mean_reversion", "bollinger", "rsi")

    def __init__(self) -> None:
        """Initialise the strategy."""
        super().__init__(
            name="Mean Reversion",
            description=(
                "Buy pullbacks to the lower Bollinger Band with RSI "
                "recovery; sell toward the middle band."
            ),
        )

    def generate_signal(self, df: Any) -> dict[str, Any]:
        """Generate a mean-reversion signal.

        Args:
            df: OHLCV DataFrame.

        Returns:
            Standardized signal payload.
        """
        data = add_volatility_indicators(df)
        data = add_momentum_indicators(data)

        if len(data) < 30:
            return self.build_signal_payload(
                SignalType.HOLD, self._price(data), 0.0
            )

        latest = data.iloc[-1]
        prev = data.iloc[-2]

        price = self._price(data)
        lower = latest.get("BB_LOWER")
        mid = latest.get("BB_MIDDLE")
        rsi = latest.get("RSI")
        prev_rsi = prev.get("RSI")

        # Buy when price touches/below lower band and RSI is recovering
        # (RSI < 40 but rising vs previous bar).
        if (
            lower is not None
            and price <= lower
            and rsi is not None
            and prev_rsi is not None
            and rsi < 45
            and rsi > prev_rsi
        ):
            return self.build_signal_payload(
                SignalType.BUY,
                price,
                stop_loss=round(lower * 0.97, 2),
                targets=[round(mid * 0.98, 2), round(mid, 2)]
                if mid is not None
                else None,
                score=0.7,
                details={"reason": "price_at_lower_band_rsi_recovery"},
            )

        # Sell when price returns to the middle band.
        if mid is not None and price >= mid:
            return self.build_signal_payload(
                SignalType.SELL,
                price,
                score=0.5,
                details={"reason": "mean_reverted_to_middle_band"},
            )

        return self.build_signal_payload(
            SignalType.HOLD, price, 0.3
        )

    @staticmethod
    def _price(df: Any) -> float:
        """Return the latest close price."""
        try:
            return float(df["Close"].iloc[-1])
        except (KeyError, IndexError, TypeError, ValueError):
            return 0.0
