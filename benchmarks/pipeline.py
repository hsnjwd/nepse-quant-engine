"""Individual benchmarks for the NEPSE Quant Engine (Sprint 11.1).

Each function returns a dict of measured metrics.  All benchmarks use
synthetic data and never touch production state files.
"""

from __future__ import annotations

import sys
import tempfile
import time
from pathlib import Path
from typing import Any

import pandas as pd

from benchmarks.common import make_synthetic_df, measure_peak_memory, timeit, write_csvs


def _percentiles(latencies_ms: list[float]) -> dict[str, float]:
    """Compute p50/p95/p99 (+ best/avg) from a sorted sample."""
    if not latencies_ms:
        return {"best_ms": 0.0, "avg_ms": 0.0, "p50_ms": 0.0, "p95_ms": 0.0, "p99_ms": 0.0}
    sorted_ms = sorted(latencies_ms)
    n = len(sorted_ms)

    def pct(p: float) -> float:
        idx = min(n - 1, max(0, int(p * n)))
        return sorted_ms[idx]

    return {
        "best_ms": round(sorted_ms[0], 3),
        "avg_ms": round(sum(sorted_ms) / n, 3),
        "p50_ms": round(pct(0.50), 3),
        "p95_ms": round(pct(0.95), 3),
        "p99_ms": round(pct(0.99), 3),
    }

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def bench_single_analysis(rows: int = 500) -> dict[str, Any]:
    """Single-stock full analysis (load-less: DataFrame already parsed)."""
    from src.engine.analyzer import analyze_dataframe  # noqa: PLC0415

    df = make_synthetic_df(rows=rows)
    best, avg = timeit(lambda: analyze_dataframe(df.copy()))
    _elapsed, peak = measure_peak_memory(lambda: analyze_dataframe(df.copy()))
    return {"analysis_best_ms": round(best, 3), "analysis_avg_ms": round(avg, 3), "analysis_peak_mib": round(peak, 2)}


def bench_history_load(
    data_dir: Path,
    symbols: int,
    rows: int,
    populate: bool = True,
) -> dict[str, Any]:
    """Historical data loading through load_csv (cold + warm).

    Args:
        data_dir: Directory containing OHLCV CSVs.
        symbols: Symbol count (informational).
        rows: Rows per symbol (informational, synthetic only).
        populate: Write synthetic CSVs first.  ``False`` when the
            directory already holds a prepared corpus (e.g. real
            scraped data) so the benchmark never mixes synthetic files
            into a real-data run.
    """
    from src.cache.scanner_cache import scanner_cache  # noqa: PLC0415
    from src.loaders.csv_loader import load_csv  # noqa: PLC0415

    if populate:
        write_csvs(data_dir, symbols, rows)
    paths = sorted(data_dir.glob("*.csv"))
    scanner_cache.clear()

    cold_best, _ = timeit(lambda: [load_csv(p) for p in paths], repeats=1)
    warm_best, warm_avg = timeit(lambda: [load_csv(p) for p in paths], repeats=3)
    return {
        "symbols": symbols,
        "rows": rows,
        "history_cold_ms": round(cold_best, 3),
        "history_warm_avg_ms": round(warm_avg, 3),
        "history_warm_best_ms": round(warm_best, 3),
    }


class _state_files:
    """Context manager redirecting alert-history / portfolio persistence.

    Benchmarks must never write synthetic symbols into the production
    ``data/alerts/history.json`` or read a real ``portfolio.json``.
    Module globals are restored on exit so a runner imported in-process
    (e.g. by a test) cannot leave state files redirected.
    """

    def __init__(self, tmp: Path) -> None:
        self._tmp = tmp

    def __enter__(self) -> "_state_files":
        from src.alerts import engine as alert_engine  # noqa: PLC0415
        from src.alerts import history as alerts_history  # noqa: PLC0415
        from src.portfolio import holdings as portfolio_holdings  # noqa: PLC0415

        self._history = alerts_history.HISTORY_FILE
        self._engine_history = alert_engine.HISTORY_FILE
        self._portfolio = portfolio_holdings.PORTFOLIO_FILE
        alerts_history.HISTORY_FILE = self._tmp / "alerts" / "history.json"
        # ``src.alerts.engine`` imports HISTORY_FILE into its own
        # namespace and ``process_alerts``/``process_alert_batch`` call
        # ``locked_json(HISTORY_FILE)`` with that copy — if it is not
        # redirected too, the cross-process lock file is created on the
        # production ``data/alerts/history.json.lock`` path (Sprint 12.0
        # review fix).
        alert_engine.HISTORY_FILE = alerts_history.HISTORY_FILE
        portfolio_holdings.PORTFOLIO_FILE = self._tmp / "portfolio.json"
        return self

    def __exit__(self, *exc: object) -> None:
        from src.alerts import engine as alert_engine  # noqa: PLC0415
        from src.alerts import history as alerts_history  # noqa: PLC0415
        from src.portfolio import holdings as portfolio_holdings  # noqa: PLC0415

        alerts_history.HISTORY_FILE = self._history
        alert_engine.HISTORY_FILE = self._engine_history
        portfolio_holdings.PORTFOLIO_FILE = self._portfolio


def bench_scan(
    data_dir: Path,
    symbols: int,
    rows: int,
    populate: bool = True,
) -> dict[str, Any]:
    """Full market scan (cold cache, then warm cache).

    Args:
        data_dir: Directory containing OHLCV CSVs.
        symbols: Symbol count (informational).
        rows: Rows per symbol (informational, synthetic only).
        populate: Write synthetic CSVs first.  ``False`` for real-corpus
            runs so no synthetic files are mixed into the corpus.
    """
    from src.cache.scanner_cache import scanner_cache  # noqa: PLC0415
    from src.scanner import engine as scanner_engine  # noqa: PLC0415

    if populate:
        write_csvs(data_dir, symbols, rows)
    old_dir = scanner_engine.DATA_DIRECTORY
    scanner_engine.DATA_DIRECTORY = str(data_dir)
    with tempfile.TemporaryDirectory(prefix="nepse_iso_") as iso_tmp:
        with _state_files(Path(iso_tmp)):
            try:
                scanner_cache.clear()
                cold_best, _ = timeit(lambda: scanner_engine.scan_market(), repeats=1)
                warm_best, warm_avg = timeit(lambda: scanner_engine.scan_market(), repeats=3)
                _elapsed, peak = measure_peak_memory(lambda: scanner_engine.scan_market())
                return {
                    "symbols": symbols,
                    "scan_cold_ms": round(cold_best, 2),
                    "scan_warm_avg_ms": round(warm_avg, 2),
                    "scan_warm_best_ms": round(warm_best, 2),
                    "scan_peak_mib": round(peak, 2),
                    "cache_state": "cold-warm",
                }
            finally:
                scanner_engine.DATA_DIRECTORY = old_dir


