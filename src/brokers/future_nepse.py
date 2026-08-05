"""FutureNepseBroker — placeholder for live NEPSE connectivity.

Defines the integration contract for a real NEPSE broker adapter.
The live adapter is not implemented (no exchange connectivity is
available) but the class structure, connection flow, and error
handling are fully specified so it can be dropped in later.
"""

from __future__ import annotations

import logging
from typing import Any

from src.brokers.base import AccountBalance, Broker, Order, Position
from src.logging.logger import logger


class FutureNepseBroker(Broker):
    """Unimplemented live NEPSE broker adapter.

    Calling any trading method raises :class:`NotImplementedError` with
    a clear message.  Subclass this and implement the abstract methods
    to integrate a real broker.

    Attributes:
        name: Broker name.
        base_url: Configured API base URL (may be empty).
    """

    name = "future_nepse"

    def __init__(self, base_url: str = "", api_key: str = "") -> None:
        """Initialise the adapter stub.

        Args:
            base_url: Optional broker API base URL.
            api_key: Optional API key (not stored securely; use env
                vars in production).
        """
        self._base_url = base_url
        self._api_key = bool(api_key)
        self._is_connected = False

    @property
    def base_url(self) -> str:
        """Return the configured base URL."""
        return self._base_url

    def connect(self) -> None:
        """Attempt to connect.

        Raises:
            NotImplementedError: Always, until a live adapter exists.
        """
        raise NotImplementedError(
            "FutureNepseBroker is a placeholder. Live NEPSE broker "
            "connectivity is not implemented — see docs/ for the "
            "integration contract."
        )

    def disconnect(self) -> None:
        """No-op disconnect for the stub."""
        self._is_connected = False

    @property
    def connected(self) -> bool:
        """Return the connection state."""
        return self._is_connected

    def place_order(self, order: Order) -> Order:
        """Place an order.

        Raises:
            NotImplementedError: Always.
        """
        raise NotImplementedError("Live order placement is not implemented.")

    def cancel_order(self, order_id: str) -> bool:
        """Cancel an order.

        Raises:
            NotImplementedError: Always.
        """
        raise NotImplementedError("Live order cancellation is not implemented.")

    def get_order(self, order_id: str) -> Order | None:
        """Fetch an order.

        Raises:
            NotImplementedError: Always.
        """
        raise NotImplementedError("Live order lookup is not implemented.")

    def get_positions(self) -> list[Position]:
        """Fetch positions.

        Raises:
            NotImplementedError: Always.
        """
        raise NotImplementedError("Live position fetching is not implemented.")

    def get_balance(self) -> AccountBalance:
        """Fetch the account balance.

        Raises:
            NotImplementedError: Always.
        """
        raise NotImplementedError("Live balance fetching is not implemented.")

    def describe(self) -> dict[str, Any]:
        """Return broker metadata."""
        return {
            "name": self.name,
            "connected": self.connected,
            "base_url": self._base_url,
            "status": "placeholder",
        }
