"""Performance and observability metrics endpoints.

``GET /metrics`` exposes a JSON snapshot of:

- process/worker identity (pid + hostname — which uvicorn worker is
  answering; Sprint 11.8)
- DataService request metrics (cache hit ratio, latency, provider stats)
- scanner cache hit/miss counters
- indicator cache hit/miss/size (Sprint 11.4) and whether it is warm
- cross-process JSON-store lock contention counters (Sprint 11.8)
- per-endpoint HTTP request latency percentiles + error counts from the
  bounded request tracker (Sprint 13.1)
- configured CI performance-gate thresholds and the last recorded CI
  verdict (Sprint 13.1)
- memory usage (best-effort)
- background refresh / live feed status

This endpoint is lightweight (no heavy imports at module scope; the
DataService is the thread-safe singleton) and is intended for
dashboards and alerting.  It exposes counters only — never filesystem
paths or secrets.
"""

from __future__ import annotations

import os
import socket
import time
from pathlib import Path
from typing import Any

from fastapi import APIRouter

from src.api.timing import request_tracker
from src.cache.scanner_cache import scanner_cache
from src.logging.logger import logger
from src.utils import json_store

router = APIRouter(prefix="/metrics", tags=["Metrics"])


# Path of the CI gate artifact (repo root).  The verdict is exposed so
# the dashboard can render the last recorded gate result; a missing
# artifact simply reports ``ci_artifact=None`` (never raises).
_CI_ARTIFACT = Path(__file__).resolve().parent.parent.parent / "benchmark-ci.json"

def _memory_mb() -> float:
    """Best-effort resident memory in MiB (0.0 when unavailable)."""
    try:
        import psutil  # noqa: PLC0415

        return round(psutil.Process(os.getpid()).memory_info().rss / (1024 * 1024), 2)
    except Exception:  # noqa: BLE001 - psutil is optional
        try:
            import resource  # noqa: PLC0415  (POSIX only)

            return round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024.0, 2)
        except Exception:  # noqa: BLE001
            return 0.0


def _provider_health_snapshot(svc: Any) -> dict[str, Any]:
    """Bounded provider-reliability snapshot (Sprint 13.5).

    Reuses the existing ``ProviderHealthMonitor`` — no second health
    architecture.  Exposes per-provider scalar state only: enabled /
    success-rate / total & failed requests / consecutive failures /
    last-success age (seconds, bounded) / average latency.  ``None``
    values mean the health monitor has no record for that provider yet.
    Provider health is observability only — never a data-trust source.
    """
    try:
        monitor = svc.health_monitor
        health = monitor.get_all_health()
    except Exception as exc:  # noqa: BLE001 - observability must never raise
        logger.debug("provider health unavailable: %s", exc)
        return {}

    now = time.time()
    out: dict[str, Any] = {}
    for h in sorted(health, key=lambda x: x.name):
        last_success_age = (
            round(max(0.0, now - h.last_success), 1) if h.last_success > 0 else None
        )
        out[h.name] = {
            "enabled": h.enabled,
            "success_rate": round(h.success_rate, 2),
            "total_requests": h.total_requests,
            "successful_requests": h.successful_requests,
            "failed_requests": h.failed_requests,
            "consecutive_failures": h.consecutive_failures,
            "last_success_age_s": last_success_age,
            "average_latency_ms": round(h.average_latency_ms, 2),
        }
    return out


