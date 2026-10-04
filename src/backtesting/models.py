"""Core data models for the institutional backtesting engine.

Defines the enums and dataclasses shared across the ``src.backtesting``
package: bars, orders, fills, positions, trades, and configuration.
All entities are plain dataclasses so they serialise cleanly for
reports, exports, and the Streamlit UI.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any


class OrderSide(str, Enum):
    """Side of an order (buy to open, sell to close, etc.)."""

    BUY = "BUY"
    SELL = "SELL"


class OrderType(str, Enum):
    """Supported order types."""

    MARKET = "MARKET"
    LIMIT = "LIMIT"
    STOP = "STOP"
    STOP_LIMIT = "STOP_LIMIT"
    TRAILING_STOP = "TRAILING_STOP"
    BRACKET = "BRACKET"
    OCO = "OCO"
    IOC = "IOC"
    FOK = "FOK"


class OrderStatus(str, Enum):
    """Order lifecycle states.

    The canonical lifecycle is::

        CREATED → SUBMITTED → ACCEPTED → PARTIALLY_FILLED → FILLED
                            ↘ CANCELLED
                            ↘ REJECTED
                            ↘ EXPIRED
    """

    CREATED = "CREATED"
    SUBMITTED = "SUBMITTED"
    ACCEPTED = "ACCEPTED"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    FILLED = "FILLED"
    CANCELLED = "CANCELLED"
    REJECTED = "REJECTED"
    EXPIRED = "EXPIRED"


class TimeInForce(str, Enum):
    """Time-in-force policies for orders."""

    DAY = "DAY"
    GTC = "GTC"  # good till cancelled
    GTD = "GTD"  # good till date
    IOC = "IOC"  # immediate or cancel
    FOK = "FOK"  # fill or kill


class PositionSide(str, Enum):
    """Direction of an open position."""

    LONG = "LONG"
    SHORT = "SHORT"


class ExitReason(str, Enum):
    """Reason a trade was closed."""

    TARGET = "TARGET"
    STOP_LOSS = "STOP_LOSS"
    TRAILING_STOP = "TRAILING_STOP"
    SIGNAL = "SIGNAL"
    BRACKET = "BRACKET"
    OCO = "OCO"
    EXPIRY = "EXPIRY"
    DELISTING = "DELISTING"
    END_OF_DATA = "END_OF_DATA"
    MANUAL = "MANUAL"


class EventType(str, Enum):
    """Event types emitted by the backtest engine."""

    BAR = "BAR"
    ORDER_CREATED = "ORDER_CREATED"
    ORDER_SUBMITTED = "ORDER_SUBMITTED"
    ORDER_ACCEPTED = "ORDER_ACCEPTED"
    ORDER_REJECTED = "ORDER_REJECTED"
    ORDER_CANCELLED = "ORDER_CANCELLED"
    ORDER_EXPIRED = "ORDER_EXPIRED"
    FILL = "FILL"
    POSITION_OPENED = "POSITION_OPENED"
    POSITION_CLOSED = "POSITION_CLOSED"
    TRADE_CLOSED = "TRADE_CLOSED"
    PORTFOLIO_UPDATED = "PORTFOLIO_UPDATED"
    EQUITY_UPDATED = "EQUITY_UPDATED"
    CORPORATE_ACTION = "CORPORATE_ACTION"
    BACKTEST_COMPLETE = "BACKTEST_COMPLETE"
    SCENARIO_APPLIED = "SCENARIO_APPLIED"


@dataclass(frozen=True)
class Bar:
    """A single OHLCV bar for one symbol.

    Attributes:
        symbol: Ticker symbol.
        timestamp: Bar timestamp (typically a trading date).
        open: Opening price.
        high: Highest traded price.
        low: Lowest traded price.
        close: Closing price.
        volume: Traded volume.
        index: Optional sequential bar index (0-based).
    """

    symbol: str
    timestamp: Any
    open: float
    high: float
    low: float
    close: float
    volume: float = 0.0
    index: int = 0


@dataclass
class Order:
    """A single order with full lifecycle tracking.

    Attributes:
        order_id: Unique order identifier.
        symbol: Ticker symbol.
        side: BUY or SELL.
        order_type: MARKET / LIMIT / STOP / ...
        quantity: Requested quantity.
        status: Current lifecycle status.
        time_in_force: TIF policy (GTC default).
        limit_price: Trigger-free execution limit (for LIMIT / STOP_LIMIT).
        stop_price: Stop trigger price (for STOP / STOP_LIMIT / TRAILING_STOP).
        trailing_offset: Absolute trail distance for TRAILING_STOP.
        trailing_pct: Fractional trail distance for TRAILING_STOP.
        filled_quantity: Quantity executed so far.
        average_fill_price: Volume-weighted average fill price.
        filled_value: Total cash value of fills (signed by side).
        commission: Total commission accrued.
        slippage: Total slippage cost accrued.
        created_at: Order creation timestamp.
        expires_at: Optional expiry (GTD).
        oco_group: Shared group id for OCO siblings.
        bracket_parent: Order id of the parent entry for bracket orders.
        note: Optional human-readable note.
        metadata: Extra payload dict.
    """

    order_id: str
    symbol: str
    side: OrderSide = OrderSide.BUY
    order_type: OrderType = OrderType.MARKET
    quantity: int = 0
    status: OrderStatus = OrderStatus.CREATED
    time_in_force: TimeInForce = TimeInForce.GTC
    limit_price: float | None = None
    stop_price: float | None = None
    trailing_offset: float | None = None
    trailing_pct: float | None = None
    filled_quantity: int = 0
    average_fill_price: float = 0.0
    filled_value: float = 0.0
    commission: float = 0.0
    slippage: float = 0.0
    created_at: datetime = field(default_factory=datetime.now)
    expires_at: datetime | None = None
    oco_group: str | None = None
    bracket_parent: str | None = None
    note: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    # ── Derived properties ──────────────────────────────────────

    @property
    def remaining_quantity(self) -> int:
        """Return quantity still unfilled."""
        return max(self.quantity - self.filled_quantity, 0)

    @property
    def is_open(self) -> bool:
        """True while the order can still be filled."""
        return self.status in (
            OrderStatus.CREATED,
            OrderStatus.SUBMITTED,
            OrderStatus.ACCEPTED,
            OrderStatus.PARTIALLY_FILLED,
        )

    @property
    def is_filled(self) -> bool:
        """True when fully filled."""
        return self.status == OrderStatus.FILLED

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serialisable dictionary."""
        return {
            "order_id": self.order_id,
            "symbol": self.symbol,
            "side": self.side.value,
            "order_type": self.order_type.value,
            "quantity": self.quantity,
            "filled_quantity": self.filled_quantity,
            "remaining_quantity": self.remaining_quantity,
            "status": self.status.value,
            "time_in_force": self.time_in_force.value,
            "limit_price": self.limit_price,
            "stop_price": self.stop_price,
            "trailing_offset": self.trailing_offset,
            "trailing_pct": self.trailing_pct,
            "average_fill_price": round(self.average_fill_price, 4),
            "filled_value": round(self.filled_value, 2),
            "commission": round(self.commission, 2),
            "slippage": round(self.slippage, 2),
            "created_at": self.created_at.isoformat(),
            "expires_at": self.expires_at.isoformat() if self.expires_at else None,
            "oco_group": self.oco_group,
            "bracket_parent": self.bracket_parent,
            "note": self.note,
        }


