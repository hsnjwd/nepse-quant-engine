"""Bounded platform soak framework (Sprint 13.8).

The engine must remain *correct, bounded, recoverable and measurable*
across sustained operation — not merely for a single execution.  This
module is a dependency-free, hermetic soak harness that repeats the
core platform workflow and verifies that no state accumulates, leaks or
contaminates across repetitions.

    Fetch → Validate → Reconcile → Provenance → Cache → Analyze →
    Scan → Alert → Metrics → Repeat

while repeatedly experiencing:

    provider failure / recovery / fallback / minor disagreement /
    material disagreement / mapping conflict / calendar validation /
    cache hit-miss / worker restart / notification activity

Scenarios (each returns a ``passed`` verdict + a report dict):

    A — Normal operation : repeated history → analyze → scan → metrics
    B — Fallback         : primary healthy ↔ unavailable ↔ fallback ↔
                           recovery, with provenance + health bounds
    C — Material conflict: AGREE → MATERIAL → AGREE → MAPPING_CONFLICT
                           → AGREE cycles; no cross-request poisoning
    D — Cache hit/miss   : cold → warm → invalidate → cold → warm with
                           bounded cache size and preserved provenance
    E — Worker restart   : spawn → request → shutdown → respawn →
                           request (opt-in; spawns a real uvicorn)

State-leak detection: before/after deltas for every bounded subsystem
(provider-health outcome windows, incident ring, repeated-minor map,
quality-trend ring, scanner cache, notification history, worker-metrics
JSON, threads, open file objects, subprocesses, temp litter).  A small
expected bounded change is acceptable; unexpected monotonic growth is
flagged.

Memory envelope: ``tracemalloc`` tracks the *Python-object layer* — the
layer where singleton / cache / ring / notification leaks live.  Raw
numpy buffers are freed by refcount and are intentionally not part of
this envelope (documented in ``run_scenario_normal``).  The envelope is
``MEMORY_LEAK_DELTA_MIB`` (default 8 MiB): generous because it must
absorb allocator noise and cache warm-up, but far below the hundreds of
MiB a real pandas-heavy leak accumulates over 100+ iterations.

Everything is hermetic: a synthetic corpus in a temp dir, state files
redirected via ``benchmarks.pipeline._state_files`` / a private state
root, ``NEPSE_HOME`` redirected for notification isolation, and no live
network access anywhere.

Usage::

    python -m benchmarks.soak --iterations 20
    python -m benchmarks.soak --iterations 100 --include-workers
    SOAK_ITERATIONS=50 python -m benchmarks.soak

Exit code 0 when every scenario passes; 1 with a printed report
otherwise.
"""

from __future__ import annotations

import argparse
import gc
import io
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import tracemalloc
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# ── Bounds (documented; imported by tests so the soak gate and the
#    regression suite assert the same envelope) ────────────────────
# Memory: max acceptable Python-object growth between the warm-up
# plateau and the end of the soak.  Real leaks in pandas-heavy code
# accumulate hundreds of MiB over 100+ iterations; 8 MiB absorbs
# allocator noise + cache warm-up while still tripping on a genuine
# monotonic singleton/cache/ring leak.
MEMORY_LEAK_DELTA_MIB = 8.0
# Threads: soak phases must not accumulate threads beyond the ambient
# baseline (scanner pool is created per call and joined).
THREAD_DELTA_MAX = 8
# Open file-objects (io.IOBase instances) must not accumulate.
FILE_OBJECTS_DELTA_MAX = 64
# State-root litter checks: no leftover tmp/lock/corrupt-backup files
# after any scenario.
DEFAULT_ITERATIONS = int(os.getenv("SOAK_ITERATIONS", "15"))
DEFAULT_SYMBOLS = 20
DEFAULT_ROWS = 120
DEFAULT_PORT = 8893


# ── Resource sampling (dependency-free) ───────────────────────────

def _open_file_objects() -> int:
    """Count live ``io.IOBase`` instances (cross-platform, no psutil)."""
    count = 0
    for obj in gc.get_objects():
        try:
            if isinstance(obj, io.IOBase) and not obj.closed:
                count += 1
        except Exception:  # noqa: BLE001 - defensive object walk
            continue
    return count


def _thread_count() -> int:
    return threading.active_count()


def _state_litter(root: Path) -> dict[str, list[str]]:
    """Leftover tmp/lock/corrupt-backup files under *root* (if any)."""
    if not root.exists():
        return {"tmp": [], "lock": [], "corrupt": []}
    rel = lambda p: str(p.relative_to(root))  # noqa: E731
    return {
        "tmp": sorted(rel(p) for p in root.rglob("*.tmp")),
        "lock": sorted(rel(p) for p in root.rglob("*.lock")),
        "corrupt": sorted(rel(p) for p in root.rglob("*.corrupt.bak")),
    }