def _provider_reliability_snapshot(
    svc: Any, base: dict[str, Any]
) -> dict[str, Any]:
    """Augment the provider-health block with bounded reliability history.

    Sprint 13.6 §5: each provider entry gains ``state`` (HEALTHY /
    DEGRADED / UNAVAILABLE / UNKNOWN), the bounded rolling window counts
    (successes / failures / timeouts / malformed / empty) and the
    window size.  Derived from the existing ``ProviderHealthMonitor``
    reliability-history API — no second health architecture.

    Args:
        base: The Sprint 13.5 provider-health snapshot (backward
            compatible — every existing key is preserved).
    """
    try:
        monitor = svc.health_monitor
        out: dict[str, Any] = dict(base)
        for name in out:
            try:
                rel = monitor.reliability_history(name)
                out[name]["state"] = rel.get("state")
                out[name]["reliability_window"] = rel.get("window")
                out[name]["reliability_max_window"] = rel.get("max_window")
                out[name]["reliability"] = {
                    "successes": rel.get("successes"),
                    "failures": rel.get("failures"),
                    "timeouts": rel.get("timeouts"),
                    "malformed": rel.get("malformed"),
                    "empty": rel.get("empty"),
                    "failure_ratio": rel.get("failure_ratio"),
                }
            except Exception as exc:  # noqa: BLE001 - observability must never raise
                logger.debug("reliability history unavailable for %s: %s", name, exc)
        return out
    except Exception as exc:  # noqa: BLE001 - observability must never raise
        logger.debug("reliability snapshot unavailable: %s", exc)
        return base


def _calendar_status_snapshot() -> dict[str, Any]:
    """Bounded governed-calendar status (Sprint 13.5).

    Exposes version / effective window / timezone / last-validated /
    source / counts of holidays & special sessions & closures — never
    the raw dates.  Missing/unavailable calendar reports an empty dict
    (observability must never raise).
    """
    try:
        from src.data.calendar import default_calendar  # noqa: PLC0415 - lazy

        status = default_calendar().to_status()
        return {
            "version": status.get("version"),
            "timezone": status.get("timezone"),
            "effective_from": status.get("effective_from"),
            "effective_to": status.get("effective_to"),
            "last_validated": status.get("last_validated"),
            "source": status.get("source"),
            "has_provenance": bool(status.get("has_provenance")),
            "holidays": int(status.get("holidays") or 0),
            "special_sessions": int(status.get("special_sessions") or 0),
            "closures": int(status.get("closures") or 0),
            "observed_sessions": status.get("observed_sessions"),
        }
    except Exception as exc:  # noqa: BLE001 - observability must never raise
        logger.debug("calendar status unavailable: %s", exc)
        return {}


def _calendar_updates_snapshot() -> dict[str, Any]:
    """Bounded calendar update/rollback version history (Sprint 13.6).

    Reads the bounded activation history written by
    ``calendar.update_calendar`` / ``rollback_calendar`` (max
    ``CALENDAR_MAX_HISTORY`` entries, compact metadata only — version,
    effective window, source, via).  Safe when empty (never raises).
    """
    try:
        from src.data.calendar import (  # noqa: PLC0415 - lazy
            CALENDAR_MAX_HISTORY,
            calendar_version_history,
        )

        return {
            "max_history": CALENDAR_MAX_HISTORY,
            "updates": calendar_version_history(),
        }
    except Exception as exc:  # noqa: BLE001 - observability must never raise
        logger.debug("calendar update history unavailable: %s", exc)
        return {"max_history": None, "updates": []}


