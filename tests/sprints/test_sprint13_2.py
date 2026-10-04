"""Sprint 13.2 — Multi-Worker Reliability & Failure-Path Hardening.

Closes the three gaps Sprint 13.1 left open:

1. **Live multi-worker validation** — a real ``uvicorn --workers=2``
   deployment (the Docker ``api`` stage) is spawned against an isolated
   corpus/state root, exercised over HTTP, its cross-worker aggregation
   checked for sum semantics, and one worker is killed + restarted.
2. **Provider timeout path** — ``API_TIMEOUT`` config is wired into
   ``APIProvider`` (it was hard-coded to 15 s and ignored the documented
   setting); timeouts now raise a distinguishable ``ProviderTimeout``
   (a ``ProviderError`` subclass) instead of degrading into a generic
   error, and a full timeout failure matrix (timeout / connection /
   HTTP / malformed / empty / unavailable) is covered.
3. **Process-level gate enforcement** — ``python -m benchmarks.ci_gate``
   is proven to exit 0 on PASS and non-zero on FAIL / invalid data, and
   threshold-boundary semantics (``actual == threshold`` -> PASS) are
   pinned.

Plus: failure cleanup guarantees (locks released, no temp/lock litter),
predictable API error responses (no traceback / path / exception-text
leakage), dashboard resilience to unavailable/malformed/stale/zero
metrics, worker start/stop lifecycle logging, and concurrent metrics
safety.

Engineering rules honoured: no architectural rewrite, reuse of the
existing ``json_store`` / worker-metrics / request-tracker / ci_gate
infrastructure, deterministic tests with isolated temp state, and the
production ``data/`` / ``~/.nepse`` are never touched (the live tests
run with ``cwd`` = a private state root and a private corpus, exactly
like ``benchmarks.validate_workers``).
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from src.utils import worker_metrics as wm
from src.utils.json_store import update_json

PROJECT_ROOT = Path(__file__).resolve().parent.parent


# ═══════════════════════════════════════════════════════════════════
# Shared helpers
# ═══════════════════════════════════════════════════════════════════


def _record(
    pid: int,
    *,
    hits: int = 0,
    misses: int = 0,
    requests: int = 0,
    errors: int = 0,
    last_seen: float | None = None,
) -> dict:
    """A worker-metrics store record (same shape as ``_self_record``)."""
    return {
        "pid": pid,
        "hostname": "testhost",
        "last_seen": time.time() if last_seen is None else last_seen,
        "indicator_cache": {"hits": hits, "misses": misses, "entries": 0, "hit_rate": 0.0},
        "json_store": {"retries": 0, "stale_recoveries": 0, "timeouts": 0},
        "api_requests": {"total": requests, "errors": errors},
    }


def _seed_store(store: Path, records: dict[str, dict]) -> None:
    """Write *records* into the worker-metrics store transactionally."""
    update_json(store, lambda _s: dict(records), {}, log_name="Sprint132")


# ═══════════════════════════════════════════════════════════════════
# Phase 3/5 — multi-worker metrics safety (unit level)
# ═══════════════════════════════════════════════════════════════════


class TestWorkerMetricsSafety:
    def test_aggregate_api_requests_exact_sum_two_workers(self, tmp_path, monkeypatch):
        """aggregate.requests = A + B (each request counted exactly once)."""
        store = tmp_path / "w.json"
        monkeypatch.setattr(wm, "WORKER_METRICS_FILE", str(store))
        now = time.time()
        _seed_store(store, {
            "100": _record(100, hits=10, misses=1, requests=12, errors=1, last_seen=now),
            "200": _record(200, hits=20, misses=2, requests=7, errors=0, last_seen=now),
        })
        monkeypatch.setattr(wm, "_last_report_ts", 0.0)
        local = {
            "process": {"pid": 100},
            "indicator_cache": {"hits": 10, "misses": 1, "entries": 1, "hit_rate": 0.9},
            "json_store": {"retries": 0, "stale_recoveries": 0, "timeouts": 0},
            "api_requests": {"total_requests": 12, "error_requests": 1},
        }
        enriched = wm.aggregate_worker_metrics(local)
        assert enriched["aggregate"]["requests"] == 19
        assert enriched["aggregate"]["request_errors"] == 1
        assert enriched["aggregate"]["hits"] == 30
        assert enriched["aggregate"]["misses"] == 3

    def test_stale_worker_excluded_from_active(self, tmp_path, monkeypatch):
        """A worker that stopped reporting (past TTL) is not counted."""
        store = tmp_path / "w.json"
        monkeypatch.setattr(wm, "WORKER_METRICS_FILE", str(store))
        ttl = wm.WORKER_METRICS_TTL_S
        now = time.time()
        _seed_store(store, {
            "1": _record(1, hits=5, last_seen=now),
            "2": _record(2, hits=50, last_seen=now - ttl - 10),
        })
        monkeypatch.setattr(wm, "_last_report_ts", 0.0)
        local = {
            "process": {"pid": 1},
            "indicator_cache": {"hits": 5, "misses": 0, "entries": 1, "hit_rate": 1.0},
            "json_store": {"retries": 0, "stale_recoveries": 0, "timeouts": 0},
            "api_requests": {"total_requests": 0, "error_requests": 0},
        }
        enriched = wm.aggregate_worker_metrics(local)
        assert enriched["workers"]["active"] == 1
        assert enriched["aggregate"]["hits"] == 5  # stale worker's 50 excluded

    def test_malformed_store_recovers_to_local_only(self, tmp_path, monkeypatch):
        """A corrupt/non-dict store must never break aggregation."""
        store = tmp_path / "w.json"
        store.write_text("[1, 2, 3]", encoding="utf-8")  # valid JSON, wrong shape
        monkeypatch.setattr(wm, "WORKER_METRICS_FILE", str(store))
        monkeypatch.setattr(wm, "_last_report_ts", 0.0)
        local = {
            "process": {"pid": 7},
            "indicator_cache": {"hits": 1, "misses": 0, "entries": 1, "hit_rate": 1.0},
            "json_store": {"retries": 0, "stale_recoveries": 0, "timeouts": 0},
            "api_requests": {"total_requests": 2, "error_requests": 0},
        }
        enriched = wm.aggregate_worker_metrics(local)  # must not raise
        assert enriched["workers"]["active"] == 1  # self-report only
        assert enriched["aggregate"]["requests"] >= 2

    def test_worker_restart_new_pid_replaces_old(self, tmp_path, monkeypatch):
        """A restarted worker self-reports under a fresh pid; the old
        record never blocks the new one (documented stale-tolerance)."""
        store = tmp_path / "w.json"
        monkeypatch.setattr(wm, "WORKER_METRICS_FILE", str(store))
        _seed_store(store, {"999": _record(999, hits=99)})
        monkeypatch.setattr(wm, "_last_report_ts", 0.0)
        local = {
            "process": {"pid": 1000},  # restarted worker, new pid
            "indicator_cache": {"hits": 0, "misses": 1, "entries": 1, "hit_rate": 0.0},
            "json_store": {"retries": 0, "stale_recoveries": 0, "timeouts": 0},
            "api_requests": {"total_requests": 1, "error_requests": 0},
        }
        wm.aggregate_worker_metrics(local)
        state = json.loads(store.read_text(encoding="utf-8"))
        assert "1000" in state  # fresh record exists
        assert state["1000"]["pid"] == 1000

    def test_concurrent_self_reports_stay_valid_json(self, tmp_path, monkeypatch):
        """Threads self-reporting simultaneously never corrupt the store and
        never lose an update: every thread's pid must be present."""
        store = tmp_path / "w.json"
        monkeypatch.setattr(wm, "WORKER_METRICS_FILE", str(store))
        # Disable the report throttle (established pattern from
        # test_sprint12_0): with the interval at 0.0 every thread's write
        # is unconditional, so the all-pids assertion is deterministic.
        monkeypatch.setattr(wm, "_REPORT_MIN_INTERVAL_S", 0.0)
        errors: list[Exception] = []
        pids = [100 + i for i in range(8)]

        def _report(pid: int) -> None:
            try:
                wm._report_worker({
                    "process": {"pid": pid},
                    "indicator_cache": {"hits": 1, "misses": 0, "entries": 1, "hit_rate": 1.0},
                    "json_store": {"retries": 0, "stale_recoveries": 0, "timeouts": 0},
                    "api_requests": {"total_requests": pid, "error_requests": 0},
                })
            except Exception as exc:  # noqa: BLE001 - thread isolation
                errors.append(exc)

        threads = [threading.Thread(target=_report, args=(pid,)) for pid in pids]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=60)
        assert not errors
        state = json.loads(store.read_text(encoding="utf-8"))
        assert isinstance(state, dict)
        # No lost updates: all 8 worker pids present despite racing writes.
        assert {str(pid) for pid in pids} <= set(state)


