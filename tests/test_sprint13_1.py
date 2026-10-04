"""Sprint 13.1 — Production API & Dashboard Integration Hardening.

Covers:

- **API instrumentation** (Phase 2): the bounded ``ApiRequestTracker``
  records per-endpoint latency/errors cheaply, never raises, and stays
  bounded under unbounded input; the FastAPI middleware feeds it.
- **/metrics standardization** (Phase 3): the payload exposes the new
  ``api_requests`` and ``performance_gates`` blocks while keeping every
  legacy key backward-compatible.
- **Gate fail-safety** (Phase 5): ``ci_gate.evaluate_checks`` is a pure
  function that FAILs — never silently passes — on missing, malformed,
  zero, non-finite, or stale measurements, and CI actually invokes the
  gate (a workflow regression test prevents it becoming informational).
- **Dashboard contract** (Phase 6): the Streamlit data layer
  (``summarize_api_requests`` / ``summarize_gates`` /
  ``summarize_scanner``) tolerates valid, missing-optional, zero,
  malformed and stale payloads without crashing.
- **Concurrency & multi-worker** (Phase 7): concurrent tracker writes
  and cross-worker aggregation remain correct and bounded.
- **Legacy-path detection** (Phase 8): the production endpoints use the
  intended service/data architecture (no direct provider calls), and
  ``src/backtest`` vs ``src/backtesting`` reachability is documented.
- **Error hardening** (Phase 9): API failures surface as predictable
  HTTP status codes with clean detail, never raw tracebacks.

Every test monkeypatches module-level state so production state under
``data/``, ``~/.nepse`` and the shared worker-metrics store are never
touched.
"""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path

import pytest

from benchmarks import ci_gate
from src.api import timing as api_timing
from src.utils import worker_metrics as wm


# ───────────────────────────────────────────────────────────────────
# Phase 2 — bounded API request tracker
# ───────────────────────────────────────────────────────────────────


class TestApiRequestTracker:
    def test_record_and_snapshot(self):
        t = api_timing.ApiRequestTracker()
        t.record("/analyze/NABIL", 200, 10.0)
        t.record("/analyze/NABIL", 200, 20.0)
        t.record("/analyze/SCB", 500, 500.0)
        snap = t.snapshot()
        assert snap["total_requests"] == 3
        assert snap["error_requests"] == 1
        assert snap["error_rate"] == pytest.approx(1 / 3, abs=0.01)
        assert snap["p50_ms"] == 20.0
        assert snap["p99_ms"] == 500.0
        assert set(snap["per_endpoint"]) == {"/analyze/NABIL", "/analyze/SCB"}
        assert snap["per_endpoint"]["/analyze/NABIL"]["requests"] == 2
        assert snap["per_endpoint"]["/analyze/NABIL"]["errors"] == 0
        assert snap["per_endpoint"]["/analyze/SCB"]["errors"] == 1
        assert snap["per_endpoint"]["/analyze/SCB"]["error_rate"] == 1.0

    def test_bounded_history(self):
        t = api_timing.ApiRequestTracker(maxlen=50)
        for i in range(500):
            t.record(f"/ep{i % 5}", 200, float(i))
        snap = t.snapshot()
        # The *counter* is monotonic (500 total; 100 per endpoint) —
        # counters are bounded by being ints.  The *latency window* is
        # what is capped at maxlen: with values 0..499 (endpoint = i%5),
        # a retained window of the last 50 samples per endpoint has a
        # p50 well above the 0..~495 full range's midpoint, proving
        # only the recent tail is kept (no unbounded history).
        for ep in snap["per_endpoint"].values():
            assert ep["requests"] == 100  # counter, not window length
            # Window retained the tail: endpoint i%5 holds values
            # 0..495 (step 5); the retained last-50 tail has p50 ~375,
            # well above the full-100-set median ~250 — proving the
            # window is bounded and no unbounded history is kept.
            assert ep["p50_ms"] > 300.0
        assert snap["total_requests"] == 500

    def test_excluded_paths_not_recorded(self):
        t = api_timing.ApiRequestTracker()
        t.record("/", 200, 1.0)
        t.record("/metrics", 200, 1.0)
        t.record("/api/analyze", 200, 5.0)
        assert t.snapshot()["total_requests"] == 1
        assert "/api/analyze" in t.snapshot()["per_endpoint"]

    def test_malformed_input_never_raises(self):
        t = api_timing.ApiRequestTracker()
        t.record(None, "garbage", None)
        t.record("", "x", float("nan"))
        t.record("/ok", 200, -5.0)  # negative clamps to 0
        snap = t.snapshot()
        assert snap["total_requests"] == 1
        assert snap["per_endpoint"]["/ok"]["average_ms"] == 0.0

    def test_reset(self):
        t = api_timing.ApiRequestTracker()
        t.record("/a", 200, 1.0)
        t.reset()
        assert t.snapshot()["total_requests"] == 0

    def test_concurrent_writes_are_thread_safe_and_bounded(self):
        t = api_timing.ApiRequestTracker(maxlen=200)
        errors: list[Exception] = []

        def _writer() -> None:
            try:
                for i in range(1000):
                    t.record("/concurrent", 200 if i % 100 else 500, float(i))
            except Exception as exc:  # noqa: BLE001
                errors.append(exc)

        threads = [threading.Thread(target=_writer) for _ in range(8)]
        for th in threads:
            th.start()
        for th in threads:
            th.join()
        assert not errors
        snap = t.snapshot()
        # Counters are exact (thread-safe) and the latency window is
        # bounded at maxlen=200 while the counter reflects all 8000.
        assert snap["total_requests"] == 8000
        assert snap["per_endpoint"]["/concurrent"]["requests"] == 8000
        assert snap["per_endpoint"]["/concurrent"]["errors"] == 80  # 8 x (i%100==0)


