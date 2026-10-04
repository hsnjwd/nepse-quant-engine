"""Future NEPSE market interfaces (intraday, margin, options, futures).

Design-only interfaces for exchange capabilities that are not yet
implemented.  Concrete adapters can be added behind these protocols
without changing consumers.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


@dataclass
class OrderBookLevel:
    """One level of the order book.

    Attributes:
        price: Level price.
        quantity: Available quantity at this price.
        side: ``"bid"`` or ``"ask"``.
    """

    price: float
    quantity: float
    side: str = "bid"

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serialisable dictionary."""
        return {"price": self.price, "quantity": self.quantity, "side": self.side}


class IntradayFeed(ABC):
    """Interface for intraday market data (ticks, order book, candles)."""

    name: str = "intraday"

    @abstractmethod
    def get_ticks(self, symbol: str, limit: int = 100) -> list[dict[str, Any]]:
        """Return recent tick data for a symbol."""
        raise NotImplementedError

    @abstractmethod
    def get_order_book(self, symbol: str, depth: int = 10) -> list[OrderBookLevel]:
        """Return the current order book."""
        raise NotImplementedError

    @abstractmethod
    def get_intraday_candles(
        self, symbol: str, interval: str = "5m", limit: int = 100
    ) -> list[dict[str, Any]]:
        """Return intraday OHLCV candles."""
        raise NotImplementedError


class MarginAccount(ABC):
    """Interface for margin trading accounts."""

    name: str = "margin"

    @abstractmethod
    def get_margin_balance(self) -> dict[str, float]:
        """Return margin balance details (equity, available, used)."""
        raise NotImplementedError

    @abstractmethod
    def get_margin_requirement(self, symbol: str, quantity: float) -> float:
        """Return the initial margin required for an order."""
        raise NotImplementedError

    @abstractmethod
    def get_margin_ratio(self) -> float:
        """Return the current margin utilisation ratio (0–1)."""
        raise NotImplementedError


class OptionsFeed(ABC):
    """Interface for options market data."""

    name: str = "options"

    @abstractmethod
    def get_option_chain(self, symbol: str, expiry: str) -> list[dict[str, Any]]:
        """Return the option chain for a symbol and expiry."""
        raise NotImplementedError

    @abstractmethod
    def get_greeks(self, symbol: str, expiry: str, strike: float) -> dict[str, float]:
        """Return option greeks (delta, gamma, theta, vega, rho)."""
        raise NotImplementedError

    @abstractmethod
    def get_implied_volatility(self, symbol: str, expiry: str) -> float:
        """Return the implied volatility surface point."""
        raise NotImplementedError


class FuturesFeed(ABC):
    """Interface for futures market data."""

    name: str = "futures"

    @abstractmethod
    def get_futures_quotes(self, symbol: str) -> list[dict[str, Any]]:
        """Return futures quotes across expiries."""
        raise NotImplementedError

    @abstractmethod
    def get_basis(self, symbol: str, expiry: str) -> float:
        """Return the futures basis (futures price - spot)."""
        raise NotImplementedError

    @abstractmethod
    def get_open_interest(self, symbol: str, expiry: str) -> int:
        """Return open interest for a contract."""
        raise NotImplementedError


def all_interfaces() -> list[type]:
    """Return all future-market interface classes."""
    return [IntradayFeed, MarginAccount, OptionsFeed, FuturesFeed]
