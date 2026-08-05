"""Centralized data service for the NEPSE Quant Engine.

``DataService`` is the **single source of truth** for all market data.
No module should call external APIs, read CSV files, or access cache
directly — everything flows through this service.
"""

from __future__ import annotations

import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError
from typing import Any, Callable

import pandas as pd

from src.config import (
    DATA_DIRECTORY,
    DATA_SERVICE_API_URLS,
    CACHE_REFRESH_INTERVAL,
    ENABLE_PERFORMANCE_MONITORING,
)
from src.data.cache import TieredCache
from src.data.exceptions import DataServiceError, DataUnavailable
from src.data.health import ProviderHealthMonitor, HealthCheckConfig
from src.data.metrics import MetricsCollector, MetricsSnapshot
from src.data.models import (
    MarketSummary,
    MarketScanResult,
    StockQuote,
    StockHistory,
    TopMover,
    WatchlistEntry,
)
from src.data.providers import (
    CSVProvider,
    HybridProvider,
    BaseProvider,
)
from src.data.rate_limiter import RateLimiter
from src.data.websocket import LiveMarketStream, WebSocketConfig
from src.scanner.engine import scan_market as _csv_scan
from src.watchlist.manager import (
    get_watchlist as _get_watchlist,
    add_stock as _add_watchlist,
    remove_stock as _remove_watchlist,
)

logger = logging.getLogger(__name__)