def bench_scan_scale(
    data_dir: Path,
    symbols: int,
    workers: int | None,
    cold_reps: int = 3,
    warm_reps: int = 5,
) -> dict[str, Any]:
    """Scanner scaling benchmark (Sprint 11.2): sequential vs parallel.

    Records cold and warm latencies with percentiles (p50/p95/p99),
    success/skipped/failure counts, and peak memory.  ``workers=1``
    exercises the sequential fallback; other values use the bounded
    thread pool.

    Args:
        data_dir: Directory containing the OHLCV CSVs (real or synthetic).
        symbols: Number of symbols present in *data_dir*.
        workers: Worker count (``1`` = sequential fallback).
        cold_reps: Number of cold-cache repetitions.
        warm_reps: Number of warm-cache repetitions.
    """
    from src.cache.scanner_cache import scanner_cache  # noqa: PLC0415
    from src.scanner import engine as scanner_engine  # noqa: PLC0415

    from src.config import SCANNER_WORKERS  # noqa: PLC0415

    # Mirror scan_market()'s exact resolution so the recorded workers /
    # mode always match what the scanner executed (None -> config,
    # explicit 0 -> clamped to 1).
    effective = max(1, SCANNER_WORKERS if workers is None else workers)

    old_dir = scanner_engine.DATA_DIRECTORY
    scanner_engine.DATA_DIRECTORY = str(data_dir)
    with tempfile.TemporaryDirectory(prefix="nepse_iso_") as iso_tmp:
        with _state_files(Path(iso_tmp)):
            try:
                # Cold: fresh cache each rep (analysis cache cleared, so
                # alert processing runs for every symbol).
                cold_latencies: list[float] = []
                for _ in range(cold_reps):
                    scanner_cache.clear()
                    cold_latencies.append(
                        timeit(lambda: scanner_engine.scan_market(workers=effective), repeats=1)[0]
                    )

                # Warm: analysis cache populated -> alert pass skipped.
                warm_latencies: list[float] = []
                for _ in range(warm_reps):
                    warm_latencies.append(
                        timeit(lambda: scanner_engine.scan_market(workers=effective), repeats=1)[0]
                    )

                scanner_cache.clear()
                _elapsed, peak = measure_peak_memory(
                    lambda: scanner_engine.scan_market(workers=effective)
                )

                # Result accounting: one more run, count successes/skips.
                out = scanner_engine.scan_market(workers=effective)
                skipped = len(out.get("skipped", []))
                return {
                    "symbols": symbols,
                    "workers": effective,
                    "mode": "sequential" if effective == 1 else f"parallel-{effective}",
                    "cold": _percentiles(cold_latencies),
                    "warm": _percentiles(warm_latencies),
                    "cold_peak_mib": round(peak, 2),
                    "successful": len(out.get("results", [])),
                    "skipped": skipped,
                    "failures": skipped,
                    "total": symbols,
                }
            finally:
                scanner_engine.DATA_DIRECTORY = old_dir


def bench_backtest(rows: int = 500) -> dict[str, Any]:
    """Backtest engine over synthetic data."""
    from src.backtesting.engine import BacktestEngine  # noqa: PLC0415

    df = make_synthetic_df(rows=rows)

    def run() -> None:
        BacktestEngine().run({"SYN": df})

    best, avg = timeit(run, repeats=2)
    return {"backtest_best_ms": round(best, 2), "backtest_avg_ms": round(avg, 2), "rows": rows}


def bench_strategy_execution(rows: int = 500) -> dict[str, Any]:
    """Strategy-level execution (analyze + signal + decision pipeline).

    ``analyze_dataframe`` already runs the full chain — indicator
    pipeline, score, confidence, signal, trade plan, risk, alerts — so
    it is the representative strategy-execution cost.
    """
    from src.engine.analyzer import analyze_dataframe  # noqa: PLC0415

    df = make_synthetic_df(rows=rows)

    def run() -> None:
        analyze_dataframe(df.copy())

    best, avg = timeit(run, repeats=2)
    return {"strategy_best_ms": round(best, 3), "strategy_avg_ms": round(avg, 3)}


def bench_indicator_cache(
    symbols: int = 20,
    rows: int = 500,
    data_dir: Path | None = None,
) -> dict[str, Any]:
    """Indicator cache: first (cold) vs repeated (warm) analysis.

    Sprint 11.4: measures the cost of the first ``analyze_dataframe``
    per symbol (cold cache — full rolling/ewm indicator chain) versus
    repeated analysis of the *same* frames (warm cache — fingerprint
    hash + dict re-derivation only).  The reported speedup is the
    measured warm-vs-cold ratio for the realistic repeated-API/
    repeated-watchlist-scan workload.

    Args:
        symbols: Number of symbols to analyse.
        rows: Rows per symbol (synthetic source only).
        data_dir: When given, load the actual CSVs from *data_dir*
            (real-corpus run) instead of synthesising frames, so the
            Sprint 11.5 real-data result never mixes synthetic frames
            with the scraped corpus.  ``None`` keeps the deterministic
            synthetic microbenchmark and is labelled ``data_source`` =
            ``"synthetic"`` in the result.
    """
    from src.engine.analyzer import analyze_dataframe  # noqa: PLC0415
    from src.indicators.cache import indicator_cache  # noqa: PLC0415

    data_source = "real" if data_dir is not None else "synthetic"
    if data_dir is not None:
        from src.loaders.csv_loader import load_csv  # noqa: PLC0415

        files = sorted(data_dir.glob("*.csv"))[:symbols]
        frames = [load_csv(p) for p in files]
        names = [p.stem for p in files]
    else:
        frames = [make_synthetic_df(rows=rows, seed=i) for i in range(symbols)]
        names = [f"SYN{i:03d}" for i in range(symbols)]

    indicator_cache.clear()

    # Cold: every symbol is a fresh miss (full indicator chain).
    cold: list[float] = []
    for i, df in enumerate(frames):
        indicator_cache.clear()  # guarantee a miss per symbol
        cold.append(
            timeit(lambda df=df: analyze_dataframe(df, symbol=names[i]), repeats=1)[0]
        )

    # Warm: same symbols again — every one is a cache hit.
    warm: list[float] = []
    for i, df in enumerate(frames):
        warm.append(
            timeit(lambda df=df: analyze_dataframe(df, symbol=names[i]), repeats=3)[1]
        )

    # Measure the warm-phase hit rate in isolation: reset the counters
    # after the cold loop so cold misses do not dilute the ratio.
    indicator_cache.reset_stats()
    for i, df in enumerate(frames):
        analyze_dataframe(df, symbol=names[i])
    warm_stats = indicator_cache.stats()

    cold_ms = sum(cold) / len(cold) if cold else 0.0
    warm_ms = sum(warm) / len(warm) if warm else 0.0
    return {
        "symbols": symbols,
        "rows": rows,
        "data_source": data_source,
        "indicator_cold_avg_ms": round(cold_ms, 3),
        "indicator_warm_avg_ms": round(warm_ms, 3),
        "speedup_x": round(cold_ms / warm_ms, 2) if warm_ms else 0.0,
        "cache_hits": warm_stats["hits"],
        "cache_misses": warm_stats["misses"],
        "hit_rate": round(warm_stats["hit_rate"], 3),
    }


