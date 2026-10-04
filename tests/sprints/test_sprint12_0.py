"""Sprint 12.0 — Production market-scan batching, portfolio percentiles,
cross-worker metrics & watchlist writer deprecation.

Covers:

- **Market-scan batch regression** (Phase 2/3): the production
  ``scan_market()`` path (which ``DataService.scan_market`` →
  ``_csv_scan`` delegates to) processes alert state through
  ``process_alert_batch`` — one history read + one atomic write per
  scan.  Functional coverage: empty market, single/multi symbol,
  skipped symbols, malformed CSVs, per-file exceptions, deterministic
  result ordering and deterministic alert ordering.  Persistence
  coverage: 1 read / 1 write per multi-symbol scan, no duplicate
  alerts, valid JSON, corruption recovery, no ``.tmp`` / ``.lock``
  litter.  Backward compatibility: ``scan_market`` /
  ``scan_watchlist`` / ``analyze_stock`` all keep working.
- **Legacy vs batch equivalence** (Phase 4): the dedicated
  ``bench_scan_alert_equivalence`` reports analysis/alerts/ranking/
  errors equivalence for the same deterministic corpus.
- **Warm portfolio percentiles** (Phase 6): ``_api_portfolio_times``
  records warm per-holding p50/p95/p99 (descriptive) alongside the
  ratio gate (which remains the CI check).
- **Cross-worker metrics aggregation** (Phase 7/8): ``/metrics`` gains
  ``workers`` + ``aggregate`` blocks backed by a shared JSON store
  written transactionally (``update_json``).  Best-effort — failures
  degrade to local-only, never break the API; no secrets or paths.
- **Watchlist writer deprecation → removal** (Phase 10 / Sprint 12.1):
  ``save_watchlist`` was deprecated in Sprint 12.0 and removed in
  Sprint 12.1 (Phase 4) — zero production callers remained; the
  canonical ``add_stock`` / ``remove_stock`` / ``update_json`` path is
  the only writer.

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


def _alert_counters(monkeypatch):
    """Install read/write counters over load_history/save_history in BOTH
    namespaces (engine + history) — the same dual-namespace patch the
    benchmarks use, because ``process_alerts``/``process_alert_batch``
    resolve from the engine namespace while ``update_state`` resolves
    ``save_history`` from the history namespace.

    Returns ``(counts, reset)`` where ``counts`` is a mutable dict and
    ``reset()`` zeroes it.
    """
    from src.alerts import engine as alert_engine

    counts = {"reads": 0, "writes": 0}
    orig_load = alerts_history.load_history
    orig_save = alerts_history.save_history
    orig_engine_load = alert_engine.load_history
    orig_engine_save = alert_engine.save_history

    def counting_load():
        counts["reads"] += 1
        return orig_load()

    def counting_save(history):
        counts["writes"] += 1
        return orig_save(history)

    monkeypatch.setattr(alerts_history, "load_history", counting_load)
    monkeypatch.setattr(alerts_history, "save_history", counting_save)
    monkeypatch.setattr(alert_engine, "load_history", counting_load)
    monkeypatch.setattr(alert_engine, "save_history", counting_save)

    def reset():
        counts["reads"] = 0
        counts["writes"] = 0

    return counts, reset


# ───────────────────────────────────────────────────────────────────
# Phase 2/3 — production market-scan batch regression
# ───────────────────────────────────────────────────────────────────


class TestMarketScanBatch:
    @pytest.fixture(autouse=True)
    def _isolate(self, monkeypatch, tmp_path):
        from src.cache.scanner_cache import scanner_cache
        from src.scanner import engine as scanner_engine

        self.data_dir = tmp_path / "data"
        self.data_dir.mkdir(exist_ok=True)
        self.history = tmp_path / "alerts" / "history.json"
        monkeypatch.setattr(alerts_history, "HISTORY_FILE", self.history)
        from src.alerts import engine as alert_engine

        monkeypatch.setattr(alert_engine, "HISTORY_FILE", self.history)
        monkeypatch.setattr(scanner_engine, "DATA_DIRECTORY", str(self.data_dir))
        scanner_cache.clear()
        self.scanner_cache = scanner_cache

    def _scan(self):
        from src.scanner.engine import scan_market

        self.scanner_cache.clear()
        return scan_market()

    # ── functional ─────────────────────────────────────────────────

    def test_empty_market(self):
        result = self._scan()
        assert result["results"] == []
        assert result["skipped"] == []
        assert not self.history.exists()

    def test_one_symbol(self):
        _write_ohlcv(self.data_dir, "NABIL", rows=120, seed=3)
        result = self._scan()
        assert [r["symbol"] for r in result["results"]] == ["NABIL"]
        assert result["skipped"] == []
        assert result["results"][0]["new_alerts"][0]["type"] == "INITIAL"

    def test_multiple_symbols(self):
        for name in ("NABIL", "SCB", "ADBL"):
            _write_ohlcv(self.data_dir, name, rows=120, seed=ord(name[0]))
        result = self._scan()
        # scan_market returns *ranked* results (score order), not file
        # order — assert the full set and that nothing was skipped.
        assert {r["symbol"] for r in result["results"]} == {"ADBL", "NABIL", "SCB"}
        assert result["skipped"] == []

    def test_skipped_symbol(self):
        _write_ohlcv(self.data_dir, "GOOD1", rows=120, seed=1)
        (self.data_dir / "BAD.csv").write_text("not,a,csv\n1,2\n", encoding="utf-8")
        _write_ohlcv(self.data_dir, "GOOD2", rows=120, seed=2)
        result = self._scan()
        assert {r["symbol"] for r in result["results"]} == {"GOOD1", "GOOD2"}
        assert [s["symbol"] for s in result["skipped"]] == ["BAD"]
        assert "error" in result["skipped"][0]

    def test_malformed_csv_is_skipped(self):
        (self.data_dir / "BROKEN.csv").write_text("garbage", encoding="utf-8")
        _write_ohlcv(self.data_dir, "HEALTHY", rows=120, seed=4)
        result = self._scan()
        assert [r["symbol"] for r in result["results"]] == ["HEALTHY"]
        assert [s["symbol"] for s in result["skipped"]] == ["BROKEN"]

    def test_one_symbol_raising(self, monkeypatch):
        from src.scanner import engine as scanner_engine

        _write_ohlcv(self.data_dir, "GOOD1", rows=120, seed=1)
        _write_ohlcv(self.data_dir, "GOOD2", rows=120, seed=2)

        def fake_analyze_stock(path, with_alerts=True):
            if "GOOD1" in str(path):
                raise ValueError("boom")
            return {"score": 5, "signal": "BUY", "confidence": 60}

        monkeypatch.setattr(scanner_engine, "analyze_stock", fake_analyze_stock)
        result = self._scan()
        assert [s["symbol"] for s in result["skipped"]] == ["GOOD1"]
        assert result["skipped"][0]["error"] == "boom"
        assert [r["symbol"] for r in result["results"]] == ["GOOD2"]

    def test_all_symbols_raising(self, monkeypatch):
        from src.scanner import engine as scanner_engine

        for name in ("A", "B", "C"):
            _write_ohlcv(self.data_dir, name, rows=60, seed=ord(name))

        def fake_analyze_stock(path, with_alerts=True):
            raise ValueError("everything broken")

        monkeypatch.setattr(scanner_engine, "analyze_stock", fake_analyze_stock)
        result = self._scan()
        assert result["results"] == []
        assert [s["symbol"] for s in result["skipped"]] == ["A", "B", "C"]
        assert not self.history.exists()

    def test_deterministic_result_ordering(self):
        for i, name in enumerate(("ZED", "AAA", "MID")):
            _write_ohlcv(self.data_dir, name, rows=120, seed=i + 10)
        first = self._scan()
        self.scanner_cache.clear()
        second = self._scan()
        # Ranking order AND the stable analysis payload must be
        # identical across scans.  Two fields legitimately differ
        # between a first and second scan (pre-existing alert-engine
        # behaviour, not a Sprint 12.0 regression): ``new_alerts`` (first
        # scan emits INITIAL, later scans see prior history state) and
        # ``milestones`` (``check_target_alerts`` only attaches the
        # milestone dict once previous state exists).  The assertion
        # therefore compares the ranked order plus the stable fields
        # that define the ranking.
        stable_keys = ("symbol", "score", "signal", "confidence", "trend")

        def _stable(results):
            return [
                {k: r[k] for k in stable_keys if k in r}
                for r in results
            ]

        assert [r["symbol"] for r in first["results"]] == [
            r["symbol"] for r in second["results"]
        ]
        assert _stable(first["results"]) == _stable(second["results"])

    def test_deterministic_alert_ordering(self):
        for name in ("NABIL", "SCB", "ADBL"):
            _write_ohlcv(self.data_dir, name, rows=120, seed=ord(name[0]))
        first = self._scan()
        self.history.unlink(missing_ok=True)
        second = self._scan()
        first_alerts = {r["symbol"]: r["new_alerts"] for r in first["results"]}
        second_alerts = {r["symbol"]: r["new_alerts"] for r in second["results"]}
        assert first_alerts == second_alerts

    # ── alert persistence ─────────────────────────────────────────

    def test_scan_batches_one_read_one_write(self, monkeypatch):
        """A multi-symbol scan must do ONE history read + ONE write."""
        for name in ("NABIL", "SCB", "ADBL"):
            _write_ohlcv(self.data_dir, name, rows=120, seed=ord(name[0]))
        counts, reset = _alert_counters(monkeypatch)
        reset()
        result = self._scan()
        assert counts["reads"] == 1, counts
        assert counts["writes"] == 1, counts
        assert len(result["results"]) == 3

    def test_no_duplicate_alerts_on_rescan(self, monkeypatch):
        _write_ohlcv(self.data_dir, "NABIL", rows=120, seed=3)
        first = self._scan()
        assert first["results"][0]["new_alerts"][0]["type"] == "INITIAL"
        second = self._scan()
        # Second scan: same data -> no new alerts, and history holds one
        # entry per symbol (no duplicate state).
        assert second["results"][0]["new_alerts"] == []
        history = json.loads(self.history.read_text(encoding="utf-8"))
        assert set(history) == {"NABIL"}

    def test_history_valid_after_scan(self):
        for name in ("NABIL", "SCB"):
            _write_ohlcv(self.data_dir, name, rows=120, seed=ord(name[0]))
        self._scan()
        history = json.loads(self.history.read_text(encoding="utf-8"))
        assert set(history) == {"NABIL", "SCB"}
        assert not list(self.history.parent.glob("*.tmp"))
        assert not list(self.history.parent.glob("*.lock"))

    def test_corrupt_history_recovers(self):
        _write_ohlcv(self.data_dir, "NABIL", rows=120, seed=3)
        self.history.parent.mkdir(parents=True, exist_ok=True)
        self.history.write_text("{corrupt", encoding="utf-8")
        result = self._scan()
        assert result["results"][0]["new_alerts"][0]["type"] == "INITIAL"
        assert list(self.history.parent.glob("history*.corrupt*"))
        assert not list(self.history.parent.glob("*.tmp"))

    # ── backward compatibility ────────────────────────────────────

    def test_scan_market_backward_compatible(self, monkeypatch):
        from src.scanner.engine import scan_market

        _write_ohlcv(self.data_dir, "NABIL", rows=120, seed=3)
        result = scan_market()
        assert "results" in result and "skipped" in result

    def test_analyze_stock_backward_compatible(self, monkeypatch):
        from src.loaders import csv_loader
        from src.engine.analyzer import analyze_stock

        monkeypatch.setattr(csv_loader, "DATA_DIRECTORY", str(self.data_dir))
        path = _write_ohlcv(self.data_dir, "NABIL", rows=120, seed=3)
        result = analyze_stock(str(path))
        assert result["symbol"] == "NABIL"
        assert result["new_alerts"][0]["type"] == "INITIAL"

    def test_scan_watchlist_backward_compatible(self, monkeypatch, tmp_path):
        from src.loaders import csv_loader
        from src.watchlist import scanner as watchlist_scanner

        self.wl_file = tmp_path / "watchlist.json"
        monkeypatch.setattr(watchlist_manager, "WATCHLIST_FILE", self.wl_file)
        monkeypatch.setattr(csv_loader, "DATA_DIRECTORY", str(self.data_dir))
        monkeypatch.setattr(watchlist_scanner, "ENABLE_ANALYZE_ALERT_BATCH", False)
        _write_ohlcv(self.data_dir, "NABIL", rows=120, seed=3)
        watchlist_manager.add_stock("NABIL")
        result = watchlist_scanner.scan_watchlist()
        assert result["stocks_scanned"] == 1
        assert result["results"][0]["symbol"] == "NABIL"


# ───────────────────────────────────────────────────────────────────
# Phase 4 — legacy vs batch equivalence
# ───────────────────────────────────────────────────────────────────


class TestScanAlertEquivalence:
    def test_benchmark_reports_full_equivalence(self):
        from benchmarks import pipeline

        result = pipeline.bench_scan_alert_equivalence(symbols=6, rows=200)
        assert result["symbols"] == 6
        assert result["analysis_equivalent"] is True
        assert result["alerts_equivalent"] is True
        assert result["ranking_equivalent"] is True
        assert result["errors_equivalent"] is True
        # Read/write accounting proves the batch path's single I/O.
        assert result["legacy_reads"] == 6
        assert result["legacy_writes"] == 6
        assert result["batch_reads"] == 1
        assert result["batch_writes"] == 1


# ───────────────────────────────────────────────────────────────────
# Phase 6 — warm /api/portfolio percentiles
# ───────────────────────────────────────────────────────────────────


class TestWarmPortfolioPercentiles:
    def test_portfolio_times_includes_warm_percentiles(self, tmp_path):
        from benchmarks import ci_gate
        from benchmarks.common import write_csvs

        data_dir = tmp_path / "data"
        write_csvs(data_dir, 6, 200)
        symbols = [p.stem for p in sorted(data_dir.glob("*.csv"))]
        # 5 reps (the gate's own protocol): 2 reps cannot support a
        # stable median — a single pause-inflated pair flips the ratio.
        # Sprint 13.8 measurement-hardening; the ratio assertion is unchanged.
        result = ci_gate._api_portfolio_times(data_dir, symbols, cold_reps=5, warm_reps=5)
        for key in (
            "cold_best_ms",
            "warm_best_ms",
            "warm_p50_ms",
            "warm_p95_ms",
            "warm_p99_ms",
            "warm_cold_ratio",
        ):
            assert key in result, key
        assert 0.0 <= result["warm_p50_ms"] <= result["warm_p99_ms"]
        assert 0.0 < result["warm_cold_ratio"] < 1.0

    def test_gate_artifact_exposes_percentiles(self, tmp_path):
        from benchmarks import ci_gate

        out = tmp_path / "gate.json"
        _passed, artifact = ci_gate.run_gate(out_file=out)
        assert "api_portfolio_warm_p50_ms" in artifact
        assert "api_portfolio_warm_p95_ms" in artifact
        assert "api_portfolio_warm_p99_ms" in artifact
        # The ratio gate remains the CI check (percentiles descriptive).
        assert "api_portfolio_warm_cold_ratio" in artifact
        assert artifact["checks"]["warm_portfolio"] is True


# ───────────────────────────────────────────────────────────────────
# Phase 7/8 — cross-worker metrics aggregation
# ───────────────────────────────────────────────────────────────────


class TestWorkerMetrics:
    @pytest.fixture(autouse=True)
    def _isolate(self, monkeypatch, tmp_path):
        import src.utils.worker_metrics as wm

        self.file = tmp_path / "worker_metrics.json"
        monkeypatch.setattr(wm, "WORKER_METRICS_FILE", self.file)
        # Bypass the throttled self-report for deterministic tests.
        monkeypatch.setattr(wm, "_REPORT_MIN_INTERVAL_S", 0.0)
        self.wm = wm

    @staticmethod
    def _local(pid, hits=10, misses=2, retries=1, stale=0, timeouts=0):
        return {
            "process": {"pid": pid, "hostname": f"host-{pid}"},
            "indicator_cache": {
                "hits": hits,
                "misses": misses,
                "entries": hits + misses,
                "hit_rate": round(hits / (hits + misses), 3) if hits + misses else 0.0,
            },
            "json_store": {
                "retries": retries,
                "stale_recoveries": stale,
                "timeouts": timeouts,
            },
            "memory_mb": 42.0,
        }

    def test_aggregation_sums_active_workers(self):
        self.wm._report_worker(self._local(111, hits=10, misses=2))
        self.wm._report_worker(self._local(222, hits=20, misses=4))
        # A third record that is too old must not be counted active.
        import time as _time

        self.wm.update_json(  # direct store write simulating a stale worker
            self.file,
            lambda s: {**s, "999": {
                "pid": "999",
                "hostname": "host-999",
                "last_seen": _time.time() - self.wm.WORKER_METRICS_TTL_S - 10,
                "indicator_cache": {"hits": 1000, "misses": 0, "entries": 1000, "hit_rate": 1.0},
                "json_store": {"retries": 0, "stale_recoveries": 0, "timeouts": 0},
            }},
            {},
            log_name="test",
        )
        enriched = self.wm.aggregate_worker_metrics(self._local(111))
        assert enriched["workers"]["active"] == 2
        assert {p["pid"] for p in enriched["workers"]["known"]} == {"111", "222", "999"}
        assert enriched["aggregate"]["hits"] == 30
        assert enriched["aggregate"]["misses"] == 6
        assert enriched["aggregate"]["hit_rate"] == pytest.approx(30 / 36, abs=1e-3)
        assert enriched["aggregate"]["lock_retries"] == 2
        # Local blocks preserved.
        assert enriched["indicator_cache"]["hits"] == 10

    def test_single_worker_no_division_by_zero(self):
        zeroed = self._local(111, hits=0, misses=0)
        self.wm._report_worker(zeroed)
        # The aggregate call self-reports the SAME zeroed payload, so the
        # store record (and therefore the aggregate) stays at zero — no
        # division by zero, no phantom hits.
        enriched = self.wm.aggregate_worker_metrics(zeroed)
        assert enriched["aggregate"]["hit_rate"] == 0.0
        assert enriched["aggregate"]["hits"] == 0

    def test_missing_store_returns_local_only(self):
        enriched = self.wm.aggregate_worker_metrics(self._local(1))
        assert enriched["workers"]["active"] == 1  # self-report was written
        assert enriched["aggregate"]["hit_rate"] >= 0.0

    def test_corrupt_store_degrades_gracefully(self):
        self.file.parent.mkdir(parents=True, exist_ok=True)
        self.file.write_text("{corrupt", encoding="utf-8")
        enriched = self.wm.aggregate_worker_metrics(self._local(1))
        # Never raises; local blocks remain; no crash.
        assert enriched["indicator_cache"]["hits"] == 10
        assert "aggregate" in enriched or "workers" in enriched

    def test_no_secrets_or_paths_in_store(self):
        self.wm._report_worker(self._local(111, hits=5, misses=1))
        text = self.file.read_text(encoding="utf-8")
        assert "token" not in text.lower()
        assert "secret" not in text.lower()
        assert "password" not in text.lower()
        assert "C:" not in text and "/Users" not in text
        record = json.loads(text)["111"]
        # Sprint 13.2: the record also carries per-worker API request
        # totals (numeric only).  The intent of this test is that the
        # store contains NO secrets, credentials, or filesystem paths —
        # the exact key set is updated when an additive numeric block
        # is introduced (never a secret/path).
        assert set(record) == {
            "pid",
            "hostname",
            "last_seen",
            "indicator_cache",
            "json_store",
            "api_requests",
        }

    def test_metrics_endpoint_exposes_aggregate(self, monkeypatch):
        from fastapi.testclient import TestClient

        from src.api.main import app
        import src.utils.worker_metrics as wm

        self.wm._report_worker(self._local(os.getpid(), hits=8, misses=2))
        client = TestClient(app)
        payload = client.get("/metrics").json()
        assert payload["workers"]["active"] >= 1
        assert 0.0 <= payload["aggregate"]["hit_rate"] <= 1.0
        assert payload["aggregate"]["lock_retries"] >= 0
        assert payload["aggregate"]["stale_recoveries"] >= 0
        assert payload["aggregate"]["timeouts"] >= 0
        assert "token" not in str(payload).lower()


# ───────────────────────────────────────────────────────────────────
# Phase 10 — save_watchlist deprecation → removal (Sprint 12.1)
# ───────────────────────────────────────────────────────────────────


class TestSaveWatchlistRemoved:
    @pytest.fixture(autouse=True)
    def _isolate(self, monkeypatch, tmp_path):
        self.file = tmp_path / "watchlist.json"
        monkeypatch.setattr(watchlist_manager, "WATCHLIST_FILE", self.file)

    def test_raw_writer_removed_from_module(self):
        """Sprint 12.1 (Phase 4): the deprecated writer is gone entirely."""
        assert not hasattr(watchlist_manager, "save_watchlist")

    def test_canonical_mutation_path_preferred(self):
        # The canonical transactional path is the only writer — it works
        # with no deprecation warning (there is no deprecated path left).
        import warnings

        with warnings.catch_warnings():
            warnings.simplefilter("error", DeprecationWarning)
            assert watchlist_manager.add_stock("NABIL") is True
            assert watchlist_manager.remove_stock("NABIL") is True

    def test_insertion_order_via_canonical_path(self):
        """The ordering contract previously owned by the raw writer is
        now fully provided by the canonical mutation path."""
        watchlist_manager.add_stock("B")
        watchlist_manager.add_stock("A")
        assert list(watchlist_manager.load_watchlist().keys()) == ["B", "A"]
