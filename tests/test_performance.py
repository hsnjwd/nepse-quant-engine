"""Sprint 11 — performance, scalability and observability regression tests.

These tests assert *behavioural* properties of the performance
changes (parallelism, caching, determinism, configurability) without
timing assertions, so they stay fast and deterministic in CI.
"""

from __future__ import annotations

import time
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from src.cache.scanner_cache import ScannerCache, scanner_cache
from src.config import (
    HISTORY_CACHE_TTL,
    SCANNER_CACHE_MAX_ENTRIES,
    SCANNER_CACHE_TTL,
    SCANNER_WORKERS,
)


# ───────────────────────────────────────────────────────────────────
# Fixtures / helpers
# ───────────────────────────────────────────────────────────────────


def _write_ohlcv(path: Path, rows: int = 120, seed: int = 1) -> Path:
    rng = np.random.default_rng(seed)
    dates = pd.bdate_range(start="2024-01-01", periods=rows)
    close = 100 + np.cumsum(rng.normal(0, 1, rows))
    df = pd.DataFrame(
        {
            "Date": dates,
            "Open": close,
            "High": close + 1,
            "Low": close - 1,
            "Close": close,
            "Volume": rng.integers(1000, 5000, rows),
        }
    )
    path.write_text(df.to_csv(index=False), encoding="utf-8")
    return path


@pytest.fixture(autouse=True)
def _clean_scanner_cache():
    scanner_cache.clear()
    yield
    scanner_cache.clear()


# ───────────────────────────────────────────────────────────────────
# Task 1 — parallel scanner
# ───────────────────────────────────────────────────────────────────


