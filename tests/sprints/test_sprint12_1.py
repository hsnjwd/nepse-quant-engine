"""Sprint 12.1 — Analyze p99 gate, Streamlit Metrics dashboard & 500+ profiling.

Covers:

- **Warm /api/analyze p99 CI gate** (Phase 1): ``benchmarks.ci_gate``
  gains a configurable absolute per-symbol p99 tripwire
  (``analyze_p99_max_ms``, default ``ANALYZE_P99_MAX_MS`` = 2000 ms).
  The session ``ci_gate_artifact`` proves the check passes at the
  default threshold on a working cache; a fast, small-corpus
  ``run_gate`` call with an absurdly strict threshold proves the FAIL
  wiring and the non-zero exit path — without duplicating the full
  gate measurement.
- **Streamlit Metrics dashboard** (Phase 2): the page's pure data layer
  (``fetch_metrics`` / ``summarize``) is unit-tested against valid,
  empty-worker, stale-worker, malformed and unreachable-API payloads —
  the page must never crash on any of them.
- **500+ symbol profiling** (Phase 3): ``bench_alert_batch_scale`` runs
  on a synthetic corpus in private temp dirs and must not touch
  production ``data/alerts/history.json`` (``_state_files`` isolation).
- **Watchlist writer removal** (Phase 4): ``save_watchlist`` is gone
  from ``src.watchlist.manager``; the canonical mutation path works.

Every test monkeypatches module-level file constants / flags so real
user state under ``data/`` and production alert history are never
touched.
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from src.watchlist import manager as watchlist_manager


# ───────────────────────────────────────────────────────────────────
# Phase 1 — warm /api/analyze p99 CI gate
# ───────────────────────────────────────────────────────────────────


class TestAnalyzeP99Gate:
    def test_gate_artifact_carries_p99_check_and_passes(self, ci_gate_artifact):
        """The session gate artifact records the p99 check and passes it
        at the default threshold on a working cache.

        ``warm_api_p99`` is deliberately absolute (a severe-regression
        tripwire), so the PASS assertion is exactly as machine-robust
        as the existing ``cold_load`` absolute check: the default 2000
        ms leaves ~4x margin over a slow CI runner at the measured
        reference p99 (~178 ms on the 20x300 synthetic corpus).
        """
        from benchmarks import ci_gate

        artifact = ci_gate_artifact
        checks = artifact["checks"]
        assert "warm_api_p99" in checks
        assert "api_analyze_warm_p99_ms" in artifact
        assert "api_analyze_warm_p99_max_ms" in artifact
        assert artifact["api_analyze_warm_p99_max_ms"] == ci_gate.ANALYZE_P99_MAX_MS
        assert artifact["analyze_p99_max_ms"] == ci_gate.ANALYZE_P99_MAX_MS
        assert checks["warm_api_p99"] is True

    def test_gate_fails_with_strict_p99_threshold(self, monkeypatch, tmp_path):
        """An absurdly strict threshold must flip ``warm_api_p99``.

        Uses a small corpus (4 symbols x 100 rows) so the FAIL-path
        ``run_gate`` call is fast (~1 s) and never duplicates the full
        session gate measurement.  The threshold 0.001 ms is impossible
        for any real measurement, so the wiring (check False -> verdict
        FAIL) is proven end-to-end with a real measurement.
        """
        from benchmarks import ci_gate

        monkeypatch.setattr(ci_gate, "GATE_SYMBOLS", 4)
        monkeypatch.setattr(ci_gate, "GATE_ROWS", 100)

        out = tmp_path / "gate_strict.json"
        passed, artifact = ci_gate.run_gate(
            out_file=out,
            analyze_p99_max_ms=0.001,
        )
        assert artifact["checks"]["warm_api_p99"] is False
        assert artifact["api_analyze_warm_p99_ms"] > 0.001
        assert artifact["verdict"] == "FAIL"
        assert passed is False


# ───────────────────────────────────────────────────────────────────
# Phase 2 — Streamlit Metrics dashboard (pure data layer)
# ───────────────────────────────────────────────────────────────────


def _valid_payload(now: float) -> dict:
    return {
        "process": {"pid": 1111, "hostname": "worker-a"},
        "workers": {
            "active": 2,
            "ttl_s": 60.0,
            "known": [
                {"pid": "1111", "hostname": "worker-a", "last_seen": now - 2},
                {"pid": "2222", "hostname": "worker-b", "last_seen": now - 5},
            ],
        },
        "aggregate": {
            "hits": 100,
            "misses": 20,
            "hit_rate": 0.833,
            "lock_retries": 3,
            "stale_recoveries": 1,
            "timeouts": 0,
        },
        "indicator_cache": {"hits": 60, "misses": 10, "entries": 5, "hit_rate": 0.857},
        "json_store": {"retries": 1, "stale_recoveries": 0, "timeouts": 0},
    }


class TestMetricsSummarize:
    def test_summarize_valid_payload(self):
        from src.ui.pages.metrics_page import summarize

        now = time.time()
        summary = summarize(_valid_payload(now), now=now)
        assert summary["status"] == "ok"
        assert summary["workers_total"] == 2
        assert summary["workers_active"] == 2
        assert summary["aggregate"]["hits"] == 100
        assert summary["aggregate"]["misses"] == 20
        assert summary["aggregate"]["hit_rate"] == pytest.approx(0.833, abs=0.001)
        assert summary["aggregate"]["lock_retries"] == 3
        assert summary["aggregate"]["stale_recoveries"] == 1
        assert summary["workers"][0]["pid"] == "1111"
        assert summary["workers"][0]["active"] is True

    def test_summarize_no_workers(self):
        from src.ui.pages.metrics_page import summarize

        payload = {"workers": {"active": 0, "known": []}}
        summary = summarize(payload)
        assert summary["status"] == "no_workers"
        assert summary["workers_total"] == 0
        assert summary["workers_active"] == 0
        assert summary["aggregate"]["hit_rate"] == 0.0

    def test_summarize_stale_workers(self):
        from src.ui.pages.metrics_page import summarize

        now = time.time()
        payload = {
            "workers": {
                "active": 0,
                "ttl_s": 60.0,
                "known": [{"pid": "1111", "hostname": "a", "last_seen": now - 500}],
            }
        }
        summary = summarize(payload, now=now)
        assert summary["status"] == "stale"
        assert summary["workers_total"] == 1
        assert summary["workers_active"] == 0
        assert summary["workers"][0]["active"] is False

    def test_summarize_malformed_records_never_raises(self):
        from src.ui.pages.metrics_page import summarize

        now = time.time()
        payload = {
            "workers": {
                "active": None,
                "ttl_s": "not-a-number",
                "known": [
                    "garbage",                     # non-dict record -> skipped
                    {"pid": None, "hostname": None, "last_seen": None},
                    {"pid": "x", "hostname": "y", "last_seen": now - 1},
                ],
            },
            "aggregate": {
                "hits": "not-an-int",
                "misses": None,
                "lock_retries": {},
                "stale_recoveries": [],
                "timeouts": None,
            },
        }
        summary = summarize(payload, now=now)
        assert summary["status"] in ("ok", "stale")
        assert summary["workers_total"] == 2  # garbage skipped
        assert summary["aggregate"]["hits"] == 0  # coercion default
        assert summary["aggregate"]["misses"] == 0
        assert summary["aggregate"]["lock_retries"] == 0

    def test_summarize_missing_blocks(self):
        from src.ui.pages.metrics_page import summarize

        summary = summarize({})
        assert summary["status"] == "no_workers"
        assert summary["aggregate"]["hits"] == 0
        assert summary["aggregate"]["hit_rate"] == 0.0

    def test_summarize_non_dict_blocks_never_raise(self):
        """A malformed response with non-dict blocks (e.g.
        ``{"workers": "garbage"}`` or ``{"aggregate": 42}``) must not
        crash the pure helper — the sprint's malformed-response
        requirement applies at the data layer, not just the page."""
        from src.ui.pages.metrics_page import summarize

        summary = summarize({"workers": "garbage", "aggregate": 42, "process": None})
        assert summary["status"] == "no_workers"
        assert summary["workers_total"] == 0
        assert summary["aggregate"]["hits"] == 0
        assert summary["aggregate"]["hit_rate"] == 0.0
        assert summary["newest_last_seen"] == 0.0

        summary = summarize({"workers": [1, 2, 3], "aggregate": "nope"})
        assert summary["status"] == "no_workers"
        assert summary["aggregate"]["lock_retries"] == 0

        # A *truthy* non-dict process block (the real crash path: it
        # passes the old ``or {}`` truthiness and would have crashed
        # render() at ``process.get(...)``) must be coerced to {} too.
        summary = summarize({"process": "garbage"})
        assert summary["process"] == {}
        assert summary["status"] == "no_workers"


class TestMetricsFetch:
    def test_fetch_metrics_parses_valid_response(self, monkeypatch):
        import urllib.request

        from src.ui.pages import metrics_page

        class _FakeResp:
            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def read(self):
                return b'{"process": {"pid": 1}}'

        def _fake_urlopen(url, timeout=0.0):
            assert "/metrics" in url
            return _FakeResp()

        monkeypatch.setattr(urllib.request, "urlopen", _fake_urlopen)
        payload = metrics_page.fetch_metrics("http://127.0.0.1:9")
        assert payload == {"process": {"pid": 1}}

    def test_fetch_metrics_raises_when_unreachable(self, monkeypatch):
        import urllib.request

        from src.ui.pages import metrics_page

        def _raise(url, timeout=0.0):
            raise OSError("connection refused")

        monkeypatch.setattr(urllib.request, "urlopen", _raise)
        with pytest.raises(OSError):
            metrics_page.fetch_metrics("http://127.0.0.1:9", timeout=0.1)

    def test_fetch_metrics_raises_on_non_object_payload(self, monkeypatch):
        import urllib.request

        from src.ui.pages import metrics_page

        class _FakeResp:
            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def read(self):
                return b"[1, 2, 3]"

        monkeypatch.setattr(urllib.request, "urlopen", lambda url, timeout=0.0: _FakeResp())
        with pytest.raises(ValueError):
            metrics_page.fetch_metrics("http://127.0.0.1:9", timeout=0.1)


# ───────────────────────────────────────────────────────────────────
# Phase 3 — 500+ symbol profiling (bench infrastructure isolation)
# ───────────────────────────────────────────────────────────────────


class TestBatchScaleBenchmark:
    def test_scale_benchmark_isolates_production_state(self, tmp_path, monkeypatch):
        """The scaling benchmark must never touch production state files.

        Uses small synthetic sizes (50/100 symbols, 100 rows) so the
        test is fast while still exercising the same ``_state_files``
        isolation the 500/750/1000-symbol runs use.  After the run the
        production ``data/alerts/history.json`` (and its lock) must be
        untouched.
        """
        from benchmarks import pipeline

        alerts_dir = Path("data/alerts")

        # Snapshot the *entire* production data/alerts file set before
        # the run — name + size + mtime so even a same-size content
        # overwrite would be caught.  (Note: a pre-existing
        # history.json.corrupt.bak evidence file from the Sprint 11.1
        # corruption repair legitimately lives here — it must simply be
        # unchanged by the benchmark.)
        def _file_set() -> set[tuple[str, int, int]]:
            if not alerts_dir.exists():
                return set()
            return {
                (p.name, p.stat().st_size, p.stat().st_mtime_ns)
                for p in alerts_dir.iterdir()
                if p.is_file()
            }

        before = _file_set()

        result = pipeline.bench_alert_batch_scale(
            sizes=(50, 100), rows=100, cold_reps=1, warm_reps=1
        )

        assert len(result["sizes"]) == 2
        assert result["corpus"] == "synthetic"
        for size_result in result["sizes"]:
            assert size_result["corpus"] == "synthetic"
            assert size_result["symbols"] in (50, 100)
            assert size_result["batch"]["cold_reads"] >= 1
            assert size_result["batch"]["cold_writes"] >= 1
        assert "scaling" in result
        assert "summary" in result["scaling"]
        # The scaling classifier must report a valid observation.  (The
        # observation is asserted in the same run as the isolation check
        # so the multi-minute benchmark executes exactly once per
        # session — Sprint 12.0 Phase 11's no-duplicate-expense rule.)
        assert result["scaling"]["summary"]["observation"] in (
            "super-linear",
            "sub-linear",
            "approximately-linear",
        )

        # Production state is untouched — the file set (name, size,
        # mtime) must be identical before and after, so even a
        # same-size content overwrite of a production file would be
        # caught, and no new tmp/lock/corrupt litter may appear.
        assert _file_set() == before


# ───────────────────────────────────────────────────────────────────
# Phase 4 — save_watchlist removal
# ───────────────────────────────────────────────────────────────────


class TestWatchlistWriterRemoved:
    def test_no_code_references_anywhere(self):
        """No source or test file may *call* the removed writer.

        Uses ``ast`` so only real identifier / attribute references are
        flagged — docstrings and comments that merely *document* the
        removal (e.g. this module's docstring) are not code references.
        """
        import ast

        root = Path(__file__).resolve().parent.parent
        offenders: list[str] = []
        for base in (root / "src", root / "tests", root / "benchmarks"):
            for path in base.rglob("*.py"):
                if "__pycache__" in path.parts:
                    continue
                try:
                    tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
                except SyntaxError:
                    continue
                for node in ast.walk(tree):
                    if isinstance(node, ast.Name) and node.id == "save_watchlist":
                        offenders.append(f"{path.relative_to(root)}:{node.lineno}")
                    if isinstance(node, ast.Attribute) and node.attr == "save_watchlist":
                        offenders.append(f"{path.relative_to(root)}:{node.lineno}")
        assert not offenders, f"save_watchlist code references remain: {offenders}"

    def test_canonical_mutation_path_still_works(self, monkeypatch, tmp_path):
        monkeypatch.setattr(watchlist_manager, "WATCHLIST_FILE", tmp_path / "watchlist.json")
        assert watchlist_manager.add_stock("NABIL") is True
        assert watchlist_manager.add_stock("nabil") is False
        assert watchlist_manager.remove_stock("NABIL") is True
        assert watchlist_manager.load_watchlist() == {}
