"""Paper trading order models.

All order types, statuses, and trade entities as dataclasses.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any


class OrderType(str, Enum):
    MARKET = "MARKET"
    LIMIT = "LIMIT"
    STOP_LOSS = "STOP_LOSS"
    TAKE_PROFIT = "TAKE_PROFIT"


class OrderSide(str, Enum):
    BUY = "BUY"
    SELL = "SELL"


class OrderStatus(str, Enum):
    PENDING = "PENDING"
    PARTIAL = "PARTIAL"
    EXECUTED = "EXECUTED"
    CANCELLED = "CANCELLED"
    REJECTED = "REJECTED"


@dataclass
class Order:
    """A single paper trading order."""

    id: str = ""
    symbol: str = ""
    side: OrderSide = OrderSide.BUY
    order_type: OrderType = OrderType.MARKET
    status: OrderStatus = OrderStatus.PENDING
    quantity: int = 0
    filled_quantity: int = 0
    price: float = 0.0  # for MARKET: expected fill price; for LIMIT: limit price
    stop_price: float = 0.0  # for STOP_LOSS / TAKE_PROFIT
    total: float = 0.0
    commission: float = 0.0
    pnl: float = 0.0
    notes: str = ""
    created_at: datetime = field(default_factory=datetime.now)
    filled_at: datetime | None = None

    @property
    def is_open(self) -> bool:
        return self.status in (OrderStatus.PENDING, OrderStatus.PARTIAL)

    @property
    def remaining_quantity(self) -> int:
        return self.quantity - self.filled_quantity


@dataclass
class OpenPosition:
    """An open (unrealized) position."""

    symbol: str = ""
    quantity: int = 0
    average_price: float = 0.0
    invested: float = 0.0
    current_price: float = 0.0
    current_value: float = 0.0
    unrealized_pnl: float = 0.0
    unrealized_pnl_pct: float = 0.0
    realized_pnl: float = 0.0
    opened_at: datetime = field(default_factory=datetime.now)

    @property
    def is_profitable(self) -> bool:
        return self.unrealized_pnl > 0


@dataclass
class PaperTrade:
    """A completed trade (buy + sell pair or individual transaction)."""

    id: str = ""
    symbol: str = ""
    side: OrderSide = OrderSide.BUY
    quantity: int = 0
    entry_price: float = 0.0
    exit_price: float = 0.0
    pnl: float = 0.0
    pnl_pct: float = 0.0
    commission: float = 0.0
    entry_date: datetime = field(default_factory=datetime.now)
    exit_date: datetime | None = None
    order_id: str = ""
    notes: str = ""


@dataclass
class PaperTradingSummary:
    """Summary of paper trading account."""

    balance: float = 0.0
    invested: float = 0.0
    current_value: float = 0.0
    total_pnl: float = 0.0
    total_pnl_pct: float = 0.0
    total_trades: int = 0
    win_count: int = 0
    loss_count: int = 0
    win_rate: float = 0.0
    open_positions_count: int = 0
    open_positions: list[OpenPosition] = field(default_factory=list)
    recent_trades: list[PaperTrade] = field(default_factory=list)
    pending_orders: list[Order] = field(default_factory=list)
    timestamp: datetime = field(default_factory=datetime.now)