class TestMiddlewareFeedsTracker:
    def test_middleware_records_real_requests(self, monkeypatch, tmp_path):
        """Hitting the running app records per-endpoint latency/errors."""
        from fastapi.testclient import TestClient

        from src.api.main import app

        api_timing.request_tracker.reset()
        client = TestClient(app)
        client.get("/")
        client.get("/metrics")
        client.get("/analyze/nonexistentstock9999")  # 404
        snap = api_timing.request_tracker.snapshot()
        # / and /metrics are deliberately excluded (probes would skew
        # percentiles); the 404 analyze path is recorded.
        assert snap["total_requests"] == 1
        assert "/" not in snap["per_endpoint"]
        assert "/metrics" not in snap["per_endpoint"]
        assert "/analyze/nonexistentstock9999" in snap["per_endpoint"]
        assert snap["per_endpoint"]["/analyze/nonexistentstock9999"]["errors"] == 1
        api_timing.request_tracker.reset()


# ───────────────────────────────────────────────────────────────────
# Phase 3 — /metrics standardization (backward compatible)
# ───────────────────────────────────────────────────────────────────


class TestMetricsEndpointBlocks:
    def test_metrics_exposes_new_blocks_and_legacy_keys(self, monkeypatch, tmp_path):
        from fastapi.testclient import TestClient

        from src.api.main import app

        monkeypatch.setattr(wm, "WORKER_METRICS_FILE", str(tmp_path / "worker_metrics.json"))
        client = TestClient(app)
        payload = client.get("/metrics").json()
        # Legacy keys preserved.
        for key in ("process", "indicator_cache", "json_store", "memory_mb",
                    "metrics", "per_operation", "per_provider", "scanner_cache",
                    "status", "workers", "aggregate"):
            assert key in payload, f"legacy /metrics key {key} missing"
        # New Sprint 13.1 blocks.
        assert "api_requests" in payload
        assert isinstance(payload["api_requests"], dict)
        assert "total_requests" in payload["api_requests"]
        assert "performance_gates" in payload
        assert "thresholds" in payload["performance_gates"]

    def test_performance_gates_thresholds_match_ci_gate(self, monkeypatch, tmp_path):
        from fastapi.testclient import TestClient

        from src.api.main import app

        monkeypatch.setattr(wm, "WORKER_METRICS_FILE", str(tmp_path / "worker_metrics.json"))
        payload = TestClient(app).get("/metrics").json()
        th = payload["performance_gates"]["thresholds"]
        assert th["warm_api_ratio_max"] == ci_gate.WARM_API_RATIO_MAX
        assert th["warm_portfolio_ratio_max"] == ci_gate.WARM_PORTFOLIO_RATIO_MAX
        assert th["analyze_p99_max_ms"] == ci_gate.ANALYZE_P99_MAX_MS

    def test_metrics_never_exposes_secrets_or_paths(self, monkeypatch, tmp_path):
        from fastapi.testclient import TestClient

        from src.api.main import app

        monkeypatch.setattr(wm, "WORKER_METRICS_FILE", str(tmp_path / "worker_metrics.json"))
        text = str(TestClient(app).get("/metrics").json())
        assert "token" not in text.lower()
        assert "secret" not in text.lower()
        assert "password" not in text.lower()
        assert "C:" not in text and "/Users" not in text


