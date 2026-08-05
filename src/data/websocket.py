"""WebSocket live market feed for real-time NEPSE data.

``LiveMarketStream`` connects to a WebSocket endpoint, subscribes callbacks
to receive market data updates, and automatically reconnects with
exponential backoff on disconnection.  If the WebSocket is unavailable,
callers fall back to API polling transparently.
"""

from __future__ import annotations

import json
import logging
import random
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable

from src.data.exceptions import DataServiceError, DataUnavailable
from src.data.models import StockQuote, MarketSummary

logger = logging.getLogger(__name__)


# Callback type: receives a stock quote update
QuoteCallback = Callable[[StockQuote], None]
# Callback type: receives market summary update
SummaryCallback = Callable[[MarketSummary], None]
# Generic callback for any message
MessageCallback = Callable[[dict[str, Any]], None]


@dataclass
class WebSocketConfig:
    """Configuration for the WebSocket connection."""

    url: str = "wss://nepseapi.surajrimal.dev/ws"
    reconnect_base_delay: float = 1.0
    reconnect_max_delay: float = 60.0
    reconnect_jitter: float = 0.1
    heartbeat_interval: float = 30.0
    connection_timeout: float = 10.0
    max_reconnect_attempts: int = 0  # 0 = unlimited
    enabled: bool = False  # off by default; configure to enable


@dataclass
class WebSocketStats:
    """Snapshot of WebSocket connection statistics."""

    connected: bool = False
    connected_at: float = 0.0
    disconnected_at: float = 0.0
    reconnect_count: int = 0
    messages_received: int = 0
    last_message_at: float = 0.0
    subscribers: int = 0
    url: str = ""
    uptime_seconds: float = 0.0


