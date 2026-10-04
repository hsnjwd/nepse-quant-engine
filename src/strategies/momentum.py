"""Momentum-based quantitative trading strategy.

Strategy logic
--------------
- **BUY** when RSI is above the buy threshold **and** price is above the
  20-period SMA **and** the 20-period SMA is above the 50-period SMA
  (moving-average alignment / trend filter).
- **SELL** when RSI drops below the sell threshold **or** price closes
  below the 50-period SMA.
- **HOLD** otherwise.

.. note::
    Indicator column names follow the engine convention produced by
    :func:`src.indicators.moving_average.add_moving_averages`
    (``SMA_20`` / ``SMA_50``).  The strategy raises a clear error when
    the required indicator columns are absent rather than silently
    defaulting, which previously disabled the moving-average filter.
"""

from __future__ import annotations

import logging
from typing import Any

import pandas as pd

from src.indicators.momentum import add_momentum_indicators
from src.indicators.moving_average import add_moving_averages
from src.strategies.base import BaseStrategy

logger = logging.getLogger(__name__)


class MomentumStrategy(BaseStrategy):
    """Trading strategy based on RSI momentum and Moving Average trend filters."""

    #: Indicator columns required for signal generation (engine naming).
    REQUIRED_INDICATOR_COLUMNS: tuple[str, ...] = ("SMA_20", "SMA_50")

    def __init__(
        self,
        rsi_period: int = 14,
        rsi_buy_threshold: float = 55.0,
        rsi_sell_threshold: float = 40.0,
        stop_loss_pct: float = 0.05,
        target_pct: float = 0.10,
    ) -> None:
        """Initialize momentum strategy with configurable thresholds.

        Args:
            rsi_period: RSI calculation period.
            rsi_buy_threshold: RSI level above which a buy signal triggers.
            rsi_sell_threshold: RSI level below which a sell signal triggers.
            stop_loss_pct: Default stop loss decimal percentage below entry.
            target_pct: Default target profit decimal percentage above entry.
        """
        super().__init__(
            name="MomentumStrategy",
            description="Evaluates RSI momentum and Moving Average alignment.",
        )
        self.rsi_period = rsi_period
        self.rsi_buy_threshold = rsi_buy_threshold
        self.rsi_sell_threshold = rsi_sell_threshold
        self.stop_loss_pct = stop_loss_pct
        self.target_pct = target_pct

    def generate_signal(self, df: Any) -> dict[str, Any]:
        """Generate momentum trading signal from market data frame.

        Args:
            df: Price data containing OHLCV columns.

        Returns:
            Signal payload dictionary.

        Raises:
            ValueError: If the required indicator columns (``SMA_20``,
                ``SMA_50``) are absent after indicator computation.
        """
        if df is None or len(df) == 0:
            return self.build_signal_payload(signal="HOLD", price=0.0, score=0.0)

        data = add_moving_averages(df)
        data = add_momentum_indicators(data)

        missing = [c for c in self.REQUIRED_INDICATOR_COLUMNS if c not in data.columns]
        if missing:
            raise ValueError(
                f"{self.name}: required indicator columns missing: {missing}. "
                "Ensure add_moving_averages() produced SMA_20/SMA_50."
            )

        latest = data.iloc[-1]
        close = float(latest.get("Close", 0.0))
        rsi_raw = latest.get("RSI")
        rsi = float(rsi_raw) if rsi_raw is not None and not pd.isna(rsi_raw) else 50.0

        ma20_raw = latest.get("SMA_20")
        ma50_raw = latest.get("SMA_50")

        # Moving-average alignment (trend filter).  Do NOT silently fall
        # back when values are missing or NaN (insufficient history) —
        # log and treat the filter as unsatisfied instead.
        if ma20_raw is None or ma50_raw is None or pd.isna(ma20_raw) or pd.isna(ma50_raw):
            logger.warning(
                "%s: SMA_20/SMA_50 unavailable for latest bar; MA filter unsatisfied.",
                self.name,
            )
            ma_aligned = False
            ma20 = None
            ma50 = None
        else:
            ma20 = float(ma20_raw)
            ma50 = float(ma50_raw)
            ma_aligned = close >= ma20 and ma20 >= ma50

        if rsi >= self.rsi_buy_threshold and ma_aligned:
            signal = "BUY"
            stop_loss = close * (1 - self.stop_loss_pct)
            target1 = close * (1 + self.target_pct)
            score = min(10.0, 5.0 + (rsi - self.rsi_buy_threshold) * 0.2)
        elif rsi <= self.rsi_sell_threshold or (ma50 is not None and close < ma50):
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
            "rsi": round(rsi, 2),
            "ma20": round(ma20, 2) if ma20 is not None else None,
            "ma50": round(ma50, 2) if ma50 is not None else None,
        }

        return self.build_signal_payload(
            signal=signal,
            price=close,
            stop_loss=stop_loss,
            target1=target1,
            score=score,
            details=details,
        )
