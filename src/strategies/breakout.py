"""Breakout-based quantitative trading strategy."""

from __future__ import annotations

from typing import Any

from src.indicators.volatility import add_volatility_indicators
from src.indicators.volume import add_volume_indicators
from src.strategies.base import BaseStrategy


class BreakoutStrategy(BaseStrategy):
    """Trading strategy based on resistance level breakouts and volume expansion."""

    def __init__(
        self,
        lookback_period: int = 20,
        volume_threshold: float = 1.2,
        stop_loss_pct: float = 0.04,
        target_pct: float = 0.12,
    ) -> None:
        """Initialize breakout strategy parameters.

        Args:
            lookback_period: Lookback window for high price breakout identification.
            volume_threshold: Relative volume multiplier for breakout validation.
            stop_loss_pct: Default stop loss decimal percentage below breakout price.
            target_pct: Default target profit decimal percentage above breakout price.
        """
        super().__init__(
            name="BreakoutStrategy",
            description="Detects N-period resistance breakouts backed by volume expansion.",
        )
        self.lookback_period = lookback_period
        self.volume_threshold = volume_threshold
        self.stop_loss_pct = stop_loss_pct
        self.target_pct = target_pct

    def generate_signal(self, df: Any) -> dict[str, Any]:
        """Generate breakout trading signal from market data frame.

        Args:
            df: Price data containing OHLCV columns.

        Returns:
            Signal payload dictionary.
        """
        if df is None or len(df) <= self.lookback_period:
            return self.build_signal_payload(signal="HOLD", price=0.0, score=0.0)

        data = add_volume_indicators(df)
        data = add_volatility_indicators(data)

        recent_df = data.iloc[-self.lookback_period - 1 : -1]
        highest_high = float(recent_df["High"].max())

        latest = data.iloc[-1]
        close = float(latest.get("Close", 0.0))
        relative_volume = float(latest.get("RELATIVE_VOLUME", 1.0)) if latest.get("RELATIVE_VOLUME") is not None else 1.0

        if close > highest_high and relative_volume >= self.volume_threshold:
            signal = "BUY"
            stop_loss = highest_high * (1 - self.stop_loss_pct)
            target1 = close * (1 + self.target_pct)
            score = min(10.0, 7.0 + (relative_volume - self.volume_threshold) * 1.5)
        elif close < highest_high * 0.95:
            signal = "SELL"
            stop_loss = None
            target1 = None
            score = 3.0
        else:
            signal = "HOLD"
            stop_loss = None
            target1 = None
            score = 5.0

        details = {
            "highest_high": round(highest_high, 2),
            "relative_volume": round(relative_volume, 2),
        }

        return self.build_signal_payload(
            signal=signal,
            price=close,
            stop_loss=stop_loss,
            target1=target1,
            score=score,
            details=details,
        )