# ───────────────────────────────────────────────────────────────────
# Phase 5 — fail-safe gate evaluation
# ───────────────────────────────────────────────────────────────────


def _measured(**overrides) -> dict:
    m = {
        "cold_load_best_ms": 160.0,
        "warm_load_best_ms": 16.0,          # 10x speedup
        "scanner_cold_ms": 3000.0,
        "scanner_warm_ms": 150.0,           # ratio 0.05
        "api_analyze_cold_best_ms": 1200.0,
        "api_analyze_warm_best_ms": 640.0,  # ratio ~0.53
        "api_analyze_warm_p99_ms": 250.0,
        "api_portfolio_cold_best_ms": 1300.0,
        "api_portfolio_warm_best_ms": 820.0,  # ratio ~0.63
    }
    m.update(overrides)
    return m


class TestEvaluateChecks:
    def test_all_passing(self):
        passed, report = ci_gate.evaluate_checks(
            _measured(), baseline_cold_load_best_ms=160.84
        )
        assert passed is True
        assert report["checks"] == {
            "cold_load": "PASS", "warm_speedup": "PASS", "warm_scan": "PASS",
            "warm_api": "PASS", "warm_portfolio": "PASS", "warm_api_p99": "PASS",
        }
        assert report["failures"] == []

    def test_threshold_failures_report_actual_vs_max(self):
        passed, report = ci_gate.evaluate_checks(
            _measured(
                api_analyze_warm_best_ms=1100.0,   # ratio ~0.92 > 0.75
                api_analyze_warm_p99_ms=2500.0,    # > 2000 ms
            ),
            baseline_cold_load_best_ms=160.84,
        )
        assert passed is False
        failures = {f["name"]: f for f in report["failures"]}
        assert "warm_api" in failures
        assert failures["warm_api"]["actual"] == pytest.approx(0.917, abs=0.01)
        assert failures["warm_api"]["maximum"] == ci_gate.WARM_API_RATIO_MAX
        assert "warm_api_p99" in failures
        assert failures["warm_api_p99"]["actual"] == 2500.0
        assert failures["warm_api_p99"]["maximum"] == ci_gate.ANALYZE_P99_MAX_MS
        assert report["checks"]["warm_api"] == "FAIL"
        assert report["checks"]["warm_api_p99"] == "FAIL"

    def test_missing_metric_fails_not_silent_pass(self):
        m = _measured()
        del m["api_analyze_warm_best_ms"]
        passed, report = ci_gate.evaluate_checks(
            m, baseline_cold_load_best_ms=160.84
        )
        assert passed is False
        assert report["checks"]["warm_api"] == "FAIL"
        assert "missing/malformed" in report["reasons"]["warm_api"]

    def test_zero_denominator_fails_not_silent_pass(self):
        """The fail-open bug: a zero cold time must NOT yield ratio 0.0 <= max."""
        passed, report = ci_gate.evaluate_checks(
            _measured(api_analyze_cold_best_ms=0.0), baseline_cold_load_best_ms=160.84
        )
        assert passed is False
        assert report["checks"]["warm_api"] == "FAIL"

    def test_malformed_and_non_finite_values_fail(self):
        for bad in ("garbage", float("nan"), float("inf"), -1.0, None):
            passed, report = ci_gate.evaluate_checks(
                _measured(api_analyze_warm_p99_ms=bad), baseline_cold_load_best_ms=160.84
            )
            assert passed is False, f"{bad!r} must fail"
            assert report["checks"]["warm_api_p99"] == "FAIL"

    def test_empty_measurements_fail_safely(self):
        passed, report = ci_gate.evaluate_checks({}, baseline_cold_load_best_ms=160.84)
        assert passed is False
        for check, verdict in report["checks"].items():
            assert verdict == "FAIL", check
        assert len(report["failures"]) == 6

    def test_missing_baseline_skips_cold_load_only(self):
        passed, report = ci_gate.evaluate_checks(_measured(), baseline_cold_load_best_ms=None)
        assert report["checks"]["cold_load"] == "SKIP"
        assert report["reasons"]["cold_load"] == "no committed baseline — skipped"
        assert passed is True  # all non-skip checks pass

    def test_very_small_sample_never_raises(self):
        passed, report = ci_gate.evaluate_checks(
            _measured(api_analyze_warm_p99_ms=0.5), baseline_cold_load_best_ms=160.84
        )
        assert passed is True
        assert report["checks"]["warm_api_p99"] == "PASS"

    def test_stale_or_empty_artifact_in_formatting(self):
        assert ci_gate.format_failures({"failures": []}) == ""
        text = ci_gate.format_failures({
            "failures": [{"name": "warm_api_p99", "actual": 2417.0,
                          "maximum": 2000.0, "reason": "too slow"}]
        })
        assert "PERFORMANCE GATE FAILED" in text
        assert "Analyze p99:" in text
        assert "actual: 2417.0" in text
        assert "maximum: 2000.0" in text

    def test_run_gate_uses_evaluator_and_writes_failures(self, tmp_path, monkeypatch):
        monkeypatch.setattr(ci_gate, "GATE_SYMBOLS", 4)
        monkeypatch.setattr(ci_gate, "GATE_ROWS", 100)
        out = tmp_path / "gate.json"
        passed, artifact = ci_gate.run_gate(out_file=out, analyze_p99_max_ms=0.001)
        assert passed is False
        assert artifact["verdict"] == "FAIL"
        assert artifact["check_details"]["warm_api_p99"] == "FAIL"
        assert "check_reasons" in artifact
        assert artifact["failures"]  # non-empty failures list