class TestParallelScanner:
    @pytest.fixture(autouse=True)
    def _isolate_alert_history(self, monkeypatch, tmp_path):
        # The scan-level alert batch (Sprint 11.3) writes the alert
        # history once per scan; redirect it so scanner tests never
        # touch the real data/alerts/history.json.
        from src.alerts import history as alerts_history

        monkeypatch.setattr(
            alerts_history, "HISTORY_FILE", tmp_path / "alerts" / "history.json"
        )

    def test_scan_market_parallel_preserves_determinism(self, monkeypatch, tmp_path):
        from src.scanner import engine

        monkeypatch.setattr(engine, "DATA_DIRECTORY", str(tmp_path))
        for i in range(6):
            _write_ohlcv(tmp_path / f"STOCK{i}.csv", rows=60, seed=i)

        calls: list[str] = []

        def fake_analyze_stock(path, with_alerts=True):
            calls.append(path)
            time.sleep(0.01)  # force interleaving across workers
            return {"score": 5, "signal": "HOLD", "confidence": 50}

        monkeypatch.setattr(engine, "analyze_stock", fake_analyze_stock)

        result = engine.scan_market()

        # Deterministic: results are in file order regardless of threads.
        symbols = [r["symbol"] for r in result["results"]]
        assert symbols == [f"STOCK{i}" for i in range(6)]
        assert result["skipped"] == []

    def test_scan_market_parallel_isolation(self, monkeypatch, tmp_path):
        from src.scanner import engine

        monkeypatch.setattr(engine, "DATA_DIRECTORY", str(tmp_path))
        _write_ohlcv(tmp_path / "GOOD1.csv", rows=60)
        _write_ohlcv(tmp_path / "BAD.csv", rows=60)
        _write_ohlcv(tmp_path / "GOOD2.csv", rows=60)

        def fake_analyze_stock(path, with_alerts=True):
            if "BAD" in path:
                raise ValueError("boom")
            return {"score": 5, "signal": "BUY"}

        monkeypatch.setattr(engine, "analyze_stock", fake_analyze_stock)

        result = engine.scan_market()
        assert [r["symbol"] for r in result["results"]] == ["GOOD1", "GOOD2"]
        assert [s["symbol"] for s in result["skipped"]] == ["BAD"]
        assert result["skipped"][0]["error"] == "boom"

    def test_scan_market_worker_count_config(self, monkeypatch, tmp_path):
        from src.scanner import engine

        monkeypatch.setattr(engine, "SCANNER_WORKERS", 3)
        monkeypatch.setattr(engine, "DATA_DIRECTORY", str(tmp_path))
        for i in range(4):
            _write_ohlcv(tmp_path / f"S{i}.csv", rows=60)

        # Fake analysis keeps the test hermetic — the real analyze_stock
        # writes to data/alerts/history.json (shared state pollution).
        monkeypatch.setattr(
            engine,
            "analyze_stock",
            lambda path, with_alerts=True: {"score": 5, "signal": "HOLD"},
        )

        created: list[int] = []

        class RecordingPool:
            def __init__(self, max_workers=1, thread_name_prefix=""):
                created.append(max_workers)

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def map(self, fn, iterable):
                return [fn(item) for item in iterable]

        monkeypatch.setattr(engine, "ThreadPoolExecutor", RecordingPool)
        engine.scan_market()
        assert created == [3]

    def test_scan_market_sequential_equals_parallel(self, monkeypatch, tmp_path):
        """Sequential fallback must produce identical results to parallel."""
        from src.scanner import engine

        monkeypatch.setattr(engine, "DATA_DIRECTORY", str(tmp_path))
        for i in range(6):
            _write_ohlcv(tmp_path / f"STOCK{i}.csv", rows=60, seed=i)

        def fake_analyze_stock(path, with_alerts=True):
            stem = Path(path).stem
            return {"score": len(stem) % 5, "signal": "BUY" if len(stem) % 2 else "HOLD", "confidence": 50}

        monkeypatch.setattr(engine, "analyze_stock", fake_analyze_stock)

        parallel = engine.scan_market(workers=4)
        sequential = engine.scan_market(workers=1)

        assert [r["symbol"] for r in parallel["results"]] == [r["symbol"] for r in sequential["results"]]
        assert [r["composite_rank"] for r in parallel["results"]] == [
            r["composite_rank"] for r in sequential["results"]
        ]
        assert parallel["skipped"] == sequential["skipped"] == []

    def test_scan_market_empty_input(self, monkeypatch, tmp_path):
        """A scan with no CSV files returns empty results without error."""
        from src.scanner import engine

        monkeypatch.setattr(engine, "DATA_DIRECTORY", str(tmp_path))

        parallel = engine.scan_market(workers=4)
        sequential = engine.scan_market(workers=1)
        assert parallel["results"] == []
        assert parallel["skipped"] == []
        assert sequential == parallel

    def test_scan_market_one_symbol(self, monkeypatch, tmp_path):
        """A single-symbol scan works in both parallel and sequential modes."""
        from src.scanner import engine

        monkeypatch.setattr(engine, "DATA_DIRECTORY", str(tmp_path))
        _write_ohlcv(tmp_path / "ONLY.csv", rows=60)
        monkeypatch.setattr(
            engine,
            "analyze_stock",
            lambda path, with_alerts=True: {"score": 5, "signal": "BUY"},
        )

        for workers in (1, 2):
            result = engine.scan_market(workers=workers)
            assert [r["symbol"] for r in result["results"]] == ["ONLY"]
            assert result["skipped"] == []

    def test_scan_market_sequential_isolation(self, monkeypatch, tmp_path):
        """Sequential mode keeps per-file exception isolation."""
        from src.scanner import engine

        monkeypatch.setattr(engine, "DATA_DIRECTORY", str(tmp_path))
        _write_ohlcv(tmp_path / "GOOD1.csv", rows=60)
        _write_ohlcv(tmp_path / "BAD.csv", rows=60)
        _write_ohlcv(tmp_path / "GOOD2.csv", rows=60)

        def fake_analyze_stock(path, with_alerts=True):
            if "BAD" in path:
                raise RuntimeError("sequential boom")
            return {"score": 5, "signal": "BUY"}

        monkeypatch.setattr(engine, "analyze_stock", fake_analyze_stock)

        result = engine.scan_market(workers=1)
        assert [r["symbol"] for r in result["results"]] == ["GOOD1", "GOOD2"]
        assert [s["symbol"] for s in result["skipped"]] == ["BAD"]
        assert "sequential boom" in result["skipped"][0]["error"]

    def test_scan_market_repeated_scans_cache_interaction(self, monkeypatch, tmp_path):
        """Repeated scans in both modes hit the analysis cache identically."""
        from src.scanner import engine

        monkeypatch.setattr(engine, "DATA_DIRECTORY", str(tmp_path))
        for i in range(4):
            _write_ohlcv(tmp_path / f"C{i}.csv", rows=60, seed=i)

        calls = {"n": 0}

        def fake(path, with_alerts=True):
            calls["n"] += 1
            return {"score": 5, "signal": "HOLD"}

        monkeypatch.setattr(engine, "analyze_stock", fake)

        engine.scan_market(workers=1)   # cold -> 4 calls
        engine.scan_market(workers=4)   # warm -> cache hits
        engine.scan_market(workers=1)   # still warm -> cache hits
        assert calls["n"] == 4


