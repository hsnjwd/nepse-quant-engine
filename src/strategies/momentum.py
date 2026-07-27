"""Momentum-based quantitative trading strategy."""

from __future__ import annotations

from typing import Any

from src.indicators.momentum import add_momentum_indicators
from src.indicators.moving_average import add_moving_averages
from src.strategies.base import BaseStrategy


class MomentumStrategy(BaseStrategy):
    """Trading strategy based on RSI momentum and Moving Average trend filters."""

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
        """
        if df is None or len(df) == 0:
            return self.build_signal_payload(signal="HOLD", price=0.0, score=0.0)

        data = add_moving_averages(df)
        data = add_momentum_indicators(data)

        latest = data.iloc[-1]
        close = float(latest.get("Close", 0.0))
        rsi = float(latest.get("RSI", 50.0)) if latest.get("RSI") is not None else 50.0
        ma20 = float(latest.get("MA20", close)) if latest.get("MA20") is not None else close
        ma50 = float(latest.get("MA50", close)) if latest.get("MA50") is not None else close

        if rsi >= self.rsi_buy_threshold and close >= ma20 and ma20 >= ma50:
            signal = "BUY"
            stop_loss = close * (1 - self.stop_loss_pct)
            target1 = close * (1 + self.target_pct)
            score = min(10.0, 5.0 + (rsi - self.rsi_buy_threshold) * 0.2)
        elif rsi <= self.rsi_sell_threshold or close < ma50:
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
            "ma20": round(ma20, 2),
            "ma50": round(ma50, 2),
        }

        return self.build_signal_payload(
            signal=signal,
            price=close,
            stop_loss=stop_loss,
            target1=target1,
            score=score,
            details=details,
        )