def bench_api_analyze(symbols: int, rows: int) -> dict[str, Any]:
    """/api/analyze latency via in-process TestClient (real route).

    Owns its own temp dir so it never touches production data and never
    depends on a caller-held (possibly already deleted) directory.
    """
    from fastapi.testclient import TestClient  # noqa: PLC0415
    from src.api.main import app  # noqa: PLC0415
    from src.engine import analyzer as analyzer_mod  # noqa: PLC0415
    from src.loaders import csv_loader as loader_mod  # noqa: PLC0415

    with tempfile.TemporaryDirectory(prefix="nepse_api_") as tmp:
        data_dir = Path(tmp) / "data"
        write_csvs(data_dir, symbols, rows)
        with _state_files(Path(tmp)):
            old_dir = loader_mod.DATA_DIRECTORY
            loader_mod.DATA_DIRECTORY = str(data_dir)
            old_analyzer_dir = getattr(analyzer_mod, "DATA_DIRECTORY", None)
            try:
                client = TestClient(app)
                client.get("/")
                latencies: list[float] = []
                for i in range(min(symbols, 5)):
                    start = timeit(lambda i=i: client.get(f"/analyze/SYN{i:03d}"), repeats=1)[0]
                    latencies.append(start)
                latencies.sort()
                avg = sum(latencies) / len(latencies)
                return {
                    "api_analyze_avg_ms": round(avg, 3),
                    "api_analyze_p95_ms": round(latencies[int(len(latencies) * 0.95)], 3) if latencies else 0.0,
                }
            finally:
                loader_mod.DATA_DIRECTORY = old_dir
                if old_analyzer_dir is not None:
                    analyzer_mod.DATA_DIRECTORY = old_analyzer_dir


def bench_api_alert_batch(symbols: int = 20, rows: int = 300) -> dict[str, Any]:
    """API multi-analysis alert path: per-symbol vs batched (Sprint 11.9).

    Measures the two ways the API-level multi-symbol paths (watchlist
    scan, portfolio) can process alert state for *symbols* analyses:

    - legacy: a loop of ``analyze_stock`` — one history read + one
      write per symbol via ``process_alerts``
    - batch: ``analyze_stock_batch(..., use_batch_alerts=True)`` — one
      history read + one atomic write for the whole set via
      ``process_alert_batch``

    Both runs start from a cold (empty) history file and use the same
    synthetic corpus so the comparison is apples-to-apples.  Alert
    history is redirected to a temp dir (``_state_files``); the
    returned ``new_alerts`` are compared to prove semantic equivalence
    (identical alert output, not just similar timing).

    Returns:
        Dict with legacy/batch wall times (best/avg/p50/p95/p99), the
        read/write counts, the speedup ratio and an equivalence flag.
    """
    from src.alerts import engine as alert_engine  # noqa: PLC0415
    from src.alerts import history as alert_history  # noqa: PLC0415
    from src.engine import analyzer as analyzer_mod  # noqa: PLC0415

    with tempfile.TemporaryDirectory(prefix="nepse_api_batch_") as tmp:
        data_dir = Path(tmp) / "data"
        write_csvs(data_dir, symbols, rows)
        with _state_files(Path(tmp)):
            files = sorted(data_dir.glob("*.csv"))

            # ── legacy per-symbol alert path ────────────────────────
            # ``process_alerts`` resolves load_history/save_history from
            # the *engine* namespace (module-level imports), while
            # ``update_state`` resolves save_history from the *history*
            # namespace — so both namespaces must be patched (same
            # approach as ``bench_alert_batch``, Sprint 11.3).
            counts = {"reads": 0, "writes": 0}
            orig_load = alert_history.load_history
            orig_save = alert_history.save_history
            orig_engine_load = alert_engine.load_history
            orig_engine_save = alert_engine.save_history

            def counting_load():
                counts["reads"] += 1
                return orig_load()

            def counting_save(history):
                counts["writes"] += 1
                return orig_save(history)

            def _apply_counters():
                alert_history.load_history = counting_load
                alert_history.save_history = counting_save
                alert_engine.load_history = counting_load
                alert_engine.save_history = counting_save

            def _restore_counters():
                alert_history.load_history = orig_load
                alert_history.save_history = orig_save
                alert_engine.load_history = orig_engine_load
                alert_engine.save_history = orig_engine_save

            _apply_counters()
            legacy_lat: list[float] = []
            legacy_alerts: list[list] = []
            try:
                for f in files:
                    # One call per file: capture timing AND alerts from
                    # the SAME call so the write count is exactly one
                    # per symbol and the alerts are first-scan INITIALs
                    # (a second call would see prior state and return
                    # no new alerts, skewing the equivalence check).
                    start = time.perf_counter()
                    result = analyzer_mod.analyze_stock(str(f))
                    legacy_lat.append((time.perf_counter() - start) * 1000.0)
                    legacy_alerts.append(result.get("new_alerts", []))
            finally:
                _restore_counters()
            legacy_reads, legacy_writes = counts["reads"], counts["writes"]

            # Reset history so the batch also starts from cold state.
            alert_history.HISTORY_FILE.unlink(missing_ok=True)
            counts["reads"] = 0
            counts["writes"] = 0
            _apply_counters()
            batch_alerts: list[list] = []
            try:
                # One call for the whole batch: timing, alerts and the
                # read/write counts all come from this single execution
                # (a second run would double the counted writes).
                start = time.perf_counter()
                batch_results = analyzer_mod.analyze_stock_batch(
                    [str(f) for f in files], use_batch_alerts=True
                )
                batch_total_ms = (time.perf_counter() - start) * 1000.0
                batch_alerts = [r.get("new_alerts", []) for r in batch_results]
            finally:
                _restore_counters()
            batch_reads, batch_writes = counts["reads"], counts["writes"]

            # Semantic equivalence: identical alert payloads both paths.
            legacy_flat = [a for al in legacy_alerts for a in al]
            batch_flat = [a for al in batch_alerts for a in al]
            equivalent = legacy_flat == batch_flat

            legacy_total_ms = sum(legacy_lat)
            return {
                "symbols": len(files),
                "legacy_total_ms": round(legacy_total_ms, 2),
                "legacy_reads": legacy_reads,
                "legacy_writes": legacy_writes,
                "batch_total_ms": round(batch_total_ms, 2),
                "batch_reads": batch_reads,
                "batch_writes": batch_writes,
                "speedup_x": round(legacy_total_ms / batch_total_ms, 2)
                if batch_total_ms
                else 0.0,
                "alerts_equivalent": equivalent,
            }


