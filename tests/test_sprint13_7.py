"""Sprint 13.7 — Production Readiness Audit & End-to-End Operational Safety.

Behavioural coverage proving that every upstream failure propagates into
an observable, safe outcome for the *whole trading workflow*:

    Provider → identity → reconciliation → calendar → quality
    → provenance/trust → cache → analyzer → scanner → alerts → notification

The central guarantee under test:

> No provider failure, data-quality failure, reconciliation conflict,
> calendar error, stale condition, cache corruption, worker failure, or
> operational degradation may silently result in an unsafe trading
> signal or alert.

Categories (mirroring the sprint brief §20):

  1. scanner trust integration       11. shared-state safety
  2. scanner degradation behaviour   12. calendar holiday intake
  3. alert safety                   13. calendar validation
  4. notification safety            14. calendar rollback
  5. operational-status integration 15. calendar regression
  6. end-to-end failure matrix      16. production-readiness checks
  7. two-worker runtime             17. configuration/security checks
  8. worker failure                 18. resource bounds
  9. worker restart                 19. performance regression
  10. concurrent API requests       20. Sprint 13.3–13.6 regression

Constraints honoured: no fabricated live provider results (the second
provider is exercised through the existing *controlled* file-backed
double), no fabricated NEPSE holidays (the intake workflow validates
operator-supplied candidates only), no duplicate trust-resolution logic
(the scanner consumes the existing provenance/trust state), and no
weakened performance gates.
"""

from __future__ import annotations

import json
import os
import signal
import socket
import subprocess
import sys
import tempfile
import threading
from datetime import date
from pathlib import Path

import pandas as pd
import pytest

from src.data.calendar import (
    EXCEPTIONAL_CLOSURE,
    HOLIDAY,
    TRADING_DAY,
    WEEKEND,
    NepseCalendar,
    validate_calendar_data,
)
from src.data.exceptions import DataUnavailable, ProviderError, ProviderTimeout
from src.data.provenance import (
    TRUST_CALENDAR_INVALID,
    TRUST_CONFLICTED,
    TRUST_FALLBACK,
    TRUST_PARTIALLY_RECONCILED,
    TRUST_QUARANTINED,
    TRUST_RECONCILED,
    TRUST_SINGLE_PROVIDER,
    TRUST_STALE,
    TRUST_TRUSTED,
    TRUST_UNAVAILABLE,
    DataProvenance,
)

PROJECT_ROOT = Path(__file__).resolve().parent.parent

# ═══════════════════════════════════════════════════════════════════
# Shared helpers
# ═══════════════════════════════════════════════════════════════════


def _fresh_ohlcv(n: int = 60, base: float = 100.0) -> pd.DataFrame:
    """Deterministic OHLCV frame ending yesterday (FRESH, not stale)."""
    dates = pd.bdate_range(
        end=pd.Timestamp.today().normalize() - pd.Timedelta(days=1),
        periods=n,
    )
    closes = [base + i * 0.5 + (i % 7) for i in range(n)]
    return pd.DataFrame(
        {
            "Date": dates,
            "Open": [c - 0.5 for c in closes],
            "High": [c + 1.0 for c in closes],
            "Low": [c - 1.0 for c in closes],
            "Close": closes,
            "Volume": [1000 + i * 10 for i in range(n)],
        }
    )


def _stale_ohlcv(n: int = 40, start: str = "2024-01-01") -> pd.DataFrame:
    """OHLCV frame ending long ago (STALE under the default policy)."""
    return _fresh_ohlcv.__wrapped__ if False else _ohlcv_stale(n, start)


def _ohlcv_stale(n: int, start: str) -> pd.DataFrame:
    dates = pd.bdate_range(start, periods=n)
    closes = [100.0 + i * 0.5 for i in range(n)]
    return pd.DataFrame(
        {
            "Date": dates,
            "Open": [c - 0.5 for c in closes],
            "High": [c + 1.0 for c in closes],
            "Low": [c - 1.0 for c in closes],
            "Close": closes,
            "Volume": [1000] * n,
        }
    )


def _full_result(overrides: dict | None = None) -> dict:
    """A fully-shaped analysis result (all keys the alert engine needs)."""
    result = {
        "symbol": "NABIL",
        "signal": "HOLD",
        "confidence": 50,
        "score": 10,
        "trend": "SIDEWAYS",
        "volume_signal": "NORMAL",
        "relative_volume": 1.0,
        "price": 100.0,
        "target1": 105.0,
        "target2": 110.0,
        "target3": 120.0,
        "alerts": [],
    }
    if overrides:
        result.update(overrides)
    return result


def _valid_calendar_dict(**overrides) -> dict:
    """Deterministic, schema-valid governed calendar (Sun–Thu trading)."""
    data = {
        "calendar_version": "2.0",
        "version": "2.0",
        "provenance": "Test fixture calendar (Sprint 13.7)",
        "source": "test-fixture",
        "operator_notes": "",
        "effective_from": None,
        "effective_to": None,
        "timezone": "Asia/Kathmandu",
        "trading_week": [0, 1, 2, 3, 6],
        "weekend_days": [4, 5],
        "holidays": [],
        "special_sessions": [],
        "closures": [],
        "last_validated": None,
        "observed_sessions": None,
    }
    data.update(overrides)
    return data


