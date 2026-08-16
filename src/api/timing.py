"""Bounded, thread-safe API request instrumentation (Sprint 13.1, Phase 2).

The FastAPI request-timing middleware (``src/api/main.py``) logs a
structured timing line per request but retains nothing — ``/metrics``
could not answer "which endpoint is slow / how many requests errored".
This module adds a tiny, in-process, *bounded* tracker that records one
row per HTTP request (endpoint, status, duration) and exposes aggregate
percentiles per endpoint plus overall counts.

Design constraints (Sprint 13.1 Phase 2):

- **cheap on the hot path** — a single ``RLock`` + deque append + two
  counter increments per request (~µs); no sorting, no persistence.
- **never crashes an endpoint** — ``record()`` swallows every error so
  instrumentation can never break a request or the middleware chain.
- **bounded** — per-endpoint latency windows are capped (LRU-drop
  oldest via ``deque(maxlen=...)``); no unbounded request history.
- **multi-worker safe** — each uvicorn worker is a separate process
  with its own tracker; ``/metrics`` already self-reports worker
  identity (pid/hostname) alongside this local snapshot, so a fleet
  view is assembled by the existing cross-worker aggregation.
- **reused, not duplicated** — this feeds the existing ``/metrics``
  payload; it is not a second metrics framework.
"""

from __future__ import annotations

import threading
import time
from collections import defaultdict, deque
from typing import Any

# Bounded latency windows per endpoint (samples kept in memory).
_PER_ENDPOINT_MAXLEN = 2000

# Endpoints that should never be instrumented (health probes / the
# metrics endpoint itself would skew latency percentiles with trivial
# requests and create feedback noise).
_EXCLUDED_PATHS = {"/", "/metrics", "/_stcore/health"}


class ApiRequestTracker:
    """Bounded, thread-safe per-endpoint HTTP latency tracker.

    Usage::

        tracker = ApiRequestTracker()
        tracker.record("/analyze/NABIL", 200, 12.3)
        tracker.record("/analyze/NABIL", 500, 300.0)
        snapshot = tracker.snapshot()   # dict, JSON-serialisable
        tracker.reset()                 # tests / admin
    """

    def __init__(self, maxlen: int = _PER_ENDPOINT_MAXLEN) -> None:
        self._maxlen = max(1, maxlen)
        self._lock = threading.RLock()
        # endpoint -> deque of duration_ms samples (bounded)
        self._latencies: dict[str, deque[float]] = defaultdict(
            lambda: deque(maxlen=self._maxlen)
        )
        # endpoint -> counters (ints are inherently bounded)
        self._counts: dict[str, int] = defaultdict(int)
        self._errors: dict[str, int] = defaultdict(int)

    # ── Recording ────────────────────────────────────────────────

    def record(self, endpoint: str, status: int, duration_ms: float) -> None:
        """Record one HTTP request. Never raises.

        Args:
            endpoint: Request path (e.g. ``/analyze/NABIL``).
            status: HTTP status code returned.
            duration_ms: Wall-clock request duration in milliseconds.
        """
        try:
            path = str(endpoint or "unknown")
            if path in _EXCLUDED_PATHS:
                return
            dur = float(duration_ms)
            if dur < 0 or dur != dur:  # negative or NaN -> ignore
                dur = 0.0
            code = int(status)
            with self._lock:
                self._latencies[path].append(dur)
                self._counts[path] += 1
                if code >= 400:
                    self._errors[path] += 1
        except Exception:  # noqa: BLE001 - instrumentation must never raise
            pass

    # ── Snapshot ─────────────────────────────────────────────────

    @staticmethod
    def _percentile(sorted_samples: list[float], pct: float) -> float:
        if not sorted_samples:
            return 0.0
        idx = min(len(sorted_samples) - 1, int(len(sorted_samples) * pct))
        return round(sorted_samples[idx], 3)

    def snapshot(self) -> dict[str, Any]:
        """Return a JSON-serialisable, bounded metrics snapshot.

        Returns:
            A dict with overall ``total_requests`` / ``error_requests`` /
            ``average_ms`` / ``p95_ms`` / ``p99_ms`` plus a
            ``per_endpoint`` block keyed by path with the same fields
            per endpoint.
        """
        with self._lock:
            per_endpoint: dict[str, dict[str, Any]] = {}
            for path in sorted(self._latencies):
                samples = sorted(self._latencies[path])
                per_endpoint[path] = {
                    "requests": self._counts[path],
                    "errors": self._errors[path],
                    "error_rate": round(
                        self._errors[path] / self._counts[path], 4
                    ) if self._counts[path] else 0.0,
                    "average_ms": round(sum(samples) / len(samples), 3) if samples else 0.0,
                    "p50_ms": self._percentile(samples, 0.50),
                    "p95_ms": self._percentile(samples, 0.95),
                    "p99_ms": self._percentile(samples, 0.99),
                }

            all_samples = sorted(
                sample
                for dq in self._latencies.values()
                for sample in dq
            )
            total = sum(self._counts.values())
            errors = sum(self._errors.values())

            return {
                "total_requests": total,
                "error_requests": errors,
                "error_rate": round(errors / total, 4) if total else 0.0,
                "average_ms": round(sum(all_samples) / len(all_samples), 3) if all_samples else 0.0,
                "p50_ms": self._percentile(all_samples, 0.50),
                "p95_ms": self._percentile(all_samples, 0.95),
                "p99_ms": self._percentile(all_samples, 0.99),
                "per_endpoint": per_endpoint,
            }

    def reset(self) -> None:
        """Drop all recorded samples and counters (tests / admin)."""
        with self._lock:
            self._latencies.clear()
            self._counts.clear()
            self._errors.clear()


# Module-level singleton shared by the FastAPI middleware and /metrics.
request_tracker = ApiRequestTracker()