def bench_scan_alert_equivalence(symbols: int = 8, rows: int = 200) -> dict[str, Any]:
    """Legacy vs batch market-scan alert equivalence (Sprint 12.0 Phase 4).

    For the same deterministic synthetic corpus, runs the two alert
    processing paths that a multi-symbol market scan can use:

    - legacy: a loop of ``analyze_stock(..., with_alerts=True)`` — one
      history read + one write per symbol via ``process_alerts``
    - batch: ``analyze_stock_batch(..., use_batch_alerts=True)`` — one
      history read + one atomic write via ``process_alert_batch``

    Both runs start from a fresh (empty) history and use the same
    corpus, so the comparison is apples-to-apples.  Analyses are
    compared on their stable fields (symbol, signal, score,
    confidence); alerts are compared as complete payloads (deterministic
    in this codebase — type/priority/message only).  Rankings are
    computed with the real ``rank_market``.  Error entries are compared
    directly.

    Returns:
        Dict with the four equivalence flags (analysis/alerts/ranking/
        errors), the read/write counts for both paths, and wall times.
    """
    from src.alerts import engine as alert_engine  # noqa: PLC0415
    from src.alerts import history as alert_history  # noqa: PLC0415
    from src.engine import analyzer as analyzer_mod  # noqa: PLC0415

    with tempfile.TemporaryDirectory(prefix="nepse_scan_eq_") as tmp:
        data_dir = Path(tmp) / "data"
        write_csvs(data_dir, symbols, rows)
        with _state_files(Path(tmp)):
            files = sorted(data_dir.glob("*.csv"))

            counts = {"reads": 0, "writes": 0}
            orig_load = alert_history.load_history
            orig_save = alert_history.save_history
            orig_engine_load = alert_engine.load_history
            orig_engine_save = alert_engine.save_history

            def counting_load():
                counts["reads"] += 1
                return orig_load()

            def counting_save(history):
                counts["writes"] += 1
                return orig_save(history)

            def _apply_counters():
                alert_history.load_history = counting_load
                alert_history.save_history = counting_save
                alert_engine.load_history = counting_load
                alert_engine.save_history = counting_save

            def _restore_counters():
                alert_history.load_history = orig_load
                alert_history.save_history = orig_save
                alert_engine.load_history = orig_engine_load
                alert_engine.save_history = orig_engine_save

            def _normalize(result: dict) -> dict:
                return {
                    "symbol": result.get("symbol"),
                    "signal": result.get("signal"),
                    "score": result.get("score"),
                    "confidence": result.get("confidence"),
                    "error": result.get("error"),
                }

            # ── legacy path ─────────────────────────────────────────
            _apply_counters()
            legacy: list[dict] = []
            legacy_alerts: dict[str, list] = {}
            try:
                start = time.perf_counter()
                for f in files:
                    r = analyzer_mod.analyze_stock(str(f))
                    legacy.append(_normalize(r))
                    legacy_alerts[r["symbol"]] = r.get("new_alerts", [])
                legacy_ms = (time.perf_counter() - start) * 1000.0
            finally:
                _restore_counters()
            legacy_reads, legacy_writes = counts["reads"], counts["writes"]

            # ── batch path (fresh history) ──────────────────────────
            alert_history.HISTORY_FILE.unlink(missing_ok=True)
            counts["reads"] = 0
            counts["writes"] = 0
            _apply_counters()
            batch: list[dict] = []
            batch_alerts: dict[str, list] = {}
            try:
                start = time.perf_counter()
                for r in analyzer_mod.analyze_stock_batch(
                    [str(f) for f in files], use_batch_alerts=True
                ):
                    batch.append(_normalize(r))
                    batch_alerts[r.get("symbol")] = r.get("new_alerts", [])
                batch_ms = (time.perf_counter() - start) * 1000.0
            finally:
                _restore_counters()
            batch_reads, batch_writes = counts["reads"], counts["writes"]

            # ── equivalence ─────────────────────────────────────────
            from src.scanner.ranking import rank_market  # noqa: PLC0415

            def _rank(rows_: list[dict]) -> list[str]:
                return [r["symbol"] for r in rank_market(list(rows_))]

            analysis_equivalent = legacy == batch
            alerts_equivalent = legacy_alerts == batch_alerts
            ranking_equivalent = _rank(legacy) == _rank(batch)
            legacy_errors = [r for r in legacy if r.get("error")]
            batch_errors = [r for r in batch if r.get("error")]
            errors_equivalent = legacy_errors == batch_errors

            return {
                "symbols": len(files),
                "analysis_equivalent": analysis_equivalent,
                "alerts_equivalent": alerts_equivalent,
                "ranking_equivalent": ranking_equivalent,
                "errors_equivalent": errors_equivalent,
                "legacy_total_ms": round(legacy_ms, 2),
                "legacy_reads": legacy_reads,
                "legacy_writes": legacy_writes,
                "batch_total_ms": round(batch_ms, 2),
                "batch_reads": batch_reads,
                "batch_writes": batch_writes,
            }