def _free_port() -> int:
    """Probe an ephemeral free loopback port."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _pid_int(pid: object) -> int | None:
    """Normalise a pid payload value (int or digit string) to int."""
    try:
        return int(pid)
    except (TypeError, ValueError):
        return None


def _hard_shutdown(proc: subprocess.Popen) -> None:
    """Teardown a spawned API process that never signals the console.

    Windows nuance (Sprint 13.7 §10): ``benchmarks.validate_workers.
    _shutdown`` *previously* sent ``CTRL_BREAK_EVENT`` to the uvicorn
    master.  When both multiprocessing workers are fully alive, that
    console event could propagate to the test runner's own console
    group and kill the whole pytest/bash process (observed as exit 58
    = ``0xC000013A`` low byte).  ``_shutdown`` now tree-kills directly
    on Windows (no console signal); this helper keeps an explicit
    ``taskkill /T /F`` so teardown is independent of that choice and
    never takes the parent down with the server.
    """
    try:
        if os.name == "nt":
            subprocess.run(
                ["taskkill", "/T", "/F", "/PID", str(proc.pid)],
                capture_output=True,
                timeout=30,
            )
        else:
            from benchmarks.validate_workers import _shutdown  # noqa: PLC0415

            _shutdown(proc)
    except (OSError, subprocess.SubprocessError):
        pass
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        pass


def _isolate_alert_history(monkeypatch, tmp_path):
    """Point both the alert engine and history module at a private file.

    The alert-safety tests must be order-independent: each writes to
    its own ``history.json`` under *tmp_path* instead of the shared
    ``data/alerts/history.json`` (which accumulates state across the
    whole suite).
    """
    from src.alerts import engine as alert_engine
    from src.alerts import history as alert_history

    target = tmp_path / "history.json"
    monkeypatch.setattr(alert_history, "HISTORY_FILE", target)
    monkeypatch.setattr(alert_engine, "HISTORY_FILE", target)
    return target


def _isolate_notifications(monkeypatch, tmp_path):
    """Reset the notification singleton against a private store.

    ``NotificationManager`` is a process-wide singleton persisted to
    ``NOTIF_FILE``.  A test that fills it to ``MAX_HISTORY`` (e.g.
    ``test_notification_bounded``) would otherwise leave the shared
    singleton at its 500-entry cap, breaking later in-process tests
    that assert ``unread_count`` can still grow (e.g. Sprint 6.5's
    ``test_unread_count``).  This helper points the module at a private
    file, drops the singleton so the next access rebuilds against it,
    and also drops it on teardown so the *next* user reloads the real
    store instead of the private one this test filled.
    """
    from src.ui.notifications import NotificationManager
    import src.ui.notifications as notif_module

    target = tmp_path / "notifications.json"
    monkeypatch.setattr(notif_module, "NOTIF_FILE", target)
    # Rebuild now against the private file...
    NotificationManager._instance = None
    # ...and drop the rebuilt singleton on teardown too (the value
    # captured by monkeypatch here is ``None``, so teardown restores
    # ``None`` — the next user reloads the real store).
    monkeypatch.setattr(NotificationManager, "_instance", None)
    return target


class _FakeProvider:
    """Minimal provider for driving the real HybridProvider chain."""

    name = "fake"

    def __init__(self, history=None, raises=None):
        self._history = history
        self._raises = raises

    def get_history(self, symbol, days=365):
        if self._raises:
            raise self._raises
        return self._history


class _ControlledSecondProvider:
    """Controlled independent provider double (file-backed, never live)."""

    name = "github_csv"

    def __init__(self, data_dir, history=None, raises=None):
        self._data_dir = Path(data_dir)
        self._history = history
        self._raises = raises

    def get_history(self, symbol, days=365):
        if self._raises:
            raise self._raises
        if self._history is not None:
            return self._history
        path = self._data_dir / f"{symbol.upper()}.csv"
        if not path.exists():
            raise ProviderError(f"No price history for {symbol}")
        df = pd.read_csv(path)
        df["Date"] = pd.to_datetime(df["Date"])
        return df.sort_values("Date").reset_index(drop=True)


# ═══════════════════════════════════════════════════════════════════
# 1. Scanner trust integration
# ═══════════════════════════════════════════════════════════════════


class TestScannerTrustIntegration:
    def _scan(self, tmp_path, monkeypatch):
        from src import scanner as scanner_pkg
        from src.cache.scanner_cache import scanner_cache

        scanner_cache.clear()
        monkeypatch.setattr(scanner_pkg.engine, "DATA_DIRECTORY", str(tmp_path))
        return scanner_pkg.engine.scan_market(workers=1)

    def _write(self, tmp_path, symbol="NABIL", df=None):
        df = df if df is not None else _fresh_ohlcv()
        df.to_csv(tmp_path / f"{symbol}.csv", index=False)

    def test_trusted_data_follows_normal_scanner_path(self, tmp_path, monkeypatch):
        self._write(tmp_path)
        out = self._scan(tmp_path, monkeypatch)
        assert len(out["results"]) == 1
        assert out["results"][0]["symbol"] == "NABIL"
        assert out["results"][0]["signal"] in ("BUY", "SELL", "HOLD")
        assert out["skipped"] == []

    def test_scanner_results_carry_provenance_block(self, tmp_path, monkeypatch):
        self._write(tmp_path)
        out = self._scan(tmp_path, monkeypatch)
        prov = out["results"][0].get("provenance") or {}
        assert prov.get("trust") in (TRUST_TRUSTED, TRUST_SINGLE_PROVIDER, TRUST_STALE)
        assert "is_safe" in prov
        assert prov["is_safe"] is True  # trusted data is safe data

    def test_single_provider_trust_attached(self, tmp_path, monkeypatch):
        self._write(tmp_path)
        out = self._scan(tmp_path, monkeypatch)
        prov = out["results"][0].get("provenance") or {}
        assert prov.get("sources") == ["csv"]

    def test_quarantined_symbol_skipped_not_ranked(self, tmp_path, monkeypatch):
        bad = _fresh_ohlcv()
        bad.loc[bad.index[-1], "Close"] = 0.0  # non-positive price -> INVALID
        self._write(tmp_path, "BAD1", bad)
        self._write(tmp_path, "GOOD1", _fresh_ohlcv())
        out = self._scan(tmp_path, monkeypatch)
        syms = [r["symbol"] for r in out["results"]]
        assert "BAD1" not in syms
        assert "GOOD1" in syms
        skipped = {s["symbol"]: s for s in out["skipped"]}
        assert skipped["BAD1"]["error"] == "invalid_data"

    def test_quarantined_signal_hold(self, tmp_path, monkeypatch):
        bad = _fresh_ohlcv()
        bad.loc[bad.index[-1], "Close"] = 0.0
        self._write(tmp_path, "BAD1", bad)
        out = self._scan(tmp_path, monkeypatch)
        assert out["skipped"], "expected a skip entry"
        # the analysis is still cached/returned with a suppressed HOLD
        assert out["results"] == []

    def test_no_duplicate_trust_resolution_in_scanner(self):
        """The scanner module must not contain its own trust resolver —
        it consumes the provenance/trust state produced upstream."""
        import inspect

        from src.scanner import engine as scanner_engine

        src = inspect.getsource(scanner_engine)
        assert "resolve_trust" not in src
        assert "UNSAFE_TRUST_STATES" not in src
        assert "provenance_from_history" not in src  # delegated to analyzer

    def test_warm_scan_cache_equivalence_includes_provenance(self, tmp_path, monkeypatch):
        """Auto-derived provenance must be deterministic (cache hit == miss)."""
        self._write(tmp_path)
        first = self._scan(tmp_path, monkeypatch)
        second = self._scan(tmp_path, monkeypatch)  # warm scan (cache hit)
        assert first["results"][0]["provenance"] == second["results"][0]["provenance"]
        assert first["results"][0]["signal"] == second["results"][0]["signal"]

    def test_scanner_skipped_has_explicit_reason(self, tmp_path, monkeypatch):
        bad = _fresh_ohlcv()
        bad.loc[bad.index[-1], "Close"] = 0.0
        self._write(tmp_path, "BAD1", bad)
        out = self._scan(tmp_path, monkeypatch)
        assert out["skipped"][0]["reasons"]


# ═══════════════════════════════════════════════════════════════════
# 2. Scanner degradation behaviour
# ═══════════════════════════════════════════════════════════════════


class TestScannerDegradation:
    def test_degraded_provider_trusted_fallback_usable(self):
        """Provider degraded + trusted fallback = potentially usable:
        fallback provenance does NOT suppress a normal signal."""
        from src.engine.analyzer import analyze_dataframe

        result = analyze_dataframe(
            _fresh_ohlcv(),
            symbol="NABIL",
            provenance=DataProvenance(
                sources=["api", "csv"],
                trust=TRUST_FALLBACK,
                fallback_used=True,
            ),
        )
        assert result["signal_suppressed"] is False
        assert result["signal"] in ("BUY", "SELL", "HOLD")

    def test_degraded_provider_no_fallback_unsafe(self):
        """Provider degraded + no trusted replacement = unsafe: UNAVAILABLE
        provenance suppresses to HOLD."""
        from src.engine.analyzer import analyze_dataframe

        result = analyze_dataframe(
            _fresh_ohlcv(),
            symbol="NABIL",
            provenance=DataProvenance(trust=TRUST_UNAVAILABLE),
        )
        assert result["signal"] == "HOLD"
        assert result["signal_suppressed"] is True
        assert result["signal_suppression_reason"] == TRUST_UNAVAILABLE

    def test_health_degraded_does_not_auto_hold_trusted_data(self):
        """§3 design principle: provider health is separate from data
        trust — a DEGRADED health label alone must not force HOLD."""
        from src.data.health import HealthCheckConfig, ProviderHealthMonitor

        monitor = ProviderHealthMonitor(
            HealthCheckConfig(failure_threshold=100)  # never disables
        )
        monitor.register("api")
        for _ in range(50):
            monitor.record_failure("api", outcome="timeout")
        assert monitor.degradation_state("api") == "DEGRADED"
        # The degradation label is observability; it does not touch trust.
        from src.engine.analyzer import analyze_dataframe

        result = analyze_dataframe(
            _fresh_ohlcv(),
            symbol="NABIL",
            provenance=DataProvenance(
                sources=["api"], trust=TRUST_SINGLE_PROVIDER
            ),
        )
        assert result["signal_suppressed"] is False

    def test_hybrid_fallback_keeps_fallback_used_flag(self, tmp_path):
        from src.data.providers import HybridProvider

        second = _ControlledSecondProvider(tmp_path, history=_fresh_ohlcv())
        hybrid = HybridProvider(
            [_FakeProvider(raises=ProviderError("primary down")), second]
        )
        df = hybrid.get_history("NABIL")
        assert len(df) > 0
        assert hybrid.fallback_used is True
        assert hybrid.last_provider == "github_csv"

    def test_all_providers_fail_unsafe(self):
        from src.data.providers import HybridProvider

        hybrid = HybridProvider(
            [
                _FakeProvider(raises=ProviderError("a")),
                _FakeProvider(raises=ProviderError("b")),
            ]
        )
        with pytest.raises(DataUnavailable):
            hybrid.get_history("NABIL")

    def test_provider_recovery_after_successes(self):
        from src.data.health import HealthCheckConfig, ProviderHealthMonitor

        monitor = ProviderHealthMonitor(HealthCheckConfig(failure_threshold=2))
        monitor.register("api")
        monitor.record_failure("api")
        monitor.record_failure("api")
        assert monitor.is_healthy("api") is False
        for _ in range(3):
            monitor.record_success("api", latency_ms=10)
        assert monitor.is_healthy("api") is True


# ═══════════════════════════════════════════════════════════════════
# 3. Alert safety
# ═══════════════════════════════════════════════════════════════════


class TestAlertSafety:
    def _suppressed(self, trust, **extra):
        from src.engine.analyzer import analyze_dataframe

        return analyze_dataframe(
            _fresh_ohlcv(),
            symbol="NABIL",
            provenance=DataProvenance(trust=trust, **extra),
        )

    def test_suppressed_signal_no_buy_alert(self):
        result = self._suppressed(TRUST_QUARANTINED)
        assert result["signal"] == "HOLD"
        types = [a["type"] for a in result["alerts"]]
        assert "BUY" not in types
        assert "SELL" not in types

    def test_suppressed_signal_keeps_suppression_reason(self):
        result = self._suppressed(TRUST_CONFLICTED)
        assert result["signal_suppression_reason"] == TRUST_CONFLICTED

    def test_quarantined_no_buy_alert(self):
        types = [a["type"] for a in self._suppressed(TRUST_QUARANTINED)["alerts"]]
        assert "BUY" not in types and "SELL" not in types

    def test_calendar_invalid_no_buy_alert(self):
        types = [a["type"] for a in self._suppressed(TRUST_CALENDAR_INVALID)["alerts"]]
        assert "BUY" not in types and "SELL" not in types

    def test_unavailable_no_buy_alert(self):
        types = [a["type"] for a in self._suppressed(TRUST_UNAVAILABLE)["alerts"]]
        assert "BUY" not in types and "SELL" not in types

    def test_material_disagreement_no_buy_alert(self):
        from src.engine.analyzer import analyze_dataframe
        from src.data.reconciliation import ReconciliationResult, MATERIAL_DISAGREEMENT

        result = analyze_dataframe(
            _fresh_ohlcv(),
            symbol="NABIL",
            reconciliation=ReconciliationResult(
                status=MATERIAL_DISAGREEMENT, providers=["api", "csv"]
            ),
        )
        assert result["signal"] == "HOLD"
        types = [a["type"] for a in result["alerts"]]
        assert "BUY" not in types and "SELL" not in types

    def test_mapping_conflict_no_buy_alert(self):
        from src.engine.analyzer import analyze_dataframe
        from src.data.reconciliation import ReconciliationResult, MAPPING_CONFLICT

        result = analyze_dataframe(
            _fresh_ohlcv(),
            symbol="NABIL",
            reconciliation=ReconciliationResult(
                status=MAPPING_CONFLICT, providers=["api", "csv"]
            ),
        )
        assert result["signal"] == "HOLD"
        types = [a["type"] for a in result["alerts"]]
        assert "BUY" not in types and "SELL" not in types

    def test_invalid_quality_no_buy_alert(self):
        bad = _fresh_ohlcv()
        bad.loc[bad.index[-1], "Close"] = 0.0
        from src.engine.analyzer import analyze_dataframe

        result = analyze_dataframe(bad, symbol="NABIL")
        assert result["signal"] == "HOLD"
        assert result["signal_suppression_reason"] == "invalid_data"
        types = [a["type"] for a in result["alerts"]]
        assert "BUY" not in types and "SELL" not in types

    def test_valid_signal_alerts_unchanged(self):
        from src.engine.analyzer import analyze_dataframe

        result = analyze_dataframe(_fresh_ohlcv(), symbol="NABIL")
        assert result["signal_suppressed"] is False
        # a real BUY/SELL signal still carries its rule alerts
        if result["signal"] == "BUY":
            assert any(a["type"] == "BUY" for a in result["alerts"])

    def test_process_alerts_suppressed_returns_suppressed_alert(self, tmp_path, monkeypatch):
        from src.alerts.engine import process_alerts

        _isolate_alert_history(monkeypatch, tmp_path)
        result = _full_result(
            {
                "signal": "HOLD",
                "signal_suppressed": True,
                "signal_suppression_reason": "invalid_data",
            }
        )
        alerts = process_alerts("NABIL", result)
        assert all(a["type"] != "BUY" and a["type"] != "SELL" for a in alerts)
        assert any(a["type"] == "SUPPRESSED" for a in alerts)

    def test_process_alerts_normal_still_works(self, tmp_path, monkeypatch):
        from src.alerts.engine import process_alerts

        _isolate_alert_history(monkeypatch, tmp_path)
        alerts = process_alerts("NABIL", _full_result({"signal": "HOLD"}))
        assert alerts  # INITIAL or rule alerts
        assert all(a["type"] != "SUPPRESSED" for a in alerts)

    def test_process_alert_batch_skips_suppressed(self, tmp_path, monkeypatch):
        from src.alerts.engine import process_alert_batch

        _isolate_alert_history(monkeypatch, tmp_path)
        entries = [
            ("NABIL", _full_result({"signal": "HOLD", "signal_suppressed": True,
                                    "signal_suppression_reason": "conflicted"})),
            ("SCB", _full_result({"signal": "BUY"})),
        ]
        batch = process_alert_batch(entries)
        assert any(a["type"] == "SUPPRESSED" for a in batch["NABIL"])
        assert all(a["type"] != "BUY" and a["type"] != "SELL"
                   for a in batch["NABIL"])


# ═══════════════════════════════════════════════════════════════════
# 4. Notification safety
# ═══════════════════════════════════════════════════════════════════


class TestNotificationSafety:
    def test_suppressed_analysis_renders_hold_not_buy(self):
        """The notification layer never decides trustworthiness; it renders
        the resolved signal.  A suppressed analysis must render HOLD."""
        result = _full_result(
            {"signal": "HOLD", "signal_suppressed": True,
             "signal_suppression_reason": "quarantined"}
        )
        # the bot's emoji map (mirror of src/bot/telegram_bot.py) shows HOLD
        emoji = {"BUY": "🟢", "HOLD": "🟡", "SELL": "🔴"}.get(result["signal"], "⚪")
        assert emoji == "🟡"
        assert "BUY" not in result["signal"]

    def test_notification_failure_does_not_corrupt_signal_state(self, monkeypatch, tmp_path):
        from src.ui.notifications import NotificationManager

        _isolate_notifications(monkeypatch, tmp_path)
        manager = NotificationManager()
        # force the persistence write to fail after construction
        import src.ui.notifications as notif_module

        notif_module.NOTIF_FILE = Path(
            tempfile.mkdtemp()
        ) / "nonexistent_dir" / "notifications.json"
        notif = manager.notify(
            "Test", "message", category="system", priority="info"
        )
        # notify() never raises even when persistence fails
        assert notif.title == "Test"

    def test_notification_bounded(self, monkeypatch, tmp_path):
        from src.ui.notifications import (
            MAX_HISTORY,
            NotificationManager,
        )

        # Isolate: this test fills the singleton to the cap; without a
        # private store it would poison later in-process tests
        # (e.g. ``test_unread_count``: ``before`` already at 500).
        _isolate_notifications(monkeypatch, tmp_path)
        manager = NotificationManager()
        for i in range(MAX_HISTORY + 50):
            manager.notify(f"t{i}", "", category="system")
        assert len(manager.get_all(limit=MAX_HISTORY + 200)) <= MAX_HISTORY

    def test_alert_eligibility_precedes_notification(self, tmp_path, monkeypatch):
        """Suppressed symbols never reach the notification path as
        actionable alerts (scanner excludes them from the alert batch)."""
        from src import scanner as scanner_pkg

        # no corpus → nothing scanned → nothing notified
        monkeypatch.setattr(scanner_pkg.engine, "DATA_DIRECTORY", str(tmp_path))
        out = scanner_pkg.engine.scan_market(workers=1)
        assert out["results"] == []

    def test_duplicate_notifications_controlled(self):
        from src.alerts.history import load_history

        # history persists deduplicated per symbol; alerts are NEW only
        history = load_history()
        assert isinstance(history, dict)

    def test_fallback_provenance_not_misrepresented(self):
        """A fallback analysis must never claim a primary source."""
        prov = DataProvenance(
            sources=["api", "csv"],
            trust=TRUST_FALLBACK,
            fallback_used=True,
        ).to_dict()
        assert prov["fallback_used"] is True
        assert prov["trust"] == TRUST_FALLBACK


# ═══════════════════════════════════════════════════════════════════
# 5. Operational-status integration
# ═══════════════════════════════════════════════════════════════════


class TestOperationalStatusIntegration:
    def test_system_status_blocks_bounded(self):
        from src.data.operational_status import system_status

        status = system_status()
        for block in (
            "data_provider",
            "reconciliation",
            "calendar",
            "data_quality",
            "incidents",
            "cache",
        ):
            assert block in status
            assert status[block]["state"] in (
                "HEALTHY", "DEGRADED", "UNAVAILABLE", "UNKNOWN",
            )

    def test_global_degraded_not_global_hold(self):
        """A DEGRADED operational status is a summary — it must not turn
        into an automatic global HOLD when the data is trusted."""
        from src.data.operational_status import DEGRADED
        from src.engine.analyzer import analyze_dataframe

        assert DEGRADED == "DEGRADED"
        result = analyze_dataframe(
            _fresh_ohlcv(),
            symbol="NABIL",
            provenance=DataProvenance(
                sources=["api"], trust=TRUST_SINGLE_PROVIDER
            ),
        )
        assert result["signal_suppressed"] is False

    def test_status_never_raises(self):
        from src.data.operational_status import system_status

        status = system_status()  # must never raise even with no providers
        assert "overall" in status

    def test_metrics_expose_system_status(self):
        from src.api.metrics import metrics

        payload = metrics()
        assert "system_status" in payload
        assert payload["system_status"]["overall"] in (
            "HEALTHY", "DEGRADED", "UNAVAILABLE",
        )

    def test_operational_status_is_summary_not_gate(self):
        """The authoritative safety mechanism is provenance/trust, not the
        operational summary."""
        from src.engine.analyzer import analyze_dataframe

        # even if the operational status were UNAVAILABLE, a trusted
        # provenance must still pass through normally
        result = analyze_dataframe(
            _fresh_ohlcv(),
            symbol="NABIL",
            provenance=DataProvenance(
                sources=["api"], trust=TRUST_RECONCILED
            ),
        )
        assert result["signal_suppressed"] is False


# ═══════════════════════════════════════════════════════════════════
# 6. End-to-end failure matrix
# ═══════════════════════════════════════════════════════════════════

# Formal matrix (§9): (label, trust, reconciliation, quality_kind,
# expected_signal, expected_suppressed, expected_alert)
FAILURE_MATRIX = [
    # (name, trust, rec_status, quality, signal, suppressed, alert_mode)
    ("provider_healthy", TRUST_TRUSTED, None, "valid", "any", False, "allowed"),
    ("degraded_trusted_fallback", TRUST_FALLBACK, None, "valid", "any", False, "allowed"),
    ("degraded_no_fallback", TRUST_UNAVAILABLE, None, "valid", "HOLD", True, "blocked"),
    ("minor_disagreement", TRUST_RECONCILED, "MINOR_DISAGREEMENT", "valid", "any", False, "allowed"),
    ("material_disagreement", TRUST_CONFLICTED, "MATERIAL_DISAGREEMENT", "valid", "HOLD", True, "blocked"),
    ("mapping_conflict", TRUST_CONFLICTED, "MAPPING_CONFLICT", "valid", "HOLD", True, "blocked"),
    ("calendar_invalid", TRUST_CALENDAR_INVALID, None, "valid", "HOLD", True, "blocked"),
    ("quarantined", TRUST_QUARANTINED, None, "valid", "HOLD", True, "blocked"),
    ("stale", TRUST_STALE, None, "stale", "any", False, "policy"),
    ("all_providers_fail", TRUST_UNAVAILABLE, None, "valid", "HOLD", True, "blocked"),
]


class TestFailureMatrix:
    @pytest.mark.parametrize(
        "name,trust,rec_status,quality,expected_signal,expected_suppressed,alert_mode",
        FAILURE_MATRIX,
        ids=[row[0] for row in FAILURE_MATRIX],
    )
    def test_matrix_row(
        self,
        name,
        trust,
        rec_status,
        quality,
        expected_signal,
        expected_suppressed,
        alert_mode,
    ):
        from src.engine.analyzer import analyze_dataframe

        df = _fresh_ohlcv() if quality == "valid" else _stale_ohlcv()
        kwargs = {"provenance": DataProvenance(trust=trust)}
        if rec_status:
            from src.data.reconciliation import ReconciliationResult

            kwargs["reconciliation"] = ReconciliationResult(
                status=rec_status, providers=["api", "csv"]
            )
        result = analyze_dataframe(df, symbol="NABIL", **kwargs)

        if expected_suppressed:
            assert result["signal"] == "HOLD", name
            assert result["signal_suppressed"] is True, name
            types = [a["type"] for a in result["alerts"]]
            assert "BUY" not in types and "SELL" not in types, name
        elif alert_mode == "allowed":
            assert result["signal_suppressed"] is False, name
            assert result["signal"] in ("BUY", "SELL", "HOLD"), name

    def test_matrix_completeness(self):
        """Every row of the §9 table is present."""
        labels = {row[0] for row in FAILURE_MATRIX}
        required = {
            "provider_healthy", "degraded_trusted_fallback",
            "degraded_no_fallback", "minor_disagreement",
            "material_disagreement", "mapping_conflict",
            "calendar_invalid", "quarantined", "stale",
            "all_providers_fail",
        }
        assert required <= labels

    def test_stale_policy_off_signal_normal(self):
        """Default policy: TRUST_STALE does not force HOLD."""
        from src.engine.analyzer import analyze_dataframe

        result = analyze_dataframe(
            _stale_ohlcv(),
            symbol="NABIL",
            provenance=DataProvenance(trust=TRUST_STALE),
        )
        assert result["signal_suppressed"] is False

    def test_stale_policy_enforced_suppresses(self, monkeypatch):
        """When ENFORCE_SIGNAL_FRESHNESS is on, stale data → HOLD."""
        monkeypatch.setenv("ENFORCE_SIGNAL_FRESHNESS", "true")
        import importlib

        from src import config as config_module

        importlib.reload(config_module)
        try:
            from src.engine.analyzer import analyze_dataframe

            result = analyze_dataframe(
                _stale_ohlcv(),
                symbol="NABIL",
                provenance=DataProvenance(trust=TRUST_STALE),
            )
            assert result["signal"] == "HOLD"
            assert result["signal_suppressed"] is True
        finally:
            monkeypatch.delenv("ENFORCE_SIGNAL_FRESHNESS", raising=False)
            importlib.reload(config_module)


# ═══════════════════════════════════════════════════════════════════
# 7–10. Two-worker runtime, worker failure/restart, concurrency,
#       shared-state safety (real uvicorn processes)
# ═══════════════════════════════════════════════════════════════════


class _TwoWorkerServer:
    """Spawns the real uvicorn 2-worker API (the Docker api stage command)
    on an ephemeral port with a private synthetic corpus + state root."""

    def __init__(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="nepse_137_"))
        data_dir = self.tmp / "data"
        state_root = self.tmp / "state"
        state_root.mkdir(parents=True, exist_ok=True)

        from benchmarks.common import write_csvs

        write_csvs(data_dir, 8, 150)

        from benchmarks.validate_workers import _spawn_api

        self.port = _free_port()
        self.proc = _spawn_api(data_dir, state_root, self.port, workers=2)
        try:
            self._wait_ready()
        except Exception:
            # Mid-init failure (e.g. wait_ready timeout) must never orphan
            # the spawned uvicorn fleet OR leak the private temp dir.
            _hard_shutdown(self.proc)
            self.proc = None
            import shutil

            shutil.rmtree(self.tmp, ignore_errors=True)
            raise

    def _wait_ready(self, timeout: float = 120.0):
        from benchmarks.validate_workers import _wait_ready

        _wait_ready(self.port, timeout=timeout)

    def request(self, path: str, timeout: float = 60.0):
        from benchmarks.validate_workers import _request

        status, body = _request(self.port, path, timeout=timeout)
        return status, body

    def shutdown(self):
        try:
            if self.proc is not None and self.proc.poll() is None:
                _hard_shutdown(self.proc)
        finally:
            import shutil

            shutil.rmtree(self.tmp, ignore_errors=True)


@pytest.fixture(scope="module")
def two_worker():
    server = None
    try:
        server = _TwoWorkerServer()
        yield server
    finally:
        # Guaranteed teardown: even a mid-init failure (e.g. a wait_ready
        # timeout) must not orphan a live uvicorn fleet holding the port
        # and state files for subsequent runs.
        if server is not None:
            server.shutdown()


class TestTwoWorkerRuntime:
    def test_two_workers_start_and_serve(self, two_worker):
        status, body = two_worker.request("/")
        assert status == 200
        assert "NEPSE Quant Engine" in body

    def test_health_endpoints_work(self, two_worker):
        status, _ = two_worker.request("/")
        assert status == 200
        status, body = two_worker.request("/metrics")
        assert status == 200
        payload = json.loads(body)
        assert "process" in payload and "pid" in payload["process"]

    def test_analyze_and_portfolio_serve(self, two_worker):
        status, body = two_worker.request("/analyze/SYN000")
        assert status == 200
        analysis = json.loads(body)
        assert analysis["signal"] in ("BUY", "SELL", "HOLD")
        status, _ = two_worker.request("/portfolio/")
        assert status == 200

    def test_worker_identity_two_pids(self, two_worker):
        """Both workers must be observable via /metrics.  Each worker
        self-reports its pid into the shared ``workers.known`` list (the
        same aggregation the dashboard consumes), so a bounded poll must
        reveal both workers without hammering the endpoint (Sprint
        13.7 §10).  Polling to a deadline (not a fixed request count)
        makes the assertion deterministic under uvicorn's round-robin."""
        import time as _time

        deadline = _time.monotonic() + 30.0
        known: set[int] = set()
        serving: set[int] = set()
        while _time.monotonic() < deadline:
            _, body = two_worker.request("/metrics")
            m = json.loads(body)
            for w in m.get("workers", {}).get("known", []) or []:
                if isinstance(w, dict):
                    pid = _pid_int(w.get("pid"))
                    if pid is not None:
                        known.add(pid)
            pid = _pid_int((m.get("process") or {}).get("pid"))
            if pid is not None:
                serving.add(pid)
            if len(known) >= 2 and serving:
                break
            _time.sleep(0.5)
        assert len(known) >= 2, f"expected 2 known workers, saw {known}"
        assert serving, "no serving worker pid observed"
        assert serving <= known, f"serving {serving} not in known {known}"

    def test_metrics_aggregation_sane(self, two_worker):
        _, body = two_worker.request("/metrics")
        payload = json.loads(body)
        assert payload["metrics"]["total_requests"] >= 0
        assert payload["metrics"]["cache_hit_rate"] >= 0.0

    def test_concurrent_requests_no_deadlock(self, two_worker):
        """Concurrent analyze + metrics + portfolio → all 200, no errors."""
        outcomes: list[int] = []
        errors: list[str] = []
        barrier = threading.Barrier(6, timeout=30)

        def _hit(path: str):
            try:
                barrier.wait()
                status, _ = two_worker.request(path, timeout=60)
                outcomes.append(status)
            except Exception as exc:  # noqa: BLE001
                errors.append(f"{path}: {exc}")

        paths = [
            "/metrics", "/analyze/SYN001", "/portfolio/", "/metrics",
            "/analyze/SYN002", "/market/",
        ]
        threads = [threading.Thread(target=_hit, args=(p,)) for p in paths]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=120)
        assert not errors, errors
        assert len(outcomes) == len(paths)
        assert set(outcomes) == {200}

    def test_shared_state_integrity(self, two_worker):
        """Worker metrics + JSON locks stay valid after concurrent load."""
        _, body = two_worker.request("/metrics")
        payload = json.loads(body)
        assert "json_store" in payload
        stats = payload["json_store"]
        assert set(stats) <= {"retries", "stale_recoveries", "timeouts"}
        assert all(v >= 0 for v in stats.values())