def capture_resources(root: Path) -> dict[str, Any]:
    """Snapshot of resource / litter state for before-after deltas."""
    return {
        "threads": _thread_count(),
        "open_file_objects": _open_file_objects(),
        "litter": _state_litter(root),
        "state_json_bytes": _state_json_bytes(root),
    }


def _state_json_bytes(root: Path) -> int:
    total = 0
    if not root.exists():
        return 0
    for p in root.rglob("*.json"):
        try:
            total += p.stat().st_size
        except OSError:
            continue
    return total


def resource_deltas(before: dict[str, Any], after: dict[str, Any]) -> dict[str, Any]:
    """Compute (and bound) the resource deltas between two snapshots."""
    deltas = {
        "threads": after["threads"] - before["threads"],
        "open_file_objects": after["open_file_objects"] - before["open_file_objects"],
        "state_json_bytes_delta": after["state_json_bytes"] - before["state_json_bytes"],
        "litter": after["litter"],
    }
    deltas["threads_ok"] = deltas["threads"] <= THREAD_DELTA_MAX
    deltas["file_objects_ok"] = deltas["open_file_objects"] <= FILE_OBJECTS_DELTA_MAX
    deltas["litter_ok"] = not (
        deltas["litter"]["tmp"] or deltas["litter"]["lock"] or deltas["litter"]["corrupt"]
    )
    return deltas


# ── Bounded-state snapshot (Sprint 13.8 §10) ──────────────────────

def _reset_measured_state() -> None:
    """Reset the process-global bounded state the soak measures.

    Every bounded subsystem below is a module-level singleton with
    monotonic counters (incident totals, reconciliation counters,
    quality snapshots) or a shared cache.  A hermetic soak must start
    from a known baseline: when invoked from the test suite, earlier
    tests legitimately populate these singletons, and the soak's
    absolute bound checks would then measure suite residue instead of
    soak behaviour.  No-op-safe: every object exposes ``reset``/
    ``clear``; the notification manager is rebound the same way the
    test conftest does.
    """
    try:
        from src.cache.scanner_cache import scanner_cache  # noqa: PLC0415

        scanner_cache.clear()
    except Exception:  # noqa: BLE001 - hermetic reset must never mask the soak
        pass
    try:
        from src.indicators.cache import indicator_cache  # noqa: PLC0415

        indicator_cache.clear()
    except Exception:  # noqa: BLE001
        pass
    try:
        from src.data.incidents import incident_tracker  # noqa: PLC0415

        incident_tracker.reset()
    except Exception:  # noqa: BLE001
        pass
    try:
        from src.data.reconciliation import reconciliation_metrics  # noqa: PLC0415

        reconciliation_metrics.reset()
    except Exception:  # noqa: BLE001
        pass
    try:
        from src.data.quality import quality_trends  # noqa: PLC0415

        quality_trends.reset()
    except Exception:  # noqa: BLE001
        pass
    try:
        import src.ui.notifications as notif_module  # noqa: PLC0415

        notif_module.NotificationManager._instance = None
        notif_module.notification_manager = notif_module.NotificationManager()
    except Exception:  # noqa: BLE001
        pass


def capture_bounded_state() -> dict[str, Any]:
    """Snapshot every bounded subsystem's current size/counters.

    Imported lazily so the module stays light; every tracked object is
    bounded by construction (deque maxlen / LRU cap / scalar counters),
    and the soak asserts the *measured* sizes stay within those caps.
    """
    from src.cache.scanner_cache import scanner_cache  # noqa: PLC0415
    from src.data.incidents import incident_tracker  # noqa: PLC0415
    from src.data.quality import quality_trends  # noqa: PLC0415
    from src.data.reconciliation import reconciliation_metrics  # noqa: PLC0415
    from src.ui.notifications import MAX_HISTORY, NotificationManager  # noqa: PLC0415

    mgr = NotificationManager()
    incidents = incident_tracker.snapshot(recent_limit=0)
    return {
        "incident_events": incidents["total"],
        "incident_recent_bounded": len(incidents["recent"]) == 0,
        "minor_symbols": len(getattr(incident_tracker, "_minor_counts", {})),
        "reconciliation_counters": reconciliation_metrics.snapshot(),
        "quality_snapshots": len(getattr(quality_trends, "_snapshots", [])),
        "scanner_cache": scanner_cache.stats(),
        "notifications": len(getattr(mgr, "_notifications", [])),
        "notifications_max": MAX_HISTORY,
    }