# ───────────────────────────────────────────────────────────────────
# Task 2 — indicator pipeline
# ───────────────────────────────────────────────────────────────────


class TestIndicatorPipeline:
    def test_inplace_indicators_equal_copy_versions(self):
        from src.indicators.momentum import add_momentum_indicators
        from src.indicators.moving_average import add_moving_averages
        from src.indicators.volatility import add_volatility_indicators
        from src.indicators.volume import add_volume_indicators

        # Build a DataFrame directly (no file I/O in this test).
        rng = np.random.default_rng(7)
        close = 100 + np.cumsum(rng.normal(0, 1, 250))
        df = pd.DataFrame(
            {
                "Date": pd.bdate_range(start="2024-01-01", periods=250),
                "Open": close,
                "High": close + 1,
                "Low": close - 1,
                "Close": close,
                "Volume": rng.integers(1000, 5000, 250),
            }
        )

        for fn in (
            add_moving_averages,
            add_momentum_indicators,
            add_volatility_indicators,
            add_volume_indicators,
        ):
            copy_result = fn(df.copy())
            inplace_result = fn(df.copy(), inplace=True)
            pd.testing.assert_frame_equal(copy_result, inplace_result)
            # inplace=True must not change the result vs copy semantics.
            assert list(copy_result.columns) == list(inplace_result.columns)

    def test_analyze_dataframe_does_not_mutate_input(self):
        from src.engine.analyzer import analyze_dataframe

        rng = np.random.default_rng(3)
        close = 100 + np.cumsum(rng.normal(0, 1, 200))
        df = pd.DataFrame(
            {
                "Date": pd.bdate_range(start="2024-01-01", periods=200),
                "Open": close,
                "High": close + 1,
                "Low": close - 1,
                "Close": close,
                "Volume": rng.integers(1000, 5000, 200),
            }
        )
        snapshot = df.copy()
        result = analyze_dataframe(df)
        assert result["score"] >= -10
        pd.testing.assert_frame_equal(df, snapshot)  # no in-place mutation of caller frame

    def test_atr_numeric_accuracy(self):
        from src.indicators.volatility import add_atr

        rng = np.random.default_rng(11)
        close = 100 + np.cumsum(rng.normal(0, 1, 150))
        df = pd.DataFrame(
            {
                "Open": close,
                "High": close + 1.5,
                "Low": close - 1.5,
                "Close": close,
            }
        )
        result = add_atr(df)
        assert result["ATR"].notna().sum() > 100  # rolling window filled
        assert float(result["ATR"].iloc[-1]) > 0


# ───────────────────────────────────────────────────────────────────
# Task 3/4 — scanner + history caching
# ───────────────────────────────────────────────────────────────────


class TestScannerCache:
    @pytest.fixture(autouse=True)
    def _isolate_alert_history(self, monkeypatch, tmp_path):
        from src.alerts import history as alerts_history

        monkeypatch.setattr(
            alerts_history, "HISTORY_FILE", tmp_path / "alerts" / "history.json"
        )

    def test_dataframe_cache_hit_and_invalidation_on_change(self, tmp_path):
        path = _write_ohlcv(tmp_path / "CACHE.csv", rows=100, seed=1)
        df1 = scanner_cache.get_dataframe(path)
        assert df1 is None  # cold

        from src.loaders.csv_loader import load_csv

        parsed1 = load_csv(path)
        df2 = scanner_cache.get_dataframe(path)
        assert df2 is not None
        assert len(parsed1) == len(df2)

        # Modify the file -> fingerprint changes -> cache invalidated.
        time.sleep(0.01)
        _write_ohlcv(path, rows=100, seed=2)
        df3 = scanner_cache.get_dataframe(path)
        assert df3 is None  # invalidated by mtime/size change

    def test_analysis_cache_hit(self, monkeypatch, tmp_path):
        from src.scanner import engine

        monkeypatch.setattr(engine, "DATA_DIRECTORY", str(tmp_path))
        _write_ohlcv(tmp_path / "A.csv", rows=80)

        calls = {"n": 0}

        def fake(path, with_alerts=True):
            calls["n"] += 1
            return {"score": 5, "signal": "HOLD"}

        monkeypatch.setattr(engine, "analyze_stock", fake)
        engine.scan_market()
        engine.scan_market()  # second scan -> analysis cache hit
        assert calls["n"] == 1

    def test_cache_bounded_lru(self, tmp_path):
        cache = ScannerCache(ttl=3600, max_entries=3)
        paths = [_write_ohlcv(tmp_path / f"F{i}.csv", rows=40) for i in range(5)]
        for p in paths:
            cache.put_dataframe(p, pd.DataFrame({"x": [1]}))
        stats = cache.stats()
        assert stats["dataframe_entries"] == 3  # LRU eviction enforced

    def test_cache_config_values_present(self):
        assert SCANNER_CACHE_TTL > 0
        assert SCANNER_CACHE_MAX_ENTRIES > 0
        assert SCANNER_WORKERS >= 1
        assert HISTORY_CACHE_TTL > 0


