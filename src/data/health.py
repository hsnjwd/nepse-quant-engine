"""Provider health monitor with automatic disable/re-enable.

``ProviderHealthMonitor`` tracks per-provider latency, uptime, failure rate,
and average response time.  Providers with excessive failures are
automatically disabled and later re-enabled after a recovery period.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable

logger = logging.getLogger(__name__)


@dataclass
class ProviderHealth:
    """Health snapshot for a single provider."""

    name: str = ""
    enabled: bool = True
    latency_ms: float = 0.0
    uptime_pct: float = 100.0
    total_requests: int = 0
    successful_requests: int = 0
    failed_requests: int = 0
    consecutive_failures: int = 0
    average_latency_ms: float = 0.0
    last_success: float = 0.0
    last_failure: float = 0.0
    disabled_at: float = 0.0
    recheck_at: float = 0.0

    @property
    def success_rate(self) -> float:
        if self.total_requests == 0:
            return 100.0
        return (self.successful_requests / self.total_requests) * 100.0

    @property
    def is_disabled(self) -> bool:
        return not self.enabled


@dataclass
class HealthCheckConfig:
    """Configuration for health monitor behaviour."""

    failure_threshold: int = 5       # consecutive failures before disable
    recovery_period: float = 300.0   # seconds before re-enable attempt
    success_recovery_count: int = 3  # successes needed to clear disabled state
    check_interval: float = 60.0     # how often to recheck disabled providers
    latency_window: int = 100        # rolling window for avg latency
    enabled: bool = True


class ProviderHealthMonitor:
    """Tracks provider health, auto-disables failing providers, re-enables later.

    Usage::

        monitor = ProviderHealthMonitor()
        monitor.register("api")
        monitor.record_success("api", latency_ms=150)
        monitor.record_failure("api")
        if monitor.is_healthy("api"):
            ...
    """

    def __init__(self, config: HealthCheckConfig | None = None) -> None:
        self._config = config or HealthCheckConfig()
        self._health: dict[str, ProviderHealth] = {}
        self._latency_samples: dict[str, list[float]] = {}
        self._lock = threading.RLock()
        self._recheck_timer: threading.Thread | None = None
        self._recheck_stop = threading.Event()

    # ── Registration ─────────────────────────────────────────────

    def register(self, name: str) -> None:
        """Register a provider for health tracking."""
        with self._lock:
            if name not in self._health:
                self._health[name] = ProviderHealth(name=name)
                self._latency_samples[name] = []
                logger.info("[HealthMonitor] Registered provider: %s", name)

    def unregister(self, name: str) -> None:
        """Remove a provider from health tracking."""
        with self._lock:
            self._health.pop(name, None)
            self._latency_samples.pop(name, None)
            logger.info("[HealthMonitor] Unregistered provider: %s", name)

    # ── Recording ────────────────────────────────────────────────

    def record_success(self, name: str, latency_ms: float = 0.0) -> None:
        """Record a successful request for *name*."""
        with self._lock:
            h = self._health.get(name)
            if h is None:
                return
            h.total_requests += 1
            h.successful_requests += 1
            h.consecutive_failures = 0
            h.last_success = time.time()

            # Track latency
            if latency_ms > 0:
                samples = self._latency_samples.setdefault(name, [])
                samples.append(latency_ms)
                if len(samples) > self._config.latency_window:
                    samples.pop(0)
                h.average_latency_ms = sum(samples) / len(samples)
                h.latency_ms = latency_ms

            # Clear disabled state after enough successes
            if h.is_disabled and h.consecutive_failures == 0:
                if h.successful_requests >= self._config.success_recovery_count:
                    h.enabled = True
                    h.disabled_at = 0.0
                    h.recheck_at = 0.0
                    logger.info("[HealthMonitor] Re-enabled provider: %s", name)

    def record_failure(self, name: str) -> None:
        """Record a failed request for *name*."""
        with self._lock:
            h = self._health.get(name)
            if h is None:
                return
            h.total_requests += 1
            h.failed_requests += 1
            h.consecutive_failures += 1
            h.last_failure = time.time()

            # Auto-disable if threshold exceeded
            if (not h.is_disabled
                    and h.consecutive_failures >= self._config.failure_threshold):
                h.enabled = False
                h.disabled_at = time.time()
                h.recheck_at = time.time() + self._config.recovery_period
                logger.warning(
                    "[HealthMonitor] Disabled provider %s (%d consecutive failures)",
                    name, h.consecutive_failures,
                )

    # ── Queries ──────────────────────────────────────────────────

    def is_healthy(self, name: str) -> bool:
        """Check if *name* is healthy and should be used."""
        with self._lock:
            h = self._health.get(name)
            if h is None:
                return True  # unknown providers are assumed healthy
            if not h.enabled:
                # Check if it's time to re-enable for a trial
                if h.recheck_at > 0 and time.time() >= h.recheck_at:
                    h.enabled = True
                    h.consecutive_failures = 0
                    logger.info("[HealthMonitor] Trial re-enable for: %s", name)
                    return True
                return False
            return True

    def get_health(self, name: str) -> ProviderHealth | None:
        """Return the health snapshot for *name*."""
        with self._lock:
            return self._health.get(name)

    def get_all_health(self) -> list[ProviderHealth]:
        """Return health snapshots for all registered providers."""
        with self._lock:
            return list(self._health.values())

    def get_healthy_providers(self, names: list[str]) -> list[str]:
        """Filter *names* to only those that are currently healthy."""
        return [n for n in names if self.is_healthy(n)]

    # ── Management ───────────────────────────────────────────────

    def enable(self, name: str) -> None:
        """Manually enable a provider."""
        with self._lock:
            h = self._health.get(name)
            if h:
                h.enabled = True
                h.disabled_at = 0.0
                h.recheck_at = 0.0
                h.consecutive_failures = 0
                logger.info("[HealthMonitor] Manually enabled: %s", name)

    def disable(self, name: str) -> None:
        """Manually disable a provider."""
        with self._lock:
            h = self._health.get(name)
            if h:
                h.enabled = False
                h.disabled_at = time.time()
                logger.info("[HealthMonitor] Manually disabled: %s", name)

    def reset(self, name: str) -> None:
        """Reset health stats for *name*."""
        with self._lock:
            self._health.pop(name, None)
            self._latency_samples.pop(name, None)

    # ── Stats ────────────────────────────────────────────────────

    @property
    def disabled_count(self) -> int:
        with self._lock:
            return sum(1 for h in self._health.values() if h.is_disabled)

    @property
    def healthy_count(self) -> int:
        with self._lock:
            return sum(1 for h in self._health.values() if h.enabled)