class DataService:
    """Centralized data access service.

    Integrates providers, cache, WebSocket live feed, rate limiter,
    health monitor, and request metrics into a single entry point.
    Thread-safe singleton.
    """

    # Class-level default URLs — single source of truth is
    # ``src.config.DATA_SERVICE_API_URLS``; updated via ``configure()``.
    _default_api_urls: dict[str, str] = dict(DATA_SERVICE_API_URLS)
    _instance: DataService | None = None
    _instance_lock = threading.Lock()

    # Uploaded-history persistence (long TTL so uploads survive restarts)
    UPLOADED_HISTORY_PREFIX: str = "uploaded_history"
    UPLOADED_HISTORY_TTL: int = 30 * 24 * 3600  # 30 days

    def __new__(cls, *args: Any, **kwargs: Any) -> DataService:
        """Return a singleton instance to share cache state across pages."""
        if cls._instance is None:
            with cls._instance_lock:
                # Double-check inside the lock
                if cls._instance is None:
                    # 1. Create the bare instance
                    instance = super().__new__(cls)
                    
                    # 2. Fully initialize it under the safety of the lock
                    instance._initialize_service(*args, **kwargs)
                    
                    # 3. Only expose it to the class AFTER it is 100% ready
                    cls._instance = instance
                    
        return cls._instance

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        # Python automatically calls __init__ after __new__.
        # Because we already handled setup inside _initialize_service under the lock, 
        # this must be completely empty to prevent duplicate executions.
        pass

    def _initialize_service(
        self,
        provider: BaseProvider | None = None,
        cache: TieredCache | None = None,
    ) -> None:
        """Internal setup method called securely by __new__."""

        # ── 1. Cache (no dependencies) ───────────────────────────
        self._cache = cache or TieredCache(memory_ttl=60, disk_ttl=600)

        # ── 2. Metrics collector (no dependencies) ───────────────
        self._metrics = MetricsCollector()
        self._performance_monitoring_enabled = ENABLE_PERFORMANCE_MONITORING

        # ── 3. Rate limiter (no dependencies) ────────────────────
        self._rate_limiter = RateLimiter()
        self._rate_limiter.configure_defaults(
            max_tokens=10.0,
            refill_rate=1.0,
            backoff_base=1.0,
            backoff_max=120.0,
            jitter=0.1,
        )

        # ── 4. Health monitor (no dependencies) ──────────────────
        self._health_monitor = ProviderHealthMonitor(
            HealthCheckConfig(
                failure_threshold=5,
                recovery_period=300.0,
                success_recovery_count=3,
            )
        )
        self._health_monitor.register("api")
        self._health_monitor.register("csv")

        # ── 5. WebSocket stream (no dependencies) ────────────────
        self._websocket = LiveMarketStream(WebSocketConfig(enabled=False))

        # ── 6. Providers (depends on health_monitor) ─────────────
        if provider:
            self._provider = provider
        else:
            from src.data.providers import APIProvider  # noqa: PLC0415
            api_urls = dict(self._default_api_urls)
            self._provider = HybridProvider(
                [APIProvider(api_urls=api_urls), CSVProvider(data_dir=DATA_DIRECTORY)],
                health_monitor=self._health_monitor,
            )

        # ── 7. Background refresh infrastructure ─────────────────
        self._background_thread: threading.Thread | None = None
        self._background_stop = threading.Event()
        self._background_pause = threading.Event()
        self._background_pause.set()  # not paused by default
        self._background_interval = CACHE_REFRESH_INTERVAL
        self._executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="datasvc")

    @classmethod
    def reset_instance(cls) -> None:
        """Reset the singleton (useful in tests).

        Stops background refresh and executor on the old instance
        before discarding it, ensuring threads don't leak between tests.
        """
        with cls._instance_lock:
            old = cls._instance
            cls._instance = None

        # Clean up the old instance outside the lock to avoid deadlocks
        if old is not None:
            try:
                old.stop_background_refresh()
            except Exception:
                pass
            try:
                old._websocket.stop()
            except Exception:
                pass
            # Full reset — discard cached market data so tests never
            # inherit stale state from a previous instance.
            try:
                old.clear_cache()
            except Exception:
                pass

    @classmethod
    def configure(cls, api_urls: dict[str, str]) -> None:
        """Override the default API URLs (call during app startup)."""
        cls._default_api_urls.update(api_urls)

    @property
    def provider(self) -> BaseProvider:
        return self._provider

    @provider.setter
    def provider(self, p: BaseProvider) -> None:
        self._provider = p

    @property
    def metrics_collector(self) -> MetricsCollector:
        """Access the metrics collector directly for instrumentation."""
        return self._metrics

    @property
    def health_monitor(self) -> ProviderHealthMonitor:
        """Access the health monitor directly."""
        return self._health_monitor

    @property
    def rate_limiter(self) -> RateLimiter:
        """Access the rate limiter directly."""
        return self._rate_limiter

    @property
    def websocket_stream(self) -> LiveMarketStream:
        """Access the WebSocket stream directly."""
        return self._websocket

    # ── Metrics ──────────────────────────────────────────────────

    def get_metrics(self) -> MetricsSnapshot:
        """Return a snapshot of all request metrics."""
        return self._metrics.snapshot()

    def reset_metrics(self) -> None:
        """Reset all collected metrics to zero."""
        self._metrics.reset()

    def _record_call(self, operation: str) -> None:
        """Increment request counter for *operation*."""
        if self._performance_monitoring_enabled:
            self._metrics.record_request(operation)

    # ── Background refresh (upgraded) ─────────────────────────────

    def start_background_refresh(self, interval: int | None = None) -> None:
        """Start a background thread that refreshes cached market data.

        Thread-safe, no duplicate threads.  Supports pause/resume and
        dynamic interval changes.  Automatically recovers from exceptions.

        Args:
            interval: Refresh interval in seconds (default: config value).
        """
        if self._background_thread and self._background_thread.is_alive():
            logger.info("Background refresh already running")
            return

        if interval is not None:
            self._background_interval = interval

        self._background_stop.clear()
        self._background_pause.set()

        def _loop() -> None:
            logger.info(
                "Background refresh started (interval=%ds)",
                self._background_interval,
            )
            while not self._background_stop.is_set():
                # Wait for pause to be cleared
                self._background_pause.wait()

                if self._background_stop.is_set():
                    break

                try:
                    self.refresh_cache()
                    logger.info("Background refresh complete")
                except Exception as exc:
                    logger.warning("Background refresh error: %s", exc)

                # Wait for interval or stop signal
                self._background_stop.wait(self._background_interval)

        self._background_thread = threading.Thread(
            target=_loop,
            daemon=True,
            name="datasvc-refresh",
        )
        self._background_thread.start()
        logger.info("Background refresh thread started")

    def stop_background_refresh(self) -> None:
        """Stop the background refresh thread.  Graceful shutdown."""
        self._background_stop.set()
        self._background_pause.set()  # resume so loop can exit
        if self._background_thread:
            self._background_thread.join(timeout=5)
            logger.info("Background refresh stopped")
        try:
            self._executor.shutdown(wait=False)
        except Exception:
            pass

    def pause_background_refresh(self) -> None:
        """Pause the background refresh loop."""
        self._background_pause.clear()
        logger.info("Background refresh paused")

    def resume_background_refresh(self) -> None:
        """Resume a paused background refresh."""
        self._background_pause.set()
        logger.info("Background refresh resumed")

    def set_refresh_interval(self, interval: int) -> None:
        """Dynamically change the refresh interval (takes effect on next cycle)."""
        self._background_interval = max(5, interval)
        logger.info("Background refresh interval set to %ds", self._background_interval)

    def is_background_refresh_running(self) -> bool:
        """Check if the background refresh thread is alive."""
        return self._background_thread is not None and self._background_thread.is_alive()

    def is_background_refresh_paused(self) -> bool:
        """Check if background refresh is paused."""
        return not self._background_pause.is_set()

    # ── WebSocket live feed ──────────────────────────────────────

    def start_live_feed(self, url: str | None = None) -> None:
        """Start the WebSocket live market feed.

        If the WebSocket fails, falls back to API polling automatically.
        """
        if url:
            self._websocket._config.url = url
        self._websocket.enable()

        # Wire up callbacks to update cache in real time
        def _on_quote(q: StockQuote) -> None:
            # Update live quotes cache — future reads get fresh data
            try:
                quotes = self._cache.get("live_quotes") or []
                # Update or append the quote
                found = False
                for i, existing in enumerate(quotes):
                    if existing.symbol.upper() == q.symbol.upper():
                        quotes[i] = q
                        found = True
                        break
                if not found:
                    quotes.append(q)
                self._cache.set("live_quotes", quotes, ttl=30)
            except Exception as exc:
                logger.debug("[WebSocket] Cache update error: %s", exc)
            if self._performance_monitoring_enabled:
                self._metrics.record_websocket_message()

        def _on_summary(s: MarketSummary) -> None:
            self._cache.set("market_summary", s, ttl=30)
            if self._performance_monitoring_enabled:
                self._metrics.record_websocket_message()

        self._websocket.subscribe(_on_quote)
        self._websocket.subscribe_summary(_on_summary)
        self._websocket.start()
        logger.info("Live feed started — WebSocket updating cache in real time")

    def stop_live_feed(self) -> None:
        """Stop the WebSocket live feed.  Falls back to API polling."""
        self._websocket.disable()
        self._websocket.stop()
        logger.info("Live feed stopped, falling back to API polling")

    def subscribe_market(self, callback: Callable[[StockQuote], None]) -> None:
        """Subscribe a callback for live quote updates from WebSocket."""
        self._websocket.subscribe(callback)

    def unsubscribe_market(self, callback: Callable[[StockQuote], None]) -> None:
        """Unsubscribe a callback from live quote updates."""
        self._websocket.unsubscribe(callback)

    def is_live_feed_connected(self) -> bool:
        """Check if the WebSocket feed is currently connected."""
        return self._websocket.is_connected()

    def get_websocket_stats(self):
        """Return WebSocket connection statistics."""
        return self._websocket.get_stats()

    # ── Market status / summary ──────────────────────────────────

    def get_market_status(self) -> str:
        try:
            return self.get_market_summary().status
        except DataUnavailable:
            return "Unknown"

    def get_market_summary(self) -> MarketSummary:
        self._record_call("get_market_summary")
        cached = self._cache.get("market_summary")
        if cached is not None:
            if self._performance_monitoring_enabled:
                self._metrics.record_cache_hit("get_market_summary")
            return cached
        if self._performance_monitoring_enabled:
            self._metrics.record_cache_miss("get_market_summary")
        try:
            if self._rate_limiter.acquire("api"):
                result = self._provider.get_market_summary()
                self._cache.set("market_summary", result, ttl=30)
                self._health_monitor.record_success("api")
                if self._performance_monitoring_enabled:
                    self._metrics.record_api_call("api")
                return result
            return MarketSummary.empty()
        except Exception as exc:
            logger.warning("get_market_summary failed: %s", exc)
            self._health_monitor.record_failure("api")
            if self._performance_monitoring_enabled:
                self._metrics.record_provider_failure("api")
            return MarketSummary.empty()

    # ── Live quotes ──────────────────────────────────────────────

    def get_live_market(self) -> list[StockQuote]:
        self._record_call("get_live_market")
        cached = self._cache.get("live_quotes")
        if cached is not None:
            if self._performance_monitoring_enabled:
                self._metrics.record_cache_hit("get_live_market")
            return cached
        if self._performance_monitoring_enabled:
            self._metrics.record_cache_miss("get_live_market")
        try:
            if self._rate_limiter.acquire("api"):
                result = self._provider.get_live_quotes()
                self._cache.set("live_quotes", result, ttl=30)
                self._health_monitor.record_success("api")
                if self._performance_monitoring_enabled:
                    self._metrics.record_api_call("api")
                return result
            return []
        except Exception as exc:
            logger.error("get_live_market failed: %s", exc)
            self._health_monitor.record_failure("api")
            return []

    def get_stock(self, symbol: str) -> StockQuote | None:
        symbol_upper = symbol.upper().strip()
        if not symbol_upper:
            return None
        quotes = self.get_live_market()
        for q in quotes:
            if q.symbol.upper() == symbol_upper:
                return q
        return None

    # ── Price history ────────────────────────────────────────────

    def get_history(self, symbol: str, days: int = 365) -> StockHistory:
        self._record_call("get_history")
        cache_key = f"history:{symbol.upper()}:{days}"
        cached = self._cache.get(cache_key)
        if cached is not None:
            if self._performance_monitoring_enabled:
                self._metrics.record_cache_hit("get_history")
            return StockHistory(symbol=symbol.upper(), df=cached, days=days, source="cache")
        if self._performance_monitoring_enabled:
            self._metrics.record_cache_miss("get_history")
        try:
            df = self._provider.get_history(symbol.upper().strip(), days)
            if df is not None and not df.empty:
                self._cache.set(cache_key, df, ttl=300)
                if self._performance_monitoring_enabled:
                    self._metrics.record_api_call("csv" if "csv" in str(type(self._provider)).lower() else "api")
                return StockHistory(symbol=symbol.upper(), df=df, days=days, source="provider")
            return StockHistory(symbol=symbol.upper(), days=days)
        except DataServiceError:
            return StockHistory(symbol=symbol.upper(), days=days)
        except Exception as exc:
            logger.warning("get_history(%s) failed: %s", symbol, exc)
            return StockHistory(symbol=symbol.upper(), days=days)

    def get_nepse_index_history(self, days: int = 500) -> pd.DataFrame:
        self._record_call("get_nepse_index_history")
        cache_key = f"nepse_index:{days}"
        cached = self._cache.get(cache_key)
        if cached is not None:
            return cached
        try:
            df = self._provider.get_nepse_index_history(days)
            if df is not None and not df.empty:
                self._cache.set(cache_key, df, ttl=300)
                return df
            return pd.DataFrame()
        except Exception as exc:
            logger.warning("get_nepse_index_history failed: %s", exc)
            return pd.DataFrame()

    # ── Uploaded price history ───────────────────────────────────

    def save_history(self, symbol: str, df: pd.DataFrame) -> StockHistory:
        """Persist uploaded OHLCV history for *symbol* through the cache.

        The frame is validated (must contain ``Date``/``Open``/``High``/
        ``Low``/``Close``/``Volume``), cleaned (dates parsed and sorted,
        numerics coerced, invalid rows dropped) and stored under a
        dedicated cache namespace so it survives application restarts.

        Args:
            symbol: Ticker symbol (case-insensitive).
            df: OHLCV history to store.

        Returns:
            The stored :class:`StockHistory`.

        Raises:
            ValueError: If the symbol is empty or required columns are
                missing, or no valid rows remain after cleaning.
        """
        symbol_upper = symbol.upper().strip() if symbol else ""
        if not symbol_upper:
            raise ValueError("Symbol must not be empty")
        if df is None or df.empty:
            raise ValueError("Cannot save empty history")
        required = ("Date", "Open", "High", "Low", "Close", "Volume")
        missing = [c for c in required if c not in df.columns]
        if missing:
            raise ValueError(f"Missing required columns: {', '.join(missing)}")

        cleaned = df.copy()
        cleaned["Date"] = pd.to_datetime(cleaned["Date"], errors="coerce")
        cleaned = cleaned.dropna(subset=["Date"]).sort_values("Date")
        for col in ("Open", "High", "Low", "Close", "Volume"):
            cleaned[col] = pd.to_numeric(cleaned[col], errors="coerce")
        cleaned = cleaned.dropna(subset=["Close"]).reset_index(drop=True)
        if cleaned.empty:
            raise ValueError("No valid rows after cleaning")

        key = f"{self.UPLOADED_HISTORY_PREFIX}:{symbol_upper}"
        self._cache.set(key, cleaned, ttl=self.UPLOADED_HISTORY_TTL)
        symbols = self.list_uploaded_symbols()
        if symbol_upper not in symbols:
            symbols.append(symbol_upper)
            self._cache.set("uploaded_symbols", symbols, ttl=self.UPLOADED_HISTORY_TTL)
        logger.info("Saved uploaded history for %s (%d rows)", symbol_upper, len(cleaned))
        return StockHistory(symbol=symbol_upper, df=cleaned, days=len(cleaned), source="upload")

    def get_uploaded_history(self, symbol: str) -> StockHistory | None:
        """Return uploaded history for *symbol*, or ``None`` if absent."""
        symbol_upper = symbol.upper().strip() if symbol else ""
        if not symbol_upper:
            return None
        key = f"{self.UPLOADED_HISTORY_PREFIX}:{symbol_upper}"
        df = self._cache.get(key)
        if not isinstance(df, pd.DataFrame) or df.empty:
            return None
        return StockHistory(symbol=symbol_upper, df=df, days=len(df), source="upload")

    def list_uploaded_symbols(self) -> list[str]:
        """Return the list of symbols with persisted uploaded history."""
        symbols = self._cache.get("uploaded_symbols") or []
        return sorted(s for s in symbols if isinstance(s, str))

    def has_uploaded_history(self, symbol: str) -> bool:
        """Check whether uploaded history exists for *symbol*."""
        return self.get_uploaded_history(symbol) is not None

    def delete_uploaded_history(self, symbol: str) -> bool:
        """Delete uploaded history for *symbol*.

        Returns:
            ``True`` if a stored history was removed.
        """
        symbol_upper = symbol.upper().strip() if symbol else ""
        if not symbol_upper:
            return False
        key = f"{self.UPLOADED_HISTORY_PREFIX}:{symbol_upper}"
        existed = isinstance(self._cache.get(key), pd.DataFrame)
        self._cache.delete(key)
        symbols = [s for s in self.list_uploaded_symbols() if s != symbol_upper]
        self._cache.set("uploaded_symbols", symbols, ttl=self.UPLOADED_HISTORY_TTL)
        return existed

    # ── Top movers ───────────────────────────────────────────────

    def get_top_gainers(self, limit: int = 10) -> list[TopMover]:
        self._record_call("get_top_gainers")
        key = f"top_gainers:{limit}"
        cached = self._cache.get(key)
        if cached is not None:
            return cached
        try:
            result = self._provider.get_top_gainers(limit)
            self._cache.set(key, result, ttl=30)
            return result
        except Exception as exc:
            logger.warning("get_top_gainers failed: %s", exc)
            return []

    def get_top_losers(self, limit: int = 10) -> list[TopMover]:
        self._record_call("get_top_losers")
        key = f"top_losers:{limit}"
        cached = self._cache.get(key)
        if cached is not None:
            return cached
        try:
            result = self._provider.get_top_losers(limit)
            self._cache.set(key, result, ttl=30)
            return result
        except Exception as exc:
            logger.warning("get_top_losers failed: %s", exc)
            return []

    def get_top_turnover(self, limit: int = 10) -> list[TopMover]:
        self._record_call("get_top_turnover")
        key = f"top_turnover:{limit}"
        cached = self._cache.get(key)
        if cached is not None:
            return cached
        try:
            result = self._provider.get_top_turnover(limit)
            self._cache.set(key, result, ttl=30)
            return result
        except Exception as exc:
            logger.warning("get_top_turnover failed: %s", exc)
            return []

    # ── Watchlist ────────────────────────────────────────────────

    def get_watchlist(self) -> list[str]:
        return _get_watchlist()

    def add_to_watchlist(self, symbol: str) -> None:
        _add_watchlist(symbol.upper().strip())

    def remove_from_watchlist(self, symbol: str) -> None:
        _remove_watchlist(symbol.upper().strip())

    # ── Market scan ──────────────────────────────────────────────

    def scan_market(self) -> MarketScanResult:
        self._record_call("scan_market")
        cached = self._cache.get("market_scan")
        if cached is not None:
            return cached
        try:
            scan_data = _csv_scan()
            result = MarketScanResult(
                results=scan_data.get("results", []),
                skipped=scan_data.get("skipped", []),
                total_scanned=len(scan_data.get("results", [])) + len(scan_data.get("skipped", [])),
            )
            self._cache.set("market_scan", result, ttl=60)
            return result
        except Exception as exc:
            logger.warning("scan_market (CSV) failed: %s", exc)
            return self._live_market_scan()

    def _live_market_scan(self) -> MarketScanResult:
        from src.engine.analyzer import analyze_dataframe
        from src.scanner.ranking import rank_market
        try:
            quotes = self.get_live_market()
            results: list[dict[str, Any]] = []
            skipped: list[dict[str, Any]] = []
            for q in quotes:
                try:
                    hist = self.get_history(q.symbol, days=365)
                    if hist.is_empty:
                        skipped.append({"symbol": q.symbol, "error": "No price history"})
                        continue
                    analysis = analyze_dataframe(hist.df)
                    analysis["symbol"] = q.symbol
                    analysis["live_price"] = q.ltp
                    results.append(analysis)
                except Exception as exc:
                    skipped.append({"symbol": q.symbol, "error": str(exc)})
            ranked = rank_market(results)
            result = MarketScanResult(results=ranked, skipped=skipped, total_scanned=len(ranked) + len(skipped))
            self._cache.set("market_scan", result, ttl=60)
            return result
        except Exception as exc:
            logger.error("_live_market_scan failed: %s", exc)
            return MarketScanResult()

    # ── Cache management ────────────────────────────────────────

    def refresh_cache(self) -> None:
        """Refresh all cached market data.  Thread-safe."""
        logger.info("[DataService] Refreshing cache...")
        keys = [
            "market_summary", "live_quotes", "market_scan",
            "top_gainers:10", "top_losers:10", "top_turnover:10",
        ]
        for k in keys:
            self._cache.delete(k)
        futures = []
        for fn in [self.get_market_summary, self.get_live_market]:
            try:
                futures.append(self._executor.submit(fn))
            except RuntimeError:
                # Executor shutdown — run inline
                try:
                    fn()
                except Exception as exc:
                    logger.warning("Cache refresh inline failure: %s", exc)
        for f in futures:
            try:
                f.result(timeout=30)
            except TimeoutError:
                logger.warning("Cache refresh timed out for a task")
            except Exception as exc:
                logger.warning("Cache refresh partial failure: %s", exc)
        logger.info("[DataService] Cache refresh complete")

    def clear_cache(self) -> None:
        """Clear all cached data."""
        logger.info("[DataService] Clearing all cache...")
        self._cache.clear()
