"""Request metrics collection for DataService.

Collects per-operation metrics including request counts, cache hit/miss rates,
API call counts, CSV fallback counts, latency percentiles, provider failures,
and WebSocket reconnect counts.
"""

from __future__ import annotations

import logging
import threading
import time
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class MetricsSnapshot:
    """Snapshot of all collected metrics."""

    total_requests: int = 0
    cache_hits: int = 0
    cache_misses: int = 0
    api_calls: int = 0
    csv_fallbacks: int = 0
    provider_failures: int = 0
    rate_limited_count: int = 0
    websocket_reconnects: int = 0
    websocket_messages: int = 0
    average_latency_ms: float = 0.0
    p95_latency_ms: float = 0.0
    p99_latency_ms: float = 0.0
    per_operation: dict[str, OperationMetrics] = field(default_factory=dict)
    per_provider: dict[str, ProviderMetrics] = field(default_factory=dict)

    @property
    def cache_hit_rate(self) -> float:
        total = self.cache_hits + self.cache_misses
        if total == 0:
            return 0.0
        return (self.cache_hits / total) * 100.0

    @property
    def failure_rate(self) -> float:
        if self.total_requests == 0:
            return 0.0
        return (self.provider_failures / self.total_requests) * 100.0


@dataclass
class OperationMetrics:
    """Metrics for a single operation type (e.g. get_market_summary)."""

    calls: int = 0
    cache_hits: int = 0
    cache_misses: int = 0
    api_calls: int = 0
    failures: int = 0
    total_latency: float = 0.0
    latencies: list[float] = field(default_factory=list)

    @property
    def average_latency_ms(self) -> float:
        if self.calls == 0:
            return 0.0
        return (self.total_latency / self.calls) * 1000.0

    @property
    def p95_latency_ms(self) -> float:
        if not self.latencies:
            return 0.0
        sorted_lat = sorted(self.latencies)
        idx = int(len(sorted_lat) * 0.95)
        return sorted_lat[min(idx, len(sorted_lat) - 1)] * 1000.0

    @property
    def p99_latency_ms(self) -> float:
        if not self.latencies:
            return 0.0
        sorted_lat = sorted(self.latencies)
        idx = int(len(sorted_lat) * 0.99)
        return sorted_lat[min(idx, len(sorted_lat) - 1)] * 1000.0


@dataclass
class ProviderMetrics:
    """Metrics for a single provider."""

    calls: int = 0
    successes: int = 0
    failures: int = 0
    total_latency: float = 0.0
    last_latency: float = 0.0

    @property
    def average_latency_ms(self) -> float:
        if self.calls == 0:
            return 0.0
        return (self.total_latency / self.calls) * 1000.0

    @property
    def success_rate(self) -> float:
        if self.calls == 0:
            return 100.0
        return (self.successes / self.calls) * 100.0