class LiveMarketStream:
    """WebSocket live market feed with subscriber pattern.

    Usage::

        stream = LiveMarketStream()
        stream.subscribe(on_quote)
        stream.start()
        ...
        stream.stop()
    """

    def __init__(self, config: WebSocketConfig | None = None) -> None:
        self._config = config or WebSocketConfig()
        self._quote_callbacks: list[QuoteCallback] = []
        self._summary_callbacks: list[SummaryCallback] = []
        self._message_callbacks: list[MessageCallback] = []
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._lock = threading.RLock()

        # Stats
        self._connected: bool = False
        self._connected_at: float = 0.0
        self._disconnected_at: float = 0.0
        self._reconnect_count: int = 0
        self._messages_received: int = 0
        self._last_message_at: float = 0.0
        self._ws = None

    # ── Subscriber pattern ───────────────────────────────────────

    def subscribe(self, callback: QuoteCallback) -> None:
        """Subscribe a callback for quote updates."""
        with self._lock:
            self._quote_callbacks.append(callback)
            logger.debug("[WebSocket] Subscribed quote callback (%d total)", len(self._quote_callbacks))

    def subscribe_summary(self, callback: SummaryCallback) -> None:
        """Subscribe a callback for market summary updates."""
        with self._lock:
            self._summary_callbacks.append(callback)
            logger.debug("[WebSocket] Subscribed summary callback (%d total)", len(self._summary_callbacks))

    def subscribe_messages(self, callback: MessageCallback) -> None:
        """Subscribe a callback for all raw messages."""
        with self._lock:
            self._message_callbacks.append(callback)
            logger.debug("[WebSocket] Subscribed message callback (%d total)", len(self._message_callbacks))

    def unsubscribe(self, callback: QuoteCallback) -> None:
        """Unsubscribe a quote callback."""
        with self._lock:
            if callback in self._quote_callbacks:
                self._quote_callbacks.remove(callback)

    def unsubscribe_summary(self, callback: SummaryCallback) -> None:
        """Unsubscribe a summary callback."""
        with self._lock:
            if callback in self._summary_callbacks:
                self._summary_callbacks.remove(callback)

    def unsubscribe_messages(self, callback: MessageCallback) -> None:
        """Unsubscribe a message callback."""
        with self._lock:
            if callback in self._message_callbacks:
                self._message_callbacks.remove(callback)

    @property
    def subscriber_count(self) -> int:
        with self._lock:
            return len(self._quote_callbacks) + len(self._summary_callbacks) + len(self._message_callbacks)

    # ── Lifecycle ────────────────────────────────────────────────

    def enable(self, enabled: bool = True) -> None:
        """Enable or disable the WebSocket connection."""
        self._config.enabled = enabled
        logger.info("[WebSocket] %s", "Enabled" if enabled else "Disabled")

    def disable(self) -> None:
        """Disable the WebSocket connection."""
        self._config.enabled = False
        logger.info("[WebSocket] Disabled")

    def start(self) -> None:
        """Start the WebSocket connection in a background thread."""
        if self._thread and self._thread.is_alive():
            logger.info("[WebSocket] Already running")
            return
        if not self._config.enabled:
            logger.info("[WebSocket] WebSocket is disabled in config")
            return

        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._run_loop,
            daemon=True,
            name="ws-live-feed",
        )
        self._thread.start()
        logger.info("[WebSocket] Started connection thread to %s", self._config.url)

    def stop(self) -> None:
        """Stop the WebSocket connection."""
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=5)
        self._connected = False
        self._disconnected_at = time.time()
        self._ws = None
        logger.info("[WebSocket] Stopped")

    def is_connected(self) -> bool:
        return self._connected

    def get_stats(self) -> WebSocketStats:
        """Return a snapshot of connection statistics."""
        uptime = 0.0
        if self._connected and self._connected_at > 0:
            uptime = time.time() - self._connected_at
        return WebSocketStats(
            connected=self._connected,
            connected_at=self._connected_at,
            disconnected_at=self._disconnected_at,
            reconnect_count=self._reconnect_count,
            messages_received=self._messages_received,
            last_message_at=self._last_message_at,
            subscribers=self.subscriber_count,
            url=self._config.url,
            uptime_seconds=uptime,
        )

    # ── Internal ─────────────────────────────────────────────────

    def _run_loop(self) -> None:
        """Main connection loop with reconnection."""
        attempt = 0
        while not self._stop_event.is_set():
            try:
                self._connect()
                attempt = 0
                self._listen()
            except Exception as exc:
                logger.warning("[WebSocket] Connection error: %s", exc)
                self._connected = False
                self._disconnected_at = time.time()

            if self._stop_event.is_set():
                break

            attempt += 1
            self._reconnect_count += 1
            delay = self._reconnect_delay(attempt)
            logger.info("[WebSocket] Reconnecting in %.1fs (attempt %d)", delay, attempt)
            self._stop_event.wait(delay)

    def _reconnect_delay(self, attempt: int) -> float:
        """Calculate exponential backoff delay with jitter."""
        if self._config.max_reconnect_attempts > 0 and attempt > self._config.max_reconnect_attempts:
            return self._config.reconnect_max_delay
        delay = min(
            self._config.reconnect_base_delay * (2 ** (attempt - 1)),
            self._config.reconnect_max_delay,
        )
        jitter = delay * self._config.reconnect_jitter * random.uniform(-1.0, 1.0)
        # Cap the jittered delay at the configured maximum (the cap must
        # apply to the final value, not just the pre-jitter backoff).
        return max(0.5, min(self._config.reconnect_max_delay, delay + jitter))

    def _connect(self) -> None:
        """Establish the WebSocket connection."""
        try:
            import websocket  # noqa: PLC0415 — lazy import
        except ImportError:
            logger.warning("[WebSocket] websocket-client not installed; using polling fallback")
            self._connected = False
            self._stop_event.set()  # disable WebSocket mode permanently
            return

        self._ws = websocket.WebSocket()
        self._ws.settimeout(self._config.connection_timeout)
        self._ws.connect(self._config.url)
        self._connected = True
        self._connected_at = time.time()
        logger.info("[WebSocket] Connected to %s", self._config.url)

        # Send heartbeat subscription
        self._send_heartbeat()

    def _send_heartbeat(self) -> None:
        """Send a heartbeat ping to keep the connection alive."""
        if self._ws and self._connected:
            try:
                self._ws.send(json.dumps({"type": "ping"}))
            except Exception:
                pass

    def _listen(self) -> None:
        """Listen for messages and dispatch to subscribers."""
        last_heartbeat = time.time()

        while not self._stop_event.is_set() and self._connected:
            try:
                # Heartbeat
                if time.time() - last_heartbeat > self._config.heartbeat_interval:
                    self._send_heartbeat()
                    last_heartbeat = time.time()

                # Read message
                if self._ws is None:
                    break
                raw = self._ws.recv()
                if not raw:
                    continue

                self._messages_received += 1
                self._last_message_at = time.time()

                try:
                    data = json.loads(raw)
                except json.JSONDecodeError:
                    continue

                # Dispatch
                self._dispatch(data)

            except Exception as exc:
                logger.debug("[WebSocket] Read error: %s", exc)
                break

        self._connected = False
        self._disconnected_at = time.time()

    def _dispatch(self, data: dict[str, Any]) -> None:
        """Dispatch a parsed message to appropriate callbacks."""
        msg_type = data.get("type", "")

        with self._lock:
            # Raw message callbacks
            for cb in self._message_callbacks:
                try:
                    cb(data)
                except Exception as exc:
                    logger.warning("[WebSocket] Message callback error: %s", exc)

            # Quote updates
            if msg_type in ("quote", "stock", "ltp"):
                quote = StockQuote(
                    symbol=str(data.get("symbol", data.get("s", "?"))),
                    ltp=float(data.get("ltp", data.get("price", 0))),
                    change=float(data.get("change", data.get("chg", 0))),
                    change_pct=float(data.get("changePct", data.get("chgPct", 0))),
                    volume=int(data.get("volume", data.get("vol", 0))),
                    high=float(data.get("high", 0)),
                    low=float(data.get("low", 0)),
                    open_price=float(data.get("open", 0)),
                )
                for cb in self._quote_callbacks:
                    try:
                        cb(quote)
                    except Exception as exc:
                        logger.warning("[WebSocket] Quote callback error: %s", exc)

            # Market summary updates
            elif msg_type in ("summary", "market"):
                summary = MarketSummary(
                    index=float(data.get("index", data.get("nepseIndex", 0))),
                    change=float(data.get("change", 0)),
                    change_pct=float(data.get("changePct", 0)),
                    volume=int(data.get("volume", 0)),
                    turnover=float(data.get("turnover", 0)),
                    advances=int(data.get("advances", 0)),
                    declines=int(data.get("declines", 0)),
                    status=str(data.get("status", "Unknown")).capitalize(),
                )
                for cb in self._summary_callbacks:
                    try:
                        cb(summary)
                    except Exception as exc:
                        logger.warning("[WebSocket] Summary callback error: %s", exc)