def bench_scan_alert_batch(
    data_dir: Path,
    symbols: int,
    rows: int,
    populate: bool = True,
    cold_reps: int = 3,
    warm_reps: int = 5,
) -> dict[str, Any]:
    """Market-scan alert path benchmark: legacy vs batch (Sprint 12.0 Phase 5).

    Measures the two alert-processing paths a production market scan can
    use over the same corpus (real or synthetic):

    - legacy: per-symbol ``analyze_stock(..., with_alerts=True)`` loop
    - batch: ``analyze_stock_batch(..., use_batch_alerts=True)``

    Records cold/warm percentiles for both paths (scanner + indicator
    caches cleared per cold rep), history read/write counts, peak
    memory, and per-symbol success/skipped/failed accounting.

    Args:
        data_dir: Directory containing OHLCV CSVs.
        symbols: Symbol count (informational).
        rows: Rows per symbol (synthetic only).
        populate: Write synthetic CSVs first.  ``False`` for real-corpus
            runs so no synthetic files are mixed into the corpus.
        cold_reps: Number of cold-cache repetitions.
        warm_reps: Number of warm-cache repetitions.
    """
    from src.alerts import engine as alert_engine  # noqa: PLC0415
    from src.alerts import history as alert_history  # noqa: PLC0415
    from src.cache.scanner_cache import scanner_cache  # noqa: PLC0415
    from src.engine import analyzer as analyzer_mod  # noqa: PLC0415
    from src.indicators.cache import indicator_cache  # noqa: PLC0415

    if populate:
        write_csvs(data_dir, symbols, rows)
    files = sorted(data_dir.glob("*.csv"))

    counts = {"reads": 0, "writes": 0}
    orig_load = alert_history.load_history
    orig_save = alert_history.save_history
    orig_engine_load = alert_engine.load_history
    orig_engine_save = alert_engine.save_history

    def counting_load():
        counts["reads"] += 1
        return orig_load()

    def counting_save(history):
        counts["writes"] += 1
        return orig_save(history)

    def _apply_counters():
        alert_history.load_history = counting_load
        alert_history.save_history = counting_save
        alert_engine.load_history = counting_load
        alert_engine.save_history = counting_save

    def _restore_counters():
        alert_history.load_history = orig_load
        alert_history.save_history = orig_save
        alert_engine.load_history = orig_engine_load
        alert_engine.save_history = orig_engine_save

    def _measure(path_fn, cold: bool) -> list[float]:
        lat: list[float] = []
        reps = cold_reps if cold else warm_reps
        for _ in range(reps):
            if cold:
                scanner_cache.clear()
                indicator_cache.clear()
            start = time.perf_counter()
            path_fn()
            lat.append((time.perf_counter() - start) * 1000.0)
        return lat

    with tempfile.TemporaryDirectory(prefix="nepse_scan_batch_") as iso:
        with _state_files(Path(iso)):
            # ── legacy ──────────────────────────────────────────────
            _apply_counters()
            try:
                legacy_cold = _measure(
                    lambda: [analyzer_mod.analyze_stock(str(f)) for f in files], cold=True
                )
                legacy_cold_reads, legacy_cold_writes = counts["reads"], counts["writes"]
                counts["reads"] = 0
                counts["writes"] = 0
                legacy_warm = _measure(
                    lambda: [analyzer_mod.analyze_stock(str(f)) for f in files], cold=False
                )
                legacy_warm_reads, legacy_warm_writes = counts["reads"], counts["writes"]
            finally:
                _restore_counters()

            # ── batch (fresh history) ───────────────────────────────
            alert_history.HISTORY_FILE.unlink(missing_ok=True)
            counts["reads"] = 0
            counts["writes"] = 0
            _apply_counters()
            try:
                batch_cold = _measure(
                    lambda: analyzer_mod.analyze_stock_batch(
                        [str(f) for f in files], use_batch_alerts=True
                    ),
                    cold=True,
                )
                batch_cold_reads, batch_cold_writes = counts["reads"], counts["writes"]
                counts["reads"] = 0
                counts["writes"] = 0
                batch_warm = _measure(
                    lambda: analyzer_mod.analyze_stock_batch(
                        [str(f) for f in files], use_batch_alerts=True
                    ),
                    cold=False,
                )
                batch_warm_reads, batch_warm_writes = counts["reads"], counts["writes"]
            finally:
                _restore_counters()

            # ── peak memory (one cold batch run) ────────────────────
            scanner_cache.clear()
            indicator_cache.clear()
            _elapsed, peak = measure_peak_memory(
                lambda: analyzer_mod.analyze_stock_batch(
                    [str(f) for f in files], use_batch_alerts=True
                )
            )

            # Result accounting stays INSIDE the ``_state_files`` context
            # (Sprint 12.0 review fix): ``analyze_stock_batch`` fires the
            # alert engine, and a call made after the context restored
            # ``HISTORY_FILE`` would write corpus symbols into the real
            # ``data/alerts/history.json`` — exactly the pollution
            # ``_state_files`` exists to prevent.
            last = analyzer_mod.analyze_stock_batch(
                [str(f) for f in files], use_batch_alerts=True
            )
            errors = [r for r in last if r.get("error")]

    return {
        "symbols": len(files),
        "legacy": {
            "cold": _percentiles(legacy_cold),
            "warm": _percentiles(legacy_warm),
            "cold_reads": legacy_cold_reads,
            "cold_writes": legacy_cold_writes,
            "warm_reads": legacy_warm_reads,
            "warm_writes": legacy_warm_writes,
        },
        "batch": {
            "cold": _percentiles(batch_cold),
            "warm": _percentiles(batch_warm),
            "cold_reads": batch_cold_reads,
            "cold_writes": batch_cold_writes,
            "warm_reads": batch_warm_reads,
            "warm_writes": batch_warm_writes,
        },
        "cold_peak_mib": round(peak, 2),
        "successful": len(last) - len(errors),
        "skipped": 0,
        "failed": len(errors),
    }