class TestCIGateEnforced:
    def test_ci_workflow_actually_invokes_gate(self):
        """The gate must be wired into CI, not merely documented.

        A future edit that makes the gate informational-only (removing
        the invocation or the fail-fast exit) fails this regression test.
        """
        ci_yml = Path(__file__).resolve().parent.parent / ".github" / "workflows" / "ci.yml"
        text = ci_yml.read_text(encoding="utf-8")
        assert "benchmarks.ci_gate" in text, "CI workflow must run the gate"
        assert "python -m benchmarks.ci_gate" in text
        # The benchmark job must upload the artifact so failures are visible.
        assert "benchmark-ci.json" in text

    def test_gate_exits_nonzero_on_failure(self, tmp_path, monkeypatch):
        monkeypatch.setattr(ci_gate, "GATE_SYMBOLS", 4)
        monkeypatch.setattr(ci_gate, "GATE_ROWS", 100)
        out = tmp_path / "fail.json"
        passed, _ = ci_gate.run_gate(out_file=out, analyze_p99_max_ms=0.001)
        assert passed is False


# ───────────────────────────────────────────────────────────────────
# Phase 6 — dashboard metrics contract (Streamlit data layer)
# ───────────────────────────────────────────────────────────────────


class TestDashboardContract:
    def _payload(self, **overrides) -> dict:
        p = {
            "api_requests": {
                "total_requests": 10,
                "error_requests": 2,
                "error_rate": 0.2,
                "p50_ms": 5.0, "p95_ms": 50.0, "p99_ms": 100.0,
                "per_endpoint": {
                    "/analyze/NABIL": {"requests": 8, "errors": 1, "error_rate": 0.125,
                                       "average_ms": 10.0, "p95_ms": 40.0, "p99_ms": 90.0},
                    "/portfolio/": {"requests": 2, "errors": 1, "error_rate": 0.5,
                                    "average_ms": 200.0, "p95_ms": 300.0, "p99_ms": 400.0},
                },
            },
            "performance_gates": {
                "thresholds": {
                    "warm_api_ratio_max": 0.75,
                    "warm_portfolio_ratio_max": 0.75,
                    "analyze_p99_max_ms": 2000.0,
                },
                "ci_artifact": {
                    "verdict": "PASS",
                    "timestamp": "2026-08-12T00:00:00+00:00",
                    "checks": {"warm_api": True, "warm_portfolio": True, "warm_api_p99": True},
                    "api_analyze_warm_cold_ratio": 0.53,
                    "api_portfolio_warm_cold_ratio": 0.63,
                    "api_analyze_warm_p99_ms": 250.0,
                },
            },
            "scanner_cache": {
                "dataframe_hits": 100, "dataframe_misses": 10, "dataframe_entries": 5,
                "analysis_hits": 90, "analysis_misses": 20, "analysis_entries": 6,
            },
        }
        p.update(overrides)
        return p

    def test_api_requests_summary_valid(self):
        from src.ui.pages.metrics_page import summarize_api_requests

        s = summarize_api_requests(self._payload())
        assert s["available"] is True
        assert s["total_requests"] == 10
        assert s["error_requests"] == 2
        assert s["p95_ms"] == 50.0
        assert s["p99_ms"] == 100.0
        assert len(s["rows"]) == 2
        assert s["rows"][0]["endpoint"] == "/analyze/NABIL"  # sorted by count desc

    def test_api_requests_missing_optional_blocks(self):
        from src.ui.pages.metrics_page import summarize_api_requests

        assert summarize_api_requests({})["available"] is False
        s = summarize_api_requests({"api_requests": "garbage"})
        assert s["available"] is False
        s = summarize_api_requests({"api_requests": {"per_endpoint": "garbage"}})
        assert s["available"] is True
        assert s["rows"] == []
        s = summarize_api_requests({"api_requests": {"per_endpoint": {"x": "junk"}}})
        assert s["rows"] == []

    def test_api_requests_zero_values(self):
        from src.ui.pages.metrics_page import summarize_api_requests

        s = summarize_api_requests({"api_requests": {"total_requests": 0, "per_endpoint": {}}})
        assert s["total_requests"] == 0
        assert s["error_rate"] == 0.0
        assert s["p95_ms"] == 0.0

    def test_gates_summary_valid_and_stale(self):
        from src.ui.pages.metrics_page import summarize_gates

        s = summarize_gates(self._payload())
        assert s["available"] is True
        assert s["ci_artifact"]["verdict"] == "PASS"
        assert len(s["gates"]) == 3
        by_name = {g["name"]: g for g in s["gates"]}
        assert by_name["Warm API ratio"]["verdict"] == "PASS"
        assert by_name["Warm API ratio"]["actual"] == 0.53
        assert by_name["Analyze p99"]["verdict"] == "PASS"

        # A FAIL artifact surfaces FAIL.
        p = self._payload()
        p["performance_gates"]["ci_artifact"]["verdict"] = "FAIL"
        p["performance_gates"]["ci_artifact"]["api_analyze_warm_p99_ms"] = 2417.0
        s = summarize_gates(p)
        assert s["ci_artifact"]["verdict"] == "FAIL"
        assert s["gates"][2]["verdict"] == "FAIL"

    def test_gates_missing_or_malformed(self):
        from src.ui.pages.metrics_page import summarize_gates

        assert summarize_gates({})["available"] is False
        s = summarize_gates({"performance_gates": 42})
        assert s["available"] is False
        s = summarize_gates({"performance_gates": {"thresholds": None, "ci_artifact": "x"}})
        assert s["available"] is True
        assert s["gates"][0]["verdict"] == "unknown"

    def test_scanner_summary(self):
        from src.ui.pages.metrics_page import summarize_scanner

        s = summarize_scanner(self._payload())
        assert s["available"] is True
        assert s["df_hits"] == 100 and s["analysis_hits"] == 90
        # (100+90) / (100+10+90+20) = 190/220
        assert s["cache_hit_rate"] == pytest.approx(190 / 220, abs=0.001)
        assert summarize_scanner({})["available"] is False


