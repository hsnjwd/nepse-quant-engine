"""Sprint 12.2 — Indicator cold-path profiling, Streamlit audit & corpus expansion.

Covers:

- **Profiling infrastructure** (Phase 1): ``benchmarks.profile_cold_path``
  — ``run_profiled`` produces a structured summary without wall-clock
  assertions, ``format_report`` renders it, the orchestrator
  ``run_profiling`` writes artifacts (pstats dump + text report +
  summary JSON) and runs under ``_state_files`` isolation so production
  state under ``data/alerts/`` is untouched.  Tiny sizes keep the test
  fast; deterministic structure, not timing, is asserted.
- **Streamlit metrics caching** (Phase 3): the ``@st.cache_data`` TTL
  wrapper ``_cached_fetch_metrics`` caches successful payloads, never
  caches raised errors (retried on the next call), honours ``clear()``
  for the manual refresh path, and the underlying pure ``fetch_metrics``
  still parses / rejects valid, non-object and unreachable responses.
  TTL expiry is verified with a short-TTL helper (no wall-clock
  assertions on production code — only that the Streamlit TTL mechanism
  behaves as documented).
- **Corpus audit** (Phase 4): ``benchmarks.corpus_audit`` is
  deterministic and read-only — schema validation, duplicate-symbol
  detection, too-few-rows, OHLC sanity and chronological ordering are
  exercised on a synthetic corpus; the audit must not modify the corpus
  directory nor touch production state files.

No exact runtime assertions anywhere — profiling/benchmark tests assert
structure, determinism and isolation only.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from src.config import METRICS_FETCH_TTL_S


# ───────────────────────────────────────────────────────────────────
# Phase 1 — Profiling infrastructure
# ───────────────────────────────────────────────────────────────────


class TestProfilingInfrastructure:
    def test_run_profiled_summary_structure(self):
        """``run_profiled`` returns a structured summary for any callable.

        Structure is asserted, never timing: ``total_s`` is a non-negative
        number and ``top`` is a non-empty list of function records with
        the documented keys.
        """
        from benchmarks.profile_cold_path import run_profiled

        # A nontrivial workload keeps ``total_s`` meaningfully measurable
        # while the assertion stays structural (``>= 0``, not an exact
        # wall-clock value).
        summary = run_profiled(lambda: sum(i * i for i in range(100_000)))
        assert summary["total_s"] >= 0
        assert summary["top"]
        record = summary["top"][0]
        for key in ("function", "module", "line", "ncalls", "calls",
                    "tottime_s", "cumtime_s", "tottime_pct", "cumtime_pct"):
            assert key in record

    def test_run_profiled_dumps_pstats(self, tmp_path):
        """The pstats dump is written and is re-readable with pstats."""
        from benchmarks.profile_cold_path import run_profiled

        dump = tmp_path / "sample.prof"
        summary = run_profiled(lambda: sum(i * i for i in range(100_000)), dump_path=dump)
        assert dump.exists()
        assert dump.stat().st_size > 0
        assert summary["total_s"] >= 0

        import pstats

        stats = pstats.Stats(str(dump))
        assert len(stats.stats) > 0

    def test_format_report_renders_summary(self):
        from benchmarks.profile_cold_path import format_report, run_profiled

        summary = run_profiled(lambda: sorted([3, 1, 2]))
        text = format_report(summary, "test workload")
        assert "test workload" in text
        assert "total wall time" in text
        assert any(r["function"][:42] in text for r in summary["top"])

    def test_run_profiling_isolates_production_state(self, tmp_path):
        """``run_profiling`` (tiny sizes) never touches production state.

        The alert-history file set under ``data/alerts/`` (name + size +
        mtime) must be identical before and after, and the profile
        artifacts (JSON + pstats dump + text report) must be written.
        """
        from benchmarks.profile_cold_path import run_profiling

        alerts_dir = Path("data/alerts")

        def _file_set() -> set[tuple[str, int, int]]:
            if not alerts_dir.exists():
                return set()
            return {
                (p.name, p.stat().st_size, p.stat().st_mtime_ns)
                for p in alerts_dir.iterdir()
                if p.is_file()
            }

        before = _file_set()

        out = tmp_path / "profiles"
        results = run_profiling(sizes=(2, 3), rows=100, out_dir=out, single=True)

        assert results["environment"]["python"]
        assert results["single"]["rows"] == 100
        assert len(results["sizes"]) == 2
        for size_result in results["sizes"]:
            assert size_result["symbols"] in (2, 3)
            assert size_result["summary"]["total_s"] > 0
            assert Path(size_result["dump_path"]).exists()
            assert Path(size_result["report_path"]).exists()
        # The orchestrator writes a machine-readable summary JSON too.
        summary_json = out / "profile_summary.json"
        assert summary_json.exists()
        loaded = json.loads(summary_json.read_text(encoding="utf-8"))
        assert len(loaded["sizes"]) == 2

        # Production state untouched — including the profile's own runs
        # firing the alert engine (redirected by _state_files).
        assert _file_set() == before

    def test_representative_corpus_loads(self):
        """A representative synthetic corpus can be profiled end-to-end
        (deterministic seeds — the workload, not the timing, is fixed)."""
        from benchmarks.common import write_csvs
        from benchmarks.profile_cold_path import profile_cold_scan

        import tempfile

        with tempfile.TemporaryDirectory(prefix="nepse_prof_repr_") as tmp:
            data_dir = Path(tmp) / "data"
            write_csvs(data_dir, 5, 120)
            summary = profile_cold_scan(data_dir)
            assert summary["total_s"] > 0
            assert summary["top"]


# ───────────────────────────────────────────────────────────────────
# Phase 2/3 — Streamlit metrics fetch + caching
# ───────────────────────────────────────────────────────────────────


class TestMetricsCaching:
    def test_cache_ttl_config_documented(self, monkeypatch):
        """The metrics fetch TTL is configurable and the *default* sits
        in the documented 5-10 s operational band (Sprint 12.2 Phase 3
        requirement).  When an operator overrides the env var, the value
        must still be positive — the band applies to the default only."""
        if "METRICS_FETCH_TTL_S" not in __import__("os").environ:
            assert 5 <= METRICS_FETCH_TTL_S <= 10
        assert METRICS_FETCH_TTL_S > 0

    def test_cached_fetch_serves_success_and_hits_cache(self, monkeypatch):
        """A successful payload is cached: a second identical call must
        not re-invoke the underlying ``fetch_metrics`` (st.cache_data
        with memory storage works outside the Streamlit runtime)."""
        from src.ui.pages import metrics_page

        # The memory cache is process-global and persists across tests;
        # clear defensively so a stale entry from a previous test (or a
        # rerun) can never fake a hit/miss.
        metrics_page._cached_fetch_metrics.clear()
        calls = {"n": 0}
        payload = {"process": {"pid": 1}}

        def _fake_fetch(base_url=None, timeout=0.0):
            calls["n"] += 1
            return payload

        monkeypatch.setattr(metrics_page, "fetch_metrics", _fake_fetch)
        # Unique base_url so this test's cache entry cannot collide with
        # another test's entry.
        url = "http://127.0.0.1:1/success"
        first = metrics_page._cached_fetch_metrics(url, 0.1)
        second = metrics_page._cached_fetch_metrics(url, 0.1)
        assert first == payload
        assert second == payload
        assert calls["n"] == 1  # cache hit — no second fetch

    def test_cached_fetch_never_caches_errors(self, monkeypatch):
        """A raised error must not be cached: the next call re-runs the
        fetch (an outage recovers on the next rerun, never pinned)."""
        from src.ui.pages import metrics_page

        metrics_page._cached_fetch_metrics.clear()
        calls = {"n": 0}

        def _flaky_fetch(base_url=None, timeout=0.0):
            calls["n"] += 1
            if calls["n"] == 1:
                raise OSError("backend down")
            return {"process": {"pid": 2}}

        monkeypatch.setattr(metrics_page, "fetch_metrics", _flaky_fetch)
        url = "http://127.0.0.1:1/error"
        with pytest.raises(OSError):
            metrics_page._cached_fetch_metrics(url, 0.1)
        # Second call must re-invoke (error was NOT cached) and succeed.
        result = metrics_page._cached_fetch_metrics(url, 0.1)
        assert result == {"process": {"pid": 2}}
        assert calls["n"] == 2

    def test_cached_fetch_clear_forces_refetch(self, monkeypatch):
        """``clear()`` (the manual refresh path) invalidates the cache:
        the next call re-invokes the underlying fetch."""
        from src.ui.pages import metrics_page

        metrics_page._cached_fetch_metrics.clear()
        calls = {"n": 0}

        def _fake_fetch(base_url=None, timeout=0.0):
            calls["n"] += 1
            return {"process": {"pid": calls["n"]}}

        monkeypatch.setattr(metrics_page, "fetch_metrics", _fake_fetch)
        url = "http://127.0.0.1:1/clear"
        metrics_page._cached_fetch_metrics(url, 0.1)
        metrics_page._cached_fetch_metrics(url, 0.1)  # cache hit
        assert calls["n"] == 1
        metrics_page._cached_fetch_metrics.clear()
        metrics_page._cached_fetch_metrics(url, 0.1)  # forced refetch
        assert calls["n"] == 2

    def test_cache_ttl_expiry_refetches(self):
        """The Streamlit TTL mechanism expires: after the TTL a call
        re-invokes the underlying function (verified with a short-TTL
        helper so the test stays fast and needs no wall-clock assertion
        on production code)."""
        import streamlit as st

        calls = {"n": 0}

        @st.cache_data(ttl=0.5, show_spinner=False)
        def _short_ttl(x: str) -> str:
            calls["n"] += 1
            return f"{x}:{calls['n']}"

        _short_ttl("k")
        _short_ttl("k")  # within TTL -> cache hit
        assert calls["n"] == 1
        time.sleep(0.7)
        _short_ttl("k")  # past TTL -> refetch
        assert calls["n"] == 2

    def test_fetch_metrics_success_and_failures(self, monkeypatch):
        """The pure fetch path still parses a valid response and rejects
        non-object payloads / unreachable backends (regression)."""
        import urllib.request

        from src.ui.pages import metrics_page

        class _FakeResp:
            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def read(self):
                return b'{"process": {"pid": 3}}'

        monkeypatch.setattr(
            urllib.request, "urlopen", lambda url, timeout=0.0: _FakeResp()
        )
        assert metrics_page.fetch_metrics("http://127.0.0.1:9") == {"process": {"pid": 3}}

        class _ListResp(_FakeResp):
            def read(self):
                return b"[1, 2, 3]"

        monkeypatch.setattr(
            urllib.request, "urlopen", lambda url, timeout=0.0: _ListResp()
        )
        with pytest.raises(ValueError):
            metrics_page.fetch_metrics("http://127.0.0.1:9", timeout=0.1)

        def _raise(url, timeout=0.0):
            raise OSError("connection refused")

        monkeypatch.setattr(urllib.request, "urlopen", _raise)
        with pytest.raises(OSError):
            metrics_page.fetch_metrics("http://127.0.0.1:9", timeout=0.1)


# ───────────────────────────────────────────────────────────────────
# Phase 4 — Corpus audit / validation
# ───────────────────────────────────────────────────────────────────


def _write_ohlcv_csv(path: Path, rows: list[tuple], sort_desc: bool = False) -> None:
    """Write an OHLCV CSV from ``(date, open, high, low, close, volume)`` rows."""
    import csv

    data = sorted(rows, reverse=sort_desc)
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["Date", "Open", "High", "Low", "Close", "Volume"])
        writer.writerows(data)


class TestCorpusAudit:
    # Row fixtures must exceed MIN_USABLE_ROWS (5) so the audit classifies
    # them as usable — a file with 2 rows is flagged "too-few-rows" and
    # cannot be used to test the other checks in isolation.
    _ROWS6 = [
        ("2026-01-02", 100, 105, 99, 104, 1000),
        ("2026-01-05", 104, 108, 102, 107, 1200),
        ("2026-01-06", 107, 110, 106, 109, 1300),
        ("2026-01-07", 109, 112, 108, 111, 900),
        ("2026-01-08", 111, 115, 110, 114, 1500),
        ("2026-01-09", 114, 117, 113, 116, 1600),
    ]

    def test_audit_valid_corpus(self, tmp_path):
        from benchmarks.corpus_audit import audit_corpus

        _write_ohlcv_csv(
            tmp_path / "nabil.csv",
            self._ROWS6,
            sort_desc=True,  # scraped newest-first — loader sorts
        )
        report = audit_corpus(tmp_path)
        assert report["file_count"] == 1
        assert report["usable"] == 1
        assert report["duplicates"] == []
        assert report["files"][0]["symbol"] == "nabil"

    def test_audit_detects_duplicate_symbols(self):
        """Duplicate detection is a pure helper (Windows' case-insensitive
        filesystem cannot host both ``NABIL.csv`` and ``nabil.csv``, so
        the duplicate test must not rely on two such files coexisting).
        The contract: *lowercase* stems in — the caller (``audit_corpus``)
        lowercases every ``path.stem`` before calling."""
        from benchmarks.corpus_audit import find_duplicate_symbols

        assert find_duplicate_symbols(["nabil", "nabil", "adbl"]) == ["nabil"]
        assert find_duplicate_symbols(["nabil", "adbl"]) == []
        assert find_duplicate_symbols([]) == []

    def test_audit_detects_too_few_rows_and_missing_columns(self, tmp_path):
        from benchmarks.corpus_audit import audit_corpus

        _write_ohlcv_csv(tmp_path / "tiny.csv", [("2026-01-02", 100, 105, 99, 104, 1000)])
        (tmp_path / "bad.csv").write_text(
            "Date,Close\n2026-01-02,100\n", encoding="utf-8"
        )
        report = audit_corpus(tmp_path)
        files = {f["symbol"]: f for f in report["files"]}
        assert files["tiny"]["ok"] is False
        assert any(p.startswith("too-few-rows") for p in files["tiny"]["problems"])
        assert files["bad"]["ok"] is False
        assert any(p.startswith("missing-columns") for p in files["bad"]["problems"])
        assert report["too_few_rows"] == 1

    def test_audit_detects_ohlc_insanity(self, tmp_path):
        from benchmarks.corpus_audit import audit_corpus

        rows = list(self._ROWS6)
        # High < Open on the second row — an OHLC sanity violation.
        rows[1] = ("2026-01-05", 104, 90, 102, 107, 1200)
        _write_ohlcv_csv(tmp_path / "badohlc.csv", rows)
        report = audit_corpus(tmp_path)
        files = {f["symbol"]: f for f in report["files"]}
        assert "ohlc-insanity" in files["badohlc"]["problems"]
        assert report["ohlc_issues"] == 1

    def test_audit_reports_raw_order_without_claiming_loader_wrong(self, tmp_path):
        """Scraped files are newest-first; the audit records the raw order
        problem while ``load_csv`` sorts ascending (canonical loader is
        the authority — the audit only reports the raw file)."""
        from benchmarks.corpus_audit import audit_corpus

        _write_ohlcv_csv(tmp_path / "desc.csv", self._ROWS6, sort_desc=True)
        report = audit_corpus(tmp_path)
        files = {f["symbol"]: f for f in report["files"]}
        assert files["desc"]["ok"] is True  # order alone is not fatal
        assert "dates-not-ascending" in files["desc"]["problems"]

    def test_audit_flags_non_numeric_fields_separately(self, tmp_path):
        """A file with quoted thousands separators (a scraped-format quirk,
        like the real ``sample.csv``) must be flagged ``non-numeric`` —
        *not* ``ohlc-insanity``, which is reserved for real High<Low
        logic violations.  The date cell is still recorded, so the audit
        reports coverage even for an all-non-numeric file."""
        import csv

        from benchmarks.corpus_audit import audit_corpus

        path = tmp_path / "quoted.csv"
        with path.open("w", encoding="utf-8", newline="") as f:
            writer = csv.writer(f, quoting=csv.QUOTE_ALL)
            writer.writerow(["Date", "Open", "High", "Low", "Close", "Volume"])
            writer.writerow(["2026-01-02", "100", "105", "99", "104", "57,566.00"])
            writer.writerow(["2026-01-05", "104", "108", "102", "107", "76,835.00"])

        report = audit_corpus(tmp_path)
        files = {f["symbol"]: f for f in report["files"]}
        problems = files["quoted"]["problems"]
        assert "non-numeric" in problems
        assert "ohlc-insanity" not in problems
        assert report["non_numeric"] == 1
        # Date cells are still collected from non-numeric rows.
        assert files["quoted"]["first_date"] == "2026-01-02"
        assert files["quoted"]["last_date"] == "2026-01-05"

    def test_audit_never_raises_on_ragged_rows(self, tmp_path):
        """A data row with fewer fields than the header (ragged) must be
        reported, never raised — the audit's documented contract."""
        from benchmarks.corpus_audit import audit_corpus

        (tmp_path / "ragged.csv").write_text(
            "Date,Open,High,Low,Close,Volume\n"
            "2026-01-02,100,105,99,104,1000\n"
            "2026-01-05,104\n",  # short row
            encoding="utf-8",
        )
        report = audit_corpus(tmp_path)  # must not raise
        files = {f["symbol"]: f for f in report["files"]}
        assert files["ragged"]["ok"] is False  # too-few-rows / ohlc flags

    def test_audit_is_read_only(self, tmp_path):
        """Auditing must never modify the corpus directory (no writes to
        the audited dir — file set, size and mtime stay identical)."""
        from benchmarks.corpus_audit import audit_corpus

        rows = [("2026-01-02", 100, 105, 99, 104, 1000)]
        _write_ohlcv_csv(tmp_path / "nabil.csv", rows)

        def _snapshot() -> set[tuple[str, int, int]]:
            return {
                (p.name, p.stat().st_size, p.stat().st_mtime_ns)
                for p in tmp_path.iterdir()
                if p.is_file()
            }

        before = _snapshot()
        audit_corpus(tmp_path)
        assert _snapshot() == before

    def test_audit_never_touches_production_state(self, tmp_path):
        """Even auditing the *real* corpus must leave ``data/alerts``
        (alert history + lock) untouched — same safeguard as the
        benchmark suite."""
        from benchmarks.corpus_audit import audit_corpus

        alerts_dir = Path("data/alerts")

        def _file_set() -> set[tuple[str, int, int]]:
            if not alerts_dir.exists():
                return set()
            return {
                (p.name, p.stat().st_size, p.stat().st_mtime_ns)
                for p in alerts_dir.iterdir()
                if p.is_file()
            }

        before = _file_set()
        audit_corpus(tmp_path)  # synthetic corpus
        assert _file_set() == before
