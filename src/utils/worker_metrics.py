"""Best-effort cross-worker metrics aggregation (Sprint 12.0, Phase 7-8).

Under ``uvicorn --workers=N`` (the Docker ``api`` stage runs 2) each
worker is a separate OS process with its own in-memory counters
(indicator-cache hits/misses, JSON-store lock contention).  ``/metrics``
reports worker-local values — useful, but no single endpoint can answer
"how many workers are active / is the cache warm across the fleet".

This module adds the *lightest* aggregation that satisfies the sprint
constraints, without introducing a new dependency:

* No Prometheus / StatsD / Redis exists in the project (audited —
  SQLite is portfolio-storage only), so the sprint's own rule applies:
  prefer a minimal stdlib-compatible solution using existing
  infrastructure.
* Each worker self-reports a small record (pid, hostname, last-seen
  timestamp, local indicator-cache and JSON-store counters) into a
  shared JSON store via the existing transactional ``update_json``
  (atomic + corruption-tolerant + cross-process lock).
* Any worker serving ``/metrics`` reads the store, marks records whose
  ``last_seen`` is within ``WORKER_METRICS_TTL_S`` as *active*, and
  sums the counter families into an ``aggregate`` block while keeping
  the worker-local blocks intact.

Design constraints (Phase 8):

- best-effort observability — a failure to read/write the store is
  logged and swallowed; metrics can never break analysis or the API;
- atomic writes via ``update_json`` — metrics cannot corrupt state and
  cannot litter ``.tmp``/``.lock`` files (the transaction machinery
  cleans both up);
- TTL-based liveness with stale-record tolerance — a crashed worker's
  record simply stops being counted after ``WORKER_METRICS_TTL_S``; it
  is never deleted and never errors;
- no secrets, credentials or filesystem paths are ever written to the
  store (only pid / hostname / numeric counters);
- the write path is throttled (at most one report per worker per
  ``_REPORT_MIN_INTERVAL_S``) so metrics collection itself never
  becomes a write hot-spot or a lock-contention source.
"""

from __future__ import annotations

import os
import socket
import threading
import time
from typing import Any

from src.config import WORKER_METRICS_FILE, WORKER_METRICS_TTL_S
from src.logging.logger import logger
from src.utils.json_store import update_json

# Throttle: a worker writes its record at most once per interval, so
# repeated /metrics polling cannot turn metrics into a write amplifier.
_REPORT_MIN_INTERVAL_S = 5.0

_report_lock = threading.Lock()
_last_report_ts: float = 0.0


def _self_record(local: dict[str, Any]) -> dict[str, Any]:
    """Build this worker's shareable record from the local metrics block.

    Only numeric counters and process identity are persisted — never
    secrets, credentials, or filesystem paths.  The worker identity is
    taken from the ``process.pid`` block (which ``/metrics`` always
    populates with the real ``os.getpid()``), falling back to
    ``os.getpid()`` so callers that pass a partial payload still get a
    valid record.  Using the payload's pid as the store key makes the
    module trivially testable (a test can simulate several workers by
    passing distinct pids) without changing production behaviour.
    """
    indicator = local.get("indicator_cache") or {}
    lock = local.get("json_store") or {}
    proc = local.get("process") or {}
    pid = proc.get("pid", os.getpid())
    return {
        "pid": pid,
        "hostname": socket.gethostname(),
        "last_seen": time.time(),
        "indicator_cache": {
            "hits": int(indicator.get("hits", 0) or 0),
            "misses": int(indicator.get("misses", 0) or 0),
            "entries": int(indicator.get("entries", 0) or 0),
            "hit_rate": float(indicator.get("hit_rate", 0.0) or 0.0),
        },
        "json_store": {
            "retries": int(lock.get("retries", 0) or 0),
            "stale_recoveries": int(lock.get("stale_recoveries", 0) or 0),
            "timeouts": int(lock.get("timeouts", 0) or 0),
        },
        # Sprint 13.2: per-worker HTTP request totals from the bounded
        # request tracker (``api_requests`` block of ``/metrics``).
        # Each worker counts only the requests *it* served, so summing
        # across active workers is a true aggregate — never a double
        # count.  Error totals are included so a fleet error rate is
        # observable.
        "api_requests": {
            "total": int((local.get("api_requests") or {}).get("total_requests", 0) or 0),
            "errors": int((local.get("api_requests") or {}).get("error_requests", 0) or 0),
        },
    }