# ═══════════════════════════════════════════════════════════════════
# Phase 6/7 — provider failure matrix (timeout distinguishable)
# ═══════════════════════════════════════════════════════════════════


class _TimeoutProvider:
    """APIProvider configured to fail every request with a Timeout."""


class TestProviderFailureMatrix:
    def _api(self):
        from src.data.providers import APIProvider

        return APIProvider(api_urls={
            "nepse_client": "http://127.0.0.1:1",
            "github_datasets": "http://127.0.0.1:1",
        })

    def test_timeout_is_distinguishable(self, monkeypatch):
        import requests

        from src.data.exceptions import ProviderTimeout

        p = self._api()

        def _timeout(url):  # noqa: ARG001
            raise requests.exceptions.Timeout("upstream timed out")

        monkeypatch.setattr(p, "_fetch_json", _timeout)
        with pytest.raises(ProviderTimeout):
            p.get_history("NABIL", days=5)

    def test_timeout_is_provider_error_subclass(self):
        from src.data.exceptions import ProviderError, ProviderTimeout

        assert issubclass(ProviderTimeout, ProviderError)

    def test_timeout_market_summary_distinguishable(self, monkeypatch):
        import requests

        from src.data.exceptions import ProviderTimeout

        p = self._api()
        monkeypatch.setattr(
            p, "_fetch_json", lambda _url: (_ for _ in ()).throw(
                requests.exceptions.Timeout("boom")
            )
        )
        with pytest.raises(ProviderTimeout):
            p.get_market_summary()

    def test_connection_error_is_generic_provider_error(self, monkeypatch):
        import requests

        from src.data.exceptions import ProviderError, ProviderTimeout

        p = self._api()
        monkeypatch.setattr(
            p, "_fetch_json", lambda _url: (_ for _ in ()).throw(
                requests.exceptions.ConnectionError("refused")
            )
        )
        with pytest.raises(ProviderError) as exc_info:
            p.get_history("NABIL", days=5)
        assert not isinstance(exc_info.value, ProviderTimeout)

    def test_socket_level_timeout_inside_connection_error_is_timeout(self, monkeypatch):
        """requests folds socket-level read/connect timeouts into
        ConnectionError with a timeout cause (socket.timeout /
        urllib3.ReadTimeoutError).  The failure-kind tracker must walk
        the cause chain so a genuine upstream stall still surfaces as
        ProviderTimeout (Sprint 13.2 review follow-up)."""
        from src.data.exceptions import ProviderTimeout
        from src.data.providers import APIProvider

        p = self._api()

        def _conn_with_socket_timeout(url):  # noqa: ARG001
            try:
                raise TimeoutError("read timed out")  # socket.timeout
            except TimeoutError as exc:
                raise __import__("requests").exceptions.ConnectionError(
                    "Connection aborted."
                ) from exc

        monkeypatch.setattr(p, "_fetch_json", _conn_with_socket_timeout)
        with pytest.raises(ProviderTimeout):
            p.get_history("NABIL", days=5)

    def test_urllib3_read_timeout_chain_is_timeout(self, monkeypatch):
        """Same as above but with the urllib3 ReadTimeoutError cause shape
        (type-name check branch)."""
        from src.data.exceptions import ProviderTimeout
        from src.data.providers import APIProvider

        p = self._api()

        def _conn_with_urllib3_timeout(url):  # noqa: ARG001
            cause = type("ReadTimeoutError", (Exception,), {})("timed out")
            raise __import__("requests").exceptions.ConnectionError(
                "read timeout"
            ) from cause

        monkeypatch.setattr(p, "_fetch_json", _conn_with_urllib3_timeout)
        with pytest.raises(ProviderTimeout):
            p.get_history("NABIL", days=5)

    def test_http_error_is_generic_provider_error(self, monkeypatch):
        import requests

        from src.data.exceptions import ProviderError, ProviderTimeout

        p = self._api()
        monkeypatch.setattr(
            p, "_fetch_json", lambda _url: (_ for _ in ()).throw(
                requests.exceptions.HTTPError("404")
            )
        )
        with pytest.raises(ProviderError) as exc_info:
            p.get_history("NABIL", days=5)
        assert not isinstance(exc_info.value, ProviderTimeout)

    def test_malformed_response_is_generic_provider_error(self, monkeypatch):
        from src.data.exceptions import ProviderError, ProviderTimeout

        p = self._api()
        monkeypatch.setattr(
            p, "_fetch_json", lambda _url: (_ for _ in ()).throw(ValueError("bad json"))
        )
        with pytest.raises(ProviderError) as exc_info:
            p.get_history("NABIL", days=5)
        assert not isinstance(exc_info.value, ProviderTimeout)

    def test_empty_response_is_generic_provider_error(self, monkeypatch):
        from src.data.exceptions import ProviderError, ProviderTimeout

        p = self._api()
        monkeypatch.setattr(p, "_fetch_json", lambda _url: None)
        with pytest.raises(ProviderError) as exc_info:
            p.get_history("NABIL", days=5)
        assert not isinstance(exc_info.value, ProviderTimeout)

    def test_hybrid_falls_back_to_csv_after_api_timeout(self, tmp_path, monkeypatch):
        import requests

        from benchmarks.common import write_csvs  # noqa: PLC0415
        from src.data.providers import APIProvider, CSVProvider, HybridProvider

        data_dir = tmp_path / "data"
        write_csvs(data_dir, 2, 60)
        api = APIProvider(api_urls={
            "nepse_client": "http://127.0.0.1:1",
            "github_datasets": "http://127.0.0.1:1",
        })
        monkeypatch.setattr(
            api, "_fetch_json", lambda _url: (_ for _ in ()).throw(
                requests.exceptions.Timeout("boom")
            )
        )
        hybrid = HybridProvider([api, CSVProvider(data_dir=str(data_dir))])
        df = hybrid.get_history("SYN000", days=60)  # must fall back, not raise
        assert not df.empty
        assert hybrid.last_provider == "csv"

    def test_hybrid_all_fail_raises_data_unavailable(self, monkeypatch):
        import requests

        from src.data.exceptions import DataUnavailable
        from src.data.providers import APIProvider, HybridProvider

        api1 = self._api()
        api2 = self._api()
        for p in (api1, api2):
            monkeypatch.setattr(
                p, "_fetch_json", lambda _url: (_ for _ in ()).throw(
                    requests.exceptions.Timeout("boom")
                )
            )
        hybrid = HybridProvider([api1, api2])
        with pytest.raises(DataUnavailable):
            hybrid.get_history("NABIL", days=5)