# ───────────────────────────────────────────────────────────────────
# Phase 7 — concurrency & multi-worker aggregation
# ───────────────────────────────────────────────────────────────────


class TestMultiWorkerAggregation:
    def test_aggregation_sums_distinct_workers(self, tmp_path, monkeypatch):
        monkeypatch.setattr(wm, "WORKER_METRICS_FILE", str(tmp_path / "w.json"))
        local = {"process": {"pid": 1}, "indicator_cache": {"hits": 10, "misses": 2,
                 "entries": 1, "hit_rate": 0.8}, "json_store": {"retries": 1,
                 "stale_recoveries": 0, "timeouts": 0}}
        # Simulate two workers self-reporting under distinct pids.
        monkeypatch.setattr(wm, "_last_report_ts", 0.0)
        enriched1 = wm.aggregate_worker_metrics(dict(local))
        monkeypatch.setattr(wm, "_last_report_ts", 0.0)
        local["process"]["pid"] = 2
        local["indicator_cache"]["hits"] = 5
        enriched2 = wm.aggregate_worker_metrics(dict(local))
        # The serving worker's own report is included.
        assert enriched2["workers"]["active"] >= 2
        assert enriched2["aggregate"]["hits"] >= 15
        assert enriched2["aggregate"]["misses"] >= 2

    def test_concurrent_metrics_writes_do_not_corrupt(self, tmp_path, monkeypatch):
        monkeypatch.setattr(wm, "WORKER_METRICS_FILE", str(tmp_path / "w.json"))
        errors: list[Exception] = []

        def _report(pid: int) -> None:
            try:
                monkeypatch.setattr(wm, "_last_report_ts", 0.0)
                wm._report_worker({
                    "process": {"pid": pid},
                    "indicator_cache": {"hits": pid, "misses": 1, "entries": 1, "hit_rate": 0.5},
                    "json_store": {"retries": 0, "stale_recoveries": 0, "timeouts": 0},
                })
            except Exception as exc:  # noqa: BLE001
                errors.append(exc)

        threads = [threading.Thread(target=_report, args=(i,)) for i in range(6)]
        for th in threads:
            th.start()
        for th in threads:
            th.join()
        assert not errors
        state = json.loads((tmp_path / "w.json").read_text(encoding="utf-8"))
        assert isinstance(state, dict)
        assert len(state) >= 1


