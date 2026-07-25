"""Trade execution simulation for historical backtests."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum
from typing import Any

DEFAULT_COMMISSION = 0.0
DEFAULT_SLIPPAGE = 0.0
MAX_HOLDING_DAYS = 10
BUY_SIGNAL = "BUY"
SELL_SIGNAL = "SELL"


class ExitReason(str, Enum):
    """Explicit exit reason categories for executed trades."""

    TARGET = "TARGET"
    STOP_LOSS = "STOP_LOSS"
    SELL_SIGNAL = "SELL_SIGNAL"
    END_OF_DATA = "END_OF_DATA"


class TradeResult(str, Enum):
    """Legacy trade outcome labels for backward compatibility."""

    WIN = "WIN"
    LOSS = "LOSS"
    SELL = "SELL"
    TIME_EXIT = "TIME_EXIT"


EXIT_TARGET = ExitReason.TARGET.value
EXIT_STOP_LOSS = ExitReason.STOP_LOSS.value
EXIT_SELL_SIGNAL = ExitReason.SELL_SIGNAL.value
EXIT_END_OF_DATA = ExitReason.END_OF_DATA.value

_RESULT_MAP: dict[str, str] = {
    ExitReason.TARGET.value: TradeResult.WIN.value,
    ExitReason.STOP_LOSS.value: TradeResult.LOSS.value,
    ExitReason.SELL_SIGNAL.value: TradeResult.SELL.value,
    ExitReason.END_OF_DATA.value: TradeResult.TIME_EXIT.value,
}


@dataclass(frozen=True)
class ExecutionConfig:
    """Configuration parameters for trade execution simulation.

    Attributes:
        commission: Per-side commission rate as a decimal fraction (e.g. 0.001 for 0.1%).
        slippage: Per-side adverse slippage rate as a decimal fraction (e.g. 0.01 for 1%).
        max_holding_days: Maximum duration in candles/days before timing out.
    """

    commission: float = DEFAULT_COMMISSION
    slippage: float = DEFAULT_SLIPPAGE
    max_holding_days: int = MAX_HOLDING_DAYS

    def __post_init__(self) -> None:
        if self.commission < 0 or self.slippage < 0:
            raise ValueError("Commission and slippage must be non-negative.")
        if self.max_holding_days <= 0:
            raise ValueError("max_holding_days must be positive.")


@dataclass
class TradeRecord:
    """Normalized trade execution output record.

    Attributes:
        entry_price: Executed long entry price (including slippage).
        exit_price: Executed exit price (including adverse slippage).
        shares: Number of shares traded.
        gross_profit: Price difference times shares.
        commission: Total round-trip commission paid.
        net_profit: Gross profit minus commission paid.
        return_pct: Percentage net return relative to entry value.
        holding_days: Total holding duration in candles/days.
        exit_reason: Reason for trade exit.
        result: Legacy trade result classification.
    """

    entry_price: float
    exit_price: float
    shares: int
    gross_profit: float
    commission: float
    net_profit: float
    return_pct: float
    holding_days: int
    exit_reason: str
    result: str

    def to_dict(self) -> dict[str, Any]:
        """Convert the trade record into a rounded dictionary payload."""
        return {
            "entry_price": round(self.entry_price, 2),
            "exit_price": round(self.exit_price, 2),
            "shares": self.shares,
            "gross_profit": round(self.gross_profit, 2),
            "commission": round(self.commission, 2),
            "net_profit": round(self.net_profit, 2),
            "return_pct": round(self.return_pct, 2),
            "holding_days": self.holding_days,
            "exit_reason": self.exit_reason,
            "result": self.result,
        }


def _extract_candle_price(candle: Any, col_names: tuple[str, ...], default_name: str) -> float:
    """Safely extract price from a candle row with case-insensitive fallback."""
    for col in col_names:
        if col in candle.index:
            return float(candle[col])

    if hasattr(candle, "index"):
        for actual_col in candle.index:
            if str(actual_col).lower() in (c.lower() for c in col_names):
                return float(candle[actual_col])

    raise KeyError(f"Candle is missing required price column matching '{default_name}'.")


def _has_sell_signal(candle: Any) -> bool:
    """Return whether a historical candle contains a sell signal."""
    for column in ("signal", "Signal", "SIGNAL"):
        if column in candle.index:
            if str(candle[column]).upper() == SELL_SIGNAL:
                return True

    if hasattr(candle, "index"):
        for col in candle.index:
            if str(col).lower() == "signal":
                if str(candle[col]).upper() == SELL_SIGNAL:
                    return True

    return False


class TradeExecutionEngine:
    """Professional trade execution simulation engine for quantitative backtests."""

    def __init__(self, config: ExecutionConfig | None = None) -> None:
        self.config = config or ExecutionConfig()

    def build_trade_record(
        self,
        entry_price: float,
        exit_price: float,
        shares: int,
        holding_days: int,
        exit_reason: str,
    ) -> TradeRecord:
        """Build a normalized TradeRecord instance from executed prices."""
        gross_profit = (exit_price - entry_price) * shares
        commission_paid = (
            (entry_price + exit_price)
            * shares
            * self.config.commission
        )
        entry_value = entry_price * shares
        net_profit = gross_profit - commission_paid
        return_pct = (net_profit / entry_value * 100.0) if entry_value > 0 else 0.0
        result = _RESULT_MAP.get(exit_reason, TradeResult.TIME_EXIT.value)

        return TradeRecord(
            entry_price=entry_price,
            exit_price=exit_price,
            shares=shares,
            gross_profit=gross_profit,
            commission=commission_paid,
            net_profit=net_profit,
            return_pct=return_pct,
            holding_days=holding_days,
            exit_reason=exit_reason,
            result=result,
        )

    def execute(
        self,
        df: Any,
        start_index: int,
        signal: Mapping[str, Any],
    ) -> TradeRecord | None:
        """Simulate execution of a long trade from a buy signal."""
        if not isinstance(signal, Mapping) or signal.get("signal") != BUY_SIGNAL:
            return None

        if start_index < 0 or start_index >= len(df):
            raise ValueError(f"start_index {start_index} out of bounds for DataFrame of length {len(df)}.")

        slippage = self.config.slippage
        entry_price = float(signal["price"]) * (1 + slippage)
        stop_loss = float(signal["stop_loss"])
        target = float(signal["target1"])
        shares = int(signal.get("shares", 1))

        final_index = min(start_index + self.config.max_holding_days, len(df) - 1)

        for index in range(start_index + 1, final_index + 1):
            candle = df.iloc[index]
            holding_days = index - start_index

            low_price = _extract_candle_price(candle, ("Low", "low", "LOW"), "Low")
            high_price = _extract_candle_price(candle, ("High", "high", "HIGH"), "High")
            close_price = _extract_candle_price(candle, ("Close", "close", "CLOSE"), "Close")

            if low_price <= stop_loss:
                return self.build_trade_record(
                    entry_price=entry_price,
                    exit_price=stop_loss * (1 - slippage),
                    shares=1,
                    holding_days=holding_days,
                    exit_reason=ExitReason.STOP_LOSS.value,
                )
            if high_price >= target:
                return self.build_trade_record(
                    entry_price=entry_price,
                    exit_price=target * (1 - slippage),
                    shares=1,
                    holding_days=holding_days,
                    exit_reason=ExitReason.TARGET.value,
                )
            if _has_sell_signal(candle):
                return self.build_trade_record(
                    entry_price=entry_price,
                    exit_price=close_price * (1 - slippage),
                    shares=1,
                    holding_days=holding_days,
                    exit_reason=ExitReason.SELL_SIGNAL.value,
                )

        final_candle = df.iloc[final_index]
        final_close = _extract_candle_price(final_candle, ("Close", "close", "CLOSE"), "Close")
        holding_days = max(final_index - start_index, 0)

        return self.build_trade_record(
            entry_price=entry_price,
            exit_price=final_close * (1 - slippage),
            shares=1,
            holding_days=holding_days,
            exit_reason=ExitReason.END_OF_DATA.value,
        )


def simulate_trade(
    df: Any,
    start_index: int,
    signal: Mapping[str, Any],
    commission: float = DEFAULT_COMMISSION,
    slippage: float = DEFAULT_SLIPPAGE,
    max_holding_days: int = MAX_HOLDING_DAYS,
) -> dict[str, Any] | None:
    """Simulate execution of a long trade from a buy signal.

    Commission and slippage are decimal rates. For example, ``0.001`` applies
    a 0.1% charge or adverse price adjustment on each side of the trade.

    Args:
        df: Historical market data with ``High``, ``Low``, and ``Close`` fields.
        start_index: Candle index at which the signal was generated.
        signal: Signal payload containing ``signal``, ``price``, ``stop_loss``,
            and ``target1`` values. Optional ``shares`` key specifies position size.
        commission: Per-side commission rate as a decimal fraction.
        slippage: Per-side adverse slippage rate as a decimal fraction.
        max_holding_days: Maximum duration in candles/days before timing out.

    Returns:
        A complete trade record dictionary, or ``None`` when the signal is not a buy.

    Raises:
        ValueError: If commission or slippage is negative, or start_index is out of bounds.
    """
    config = ExecutionConfig(
        commission=commission,
        slippage=slippage,
        max_holding_days=max_holding_days,
    )
    engine = TradeExecutionEngine(config=config)
    record = engine.execute(df, start_index, signal)
    return record.to_dict() if record is not None else None
