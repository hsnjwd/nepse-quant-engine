"""Broker adapter stubs for external brokers.

Defines the integration contract for IBKR, Binance, and Alpaca
adapters.  These require credentials and external SDKs, so they are
provided as documented placeholders that fail fast with clear errors.
"""

from __future__ import annotations

from typing import Any

from src.brokers.base import Broker
from src.brokers.future_nepse import FutureNepseBroker


class BrokerAdapterStub(FutureNepseBroker):
    """Base stub for external broker adapters."""

    name = "adapter_stub"
    required_env: tuple[str, ...] = ()

    def __init__(self, **kwargs: Any) -> None:
        """Initialise the adapter with documented credentials.

        Args:
            **kwargs: Adapter-specific options (token, key, secret,
                account id, etc).
        """
        super().__init__(
            base_url=kwargs.get("base_url", ""),
            api_key=kwargs.get("api_key", ""),
        )
        self._options = dict(kwargs)
        missing = [
            key for key in self.required_env if not kwargs.get(key)
        ]
        if missing:
            logger_warning(
                "Adapter '%s' created without credentials: %s",
                self.name,
                ", ".join(missing),
            )

    def describe(self) -> dict[str, Any]:
        """Return adapter metadata."""
        return {
            "name": self.name,
            "connected": self.connected,
            "required_env": list(self.required_env),
            "status": "stub — requires live credentials",
        }


def logger_warning(message: str, *args: Any) -> None:
    """Log a warning via the shared logger."""
    from src.logging.logger import logger

    logger.warning(message, *args)


class InteractiveBrokersAdapter(BrokerAdapterStub):
    """IBKR adapter placeholder (requires ib_insync/ibapi)."""

    name = "ibkr"
    required_env = ("account",)


class BinanceAdapter(BrokerAdapterStub):
    """Binance adapter placeholder (requires python-binance)."""

    name = "binance"
    required_env = ("api_key", "api_secret")


class AlpacaAdapter(BrokerAdapterStub):
    """Alpaca adapter placeholder (requires alpaca-py)."""

    name = "alpaca"
    required_env = ("api_key", "api_secret")


def get_adapter(name: str, **kwargs: Any) -> Broker:
    """Return an adapter instance by name.

    Args:
        name: Adapter key (``ibkr``, ``binance``, ``alpaca``).
        **kwargs: Credential options.

    Returns:
        An adapter instance.

    Raises:
        ValueError: For unknown adapter names.
    """
    adapters: dict[str, type[BrokerAdapterStub]] = {
        "ibkr": InteractiveBrokersAdapter,
        "binance": BinanceAdapter,
        "alpaca": AlpacaAdapter,
    }
    try:
        cls = adapters[name.lower()]
    except KeyError as exc:
        raise ValueError(
            f"Unknown adapter '{name}'. Available: {sorted(adapters)}"
        ) from exc
    return cls(**kwargs)
