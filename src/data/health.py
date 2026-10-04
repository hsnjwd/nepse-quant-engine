"""Provider health monitor with automatic disable/re-enable.

``ProviderHealthMonitor`` tracks per-provider latency, uptime, failure rate,
and average response time.  Providers with excessive failures are
automatically disabled and later re-enabled after a recovery period.

Sprint 13.6 adds **bounded reliability history** and **degradation
classification** on top of the existing scalar health state (no second
health architecture):

- ``record_*`` calls now also append a compact outcome to a bounded
  rolling window per provider (``outcome_window`` outcomes, default 100).
- ``degradation_state(name)`` derives a HEALTHY / DEGRADED /
  UNAVAILABLE classification from the window (elevated failure ratio,
  repeated timeouts, repeated malformed/empty responses).  A single
  failure never degrades a provider; the model is deliberately
  non-aggressive (Sprint 13.6 §6).
- ``reliability_history(name)`` exposes the bounded window as a compact
  snapshot (counts only — never raw timestamps/payloads).
- Outcome kinds are distinguished: success / failure / timeout /
  malformed / empty, so degradation can react to *which* failure mode
  dominates (timeouts and malformed/empty responses are degradation
  signals without hammering the disable threshold).
"""

from __future__ import annotations

import logging
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Callable

logger = logging.getLogger(__name__)

# ── Degradation states (Sprint 13.6 §6) ───────────────────────────
HEALTHY = "HEALTHY"
DEGRADED = "DEGRADED"
UNAVAILABLE = "UNAVAILABLE"
UNKNOWN = "UNKNOWN"

# Outcome kinds recorded into the bounded rolling window.
OUTCOME_SUCCESS = "success"
OUTCOME_FAILURE = "failure"
OUTCOME_TIMEOUT = "timeout"
OUTCOME_MALFORMED = "malformed"
OUTCOME_EMPTY = "empty"