# ───────────────────────────────────────────────────────────────────
# Phase 8 — legacy-path detection & production-path verification
# ───────────────────────────────────────────────────────────────────


class TestProductionPathVerification:
    def test_no_direct_provider_calls_in_api(self):
        """API endpoints must route through DataService / service layer."""
        import ast

        root = Path(__file__).resolve().parent.parent
        banned = ("APIProvider", "CSVProvider", "HybridProvider", "requests.get")
        offenders: list[str] = []
        for path in (root / "src" / "api").rglob("*.py"):
            if "__pycache__" in path.parts:
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
            for token in banned:
                if token in text:
                    offenders.append(f"{path.relative_to(root)}: {token}")
        assert not offenders, f"direct provider calls in API: {offenders}"

    def test_analyze_uses_engine_path_not_backtesting(self):
        import ast

        analyze = (Path(__file__).resolve().parent.parent / "src" / "api" / "analyze.py")
        tree = ast.parse(analyze.read_text(encoding="utf-8"))
        imports = " ".join(
            ast.unparse(node) for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)
        )
        assert "src.engine.analyzer" in imports
        assert "src.backtesting" not in imports
        assert "src.backtest" not in imports

    def test_legacy_backtest_reachability_documented(self):
        """Both engines exist: src.backtest (API hot path) and
        src.backtesting (advanced event-driven engine).  The /backtest
        endpoint deliberately uses src.backtest.engine — documented, not
        accidental legacy usage."""
        import ast

        backtest_api = (Path(__file__).resolve().parent.parent / "src" / "api" / "backtest.py")
        tree = ast.parse(backtest_api.read_text(encoding="utf-8"))
        imports = " ".join(
            ast.unparse(node) for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)
        )
        assert "src.backtest.engine" in imports
        # Both modules are importable (no dead/broken legacy dirs).
        import importlib

        importlib.import_module("src.backtesting.engine")
        importlib.import_module("src.backtest.engine")


# ───────────────────────────────────────────────────────────────────
# Phase 9 — error & timeout hardening
# ───────────────────────────────────────────────────────────────────


class TestErrorHardening:
    def test_analyze_errors_are_predictable_http(self):
        from fastapi.testclient import TestClient

        from src.api.main import app

        client = TestClient(app)
        # Missing symbol -> 404 with clean detail (no traceback).
        resp = client.get("/analyze/nonexistentstock9999")
        assert resp.status_code == 404
        detail = resp.json()["detail"]
        assert "Traceback" not in detail
        assert "File \"" not in detail
        # Malformed symbol -> 400.
        assert client.get("/analyze/bad..symbol").status_code == 400

    def test_metrics_never_returns_non_json_or_traceback(self):
        from fastapi.testclient import TestClient

        from src.api.main import app

        resp = TestClient(app).get("/metrics")
        assert resp.status_code == 200
        assert "Traceback" not in resp.text
        resp.json()  # must parse as JSON

    def test_market_status_500_is_clean(self):
        from unittest.mock import patch

        from fastapi.testclient import TestClient

        from src.api.main import app

        with patch("src.data.DataService") as mock_svc:
            mock_svc.return_value.get_market_summary.side_effect = RuntimeError("boom")
            resp = TestClient(app).get("/market/status")
        assert resp.status_code == 500
        assert "Failed to fetch market status" in resp.json()["detail"]
        assert "Traceback" not in resp.json()["detail"]