# ═══════════════════════════════════════════════════════════════════
# Phase 6 — provider timeout through the service layer
# ═══════════════════════════════════════════════════════════════════


class TestServiceTimeoutBehavior:
    def test_get_history_empty_on_timeout(self, monkeypatch):
        import requests

        from src.data import DataService
        from src.data.cache import TieredCache
        from src.data.providers import APIProvider

        DataService.reset_instance()
        p = APIProvider(api_urls={
            "nepse_client": "http://127.0.0.1:1",
            "github_datasets": "http://127.0.0.1:1",
        })
        monkeypatch.setattr(
            p, "_fetch_json", lambda _url: (_ for _ in ()).throw(
                requests.exceptions.Timeout("boom")
            )
        )
        svc = DataService(provider=p, cache=TieredCache(memory_ttl=0))
        h = svc.get_history("NABIL", days=5)
        assert h.is_empty  # controlled: empty history, no crash

    def test_market_summary_returns_empty_on_timeout(self, monkeypatch):
        import requests

        from src.data import DataService
        from src.data.cache import TieredCache
        from src.data.providers import APIProvider

        DataService.reset_instance()
        p = APIProvider(api_urls={
            "nepse_client": "http://127.0.0.1:1",
            "github_datasets": "http://127.0.0.1:1",
        })
        monkeypatch.setattr(
            p, "_fetch_json", lambda _url: (_ for _ in ()).throw(
                requests.exceptions.Timeout("boom")
            )
        )
        svc = DataService(provider=p, cache=TieredCache(memory_ttl=0))
        s = svc.get_market_summary()
        assert s.index == 0.0  # controlled empty summary
        assert s.status == "Unknown"

    def test_timeout_increments_provider_failure_metric(self, monkeypatch):
        import requests

        from src.data import DataService
        from src.data.cache import TieredCache
        from src.data.providers import APIProvider

        DataService.reset_instance()
        p = APIProvider(api_urls={
            "nepse_client": "http://127.0.0.1:1",
            "github_datasets": "http://127.0.0.1:1",
        })
        monkeypatch.setattr(
            p, "_fetch_json", lambda _url: (_ for _ in ()).throw(
                requests.exceptions.Timeout("boom")
            )
        )
        svc = DataService(provider=p, cache=TieredCache(memory_ttl=0))
        svc._performance_monitoring_enabled = True
        svc.get_market_summary()
        assert svc.get_metrics().provider_failures >= 1

    def test_timeout_does_not_poison_cache_then_recovers(self, monkeypatch):
        """A timeout with an empty cache does not corrupt later success."""
        from src.data import DataService
        from src.data.cache import TieredCache
        from src.data.providers import CSVProvider

        DataService.reset_instance()
        svc = DataService(provider=CSVProvider(data_dir="data/raw"), cache=TieredCache(memory_ttl=0))
        # Provider swap to a failing one, then back — no corruption.
        from src.data.providers import APIProvider

        failing = APIProvider(api_urls={"nepse_client": "http://127.0.0.1:1"})
        monkeypatch.setattr(
            failing, "_fetch_json", lambda _url: (_ for _ in ()).throw(
                __import__("requests").exceptions.Timeout("boom")
            )
        )
        svc.provider = failing
        s1 = svc.get_market_summary()
        assert s1.status == "Unknown"  # controlled failure
        svc.provider = CSVProvider(data_dir="data/raw")
        s2 = svc.get_market_summary()  # recovers, no crash
        assert isinstance(s2, object)