def _performance_gates_snapshot() -> dict[str, Any]:
    """Expose the configured CI gate thresholds and last recorded verdict.

    The gate thresholds are read from ``benchmarks.ci_gate`` (the single
    source of truth used by CI) so the dashboard never duplicates or
    drifts from the enforcement values.  The last recorded CI verdict is
    read from ``benchmark-ci.json`` when present; a missing artifact
    reports ``ci_artifact=None`` — the dashboard renders the thresholds
    and a "no CI result yet" state instead of crashing.

    Returns:
        A dict with ``thresholds`` (warm API / portfolio ratios,
        analyze p99 ms) and ``ci_artifact`` (last verdict or ``None``).
    """
    try:
        from benchmarks import ci_gate  # noqa: PLC0415 - lazy (stdlib only)

        thresholds = {
            "warm_api_ratio_max": ci_gate.WARM_API_RATIO_MAX,
            "warm_portfolio_ratio_max": ci_gate.WARM_PORTFOLIO_RATIO_MAX,
            "analyze_p99_max_ms": ci_gate.ANALYZE_P99_MAX_MS,
        }
    except Exception:  # noqa: BLE001 - observability must never raise
        logger.debug("ci_gate thresholds unavailable", exc_info=True)
        thresholds = {}

    artifact: dict[str, Any] | None = None
    try:
        if _CI_ARTIFACT.exists():
            import json  # noqa: PLC0415 - lazy

            raw = json.loads(_CI_ARTIFACT.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                artifact = {
                    "timestamp": raw.get("timestamp"),
                    "verdict": raw.get("verdict"),
                    "checks": raw.get("checks"),
                    "api_analyze_warm_p99_ms": raw.get("api_analyze_warm_p99_ms"),
                    "api_analyze_warm_cold_ratio": raw.get("api_analyze_warm_cold_ratio"),
                    "api_portfolio_warm_cold_ratio": raw.get("api_portfolio_warm_cold_ratio"),
                }
    except Exception:  # noqa: BLE001 - malformed artifact must never raise
        logger.debug("CI gate artifact unreadable", exc_info=True)
        artifact = None

    return {
        "thresholds": thresholds,
        "ci_artifact": artifact,
    }


@router.get("", status_code=200)
def metrics() -> dict[str, Any]:
    """Return a JSON metrics snapshot for observability."""
    from src.data import DataService  # noqa: PLC0415 - lazy for fast module import
    from src.indicators.cache import indicator_cache  # noqa: PLC0415 - lazy (pandas)

    svc = DataService()
    snapshot = svc.get_metrics()
    scan_stats = scanner_cache.stats()
    from src.data.quality import quality_metrics  # noqa: PLC0415 - bounded counters
    from src.data.reconciliation import reconciliation_metrics  # noqa: PLC0415 - bounded

    quality_stats = quality_metrics.snapshot()
    reconciliation_stats = reconciliation_metrics.snapshot()

    try:
        memory_mb = _memory_mb()
    except Exception:  # noqa: BLE001
        memory_mb = 0.0

    # Sprint 13.5: bounded provider-reliability snapshot from the
    # existing ProviderHealthMonitor (no second health architecture).
    provider_health = _provider_health_snapshot(svc)
    # Sprint 13.6: provider health now carries the bounded degradation
    # state + window (reliability history) per provider.
    provider_health = _provider_reliability_snapshot(svc, provider_health)
    # Sprint 13.5: bounded calendar status (version / coverage /
    # counts — never raw holiday lists) from the governed calendar.
    calendar_status = _calendar_status_snapshot()

    # Sprint 13.6: bounded reconciliation/provider incident tracker.
    from src.data.incidents import incident_tracker  # noqa: PLC0415 - bounded

    incidents = incident_tracker.snapshot(recent_limit=20)
    # Sprint 13.6: bounded data-quality trend (latest vs previous).
    from src.data.quality import quality_trends  # noqa: PLC0415 - bounded

    data_quality_trend = quality_trends.trend()
    # Sprint 13.6: derived operational system status (never raises).
    from src.data.operational_status import system_status  # noqa: PLC0415 - lazy

    system_status_block = system_status(svc)

    payload: dict[str, Any] = {
        "process": {
            # Worker identity: under ``uvicorn --workers=N`` each worker
            # is a separate process, so pid + hostname identify which
            # worker served this snapshot (no secrets, no filesystem
            # paths).
            "pid": os.getpid(),
            "hostname": socket.gethostname(),
        },
        "indicator_cache": indicator_cache.stats(),
        "json_store": json_store.lock_stats(),
        "memory_mb": memory_mb,
        # Bounded per-endpoint HTTP latency + error tracker (Sprint 13.1).
        "api_requests": request_tracker.snapshot(),
        # Configured CI gate thresholds + last verdict (Sprint 13.1).
        "performance_gates": _performance_gates_snapshot(),
        "metrics": {
            "total_requests": snapshot.total_requests,
            "cache_hits": snapshot.cache_hits,
            "cache_misses": snapshot.cache_misses,
            "cache_hit_rate": round(snapshot.cache_hit_rate, 2),
            "api_calls": snapshot.api_calls,
            "csv_fallbacks": snapshot.csv_fallbacks,
            "provider_failures": snapshot.provider_failures,
            "average_latency_ms": round(snapshot.average_latency_ms, 3),
            "p95_latency_ms": round(snapshot.p95_latency_ms, 3),
            "p99_latency_ms": round(snapshot.p99_latency_ms, 3),
        },
        "per_operation": {
            op: {
                "calls": m.calls,
                "cache_hits": m.cache_hits,
                "cache_misses": m.cache_misses,
                "average_latency_ms": round(m.average_latency_ms, 3),
            }
            for op, m in sorted(snapshot.per_operation.items())
        },
        "per_provider": {
            name: {
                "calls": p.calls,
                "success_rate": round(p.success_rate, 2),
                "average_latency_ms": round(p.average_latency_ms, 3),
            }
            for name, p in sorted(snapshot.per_provider.items())
        },
        "scanner_cache": {
            "dataframe_hits": scan_stats["dataframe_hits"],
            "dataframe_misses": scan_stats["dataframe_misses"],
            "analysis_hits": scan_stats["analysis_hits"],
            "analysis_misses": scan_stats["analysis_misses"],
            "dataframe_entries": scan_stats["dataframe_entries"],
            "analysis_entries": scan_stats["analysis_entries"],
            "cache_hit_rate": round(scanner_cache.cache_hit_rate, 2),
        },
        # Sprint 13.3 Phase 13: bounded data-quality counters (scalar
        # totals only — never per-record history).  Lets operators see
        # how much of the corpus is valid/invalid/suspicious, how many
        # duplicates/conflicts were detected, and how many provider
        # failures / timeouts / fallbacks the hybrid chain performed.
        "data_quality": quality_stats,
        # Sprint 13.4 Phase 9/20: bounded cross-provider reconciliation
        # counters (provider requests/successes/failures/timeouts,
        # fallbacks, reconciliation checks, agreements, minor/material
        # disagreements, mapping conflicts, quarantined conflicts) —
        # scalar totals only, preserving every existing key.
        "reconciliation": reconciliation_stats,
        # Sprint 13.5: bounded provider-reliability observability
        # (enabled / success-rate / failure counts / last-success age /
        # latency per registered provider) — scalar per-provider only.
        "provider_health": provider_health,
        # Sprint 13.5: bounded governed-calendar status (version,
        # effective window, timezone, last-validated, counts of
        # holidays/special-sessions/closures).  Never raw dates.
        "calendar": calendar_status,
        # Sprint 13.6: bounded incident tracking (recent events capped at
        # 20; aggregate counts scalar-only).  Never raw provider payloads.
        "incidents": incidents,
        # Sprint 13.6: bounded data-quality trend (latest vs previous
        # corpus gate; empty-safe).
        "data_quality_trend": data_quality_trend,
        # Sprint 13.6: bounded calendar update/rollback version history
        # (max CALENDAR_MAX_HISTORY entries; compact metadata only).
        "calendar_updates": _calendar_updates_snapshot(),
        # Sprint 13.6: derived operational production-readiness status.
        # Built from existing state only — never raises.
        "system_status": system_status_block,
        "status": {
            "background_refresh_running": svc.is_background_refresh_running(),
            "live_feed_connected": svc.is_live_feed_connected(),
        },
    }
    # Sprint 12.0 (Phase 7-8): best-effort cross-worker aggregation.
    # The serving worker self-reports its local counters into the shared
    # JSON store and the payload is enriched with ``workers`` and
    # ``aggregate`` blocks.  Observability-only: any failure returns the
    # local-only payload unchanged, so metrics can never break the API.
    try:
        from src.utils.worker_metrics import aggregate_worker_metrics  # noqa: PLC0415 - lazy

        payload = aggregate_worker_metrics(payload)
    except Exception as exc:  # noqa: BLE001 - best-effort observability
        logger.debug("Cross-worker metrics aggregation unavailable: %s", exc)
    logger.debug("Metrics snapshot served")
    return payload