class MetricsCollector:
    """Thread-safe collector for DataService metrics.

    Usage::

        metrics = MetricsCollector()
        metrics.record_cache_hit("get_market_summary")
        metrics.record_api_call("nepse_scraper", latency_ms=150.0)
        snapshot = metrics.snapshot()
    """

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._operations: dict[str, OperationMetrics] = defaultdict(OperationMetrics)
        self._providers: dict[str, ProviderMetrics] = defaultdict(ProviderMetrics)
        self._total_requests: int = 0
        self._cache_hits: int = 0
        self._cache_misses: int = 0
        self._api_calls: int = 0
        self._csv_fallbacks: int = 0
        self._provider_failures: int = 0
        self._rate_limited_count: int = 0
        self._websocket_reconnects: int = 0
        self._websocket_messages: int = 0
        self._all_latencies: list[float] = []

    # ── Recording ────────────────────────────────────────────────

    def record_request(self, operation: str) -> None:
        """Record a request to *operation*."""
        with self._lock:
            self._total_requests += 1
            self._operations[operation].calls += 1

    def record_cache_hit(self, operation: str) -> None:
        """Record a cache hit for *operation*."""
        with self._lock:
            self._cache_hits += 1
            self._operations[operation].cache_hits += 1

    def record_cache_miss(self, operation: str) -> None:
        """Record a cache miss for *operation*."""
        with self._lock:
            self._cache_misses += 1
            self._operations[operation].cache_misses += 1

    def record_api_call(self, provider: str, latency_ms: float = 0.0) -> None:
        """Record an API call to *provider*."""
        with self._lock:
            self._api_calls += 1
            pm = self._providers[provider]
            pm.calls += 1
            pm.successes += 1
            pm.total_latency += latency_ms / 1000.0
            pm.last_latency = latency_ms / 1000.0
            if latency_ms > 0:
                self._all_latencies.append(latency_ms / 1000.0)
                if len(self._all_latencies) > 10000:
                    self._all_latencies = self._all_latencies[-5000:]

    def record_csv_fallback(self, provider: str = "csv") -> None:
        """Record a CSV fallback."""
        with self._lock:
            self._csv_fallbacks += 1
            pm = self._providers[provider]
            pm.calls += 1
            pm.successes += 1

    def record_provider_failure(self, provider: str) -> None:
        """Record a provider failure."""
        with self._lock:
            self._provider_failures += 1
            pm = self._providers[provider]
            pm.calls += 1
            pm.failures += 1

    def record_rate_limited(self, count: int = 1) -> None:
        """Record rate-limited requests."""
        with self._lock:
            self._rate_limited_count += count

    def record_websocket_reconnect(self) -> None:
        """Record a WebSocket reconnection."""
        with self._lock:
            self._websocket_reconnects += 1

    def record_websocket_message(self) -> None:
        """Record a WebSocket message received."""
        with self._lock:
            self._websocket_messages += 1

    def record_latency(self, operation: str, latency_seconds: float) -> None:
        """Record operation latency in seconds."""
        with self._lock:
            op = self._operations[operation]
            op.total_latency += latency_seconds
            op.latencies.append(latency_seconds)
            if len(op.latencies) > 1000:
                op.latencies = op.latencies[-500:]

    # ── Snapshot ─────────────────────────────────────────────────

    def snapshot(self) -> MetricsSnapshot:
        """Return a snapshot of all metrics."""
        with self._lock:
            # Calculate percentiles
            all_sorted = sorted(self._all_latencies)
            p95 = 0.0
            p99 = 0.0
            avg = 0.0
            if all_sorted:
                avg = (sum(all_sorted) / len(all_sorted)) * 1000.0
                p95_idx = int(len(all_sorted) * 0.95)
                p99_idx = int(len(all_sorted) * 0.99)
                p95 = all_sorted[min(p95_idx, len(all_sorted) - 1)] * 1000.0
                p99 = all_sorted[min(p99_idx, len(all_sorted) - 1)] * 1000.0

            return MetricsSnapshot(
                total_requests=self._total_requests,
                cache_hits=self._cache_hits,
                cache_misses=self._cache_misses,
                api_calls=self._api_calls,
                csv_fallbacks=self._csv_fallbacks,
                provider_failures=self._provider_failures,
                rate_limited_count=self._rate_limited_count,
                websocket_reconnects=self._websocket_reconnects,
                websocket_messages=self._websocket_messages,
                average_latency_ms=avg,
                p95_latency_ms=p95,
                p99_latency_ms=p99,
                per_operation=dict(self._operations),
                per_provider=dict(self._providers),
            )

    def reset(self) -> None:
        """Reset all metrics to zero."""
        with self._lock:
            self._operations.clear()
            self._providers.clear()
            self._total_requests = 0
            self._cache_hits = 0
            self._cache_misses = 0
            self._api_calls = 0
            self._csv_fallbacks = 0
            self._provider_failures = 0
            self._rate_limited_count = 0
            self._websocket_reconnects = 0
            self._websocket_messages = 0
            self._all_latencies.clear()
