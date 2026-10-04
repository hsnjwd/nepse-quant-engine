"""Launcher: Streamlit dashboard process management + ProcessManager.

Hermetic tests — no real servers, Streamlit processes, or subprocesses
are spawned.  ``subprocess.Popen`` and the ``*_running`` probes are
monkeypatched; ``dashboard_running`` uses a real loopback socket bound
to an ephemeral port (no external dependency).
"""

from __future__ import annotations

import socket
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from launcher import dashboard as db
from launcher import process_manager as pm


def _free_port() -> int:
    """Return an ephemeral port that is currently free."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


# ───────────────────────────────────────────────────────────────────
# dashboard_running probe
# ───────────────────────────────────────────────────────────────────


class _HealthServer:
    """Minimal HTTP server answering ``/_stcore/health`` with a chosen
    status on an ephemeral port (mirrors Streamlit's health endpoint)."""

    def __init__(self, status: int = 200) -> None:
        self.status = status
        self.httpd: ThreadingHTTPServer | None = None
        self.thread: threading.Thread | None = None
        self.port: int = 0

    def start(self) -> "_HealthServer":
        class _Handler(BaseHTTPRequestHandler):
            status = self.status

            def do_GET(self):  # noqa: N802 - http.server API
                if self.path == "/_stcore/health":
                    self.send_response(self.status)
                    self.send_header("Content-Length", "2")
                    self.end_headers()
                    self.wfile.write(b"ok")
                else:
                    self.send_response(404)
                    self.end_headers()

            def log_message(self, *args) -> None:  # silence test noise
                pass

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        self.port = self.httpd.server_address[1]
        self.thread = threading.Thread(
            target=self.httpd.serve_forever, daemon=True
        )
        self.thread.start()
        return self

    def close(self) -> None:
        if self.httpd is not None:
            self.httpd.shutdown()
            self.httpd.server_close()
        if self.thread is not None:
            self.thread.join(timeout=5)


class TestDashboardRunning:
    def test_false_when_nothing_listening(self, monkeypatch):
        monkeypatch.setattr(db, "DASHBOARD_PORT", _free_port())
        assert db.dashboard_running() is False

    def test_true_when_port_bound(self, monkeypatch):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind(("127.0.0.1", 0))
            s.listen(1)
            monkeypatch.setattr(db, "DASHBOARD_PORT", s.getsockname()[1])
            assert db.dashboard_running() is True


# ───────────────────────────────────────────────────────────────────
# start_dashboard / stop_dashboard pid-file management
# ───────────────────────────────────────────────────────────────────


class TestDashboardLifecycle:
    def test_start_writes_pid_file(self, tmp_path, monkeypatch):
        pid_file = tmp_path / "dashboard.pid"
        monkeypatch.setattr(db, "PID_FILE", pid_file)
        monkeypatch.setattr(db, "dashboard_running", lambda: False)

        class _Proc:
            pid = 4242

        def _popen(cmd, **kwargs):
            assert sys.executable and "-m" in cmd
            assert "streamlit" in cmd and "app.py" in " ".join(cmd)
            return _Proc()

        monkeypatch.setattr(db.subprocess, "Popen", _popen)
        proc = db.start_dashboard()
        assert proc.pid == 4242
        assert pid_file.read_text() == "4242"

    def test_start_skips_when_already_running(self, monkeypatch):
        monkeypatch.setattr(db, "dashboard_running", lambda: True)
        assert db.start_dashboard() is None

    def test_stop_unlinks_pid_file(self, tmp_path, monkeypatch):
        pid_file = tmp_path / "dashboard.pid"
        pid_file.write_text("4242")
        monkeypatch.setattr(db, "PID_FILE", pid_file)
        db.stop_dashboard()
        assert not pid_file.exists()

    def test_stop_terminates_process(self, monkeypatch):
        class _Proc:
            def terminate(self):
                self.killed = True

            def wait(self):
                pass

        proc = _Proc()
        db.stop_dashboard(proc)
        assert proc.killed is True


# ───────────────────────────────────────────────────────────────────
# Console dashboard renders the Streamlit line
# ───────────────────────────────────────────────────────────────────


# ───────────────────────────────────────────────────────────────────
# Boot health check (/_stcore/health, gate streamlit-boot pattern)
# ───────────────────────────────────────────────────────────────────


class TestWaitUntilHealthy:
    def test_true_when_health_answers_200(self, monkeypatch):
        server = _HealthServer(200).start()
        try:
            monkeypatch.setattr(db, "DASHBOARD_PORT", server.port)
            monkeypatch.setattr(db, "_HEALTH_POLL_SECONDS", 0.05)
            healthy, reason = db.wait_until_healthy(timeout=5.0)
            assert healthy is True
            assert reason == ""
        finally:
            server.close()

    def test_false_when_endpoint_returns_error_status(self, monkeypatch):
        # urllib.urlopen RAISES HTTPError for 5xx (never returns a
        # response), so the recorded reason is "HTTPError: HTTP Error
        # 500 ..." — assert on the status digit, not the resp.status path.
        server = _HealthServer(500).start()
        try:
            monkeypatch.setattr(db, "DASHBOARD_PORT", server.port)
            monkeypatch.setattr(db, "_HEALTH_POLL_SECONDS", 0.05)
            healthy, reason = db.wait_until_healthy(timeout=1.0)
            assert healthy is False
            assert "500" in reason
        finally:
            server.close()

    def test_false_when_nothing_listening(self, monkeypatch):
        monkeypatch.setattr(db, "DASHBOARD_PORT", _free_port())
        monkeypatch.setattr(db, "_HEALTH_POLL_SECONDS", 0.05)
        healthy, reason = db.wait_until_healthy(timeout=1.0)
        assert healthy is False
        assert reason  # last probe failure recorded, never empty

    def test_default_timeout_reads_config_at_call_time(self, monkeypatch):
        """With timeout=None the helper must resolve the config value AT
        CALL TIME — monkeypatching DASHBOARD_HEALTH_TIMEOUT after import
        must take effect (it is not bound into __defaults__ at import)."""
        monkeypatch.setattr(db, "DASHBOARD_HEALTH_TIMEOUT", 0.3)
        monkeypatch.setattr(db, "DASHBOARD_PORT", _free_port())
        monkeypatch.setattr(db, "_HEALTH_POLL_SECONDS", 0.05)
        start = time.perf_counter()
        healthy, _reason = db.wait_until_healthy()  # no timeout arg
        elapsed = time.perf_counter() - start
        assert healthy is False
        # The 0.3 s config default (not the 90 s import-time default)
        # was used — a 90 s wait would trip this by a wide margin.
        assert elapsed < 5.0


# ───────────────────────────────────────────────────────────────────
# run_launcher recovery: stale pid cleared + one restart + re-check
# ───────────────────────────────────────────────────────────────────


class _FakeManager:
    """Records start_dashboard calls without spawning a real process."""

    def __init__(self) -> None:
        self.start_calls = 0

    def start_dashboard(self) -> bool:
        self.start_calls += 1
        return True


class TestRecoverDashboard:
    def test_clears_stale_pid_restarts_once_and_recovers(self, tmp_path, monkeypatch):
        """recover_dashboard re-checks health exactly ONCE (main() runs
        the initial check); the single re-check here succeeds."""
        import run_launcher

        pid_file = tmp_path / "dashboard.pid"
        pid_file.write_text("9999")  # stale pid from a dead process
        manager = _FakeManager()
        monkeypatch.setattr(
            run_launcher, "wait_until_healthy", lambda **kw: (True, "")
        )
        monkeypatch.setattr(run_launcher, "PID_FILE", pid_file)

        healthy, reason = run_launcher.recover_dashboard(manager)

        assert healthy is True
        assert reason == ""
        assert manager.start_calls == 1
        assert not pid_file.exists()  # stale pid cleared

    def test_warns_when_port_bound_but_unhealthy(self, tmp_path, monkeypatch):
        """A bound-but-unhealthy port means the manager's start_dashboard
        guard would SKIP the spawn — the recovery must warn explicitly so
        the operator knows the retry may have started nothing."""
        import run_launcher

        manager = _FakeManager()
        warnings: list[str] = []
        monkeypatch.setattr(run_launcher, "dashboard_running", lambda: True)
        monkeypatch.setattr(
            run_launcher, "wait_until_healthy", lambda **kw: (False, "still down")
        )
        monkeypatch.setattr(run_launcher, "PID_FILE", tmp_path / "dashboard.pid")
        monkeypatch.setattr(
            run_launcher.logger, "warning", lambda msg, *a, **k: warnings.append(str(msg))
        )

        healthy, reason = run_launcher.recover_dashboard(manager)

        assert healthy is False
        assert reason == "still down"
        assert any("already bound" in w for w in warnings)

    def test_still_unhealthy_after_retry_reports_reason(self, tmp_path, monkeypatch):
        import run_launcher

        manager = _FakeManager()
        monkeypatch.setattr(
            run_launcher, "wait_until_healthy", lambda **kw: (False, "still down")
        )
        monkeypatch.setattr(run_launcher, "PID_FILE", tmp_path / "dashboard.pid")

        healthy, reason = run_launcher.recover_dashboard(manager)

        assert healthy is False
        assert reason == "still down"
        assert manager.start_calls == 1

    def test_restarts_even_without_pid_file(self, tmp_path, monkeypatch):
        import run_launcher

        manager = _FakeManager()
        monkeypatch.setattr(
            run_launcher, "wait_until_healthy", lambda **kw: (True, "")
        )
        monkeypatch.setattr(run_launcher, "PID_FILE", tmp_path / "missing.pid")

        healthy, reason = run_launcher.recover_dashboard(manager)

        assert healthy is True
        assert reason == ""
        assert manager.start_calls == 1


class TestConsoleShow:
    def test_show_renders_dashboard_status(self, monkeypatch):
        lines: list[str] = []
        monkeypatch.setattr(db, "clear", lambda: None)
        monkeypatch.setattr(db.logger, "info", lambda msg: lines.append(str(msg)))
        db.show({"api": True, "bot": False, "dashboard": True})
        assert any("Streamlit Dash" in l and "🟢 Running" in l for l in lines)

    def test_show_tolerates_missing_dashboard_key(self, monkeypatch):
        lines: list[str] = []
        monkeypatch.setattr(db, "clear", lambda: None)
        monkeypatch.setattr(db.logger, "info", lambda msg: lines.append(str(msg)))
        db.show({"api": True, "bot": False})  # legacy 2-key status dict
        assert any("Streamlit Dash" in l and "🔴 Stopped" in l for l in lines)


# ───────────────────────────────────────────────────────────────────
# ProcessManager: all three services
# ───────────────────────────────────────────────────────────────────


class TestProcessManagerDashboard:
    def test_start_dashboard_sets_process(self, monkeypatch):
        monkeypatch.setattr(pm, "dashboard_running", lambda: False)
        monkeypatch.setattr(pm, "start_dashboard", lambda: object())
        manager = pm.ProcessManager()
        assert manager.start_dashboard() is True
        assert manager.dashboard_process is not None

    def test_start_dashboard_skips_when_running(self, monkeypatch):
        monkeypatch.setattr(pm, "dashboard_running", lambda: True)
        manager = pm.ProcessManager()
        assert manager.start_dashboard() is True
        assert manager.dashboard_process is None

    def test_start_all_starts_api_bot_dashboard(self, monkeypatch):
        calls: list[str] = []
        api_up = {"up": False}
        monkeypatch.setattr(pm, "api_running", lambda: api_up["up"])

        def _start_api():
            calls.append("api")
            api_up["up"] = True  # simulate successful API startup
            return object()

        monkeypatch.setattr(pm, "start_api", _start_api)
        monkeypatch.setattr(
            pm, "start_bot", lambda: calls.append("bot") or object()
        )
        monkeypatch.setattr(pm, "dashboard_running", lambda: False)
        monkeypatch.setattr(
            pm, "start_dashboard", lambda: calls.append("dashboard") or object()
        )

        manager = pm.ProcessManager()
        assert manager.start_all() is True
        assert calls == ["api", "bot", "dashboard"]

    def test_stop_all_stops_all_three(self, monkeypatch):
        stopped: list[str] = []
        monkeypatch.setattr(pm, "stop_bot", lambda p: stopped.append("bot"))
        monkeypatch.setattr(pm, "stop_api", lambda p: stopped.append("api"))
        monkeypatch.setattr(
            pm, "stop_dashboard", lambda p: stopped.append("dashboard")
        )
        pm.ProcessManager().stop_all()
        assert stopped == ["bot", "api", "dashboard"]

    def test_status_covers_all_three_services(self, monkeypatch):
        monkeypatch.setattr(pm, "api_running", lambda: True)
        monkeypatch.setattr(pm, "dashboard_running", lambda: True)
        manager = pm.ProcessManager()
        manager.bot_process = object()
        status = manager.status()
        assert set(status) == {"api", "bot", "dashboard"}
        assert status["api"] is True
        assert status["bot"] is True
        assert status["dashboard"] is True


# ───────────────────────────────────────────────────────────────────
# run_launcher corpus refresher thread
# ───────────────────────────────────────────────────────────────────


class TestCorpusRefresher:
    def test_disabled_returns_none(self, monkeypatch):
        import launcher.config as launcher_config

        monkeypatch.setattr(launcher_config, "CORPUS_REFRESH_ENABLED", False)

        import run_launcher

        assert run_launcher.start_corpus_refresher() is None

    def test_enabled_starts_daemon_thread_with_configured_schedule(
        self, monkeypatch
    ):
        import launcher.config as launcher_config
        from scripts import refresh_corpus

        monkeypatch.setattr(launcher_config, "CORPUS_REFRESH_ENABLED", True)
        monkeypatch.setattr(launcher_config, "CORPUS_REFRESH_AT", "16:00")
        monkeypatch.setattr(launcher_config, "CORPUS_REFRESH_INTERVAL_HOURS", 24.0)
        monkeypatch.setattr(launcher_config, "CORPUS_REFRESH_DAYS", 14)
        monkeypatch.setattr(launcher_config, "CORPUS_REFRESH_DRY_RUN", False)

        captured: dict = {}

        def fake_run_scheduler(**kwargs):
            captured.update(kwargs)
            kwargs["stop_event"].set()  # end the loop immediately

        def fake_main(argv):
            captured["argv"] = argv
            return 0

        monkeypatch.setattr(refresh_corpus, "run_scheduler", fake_run_scheduler)
        monkeypatch.setattr(refresh_corpus, "main", fake_main)

        import run_launcher

        result = run_launcher.start_corpus_refresher()
        assert result is not None
        thread, stop_event = result
        thread.join(timeout=5)
        assert not thread.is_alive()
        assert captured["at_time"] == "16:00"
        assert captured["interval_hours"] == 24.0
        assert captured["stop_event"] is stop_event

        # The on_run callback drives the real refresh with the configured
        # argv (no --dry-run, so the default 14-day scan).
        captured.pop("argv", None)
        captured["on_run"]()
        assert captured["argv"] == ["--days", "14"]

    def test_dry_run_flag_wired_into_refresh_argv(self, monkeypatch):
        import launcher.config as launcher_config
        from scripts import refresh_corpus

        monkeypatch.setattr(launcher_config, "CORPUS_REFRESH_ENABLED", True)
        monkeypatch.setattr(launcher_config, "CORPUS_REFRESH_DRY_RUN", True)

        captured: dict = {}

        def fake_run_scheduler(**kwargs):
            kwargs["stop_event"].set()
            captured["on_run"] = kwargs["on_run"]

        def fake_main(argv):
            captured["argv"] = argv
            return 0

        monkeypatch.setattr(refresh_corpus, "run_scheduler", fake_run_scheduler)
        monkeypatch.setattr(refresh_corpus, "main", fake_main)

        import run_launcher

        result = run_launcher.start_corpus_refresher()
        assert result is not None
        thread, _stop = result
        thread.join(timeout=5)
        captured["on_run"]()
        assert captured["argv"] == ["--days", "14", "--dry-run"]

    def test_refresher_thread_is_daemon(self, monkeypatch):
        """The thread must be a daemon so process exit never hangs on it."""
        import launcher.config as launcher_config
        from scripts import refresh_corpus

        monkeypatch.setattr(launcher_config, "CORPUS_REFRESH_ENABLED", True)
        monkeypatch.setattr(refresh_corpus, "run_scheduler", lambda **kw: None)

        import run_launcher

        result = run_launcher.start_corpus_refresher()
        assert result is not None
        thread, stop_event = result
        try:
            assert thread.daemon is True
            assert thread.name == "corpus-refresher"
        finally:
            stop_event.set()
            thread.join(timeout=5)


class TestCorpusRefreshConfig:
    def test_garbage_env_values_fall_back_to_defaults(self, monkeypatch):
        """A typo'd env var must never crash the launcher at import — the
        numeric knobs fall back to their defaults instead."""
        import importlib

        import launcher.config as launcher_config

        monkeypatch.setenv("CORPUS_REFRESH_DAYS", "abc")
        monkeypatch.setenv("CORPUS_REFRESH_INTERVAL_HOURS", "abc")
        importlib.reload(launcher_config)
        try:
            assert launcher_config.CORPUS_REFRESH_DAYS == 14
            assert launcher_config.CORPUS_REFRESH_INTERVAL_HOURS == 24.0
        finally:
            # Restore the module to default-loaded state for other tests.
            monkeypatch.delenv("CORPUS_REFRESH_DAYS", raising=False)
            monkeypatch.delenv("CORPUS_REFRESH_INTERVAL_HOURS", raising=False)
            importlib.reload(launcher_config)

    def test_negative_days_clamped_to_one(self, monkeypatch):
        import importlib

        import launcher.config as launcher_config

        monkeypatch.setenv("CORPUS_REFRESH_DAYS", "-5")
        importlib.reload(launcher_config)
        try:
            assert launcher_config.CORPUS_REFRESH_DAYS == 1
        finally:
            monkeypatch.delenv("CORPUS_REFRESH_DAYS", raising=False)
            importlib.reload(launcher_config)