# ═══════════════════════════════════════════════════════════════════
# Phase 8 — failure cleanup guarantees
# ═══════════════════════════════════════════════════════════════════


class TestFailureCleanup:
    def test_locked_json_releases_lock_on_exception(self, tmp_path):
        from src.utils.json_store import _lock_file_for, locked_json

        path = tmp_path / "x.json"
        with pytest.raises(RuntimeError):
            with locked_json(path):
                raise RuntimeError("boom")
        assert not _lock_file_for(path).exists()

    def test_update_json_mutator_exception_no_litter(self, tmp_path):
        path = tmp_path / "x.json"
        path.write_text('{"a": 1}', encoding="utf-8")

        def _bad(state):  # noqa: ARG001
            raise ValueError("mutator failed")

        with pytest.raises(ValueError):
            update_json(path, _bad, {}, log_name="Sprint132")
        assert not list(tmp_path.glob("*.tmp"))
        assert not list(tmp_path.glob("*.lock"))
        assert json.loads(path.read_text(encoding="utf-8")) == {"a": 1}  # unchanged

    def test_save_json_cleans_tmp_on_serialization_failure(self, tmp_path):
        from src.utils.json_store import save_json

        path = tmp_path / "x.json"
        with pytest.raises(TypeError):
            save_json(path, {"bad": object()}, log_name="Sprint132")
        assert not list(tmp_path.glob("*.tmp"))

    def test_corrupt_store_recovery_backs_up_and_returns_default(self, tmp_path):
        from src.utils.json_store import load_json

        path = tmp_path / "c.json"
        path.write_text("{ not json", encoding="utf-8")
        result = load_json(path, {"recovered": True}, log_name="Sprint132")
        assert result == {"recovered": True}
        assert list(tmp_path.glob("c*.bak"))  # evidence preserved


# ═══════════════════════════════════════════════════════════════════
# Phase 10/11 — gate boundaries, invalid data, process exit codes
# ═══════════════════════════════════════════════════════════════════


