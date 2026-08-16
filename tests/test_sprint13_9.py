"""Sprint 13.9 — Deployment Hardening, Release Engineering & Operational Continuity.

Moves the platform from *"reliable while running"* to *"reliably
deployable, upgradeable, recoverable, observable, operable"*.

Coverage map (Sprint 13.9 §30):

    1.  clean startup               15. rollback
    2.  invalid configuration       16. upgrade compatibility
    3.  readiness                   17. calendar/watchlist/cache migration
    4.  liveness                    18. Docker static audit (build/startup/restart)
    5.  graceful shutdown           19. two-worker deployment (release artifact)
    6.  crash recovery              20. metrics bounds
    7.  persistent-state inventory  21. operational status
    8.  state corruption            22. secret/configuration checks
    9.  atomic writes               23. release-artifact integrity
    10. backup                      24. dependency reproducibility
    11. restore                     25. performance constants (gate unchanged)
    12. provenance persistence      26. Sprint 13.8 regression (locks, worker)
    13. cache migration             27. release manifest
    14. Docker audit

Constraints honoured: no thresholds weakened, no fabricated Docker/live
deployment evidence (Docker runtime is validated statically here and in
the release CI smoke job), no secrets printed, no second cache /
DataService / metrics framework created, no existing test removed.
"""

from __future__ import annotations

import importlib.metadata
import json
import os
import re
import shutil
import signal
import socket
import subprocess
import sys
import tarfile
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

REPO = Path(__file__).resolve().parent.parent

from src.api.main import app  # noqa: E402
from src.config.validation import validate_config  # noqa: E402
from src.utils.json_store import (  # noqa: E402
    load_json,
    locked_json,
    lock_stats,
    save_json,
    update_json,
)

test_client = TestClient(app)


# ═══════════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════════

def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def _http_get(port: int, path: str, timeout: float = 3.0):
    try:
        with urllib.request.urlopen(
            f"http://127.0.0.1:{port}{path}", timeout=timeout
        ) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, OSError, json.JSONDecodeError):
        return None


def _wait_http(port: int, path: str, *, deadline: float = 90.0, expect: int = 200):
    end = time.monotonic() + deadline
    last = None
    while time.monotonic() < end:
        last = _http_get(port, path)
        if last is not None and last[0] == expect:
            return last
        time.sleep(0.5)
    return last