def bench_alert_batch_scale(
    sizes: tuple[int, ...] = (500, 750, 1000),
    rows: int = 300,
    cold_reps: int = 2,
    warm_reps: int = 3,
) -> dict[str, Any]:
    """``process_alert_batch`` scaling at 500+ symbols (Sprint 12.1 Phase 3).

    The real scraped corpus ships 287 CSVs, so 500/750/1000-symbol runs
    use a **synthetic** corpus (clearly labelled ``corpus: synthetic``)
    generated in private temp dirs.  Each size reuses the exact
    ``bench_scan_alert_batch`` measurement (cold/warm percentiles,
    alert-history read/write counts, peak memory, failures) with
    reduced repetitions so a 1000-symbol run stays tractable.

    Returns:
        A dict with ``sizes`` (list of per-size results, each a
        ``bench_scan_alert_batch`` dict plus ``symbols``) and
        ``scaling`` — per-symbol cost estimates (cold p50 / symbols,
        warm p50 / symbols) that reveal whether the batch path scales
        approximately linearly, sub-linearly or super-linearly.
    """
    from src.cache.scanner_cache import scanner_cache  # noqa: PLC0415
    from src.indicators.cache import indicator_cache  # noqa: PLC0415

    per_size: list[dict[str, Any]] = []
    for n in sizes:
        with tempfile.TemporaryDirectory(prefix=f"nepse_scale_{n}_") as tmp:
            data_dir = Path(tmp) / "data"
            write_csvs(data_dir, n, rows)
            # Caches are process-global; clear between sizes so a
            # 1000-symbol run's warm phase cannot reuse a smaller
            # run's fingerprints.
            scanner_cache.clear()
            indicator_cache.clear()
            result = bench_scan_alert_batch(
                data_dir,
                n,
                rows,
                populate=False,
                cold_reps=cold_reps,
                warm_reps=warm_reps,
            )
            result["corpus"] = "synthetic"
            per_size.append(result)

    scaling: dict[str, Any] = {}
    for r in per_size:
        n = r["symbols"]
        cold_p50 = r["batch"]["cold"]["p50_ms"]
        warm_p50 = r["batch"]["warm"]["p50_ms"]
        scaling[str(n)] = {
            "cold_p50_per_symbol_ms": round(cold_p50 / n, 3) if n else 0.0,
            "warm_p50_per_symbol_ms": round(warm_p50 / n, 3) if n else 0.0,
        }

    # Super-linearity check: if per-symbol cost grows with n, the batch
    # path is not scaling linearly (e.g. history re-reads inside the
    # loop or O(n^2) state).
    ordered = sorted(per_size, key=lambda r: r["symbols"])
    if len(ordered) >= 2:
        first, last = ordered[0], ordered[-1]
        n_ratio = last["symbols"] / first["symbols"]
        cold_cost_ratio = (
            scaling[str(last["symbols"])]["cold_p50_per_symbol_ms"]
            / scaling[str(first["symbols"])]["cold_p50_per_symbol_ms"]
            if scaling[str(first["symbols"])]["cold_p50_per_symbol_ms"]
            else 0.0
        )
        scaling["summary"] = {
            "largest_to_smallest_symbol_ratio": round(n_ratio, 2),
            "largest_to_smallest_cold_per_symbol_ratio": round(cold_cost_ratio, 2),
            # ratio ~1.0 => linear; >1.0 => super-linear (cost grows
            # faster than symbols); <1.0 => sub-linear.
            "observation": (
                "super-linear"
                if cold_cost_ratio > 1.15
                else "sub-linear" if cold_cost_ratio < 0.85 else "approximately-linear"
            ),
        }

    return {"sizes": per_size, "scaling": scaling, "corpus": "synthetic"}


def bench_api_portfolio() -> dict[str, Any]:
    """/api/portfolio latency against an isolated (empty) portfolio."""
    from fastapi.testclient import TestClient  # noqa: PLC0415
    from src.api.main import app  # noqa: PLC0415

    with tempfile.TemporaryDirectory(prefix="nepse_port_") as tmp:
        with _state_files(Path(tmp)):
            client = TestClient(app)
        client.get("/")
        latencies: list[float] = []
        for _ in range(5):
            latencies.append(timeit(lambda: client.get("/portfolio/"), repeats=1)[0])
        latencies.sort()
        return {"api_portfolio_avg_ms": round(sum(latencies) / len(latencies), 3)}


def bench_cache_hit_miss(data_dir: Path, symbols: int, rows: int) -> dict[str, Any]:
    """DataService cache miss vs hit for history retrieval.

    Synthetic files are written into a private ``_syn_cache`` subdir so
    they are never mixed into a real-data corpus (the scanner glob is
    non-recursive and only reads ``*.csv`` at the top level).
    """
    from src.data import DataService  # noqa: PLC0415
    from src.data.cache import TieredCache  # noqa: PLC0415
    from src.data.providers import CSVProvider  # noqa: PLC0415

    syn_dir = data_dir / "_syn_cache"
    write_csvs(syn_dir, symbols, rows)
    DataService.reset_instance()
    # Explicit in-memory cache (no disk tier) so the benchmark never
    # writes cache files into the real ~/.nepse/cache directory.
    svc = DataService(provider=CSVProvider(data_dir=str(syn_dir)), cache=TieredCache(memory_ttl=60, disk_ttl=600))
    symbol = "SYN000"

    svc.clear_cache()
    miss_best, _ = timeit(lambda: svc.get_history(symbol, days=rows), repeats=1)
    hit_best, hit_avg = timeit(lambda: svc.get_history(symbol, days=rows), repeats=3)
    return {
        "cache_miss_ms": round(miss_best, 3),
        "cache_hit_avg_ms": round(hit_avg, 3),
        "cache_hit_best_ms": round(hit_best, 3),
    }


def bench_alert_batch(symbols: int = 50) -> dict[str, Any]:
    """Scan-level alert batching: per-symbol vs one-shot processing.

    Sprint 11.3: measures wall time AND the number of alert-history
    reads/writes for processing *symbols* analyses through
    ``process_alerts`` (one read + one write per symbol) versus
    ``process_alert_batch`` (one read + one write for the whole set).

    History persistence is redirected to a temp dir (``_state_files``)
    and the functions are wrapped with counters, so production state is
    never touched and the I/O counts are exact.  Both runs start from a
    cold (empty) history file.
    """
    from src.alerts import engine as alert_engine  # noqa: PLC0415
    from src.alerts import history as alert_history  # noqa: PLC0415

    def _payload(i: int) -> dict:
        price = 100.0 + i
        return {
            "signal": "BUY",
            "confidence": 90,
            "score": 7,
            "best_rr": 3.2,
            "volume_signal": "VOLUME_SPIKE",
            "relative_volume": 2.1,
            "pattern_type": "Bullish",
            "pattern": "Bullish Engulfing",
            "trend": "UPTREND",
            "price": price,
            "target1": price + 5.0,
            "target2": price + 10.0,
            "target3": price + 15.0,
            "milestones": {"target1": False, "target2": False, "target3": False},
        }

    entries = [(f"SYN{i:04d}", _payload(i)) for i in range(symbols)]

    with tempfile.TemporaryDirectory(prefix="nepse_alert_") as tmp:
        with _state_files(Path(tmp)):
            counts = {"reads": 0, "writes": 0}
            orig_load = alert_history.load_history
            orig_save = alert_history.save_history
            orig_engine_load = alert_engine.load_history
            orig_engine_save = alert_engine.save_history

            def counting_load():
                counts["reads"] += 1
                return orig_load()

            def counting_save(history):
                counts["writes"] += 1
                return orig_save(history)

            try:
                # Patch both namespaces: process_alerts/process_alert_batch
                # call load_history/save_history from engine's namespace,
                # while update_state resolves save_history in history's.
                alert_history.load_history = counting_load
                alert_history.save_history = counting_save
                alert_engine.load_history = counting_load
                alert_engine.save_history = counting_save

                counts["reads"] = 0
                counts["writes"] = 0
                per_ms, _ = timeit(
                    lambda: [alert_engine.process_alerts(s, r) for s, r in entries],
                    repeats=1,
                )
                per_reads, per_writes = counts["reads"], counts["writes"]

                # Reset history so the batch also starts from cold state.
                alert_history.HISTORY_FILE.unlink(missing_ok=True)
                counts["reads"] = 0
                counts["writes"] = 0
                batch_ms, _ = timeit(
                    lambda: alert_engine.process_alert_batch(entries),
                    repeats=1,
                )
                batch_reads, batch_writes = counts["reads"], counts["writes"]
            finally:
                alert_history.load_history = orig_load
                alert_history.save_history = orig_save
                alert_engine.load_history = orig_engine_load
                alert_engine.save_history = orig_engine_save

    return {
        "symbols": symbols,
        "per_symbol_ms": round(per_ms, 2),
        "per_symbol_reads": per_reads,
        "per_symbol_writes": per_writes,
        "batch_ms": round(batch_ms, 2),
        "batch_reads": batch_reads,
        "batch_writes": batch_writes,
        "speedup_x": round(per_ms / batch_ms, 2) if batch_ms else 0.0,
    }