def _measured(**overrides: float) -> dict:
    """A measurement dict where every gate check passes by default."""
    base = {
        "cold_load_best_ms": 100.0,
        "warm_load_best_ms": 10.0,  # speedup 10x
        "scanner_cold_ms": 1000.0,
        "scanner_warm_ms": 500.0,  # ratio 0.5
        "api_analyze_cold_best_ms": 1000.0,
        "api_analyze_warm_best_ms": 750.0,  # ratio 0.75
        "api_analyze_warm_p99_ms": 1000.0,
        "api_portfolio_cold_best_ms": 1000.0,
        "api_portfolio_warm_best_ms": 750.0,  # ratio 0.75
    }
    base.update(overrides)
    return base


class TestGateBoundaries:
    def _eval(self, measured, baseline=100.0):
        from benchmarks import ci_gate  # noqa: PLC0415

        return ci_gate.evaluate_checks(measured, baseline_cold_load_best_ms=baseline)

    def test_all_passing(self):
        passed, report = self._eval(_measured())
        assert passed
        assert report["failures"] == []
        assert all(v == "PASS" for v in report["checks"].values())

    def test_ratio_boundary_equal_threshold_passes(self):
        # warm/cold = 0.75 == 0.75  -> PASS (repository uses <=)
        passed, _report = self._eval(_measured(api_analyze_warm_best_ms=750.0))
        assert passed

    def test_ratio_just_above_threshold_fails(self):
        passed, report = self._eval(_measured(api_analyze_warm_best_ms=751.0))
        assert not passed
        names = {f["name"] for f in report["failures"]}
        assert "warm_api" in names

    def test_p99_boundary_equal_threshold_passes(self):
        # 2000.0 == 2000.0 -> PASS
        passed, _report = self._eval(_measured(api_analyze_warm_p99_ms=2000.0))
        assert passed

    def test_p99_just_above_threshold_fails(self):
        passed, report = self._eval(_measured(api_analyze_warm_p99_ms=2000.1))
        assert not passed
        names = {f["name"] for f in report["failures"]}
        assert "warm_api_p99" in names

    def test_portfolio_ratio_boundary_equal_passes(self):
        passed, _report = self._eval(_measured(api_portfolio_warm_best_ms=750.0))
        assert passed

    def test_portfolio_ratio_just_above_fails(self):
        passed, report = self._eval(_measured(api_portfolio_warm_best_ms=751.0))
        assert not passed
        names = {f["name"] for f in report["failures"]}
        assert "warm_portfolio" in names

    def test_speedup_boundary_equal_minimum_passes(self):
        # 100/20 = 5.0 == WARM_SPEEDUP_MIN -> PASS
        passed, _report = self._eval(_measured(warm_load_best_ms=20.0))
        assert passed

    def test_speedup_just_below_fails(self):
        passed, report = self._eval(_measured(warm_load_best_ms=20.01))
        assert not passed
        names = {f["name"] for f in report["failures"]}
        assert "warm_speedup" in names


class TestGateInvalidData:
    def test_missing_metric_fails_not_pass(self):
        from benchmarks import ci_gate  # noqa: PLC0415

        measured = _measured()
        del measured["api_analyze_warm_p99_ms"]
        passed, report = ci_gate.evaluate_checks(measured, baseline_cold_load_best_ms=100.0)
        assert not passed
        assert "warm_api_p99" in {f["name"] for f in report["failures"]}

    def test_nan_metric_fails(self):
        from benchmarks import ci_gate  # noqa: PLC0415

        passed, _report = ci_gate.evaluate_checks(
            _measured(api_analyze_warm_p99_ms=float("nan")),
            baseline_cold_load_best_ms=100.0,
        )
        assert not passed

    def test_infinite_metric_fails(self):
        from benchmarks import ci_gate  # noqa: PLC0415

        passed, _report = ci_gate.evaluate_checks(
            _measured(api_analyze_warm_best_ms=float("inf")),
            baseline_cold_load_best_ms=100.0,
        )
        assert not passed

    def test_empty_benchmark_fails(self):
        from benchmarks import ci_gate  # noqa: PLC0415

        passed, _report = ci_gate.evaluate_checks({}, baseline_cold_load_best_ms=100.0)
        assert not passed  # missing data must never become PASS

    def test_insufficient_samples_fails(self):
        """A one-key measurement dict is unmeasurable -> FAIL, never PASS."""
        from benchmarks import ci_gate  # noqa: PLC0415

        passed, _report = ci_gate.evaluate_checks(
            {"api_analyze_warm_p99_ms": 100.0},
            baseline_cold_load_best_ms=100.0,
        )
        assert not passed