@dataclass
class Fill:
    """An execution fill against an order.

    Attributes:
        fill_id: Unique fill identifier.
        order_id: Order this fill belongs to.
        symbol: Ticker symbol.
        side: BUY or SELL.
        quantity: Quantity executed.
        price: Execution price (after slippage).
        commission: Commission charged for this fill.
        slippage_cost: Slippage cost for this fill.
        timestamp: Fill timestamp.

    Note:
        ``is_buy`` is a read-only convenience property derived from
        ``side`` (``side == OrderSide.BUY``); it is not a constructor
        argument and never needs to be passed in.
    """

    fill_id: str
    order_id: str
    symbol: str
    side: OrderSide
    quantity: int
    price: float
    commission: float = 0.0
    slippage_cost: float = 0.0
    timestamp: Any = None

    @property
    def is_buy(self) -> bool:
        """True when the fill is a purchase (BUY side)."""
        return self.side == OrderSide.BUY

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serialisable dictionary."""
        return {
            "fill_id": self.fill_id,
            "order_id": self.order_id,
            "symbol": self.symbol,
            "side": self.side.value,
            "is_buy": self.is_buy,
            "quantity": self.quantity,
            "price": round(self.price, 4),
            "commission": round(self.commission, 2),
            "slippage_cost": round(self.slippage_cost, 2),
            "timestamp": str(self.timestamp),
        }


@dataclass
class Position:
    """An open position in one symbol.

    Attributes:
        symbol: Ticker symbol.
        side: LONG or SHORT.
        quantity: Net quantity held.
        average_price: Volume-weighted average entry price.
        realized_pnl: Realised P&L from closed portions (cash basis).
        opened_at: Timestamp when the position was opened.
    """

    symbol: str
    side: PositionSide = PositionSide.LONG
    quantity: int = 0
    average_price: float = 0.0
    realized_pnl: float = 0.0
    opened_at: Any = None

    def market_value(self, price: float) -> float:
        """Market value of the position at *price*."""
        return self.quantity * price

    def unrealized_pnl(self, price: float) -> float:
        """Unrealised P&L at *price*."""
        if self.side == PositionSide.LONG:
            return (price - self.average_price) * self.quantity
        return (self.average_price - price) * self.quantity

    def to_dict(self, price: float | None = None) -> dict[str, Any]:
        """Return a JSON-serialisable dictionary (optionally marked)."""
        result = {
            "symbol": self.symbol,
            "side": self.side.value,
            "quantity": self.quantity,
            "average_price": round(self.average_price, 4),
            "realized_pnl": round(self.realized_pnl, 2),
            "opened_at": str(self.opened_at),
        }
        if price is not None:
            result["market_value"] = round(self.market_value(price), 2)
            result["unrealized_pnl"] = round(self.unrealized_pnl(price), 2)
        return result


@dataclass
class Trade:
    """A completed round-trip trade (position opened and closed).

    Attributes:
        trade_id: Unique trade identifier.
        symbol: Ticker symbol.
        side: Original entry side (LONG or SHORT).
        quantity: Quantity traded.
        entry_price: Average entry price.
        exit_price: Average exit price.
        entry_time: Entry timestamp.
        exit_time: Exit timestamp.
        gross_pnl: Gross P&L before costs.
        net_pnl: Net P&L after commissions and slippage.
        commission: Total commissions paid.
        slippage: Total slippage cost.
        return_pct: Net return as a percentage of entry value.
        holding_bars: Number of bars held.
        exit_reason: Why the trade was closed.
    """

    trade_id: str
    symbol: str
    side: PositionSide
    quantity: int
    entry_price: float
    exit_price: float
    entry_time: Any = None
    exit_time: Any = None
    gross_pnl: float = 0.0
    net_pnl: float = 0.0
    commission: float = 0.0
    slippage: float = 0.0
    return_pct: float = 0.0
    holding_bars: int = 0
    exit_reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serialisable dictionary."""
        return {
            "trade_id": self.trade_id,
            "symbol": self.symbol,
            "side": self.side.value,
            "quantity": self.quantity,
            "entry_price": round(self.entry_price, 4),
            "exit_price": round(self.exit_price, 4),
            "entry_time": str(self.entry_time),
            "exit_time": str(self.exit_time),
            "gross_pnl": round(self.gross_pnl, 2),
            "net_pnl": round(self.net_pnl, 2),
            "commission": round(self.commission, 2),
            "slippage": round(self.slippage, 2),
            "return_pct": round(self.return_pct, 4),
            "holding_bars": self.holding_bars,
            "exit_reason": self.exit_reason,
        }