def _report_worker(local: dict[str, Any]) -> None:
    """Best-effort upsert of this worker's record (throttled)."""
    global _last_report_ts
    now = time.time()
    with _report_lock:
        if now - _last_report_ts < _REPORT_MIN_INTERVAL_S:
            return
        _last_report_ts = now
    record = _self_record(local)

    def _mutate(state: Any) -> Any:
        if not isinstance(state, dict):
            state = {}
        state[str(record["pid"])] = record
        return state

    try:
        update_json(WORKER_METRICS_FILE, _mutate, {}, log_name="WorkerMetrics")
    except Exception as exc:  # noqa: BLE001 - best-effort observability
        logger.debug("worker-metrics self-report failed (best-effort): %s", exc)


def aggregate_worker_metrics(local: dict[str, Any]) -> dict[str, Any]:
    """Enrich a worker-local metrics payload with cross-worker aggregates.

    Self-reports this worker (throttled, best-effort), then reads the
    shared store and computes:

    - ``workers.active`` / ``workers.known`` — liveness by TTL;
    - ``aggregate.hits`` / ``aggregate.misses`` / ``aggregate.hit_rate``
      — summed indicator-cache counters across active workers;
    - ``aggregate.lock_retries`` / ``aggregate.stale_recoveries`` /
      ``aggregate.timeouts`` — summed JSON-store lock counters.

    Worker-local blocks (``process``, ``indicator_cache``,
    ``json_store``, ...) are preserved unchanged.  Never raises: on any
    store failure the payload is returned as-is (local-only), so
    observability degradation can never break the API.

    Args:
        local: The worker-local ``/metrics`` payload dict.

    Returns:
        The enriched payload dict.
    """
    _report_worker(local)

    records: dict[str, Any] = {}
    try:
        from src.utils.json_store import load_json  # noqa: PLC0415 - lazy

        state = load_json(WORKER_METRICS_FILE, {}, log_name="WorkerMetrics")
        if isinstance(state, dict):
            records = state
    except Exception as exc:  # noqa: BLE001 - best-effort observability
        logger.debug("worker-metrics read failed (best-effort): %s", exc)
        return dict(local)

    now = time.time()
    active: list[dict[str, Any]] = []
    known: list[dict[str, Any]] = []
    for pid_str, rec in records.items():
        if not isinstance(rec, dict):
            continue
        try:
            last_seen = float(rec.get("last_seen", 0.0))
        except (TypeError, ValueError):
            last_seen = 0.0
        entry = {
            "pid": pid_str,
            "hostname": rec.get("hostname", ""),
            "last_seen": round(last_seen, 2),
        }
        known.append(entry)
        if now - last_seen <= WORKER_METRICS_TTL_S:
            active.append(rec)

    def _sum(family: str, key: str) -> int:
        total = 0
        for rec in active:
            block = rec.get(family)
            if isinstance(block, dict):
                try:
                    total += int(block.get(key, 0) or 0)
                except (TypeError, ValueError):
                    pass
        return total

    hits = _sum("indicator_cache", "hits")
    misses = _sum("indicator_cache", "misses")
    hit_rate = round(hits / (hits + misses), 3) if (hits + misses) else 0.0

    enriched = dict(local)
    enriched["workers"] = {
        "active": len(active),
        "known": known,
        "ttl_s": WORKER_METRICS_TTL_S,
    }
    enriched["aggregate"] = {
        "hits": hits,
        "misses": misses,
        "hit_rate": hit_rate,
        "lock_retries": _sum("json_store", "retries"),
        "stale_recoveries": _sum("json_store", "stale_recoveries"),
        "timeouts": _sum("json_store", "timeouts"),
        # Sprint 13.2: fleet request totals = sum of every active
        # worker's own counts (each request is served by exactly one
        # worker, so the sum is the true fleet total — no double
        # counting).  Backward-compatible: new keys only.
        "requests": _sum("api_requests", "total"),
        "request_errors": _sum("api_requests", "errors"),
    }
    return enriched