def bench_cold_load(
    data_dir: Path,
    symbols: int,
    reps: int = 5,
) -> dict[str, Any]:
    """Cold CSV load percentiles + peak memory (Sprint 11.6, Phase 2).

    Measures cold ``load_csv`` over *symbols* files independently from
    analysis, with the scanner cache cleared before every repetition so
    each run is a true cold path.  Reports best/avg/p50/p95/p99, peak
    memory, total rows and per-symbol successful/skipped/failed counts
    (Phase 2: failed = raising files, skipped = empty frames).

    Args:
        data_dir: Directory containing OHLCV CSVs.
        symbols: Symbol count (clamped to available files).
        reps: Number of cold repetitions for the percentile sample.
    """
    import gc
    import time

    from src.cache.scanner_cache import scanner_cache  # noqa: PLC0415
    from src.loaders.csv_loader import load_csv  # noqa: PLC0415

    files = sorted(data_dir.glob("*.csv"))[:symbols]
    if not files:
        return {
            "symbols": 0,
            "cold": _percentiles([]),
            "cold_peak_mib": 0.0,
            "total_rows": 0,
            "avg_rows_per_symbol": 0.0,
            "successful": 0,
            "skipped": 0,
            "failed": 0,
        }

    # One pass to count rows and success/skip/failed (an empty frame is
    # a *skipped* symbol; a raising file is a *failed* symbol — Phase 2
    # asks for the three counters separately).  The same per-file
    # isolation is applied inside the timed repetitions so one bad file
    # cannot abort the run.
    scanner_cache.clear()
    frames: dict[Path, pd.DataFrame] = {}
    failed = 0
    for p in files:
        try:
            frames[p] = load_csv(p)
        except Exception:  # noqa: BLE001 - per-file isolation
            failed += 1
            frames[p] = pd.DataFrame()
    total_rows = sum(len(df) for df in frames.values())
    successful = sum(1 for df in frames.values() if not df.empty)
    skipped = len(files) - successful - failed
    # Free the parsed frames before timing so retained memory cannot
    # skew the cold-load measurements (GC / allocation pressure).
    del frames
    gc.collect()

    # Cold timing repetitions (cache cleared per rep).  Per-file
    # exceptions are ignored inside the timed window (already counted).
    latencies: list[float] = []
    for _ in range(reps):
        scanner_cache.clear()
        gc.collect()
        start = time.perf_counter()
        for p in files:
            try:
                load_csv(p)
            except Exception:  # noqa: BLE001 - per-file isolation
                pass
        latencies.append((time.perf_counter() - start) * 1000.0)

    scanner_cache.clear()
    _elapsed, peak = measure_peak_memory(lambda: [load_csv(p) for p in files])

    return {
        "symbols": len(files),
        "cold": _percentiles(latencies),
        "cold_peak_mib": round(peak, 2),
        "total_rows": total_rows,
        "avg_rows_per_symbol": round(total_rows / len(files), 1),
        "successful": successful,
        "skipped": skipped,
        "failed": failed,
    }


def bench_csv_stages(data_dir: Path, symbols: int) -> dict[str, Any]:
    """Stage-by-stage profile of the cold CSV load path (Sprint 11.6).

    Breaks the cold ``load_csv`` pipeline into measurable stages and
    reports each stage's average ms and its share of the total cold
    load: filesystem read, header detection, ``read_csv`` (the pandas
    C-parser fast path — the real parse for clean files), column strip,
    numeric conversion, datetime, sort, dropna, cache write, copy.

    Args:
        data_dir: Directory containing OHLCV CSVs.
        symbols: Symbol count (informational).
    """
    import gc
    import time

    from src.cache.scanner_cache import scanner_cache  # noqa: PLC0415
    from src.loaders import csv_loader  # noqa: PLC0415
    from src.loaders.csv_loader import _find_header_idx, _read_csv_robust  # noqa: PLC0415

    files = sorted(data_dir.glob("*.csv"))[:symbols]
    if not files:
        return {"symbols": 0, "stages": {}, "total_load_avg_ms": 0.0}

    stage_times: dict[str, list[float]] = {k: [] for k in (
        "filesystem_read",
        "split_header",
        "read_csv",
        "strip_cols",
        "numeric",
        "datetime",
        "sort",
        "dropna",
        "cache_write",
        "copy",
        "total_load_csv",
    )}

    def _timed(fn) -> float:
        gc.collect()
        start = time.perf_counter()
        fn()
        return (time.perf_counter() - start) * 1000.0

    for p in files:
        d1 = _timed(lambda: p.read_text(encoding="utf-8-sig", errors="replace"))
        text = p.read_text(encoding="utf-8-sig", errors="replace")
        lines = text.splitlines()
        d2 = _timed(lambda: _find_header_idx(lines))

        # The real parse: for clean files this is the pandas C-parser
        # fast path; for comma/quote files it is the tolerant schema
        # parser (the fast path falls back internally).
        d3 = _timed(lambda: _read_csv_robust(p))
        df = _read_csv_robust(p)

        d4 = _timed(lambda: df.columns.str.strip())
        df.columns = df.columns.str.strip()

        def _numeric():
            for col in ("Open", "High", "Low", "Close", "Volume"):
                if col not in df.columns:
                    continue
                s = df[col]
                if s.dtype.kind in "iuf":
                    continue
                if s.dtype.kind == "O":
                    if s.str.contains(",", regex=False).any():
                        s = s.astype(str).str.replace(",", "", regex=False)
                    df[col] = pd.to_numeric(s, errors="coerce")
                else:
                    df[col] = pd.to_numeric(
                        df[col].astype(str).str.replace(",", "", regex=False),
                        errors="coerce",
                    )

        d5 = _timed(_numeric)

        def _datetime():
            if "Date" in df.columns:
                df["Date"] = pd.to_datetime(df["Date"], errors="coerce")

        d6 = _timed(_datetime)
        d7 = _timed(lambda: df.sort_values("Date"))
        d8 = _timed(lambda: df.dropna(subset=["Close"]))
        d9 = _timed(lambda: scanner_cache.put_dataframe(p, df))
        d10 = _timed(lambda: df.copy())

        scanner_cache.clear()
        d11 = _timed(lambda: csv_loader.load_csv(p))
        scanner_cache.clear()

        stage_times["filesystem_read"].append(d1)
        stage_times["split_header"].append(d2)
        stage_times["read_csv"].append(d3)
        stage_times["strip_cols"].append(d4)
        stage_times["numeric"].append(d5)
        stage_times["datetime"].append(d6)
        stage_times["sort"].append(d7)
        stage_times["dropna"].append(d8)
        stage_times["cache_write"].append(d9)
        stage_times["copy"].append(d10)
        stage_times["total_load_csv"].append(d11)

    def _avg(key: str) -> float:
        values = stage_times[key]
        return round(sum(values) / len(values), 3) if values else 0.0

    total = _avg("total_load_csv")
    stages: dict[str, Any] = {}
    for key in (
        "filesystem_read",
        "split_header",
        "read_csv",
        "strip_cols",
        "numeric",
        "datetime",
        "sort",
        "dropna",
        "cache_write",
        "copy",
    ):
        avg = _avg(key)
        stages[key] = {
            "avg_ms": avg,
            "pct_of_load": round(avg / total * 100.0, 1) if total else 0.0,
        }
    return {
        "symbols": len(files),
        "stages": stages,
        "total_load_avg_ms": total,
    }