@dataclass
class BacktestConfig:
    """Configuration for a backtest run.

    Attributes:
        initial_cash: Starting cash balance.
        commission_model: Commission model name (or model instance).
        slippage_model: Slippage model name (or model instance).
        lot_size: Order quantities are rounded to multiples of this.
        allow_short: Whether short selling is enabled.
        max_leverage: Maximum portfolio leverage (1.0 = no margin).
        benchmark_symbol: Optional symbol used as benchmark (e.g. "NEPSE").
        risk_free_rate: Annualised risk-free rate for Sharpe/Sortino.
        trades_per_year: Periods per year used for annualisation.
        order_fee: Flat per-order fee (additionally applied).
        tax_rate: Flat tax rate applied to executed value.
        stamp_duty: Stamp duty applied to executed value.
        cash_interest: Interest earned on idle cash per period (fraction).
    """

    initial_cash: float = 1_000_000.0
    commission_model: str = "percentage"  # flat|percentage|tiered|broker
    slippage_model: str = "fixed"  # fixed|percentage|volume|volatility|spread|random
    lot_size: int = 1
    allow_short: bool = True
    max_leverage: float = 1.0
    benchmark_symbol: str | None = None
    risk_free_rate: float = 0.0
    trades_per_year: int = 252
    order_fee: float = 0.0
    tax_rate: float = 0.0
    stamp_duty: float = 0.0
    cash_interest: float = 0.0