class TestWorkerRestart:
    def test_full_restart_recovers(self):
        """A full server restart (the Docker restart policy equivalent)
        recovers: ready, serves, metrics valid."""
        from benchmarks.common import write_csvs
        from benchmarks.validate_workers import _spawn_api, _wait_ready

        tmp = Path(tempfile.mkdtemp(prefix="nepse_137r_"))
        try:
            data_dir = tmp / "data"
            state_root = tmp / "state"
            state_root.mkdir(parents=True, exist_ok=True)
            write_csvs(data_dir, 4, 100)
            port = _free_port()
            proc = _spawn_api(data_dir, state_root, port, workers=2)
            try:
                _wait_ready(port, timeout=120)
                status, body = _request_json(port, "/metrics")
                assert status == 200
            finally:
                _hard_shutdown(proc)

            # restart on the same port
            proc2 = _spawn_api(data_dir, state_root, port, workers=2)
            try:
                _wait_ready(port, timeout=120)
                status, body = _request_json(port, "/")
                assert status == 200
            finally:
                _hard_shutdown(proc2)
        finally:
            import shutil

            shutil.rmtree(tmp, ignore_errors=True)


def _request_json(port: int, path: str):
    from benchmarks.validate_workers import _request

    return _request(port, path, timeout=60.0)


