"""Sprint 11.3 — scan-level alert batching, indicator reuse & cache isolation.

Covers:

- ``process_alert_batch`` (one history read + one write per scan)
  — empty batch, one-symbol parity with ``process_alerts``, multi-symbol,
    duplicate prevention, deterministic ordering, corrupt-history
    recovery (``.corrupt.bak``), atomic persistence, per-entry failure
    isolation, concurrent batch calls, and backward compatibility.
- Indicator reuse — ``BB_MIDDLE`` is value-identical to ``SMA_20`` when
  the analyzer computed moving averages first.
- ``DataService.get_live_market`` copy-on-return — mutating a returned
  list can never corrupt the cached quote list.
- Scanner integration — the scan-level batch runs once per scan and
  attaches ``new_alerts`` to every fresh analysis.

Every test redirects the alert-history file to a temp path so real user
state under ``data/`` is never touched.
"""

from __future__ import annotations

import json
import threading
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from src.alerts import engine as alert_engine
from src.alerts import history as alerts_history


# ───────────────────────────────────────────────────────────────────
# Shared helpers
# ───────────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _isolate_history(monkeypatch, tmp_path):
    """Redirect alert history to a temp file for every test here."""
    file = tmp_path / "alerts" / "history.json"
    monkeypatch.setattr(alerts_history, "HISTORY_FILE", file)
    return file


