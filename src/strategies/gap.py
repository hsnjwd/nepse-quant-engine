"""Gap trading marketplace strategy.

Buys gap-up opens that hold above the prior close with strong volume;
sells gap-down opens that fail to recover.
"""

from __future__ import annotations

from typing import Any

from src.indicators.volume import add_volume_indicators
from src.strategies.base import BaseStrategy, SignalType


class GapStrategy(BaseStrategy):
    """Gap-open strategy using open vs prior close and volume."""

    VERSION = "1.0.0"
    AUTHOR = "NEPSE Quant Engine"
    TAGS = ("gap", "opening", "momentum")

    def __init__(self) -> None:
        """Initialise the strategy."""
        super().__init__(
            name="Gap",
            description=(
                "Trade gap opens: buy gap-ups holding above prior close "
                "with high relative volume."
            ),
        )

    def generate_signal(self, df: Any) -> dict[str, Any]:
        """Generate a gap signal.

        Args:
            df: OHLCV DataFrame.

        Returns:
            Standardized signal payload.
        """
        data = add_volume_indicators(df)
        if len(data) < 25:
            return self.build_signal_payload(
                SignalType.HOLD, self._price(data), 0.0
            )

        latest = data.iloc[-1]
        prev_close = data["Close"].iloc[-2]
        price = self._price(data)
        gap_pct = (latest["Open"] / prev_close - 1) * 100 if prev_close else 0.0
        rvol = latest.get("RELATIVE_VOLUME", 0) or 0

        if gap_pct > 1.0 and price > latest["Open"] and rvol > 1.3:
            return self.build_signal_payload(
                SignalType.BUY,
                price,
                stop_loss=round(prev_close * 0.98, 2),
                targets=[
                    round(price * (1 + gap_pct / 100), 2),
                    round(price * 1.03, 2),
                ],
                score=0.72,
                details={"reason": "bullish_gap_hold", "gap_pct": round(gap_pct, 2)},
            )

        if gap_pct < -1.0 and price < latest["Open"]:
            return self.build_signal_payload(
                SignalType.SELL,
                price,
                score=0.6,
                details={"reason": "bearish_gap_fail", "gap_pct": round(gap_pct, 2)},
            )

        return self.build_signal_payload(SignalType.HOLD, price, 0.3)

    @staticmethod
    def _price(df: Any) -> float:
        """Return the latest close price."""
        try:
            return float(df["Close"].iloc[-1])
        except (KeyError, IndexError, TypeError, ValueError):
            return 0.0