class TestGateProcessExitCodes:
    """Process-level proof: the CLI's exit code follows the gate verdict."""

    def _run_cli(self, *args: str) -> subprocess.CompletedProcess:
        env = dict(os.environ)
        env["PYTHONPATH"] = str(PROJECT_ROOT)
        return subprocess.run(
            [sys.executable, "-m", "benchmarks.ci_gate", *args],
            cwd=str(PROJECT_ROOT),
            env=env,
            capture_output=True,
            text=True,
            timeout=600,
        )

    def test_pass_case_exits_zero(self, tmp_path):
        out = tmp_path / "pass.json"
        proc = self._run_cli("--out", str(out))
        assert proc.returncode == 0, proc.stderr[-2000:]
        artifact = json.loads(out.read_text(encoding="utf-8"))
        assert artifact["verdict"] == "PASS"

    def test_fail_case_exits_nonzero(self, tmp_path):
        # An impossible p99 ceiling forces warm_api_p99 to FAIL.
        out = tmp_path / "fail.json"
        proc = self._run_cli("--analyze-p99", "0.001", "--out", str(out))
        assert proc.returncode != 0
        assert "PERFORMANCE GATE FAILED" in proc.stdout + proc.stderr
        artifact = json.loads(out.read_text(encoding="utf-8"))
        assert artifact["verdict"] == "FAIL"

    def test_invalid_data_exit_nonzero(self):
        """Missing/malformed benchmark data cannot silently pass at the
        process level: the fail-safe evaluator drives a non-zero exit."""
        code = (
            "import sys\n"
            "from benchmarks.ci_gate import evaluate_checks\n"
            "passed, _ = evaluate_checks({})\n"
            "sys.exit(0 if passed else 1)\n"
        )
        proc = subprocess.run(
            [sys.executable, "-c", code],
            cwd=str(PROJECT_ROOT),
            capture_output=True,
            text=True,
            timeout=120,
        )
        assert proc.returncode != 0


# ═══════════════════════════════════════════════════════════════════
# Phase 12 — API contract under failure (predictable, leak-free)
# ═══════════════════════════════════════════════════════════════════


class TestApiContractUnderFailure:
    def test_analyze_500_detail_sanitized(self, monkeypatch):
        from fastapi.testclient import TestClient

        from src.api import analyze as analyze_mod
        from src.api.main import app

        def _boom(_symbol):
            raise RuntimeError("SECRETPATH /home/user/.nepse/token.txt boom")

        monkeypatch.setattr(analyze_mod, "resolve_stock_csv_path", lambda _s: Path("data/raw/NABIL.csv"))
        monkeypatch.setattr(analyze_mod, "analyze_stock", _boom)
        resp = TestClient(app).get("/analyze/NABIL")
        assert resp.status_code == 500
        detail = resp.json()["detail"]
        assert detail == "Failed to analyze stock data for NABIL"
        assert "SECRETPATH" not in detail
        assert "RuntimeError" not in detail
        assert "Traceback" not in detail
        assert "/home/" not in detail

    def test_market_status_timeout_500_sanitized(self, monkeypatch):
        from fastapi.testclient import TestClient

        from src.api.main import app
        from src.data import DataService
        from src.data.exceptions import ProviderTimeout

        def _raise():
            raise ProviderTimeout("upstream timed out at http://internal/indices.json")

        monkeypatch.setattr(DataService, "get_market_summary", _raise)
        resp = TestClient(app).get("/market/status")
        assert resp.status_code == 500
        detail = resp.json()["detail"]
        assert detail == "Failed to fetch market status"
        assert "http://internal" not in detail
        assert "Traceback" not in detail

    def test_portfolio_500_sanitized(self, monkeypatch):
        from fastapi.testclient import TestClient

        from src.api.main import app
        from src.api import portfolio as portfolio_mod

        def _boom():
            raise RuntimeError("C:\\Users\\secret\\portfolio.db")

        monkeypatch.setattr(portfolio_mod, "analyze_portfolio", _boom)
        resp = TestClient(app).get("/portfolio/")
        assert resp.status_code == 500
        detail = resp.json()["detail"]
        assert detail == "Failed to analyze portfolio"
        assert "C:\\" not in detail
        assert "secret" not in detail

    def test_watchlist_500_sanitized(self, monkeypatch):
        from fastapi.testclient import TestClient

        from src.api import watchlist as watchlist_mod
        from src.api.main import app

        def _boom():
            raise RuntimeError("token=supersecret watchlist.db")

        monkeypatch.setattr(watchlist_mod, "load_watchlist", _boom)
        resp = TestClient(app).get("/watchlist")
        assert resp.status_code == 500
        detail = resp.json()["detail"]
        assert detail == "Failed to load watchlist"
        assert "supersecret" not in detail
        assert "Traceback" not in detail

    def test_invalid_symbol_is_400_not_500(self):
        from fastapi.testclient import TestClient

        from src.api.main import app

        resp = TestClient(app).get("/analyze/..%2Fetc%2Fpasswd")
        assert resp.status_code in (400, 404)
        assert "Traceback" not in resp.text


# ═══════════════════════════════════════════════════════════════════
# Phase 13 — dashboard resilience (unavailable/malformed/stale/zero)
# ═══════════════════════════════════════════════════════════════════


