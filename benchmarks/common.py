"""Shared utilities for the benchmark suite: synthetic data, timing and memory."""

from __future__ import annotations

import contextlib
import gc
import threading
import time
import tracemalloc
from pathlib import Path
from typing import Any, Callable, Iterator

import numpy as np
import pandas as pd


def make_synthetic_df(rows: int = 500, seed: int = 42, start: str = "2023-01-02") -> pd.DataFrame:
    """Build a deterministic OHLCV DataFrame for benchmarking."""
    rng = np.random.default_rng(seed)
    dates = pd.bdate_range(start=start, periods=rows)
    drift = np.linspace(0, rows * 0.05, rows)
    close = 100 + drift + np.cumsum(rng.normal(0, 1.5, rows))
    open_ = close + rng.normal(0, 1.0, rows)
    high = np.maximum(open_, close) + np.abs(rng.normal(0, 1.0, rows))
    low = np.minimum(open_, close) - np.abs(rng.normal(0, 1.0, rows))
    volume = rng.integers(100_000, 5_000_000, rows)
    return pd.DataFrame(
        {
            "Date": dates,
            "Open": open_.round(2),
            "High": high.round(2),
            "Low": low.round(2),
            "Close": close.round(2),
            "Volume": volume,
        }
    )


def write_csvs(data_dir: Path, symbols: int, rows: int) -> list[Path]:
    """Write *symbols* synthetic OHLCV CSVs into *data_dir*."""
    data_dir.mkdir(parents=True, exist_ok=True)
    paths: list[Path] = []
    for i in range(symbols):
        df = make_synthetic_df(rows=rows, seed=1000 + i)
        path = data_dir / f"SYN{i:03d}.csv"
        df.to_csv(path, index=False)
        paths.append(path)
    return paths


def timeit(fn: Callable[[], Any], repeats: int = 3) -> tuple[float, float]:
    """Return ``(best_ms, avg_ms)`` over *repeats* runs of ``fn``."""
    best = float("inf")
    total = 0.0
    for _ in range(repeats):
        gc.collect()
        start = time.perf_counter()
        fn()
        elapsed = (time.perf_counter() - start) * 1000.0
        best = min(best, elapsed)
        total += elapsed
    return best, total / repeats


def measure_peak_memory(fn: Callable[[], Any]) -> tuple[float, float]:
    """Return ``(elapsed_ms, peak_mib)`` for one run of ``fn``."""
    gc.collect()
    tracemalloc.start()
    start = time.perf_counter()
    fn()
    elapsed_ms = (time.perf_counter() - start) * 1000.0
    _current, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    return elapsed_ms, peak / (1024 * 1024)


# Serialises the module-global sections of concurrent benchmark runs
# (scanner DATA_DIRECTORY, alert/portfolio state files, DataService
# singleton).  Concurrent ``run_all`` calls are safe: they serialise on
# the globals they must touch rather than corrupting each other
# (Sprint 13.5 determinism regression: concurrent execution must not
# produce order-dependent or corrupted results).
_BENCH_ISOLATION_LOCK = threading.RLock()


@contextlib.contextmanager
def benchmark_isolation(iso_dir: str | Path) -> Iterator[Path]:
    """Isolate a benchmark run from production state (Sprint 13.5).

    Benchmarks must never leak shared module state into the caller or
    other tests.  This context manager:

    1. redirects alert-history / portfolio persistence into *iso_dir*
       (so synthetic symbols never touch production JSON state),
    2. snapshots and restores ``scanner.engine.DATA_DIRECTORY`` (the
       benchmark legs temporarily point the scanner at a synthetic
       corpus),
    3. clears the shared scanner cache on entry and exit,
    4. resets the ``DataService`` singleton on exit so a later caller
       never inherits a synthetic provider / cache from the benchmark.

    The same guarantees are already applied *per-benchmark-leg* inside
    ``benchmarks/pipeline.py`` (``_state_files``); this wrapper adds the
    suite-level guarantees so a whole ``run_all`` is safe to invoke
    in-process (e.g. from ``test_benchmark_runner_smoke``) without
    leaking state that would make other tests order-dependent.  The
    global-mutating section is serialised with a module-level lock so
    concurrent invocation is safe.

    Usage::

        with benchmark_isolation(tmp_path) as iso:
            results = run_all(symbols=4, rows=60, results_dir=tmp_path)
    """
    from src.cache.scanner_cache import scanner_cache  # noqa: PLC0415 - lazy
    from src.scanner import engine as scanner_engine  # noqa: PLC0415 - lazy

    iso = Path(iso_dir)
    iso.mkdir(parents=True, exist_ok=True)

    from src.alerts import engine as alert_engine  # noqa: PLC0415 - lazy
    from src.alerts import history as alerts_history  # noqa: PLC0415 - lazy
    from src.portfolio import holdings as portfolio_holdings  # noqa: PLC0415 - lazy

    old_history = alerts_history.HISTORY_FILE
    old_engine_history = alert_engine.HISTORY_FILE
    old_portfolio = portfolio_holdings.PORTFOLIO_FILE
    old_scanner_dir = scanner_engine.DATA_DIRECTORY

    with _BENCH_ISOLATION_LOCK:
        try:
            scanner_cache.clear()
            alerts_history.HISTORY_FILE = iso / "alerts" / "history.json"
            alert_engine.HISTORY_FILE = alerts_history.HISTORY_FILE
            portfolio_holdings.PORTFOLIO_FILE = iso / "portfolio.json"
            yield iso
        finally:
            scanner_cache.clear()
            alerts_history.HISTORY_FILE = old_history
            alert_engine.HISTORY_FILE = old_engine_history
            portfolio_holdings.PORTFOLIO_FILE = old_portfolio
            scanner_engine.DATA_DIRECTORY = old_scanner_dir
            # Reset the singleton so a later caller never inherits a
            # synthetic provider / cache left behind by the benchmark legs.
            try:
                from src.data import DataService  # noqa: PLC0415 - lazy

                DataService.reset_instance()
            except Exception:  # noqa: BLE001 - isolation cleanup must never mask the run
                pass
