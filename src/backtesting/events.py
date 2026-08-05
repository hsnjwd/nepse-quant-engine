"""Event bus for the institutional backtesting engine.

Implements a lightweight publish/subscribe event system so strategy
and analytics code can observe the engine lifecycle (bars, orders,
fills, position changes) without coupling to the engine internals.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from src.backtesting.models import EventType

logger = logging.getLogger(__name__)

# Subscriber callback signature: (event_type, payload_dict)
Subscriber = Callable[[EventType, dict[str, Any]], None]


@dataclass
class BacktestEvent:
    """An event emitted by the backtest engine.

    Attributes:
        type: Event type enum.
        payload: Arbitrary event payload.
        bar_index: Optional bar index the event belongs to.
        timestamp: Optional timestamp of the event.
    """

    type: EventType
    payload: dict[str, Any] = field(default_factory=dict)
    bar_index: int = 0
    timestamp: Any = None


class EventBus:
    """Thread-safe publish/subscribe event bus.

    Usage::

        bus = EventBus()
        bus.subscribe(EventType.FILL, my_callback)
        engine = BacktestEngine(config, event_bus=bus)
        engine.run(data)

    Subscribers may be attached for specific event types, or for
    every event by subscribing with ``EventType`` unset (None).
    """

    def __init__(self) -> None:
        """Initialise an empty subscriber registry."""
        self._subscribers: dict[EventType | None, list[Subscriber]] = {}

    def subscribe(
        self,
        event_type: EventType | None,
        callback: Subscriber,
    ) -> None:
        """Register *callback* for *event_type* (or all events when None).

        Args:
            event_type: Event type to observe, or ``None`` for all events.
            callback: Callable accepting ``(EventType, dict)``.

        Raises:
            TypeError: If callback is not callable.
        """
        if not callable(callback):
            raise TypeError("Subscriber callback must be callable.")
        self._subscribers.setdefault(event_type, []).append(callback)
        logger.debug("Subscribed %s for %s", callback.__name__, event_type)

    def unsubscribe(
        self,
        event_type: EventType | None,
        callback: Subscriber,
    ) -> None:
        """Remove *callback* from *event_type*.

        Args:
            event_type: Event type to stop observing.
            callback: The callback previously subscribed.

        Raises:
            ValueError: If the callback was not registered.
        """
        callbacks = self._subscribers.get(event_type)
        if not callbacks or callback not in callbacks:
            raise ValueError("Callback is not subscribed to this event type.")
        callbacks.remove(callback)

    def publish(
        self,
        event: BacktestEvent,
    ) -> None:
        """Deliver an event to all matching subscribers.

        Args:
            event: The event to publish.
        """
        global_cbs = list(self._subscribers.get(None, []))
        specific_cbs = list(self._subscribers.get(event.type, []))
        for cb in global_cbs + specific_cbs:
            try:
                cb(event.type, event.payload)
            except Exception as exc:
                logger.warning(
                    "Subscriber %s failed for %s: %s",
                    getattr(cb, "__name__", cb),
                    event.type,
                    exc,
                )

    def clear(self) -> None:
        """Remove all subscribers."""
        self._subscribers.clear()

    def subscriber_count(self, event_type: EventType | None = None) -> int:
        """Return the number of subscribers for *event_type* (or all)."""
        if event_type is None:
            return sum(len(cbs) for cbs in self._subscribers.values())
        return len(self._subscribers.get(event_type, []))


# Module-level default bus for convenience use cases.
default_event_bus = EventBus()
