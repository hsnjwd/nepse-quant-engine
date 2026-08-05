"""Volume Spike marketplace strategy.

Triggers when volume explodes relative to its 20-day average and price
moves with the spike, indicating institutional participation.
"""

from __future__ import annotations

from typing import Any

from src.indicators.volume import add_volume_indicators
from src.strategies.base import BaseStrategy, SignalType


class VolumeSpikeStrategy(BaseStrategy):
    """Volume-spike breakout strategy."""

    VERSION = "1.0.0"
    AUTHOR = "NEPSE Quant Engine"
    TAGS = ("volume", "spike", "breakout")

    def __init__(self, spike_threshold: float = 2.0) -> None:
        """Initialise the strategy.

        Args:
            spike_threshold: Minimum relative volume to trigger.
        """
        super().__init__(
            name="Volume Spike",
            description=(
                "Detect volume spikes with price confirmation for "
                "breakout entries."
            ),
        )
        self._threshold = float(spike_threshold)

    def generate_signal(self, df: Any) -> dict[str, Any]:
        """Generate a volume-spike signal.

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
        prev = data.iloc[-2]
        price = self._price(data)
        rvol = latest.get("RELATIVE_VOLUME", 0) or 0
        price_up = price > prev["Close"]
        price_down = price < prev["Close"]

        if rvol >= self._threshold and price_up:
            return self.build_signal_payload(
                SignalType.BUY,
                price,
                stop_loss=round(prev["Close"] * 0.97, 2),
                targets=[
                    round(price * 1.03, 2),
                    round(price * 1.06, 2),
                ],
                score=0.78,
                details={"reason": "volume_spike_up", "rvol": round(rvol, 2)},
            )

        if rvol >= self._threshold and price_down:
            return self.build_signal_payload(
                SignalType.SELL,
                price,
                score=0.65,
                details={"reason": "volume_spike_down", "rvol": round(rvol, 2)},
            )

        return self.build_signal_payload(SignalType.HOLD, price, 0.3)

    @staticmethod
    def _price(df: Any) -> float:
        """Return the latest close price."""
        try:
            return float(df["Close"].iloc[-1])
        except (KeyError, IndexError, TypeError, ValueError):
            return 0.0