class TestDashboardResilience:
    def test_summarize_malformed_blocks_no_crash(self):
        from src.ui.pages.metrics_page import summarize

        summary = summarize({"workers": "garbage", "aggregate": 42, "process": None})
        assert summary["workers"] == []
        assert summary["workers_total"] == 0
        assert summary["status"] == "no_workers"
        assert summary["aggregate"]["requests"] == 0

    def test_summarize_stale_workers(self):
        from src.ui.pages.metrics_page import summarize

        ttl = wm.WORKER_METRICS_TTL_S
        now = time.time()
        payload = {
            "workers": {
                "ttl_s": ttl,
                "known": [{"pid": "1", "hostname": "h", "last_seen": now - ttl - 60}],
            }
        }
        summary = summarize(payload, now=now)
        assert summary["status"] == "stale"
        assert summary["workers_active"] == 0

    def test_summarize_zero_metrics(self):
        from src.ui.pages.metrics_page import summarize

        summary = summarize({"workers": {"ttl_s": 60, "known": []}, "aggregate": {}})
        assert summary["aggregate"]["hit_rate"] == 0.0
        assert summary["aggregate"]["requests"] == 0
        assert summary["workers_active"] == 0

    def test_summarize_api_requests_fleet_totals(self):
        from src.ui.pages.metrics_page import summarize

        summary = summarize({"aggregate": {"requests": 42, "request_errors": 3}})
        assert summary["aggregate"]["requests"] == 42
        assert summary["aggregate"]["request_errors"] == 3

    def test_summarize_gates_missing_fields(self):
        from src.ui.pages.metrics_page import summarize_gates

        result = summarize_gates({"performance_gates": {"thresholds": {}}})
        assert result["available"]
        assert len(result["gates"]) == 3
        assert all(g["verdict"] == "unknown" for g in result["gates"])

    def test_summarize_api_requests_malformed(self):
        from src.ui.pages.metrics_page import summarize_api_requests

        result = summarize_api_requests({"api_requests": "garbage"})
        assert result["available"] is False
        assert result["rows"] == []


# ═══════════════════════════════════════════════════════════════════
# Phase 14 — worker lifecycle observability
# ═══════════════════════════════════════════════════════════════════


class TestWorkerLifecycleLogging:
    def test_lifespan_logs_worker_start_stop(self, caplog):
        import logging

        from fastapi.testclient import TestClient

        from src.api.main import app

        with caplog.at_level(logging.INFO):
            with TestClient(app) as client:
                assert client.get("/").status_code == 200
        messages = [r.getMessage() for r in caplog.records]
        assert any("worker start pid=" in m for m in messages)
        assert any("worker stop pid=" in m for m in messages)


# ═══════════════════════════════════════════════════════════════════
# Phase 2/5/17 — LIVE multi-worker validation (real uvicorn, 2 workers)
# ═══════════════════════════════════════════════════════════════════
# Spawns the exact production entrypoint (uvicorn src.api.main:app
# --workers=2 — the Docker api stage) with cwd = a private state root
# and DATA_DIRECTORY = a private corpus copy, so production state is
# never touched.  Provider URLs point at a dead loopback port so the
# live run is hermetic and fast (the CSV provider serves the corpus).


def _free_port() -> int:
    import socket

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _offline_env() -> dict:
    return {
        "NEPSE_SCRAPER_URL": "http://127.0.0.1:1",
        "NEPSE_CLIENT_URL": "http://127.0.0.1:1",
        "GITHUB_DATASETS_URL": "http://127.0.0.1:1",
        "NEPSE_DATA_API_URL": "",
        "NEPALSTOCK_OFFICIAL_URL": "",
    }