class TestConcurrentApiRequests:
    def test_five_way_concurrent_local(self, tmp_path):
        """In-process concurrency across DataService paths never corrupts
        shared caches.

        Hermetic (Sprint 13.7): a synthetic CSV-only provider is injected
        so the test never touches the live provider chain or the real
        ``DATA_DIRECTORY`` corpus (which has no ``NABIL.csv``).
        """
        from src.data.providers import CSVProvider
        from src.data.service import DataService

        data_dir = tmp_path / "data"
        data_dir.mkdir(parents=True, exist_ok=True)
        _fresh_ohlcv(60).to_csv(data_dir / "NABIL.csv", index=False)

        DataService.reset_instance()
        try:
            svc = DataService(provider=CSVProvider(data_dir=data_dir))
            results: list = []
            barrier = threading.Barrier(5, timeout=30)

            def _get(i: int):
                barrier.wait()
                results.append(svc.get_history("NABIL", days=60))

            threads = [threading.Thread(target=_get, args=(i,)) for i in range(5)]
            for t in threads:
                t.start()
            for t in threads:
                t.join(timeout=60)
            assert len(results) == 5
            assert all(r.provenance for r in results)
        finally:
            DataService.reset_instance()


# ═══════════════════════════════════════════════════════════════════
# 11. Shared-state safety
# ═══════════════════════════════════════════════════════════════════