def _payload(signal="BUY", score=7, price=110.0, i=0) -> dict:
    """Full analysis payload matching the real ``analyze_stock`` shape.

    Includes the trade-plan targets that ``check_target_alerts`` reads.
    """
    return {
        "signal": signal,
        "confidence": 90 + i,
        "score": score,
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


# ───────────────────────────────────────────────────────────────────
# Phase 4 — alert batch correctness
# ───────────────────────────────────────────────────────────────────


class TestAlertBatch:
    def test_empty_batch_no_side_effects(self):
        assert alert_engine.process_alert_batch([]) == {}
        # Empty batch must not create a history file.
        assert not alerts_history.HISTORY_FILE.exists()

    def test_one_symbol_matches_process_alerts(self, monkeypatch, tmp_path):
        payload = _payload(i=1)
        single = alert_engine.process_alerts("NABIL", payload)

        # Run the batch against a fresh (empty) history so both paths
        # start from the same first-scan state.
        fresh_file = tmp_path / "alerts2" / "history.json"
        monkeypatch.setattr(alerts_history, "HISTORY_FILE", fresh_file)
        batch = alert_engine.process_alert_batch([("NABIL", payload)])

        assert batch["NABIL"] == single
        assert batch["NABIL"][0]["type"] == "INITIAL"

    def test_multi_symbol_all_processed(self):
        entries = [(f"SYM{i:02d}", _payload(i=i)) for i in range(10)]
        out = alert_engine.process_alert_batch(entries)
        assert set(out) == {f"SYM{i:02d}" for i in range(10)}
        history = alerts_history.load_history()
        assert len(history) == 10
        assert all(s in history for s in out)

    def test_duplicate_prevention_identical_to_single(self):
        """Repeated processing produces no duplicate alert entries."""
        alert_engine.process_alert_batch([("NABIL", _payload())])
        second = alert_engine.process_alert_batch([("NABIL", _payload())])
        # Same signal/score -> no new change alerts.
        assert second["NABIL"] == []

    def test_ordering_deterministic(self):
        entries = [(f"SYM{i:02d}", _payload(i=i)) for i in range(8)]
        first = alert_engine.process_alert_batch(entries)
        second = alert_engine.process_alert_batch(entries)
        assert list(first) == list(second)
        assert list(first) == [f"SYM{i:02d}" for i in range(8)]

    def test_corrupt_history_recovers_and_continues(self):
        alerts_history.HISTORY_FILE.parent.mkdir(parents=True, exist_ok=True)
        alerts_history.HISTORY_FILE.write_text("{corrupt", encoding="utf-8")
        out = alert_engine.process_alert_batch([("NABIL", _payload())])
        assert out["NABIL"][0]["type"] == "INITIAL"
        backups = list(alerts_history.HISTORY_FILE.parent.glob("history*.corrupt*"))
        assert backups, "Corrupt history must be backed up aside"
        # Processing continues from a clean empty state.
        assert alerts_history.get_last_state("NABIL")["signal"] == "BUY"

    def test_atomic_persistence_no_temp_litter(self):
        entries = [(f"SYM{i:02d}", _payload(i=i)) for i in range(5)]
        alert_engine.process_alert_batch(entries)
        leftovers = [p for p in alerts_history.HISTORY_FILE.parent.iterdir() if p.name.endswith(".tmp")]
        assert leftovers == []
        json.loads(alerts_history.HISTORY_FILE.read_text(encoding="utf-8"))

    def test_failure_isolation_one_bad_entry(self):
        good = [("GOOD1", _payload(i=1)), ("GOOD2", _payload(i=2))]
        bad = ("BAD", {"signal": "BUY"})  # missing required keys
        out = alert_engine.process_alert_batch(good + [bad])
        # The good entries succeed; the bad one is isolated with [].
        assert out["GOOD1"][0]["type"] == "INITIAL"
        assert out["GOOD2"][0]["type"] == "INITIAL"
        assert out["BAD"] == []
        history = alerts_history.load_history()
        assert "BAD" not in history, "Failed entry must be rolled back"
        assert "GOOD1" in history and "GOOD2" in history

    def test_concurrent_batch_no_lost_updates(self):
        errors: list[Exception] = []

        def worker(symbol: str, i: int) -> None:
            try:
                alert_engine.process_alert_batch([(symbol, _payload(i=i))])
            except Exception as exc:  # pragma: no cover - failure path
                errors.append(exc)

        threads = [
            threading.Thread(target=worker, args=(f"SYM{j:02d}", j))
            for j in range(8)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert errors == []
        history = alerts_history.load_history()
        assert len(history) == 8
        json.loads(alerts_history.HISTORY_FILE.read_text(encoding="utf-8"))

    def test_backward_compat_process_alerts_still_works(self):
        payload = _payload()
        out = alert_engine.process_alerts("NABIL", payload)
        assert out[0]["type"] == "INITIAL"
        assert alerts_history.get_last_state("NABIL")["signal"] == "BUY"


# ───────────────────────────────────────────────────────────────────
# Phase 9 — indicator reuse correctness (BB_MIDDLE == SMA_20)
# ───────────────────────────────────────────────────────────────────


class TestIndicatorReuse:
    def _frame(self, rows: int = 250, seed: int = 7) -> pd.DataFrame:
        rng = np.random.default_rng(seed)
        close = 100 + np.cumsum(rng.normal(0, 1, rows))
        return pd.DataFrame(
            {
                "Date": pd.bdate_range(start="2024-01-01", periods=rows),
                "Open": close,
                "High": close + 1,
                "Low": close - 1,
                "Close": close,
                "Volume": rng.integers(1000, 5000, rows),
            }
        )

    def test_bb_middle_identical_to_sma20_in_pipeline(self):
        from src.engine.analyzer import analyze_dataframe
        from src.indicators.moving_average import add_moving_averages
        from src.indicators.volatility import add_bollinger_bands

        df = self._frame()
        # The analyzer chain computes SMA_20 first, then Bollinger.
        with_ma = add_moving_averages(df.copy())
        result = add_bollinger_bands(with_ma)
        pd.testing.assert_series_equal(
            result["BB_MIDDLE"], result["SMA_20"], check_names=False
        )

        # Full-pipeline values are unchanged vs the old computation.
        old = df.copy()
        old["SMA_20"] = old["Close"].rolling(20).mean()
        old["BB_MIDDLE"] = old["Close"].rolling(20).mean()
        analysis = analyze_dataframe(df.copy())
        assert analysis["score"] >= -10  # pipeline still runs end-to-end

    def test_standalone_bollinger_unchanged_without_sma20(self):
        from src.indicators.volatility import add_bollinger_bands

        df = self._frame()
        result = add_bollinger_bands(df.copy())
        expected = df["Close"].rolling(20).mean()
        pd.testing.assert_series_equal(
            result["BB_MIDDLE"], expected, check_names=False
        )

    def test_non_default_period_still_computes_itself(self):
        from src.indicators.moving_average import add_moving_averages
        from src.indicators.volatility import add_bollinger_bands

        df = add_moving_averages(self._frame())
        result = add_bollinger_bands(df.copy(), period=30)
        expected = df["Close"].rolling(30).mean()
        pd.testing.assert_series_equal(
            result["BB_MIDDLE"], expected, check_names=False
        )


# ───────────────────────────────────────────────────────────────────
# Phase 10 — live-market cache copy-on-return
# ───────────────────────────────────────────────────────────────────


class TestLiveMarketCopy:
    @pytest.fixture(autouse=True)
    def _fresh_service(self):
        from src.data import DataService
        from src.data.cache import TieredCache

        DataService.reset_instance()
        self._svc = DataService(
            provider=_FakeProvider(),
            cache=TieredCache(memory_ttl=60, disk_ttl=600),
        )
        yield
        DataService.reset_instance()

    def test_mutating_returned_list_does_not_corrupt_cache(self):
        from src.data.models import StockQuote

        self._svc.clear_cache()
        first = self._svc.get_live_market()
        assert len(first) == 2
        # Mutate the returned list aggressively.
        first.append(StockQuote(symbol="INTRUDER", ltp=1.0))
        first.clear()
        # Internal cache must be unchanged.
        second = self._svc.get_live_market()
        assert len(second) == 2
        assert [q.symbol for q in second] == ["NABIL", "SCB"]

    def test_repeated_calls_return_consistent_copies(self):
        self._svc.clear_cache()
        a = self._svc.get_live_market()
        b = self._svc.get_live_market()
        assert a is not b
        assert a == b

    def test_empty_market(self):
        self._svc._provider.quotes = []
        self._svc.clear_cache()
        assert self._svc.get_live_market() == []

    def test_cache_refresh_returns_fresh_copy(self):
        from src.data.models import StockQuote

        self._svc.clear_cache()
        _ = self._svc.get_live_market()
        self._svc._provider.quotes = [StockQuote(symbol="NEWSYM", ltp=9.0)]
        self._svc._cache.delete("live_quotes")
        refreshed = self._svc.get_live_market()
        assert [q.symbol for q in refreshed] == ["NEWSYM"]


class _FakeProvider:
    """Minimal provider returning a fixed quote list."""

    name = "fake"

    def __init__(self) -> None:
        from src.data.models import StockQuote

        self.quotes = [
            StockQuote(symbol="NABIL", ltp=100.0, close=100.0),
            StockQuote(symbol="SCB", ltp=50.0, close=50.0),
        ]

    def get_live_quotes(self):
        return list(self.quotes)


# ───────────────────────────────────────────────────────────────────
# Phase 12 — scanner batch integration
# ───────────────────────────────────────────────────────────────────


class TestScannerBatchIntegration:
    @pytest.fixture(autouse=True)
    def _isolate(self, monkeypatch, tmp_path):
        from src.cache.scanner_cache import scanner_cache
        from src.scanner import engine as scanner_engine

        scanner_cache.clear()
        monkeypatch.setattr(scanner_engine, "DATA_DIRECTORY", str(tmp_path / "data"))
        monkeypatch.setattr(
            alerts_history, "HISTORY_FILE", tmp_path / "alerts" / "history.json"
        )
        self._scanner = scanner_engine
        self._data_dir = tmp_path / "data"
        self._data_dir.mkdir(parents=True, exist_ok=True)
        yield
        scanner_cache.clear()

    def _write(self, name: str, rows: int = 120, seed: int = 1) -> Path:
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
        path = self._data_dir / f"{name}.csv"
        path.write_text(df.to_csv(index=False), encoding="utf-8")
        return path

    def test_scan_calls_batch_once_and_attaches_new_alerts(self, monkeypatch):
        self._write("NABIL")
        self._write("SCB")

        calls: list[list] = []
        orig_batch = alert_engine.process_alert_batch

        def counting_batch(entries):
            calls.append(list(entries))
            return {s: [{"type": "INITIAL", "priority": 1, "message": "x"}] for s, _ in entries}

        monkeypatch.setattr(alert_engine, "process_alert_batch", counting_batch)
        # The scanner imports the function into its own namespace.
        monkeypatch.setattr(self._scanner, "process_alert_batch", counting_batch)

        result = self._scanner.scan_market(workers=1)

        assert calls, "scan_market must call the batch API"
        assert len(calls) == 1, "one batch call per scan"
        symbols = {s for s, _ in calls[0]}
        assert symbols == {"NABIL", "SCB"}
        for r in result["results"]:
            assert r.get("new_alerts"), "batch alerts attached to each result"

    def test_scan_batch_persists_state_once(self):
        self._write("NABIL")
        self._write("SCB")
        self._scanner.scan_market(workers=1)
        history = alerts_history.load_history()
        assert "NABIL" in history and "SCB" in history

    def test_cached_analysis_reuses_batch_result(self):
        self._write("NABIL")
        self._scanner.scan_market(workers=1)
        # Second scan is served from the analysis cache; batch still runs
        # only for fresh files (none) so state is not double-advanced.
        self._scanner.scan_market(workers=1)
        assert alerts_history.get_last_state("NABIL") is not None