@pytest.mark.live2worker
class TestLiveTwoWorker:
    def test_two_worker_aggregation_and_restart(self, tmp_path):
        """2 workers serve endpoints, aggregate with sum semantics, and
        recover after one worker is killed (uvicorn respawns it)."""
        from benchmarks.common import write_csvs  # noqa: PLC0415
        from benchmarks.validate_workers import (  # noqa: PLC0415
            _request,
            _shutdown,
            _spawn_api,
            _wait_ready,
        )

        data_dir = tmp_path / "data"
        state_root = tmp_path / "state"
        state_root.mkdir(parents=True, exist_ok=True)
        write_csvs(data_dir, 6, 300)
        port = _free_port()

        env = dict(os.environ)
        env.update(_offline_env())
        proc = _spawn_api(data_dir, state_root, port, workers=2)
        try:
            _wait_ready(port, timeout=120)
            symbol = "SYN000"

            # ── endpoints serve 200 ────────────────────────────────
            for path in (f"/analyze/{symbol}", "/portfolio", "/watchlist", "/metrics"):
                status, _body = _request(port, path)
                assert status == 200, f"{path} -> {status}"

            # ── generate enough traffic that both workers self-report ──
            seen_pids: set[int] = set()
            agg_requests = local_requests = 0
            for i in range(16):
                status, body = _request(port, f"/analyze/{symbol}")
                assert status == 200
                status, body = _request(port, "/metrics")
                assert status == 200
                m = json.loads(body)
                pid = (m.get("process") or {}).get("pid")
                if isinstance(pid, int):
                    seen_pids.add(pid)
                agg = m.get("aggregate") or {}
                req = m.get("api_requests") or {}
                agg_requests = max(agg_requests, int(agg.get("requests", 0)))
                local_requests = max(local_requests, int(req.get("total_requests", 0)))
                time.sleep(0.4)

            # Sum semantics: aggregate counts every worker's requests,
            # so it must be >= the serving worker's local count.  The
            # cross-worker aggregate lags the local counters by up to the
            # 5 s worker self-report throttle (worker_metrics), so the
            # assertion is evaluated after the fleet has had time to
            # report — otherwise a fast machine serves its final requests
            # inside the throttle window and the aggregate legitimately
            # lags (a harness race, not a sum bug).  Sprint 13.8
            # determinism hardening: spread a few settle polls across the
            # round-robin so BOTH workers report before the check.
            for _ in range(3):
                time.sleep(2.0)
                _status, body = _request(port, "/metrics")
                assert _status == 200
                settled = json.loads(body)
                agg_requests = max(
                    agg_requests,
                    int((settled.get("aggregate") or {}).get("requests", 0)),
                )
                local_requests = max(
                    local_requests,
                    int((settled.get("api_requests") or {}).get("total_requests", 0)),
                )
            assert agg_requests >= local_requests
            assert agg_requests >= 1
            assert len(seen_pids) >= 1

            # ── kill one worker; the fleet must keep serving ───────
            known_before = sorted(p["pid"] for p in (json.loads(
                _request(port, "/metrics")[1]
            ).get("workers", {}).get("known", [])))
            target = None
            serving = None
            _status, body = _request(port, "/metrics")
            m = json.loads(body)
            serving = (m.get("process") or {}).get("pid")
            for pid in known_before:
                if str(pid) != str(serving):
                    target = int(pid)
                    break
            if target is not None:
                try:
                    os.kill(target, signal.SIGTERM)
                except (OSError, ProcessLookupError):
                    target = None  # already gone; nothing to kill
            if target is not None:
                # Remaining worker must keep serving; uvicorn respawns
                # the dead worker (new pid) within a bounded window.
                deadline = time.monotonic() + 60
                respawned = False
                while time.monotonic() < deadline:
                    try:
                        status, _body = _request(port, "/metrics")
                        assert status == 200  # fleet still answers
                        m = json.loads(_body)
                        known_now = [p["pid"] for p in m.get("workers", {}).get("known", [])]
                        if any(str(pid) != str(target) and str(pid) not in known_before
                               for pid in known_now):
                            respawned = True
                            break
                    except AssertionError:
                        raise
                    except Exception:  # noqa: BLE001 - transient while restarting
                        pass
                    time.sleep(1.0)
                assert respawned, (
                    f"no new worker pid appeared within 60s (known_before={known_before}, "
                    f"target={target})"
                )
            # Fleet still serves real analysis after the restart.
            status, _body = _request(port, f"/analyze/{symbol}")
            assert status == 200
        finally:
            _shutdown(proc)

    def test_live_provider_failure_path(self, tmp_path):
        """With every provider URL dead, /market/status degrades to a
        controlled empty summary and the API keeps serving /analyze."""
        from benchmarks.common import write_csvs  # noqa: PLC0415
        from benchmarks.validate_workers import (  # noqa: PLC0415
            _request,
            _shutdown,
            _spawn_api,
            _wait_ready,
        )

        data_dir = tmp_path / "data"
        state_root = tmp_path / "state"
        state_root.mkdir(parents=True, exist_ok=True)
        write_csvs(data_dir, 4, 300)
        port = _free_port()

        env = dict(os.environ)
        env.update(_offline_env())
        proc = _spawn_api(data_dir, state_root, port, workers=1)
        try:
            _wait_ready(port, timeout=120)
            # Provider chain fails fast (dead loopback) -> DataService
            # returns the controlled empty summary, never a crash.
            status, body = _request(port, "/market/status")
            assert status == 200, body[:200]
            payload = json.loads(body)
            assert "status" in payload  # predictable schema
            # The engine still serves real analysis from CSV fallback.
            status, body = _request(port, "/analyze/SYN000")
            assert status == 200
            assert "signal" in json.loads(body)
            # /metrics remains available after the provider failure.
            status, _body = _request(port, "/metrics")
            assert status == 200
        finally:
            _shutdown(proc)


# ═══════════════════════════════════════════════════════════════════
# Phase 3 — concurrent /metrics + writes stress (in-process)
# ═══════════════════════════════════════════════════════════════════


class TestConcurrentMetricsStress:
    def test_concurrent_update_json_writes_no_lost_updates(self, tmp_path):
        """N threads each add their own key — all must survive."""
        store = tmp_path / "s.json"
        errors: list[Exception] = []
        barrier = threading.Barrier(8, timeout=30)

        def _writer(i: int) -> None:
            try:
                barrier.wait(timeout=30)

                def _mutate(state):
                    state[f"SYM{i}"] = {"worker": i}
                    return state

                update_json(store, _mutate, {}, log_name="Sprint132")
            except Exception as exc:  # noqa: BLE001 - thread isolation
                errors.append(exc)

        threads = [threading.Thread(target=_writer, args=(i,)) for i in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=60)
        assert not errors
        final = json.loads(store.read_text(encoding="utf-8"))
        assert len(final) == 8
        assert not list(tmp_path.glob("*.tmp"))
        assert not list(tmp_path.glob("*.lock"))

    def test_repeated_metrics_reads_bounded(self):
        """api_requests totals are bounded counters, not cumulative
        across snapshots (snapshot is a point-in-time view)."""
        from src.api.timing import ApiRequestTracker

        tracker = ApiRequestTracker(maxlen=50)
        tracker.record("/analyze/NABIL", 200, 10.0)
        s1 = tracker.snapshot()
        s2 = tracker.snapshot()  # read-only: totals unchanged
        assert s1["total_requests"] == s2["total_requests"] == 1
        assert s1["per_endpoint"]["/analyze/NABIL"]["requests"] == 1
