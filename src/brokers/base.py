"""Abstract broker interface for the NEPSE Quant Engine.

All brokers (paper, mock, and future live adapters) implement this
interface so that higher-level systems can trade against any backend
without coupling.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any


class OrderSide(str, Enum):
    """Order direction."""

    BUY = "BUY"
    SELL = "SELL"


class OrderType(str, Enum):
    """Order kind."""

    MARKET = "MARKET"
    LIMIT = "LIMIT"
    STOP = "STOP"
    STOP_LIMIT = "STOP_LIMIT"


class OrderStatus(str, Enum):
    """Order lifecycle states."""

    PENDING = "PENDING"
    EXECUTED = "EXECUTED"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    CANCELLED = "CANCELLED"
    REJECTED = "REJECTED"


@dataclass
class Order:
    """A broker order.

    Attributes:
        symbol: Stock symbol.
        side: BUY or SELL.
        quantity: Number of shares.
        order_type: MARKET / LIMIT / STOP / STOP_LIMIT.
        limit_price: Optional limit price.
        stop_price: Optional stop trigger price.
        order_id: Unique order identifier.
        status: Current order status.
        filled_price: Average fill price (when executed).
        created_at: ISO timestamp of creation.
        client_order_id: Optional client reference.
    """

    symbol: str
    side: OrderSide
    quantity: float
    order_type: OrderType = OrderType.MARKET
    limit_price: float | None = None
    stop_price: float | None = None
    order_id: str = ""
    status: OrderStatus = OrderStatus.PENDING
    filled_price: float | None = None
    created_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    client_order_id: str = ""

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serialisable dictionary."""
        return {
            "order_id": self.order_id,
            "client_order_id": self.client_order_id,
            "symbol": self.symbol,
            "side": self.side.value,
            "quantity": self.quantity,
            "order_type": self.order_type.value,
            "limit_price": self.limit_price,
            "stop_price": self.stop_price,
            "status": self.status.value,
            "filled_price": self.filled_price,
            "created_at": self.created_at,
        }


@dataclass
class Position:
    """An open position.

    Attributes:
        symbol: Stock symbol.
        quantity: Shares held.
        average_price: Average entry price.
        current_price: Latest market price.
    """

    symbol: str
    quantity: float = 0.0
    average_price: float = 0.0
    current_price: float = 0.0

    def market_value(self) -> float:
        """Return the current market value."""
        return self.quantity * self.current_price

    def unrealized_pnl(self) -> float:
        """Return the unrealised profit/loss."""
        return (self.current_price - self.average_price) * self.quantity

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serialisable dictionary."""
        return {
            "symbol": self.symbol,
            "quantity": self.quantity,
            "average_price": round(self.average_price, 4),
            "current_price": round(self.current_price, 4),
            "market_value": round(self.market_value(), 2),
            "unrealized_pnl": round(self.unrealized_pnl(), 2),
        }


@dataclass
class AccountBalance:
    """Broker account balance.

    Attributes:
        cash: Available cash.
        equity: Total account equity.
        buying_power: Available buying power.
        currency: Account currency code.
    """

    cash: float = 0.0
    equity: float = 0.0
    buying_power: float = 0.0
    currency: str = "NPR"

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serialisable dictionary."""
        return {
            "cash": round(self.cash, 2),
            "equity": round(self.equity, 2),
            "buying_power": round(self.buying_power, 2),
            "currency": self.currency,
        }


class Broker(ABC):
    """Abstract broker interface."""

    name: str = "broker"

    @abstractmethod
    def connect(self) -> None:
        """Establish the broker connection."""
        raise NotImplementedError

    @abstractmethod
    def disconnect(self) -> None:
        """Tear down the broker connection."""
        raise NotImplementedError

    @property
    @abstractmethod
    def connected(self) -> bool:
        """Return True when connected."""
        raise NotImplementedError

    @abstractmethod
    def place_order(self, order: Order) -> Order:
        """Submit an order.

        Args:
            order: Order to submit.

        Returns:
            The order with a broker-assigned ID and status.
        """
        raise NotImplementedError

    @abstractmethod
    def cancel_order(self, order_id: str) -> bool:
        """Cancel a pending order.

        Args:
            order_id: Broker order ID.

        Returns:
            True when cancelled.
        """
        raise NotImplementedError

    @abstractmethod
    def get_order(self, order_id: str) -> Order | None:
        """Return an order by ID.

        Args:
            order_id: Broker order ID.

        Returns:
            The order or None.
        """
        raise NotImplementedError

    @abstractmethod
    def get_positions(self) -> list[Position]:
        """Return all open positions."""
        raise NotImplementedError

    @abstractmethod
    def get_balance(self) -> AccountBalance:
        """Return the account balance."""
        raise NotImplementedError

    def describe(self) -> dict[str, Any]:
        """Return broker metadata."""
        return {"name": self.name, "connected": self.connected}