def bench_history_batch(data_dir: Path, symbols: int, rows: int) -> dict[str, Any]:
    """DataService ``get_history_batch`` over all symbols (cache cold).

    Migrated from the Sprint 11 ``scripts/benchmarks.py``.
    """
    from src.data import DataService  # noqa: PLC0415
    from src.data.cache import TieredCache  # noqa: PLC0415
    from src.data.providers import CSVProvider  # noqa: PLC0415

    write_csvs(data_dir, symbols, rows)
    DataService.reset_instance()
    svc = DataService(provider=CSVProvider(data_dir=str(data_dir)), cache=TieredCache(memory_ttl=60, disk_ttl=600))
    all_symbols = [f"SYN{i:03d}" for i in range(symbols)]
    svc.clear_cache()
    best, _ = timeit(lambda: svc.get_history_batch(all_symbols, days=rows), repeats=1)
    return {"history_batch_ms": round(best, 2), "symbols": symbols}


def bench_memory_scan(data_dir: Path, symbols: int) -> dict[str, Any]:
    """Peak memory of a cold scan under tracemalloc.

    Migrated from the Sprint 11 ``scripts/benchmarks.py`` so the old
    script can be a thin wrapper without duplicated logic.  Alert-history
    / portfolio persistence is redirected to a temp dir (``_state_files``)
    so the cold scan never writes synthetic symbols into production state.
    """
    from src.cache.scanner_cache import scanner_cache  # noqa: PLC0415
    from src.scanner import engine as scanner_engine  # noqa: PLC0415

    old_dir = scanner_engine.DATA_DIRECTORY
    scanner_engine.DATA_DIRECTORY = str(data_dir)
    with tempfile.TemporaryDirectory(prefix="nepse_iso_") as iso_tmp:
        with _state_files(Path(iso_tmp)):
            try:
                scanner_cache.clear()
                elapsed, peak = measure_peak_memory(lambda: scanner_engine.scan_market())
                return {
                    "scan_peak_memory_mb": round(peak, 2),
                    "scan_cold_ms": round(elapsed, 2),
                }
            finally:
                scanner_engine.DATA_DIRECTORY = old_dir


def bench_api_health() -> dict[str, Any]:
    """API health-route latency (avg + p95) via the in-process TestClient.

    Migrated from the Sprint 11 ``scripts/benchmarks.py``.
    """
    from fastapi.testclient import TestClient  # noqa: PLC0415
    from src.api.main import app  # noqa: PLC0415

    client = TestClient(app)
    client.get("/")  # warm-up
    latencies: list[float] = []
    for _ in range(20):
        latencies.append(timeit(lambda: client.get("/"), repeats=1)[0])
    latencies.sort()
    n = len(latencies)
    return {
        "api_health_avg_ms": round(sum(latencies) / n, 3),
        "api_health_p95_ms": round(latencies[min(n - 1, int(n * 0.95))], 3),
    }


def bench_startup() -> dict[str, Any]:
    """Fresh-subprocess import time of the FastAPI app.

    Migrated from the Sprint 11 ``scripts/benchmarks.py``.  This metric
    is **informational only** — it is not part of the CI performance
    gate (``benchmarks.ci_gate`` never reads ``startup``), so a slow or
    failed probe must never fail the benchmark runner or the test suite
    that exercises it (Sprint 13.5 determinism: the flaky
    ``test_benchmark_runner_smoke`` was caused by this subprocess import
    exceeding the 120 s timeout under full-suite load and raising).
    Failures are recorded as ``error`` and ``startup_ms=None`` instead
    of raising; consumers already treat a non-numeric ``startup_ms`` as
    ``n/a``.
    """
    import subprocess  # noqa: PLC0415
    import sys  # noqa: PLC0415

    code = "import src.api.main"
    start = time.perf_counter()
    try:
        subprocess.run(
            [sys.executable, "-c", code],
            cwd=str(Path(__file__).resolve().parent.parent),
            capture_output=True,
            timeout=120,
            check=True,
        )
    except subprocess.TimeoutExpired:
        return {"startup_ms": None, "error": "subprocess timed out (system load)"}
    except subprocess.CalledProcessError as exc:
        return {"startup_ms": None, "error": f"subprocess import failed ({exc.returncode})"}
    except Exception as exc:  # noqa: BLE001 - informational probe must never raise
        return {"startup_ms": None, "error": str(exc)}
    return {"startup_ms": round((time.perf_counter() - start) * 1000.0, 1)}
