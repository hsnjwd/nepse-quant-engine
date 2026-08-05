"""Opening Range Breakout marketplace strategy.

Captures the high/low of the opening session and triggers when the
price breaks the range with volume confirmation.
"""

from __future__ import annotations

from typing import Any

from src.indicators.volume import add_volume_indicators
from src.strategies.base import BaseStrategy, SignalType


class OpeningRangeBreakoutStrategy(BaseStrategy):
    """Opening-range breakout strategy."""

    VERSION = "1.0.0"
    AUTHOR = "NEPSE Quant Engine"
    TAGS = ("opening_range", "breakout", "volume")

    def __init__(self, range_bars: int = 5) -> None:
        """Initialise the strategy.

        Args:
            range_bars: Number of bars defining the opening range.
        """
        super().__init__(
            name="Opening Range",
            description=(
                "Breakout of the opening N-bar high/low with volume "
                "confirmation."
            ),
        )
        self._range_bars = max(2, int(range_bars))

    def generate_signal(self, df: Any) -> dict[str, Any]:
        """Generate an opening-range breakout signal.

        Args:
            df: OHLCV DataFrame.

        Returns:
            Standardized signal payload.
        """
        data = add_volume_indicators(df)
        if len(data) < self._range_bars + 5:
            return self.build_signal_payload(
                SignalType.HOLD, self._price(data), 0.0
            )

        window = data.iloc[: self._range_bars]
        range_high = float(window["High"].max())
        range_low = float(window["Low"].min())
        latest = data.iloc[-1]
        price = self._price(data)
        rvol = latest.get("RELATIVE_VOLUME", 0) or 0

        if price > range_high and rvol > 1.2:
            return self.build_signal_payload(
                SignalType.BUY,
                price,
                stop_loss=round(range_low * 0.98, 2),
                targets=[
                    round(price * 1.02, 2),
                    round(price * 1.05, 2),
                ],
                score=0.7,
                details={
                    "reason": "opening_range_breakout_up",
                    "range_high": round(range_high, 2),
                    "range_low": round(range_low, 2),
                },
            )

        if price < range_low and rvol > 1.2:
            return self.build_signal_payload(
                SignalType.SELL,
                price,
                score=0.6,
                details={"reason": "opening_range_breakdown"},
            )

        return self.build_signal_payload(SignalType.HOLD, price, 0.3)

    @staticmethod
    def _price(df: Any) -> float:
        """Return the latest close price."""
        try:
            return float(df["Close"].iloc[-1])
        except (KeyError, IndexError, TypeError, ValueError):
            return 0.0