# ───────────────────────────────────────────────────────────────────
# Task 3 — DataService history batch + TTL config
# ───────────────────────────────────────────────────────────────────


class TestHistoryOptimization:
    @pytest.fixture(autouse=True)
    def _restore_dataservice(self):
        """Restore the DataService singleton after each test in this class.

        These tests inject a CSVProvider into the singleton; without a
        reset, later tests (e.g. the metrics endpoint) would inherit the
        injected provider instead of the default HybridProvider.
        """
        from src.data import DataService

        yield
        DataService.reset_instance()

    def test_get_history_batch_populates_all(self, tmp_path):
        from src.data import DataService
        from src.data.providers import CSVProvider

        for i in range(3):
            _write_ohlcv(tmp_path / f"H{i}.csv", rows=60, seed=i)

        DataService.reset_instance()
        svc = DataService(provider=CSVProvider(data_dir=str(tmp_path)))

        result = svc.get_history_batch(["H0", "H1", "H2"], days=60)
        assert set(result.keys()) == {"H0", "H1", "H2"}
        assert all(not h.is_empty for h in result.values())

    def test_history_uses_configured_ttl(self, monkeypatch, tmp_path):
        from src.data import service as service_module
        from src.data.providers import CSVProvider

        _write_ohlcv(tmp_path / "TTL.csv", rows=60)
        monkeypatch.setattr(service_module, "HISTORY_CACHE_TTL", 3600)

        from src.data import DataService

        DataService.reset_instance()
        svc = DataService(provider=CSVProvider(data_dir=str(tmp_path)))
        svc.clear_cache()

        first = svc.get_history("TTL", days=60)
        second = svc.get_history("TTL", days=60)
        assert not first.is_empty
        assert second.source == "cache"  # hit served from cache

    def test_market_cache_ttl_from_config(self, monkeypatch):
        from src import config
        from src.cache import market_cache

        monkeypatch.setattr(config, "SCANNER_CACHE_TTL", 1234)
        # Reload the constant the module imported at import time.
        monkeypatch.setattr(market_cache, "CACHE_SECONDS", config.SCANNER_CACHE_TTL)
        assert market_cache.CACHE_SECONDS == 1234


# ───────────────────────────────────────────────────────────────────
# Task 5/8 — API metrics + observability
# ───────────────────────────────────────────────────────────────────


class TestMetricsEndpoint:
    def test_metrics_endpoint_returns_snapshot(self):
        from fastapi.testclient import TestClient

        from src.api.main import app

        client = TestClient(app)
        resp = client.get("/metrics")
        assert resp.status_code == 200
        payload = resp.json()
        assert "metrics" in payload
        assert "scanner_cache" in payload
        assert "cache_hit_rate" in payload["metrics"]
        assert payload["metrics"]["total_requests"] >= 0

    def test_timing_helper_logs_structured_record(self, caplog):
        import logging

        from src.logging.timing import timed

        with caplog.at_level(logging.DEBUG, logger="nepse.performance"):
            with timed("bench_op", level=logging.DEBUG, symbol="NABIL"):
                pass
        assert any("timing name=bench_op" in rec.getMessage() for rec in caplog.records)


# ───────────────────────────────────────────────────────────────────
# Task 7 — benchmark suite runs independently
# ───────────────────────────────────────────────────────────────────