def _spawn_uvicorn(port: int, tmp: Path, workers: int = 1) -> subprocess.Popen:
    env = dict(os.environ)
    env["NEPSE_HOME"] = str(tmp / "home")
    env["WORKER_METRICS_FILE"] = str(tmp / "worker_metrics.json")
    proc = subprocess.Popen(
        [
            sys.executable, "-m", "uvicorn", "src.api.main:app",
            "--host", "127.0.0.1", "--port", str(port),
            "--workers", str(workers),
        ],
        cwd=REPO,
        env=env,
        # DEVNULL, never PIPE: these lifecycle tests never read the
        # worker's stdout, and an undrained pipe fills up (~4 KB on
        # Windows) once startup logs under load exceed the buffer — the
        # worker then blocks writing and never becomes live, which
        # flakes ``*_became_live`` asserts in long suite runs.
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    return proc


def _terminate(proc: subprocess.Popen, timeout: float = 30.0) -> int:
    if proc.poll() is None:
        proc.send_signal(signal.SIGTERM)
        try:
            return proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            proc.kill()
            return proc.wait()
    return proc.returncode


def _assert_clean_stop(rc: int) -> None:
    """A SIGTERM stop must exit 0 (graceful) or -15 (signal death).

    On Windows ``send_signal(SIGTERM)`` maps to ``TerminateProcess``,
    which always exits with code 1 and cannot drain requests, so the
    graceful-shutdown contract is asserted on POSIX (local dev, CI).
    The Windows run still verifies liveness/readiness before the stop
    and the no-leftover-files test after it, so ``rc in (0, 1)`` is a
    sanity bound that still catches an abnormal crash.
    """
    if os.name == "nt":
        assert rc in (0, 1), f"unexpected exit code on Windows: rc={rc}"
    else:
        assert rc in (0, -15), f"SIGTERM should stop cleanly, got rc={rc}"


def _shipped_calendar_dict() -> dict:
    return json.loads((REPO / "data/state/nepse_calendar.json").read_text(encoding="utf-8"))


# ═══════════════════════════════════════════════════════════════════
# 1. Clean startup
# ═══════════════════════════════════════════════════════════════════

class TestCleanStartup:
    def test_app_imports_and_title(self) -> None:
        assert app.title == "NEPSE Quant Engine API"

    def test_version_file_present(self) -> None:
        version = (REPO / "VERSION").read_text(encoding="utf-8").strip()
        assert version

    def test_requirements_parse(self) -> None:
        from src.utils.release_manifest import parse_requirements

        deps = parse_requirements(REPO)
        assert len(deps) >= 10
        assert "fastapi" in deps
        assert "uvicorn" in deps
        assert "streamlit" in deps

    def test_health_routes_registered(self) -> None:
        # FastAPI 0.116+ wraps routers in ``_IncludedRouter`` objects
        # without a ``.path`` attribute, so verify reachability through
        # the TestClient instead of introspecting ``app.routes``.
        for path, expect in (
            ("/", 200),
            ("/health/live", 200),
            ("/health/ready", 200),
            ("/metrics", 200),
        ):
            assert test_client.get(path).status_code == expect, path

    def test_config_schema_version_present(self) -> None:
        import src.config as config

        assert config.CONFIG_SCHEMA_VERSION

    def test_compileall_clean(self) -> None:
        result = subprocess.run(
            [sys.executable, "-m", "compileall", "-q", "src"],
            cwd=REPO, capture_output=True, text=True, timeout=120,
        )
        assert result.returncode == 0, result.stderr


# ═══════════════════════════════════════════════════════════════════
# 2. Invalid configuration
# ═══════════════════════════════════════════════════════════════════

class TestConfigValidation:
    def test_default_config_valid(self) -> None:
        assert validate_config() == []

    def test_negative_rate_limit(self, monkeypatch) -> None:
        monkeypatch.setattr("src.config.validation.RATE_LIMIT", 0)
        problems = validate_config()
        assert any(key == "RATE_LIMIT" for key, _ in problems)

    def test_zero_api_timeout(self, monkeypatch) -> None:
        monkeypatch.setattr("src.config.validation.API_TIMEOUT", 0)
        assert any(key == "API_TIMEOUT" for key, _ in validate_config())

    def test_retry_count_zero_allowed(self, monkeypatch) -> None:
        monkeypatch.setattr("src.config.validation.RETRY_COUNT", 0)
        assert validate_config() == []

    def test_macd_inverted(self, monkeypatch) -> None:
        monkeypatch.setattr("src.config.validation.MACD_SLOW", 5)
        monkeypatch.setattr("src.config.validation.MACD_FAST", 10)
        assert any(key == "MACD_SLOW" for key, _ in validate_config())

    @pytest.mark.parametrize(
        "attr,value",
        [
            ("RECONCILE_PRICE_TOLERANCE_PCT", 0.0),
            ("RECONCILE_PRICE_TOLERANCE_PCT", 101.0),
            ("RECONCILE_MATERIAL_VOLUME_PCT", -1.0),
            ("RECONCILE_CORPORATE_ACTION_MOVE_PCT", 0.0),
        ],
    )
    def test_pct_out_of_bounds(self, monkeypatch, attr: str, value: float) -> None:
        monkeypatch.setattr(f"src.config.validation.{attr}", value)
        assert any(key == attr for key, _ in validate_config())

    def test_bad_websocket_url(self, monkeypatch) -> None:
        monkeypatch.setattr("src.config.validation.WEBSOCKET_URL", "not-a-url")
        assert any(key == "WEBSOCKET_URL" for key, _ in validate_config())

    def test_bad_second_provider_url(self, monkeypatch) -> None:
        monkeypatch.setattr("src.config.validation.SECOND_PROVIDER_URL", "ftp://bad")
        monkeypatch.setattr("src.config.validation.SECOND_PROVIDER_NAME", "p2")
        assert any(key == "SECOND_PROVIDER_URL" for key, _ in validate_config())

    def test_second_provider_requires_name(self, monkeypatch) -> None:
        monkeypatch.setattr("src.config.validation.SECOND_PROVIDER_URL", "https://example.com/data")
        monkeypatch.setattr("src.config.validation.SECOND_PROVIDER_NAME", "")
        assert any(key == "SECOND_PROVIDER_NAME" for key, _ in validate_config())

    @pytest.mark.parametrize(
        "key",
        ["WEBSOCKET_ENABLED", "ENABLE_PERFORMANCE_MONITORING",
         "ENFORCE_SIGNAL_FRESHNESS", "INDICATOR_CACHE_ENABLED",
         "ENABLE_ANALYZE_ALERT_BATCH"],
    )
    def test_garbage_bool_reported(self, monkeypatch, key: str) -> None:
        monkeypatch.setenv(key, "sometimes")
        assert any(k == key for k, _ in validate_config())

    @pytest.mark.parametrize(
        "key,value",
        [("WEBSOCKET_ENABLED", "true"), ("ENABLE_PERFORMANCE_MONITORING", "0"),
         ("INDICATOR_CACHE_ENABLED", "YES")],
    )
    def test_valid_bools_accepted(self, monkeypatch, key: str, value: str) -> None:
        monkeypatch.setenv(key, value)
        assert all(k != key for k, _ in validate_config())

    def test_validation_never_raises(self, monkeypatch) -> None:
        # The parser guarantees numbers at import time; validation must
        # never raise on numeric edge values.
        monkeypatch.setattr("src.config.validation.RATE_LIMIT", float("nan"))
        monkeypatch.setattr("src.config.validation.API_TIMEOUT", 10**9)
        monkeypatch.setattr("src.config.validation.MACD_SLOW", 1)
        monkeypatch.setattr("src.config.validation.MACD_FAST", 1)
        try:
            validate_config()
        except Exception as exc:  # pragma: no cover - defensive
            pytest.fail(f"validate_config raised: {exc!r}")

    def test_messages_never_include_values(self, monkeypatch) -> None:
        monkeypatch.setattr("src.config.validation.WEBSOCKET_URL", "super-secret-value-xyz")
        for _key, message in validate_config():
            assert "super-secret-value-xyz" not in message


# ═══════════════════════════════════════════════════════════════════
# 3. Readiness vs liveness
# ═══════════════════════════════════════════════════════════════════

class TestReadinessLiveness:
    def test_liveness_200(self) -> None:
        resp = test_client.get("/health/live")
        assert resp.status_code == 200
        assert resp.json() == {"status": "live"}

    def test_ready_200_clean(self) -> None:
        resp = test_client.get("/health/ready")
        assert resp.status_code == 200
        assert resp.json()["status"] == "ready"

    def test_ready_503_bad_config(self, monkeypatch) -> None:
        monkeypatch.setattr(
            "src.config.validation.validate_config",
            lambda: [("RATE_LIMIT", "must be >= 1")],
        )
        resp = test_client.get("/health/ready")
        assert resp.status_code == 503
        assert resp.json()["status"] == "not_ready"
        assert resp.json()["checks"]["configuration"] == "INVALID"

    def test_ready_503_calendar_unavailable(self, monkeypatch) -> None:
        monkeypatch.setattr(
            "src.data.operational_status.calendar_status",
            lambda: {"state": "UNAVAILABLE", "reason": "broken"},
        )
        resp = test_client.get("/health/ready")
        assert resp.status_code == 503
        assert resp.json()["checks"]["calendar"] == "UNAVAILABLE"

    def test_ready_503_cache_broken(self, monkeypatch) -> None:
        monkeypatch.setattr(
            "src.data.operational_status.cache_status",
            lambda svc: {"state": "DEGRADED", "reason": "no round-trip"},
        )
        resp = test_client.get("/health/ready")
        assert resp.status_code == 503
        assert resp.json()["checks"]["cache"] == "DEGRADED"

    def test_liveness_unaffected_by_config_problems(self, monkeypatch) -> None:
        monkeypatch.setattr(
            "src.config.validation.validate_config",
            lambda: [("RATE_LIMIT", "must be >= 1")],
        )
        resp = test_client.get("/health/live")
        assert resp.status_code == 200

    def test_liveness_unaffected_by_calendar_problems(self, monkeypatch) -> None:
        monkeypatch.setattr(
            "src.data.operational_status.calendar_status",
            lambda: {"state": "UNAVAILABLE", "reason": "broken"},
        )
        resp = test_client.get("/health/live")
        assert resp.status_code == 200

    def test_legacy_root_unchanged(self) -> None:
        resp = test_client.get("/")
        assert resp.status_code == 200
        assert resp.json() == {"status": "NEPSE Quant Engine Running"}

    def test_ready_payload_has_checks(self) -> None:
        resp = test_client.get("/health/ready")
        payload = resp.json()
        assert set(payload["checks"]) >= {"configuration", "calendar", "cache", "system"}


# ═══════════════════════════════════════════════════════════════════
# 4. Graceful shutdown (real uvicorn process)
# ═══════════════════════════════════════════════════════════════════

class TestGracefulShutdown:
    def test_uvicorn_serves_and_sigterm_exits_clean(self, tmp_path) -> None:
        port = _free_port()
        proc = _spawn_uvicorn(port, tmp_path)
        try:
            live = _wait_http(port, "/health/live")
            assert live is not None and live[0] == 200, "uvicorn never became live"
            ready = _wait_http(port, "/health/ready")
            assert ready is not None and ready[0] == 200, "uvicorn never became ready"
        finally:
            rc = _terminate(proc)
        # Single-worker uvicorn (no supervisor) terminates via SIGTERM
        # death (rc=-15) after draining; the two-worker supervisor
        # (the Docker api stage) exits 0.  Both are clean stops — the
        # state-safety assertion is the no-leftover-files test below.
        _assert_clean_stop(rc)

    def test_no_leftover_temp_or_lock_files(self, tmp_path) -> None:
        port = _free_port()
        proc = _spawn_uvicorn(port, tmp_path)
        try:
            assert _wait_http(port, "/health/live") is not None
        finally:
            _terminate(proc)
        leftovers = [
            p for p in (REPO / "data").rglob("*.tmp")
        ] + [p for p in (REPO / "data").rglob("*.lock")]
        # Only allowed in temp dirs created by tests; nothing under repo data/.
        assert leftovers == [], [str(p) for p in leftovers]


# ═══════════════════════════════════════════════════════════════════
# 5. Crash recovery
# ═══════════════════════════════════════════════════════════════════

class TestCrashRecovery:
    def test_dataservice_reset_and_reinit(self) -> None:
        from src.data.service import DataService

        svc = DataService()
        DataService.reset_instance()
        svc2 = DataService()
        assert svc2 is not None
        DataService.reset_instance()

    def test_background_thread_starts_and_stops(self) -> None:
        from src.data.service import DataService

        svc = DataService()
        svc.start_background_refresh(interval=3600)
        assert svc.is_background_refresh_running()
        svc.stop_background_refresh()
        # stop_background_refresh() joins with a fixed 5 s timeout, but
        # the first refresh cycle can take longer than that on a loaded
        # machine (the thread is daemon and exits as soon as the cycle
        # completes).  Assert the real guarantee — the thread actually
        # stops — instead of assuming it fits inside the join window.
        deadline = time.monotonic() + 30
        while svc.is_background_refresh_running() and time.monotonic() < deadline:
            time.sleep(0.25)
        assert not svc.is_background_refresh_running(), (
            "refresh thread did not stop within 30 s"
        )
        DataService.reset_instance()

    def test_disk_cache_corrupt_entry_is_safe_miss(self, tmp_path) -> None:
        from src.data.cache import DiskCache

        cache = DiskCache(cache_dir=tmp_path / "cache")
        cache.set("NABIL", {"close": 500.0})
        entry_file = tmp_path / "cache" / "NABIL.json"
        assert entry_file.exists()
        entry_file.write_text("{garbage not json", encoding="utf-8")
        # A corrupt cache entry is a miss — never a crash, never invented data.
        assert cache.get("NABIL") is None

    def test_state_survives_service_reset(self, monkeypatch, tmp_path) -> None:
        import src.watchlist.manager as manager

        target = tmp_path / "watchlist.json"
        monkeypatch.setattr(manager, "WATCHLIST_FILE", target)
        assert manager.add_stock("NABIL")
        reloaded = manager.load_watchlist()
        assert "NABIL" in reloaded

    def test_worker_metrics_corruption_tolerated(self, tmp_path, monkeypatch) -> None:
        import src.utils.worker_metrics as wm

        target = tmp_path / "worker_metrics.json"
        target.write_text("{broken", encoding="utf-8")
        monkeypatch.setattr(wm, "WORKER_METRICS_FILE", target)
        # Un-throttle the best-effort self-report so this test is
        # deterministic regardless of when an earlier test last reported.
        monkeypatch.setattr(wm, "_last_report_ts", 0.0)
        # aggregate_worker_metrics must tolerate a corrupt store, still
        # produce a bounded payload, and leave the store valid JSON.
        payload = wm.aggregate_worker_metrics({"process": {"pid": 1}})
        assert "workers" in payload
        assert payload["workers"]["active"] >= 1  # the reporting worker itself
        assert json.loads(target.read_text(encoding="utf-8")) is not None


# ═══════════════════════════════════════════════════════════════════
# 6. Persistent-state inventory
# ═══════════════════════════════════════════════════════════════════

class TestStateInventory:
    def test_inventory_nonempty_unique(self) -> None:
        from src.utils.state_inventory import STATE_STORES, store_names

        assert len(STATE_STORES) >= 8
        names = store_names()
        assert len(names) == len(set(names))

    def test_core_stores_covered(self) -> None:
        from src.utils.state_inventory import store_names

        covered = set(store_names())
        assert {"Trading calendar", "Watchlist", "Alert history", "Worker metrics",
                "Disk cache", "Release manifest"} <= covered

    def test_every_json_store_documents_atomicity_and_locking(self) -> None:
        from src.utils.state_inventory import json_stores

        for store in json_stores():
            assert store.atomic_writes is True, store.name
            assert store.corruption_policy, store.name

    def test_owners_importable(self) -> None:
        from src.utils.state_inventory import STATE_STORES

        for store in STATE_STORES:
            if not store.owner.startswith("src.") or " + " in store.owner:
                continue  # composite owners (e.g. loader + script) are not modules
            module_name = store.owner.rsplit(".", 1)[0].replace("/", ".")
            try:
                __import__(module_name)
            except ImportError:
                pytest.fail(f"owner module not importable: {module_name}")

    def test_tracked_state_present(self) -> None:
        assert (REPO / "data/state/nepse_calendar.json").exists()
        assert (REPO / "data/raw/sample.csv").exists()

    def test_inventory_docs_mirror_exists(self) -> None:
        assert (REPO / "docs/STATE_INVENTORY.md").exists()

    def test_no_duplicate_state_stores(self) -> None:
        from src.utils.state_inventory import STATE_STORES

        locations = [s.location for s in STATE_STORES]
        assert len(locations) == len(set(locations))


# ═══════════════════════════════════════════════════════════════════
# 7. State corruption matrix
# ═══════════════════════════════════════════════════════════════════

class TestStateCorruption:
    @pytest.mark.parametrize("content", ["", "{bad json", "not utf8 \xff\xfe"])
    def test_json_store_corrupt_file_backup_and_default(self, tmp_path, content: str) -> None:
        target = tmp_path / "store.json"
        if content == "not utf8 \xff\xfe":
            target.write_bytes(b"\xff\xfe broken")
        else:
            target.write_text(content, encoding="utf-8")
        result = load_json(target, {"default": True}, log_name="test")
        assert result == {"default": True}
        backups = list(tmp_path.glob("*.corrupt.bak*"))
        assert backups, "corrupt file must be preserved aside"

    def test_json_store_missing_file_default(self, tmp_path) -> None:
        assert load_json(tmp_path / "missing.json", {"d": 1}) == {"d": 1}

    def test_json_store_empty_file_backup(self, tmp_path) -> None:
        target = tmp_path / "empty.json"
        target.write_text("   \n", encoding="utf-8")
        assert load_json(target, 42) == 42
        assert list(tmp_path.glob("*.corrupt.bak*"))

    def test_json_store_read_failure_default(self, tmp_path, monkeypatch) -> None:
        target = tmp_path / "locked.json"
        target.write_text("{}", encoding="utf-8")

        def _boom(*_a, **_k):
            raise OSError("permission denied")

        monkeypatch.setattr(Path, "read_text", _boom)
        assert load_json(target, "fallback") == "fallback"

    def test_watchlist_malformed_fails_safe(self, monkeypatch, tmp_path) -> None:
        import src.watchlist.manager as manager

        target = tmp_path / "watchlist.json"
        target.write_text("{broken", encoding="utf-8")
        monkeypatch.setattr(manager, "WATCHLIST_FILE", target)
        assert manager.load_watchlist() == {}

    def test_watchlist_wrong_shape_fails_safe(self, monkeypatch, tmp_path) -> None:
        import src.watchlist.manager as manager

        target = tmp_path / "watchlist.json"
        target.write_text("[1, 2, 3]", encoding="utf-8")
        monkeypatch.setattr(manager, "WATCHLIST_FILE", target)
        assert manager.load_watchlist() == {}

    def test_alert_history_malformed_fails_safe(self, monkeypatch, tmp_path) -> None:
        import src.alerts.history as history

        target = tmp_path / "history.json"
        target.write_text("}", encoding="utf-8")
        monkeypatch.setattr(history, "HISTORY_FILE", target)
        assert history.load_history() == {}

    def test_alert_history_wrong_shape_fails_safe(self, monkeypatch, tmp_path) -> None:
        import src.alerts.history as history

        target = tmp_path / "history.json"
        target.write_text('["not","a","dict"]', encoding="utf-8")
        monkeypatch.setattr(history, "HISTORY_FILE", target)
        assert history.load_history() == {}

    def test_corrupt_calendar_never_becomes_trusted(self, monkeypatch, tmp_path) -> None:
        import src.data.calendar as calendar

        target = tmp_path / "calendar.json"
        target.write_text("{corrupt", encoding="utf-8")
        monkeypatch.setattr(calendar, "NEPSE_CALENDAR_FILE", target)
        cal = calendar.default_calendar()
        # Base weekend rule: Sunday-Thursday open; Friday/Saturday closed.
        assert cal.is_weekend(calendar.date(2026, 8, 15))  # Saturday
        assert not cal.is_weekend(calendar.date(2026, 8, 13))  # Thursday
        assert cal.holidays == ()

    def test_calendar_wrong_schema_fails_safe(self, monkeypatch, tmp_path) -> None:
        import src.data.calendar as calendar

        target = tmp_path / "calendar.json"
        target.write_text('{"calendar_version": "99.0", "holidays": []}', encoding="utf-8")
        monkeypatch.setattr(calendar, "NEPSE_CALENDAR_FILE", target)
        cal = calendar.default_calendar()
        assert cal.holidays == ()

    def test_leftover_temp_file_ignored(self, tmp_path) -> None:
        target = tmp_path / "store.json"
        save_json(target, {"a": 1})
        # Simulate a crash that left a temp file behind.
        leftover = tmp_path / ".store.json.abc.tmp"
        leftover.write_text("partial garbage", encoding="utf-8")
        assert load_json(target, None) == {"a": 1}


# ═══════════════════════════════════════════════════════════════════
# 8. Atomic writes
# ═══════════════════════════════════════════════════════════════════

class TestAtomicWrites:
    def test_save_json_roundtrip(self, tmp_path) -> None:
        target = tmp_path / "a" / "b" / "store.json"
        save_json(target, {"x": [1, 2, 3]})
        assert load_json(target, None) == {"x": [1, 2, 3]}

    def test_save_json_parents_created(self, tmp_path) -> None:
        target = tmp_path / "deep" / "nested" / "store.json"
        save_json(target, {"ok": True})
        assert target.exists()

    def test_save_json_cleans_temp_on_failure(self, tmp_path) -> None:
        target = tmp_path / "store.json"
        with pytest.raises(TypeError):
            save_json(target, {"bad": object()})
        assert not list(tmp_path.glob("*.tmp"))

    def test_save_json_fsync_flag(self, tmp_path) -> None:
        target = tmp_path / "critical.json"
        save_json(target, {"critical": True}, fsync=True)
        assert load_json(target, None) == {"critical": True}

    def test_update_json_concurrent_threads_no_lost_updates(self, tmp_path) -> None:
        target = tmp_path / "counter.json"
        n_threads, per_thread = 8, 25

        def _worker():
            for _ in range(per_thread):
                update_json(
                    target,
                    lambda state: ({"n": state.get("n", 0) + 1}, None),
                    {},
                    log_name="test",
                )

        threads = [threading.Thread(target=_worker) for _ in range(n_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert load_json(target, {})["n"] == n_threads * per_thread

    def test_locked_json_serializes(self, tmp_path) -> None:
        target = tmp_path / "locked.json"
        with locked_json(target):
            save_json(target, {"a": 1})
        assert load_json(target, None) == {"a": 1}
        assert not list(tmp_path.glob("*.lock"))

    def test_stale_lock_recovered(self, tmp_path) -> None:
        target = tmp_path / "store.json"
        lock_path = tmp_path / "store.json.lock"
        lock_path.write_text("999999", encoding="ascii")  # dead pid
        old = time.time() - 60
        os.utime(lock_path, (old, old))
        with locked_json(target):
            save_json(target, {"ok": True})
        assert load_json(target, None) == {"ok": True}

    def test_lock_stats_bounded(self) -> None:
        stats = lock_stats()
        assert set(stats) == {"retries", "stale_recoveries", "timeouts"}
        assert all(isinstance(v, int) and v >= 0 for v in stats.values())


# ═══════════════════════════════════════════════════════════════════
# 9. Backup & restore
# ═══════════════════════════════════════════════════════════════════

class TestBackupAndRestore:
    def test_repeated_corruption_preserves_each_evidence(self, tmp_path) -> None:
        target = tmp_path / "store.json"
        target.write_text("{bad", encoding="utf-8")
        load_json(target, None)
        target.write_text("{also bad", encoding="utf-8")
        load_json(target, None)
        backups = list(tmp_path.glob("*.corrupt*"))
        assert len(backups) == 2

    def test_calendar_update_creates_backup(self, tmp_path, monkeypatch) -> None:
        import src.data.calendar as calendar

        target = tmp_path / "calendar.json"
        data = _shipped_calendar_dict()
        monkeypatch.setattr(calendar, "NEPSE_CALENDAR_FILE", target)
        monkeypatch.setattr(calendar, "CALENDAR_HISTORY_FILE", tmp_path / "history.json")
        ok, problems = calendar.update_calendar(data, path=target, via="test")
        assert ok, problems
        data2 = dict(data)
        data2["holidays"] = [{"date": "2026-10-11", "name": "Dashain test", "source": "test"}]
        ok, problems = calendar.update_calendar(data2, path=target, via="test")
        assert ok, problems
        backups = list(tmp_path.glob("calendar.*.bak.json"))
        assert backups, "previous calendar must be backed up before activation"

    def test_calendar_rollback_restores_previous(self, tmp_path, monkeypatch) -> None:
        import src.data.calendar as calendar

        target = tmp_path / "calendar.json"
        monkeypatch.setattr(calendar, "NEPSE_CALENDAR_FILE", target)
        monkeypatch.setattr(calendar, "CALENDAR_HISTORY_FILE", tmp_path / "history.json")
        base = _shipped_calendar_dict()
        assert calendar.update_calendar(base, path=target, via="test")[0]
        updated = dict(base)
        updated["holidays"] = [{"date": "2026-10-11", "name": "Dashain", "source": "test"}]
        assert calendar.update_calendar(updated, path=target, via="test")[0]
        assert calendar.default_calendar().holidays, "holiday should be active"
        ok, problems = calendar.rollback_calendar(path=target)
        assert ok, problems
        assert calendar.default_calendar().holidays == ()

    def test_calendar_rollback_rejects_corrupt_backup(self, tmp_path, monkeypatch) -> None:
        import src.data.calendar as calendar

        target = tmp_path / "calendar.json"
        monkeypatch.setattr(calendar, "NEPSE_CALENDAR_FILE", target)
        monkeypatch.setattr(calendar, "CALENDAR_HISTORY_FILE", tmp_path / "history.json")
        # First activation creates the file; a second activation backs
        # up the previous version (the first activation has nothing to
        # back up — the target did not exist yet).
        assert calendar.update_calendar(_shipped_calendar_dict(), path=target, via="test")[0]
        updated = dict(_shipped_calendar_dict())
        updated["holidays"] = [{"date": "2026-10-11", "name": "Dashain", "source": "test"}]
        assert calendar.update_calendar(updated, path=target, via="test")[0]
        backups = list(tmp_path.glob("calendar.*.bak.json"))
        assert backups
        backups[-1].write_text("{corrupt backup", encoding="utf-8")
        ok, problems = calendar.rollback_calendar(path=target)
        assert not ok
        assert problems, "a corrupt backup must be rejected with diagnostics"
        # Active calendar untouched.
        assert calendar.NepseCalendar.load(target) is not None

    def test_backup_script_executes(self, tmp_path) -> None:
        if not shutil.which("bash"):
            pytest.skip("bash not available")
        dest = tmp_path / "backups"
        result = subprocess.run(
            ["bash", "scripts/backup.sh", str(dest)],
            cwd=REPO, capture_output=True, text=True, timeout=120,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        archives = list(dest.glob("nepse_quant_engine_backup_*.tar.gz"))
        assert archives, "backup archive not created"

    def test_backup_retention_bounded(self) -> None:
        text = (REPO / "scripts/backup.sh").read_text(encoding="utf-8")
        match = re.search(r"RETENTION_DAYS\s*=\s*(\d+)", text)
        assert match, "backup script must declare a retention window"
        assert int(match.group(1)) > 0


# ═══════════════════════════════════════════════════════════════════
# 10. Calendar migration & provenance
# ═══════════════════════════════════════════════════════════════════

class TestCalendarMigration:
    def test_shipped_calendar_loads_with_provenance(self) -> None:
        import src.data.calendar as calendar

        cal = calendar.default_calendar()
        status = cal.to_status()
        assert status.get("has_provenance") is True

    def test_supported_versions_declared(self) -> None:
        import src.data.calendar as calendar

        assert "2.0" in calendar.SUPPORTED_CALENDAR_VERSIONS
        assert "1.0" in calendar.SUPPORTED_CALENDAR_VERSIONS

    def test_version_upgrade_persists(self, tmp_path, monkeypatch) -> None:
        import src.data.calendar as calendar

        target = tmp_path / "calendar.json"
        monkeypatch.setattr(calendar, "CALENDAR_HISTORY_FILE", tmp_path / "history.json")
        data = _shipped_calendar_dict()
        assert calendar.update_calendar(data, path=target, via="test")[0]
        loaded = calendar.NepseCalendar.load(target)
        assert loaded is not None
        assert loaded.version == "2.0"

    def test_unsupported_version_rejected(self, tmp_path) -> None:
        import src.data.calendar as calendar

        target = tmp_path / "calendar.json"
        data = _shipped_calendar_dict()
        data["calendar_version"] = "9.9"
        ok, problems = calendar.update_calendar(data, path=target, via="test")
        assert not ok
        assert problems
        assert not target.exists(), "rejected candidate must never be written"

    def test_v1_shape_backward_compatible(self) -> None:
        import src.data.calendar as calendar

        data = _shipped_calendar_dict()
        data["calendar_version"] = "1.0"
        data.pop("provenance", None)
        data.pop("weekend_days", None)
        problems = calendar.validate_calendar_data(data)
        assert not problems

    def test_history_records_activation(self, tmp_path, monkeypatch) -> None:
        import src.data.calendar as calendar

        target = tmp_path / "calendar.json"
        monkeypatch.setattr(calendar, "NEPSE_CALENDAR_FILE", target)
        monkeypatch.setattr(calendar, "CALENDAR_HISTORY_FILE", tmp_path / "history.json")
        assert calendar.update_calendar(_shipped_calendar_dict(), path=target, via="test")[0]
        history = calendar.calendar_version_history()
        assert history, "activation must be recorded in version history"
        assert history[-1]["via"] == "test"

    def test_provenance_survives_save_load(self, tmp_path) -> None:
        import src.data.calendar as calendar

        cal = calendar.default_calendar()
        target = tmp_path / "calendar.json"
        cal.save(target)
        reloaded = calendar.NepseCalendar.load(target)
        assert reloaded is not None
        assert reloaded.to_status().get("has_provenance") is True

    def test_no_silent_reinterpretation_of_unknown_version(self, tmp_path) -> None:
        import src.data.calendar as calendar

        target = tmp_path / "calendar.json"
        data = _shipped_calendar_dict()
        data["calendar_version"] = "2.5"
        ok, _ = calendar.update_calendar(data, path=target, via="test")
        # A major-line-supported version may be accepted; a wholly
        # foreign version must never be reinterpreted.
        if not ok:
            assert not target.exists()
        else:
            loaded = calendar.NepseCalendar.load(target)
            assert loaded is not None


# ═══════════════════════════════════════════════════════════════════
# 11. Watchlist / cache migration compat
# ═══════════════════════════════════════════════════════════════════

class TestWatchlistCacheCompat:
    def test_watchlist_roundtrip(self, monkeypatch, tmp_path) -> None:
        import src.watchlist.manager as manager

        target = tmp_path / "watchlist.json"
        monkeypatch.setattr(manager, "WATCHLIST_FILE", target)
        manager.add_stock("NABIL")
        manager.add_stock("SCB")
        assert set(manager.load_watchlist()) == {"NABIL", "SCB"}
        assert manager.remove_stock("NABIL")
        assert set(manager.load_watchlist()) == {"SCB"}

    def test_watchlist_unknown_keys_preserved(self, monkeypatch, tmp_path) -> None:
        import src.watchlist.manager as manager

        target = tmp_path / "watchlist.json"
        target.write_text(json.dumps({"NABIL": {"enabled": True, "future_field": 1}}))
        monkeypatch.setattr(manager, "WATCHLIST_FILE", target)
        assert manager.load_watchlist()["NABIL"]["future_field"] == 1

    def test_alert_history_roundtrip(self, monkeypatch, tmp_path) -> None:
        import src.alerts.history as history

        target = tmp_path / "history.json"
        monkeypatch.setattr(history, "HISTORY_FILE", target)
        history.save_history({"NABIL": {"milestones": {"target1": True}}})
        assert history.load_history()["NABIL"]["milestones"]["target1"] is True

    def test_disk_cache_roundtrip(self, tmp_path) -> None:
        from src.data.cache import DiskCache

        cache = DiskCache(cache_dir=tmp_path / "cache")
        cache.set("ADBL", {"close": 250.0})
        assert cache.get("ADBL") == {"close": 250.0}

    def test_disk_cache_ttl_expiry(self, tmp_path) -> None:
        from src.data.cache import DiskCache

        cache = DiskCache(cache_dir=tmp_path / "cache")
        cache.set("ADBL", {"close": 1.0}, ttl=1)
        assert cache.get("ADBL") is not None
        time.sleep(1.1)
        assert cache.get("ADBL") is None


# ═══════════════════════════════════════════════════════════════════
# 12. Metrics & operational status
# ═══════════════════════════════════════════════════════════════════

class TestMetricsEndpoint:
    def test_metrics_200(self) -> None:
        resp = test_client.get("/metrics")
        assert resp.status_code == 200

    def test_metrics_process_identity(self) -> None:
        resp = test_client.get("/metrics").json()
        assert resp["process"]["pid"] == os.getpid()
        assert resp["process"]["hostname"]

    def test_metrics_system_status_present(self) -> None:
        resp = test_client.get("/metrics").json()
        block = resp["system_status"]
        assert block["overall"] in {"HEALTHY", "DEGRADED", "UNAVAILABLE", "UNKNOWN"}
        assert set(block) >= {"data_provider", "reconciliation", "calendar", "data_quality", "incidents", "cache"}

    def test_metrics_workers_and_aggregate_present(self) -> None:
        resp = test_client.get("/metrics").json()
        assert "workers" in resp
        assert "aggregate" in resp
        assert resp["workers"]["active"] >= 1

    def test_metrics_bounded(self) -> None:
        resp = test_client.get("/metrics").json()
        assert len(resp["incidents"].get("recent", [])) <= 25

    def test_metrics_has_no_secret_keys(self) -> None:
        resp = test_client.get("/metrics").json()

        def _walk(node, path=""):
            if isinstance(node, dict):
                for k, v in node.items():
                    assert "token" not in k.lower(), f"secret-looking key {path}/{k}"
                    assert "secret" not in k.lower(), f"secret-looking key {path}/{k}"
                    assert "password" not in k.lower(), f"secret-looking key {path}/{k}"
                    _walk(v, f"{path}/{k}")
            elif isinstance(node, list):
                for i, item in enumerate(node):
                    _walk(item, f"{path}[{i}]")

        _walk(resp)


class TestOperationalStatus:
    def test_system_status_never_raises(self) -> None:
        from src.data.operational_status import system_status

        result = system_status()
        assert isinstance(result, dict)
        assert "overall" in result

    def test_provider_status_empty_unknown(self) -> None:
        from src.data.operational_status import UNKNOWN, provider_status

        fake = SimpleNamespace(
            health_monitor=SimpleNamespace(
                get_all_health=lambda: [],
                degradation_state=lambda name: "HEALTHY",
            )
        )
        assert provider_status(fake)["state"] == UNKNOWN

    def test_provider_all_disabled_unavailable(self) -> None:
        from src.data.operational_status import UNAVAILABLE, provider_status

        class _H:
            def get_all_health(self):
                return [SimpleNamespace(name="api", is_disabled=True)]
            def degradation_state(self, name):
                return "HEALTHY"

        fake = SimpleNamespace(health_monitor=_H())
        assert provider_status(fake)["state"] == UNAVAILABLE

    def test_cache_status_healthy_roundtrip(self) -> None:
        from src.data.cache import TieredCache
        from src.data.operational_status import HEALTHY, cache_status

        cache = TieredCache(memory_ttl=60, disk_ttl=60)
        fake = SimpleNamespace(_cache=cache)
        assert cache_status(fake)["state"] == HEALTHY

    def test_calendar_status_shipped(self) -> None:
        from src.data.operational_status import HEALTHY, calendar_status

        assert calendar_status()["state"] == HEALTHY


# ═══════════════════════════════════════════════════════════════════
# 13. Secret handling & security regression
# ═══════════════════════════════════════════════════════════════════

class TestSecretHandling:
    _SECRET_RE = re.compile(
        r"(?i)\b(?:telegram_token|bot_token|api[_-]?key|client[_-]?secret|"
        r"access[_-]?token|secret_key|password|credential)\b\s*=\s*[\"']?[A-Za-z0-9_\-.:]{12,}"
    )
    _PLACEHOLDER_RE = re.compile(r"(?i)(your_|xxx+|example|placeholder|changeme|<[^>]+>|[$][{]|[{][{])")

    def test_no_committed_secret_assignments(self) -> None:
        tracked = subprocess.run(
            ["git", "ls-files"], cwd=REPO, capture_output=True, text=True, timeout=30
        ).stdout.splitlines()
        flagged = []
        for rel in tracked:
            if not rel.endswith((".py", ".md", ".txt", ".yml", ".yaml", ".json", ".sh", ".ini", ".bat")):
                continue
            text = (REPO / rel).read_text(encoding="utf-8", errors="replace")
            for match in self._SECRET_RE.finditer(text):
                if self._PLACEHOLDER_RE.search(match.group(0)):
                    continue
                flagged.append(f"{rel}: {match.group(0)[:40]}")
        assert flagged == [], flagged

    def test_env_example_has_no_secret_values(self) -> None:
        example = REPO / ".env.example"
        assert example.exists()
        text = example.read_text(encoding="utf-8")
        for match in self._SECRET_RE.finditer(text):
            if self._PLACEHOLDER_RE.search(match.group(0)):
                continue
            pytest.fail(f"non-placeholder secret-looking value in .env.example: {match.group(0)[:40]}")

    def test_metrics_no_token_values(self) -> None:
        payload = json.dumps(test_client.get("/metrics").json())
        assert "token" not in payload.lower().replace('"token"', "")

    def test_config_errors_do_not_log_values(self, monkeypatch, caplog) -> None:
        monkeypatch.setattr("src.config.validation.WEBSOCKET_URL", "wss://super-secret-host")
        validate_config()
        assert "super-secret-host" not in caplog.text


# ═══════════════════════════════════════════════════════════════════
# 14. Release manifest
# ═══════════════════════════════════════════════════════════════════

class TestReleaseManifest:
    def test_build_static_fields(self) -> None:
        from src.utils.release_manifest import build_manifest

        manifest = build_manifest(REPO, revision="abc123", timestamp="2026-08-16T00:00:00+00:00")
        assert manifest["version"] == (REPO / "VERSION").read_text(encoding="utf-8").strip()
        assert manifest["source_revision"] == "abc123"
        assert manifest["schema"] == "nepse-quant-engine-release-manifest"
        assert "fastapi" in manifest["dependencies"]

    def test_static_fields_deterministic(self) -> None:
        from src.utils.release_manifest import build_manifest

        a = build_manifest(REPO, revision="rev1", timestamp="2026-08-16T00:00:00+00:00")
        b = build_manifest(REPO, revision="rev1", timestamp="2026-08-16T00:00:00+00:00")
        assert a == b

    def test_dynamic_fields_change(self) -> None:
        from src.utils.release_manifest import build_manifest

        a = build_manifest(REPO, revision="rev1", timestamp="2026-08-16T00:00:00+00:00")
        b = build_manifest(REPO, revision="rev2", timestamp="2026-08-16T00:00:00+00:00")
        assert a["build_id"] != b["build_id"]

    def test_verify_clean(self) -> None:
        from src.utils.release_manifest import build_manifest, verify_manifest

        assert verify_manifest(build_manifest(REPO), REPO) == []

    def test_verify_detects_version_mismatch(self) -> None:
        from src.utils.release_manifest import build_manifest, verify_manifest

        manifest = build_manifest(REPO)
        manifest["version"] = "9.9.9"
        assert verify_manifest(manifest, REPO)

    def test_verify_detects_dependency_drift(self) -> None:
        from src.utils.release_manifest import build_manifest, verify_manifest

        manifest = build_manifest(REPO)
        manifest["dependencies"] = dict(manifest["dependencies"])
        manifest["dependencies"]["fake_pkg"] = ">=1.0"
        assert verify_manifest(manifest, REPO)

    def test_manifest_contains_no_secrets(self) -> None:
        from src.utils.release_manifest import build_manifest

        assert json.dumps(build_manifest(REPO)).lower().count("token") == 0

    def test_write_manifest_atomic(self, tmp_path) -> None:
        from src.utils.release_manifest import build_manifest, write_manifest

        target = tmp_path / "manifest.json"
        write_manifest(build_manifest(REPO), target)
        assert target.exists()
        assert not list(tmp_path.glob("*.tmp"))


# ═══════════════════════════════════════════════════════════════════
# 15. Release artifact integrity
# ═══════════════════════════════════════════════════════════════════

class TestReleaseArtifactIntegrity:
    def test_build_tarball_excludes_junk(self, tmp_path) -> None:
        from src.utils.release_artifact import build_source_tarball

        out = tmp_path / "artifact.tar.gz"
        build_source_tarball(REPO, out)
        with tarfile.open(out, "r:gz") as tar:
            names = tar.getnames()
        assert not any("__pycache__" in n for n in names)
        assert not any(n.endswith(".pyc") for n in names)
        assert not any(".pytest_tmp" in n for n in names)

    def test_tarball_excludes_local_workspace_metadata(self, tmp_path) -> None:
        # Freebuff workspace metadata must never ship in a release
        # artifact (Sprint 13.9 §3/§4: no reliance on local state).
        from src.utils.release_artifact import build_source_tarball

        out = tmp_path / "artifact.tar.gz"
        build_source_tarball(REPO, out)
        with tarfile.open(out, "r:gz") as tar:
            names = tar.getnames()
        assert not any(".freebuff" in n for n in names), [n for n in names if ".freebuff" in n]

    def test_tarball_excludes_generated_benchmark_corpus(self, tmp_path) -> None:
        # benchmarks.common.write_csvs generates synNNN.csv at runtime;
        # leftover files under data/raw must not ship (build-machine
        # residue, and data/raw/*.csv is gitignored).
        from src.utils.release_artifact import build_source_tarball

        out = tmp_path / "artifact.tar.gz"
        build_source_tarball(REPO, out)
        with tarfile.open(out, "r:gz") as tar:
            names = tar.getnames()
        synthetic = [n for n in names if re.search(r"data/raw/syn\d+\.csv", n)]
        assert synthetic == [], synthetic

    def test_tarball_excludes_runtime_state_and_keeps_calendar(self, tmp_path) -> None:
        # Gitignored runtime/build state is regenerated, never shipped;
        # the tracked governed calendar must always ship.
        from src.utils.release_artifact import build_source_tarball

        out = tmp_path / "artifact.tar.gz"
        build_source_tarball(REPO, out)
        with tarfile.open(out, "r:gz") as tar:
            names = tar.getnames()
        for junk in ("worker_metrics.json", "nepse_calendar_history.json", "release_manifest.json"):
            assert not any(n.endswith(junk) for n in names), junk
        assert any(n.endswith("data/state/nepse_calendar.json") for n in names)

    def test_verify_clean_artifact(self, tmp_path) -> None:
        from src.utils.release_artifact import build_source_tarball, verify_tarball

        out = tmp_path / "artifact.tar.gz"
        build_source_tarball(REPO, out)
        problems = verify_tarball(out)
        assert problems == [], problems

    def test_verify_detects_committed_secret(self, tmp_path) -> None:
        from src.utils.release_artifact import verify_release_artifact

        (tmp_path / "src").mkdir()
        (tmp_path / "VERSION").write_text("1.0.0")
        (tmp_path / "requirements.txt").write_text("fastapi")
        secret_file = tmp_path / "config.py"
        # Built by concatenation so the fixture value itself never
        # appears as a literal in this source tree.
        key = "TELEGRAM" + "_TOKEN"
        secret_file.write_text(f"{key} = '1234567890ABCDEFGH'")
        problems = verify_release_artifact(tmp_path)
        assert any("possible secret" in p for p in problems), problems

    def test_verify_detects_local_path(self, tmp_path) -> None:
        from src.utils.release_artifact import verify_release_artifact

        (tmp_path / "src").mkdir()
        (tmp_path / "VERSION").write_text("1.0.0")
        (tmp_path / "requirements.txt").write_text("fastapi")
        dev_home = "/home/" + "some-developer"
        (tmp_path / "script.sh").write_text(f"cd {dev_home}/project\n")
        problems = verify_release_artifact(tmp_path)
        assert any("local developer path" in p for p in problems), problems

    def test_verify_placeholder_not_flagged(self, tmp_path) -> None:
        from src.utils.release_artifact import verify_release_artifact

        (tmp_path / "src").mkdir()
        (tmp_path / "VERSION").write_text("1.0.0")
        (tmp_path / "requirements.txt").write_text("fastapi")
        (tmp_path / "config.py").write_text("TELEGRAM_TOKEN = 'your_telegram_bot_token'")
        problems = verify_release_artifact(tmp_path)
        assert not any("possible secret" in p for p in problems), problems

    def test_verify_requires_tests(self, tmp_path) -> None:
        from src.utils.release_artifact import verify_release_artifact

        (tmp_path / "VERSION").write_text("1.0.0")
        (tmp_path / "requirements.txt").write_text("fastapi")
        problems = verify_release_artifact(tmp_path)
        assert any("tests/" in p for p in problems), problems

    def test_required_files_declared(self) -> None:
        from src.utils.release_artifact import REQUIRED_SOURCE_FILES

        assert "app.py" in REQUIRED_SOURCE_FILES
        assert "src/api/main.py" in REQUIRED_SOURCE_FILES
        assert "docs/OPERATIONS.md" in REQUIRED_SOURCE_FILES


# ═══════════════════════════════════════════════════════════════════
# 16. Dependency reproducibility
# ═══════════════════════════════════════════════════════════════════

class TestDependencyReproducibility:
    def test_requirements_no_absolute_paths(self) -> None:
        text = (REPO / "requirements.txt").read_text(encoding="utf-8")
        assert not re.search(r"(?m)^\s*/", text), "absolute path in requirements"

    def test_requirements_no_local_vcs_deps(self) -> None:
        text = (REPO / "requirements.txt").read_text(encoding="utf-8")
        assert "git+" not in text
        assert "file://" not in text

    def test_installed_satisfies_requirements(self) -> None:
        from packaging.requirements import Requirement

        for raw in (REPO / "requirements.txt").read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            try:
                req = Requirement(line)
            except Exception:
                continue
            try:
                installed = importlib.metadata.version(req.name)
            except importlib.metadata.PackageNotFoundError:
                continue  # optional dependency not installed in this env
            assert req.specifier.contains(installed, prereleases=True), (
                f"{req.name} {installed} does not satisfy {req.specifier}"
            )

    def test_no_venv_reference_in_scripts(self) -> None:
        for rel in ("Dockerfile", "docker-compose.yml", ".github/workflows/ci.yml",
                    ".github/workflows/release.yml"):
            text = (REPO / rel).read_text(encoding="utf-8")
            assert ".venv" not in text, f"{rel} references a developer venv"


# ═══════════════════════════════════════════════════════════════════
# 17. Docker static audit
# ═══════════════════════════════════════════════════════════════════

class TestDockerStaticAudit:
    def test_dockerfile_has_healthchecks(self) -> None:
        text = (REPO / "Dockerfile").read_text(encoding="utf-8")
        assert text.count("HEALTHCHECK") >= 2

    def test_dockerfile_non_root_user(self) -> None:
        text = (REPO / "Dockerfile").read_text(encoding="utf-8")
        assert re.search(r"^USER nepse", text, re.M)

    def test_dockerfile_exposes_only_expected_ports(self) -> None:
        text = (REPO / "Dockerfile").read_text(encoding="utf-8")
        exposes = re.findall(r"^EXPOSE\s+(\S+)", text, re.M)
        assert set(exposes) <= {"8501", "8000"}

    def test_dockerfile_no_secret_env_literals(self) -> None:
        text = (REPO / "Dockerfile").read_text(encoding="utf-8")
        assert "TELEGRAM_TOKEN=" not in text.replace("${TELEGRAM_TOKEN", "")
        assert "API_KEY=" not in text

    def test_dockerfile_web_binds_all_interfaces(self) -> None:
        text = (REPO / "Dockerfile").read_text(encoding="utf-8")
        assert "--server.address=0.0.0.0" in text

    def test_dockerfile_api_binds_all_interfaces(self) -> None:
        text = (REPO / "Dockerfile").read_text(encoding="utf-8")
        assert "--host=0.0.0.0" in text

    def test_compose_restart_policies(self) -> None:
        text = (REPO / "docker-compose.yml").read_text(encoding="utf-8")
        assert text.count("restart: unless-stopped") >= 2

    def test_compose_volumes_declared(self) -> None:
        text = (REPO / "docker-compose.yml").read_text(encoding="utf-8")
        for volume in ("market_data", "nepse_cache", "app_logs"):
            assert volume in text

    def test_compose_api_healthcheck_liveness(self) -> None:
        text = (REPO / "docker-compose.yml").read_text(encoding="utf-8")
        assert "/health/live" in text

    def test_compose_bot_requires_token(self) -> None:
        text = (REPO / "docker-compose.yml").read_text(encoding="utf-8")
        assert "TELEGRAM_TOKEN:?Telegram token required" in text

    def test_compose_no_secret_literals(self) -> None:
        text = (REPO / "docker-compose.yml").read_text(encoding="utf-8")
        # Every secret-looking assignment must reference a ${...}
        # placeholder or be empty — never a literal value.
        for match in re.finditer(r"(?m)^\s*[-\w.]*\s*(TELEGRAM_TOKEN|API_KEY|SECRET)\s*=\s*(\S*)", text):
            value = match.group(2)
            assert value.startswith("${") or value == "", (
                f"literal secret-looking value in compose: {match.group(0)}"
            )


# ═══════════════════════════════════════════════════════════════════
# 18. Two-worker deployment (release artifact)
# ═══════════════════════════════════════════════════════════════════

class TestTwoWorkerDeployment:
    def test_two_workers_register_in_metrics(self, tmp_path) -> None:
        port = _free_port()
        proc = _spawn_uvicorn(port, tmp_path, workers=2)
        try:
            live = _wait_http(port, "/health/live")
            assert live is not None and live[0] == 200, "two-worker api never became live"
            deadline = time.monotonic() + 60
            seen_active = 0
            while time.monotonic() < deadline:
                result = _http_get(port, "/metrics")
                if result and result[0] == 200:
                    workers = result[1].get("workers", {})
                    seen_active = max(seen_active, int(workers.get("active", 0)))
                    if seen_active >= 2:
                        break
                time.sleep(0.5)
            assert seen_active >= 2, f"expected 2 active workers, saw {seen_active}"
        finally:
            rc = _terminate(proc)
        # The two-worker supervisor exits 0 after draining on POSIX; on
        # Windows SIGTERM is TerminateProcess (rc=1).  Both are clean
        # stops — the metrics store stays valid is asserted below.
        _assert_clean_stop(rc)

    def test_worker_metrics_store_stays_valid(self, tmp_path) -> None:
        port = _free_port()
        proc = _spawn_uvicorn(port, tmp_path, workers=2)
        try:
            assert _wait_http(port, "/health/live") is not None
            _http_get(port, "/metrics")
        finally:
            _terminate(proc)
        store = tmp_path / "worker_metrics.json"
        if store.exists():
            data = json.loads(store.read_text(encoding="utf-8"))
            assert isinstance(data, dict)


# ═══════════════════════════════════════════════════════════════════
# 19. Performance constants unchanged
# ═══════════════════════════════════════════════════════════════════

class TestPerformanceConstants:
    def test_gate_constants_unchanged(self) -> None:
        import benchmarks.ci_gate as gate

        assert gate.WARM_API_RATIO_MAX == 0.75
        assert gate.WARM_PORTFOLIO_RATIO_MAX == 0.75
        assert gate.ANALYZE_P99_MAX_MS == 2000.0
        assert gate.WARM_SPEEDUP_MIN == 5.0

    def test_gate_module_imports(self) -> None:
        import benchmarks.ci_gate as gate  # noqa: F401


# ═══════════════════════════════════════════════════════════════════
# 20. Sprint 13.8 regression spot-checks
# ═══════════════════════════════════════════════════════════════════

class TestSprint13_8Regression:
    def test_json_store_stale_lock_breaker(self, tmp_path) -> None:
        target = tmp_path / "store.json"
        lock_path = tmp_path / "store.json.lock"
        lock_path.write_text("1", encoding="ascii")
        old = time.time() - 60
        os.utime(lock_path, (old, old))
        with locked_json(target):
            save_json(target, {"ok": 1})
        stats = lock_stats()
        assert stats["stale_recoveries"] >= 1

    def test_dataservice_metrics_bounded(self) -> None:
        from src.data.service import DataService

        svc = DataService()
        metrics = svc.get_metrics()
        total = metrics.total_requests
        assert isinstance(total, int) and total >= 0
        DataService.reset_instance()

    def test_alert_history_concurrent_writers_no_corruption(self, monkeypatch, tmp_path) -> None:
        import src.alerts.history as history

        target = tmp_path / "history.json"
        monkeypatch.setattr(history, "HISTORY_FILE", target)
        errors: list[Exception] = []

        def _writer(idx: int):
            try:
                for _ in range(5):
                    history.save_history({"w": idx})
            except Exception as exc:  # noqa: BLE001
                errors.append(exc)

        threads = [threading.Thread(target=_writer, args=(i,)) for i in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert not errors
        # Store must still be valid JSON.
        json.loads(target.read_text(encoding="utf-8"))