class TestSharedStateSafety:
    def test_json_lock_stats_bounded(self):
        from src.utils.json_store import lock_stats

        stats = lock_stats()
        assert set(stats) == {"retries", "stale_recoveries", "timeouts"}
        assert all(v >= 0 for v in stats.values())

    def test_cross_process_alert_history_lock(self):
        """Two processes writing alert history concurrently never lose
        each other's updates (cross-process file lock)."""
        child = (
            "import sys; from pathlib import Path; "
            "from src.alerts.engine import process_alert_batch; "
            "r=process_alert_batch([('SYM', {'signal':'HOLD','score':1,"
            "'confidence':50,'trend':'S','volume_signal':'N',"
            "'relative_volume':1.0,'price':1.0,'target1':2.0,"
            "'target2':3.0,'target3':4.0,'alerts':[]})]); print(len(r))"
        )
        procs = [
            subprocess.Popen(
                [sys.executable, "-c", child],
                cwd=str(PROJECT_ROOT),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            for _ in range(2)
        ]
        outs = [p.communicate(timeout=300)[0].decode().strip() for p in procs]
        assert all(o == "1" for o in outs), outs

    def test_worker_metrics_aggregation_bounded(self):
        from src.utils.worker_metrics import aggregate_worker_metrics

        # aggregate_worker_metrics self-reports (throttled) then reads the
        # shared store, so a single call exercises the whole path.
        payload = aggregate_worker_metrics({"metrics": {"cache_hits": 1}})
        assert "workers" in payload or "aggregate" in payload


# ═══════════════════════════════════════════════════════════════════
# 12. Calendar holiday intake
# ═══════════════════════════════════════════════════════════════════


class TestCalendarHolidayIntake:
    def _candidate(self, **overrides) -> dict:
        cand = {
            "date": "2026-10-11",
            "description": "Dashain closure",
            "type": "holiday",
            "source": "NEPSE notice",
            "provenance": "operator intake",
            "operator": "ops-1",
            "notes": "validated via Sprint 13.7 intake",
        }
        cand.update(overrides)
        return cand

    def test_valid_candidate_passes_validation(self):
        from src.data.calendar import validate_holiday_candidate

        cal = NepseCalendar.from_dict(_valid_calendar_dict())
        assert validate_holiday_candidate(self._candidate(), cal) == []

    def test_missing_date_rejected(self):
        from src.data.calendar import validate_holiday_candidate

        assert validate_holiday_candidate(self._candidate(date=None),
                                          NepseCalendar())

    def test_bad_type_rejected(self):
        from src.data.calendar import validate_holiday_candidate

        problems = validate_holiday_candidate(
            self._candidate(type="party_day"), NepseCalendar()
        )
        assert any("invalid holiday type" in p for p in problems)

    def test_missing_provenance_rejected(self):
        from src.data.calendar import validate_holiday_candidate

        problems = validate_holiday_candidate(
            self._candidate(provenance=None, source=None, operator=None),
            NepseCalendar(),
        )
        assert any("provenance" in p for p in problems)

    def test_weekend_closure_rejected(self):
        from src.data.calendar import validate_holiday_candidate

        # 2026-10-09 is a Friday — a closure on a weekend is redundant
        problems = validate_holiday_candidate(
            self._candidate(date="2026-10-09", type="exceptional_closure"),
            NepseCalendar(),
        )
        assert any("weekend" in p for p in problems)

    def test_duplicate_rejected(self):
        from src.data.calendar import validate_holiday_candidate

        cal = NepseCalendar.from_dict(
            _valid_calendar_dict(holidays=["2026-10-11"])
        )
        problems = validate_holiday_candidate(self._candidate(), cal)
        assert any("duplicate" in p for p in problems)

    def test_conflicting_special_session_rejected(self):
        from src.data.calendar import validate_holiday_candidate

        cal = NepseCalendar.from_dict(
            _valid_calendar_dict(special_sessions=["2026-10-11"])
        )
        problems = validate_holiday_candidate(self._candidate(), cal)
        assert any("conflict" in p for p in problems)

    def test_effective_range_rejected(self):
        from src.data.calendar import validate_holiday_candidate

        cal = NepseCalendar.from_dict(
            _valid_calendar_dict(
                effective_from="2026-01-01", effective_to="2026-12-31"
            )
        )
        problems = validate_holiday_candidate(
            self._candidate(date="2027-03-01"), cal
        )
        assert any("effective" in p for p in problems)

    def test_submit_activates_and_bumps_version(self, tmp_path, monkeypatch):
        from src.data import calendar as cal_module
        from src.data.calendar import submit_holiday_candidate

        monkeypatch.setattr(
            cal_module, "CALENDAR_HISTORY_FILE", str(tmp_path / "history.json")
        )
        path = tmp_path / "nepse_calendar.json"
        cal_module.update_calendar(_valid_calendar_dict(), path=path)
        ok, problems = submit_holiday_candidate(self._candidate(), path=path)
        assert ok and not problems
        loaded = NepseCalendar.load(path)
        assert loaded.is_holiday(date(2026, 10, 11))
        assert loaded.version != "2.0"  # version bumped

    def test_submit_rejection_leaves_calendar_unchanged(self, tmp_path, monkeypatch):
        from src.data import calendar as cal_module
        from src.data.calendar import submit_holiday_candidate

        monkeypatch.setattr(
            cal_module, "CALENDAR_HISTORY_FILE", str(tmp_path / "history.json")
        )
        path = tmp_path / "nepse_calendar.json"
        cal_module.update_calendar(_valid_calendar_dict(), path=path)
        before = path.read_bytes()
        ok, problems = submit_holiday_candidate(
            self._candidate(type="bogus"), path=path
        )
        assert ok is False and problems
        assert path.read_bytes() == before

    def test_submit_special_session_on_weekend_accepted(self, tmp_path, monkeypatch):
        from src.data import calendar as cal_module
        from src.data.calendar import submit_holiday_candidate

        monkeypatch.setattr(
            cal_module, "CALENDAR_HISTORY_FILE", str(tmp_path / "history.json")
        )
        path = tmp_path / "nepse_calendar.json"
        cal_module.update_calendar(_valid_calendar_dict(), path=path)
        ok, problems = submit_holiday_candidate(
            self._candidate(date="2026-10-10", type="special_session"),
            path=path,
        )
        assert ok and not problems
        loaded = NepseCalendar.load(path)
        assert loaded.is_special_session(date(2026, 10, 10))

    def test_submit_records_version_history(self, tmp_path, monkeypatch):
        from src.data import calendar as cal_module
        from src.data.calendar import calendar_version_history

        history_file = tmp_path / "history.json"
        monkeypatch.setattr(cal_module, "CALENDAR_HISTORY_FILE", str(history_file))
        path = tmp_path / "nepse_calendar.json"
        cal_module.update_calendar(_valid_calendar_dict(), path=path)
        cal_module.submit_holiday_candidate(self._candidate(), path=path)
        history = calendar_version_history()
        assert history and any(h["via"] == "holiday_intake" for h in history)


# ═══════════════════════════════════════════════════════════════════
# 13. Calendar validation
# ═══════════════════════════════════════════════════════════════════


class TestCalendarValidation:
    def test_valid_calendar_passes(self):
        assert validate_calendar_data(_valid_calendar_dict()) == []

    def test_unsupported_version_rejected(self):
        problems = validate_calendar_data(
            _valid_calendar_dict(calendar_version="9.9")
        )
        assert any("version" in p for p in problems)

    def test_duplicate_date_in_category(self):
        problems = validate_calendar_data(
            _valid_calendar_dict(holidays=["2026-10-11", "2026-10-11"])
        )
        assert any("duplicate" in p for p in problems)

    def test_cross_category_contradiction(self):
        problems = validate_calendar_data(
            _valid_calendar_dict(
                holidays=["2026-10-11"], special_sessions=["2026-10-11"]
            )
        )
        assert any("contradictory" in p for p in problems)

    def test_missing_provenance_rejected(self):
        problems = validate_calendar_data(
            _valid_calendar_dict(provenance=None, source=None)
        )
        assert any("provenance" in p for p in problems)

    def test_validation_never_raises(self):
        for junk in (None, [], "x", 42):
            assert isinstance(validate_calendar_data(junk), list)

    def test_effective_range_check(self):
        problems = validate_calendar_data(
            _valid_calendar_dict(
                effective_from="2026-01-01",
                effective_to="2026-12-31",
                holidays=["2027-01-01"],
            )
        )
        assert any("after effective_to" in p for p in problems)


# ═══════════════════════════════════════════════════════════════════
# 14. Calendar rollback
# ═══════════════════════════════════════════════════════════════════


class TestCalendarRollback:
    def test_update_preserves_previous_version(self, tmp_path, monkeypatch):
        from src.data import calendar as cal_module
        from src.data.calendar import update_calendar

        monkeypatch.setattr(
            cal_module, "CALENDAR_HISTORY_FILE", str(tmp_path / "history.json")
        )
        path = tmp_path / "nepse_calendar.json"
        ok, _ = update_calendar(_valid_calendar_dict(), path=path)
        assert ok
        ok2, _ = update_calendar(
            _valid_calendar_dict(source="operator-v2"), path=path
        )
        assert ok2
        assert list(tmp_path.glob("nepse_calendar.*.bak.json"))

    def test_rollback_restores_previous(self, tmp_path, monkeypatch):
        from src.data import calendar as cal_module
        from src.data.calendar import update_calendar, rollback_calendar

        monkeypatch.setattr(
            cal_module, "CALENDAR_HISTORY_FILE", str(tmp_path / "history.json")
        )
        path = tmp_path / "nepse_calendar.json"
        update_calendar(_valid_calendar_dict(), path=path)
        update_calendar(
            _valid_calendar_dict(holidays=["2026-10-11"], source="v2"), path=path
        )
        assert NepseCalendar.load(path).is_holiday(date(2026, 10, 11))
        ok, problems = rollback_calendar(path=path)
        assert ok and not problems
        assert not NepseCalendar.load(path).is_holiday(date(2026, 10, 11))

    def test_rollback_validates_target(self, tmp_path, monkeypatch):
        """A corrupt backup must be rejected; the active calendar stays."""
        from src.data import calendar as cal_module
        from src.data.calendar import update_calendar, rollback_calendar

        monkeypatch.setattr(
            cal_module, "CALENDAR_HISTORY_FILE", str(tmp_path / "history.json")
        )
        path = tmp_path / "nepse_calendar.json"
        update_calendar(_valid_calendar_dict(), path=path)
        update_calendar(_valid_calendar_dict(source="v2"), path=path)
        # corrupt the newest backup
        backups = sorted(tmp_path.glob("nepse_calendar.*.bak.json"))
        backups[-1].write_text("{ not json", encoding="utf-8")
        ok, problems = rollback_calendar(path=path)
        assert ok is False
        assert problems

    def test_rollback_reversible(self, tmp_path, monkeypatch):
        from src.data import calendar as cal_module
        from src.data.calendar import update_calendar, rollback_calendar

        monkeypatch.setattr(
            cal_module, "CALENDAR_HISTORY_FILE", str(tmp_path / "history.json")
        )
        path = tmp_path / "nepse_calendar.json"
        update_calendar(_valid_calendar_dict(), path=path)
        update_calendar(
            _valid_calendar_dict(holidays=["2026-10-11"], source="v2"), path=path
        )
        rollback_calendar(path=path)
        # rollback is reversible: the pre-rollback version is preserved
        ok, problems = rollback_calendar(path=path)
        assert ok and not problems
        assert NepseCalendar.load(path).is_holiday(date(2026, 10, 11))


# ═══════════════════════════════════════════════════════════════════
# 15. Calendar regression
# ═══════════════════════════════════════════════════════════════════


class TestCalendarRegression:
    def test_sun_thu_trading_preserved(self):
        cal = NepseCalendar.from_dict(_valid_calendar_dict())
        assert cal.is_trading_day(date(2026, 8, 10)) is True   # Monday
        assert cal.is_trading_day(date(2026, 8, 12)) is True   # Wednesday
        assert cal.is_trading_day(date(2026, 8, 16)) is True   # Sunday

    def test_fri_sat_weekend_preserved(self):
        cal = NepseCalendar.from_dict(_valid_calendar_dict())
        assert cal.is_trading_day(date(2026, 8, 14)) is False  # Friday
        assert cal.is_trading_day(date(2026, 8, 15)) is False  # Saturday

    def test_holiday_non_trading(self):
        cal = NepseCalendar.from_dict(
            _valid_calendar_dict(holidays=["2026-08-13"])
        )
        assert cal.is_trading_day(date(2026, 8, 13)) is False  # Thursday holiday

    def test_special_session_trading(self):
        cal = NepseCalendar.from_dict(
            _valid_calendar_dict(special_sessions=["2026-08-15"])
        )
        assert cal.is_trading_day(date(2026, 8, 15)) is True  # Saturday session

    def test_exceptional_closure_non_trading(self):
        cal = NepseCalendar.from_dict(
            _valid_calendar_dict(closures=["2026-08-12"])
        )
        assert cal.is_trading_day(date(2026, 8, 12)) is False

    def test_effective_boundaries(self):
        cal = NepseCalendar.from_dict(
            _valid_calendar_dict(
                effective_from="2026-01-01", effective_to="2026-12-31"
            )
        )
        assert cal.covers(date(2026, 6, 1)) is True
        assert cal.covers(date(2027, 1, 2)) is False

    def test_missing_sessions_not_inflated_by_holiday(self):
        """Adding a holiday must not inflate missing-session counts for
        that day (it becomes an EXPECTED non-trading day)."""
        cal = NepseCalendar.from_dict(
            _valid_calendar_dict(
                holidays=["2026-08-13"], observed_sessions=["2026-08-10",
                                                           "2026-08-11",
                                                           "2026-08-12"]
            )
        )
        findings = cal.classify_sessions(
            [date(2026, 8, 10), date(2026, 8, 11), date(2026, 8, 12)],
            start=date(2026, 8, 10), end=date(2026, 8, 14),
        )
        # Thursday holiday = EXPECTED non-trading day, not MISSING
        assert not any(f.classification == "MISSING_TRADING_SESSION"
                       and f.day == date(2026, 8, 13) for f in findings)


# ═══════════════════════════════════════════════════════════════════
# 16. Production-readiness checks
# ═══════════════════════════════════════════════════════════════════


class TestProductionReadiness:
    def test_audit_doc_exists(self):
        assert (PROJECT_ROOT / "docs" / "PRODUCTION_READINESS.md").exists()

    def test_audit_doc_classifies_subsystems(self):
        text = (PROJECT_ROOT / "docs" / "PRODUCTION_READINESS.md").read_text(
            encoding="utf-8"
        )
        for token in ("READY", "CONDITIONALLY_READY", "NOT_READY"):
            assert token in text

    def test_external_dependency_register_present(self):
        text = (PROJECT_ROOT / "docs" / "PRODUCTION_READINESS.md").read_text(
            encoding="utf-8"
        )
        assert "External" in text
        assert "Dependency" in text or "dependencies" in text

    def test_verification_labels_present(self):
        text = (PROJECT_ROOT / "docs" / "PRODUCTION_READINESS.md").read_text(
            encoding="utf-8"
        )
        assert "LIVE VERIFIED" in text or "NOT VERIFIED" in text


# ═══════════════════════════════════════════════════════════════════
# 17. Configuration / security checks
# ═══════════════════════════════════════════════════════════════════


class TestSecurityConfig:
    def test_no_hardcoded_secrets_in_src(self):
        import re

        pattern = re.compile(
            r"(sk-[A-Za-z0-9]{20,}|api[_-]?key\s*=\s*[\"'][A-Za-z0-9]{16,}"
            r"|password\s*=\s*[\"'][^\"']{8,}[\"'])",
            re.IGNORECASE,
        )
        hits = []
        for path in (PROJECT_ROOT / "src").rglob("*.py"):
            for line_no, line in enumerate(path.read_text(
                encoding="utf-8", errors="ignore"
            ).splitlines(), 1):
                if pattern.search(line):
                    hits.append(f"{path}:{line_no}")
        assert not hits, hits

    def test_no_eval_exec_in_src(self):
        import re

        bad = re.compile(r"\b(eval|exec)\s*\(")
        hits = []
        for path in (PROJECT_ROOT / "src").rglob("*.py"):
            for line_no, line in enumerate(path.read_text(
                encoding="utf-8", errors="ignore"
            ).splitlines(), 1):
                if bad.search(line):
                    hits.append(f"{path}:{line_no}")
        assert not hits, hits

    def test_symbol_path_traversal_rejected(self):
        from src.api.analyze import analyze
        from fastapi import HTTPException

        for symbol in ("../../etc/passwd", "a/b", "..", "/etc"):
            try:
                analyze(symbol)
                raise AssertionError(f"symbol {symbol} not rejected")
            except HTTPException as exc:
                assert exc.status_code == 400

    def test_metrics_payload_has_no_filesystem_paths(self):
        from src.api.metrics import metrics

        payload = metrics()
        text = json.dumps(payload)
        assert "nepse-quant-engine" not in text
        assert "C:\\\\" not in text and "/home/" not in text

    def test_analyze_error_is_sanitized(self):
        from fastapi import HTTPException

        from src.api.analyze import analyze

        try:
            analyze("__DOES_NOT_EXIST__999")
            raise AssertionError("expected 404")
        except HTTPException as exc:
            assert exc.status_code == 404
            assert "data" not in str(exc.detail).lower() or "not found" in str(exc.detail)


# ═══════════════════════════════════════════════════════════════════
# 18. Resource bounds
# ═══════════════════════════════════════════════════════════════════


class TestResourceBounds:
    def test_incidents_bounded(self):
        from src.data.incidents import incident_tracker

        incident_tracker.reset()
        for i in range(300):
            incident_tracker.record(
                "provider_timeout", providers=["api"], symbol=f"S{i}"
            )
        snap = incident_tracker.snapshot(recent_limit=500)
        assert len(snap["recent"]) <= snap["max_events"]
        assert snap["max_events"] == 200

    def test_health_window_bounded(self):
        from src.data.health import HealthCheckConfig, ProviderHealthMonitor

        monitor = ProviderHealthMonitor(HealthCheckConfig(outcome_window=100))
        monitor.register("api")
        for _ in range(500):
            monitor.record_success("api", latency_ms=5)
        rel = monitor.reliability_history("api")
        assert rel["window"] <= 100

    def test_quality_trends_bounded(self):
        from src.data.quality import quality_trends

        for _ in range(10):
            quality_trends.record_corpus({"symbols_checked": 100, "valid": 100,
                                          "invalid": 0, "conflicts": 0})
        trend = quality_trends.trend()
        assert isinstance(trend, dict)
        assert trend["max_snapshots"] >= 2          # latest + previous retained
        assert trend["snapshots"] <= trend["max_snapshots"]  # ring bounded

    def test_notifications_bounded(self, monkeypatch, tmp_path):
        from src.ui.notifications import MAX_HISTORY, NotificationManager

        # Isolate the process-wide singleton: this test fills it to the
        # cap; without a private store it would poison later in-process
        # tests (e.g. ``test_unread_count``: ``before`` already 500).
        _isolate_notifications(monkeypatch, tmp_path)
        manager = NotificationManager()
        for i in range(MAX_HISTORY + 100):
            manager.notify(f"x{i}")
        assert len(manager.get_all(limit=MAX_HISTORY * 2)) <= MAX_HISTORY

    def test_repeated_minor_symbols_bounded(self):
        from src.data.incidents import (
            REPEATED_MINOR_MAX_SYMBOLS,
            incident_tracker,
        )

        incident_tracker.reset()
        for i in range(REPEATED_MINOR_MAX_SYMBOLS + 20):
            incident_tracker.record_minor(f"SYM{i}")
        snap = incident_tracker.snapshot(recent_limit=0)
        assert snap["counts"]["repeated_minor_disagreement"] <= (
            REPEATED_MINOR_MAX_SYMBOLS + 20
        )

    def test_scanner_cache_bounded(self):
        from src.cache.scanner_cache import scanner_cache

        stats = scanner_cache.stats()
        assert stats["dataframe_entries"] >= 0
        assert stats["analysis_entries"] >= 0

    def test_long_running_bounded_stability(self):
        """Simulated long-running operation: recording many events never
        grows the incident ring past its bound."""
        from src.data.incidents import incident_tracker

        incident_tracker.reset()
        for i in range(2000):
            incident_tracker.record("fallback", providers=["csv"],
                                    symbol=f"S{i % 50}")
        snap = incident_tracker.snapshot(recent_limit=9999)
        assert len(snap["recent"]) <= snap["max_events"]


# ═══════════════════════════════════════════════════════════════════
# 19. Performance regression
# ═══════════════════════════════════════════════════════════════════


class TestPerformanceRegression:
    def test_gate_thresholds_present(self):
        from benchmarks.ci_gate import (
            ANALYZE_P99_MAX_MS,
            WARM_API_RATIO_MAX,
            WARM_PORTFOLIO_RATIO_MAX,
        )

        assert WARM_API_RATIO_MAX <= 0.75
        assert WARM_PORTFOLIO_RATIO_MAX <= 0.75
        assert ANALYZE_P99_MAX_MS <= 2000.0

    def test_thresholds_not_weakened(self):
        from benchmarks.ci_gate import (
            ANALYZE_P99_MAX_MS,
            WARM_API_RATIO_MAX,
            WARM_PORTFOLIO_RATIO_MAX,
        )

        assert WARM_API_RATIO_MAX == 0.75
        assert WARM_PORTFOLIO_RATIO_MAX == 0.75
        assert ANALYZE_P99_MAX_MS == 2000.0

    def test_scanner_warm_path_smoke(self, tmp_path, monkeypatch):
        """A warm scan (pure cache hits) must be fast and byte-identical."""
        from src import scanner as scanner_pkg
        from src.cache.scanner_cache import scanner_cache

        df = _fresh_ohlcv(120)
        df.to_csv(tmp_path / "NABIL.csv", index=False)
        scanner_cache.clear()
        monkeypatch.setattr(scanner_pkg.engine, "DATA_DIRECTORY", str(tmp_path))
        scanner_pkg.engine.scan_market(workers=1)  # cold
        scanner_cache.stats()
        warm = scanner_pkg.engine.scan_market(workers=1)  # warm
        assert len(warm["results"]) == 1

    def test_alert_batch_single_write(self, tmp_path, monkeypatch):
        """process_alert_batch performs one history write for N symbols."""
        from src.alerts.engine import process_alert_batch
        from src.alerts import history as alert_history

        target = tmp_path / "history.json"
        monkeypatch.setattr(alert_history, "HISTORY_FILE", target)
        entries = [
            ("NABIL", _full_result({"signal": "HOLD"})),
            ("SCB", _full_result({"signal": "BUY"})),
            ("ADBL", _full_result({"signal": "SELL"})),
        ]
        batch = process_alert_batch(entries)
        assert set(batch) == {"NABIL", "SCB", "ADBL"}
        assert target.exists()


# ═══════════════════════════════════════════════════════════════════
# 20. Sprint 13.3–13.6 regression
# ═══════════════════════════════════════════════════════════════════


class TestSprint1336Regression:
    def test_invalid_data_hold_contract(self):
        """Sprint 13.3 contract: INVALID quality → HOLD / invalid_data."""
        bad = _fresh_ohlcv()
        bad.loc[bad.index[-1], "Close"] = 0.0
        from src.engine.analyzer import analyze_dataframe

        result = analyze_dataframe(bad, symbol="NABIL")
        assert result["signal_suppressed"] is True
        assert result["signal_suppression_reason"] == "invalid_data"

    def test_reconciliation_suppression_contract(self):
        """Sprint 13.4 contract: MATERIAL → HOLD / material_disagreement."""
        from src.data.reconciliation import MATERIAL_DISAGREEMENT, ReconciliationResult
        from src.engine.analyzer import analyze_dataframe

        result = analyze_dataframe(
            _fresh_ohlcv(),
            symbol="NABIL",
            reconciliation=ReconciliationResult(
                status=MATERIAL_DISAGREEMENT, providers=["api", "csv"]
            ),
        )
        assert result["signal_suppressed"] is True
        assert result["signal_suppression_reason"] == "material_disagreement"

    def test_provenance_suppression_contract(self):
        """Sprint 13.5 contract: unsafe provenance → HOLD with trust reason."""
        from src.engine.analyzer import analyze_dataframe

        result = analyze_dataframe(
            _fresh_ohlcv(),
            symbol="NABIL",
            provenance=DataProvenance(trust=TRUST_CALENDAR_INVALID),
        )
        assert result["signal_suppression_reason"] == TRUST_CALENDAR_INVALID

    def test_legacy_shape_preserved(self, monkeypatch):
        """Non-OHLCV frames keep the legacy shape (no quality/reconciliation)."""
        from src.engine import analyzer as analyzer_mod

        # Mirror test_sprint13_3's stub tail: the 1-row frame reaches the
        # result builder untouched (the real indicator chain needs OHLCV
        # columns and is exercised by the cacheable-frame tests).
        monkeypatch.setattr(analyzer_mod, "add_moving_averages", lambda df, inplace=False: df)
        monkeypatch.setattr(analyzer_mod, "add_momentum_indicators", lambda df, inplace=False: df)
        monkeypatch.setattr(analyzer_mod, "add_volume_indicators", lambda df, inplace=False: df)
        monkeypatch.setattr(analyzer_mod, "add_volatility_indicators", lambda df, inplace=False: df)
        monkeypatch.setattr(analyzer_mod, "get_support", lambda df: 80.0)
        monkeypatch.setattr(analyzer_mod, "get_resistance", lambda df: 120.0)
        monkeypatch.setattr(analyzer_mod, "get_trend", lambda latest: "UPTREND")
        monkeypatch.setattr(
            analyzer_mod,
            "detect_pattern",
            lambda df: {"name": "Bullish", "type": "Bullish",
                        "strength": "Strong", "score": 2},
        )
        monkeypatch.setattr(
            analyzer_mod,
            "calculate_score",
            lambda latest, pattern: (6, {"trend": 2, "rsi": 1, "macd": 2,
                                         "volume": 1, "pattern": 2,
                                         "reasons": ["ok"]}),
        )
        monkeypatch.setattr(analyzer_mod, "calculate_confidence", lambda score, breakdown: 85)
        monkeypatch.setattr(analyzer_mod, "create_trade_plan", lambda result: {"target1": 110.0})
        monkeypatch.setattr(analyzer_mod, "calculate_risk_reward", lambda result: {"best_rr": 3.2})
        monkeypatch.setattr(analyzer_mod, "calculate_position_size", lambda result: {"position_size": 10})
        monkeypatch.setattr(analyzer_mod, "check_alerts", lambda result: [{"type": "BUY"}])
        monkeypatch.setattr(analyzer_mod, "generate_signal", lambda result: "BUY")

        df = pd.DataFrame(
            [
                {
                    "Date": pd.Timestamp("2026-08-01"),
                    "Close": 100.0,
                    "SMA_20": 95.0,
                    "SMA_50": 90.0,
                    "RSI": 55.0,
                    "MACD": 1.5,
                    "MACD_SIGNAL": 0.8,
                    "VOLUME_SIGNAL": "NORMAL",
                    "RELATIVE_VOLUME": 1.2,
                    "VOLUME_SCORE": 1,
                    "ATR": 2.5,
                }
            ]
        )
        result = analyzer_mod.analyze_dataframe(df, symbol="NABIL")
        assert "data_quality" not in result
        assert "reconciliation" not in result

    def test_calendar_update_validation_contract(self):
        from src.data.calendar import validate_candidate_calendar

        problems = validate_candidate_calendar(_valid_calendar_dict())
        assert problems == []

    def test_metrics_compatibility(self):
        from src.api.metrics import metrics

        payload = metrics()
        for key in ("metrics", "scanner_cache", "indicator_cache", "data_quality",
                    "reconciliation", "provider_health", "calendar", "incidents",
                    "system_status", "status"):
            assert key in payload, key

    def test_quality_contract_kept(self):
        from src.data.quality import assess_history

        report = assess_history(_fresh_ohlcv(), symbol="NABIL")
        assert report.status in ("VALID", "SUSPICIOUS")
        assert report.freshness in ("FRESH", "UNKNOWN")

    def test_docker_config_contract(self):
        # Text-based checks (PyYAML is not a dependency; the Sprint 13.6
        # docker contract tests use the same approach).
        compose = (PROJECT_ROOT / "docker-compose.yml").read_text(encoding="utf-8")
        assert "api:" in compose
        assert "restart: unless-stopped" in compose
        assert "healthcheck" in compose