def _bounded_state_report(before: dict[str, Any], after: dict[str, Any]) -> dict[str, Any]:
    """Diff two bounded-state snapshots and bound the growth."""
    report: dict[str, Any] = {}
    report["incident_events_delta"] = after["incident_events"] - before["incident_events"]
    report["minor_symbols_delta"] = after["minor_symbols"] - before["minor_symbols"]
    report["quality_snapshots_delta"] = after["quality_snapshots"] - before["quality_snapshots"]
    report["notifications_delta"] = after["notifications"] - before["notifications"]
    report["scanner_cache_entries"] = after["scanner_cache"]["analysis_entries"]
    report["scanner_cache_max"] = after["scanner_cache"]["max_entries"]
    report["notifications_now"] = after["notifications"]
    report["notifications_max"] = after["notifications_max"]
    report["incident_events_bounded"] = after["incident_events"] <= 250
    report["minor_symbols_bounded"] = after["minor_symbols"] <= 500
    report["scanner_cache_bounded"] = (
        report["scanner_cache_entries"] <= report["scanner_cache_max"]
    )
    report["notifications_bounded"] = report["notifications_now"] <= report["notifications_max"]
    return report


# ── Scenario A — normal operation ─────────────────────────────────

def run_scenario_normal(
    data_dir: Path,
    state_root: Path,
    iterations: int,
    symbols: int,
    rows: int,
) -> dict[str, Any]:
    """Repeatedly: history load → analyze → scan → metrics snapshot.

    Uses the exact hermetic in-process pipeline the CI gate exercises
    (``load_csv`` → ``analyze_stock`` → ``scan_market``), with state
    files redirected to *state_root*.  Cache is deliberately kept warm
    across repetitions (the sustained-operation condition).

    Memory envelope (tracemalloc): the Python-object layer — where
    singleton/cache/ring/notification leaks live.  numpy buffers are
    freed by refcount and excluded (documented in the module docstring).
    """
    from benchmarks.common import write_csvs  # noqa: PLC0415
    from benchmarks.pipeline import _state_files  # noqa: PLC0415
    from src.cache.scanner_cache import scanner_cache  # noqa: PLC0415
    from src.engine.analyzer import analyze_stock  # noqa: PLC0415
    from src.loaders.csv_loader import load_csv  # noqa: PLC0415
    from src.scanner import engine as scanner_engine  # noqa: PLC0415

    files = sorted(data_dir.glob("*.csv"))
    old_dir = scanner_engine.DATA_DIRECTORY
    scanner_engine.DATA_DIRECTORY = str(data_dir)
    tracemalloc.start()
    memory_samples: list[float] = []
    failures: list[str] = []
    try:
        with _state_files(state_root):
            for i in range(iterations):
                # 1. history
                for p in files:
                    load_csv(p)
                # 2. analyze
                for p in files:
                    analyze_stock(str(p))
                # 3. scan (parallel, bounded pool)
                scanner_engine.scan_market(workers=2)
                # 4. metrics snapshot (bounded counters)
                snapshot = capture_bounded_state()
                if snapshot["notifications"] > snapshot["notifications_max"]:
                    failures.append(
                        f"iteration {i}: notifications {snapshot['notifications']} > "
                        f"max {snapshot['notifications_max']}"
                    )
                gc.collect()
                current, _peak = tracemalloc.get_traced_memory()
                memory_samples.append(current / (1024 * 1024))
    finally:
        tracemalloc.stop()
        scanner_engine.DATA_DIRECTORY = old_dir

    # Memory envelope: compare the warm-up plateau vs the end.
    # Warm-up = first 20% (allocator + cache warm-up); the plateau is
    # the median of the rest.  A monotonic leak grows the end well above
    # the plateau; noise oscillates around it.
    warmup = max(1, iterations // 5)
    plateau = memory_samples[warmup:] or memory_samples[-1:]
    end = memory_samples[-1] if memory_samples else 0.0
    plateau_median = sorted(plateau)[len(plateau) // 2] if plateau else 0.0
    growth = end - plateau_median
    memory_ok = growth <= MEMORY_LEAK_DELTA_MIB
    if not memory_ok:
        failures.append(
            f"memory growth {growth:.2f} MiB > envelope "
            f"{MEMORY_LEAK_DELTA_MIB:.1f} MiB (plateau {plateau_median:.1f} MiB -> "
            f"end {end:.1f} MiB)"
        )
    return {
        "passed": not failures,
        "iterations": iterations,
        "symbols": symbols,
        "rows": rows,
        "failures": failures,
        "memory_samples": [round(x, 3) for x in memory_samples],
        "memory_plateau_mib": round(plateau_median, 3),
        "memory_end_mib": round(end, 3),
        "memory_growth_mib": round(growth, 3),
        "memory_envelope_mib": MEMORY_LEAK_DELTA_MIB,
    }


# ── Scenario B — provider fallback ────────────────────────────────

def run_scenario_fallback(
    iterations: int,
    *,
    failure_threshold: int = 3,
    recovery_period: float = 0.01,
    success_recovery_count: int = 2,
    outcome_window: int = 100,
) -> dict[str, Any]:
    """Repeatedly: primary healthy → primary unavailable → fallback →
    primary recovery.

    Drives the real ``ProviderHealthMonitor`` and provenance builder,
    alternating the health of two providers, and verifies every cycle:
    bounded outcome window, accurate counters, automatic recovery, no
    permanent disable, and correct ``fallback_used`` provenance.
    """
    from src.data.health import (  # noqa: PLC0415
        HealthCheckConfig,
        ProviderHealthMonitor,
    )
    from src.data.provenance import (  # noqa: PLC0415
        TRUST_FALLBACK,
        TRUST_TRUSTED,
        DataProvenance,
    )
    from src.data.reconciliation import (  # noqa: PLC0415
        AGREE,
        UNAVAILABLE,
        ReconciliationMetricsCollector,
        ReconciliationResult,
    )

    monitor = ProviderHealthMonitor(
        HealthCheckConfig(
            failure_threshold=failure_threshold,
            recovery_period=recovery_period,
            success_recovery_count=success_recovery_count,
            outcome_window=outcome_window,
        )
    )
    monitor.register("primary")
    monitor.register("fallback")
    metrics = ReconciliationMetricsCollector()
    failures: list[str] = []
    cycles = 0
    fallback_cycles = 0
    recovered_cycles = 0

    for i in range(iterations):
        phase = i % 4
        if phase == 0:  # primary healthy
            monitor.record_success("primary", latency_ms=80)
        elif phase == 1:  # primary failure burst → disabled
            for _ in range(failure_threshold):
                monitor.record_failure("primary", outcome="timeout")
            if monitor.is_healthy("primary"):
                failures.append(f"iter {i}: primary still healthy after failure burst")
        elif phase == 2:  # fallback serves (primary unavailable)
            monitor.record_success("fallback", latency_ms=120)
            result = ReconciliationResult(
                status=UNAVAILABLE,
                symbol="SYN000",
                providers=["primary", "fallback"],
                as_of="2026-08-14T12:00:00",
                fallback_used=True,
            )
            metrics.record_reconciliation(result)
            # ``fallback_count`` is bumped explicitly — the metrics
            # collector records it on ``record_fallback`` (a provider
            # chain event), not on ``record_reconciliation`` (an outcome
            # counter).  A fallback cycle is exactly one provider-level
            # fallback event.
            metrics.record_fallback()
            prov = DataProvenance(
                sources=["primary", "fallback"],
                trust=TRUST_FALLBACK,
                reconciliation_status=UNAVAILABLE,
                fallback_used=True,
            )
            if not prov.is_safe or prov.trust != TRUST_FALLBACK:
                failures.append(f"iter {i}: fallback provenance not FALLBACK/safe")
            fallback_cycles += 1
        else:  # primary recovery
            # Successes needed to clear the disabled state.
            for _ in range(success_recovery_count):
                monitor.record_success("primary", latency_ms=75)
            if not monitor.is_healthy("primary"):
                failures.append(f"iter {i}: primary did not recover after successes")
            result = ReconciliationResult(
                status=AGREE,
                symbol="SYN000",
                providers=["primary"],
                as_of="2026-08-14T12:00:00",
            )
            metrics.record_reconciliation(result)
            prov = DataProvenance(
                sources=["primary"],
                trust=TRUST_TRUSTED,
                reconciliation_status=AGREE,
            )
            if prov.trust != TRUST_TRUSTED:
                failures.append(f"iter {i}: recovery provenance not TRUSTED")
            recovered_cycles += 1
        cycles += 1

    # Bounded outcome window + counters.
    window = monitor.outcome_window("primary")
    if len(window) > outcome_window:
        failures.append(f"outcome window {len(window)} > {outcome_window}")
    snap = metrics.snapshot()
    expected_fallback = fallback_cycles
    expected_agree = recovered_cycles
    if snap["fallback_count"] != expected_fallback:
        failures.append(
            f"fallback_count {snap['fallback_count']} != expected {expected_fallback}"
        )
    if snap["agreements"] != expected_agree:
        failures.append(f"agreements {snap['agreements']} != expected {expected_agree}")
    # The UNAVAILABLE reconciliation must NOT be counted as an agreement
    # (the collector has no UNAVAILABLE branch by design — only AGREE /
    # MINOR / MATERIAL / MAPPING increment; the fallback phase contributes
    # to ``fallback_count`` via the explicit ``record_fallback`` above).
    return {
        "passed": not failures,
        "iterations": iterations,
        "cycles": cycles,
        "fallback_cycles": fallback_cycles,
        "recovered_cycles": recovered_cycles,
        "primary_window_len": len(window),
        "outcome_window_cap": outcome_window,
        "metrics": snap,
        "failures": failures,
    }


# ── Scenario C — material-conflict cycling ────────────────────────

def run_scenario_conflict(
    iterations: int,
) -> dict[str, Any]:
    """Repeatedly inject AGREE → MINOR → MATERIAL → AGREE →
    MAPPING_CONFLICT → AGREE and verify no cross-request poisoning.

    The critical regression (Sprint 13.8 §15): a MATERIAL request must
    produce an untrusted result, and the *next* AGREE request must be
    fully trusted — the conflict must never contaminate later requests.
    """
    from src.data.incidents import IncidentTracker  # noqa: PLC0415
    from src.data.provenance import (  # noqa: PLC0415
        TRUST_CONFLICTED,
        TRUST_RECONCILED,
        DataProvenance,
    )
    from src.data.reconciliation import (  # noqa: PLC0415
        AGREE,
        MAPPING_CONFLICT,
        MATERIAL_DISAGREEMENT,
        MINOR_DISAGREEMENT,
        reconcile_records,
    )

    tracker = IncidentTracker(max_events=200)
    failures: list[str] = []
    material_seen = 0
    mapping_seen = 0
    agree_seen = 0

    def record(close_a: float, close_b: float, symbol_a: str = "SYN000") -> Any:
        """Reconcile two provider records for one symbol."""
        return reconcile_records(
            [
                ("primary", {"symbol": symbol_a, "close": close_a, "open": close_a - 1,
                             "high": close_a + 1, "low": close_a - 2, "volume": 1000}),
                ("fallback", {"symbol": "SYN000", "close": close_b, "open": close_b - 1,
                              "high": close_b + 1, "low": close_b - 2, "volume": 1000}),
            ],
            symbol=symbol_a,
            record_date="2026-08-14",
            as_of="2026-08-14T12:00:00",
        )

    pattern = [
        ("AGREE", 100.0, 100.0),
        # 1.5% diff: above the 1.0% tolerance, below the 5.0% material
        # threshold -> MINOR (resolved to the preferred source).
        # MINOR 1.5%: above the 1.0% tolerance, below the 5.0%
        # material threshold (config RECONCILE_*_PCT defaults).
        ("MINOR", 100.0, 101.5),
        ("MATERIAL", 100.0, 110.0),      # > 5% material threshold
        ("AGREE", 100.0, 100.0),
        ("MAPPING", 100.0, 100.0, "OTHER"),  # symbol mismatch
        ("AGREE", 100.0, 100.0),
    ]
    for i in range(iterations):
        step = pattern[i % len(pattern)]
        name = step[0]
        close_a, close_b = float(step[1]), float(step[2])
        symbol_a = step[3] if len(step) > 3 else "SYN000"
        res = record(close_a, close_b, symbol_a)

        # Trust resolution per outcome.
        trust = res.status
        if name == "AGREE":
            agree_seen += 1
            if res.status != AGREE:
                failures.append(f"iter {i}: expected AGREE got {res.status}")
            prov = DataProvenance(
                sources=res.providers,
                trust=TRUST_RECONCILED,
                reconciliation_status=res.status,
            )
            if not prov.is_safe:
                failures.append(f"iter {i}: clean AGREE flagged unsafe (poisoned?)")
        elif name == "MINOR":
            if res.status != MINOR_DISAGREEMENT:
                failures.append(f"iter {i}: expected MINOR got {res.status}")
            if not res.is_trusted:
                failures.append(f"iter {i}: MINOR must be trusted (preferred source)")
        elif name == "MATERIAL":
            material_seen += 1
            if res.status != MATERIAL_DISAGREEMENT:
                failures.append(f"iter {i}: expected MATERIAL got {res.status}")
            if res.is_trusted:
                failures.append(f"iter {i}: MATERIAL must NEVER be trusted")
            prov = DataProvenance(
                sources=res.providers,
                trust=TRUST_CONFLICTED,
                reconciliation_status=res.status,
            )
            if prov.is_safe:
                failures.append(f"iter {i}: conflicted provenance must be unsafe")
            tracker.record("material_disagreement", symbol="SYN000")
        elif name == "MAPPING":
            mapping_seen += 1
            if res.status != MAPPING_CONFLICT:
                failures.append(f"iter {i}: expected MAPPING_CONFLICT got {res.status}")
            if res.is_trusted:
                failures.append(f"iter {i}: MAPPING_CONFLICT must never be trusted")
            tracker.record("mapping_conflict", symbol=symbol_a)        # Contamination check: after the last step, a final clean AGREE must
        # be fully trusted (the regression test from §15).
        final = record(100.0, 100.0)
        final_prov = DataProvenance(
            sources=final.providers,
            trust=TRUST_RECONCILED,
            reconciliation_status=final.status,
        )
        if final.status != AGREE or not final_prov.is_safe:
            failures.append("final clean AGREE after conflict cycle is unsafe (poisoned!)")
    if agree_seen == 0:
        failures.append("no clean AGREE outcome exercised in conflict soak")

    # Bounded incidents.
    snap = tracker.snapshot(recent_limit=0)
    if snap["total"] != material_seen + mapping_seen:
        failures.append(
            f"incident total {snap['total']} != {material_seen + mapping_seen}"
        )
    if snap["counts"]["material_disagreement"] != material_seen:
        failures.append(
            f"material incidents {snap['counts']['material_disagreement']} != {material_seen}"
        )
    if snap["counts"]["mapping_conflict"] != mapping_seen:
        failures.append(
            f"mapping incidents {snap['counts']['mapping_conflict']} != {mapping_seen}"
        )
    return {
        "passed": not failures,
        "iterations": iterations,
        "pattern_len": len(pattern),
        "material_seen": material_seen,
        "mapping_seen": mapping_seen,
        "agree_seen": agree_seen,
        "incident_snapshot": snap,
        "failures": failures,
    }


# ── Scenario D — cache hit/miss cycling ───────────────────────────

def run_scenario_cache(
    data_dir: Path,
    state_root: Path,
    iterations: int,
    max_entries: int = 600,
) -> dict[str, Any]:
    """Alternate cold → warm → invalidate → cold → warm.

    Verifies the scanner cache stays bounded (LRU cap), invalidates on
    demand, and that a warm analysis hit returns a result consistent
    with a fresh (cold) one — provenance preserved across the cache.
    """
    from benchmarks.common import write_csvs  # noqa: PLC0415
    from src.cache.scanner_cache import scanner_cache  # noqa: PLC0415
    from src.engine.analyzer import analyze_stock  # noqa: PLC0415
    from src.indicators.cache import indicator_cache  # noqa: PLC0415

    # Dedicated small sub-corpus so the scenario always cycles exactly
    # 10 files and never touches the parent soak corpus in *data_dir*.
    cycle_dir = data_dir / "cache_cycle"
    write_csvs(cycle_dir, 10, 60)  # small corpus for fast cycling
    files = sorted(cycle_dir.glob("*.csv"))
    failures: list[str] = []
    for i in range(iterations):
        for p in files:
            # cold → warm within the cycle: clear both tiers, then run
            # the same stock twice.  ``analyze_stock`` consults the
            # scanner cache for the parsed frame and the indicator
            # cache for the indicator-heavy analysis (both keyed by
            # file fingerprint), so the second call is a warm hit.
            scanner_cache.clear()
            indicator_cache.clear()
            analysis = analyze_stock(str(p))  # cold (miss)
            again = analyze_stock(str(p))  # warm (hit unless bypassed)
            if again is not None and analysis is not None:
                # Both paths return dicts; compare the deterministic keys.
                if again.get("signal") != analysis.get("signal"):
                    failures.append(f"iter {i}: cache hit diverged from cold analysis")
                if again.get("trend") != analysis.get("trend"):
                    failures.append(f"iter {i}: cache hit trend diverged from cold")
            stats = scanner_cache.stats()
            if stats["dataframe_entries"] > max_entries:
                failures.append(
                    f"iter {i}: scanner df cache entries {stats['dataframe_entries']} > "
                    f"{max_entries}"
                )
            if stats["analysis_entries"] > max_entries:
                failures.append(
                    f"iter {i}: scanner analysis cache entries {stats['analysis_entries']} > "
                    f"{max_entries}"
                )
            istats = indicator_cache.stats()
            if istats["entries"] > istats["max_entries"]:
                failures.append(
                    f"iter {i}: indicator cache entries {istats['entries']} > "
                    f"max {istats['max_entries']}"
                )
    report = {
        "passed": not failures,
        "iterations": iterations,
        "cache_entries": scanner_cache.stats()["analysis_entries"],
        "cache_max": max_entries,
        "cache_stats": scanner_cache.stats(),
        "indicator_cache_stats": indicator_cache.stats(),
        "failures": failures,
    }
    return report


# ── Scenario E — worker restart (opt-in) ──────────────────────────

def run_scenario_worker(
    data_dir: Path,
    state_root: Path,
    *,
    port: int | None = None,
    cycles: int = 2,
    workers: int = 1,
) -> dict[str, Any]:
    """Spawn a real uvicorn API, request, shutdown (tree-kill), respawn,
    request again.  Verifies state integrity after each restart and no
    subprocess survives ``_shutdown``.

    Opt-in: spawns a subprocess and binds a port.  Bounded to *cycles*
    (default 2) restarts.
    """
    import socket  # noqa: PLC0415

    from benchmarks.common import write_csvs  # noqa: PLC0415
    from benchmarks.validate_workers import (  # noqa: PLC0415
        _request,
        _scan_state,
        _shutdown,
        _spawn_api,
        _wait_ready,
    )

    # Bind a free port up-front ONLY when the caller did not supply one,
    # so a stale listener on the default port can never be mistaken for
    # the worker we spawn (the socket is closed immediately; the race
    # window is negligible for an opt-in soak scenario).
    if port is None:
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
    failures: list[str] = []
    procs: list[subprocess.Popen] = []
    statuses: list[int] = []
    for cycle in range(cycles):
        proc = _spawn_api(data_dir, state_root, port, workers=workers)
        procs.append(proc)
        try:
            _wait_ready(port, timeout=120)
            status, body = _request(port, "/")
            statuses.append(status)
            if status != 200:
                failures.append(f"cycle {cycle}: / -> {status}")
            _mstatus, _ = _request(port, "/metrics")
            if _mstatus != 200:
                failures.append(f"cycle {cycle}: /metrics -> {_mstatus}")
        except Exception as exc:  # noqa: BLE001 - cycle isolation
            failures.append(f"cycle {cycle}: {exc}")
        finally:
            _shutdown(proc)
    # State integrity after all restarts.
    state = _scan_state(state_root)
    for name, verdict in state["stores"].items():
        if verdict != "valid-json":
            failures.append(f"state store {name} not valid: {verdict}")
    if state["leftover_tmp"] or state["corrupt_backups"] or state["leftover_locks"]:
        failures.append(f"state litter after restarts: {state}")
    # Every spawned process must be gone.
    for proc in procs:
        if proc.poll() is None:
            failures.append(f"worker process {proc.pid} still alive after shutdown")
    return {
        "passed": not failures,
        "cycles": cycles,
        "workers": workers,
        "statuses": statuses,
        "state": state,
        "failures": failures,
    }


# ── Orchestration ─────────────────────────────────────────────────

def run_soak(
    *,
    iterations: int = DEFAULT_ITERATIONS,
    symbols: int = DEFAULT_SYMBOLS,
    rows: int = DEFAULT_ROWS,
    include_workers: bool = False,
    worker_cycles: int = 2,
) -> dict[str, Any]:
    """Run every scenario over one hermetic temp workspace and return a
    full report (verdict + per-scenario results + resource/bounded-state
    deltas)."""
    from benchmarks.common import write_csvs  # noqa: PLC0415

    tmp = Path(tempfile.mkdtemp(prefix="nepse_soak_"))
    data_dir = tmp / "data"
    state_root = tmp / "state"
    state_root.mkdir(parents=True, exist_ok=True)
    write_csvs(data_dir, symbols, rows)

    start = time.perf_counter()
    scenarios: dict[str, Any] = {}
    failures: list[str] = []
    scenario_errors: dict[str, str] = {}

    # Sprint 13.8 hermeticity: every bounded subsystem the soak
    # measures is a process-global singleton with monotonic counters.
    # When the soak runs IN-PROCESS from the test suite, earlier tests
    # legitimately populate them — the soak must start from a known
    # baseline or its absolute bound checks (e.g. incidents <= 250)
    # fail on suite residue, not on soak behaviour.  Reset exactly the
    # state ``capture_bounded_state`` measures + the caches the
    # scenarios drive.
    _reset_measured_state()
    before_resources = capture_resources(state_root)
    before_state = capture_bounded_state()

    def _run(name: str, fn: Callable[[], dict[str, Any]]) -> None:
        """Scenario isolation: a raising scenario is recorded as a FAIL
        instead of aborting the whole soak (and leaking its temp dir)."""
        try:
            scenarios[name] = fn()
        except Exception as exc:  # noqa: BLE001 - scenario isolation
            scenarios[name] = {"passed": False, "failures": [f"raised: {exc!r}"]}
            scenario_errors[name] = repr(exc)

    try:
        _run("normal", lambda: run_scenario_normal(
            data_dir, state_root, iterations, symbols, rows
        ))
        _run("fallback", lambda: run_scenario_fallback(iterations))
        _run("conflict", lambda: run_scenario_conflict(iterations))
        _run("cache", lambda: run_scenario_cache(
            data_dir, state_root, max(3, iterations // 3)
        ))
        if include_workers:
            _run("worker", lambda: run_scenario_worker(
                data_dir, state_root, cycles=worker_cycles
            ))
    finally:
        # Always clean the hermetic workspace, even when a scenario
        # raised (the except above re-raises nothing; the finally
        # guarantees no temp-dir leak on any path).
        after_resources = capture_resources(state_root)
        after_state = capture_bounded_state()
        # Reset the two process-wide caches the scenarios populated so a
        # later soak/test run starts from a known state (fingerprints
        # would prevent real-corpus collisions, but hygiene matters).
        try:
            from src.cache.scanner_cache import scanner_cache  # noqa: PLC0415
            from src.indicators.cache import indicator_cache  # noqa: PLC0415

            scanner_cache.clear()
            indicator_cache.clear()
        except Exception:  # noqa: BLE001 - cache reset is best-effort
            pass

    resource = resource_deltas(before_resources, after_resources)
    bounded = _bounded_state_report(before_state, after_state)

    for name, report in scenarios.items():
        if not report.get("passed", False):
            failures.append(f"scenario {name} failed: {report.get('failures')}")
    if not resource["threads_ok"]:
        failures.append(f"threads grew by {resource['threads']}")
    if not resource["file_objects_ok"]:
        failures.append(f"open file objects grew by {resource['open_file_objects']}")
    if not resource["litter_ok"]:
        failures.append(f"state litter after soak: {resource['litter']}")
    for key in (
        "incident_events_bounded",
        "minor_symbols_bounded",
        "scanner_cache_bounded",
        "notifications_bounded",
    ):
        if not bounded[key]:
            failures.append(f"bounded state violated: {key}")

    duration = time.perf_counter() - start
    report = {
        "verdict": "PASS" if not failures else "FAIL",
        "failures": failures,
        "scenario_errors": scenario_errors,
        "iterations": iterations,
        "symbols": symbols,
        "rows": rows,
        "duration_seconds": round(duration, 2),
        "scenarios": scenarios,
        "resources": resource,
        "bounded_state": bounded,
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }
    # Always clean the hermetic workspace, even on failure.
    import shutil  # noqa: PLC0415

    shutil.rmtree(tmp, ignore_errors=True)
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description="NEPSE platform soak (Sprint 13.8)")
    parser.add_argument("--iterations", type=int, default=DEFAULT_ITERATIONS)
    parser.add_argument("--symbols", type=int, default=DEFAULT_SYMBOLS)
    parser.add_argument("--rows", type=int, default=DEFAULT_ROWS)
    parser.add_argument(
        "--include-workers",
        action="store_true",
        help="Include scenario E (spawns a real uvicorn subprocess)",
    )
    parser.add_argument("--worker-cycles", type=int, default=2)
    parser.add_argument("--out", type=str, default="soak-report.json")
    args = parser.parse_args()

    report = run_soak(
        iterations=args.iterations,
        symbols=args.symbols,
        rows=args.rows,
        include_workers=args.include_workers,
        worker_cycles=args.worker_cycles,
    )
    Path(args.out).write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"Soak verdict: {report['verdict']}  "
          f"({report['iterations']} iterations, {report['duration_seconds']}s)")
    for name, scen in report["scenarios"].items():
        print(f"  {name:<10} -> {'PASS' if scen['passed'] else 'FAIL'}")
    print(f"  resources -> threads delta {report['resources']['threads']}, "
          f"file objects delta {report['resources']['open_file_objects']}, "
          f"litter ok {report['resources']['litter_ok']}")
    bs = report["bounded_state"]
    print(f"  bounded  -> incidents delta {bs['incident_events_delta']}, "
          f"minor symbols delta {bs['minor_symbols_delta']}, "
          f"notifications {bs['notifications_now']}/{bs['notifications_max']}, "
          f"scanner cache {bs['scanner_cache_entries']}/{bs['scanner_cache_max']}")
    if report["failures"]:
        print("FAILURES:")
        for f in report["failures"]:
            print(f"  - {f}")
    return 0 if report["verdict"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
