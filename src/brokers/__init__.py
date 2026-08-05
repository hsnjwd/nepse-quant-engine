"""Broker layer for the NEPSE Quant Engine.

Provides an abstract :class:`Broker` interface plus concrete
implementations:

* :class:`PaperBroker` — in-memory simulated trading.
* :class:`MockBroker` — deterministic fake trading for tests/demos.
* :class:`FutureNepseBroker` — placeholder for live NEPSE connectivity.
* External adapter stubs (IBKR, Binance, Alpaca).
"""

from __future__ import annotations

from src.brokers.adapters import (
    AlpacaAdapter,
    BinanceAdapter,
    InteractiveBrokersAdapter,
    get_adapter,
)
from src.brokers.base import (
    AccountBalance,
    Broker,
    Order,
    OrderSide,
    OrderStatus,
    OrderType,
    Position,
)
from src.brokers.future_nepse import FutureNepseBroker
from src.brokers.mock import MockBroker
from src.brokers.paper import PaperBroker

__all__ = [
    "AlpacaAdapter",
    "BinanceAdapter",
    "InteractiveBrokersAdapter",
    "get_adapter",
    "AccountBalance",
    "Broker",
    "Order",
    "OrderSide",
    "OrderStatus",
    "OrderType",
    "Position",
    "FutureNepseBroker",
    "MockBroker",
    "PaperBroker",
]