class TestBenchmarkSuite:
    def test_synthetic_data_generator(self):
        from scripts.benchmarks import make_synthetic_df

        df = make_synthetic_df(rows=100, seed=1)
        assert len(df) == 100
        assert {"Date", "Open", "High", "Low", "Close", "Volume"} <= set(df.columns)

    def test_benchmark_runner_smoke(self, tmp_path):
        from scripts.benchmarks import run_all

        results = run_all(symbols=4, rows=60, results_dir=tmp_path)
        assert "scan" in results
        assert "analysis" in results
        assert "history" in results
        assert "backtest" in results
        assert "api" in results
        assert results["scan"]["scan_cold_ms"] > 0
        # The subprocess-startup leg is informational and must never
        # crash the run (Sprint 13.5: previously a timeout under
        # full-suite load raised through run_all and flaked this test).
        assert "startup" in results
        assert "startup_ms" in results["startup"] or "error" in results["startup"]
        # Results persisted to disk.
        assert list(tmp_path.glob("benchmark_*.json"))

    def test_benchmark_runner_isolated_state(self, tmp_path):
        """run_all must not leak shared state into later callers.

        Sprint 13.5 determinism: an in-process benchmark run redirects
        alert/portfolio persistence, restores the scanner DATA_DIRECTORY
        global, clears the shared scanner cache and resets the
        DataService singleton — so running the suite cannot make other
        tests order-dependent.

        The production alert-history file is compared byte-for-byte
        before vs after the run (the file may legitimately contain
        ``SYN000`` entries from pre-isolation benchmark runs in earlier
        sprints — the invariant is that *this* run must not touch it).
        """
        from src.scanner import engine as scanner_engine
        from scripts.benchmarks import run_all

        from src.alerts.history import HISTORY_FILE

        original_dir = scanner_engine.DATA_DIRECTORY
        before = (
            HISTORY_FILE.read_text(encoding="utf-8") if HISTORY_FILE.exists() else None
        )
        run_all(symbols=2, rows=40, results_dir=tmp_path)

        # Scanner global restored.
        assert scanner_engine.DATA_DIRECTORY == original_dir
        # No synthetic alert/portfolio state written into the project:
        # the production history file is byte-identical to its pre-run
        # state (benchmark legs redirect into a temp dir).
        after = (
            HISTORY_FILE.read_text(encoding="utf-8") if HISTORY_FILE.exists() else None
        )
        assert after == before
        # DataService singleton was reset to a fresh default instance.
        from src.data import DataService

        svc = DataService()
        assert svc.provider is not None

    def test_benchmark_runner_repeatable(self, tmp_path):
        """Repeated runs produce the same structural result (determinism)."""
        from scripts.benchmarks import run_all

        r1 = run_all(symbols=3, rows=50, results_dir=tmp_path / "a")
        r2 = run_all(symbols=3, rows=50, results_dir=tmp_path / "b")
        for section in ("scan", "analysis", "history", "backtest", "api", "memory"):
            assert set(r1[section]) == set(r2[section])
        # Fresh dirs: neither run compares against a previous snapshot.
        assert "previous" not in r1 and "previous" not in r2
        assert set(r1.keys()) == set(r2.keys())
        # Both runs persisted snapshots.
        assert list((tmp_path / "a").glob("benchmark_*.json"))
        assert list((tmp_path / "b").glob("benchmark_*.json"))

    def test_benchmark_runner_concurrent(self, tmp_path):
        """Concurrent benchmark runs stay safe (serialised globals).

        Sprint 13.5: ``benchmark_isolation`` serialises the
        module-global section with a lock, so two threads invoking the
        whole suite concurrently cannot corrupt each other's scanner
        directory / state files / DataService singleton.
        """
        import threading

        from scripts.benchmarks import run_all

        results: dict[str, dict] = {}
        errors: list[Exception] = []

        def _run(name: str, out: Path) -> None:
            try:
                results[name] = run_all(symbols=2, rows=40, results_dir=out)
            except Exception as exc:  # noqa: BLE001 - captured for assertion
                errors.append(exc)

        t1 = threading.Thread(target=_run, args=("a", tmp_path / "a"))
        t2 = threading.Thread(target=_run, args=("b", tmp_path / "b"))
        t1.start()
        t2.start()
        t1.join(timeout=300)
        t2.join(timeout=300)
        assert not t1.is_alive() and not t2.is_alive()
        assert not errors
        assert results["a"]["scan"]["scan_cold_ms"] > 0
        assert results["b"]["scan"]["scan_cold_ms"] > 0

    def test_benchmark_startup_records_error_not_raises(self, monkeypatch):
        """bench_startup must never raise on a slow/failed subprocess."""
        import subprocess

        from benchmarks import pipeline

        def _boom(*args, **kwargs):
            raise subprocess.TimeoutExpired(cmd="python -c import src.api.main", timeout=120)

        monkeypatch.setattr(subprocess, "run", _boom)
        out = pipeline.bench_startup()
        assert out["startup_ms"] is None
        assert "error" in out