# Thresholds (proportion of the *window*) used by ``degradation_state``.
# A provider is DEGRADED when any trigger fires:
#   - failure-ratio trigger: (failures + timeouts + malformed) / window
#     >= degraded_failure_ratio
#   - timeout trigger:       timeouts in window >= degraded_timeout_count
#   - malformed trigger:     malformed in window >= degraded_malformed_count
#   - empty trigger:         empty in window >= degraded_empty_count
DEFAULT_DEGRADED_FAILURE_RATIO = 0.5
DEFAULT_DEGRADED_TIMEOUT_COUNT = 5
DEFAULT_DEGRADED_MALFORMED_COUNT = 5
DEFAULT_DEGRADED_EMPTY_COUNT = 5
DEFAULT_MIN_WINDOW_SAMPLES = 10


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
    # ── Sprint 13.6 reliability-history / degradation knobs (bounded) ──
    outcome_window: int = 100        # rolling outcome window per provider
    degraded_failure_ratio: float = DEFAULT_DEGRADED_FAILURE_RATIO
    degraded_timeout_count: int = DEFAULT_DEGRADED_TIMEOUT_COUNT
    degraded_malformed_count: int = DEFAULT_DEGRADED_MALFORMED_COUNT
    degraded_empty_count: int = DEFAULT_DEGRADED_EMPTY_COUNT
    min_window_samples: int = DEFAULT_MIN_WINDOW_SAMPLES


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
        # Sprint 13.6: bounded rolling outcome window per provider.  The
        # deque is capped at ``config.outcome_window`` (default 100) so
        # reliability history is bounded regardless of traffic volume.
        self._outcomes: dict[str, deque[str]] = {}
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
                self._outcomes[name] = deque(maxlen=self._config.outcome_window)
                logger.info("[HealthMonitor] Registered provider: %s", name)

    def unregister(self, name: str) -> None:
        """Remove a provider from health tracking."""
        with self._lock:
            self._health.pop(name, None)
            self._latency_samples.pop(name, None)
            self._outcomes.pop(name, None)
            logger.info("[HealthMonitor] Unregistered provider: %s", name)

    # ── Recording ────────────────────────────────────────────────

    def _append_outcome(self, name: str, outcome: str) -> None:
        """Append *outcome* to the bounded rolling window for *name*."""
        window = self._outcomes.setdefault(name, deque(maxlen=self._config.outcome_window))
        window.append(outcome)

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
            self._append_outcome(name, OUTCOME_SUCCESS)

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

    def record_failure(self, name: str, outcome: str = OUTCOME_FAILURE) -> None:
        """Record a failed request for *name*.

        Args:
            outcome: Failure kind appended to the bounded reliability
                window — ``failure`` (default), ``timeout``,
                ``malformed`` or ``empty`` (Sprint 13.6).  A generic
                failure counts towards both the disable threshold and
                the degradation model.
        """
        with self._lock:
            h = self._health.get(name)
            if h is None:
                return
            h.total_requests += 1
            h.failed_requests += 1
            h.consecutive_failures += 1
            h.last_failure = time.time()
            self._append_outcome(name, outcome)

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

    def record_timeout(self, name: str) -> None:
        """Record a timeout for *name* (distinct outcome, Sprint 13.6)."""
        self.record_failure(name, outcome=OUTCOME_TIMEOUT)

    def record_malformed(self, name: str) -> None:
        """Record a malformed/validation-rejected response for *name*."""
        self.record_failure(name, outcome=OUTCOME_MALFORMED)

    def record_empty(self, name: str) -> None:
        """Record an empty (no-data) response for *name*.

        Empty responses are degradation signals but do **not** count as
        hard failures against the disable threshold (the provider may
        legitimately have nothing for a symbol).  They still enter the
        bounded outcome window so ``degradation_state`` can react to
        repeated emptiness.
        """
        with self._lock:
            h = self._health.get(name)
            if h is None:
                return
            h.total_requests += 1
            h.last_failure = time.time()
            self._append_outcome(name, OUTCOME_EMPTY)

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

    # ── Reliability history / degradation (Sprint 13.6 §5/§6) ────

    def outcome_window(self, name: str) -> list[str]:
        """Return a copy of the bounded outcome window for *name*."""
        with self._lock:
            return list(self._outcomes.get(name, deque()))

    def _window_counts(self, name: str) -> dict[str, int]:
        window = self._outcomes.get(name, deque())
        counts: dict[str, int] = {
            OUTCOME_SUCCESS: 0,
            OUTCOME_FAILURE: 0,
            OUTCOME_TIMEOUT: 0,
            OUTCOME_MALFORMED: 0,
            OUTCOME_EMPTY: 0,
        }
        for outcome in window:
            if outcome in counts:
                counts[outcome] += 1
        return counts

    def degradation_state(self, name: str) -> str:
        """Classify *name* as HEALTHY / DEGRADED / UNAVAILABLE / UNKNOWN.

        Sprint 13.6 §6 — the degradation model is deliberately
        non-aggressive:

        - UNAVAILABLE when the provider is disabled (or was never
          registered and a request already failed for it).
        - HEALTHY when the window has fewer than ``min_window_samples``
          samples (never degrade from a single failure) or none of the
          triggers fire.
        - DEGRADED when any trigger fires: elevated failure ratio over
          the window, repeated timeouts, repeated malformed responses,
          or repeated empty responses.

        State transitions are observable via ``reliability_history`` and
        ``/metrics.provider_health`` (which now carries ``state``).
        """
        with self._lock:
            h = self._health.get(name)
            if h is None:
                return UNKNOWN
            if not h.enabled:
                return UNAVAILABLE
            window = list(self._outcomes.get(name, deque()))
            if len(window) < self._config.min_window_samples:
                return HEALTHY
            counts = self._window_counts(name)
            total = len(window)
            hard = counts[OUTCOME_FAILURE] + counts[OUTCOME_TIMEOUT] + counts[OUTCOME_MALFORMED]
            ratio = hard / total
            if (
                ratio >= self._config.degraded_failure_ratio
                or counts[OUTCOME_TIMEOUT] >= self._config.degraded_timeout_count
                or counts[OUTCOME_MALFORMED] >= self._config.degraded_malformed_count
                or counts[OUTCOME_EMPTY] >= self._config.degraded_empty_count
            ):
                return DEGRADED
            return HEALTHY

    def reliability_history(self, name: str) -> dict[str, Any]:
        """Compact, bounded reliability snapshot for *name* (Sprint 13.6 §5).

        Counts only — never raw timestamps or payloads.  Includes the
        current degradation state, the window length (bounded), per-kind
        counts, the derived failure ratio and the average latency.
        """
        with self._lock:
            counts = self._window_counts(name)
            total = sum(counts.values())
            hard = counts[OUTCOME_FAILURE] + counts[OUTCOME_TIMEOUT] + counts[OUTCOME_MALFORMED]
            ratio = (hard / total) if total else 0.0
            h = self._health.get(name)
            return {
                "provider": name,
                "state": self.degradation_state(name),
                "window": total,
                "max_window": self._config.outcome_window,
                "successes": counts[OUTCOME_SUCCESS],
                "failures": counts[OUTCOME_FAILURE],
                "timeouts": counts[OUTCOME_TIMEOUT],
                "malformed": counts[OUTCOME_MALFORMED],
                "empty": counts[OUTCOME_EMPTY],
                "failure_ratio": round(ratio, 4),
                "average_latency_ms": round(h.average_latency_ms, 2) if h else 0.0,
                "enabled": h.enabled if h else True,
            }

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
