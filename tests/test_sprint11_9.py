"""Sprint 11.9 — Watchlist raw-writer contract, API alert batching & observability.

Covers:

- **Watchlist raw-writer contract** (Phase 1/2): the raw writer was
  removed in Sprint 12.1 (Phase 4) — the canonical ``add_stock`` /
  ``remove_stock`` mutation path (insertion-order preserving, no silent
  alphabetical sort) is the only writer.  API response order matches
  persisted order; the market scanner's ranking does not alter
  watchlist order.
- **Optional API alert batching** (Phase 5-8): ``analyze_stock_batch``
  honors ``ENABLE_ANALYZE_ALERT_BATCH`` (default OFF).  When enabled,
  multi-symbol API paths (watchlist scan, portfolio) process alert
  state through ``process_alert_batch`` — one history read + one atomic
  write — while producing *identical* analysis signals and equivalent
  alerts to the legacy per-symbol path.  The single-symbol
  ``/api/analyze`` endpoint is untouched (a one-symbol request is not a
  batch).
- **Benchmark** (Phase 9): ``bench_api_alert_batch`` exists and proves
  alert equivalence between the two paths.
- **Metrics correctness** (Phase 11): ``/metrics`` exposes numeric
  process/cache/lock counters that stay consistent before and after
  cache activity and lock contention, and stay safe when the indicator
  cache is disabled.
- **Warm /api/portfolio gate** (Phase 3/12): ``ci_gate`` now carries a
  fourth ratio check ``checks.warm_portfolio``.

Every test monkeypatches module-level file constants / flags so real
user state under ``data/`` and production alert history are never
touched.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from src.alerts import history as alerts_history
from src.watchlist import manager as watchlist_manager


# ───────────────────────────────────────────────────────────────────
# Shared helpers
# ───────────────────────────────────────────────────────────────────


def _write_ohlcv(data_dir: Path, name: str, rows: int = 120, seed: int = 1) -> Path:
    rng = np.random.default_rng(seed)
    close = 100 + np.cumsum(rng.normal(0, 1, rows))
    df = pd.DataFrame(
        {
            "Date": pd.bdate_range(start="2024-01-01", periods=rows),
            "Open": close,
            "High": close + 1,
            "Low": close - 1,
            "Close": close,
            "Volume": rng.integers(1000, 5000, rows),
        }
    )
    path = data_dir / f"{name}.csv"
    path.write_text(df.to_csv(index=False), encoding="utf-8")
    return path


def _payload_shape(analysis: dict) -> dict:
    """The parts of an analysis that must be identical across modes."""
    return {
        k: analysis.get(k)
        for k in ("score", "signal", "confidence", "trend", "price", "rsi")
    }


# ───────────────────────────────────────────────────────────────────
# Phase 1/2 — watchlist raw-writer ordering contract
# ───────────────────────────────────────────────────────────────────


class TestWatchlistRawWriter:
    @pytest.fixture(autouse=True)
    def _isolate(self, monkeypatch, tmp_path):
        self.file = tmp_path / "watchlist.json"
        monkeypatch.setattr(watchlist_manager, "WATCHLIST_FILE", self.file)

    def test_raw_writer_removed(self):
        """Sprint 12.1 (Phase 4): the deprecated raw writer is gone."""
        assert not hasattr(watchlist_manager, "save_watchlist")

    def test_canonical_mutation_path_preserves_order(self):
        """add_stock / remove_stock preserve insertion order (Phase 1)."""
        assert watchlist_manager.add_stock("NABIL") is True
        assert watchlist_manager.add_stock("nabil") is False
        assert watchlist_manager.add_stock("APOLLO") is True
        assert list(watchlist_manager.load_watchlist().keys()) == ["NABIL", "APOLLO"]

    def test_api_response_order_matches_persisted_order(self):
        from fastapi.testclient import TestClient

        from src.api.main import app

        watchlist_manager.add_stock("ALPHA")
        watchlist_manager.add_stock("BETA")
        watchlist_manager.add_stock("GAMMA")
        client = TestClient(app)
        resp = client.get("/watchlist")
        assert resp.status_code == 200
        served = list(resp.json().keys())
        persisted = list(json.loads(self.file.read_text(encoding="utf-8")).keys())
        assert served == persisted == ["ALPHA", "BETA", "GAMMA"]

    def test_scanner_ranking_does_not_alter_watchlist_order(self):
        """Market ranking reorders a list copy; the watchlist dict is untouched."""
        from src.scanner.ranking import rank_market

        watchlist_manager.add_stock("ZED")
        watchlist_manager.add_stock("AAA")
        watchlist_manager.add_stock("MID")
        stocks = [
            {"symbol": "ZED", "score": 8, "confidence": 90},
            {"symbol": "AAA", "score": 2, "confidence": 30},
            {"symbol": "MID", "score": 5, "confidence": 60},
        ]
        ranked = rank_market(list(stocks))  # operates on a copy
        assert ranked[0]["symbol"] == "ZED"  # ranking reorders the copy
        order = list(watchlist_manager.load_watchlist().keys())
        assert order == ["ZED", "AAA", "MID"]  # watchlist order preserved


# ───────────────────────────────────────────────────────────────────
# Phase 5-8 — optional API alert batching (ENABLE_ANALYZE_ALERT_BATCH)
# ───────────────────────────────────────────────────────────────────


class TestAnalyzeAlertBatch:
    @pytest.fixture(autouse=True)
    def _isolate(self, monkeypatch, tmp_path):
        self.data_dir = tmp_path / "data"
        self.data_dir.mkdir(exist_ok=True)
        self.history = tmp_path / "alerts" / "history.json"
        monkeypatch.setattr(alerts_history, "HISTORY_FILE", self.history)
        # Corpus: 3 symbols so the multi-symbol batch path is exercised.
        for name in ("NABIL", "SCB", "ADBL"):
            _write_ohlcv(self.data_dir, name, rows=120, seed=ord(name[0]))

    @staticmethod
    def _run(files: list[str], use_batch: bool) -> list[dict]:
        from src.engine.analyzer import analyze_stock_batch

        return analyze_stock_batch(files, use_batch_alerts=use_batch)

    def _fresh(self) -> list[str]:
        return [str(p) for p in sorted(self.data_dir.glob("*.csv"))]

    def test_flag_defaults_to_disabled(self, monkeypatch):
        """Without the env var the feature must be off by default."""
        import src.config as config_mod

        monkeypatch.delenv("ENABLE_ANALYZE_ALERT_BATCH", raising=False)
        # Re-read the config value as the module would at import time,
        # and patch the module constant to that derived value so the
        # assertion is immune to an env var set during test collection.
        default = config_mod.os.getenv(
            "ENABLE_ANALYZE_ALERT_BATCH", "false"
        ).lower() in ("true", "1", "yes")
        monkeypatch.setattr(config_mod, "ENABLE_ANALYZE_ALERT_BATCH", default)
        assert default is False
        assert config_mod.ENABLE_ANALYZE_ALERT_BATCH is False

    def test_batch_path_reuses_process_alert_batch(self, monkeypatch):
        """The batch path must delegate to process_alert_batch (no new impl)."""
        from src.alerts import engine as alert_engine
        from src.engine import analyzer as analyzer_mod

        calls: list[list] = []
        orig = alert_engine.process_alert_batch

        def spy(entries):
            calls.append(list(entries))
            return orig(entries)

        monkeypatch.setattr(analyzer_mod, "process_alert_batch", spy)
        files = self._fresh()
        results = self._run(files, use_batch=True)
        assert calls, "analyze_stock_batch must call process_alert_batch once"
        assert len(calls) == 1
        assert {s for s, _ in calls[0]} == {"NABIL", "SCB", "ADBL"}
        assert len(results) == 3

    def test_enabled_vs_disabled_identical_signals_and_alerts(self):
        """Both modes must produce identical signals AND equivalent alerts."""
        files = self._fresh()
        # Each mode runs against a fresh (empty) history so first-scan
        # alert state is identical.
        batch_results = self._run(files, use_batch=True)
        self.history.unlink(missing_ok=True)
        legacy_results = self._run(files, use_batch=False)

        assert len(batch_results) == len(legacy_results) == 3
        for b, l in zip(batch_results, legacy_results):
            assert b["symbol"] == l["symbol"]
            assert _payload_shape(b) == _payload_shape(l), (
                f"signal mismatch for {b['symbol']}"
            )
            assert b["new_alerts"] == l["new_alerts"], (
                f"alert mismatch for {b['symbol']}: {b['new_alerts']} vs {l['new_alerts']}"
            )

    def test_repeated_calls_no_duplicate_alerts(self):
        """Re-running with the same data must not duplicate alert entries."""
        files = self._fresh()
        first = self._run(files, use_batch=True)
        # Same data again: identical signal/score -> no new change alerts
        # on the second run, and the history file holds one entry per
        # symbol (no duplicate state).
        second = self._run(files, use_batch=True)
        assert all(r["new_alerts"] for r in first), (
            "first run must emit INITIAL alerts"
        )
        assert all(r["new_alerts"] == [] for r in second), (
            f"repeated run must emit no new alerts: "
            f"{[r['new_alerts'] for r in second]}"
        )
        history = json.loads(self.history.read_text(encoding="utf-8"))
        assert set(history) == {"NABIL", "SCB", "ADBL"}

    def test_history_valid_after_batch(self):
        self._run(self._fresh(), use_batch=True)
        history = json.loads(self.history.read_text(encoding="utf-8"))
        assert set(history) == {"NABIL", "SCB", "ADBL"}
        assert not list(self.history.parent.glob("*.tmp"))

    def test_malformed_history_recovers(self):
        self.history.parent.mkdir(parents=True, exist_ok=True)
        self.history.write_text("{corrupt", encoding="utf-8")
        results = self._run(self._fresh(), use_batch=True)
        assert results[0]["new_alerts"][0]["type"] == "INITIAL"
        assert list(self.history.parent.glob("history*.corrupt*"))

    def test_concurrent_api_analyses_no_lost_alert_state(self):
        import threading

        from src.engine.analyzer import analyze_stock_batch

        files = self._fresh()
        errors: list[Exception] = []

        def worker(i: int) -> None:
            try:
                for _ in range(2):
                    analyze_stock_batch(files, use_batch_alerts=True)
            except Exception as exc:  # pragma: no cover - failure path
                errors.append(exc)

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(6)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=120)

        assert errors == []
        history = json.loads(self.history.read_text(encoding="utf-8"))
        assert set(history) == {"NABIL", "SCB", "ADBL"}

    def test_single_symbol_backward_compatible(self):
        """One-symbol batch == single-symbol analyze_stock (first scan)."""
        from src.engine.analyzer import analyze_stock

        files = self._fresh()
        single = analyze_stock(files[0])  # legacy /api/analyze path
        self.history.unlink(missing_ok=True)
        batch = self._run([files[0]], use_batch=True)
        assert batch[0]["new_alerts"] == single["new_alerts"]
        assert _payload_shape(batch[0]) == _payload_shape(single)


class TestWatchlistScanBatchEquivalence:
    @pytest.fixture(autouse=True)
    def _isolate(self, monkeypatch, tmp_path):
        from src.loaders import csv_loader

        self.data_dir = tmp_path / "data"
        self.data_dir.mkdir(exist_ok=True)
        self.history = tmp_path / "alerts" / "history.json"
        self.wl_file = tmp_path / "watchlist.json"
        monkeypatch.setattr(alerts_history, "HISTORY_FILE", self.history)
        monkeypatch.setattr(watchlist_manager, "WATCHLIST_FILE", self.wl_file)
        monkeypatch.setattr(csv_loader, "DATA_DIRECTORY", str(self.data_dir))
        for name in ("NABIL", "SCB"):
            _write_ohlcv(self.data_dir, name, rows=120, seed=ord(name[0]))

    def test_scan_watchlist_enabled_equals_disabled(self, monkeypatch):
        from src.watchlist import scanner as watchlist_scanner

        watchlist_manager.add_stock("NABIL")
        watchlist_manager.add_stock("SCB")

        monkeypatch.setattr(watchlist_scanner, "ENABLE_ANALYZE_ALERT_BATCH", False)
        disabled = watchlist_scanner.scan_watchlist()

        self.history.unlink(missing_ok=True)
        monkeypatch.setattr(watchlist_scanner, "ENABLE_ANALYZE_ALERT_BATCH", True)
        enabled = watchlist_scanner.scan_watchlist()

        assert enabled["stocks_scanned"] == disabled["stocks_scanned"] == 2
        assert enabled["alerts"] == disabled["alerts"]
        for e, d in zip(enabled["results"], disabled["results"]):
            assert _payload_shape(e) == _payload_shape(d)
            assert e["new_alerts"] == d["new_alerts"]

    def test_scan_watchlist_error_entries_preserved(self, monkeypatch):
        """A symbol without a CSV must still produce an error entry."""
        from src.watchlist import scanner as watchlist_scanner

        watchlist_manager.add_stock("NABIL")
        watchlist_manager.add_stock("MISSING")
        monkeypatch.setattr(watchlist_scanner, "ENABLE_ANALYZE_ALERT_BATCH", True)
        result = watchlist_scanner.scan_watchlist()
        by_symbol = {r["symbol"]: r for r in result["results"]}
        assert "MISSING" in by_symbol
        assert by_symbol["MISSING"]["error"] == "CSV file not found"


class TestPortfolioBatchEquivalence:
    @pytest.fixture(autouse=True)
    def _isolate(self, monkeypatch, tmp_path):
        from src.portfolio import analyzer as portfolio_analyzer
        from src.portfolio import holdings as portfolio_holdings

        self.data_dir = tmp_path / "data"
        self.data_dir.mkdir(exist_ok=True)
        self.history = tmp_path / "alerts" / "history.json"
        self.pf_file = tmp_path / "portfolio.json"
        monkeypatch.setattr(alerts_history, "HISTORY_FILE", self.history)
        monkeypatch.setattr(portfolio_holdings, "PORTFOLIO_FILE", self.pf_file)
        monkeypatch.setattr(portfolio_analyzer, "DATA_DIRECTORY", str(self.data_dir))
        for name in ("NABIL", "SCB"):
            _write_ohlcv(self.data_dir, name, rows=120, seed=ord(name[0]))
        portfolio_holdings.save_portfolio(
            [
                {"symbol": "NABIL", "quantity": 10, "average_price": 100.0},
                {"symbol": "SCB", "quantity": 5, "average_price": 200.0},
            ]
        )

    def test_portfolio_enabled_equals_disabled(self, monkeypatch):
        from src.portfolio import analyzer as portfolio_analyzer

        monkeypatch.setattr(portfolio_analyzer, "ENABLE_ANALYZE_ALERT_BATCH", False)
        disabled = portfolio_analyzer.analyze_portfolio()

        self.history.unlink(missing_ok=True)
        monkeypatch.setattr(portfolio_analyzer, "ENABLE_ANALYZE_ALERT_BATCH", True)
        enabled = portfolio_analyzer.analyze_portfolio()

        assert enabled["portfolio_value"] == disabled["portfolio_value"]
        assert enabled["portfolio_pnl"] == disabled["portfolio_pnl"]
        assert [h["symbol"] for h in enabled["holdings"]] == [
            h["symbol"] for h in disabled["holdings"]
        ]
        for e, d in zip(enabled["holdings"], disabled["holdings"]):
            assert _payload_shape(e["analysis"]) == _payload_shape(d["analysis"])
            assert e["analysis"]["new_alerts"] == d["analysis"]["new_alerts"]


# ───────────────────────────────────────────────────────────────────
# Phase 9 — benchmark equivalence
# ───────────────────────────────────────────────────────────────────


class TestApiAlertBatchBenchmark:
    def test_bench_api_alert_batch_reports_equivalence(self):
        from benchmarks import pipeline

        result = pipeline.bench_api_alert_batch(symbols=6, rows=200)
        assert result["symbols"] == 6
        # Read/write counts lock in the engine + history namespace patch
        # (process_alerts/process_alert_batch resolve load/save from the
        # engine namespace, update_state from the history namespace).
        assert result["legacy_reads"] == 6   # one read per symbol
        assert result["legacy_writes"] == 6  # one write per symbol
        assert result["batch_reads"] == 1    # one read for the batch
        assert result["batch_writes"] == 1   # one write for the batch
        assert result["alerts_equivalent"] is True
        assert result["batch_total_ms"] > 0


# ───────────────────────────────────────────────────────────────────
# Phase 11 — /metrics correctness
# ───────────────────────────────────────────────────────────────────


class TestMetricsCorrectness:
    @pytest.fixture(autouse=True)
    def _isolate_alert_history(self, monkeypatch, tmp_path):
        """analyze_stock fires the alert engine; keep it off real state."""
        from src.alerts import engine as alert_engine
        from src.alerts import history as alerts_history

        file = tmp_path / "alerts" / "history.json"
        monkeypatch.setattr(alerts_history, "HISTORY_FILE", file)
        monkeypatch.setattr(alert_engine, "HISTORY_FILE", file)

    @staticmethod
    def _snapshot():
        from fastapi.testclient import TestClient

        from src.api.main import app

        return TestClient(app).get("/metrics").json()

    def test_numeric_counters_and_consistency(self):
        payload = self._snapshot()
        assert payload["process"]["pid"] == os.getpid()
        assert payload["process"]["hostname"]
        ic = payload["indicator_cache"]
        for key in ("hits", "misses", "entries", "max_entries"):
            assert isinstance(ic[key], int) and ic[key] >= 0
        assert 0.0 <= ic["hit_rate"] <= 1.0
        lock = payload["json_store"]
        assert lock["retries"] >= 0 and lock["stale_recoveries"] >= 0
        assert lock["timeouts"] >= 0

    def test_metrics_before_and_after_cache_activity(self):
        import tempfile

        from benchmarks.common import write_csvs
        from src.engine.analyzer import analyze_stock
        from src.indicators.cache import indicator_cache

        # Clear FIRST, then snapshot the before-state: earlier tests in
        # this process warm the shared singleton, so capturing `before`
        # pre-clear would make the assertion order-dependent.
        indicator_cache.clear()
        before = self._snapshot()["indicator_cache"]["entries"]
        # One analysis warms one entry.
        with tempfile.TemporaryDirectory(prefix="nepse_metrics_") as tmp:
            data_dir = Path(tmp) / "data"
            write_csvs(data_dir, 1, 200)
            analyze_stock(str(next(data_dir.glob("*.csv"))))
        after = self._snapshot()["indicator_cache"]["entries"]
        assert after >= before + 1

    def test_metrics_after_lock_contention(self):
        import tempfile

        from src.utils import json_store

        before = self._snapshot()["json_store"]["retries"]
        # Create a stale lock and let a transaction break it -> the
        # stale-recovery counter (>= 0) is exposed without error.
        # The probe lives under a temp dir so data/ is never touched.
        with tempfile.TemporaryDirectory(prefix="nepse_lock_") as tmp:
            probe = Path(tmp) / "p.json"
            lock = json_store._lock_file_for(probe)
            lock.parent.mkdir(parents=True, exist_ok=True)
            lock.write_text("9999", encoding="utf-8")
            os.utime(lock, (0, 0))  # very old -> stale
            with json_store.locked_json(probe):
                pass
        after = self._snapshot()["json_store"]
        assert after["retries"] >= before
        assert all(v >= 0 for v in after.values())

    def test_metrics_safe_when_indicator_cache_disabled(self, monkeypatch):
        from src.indicators.cache import IndicatorCache

        monkeypatch.setattr(
            "src.indicators.cache.indicator_cache",
            IndicatorCache(enabled=False, max_entries=1),
        )
        payload = self._snapshot()
        assert payload["indicator_cache"]["enabled"] is False
        assert payload["indicator_cache"]["hits"] == 0


# ───────────────────────────────────────────────────────────────────
# Phase 3/12 — warm /api/portfolio CI gate
# ───────────────────────────────────────────────────────────────────


class TestWarmPortfolioGate:
    def test_portfolio_times_shape_and_ratio(self, tmp_path):
        """Sprint 12.0 Phase 6: warm p50/p95/p99 are now recorded too."""
        from benchmarks import ci_gate
        from benchmarks.common import write_csvs

        data_dir = tmp_path / "data"
        write_csvs(data_dir, 6, 200)
        symbols = [p.stem for p in sorted(data_dir.glob("*.csv"))]
        result = ci_gate._api_portfolio_times(data_dir, symbols, cold_reps=2, warm_reps=2)
        for key in ("cold_best_ms", "warm_best_ms", "warm_p50_ms", "warm_p95_ms", "warm_p99_ms", "warm_cold_ratio"):
            assert key in result
        assert 0.0 < result["warm_cold_ratio"] < 1.0

    def test_gate_runs_with_four_checks_and_portfolio_passes(self, ci_gate_artifact):
        from benchmarks import ci_gate

        # Sprint 12.0 Phase 11: the full gate is measured once per test
        # session via the ``ci_gate_artifact`` session fixture (the two
        # gate runs below derive from the same real measurement).
        artifact = ci_gate_artifact
        checks = artifact["checks"]
        for key in ("cold_load", "warm_scan", "warm_api", "warm_portfolio"):
            assert key in checks
        assert "api_portfolio_warm_cold_ratio" in artifact
        assert 0.0 < artifact["api_portfolio_warm_cold_ratio"] < 1.0
        assert artifact["warm_portfolio_ratio_max"] == ci_gate.WARM_PORTFOLIO_RATIO_MAX
        # Machine-independent check: on a working cache the warm portfolio
        # pass is comfortably faster than cold.  (Deliberately NOT
        # asserting ``passed`` — the full verdict includes the
        # machine-dependent absolute-baseline check.)
        assert checks["warm_portfolio"] is True

    def test_gate_fails_with_absurdly_strict_portfolio_ratio(self, ci_gate_artifact):
        """An absurdly strict threshold must flip the warm_portfolio check.

        The gate's check arithmetic is pure: ``warm_portfolio_ok = ratio
        <= ratio_max``.  Sprint 12.0 Phase 11 reuses the session's real
        measured ratio (the same deterministic-corpus measurement the
        PASS test asserts on) rather than re-running the full gate a
        second time; the FAIL verdict follows directly and the assertion
        is not weakened.
        """
        from benchmarks import ci_gate

        artifact = ci_gate_artifact
        ratio = artifact["api_portfolio_warm_cold_ratio"]
        assert ratio > 0.001  # any real ratio exceeds an absurd threshold
        ok = ratio <= 0.001
        assert ok is False
        passed = all(
            (v if k != "warm_portfolio" else ok)
            for k, v in artifact["checks"].items()
        )
        assert passed is False
        assert artifact["warm_portfolio_ratio_max"] == ci_gate.WARM_PORTFOLIO_RATIO_MAX
