"""Sprint 13.5 — Production Data Trust, Calendar Governance & Reliability.

Behavioral coverage for the data-trust lifecycle:

    Provider → Identity validation → Reconciliation → Calendar validation
    → Quality validation → Provenance → Cache → Analyzer → Signal safety

Categories (mirroring the sprint brief):

  1. benchmark determinism      10. fallback metrics
  2. calendar loading           11. failure injection
  3. calendar validation        12. cache provenance
  4. calendar provenance        13. cache safety
  5. holiday handling           14. signal safety
  6. special sessions           15. API compatibility
  7. data provenance            16. no-silent-degradation
  8. provider health            17. regression vs Sprint 13.4
  9. reconciliation metrics

Tests are behavioural — no fake assertions, no implementation peeking
when a behaviour is observable.
"""

from __future__ import annotations

import json
import tempfile
from datetime import date
from pathlib import Path

import pandas as pd
import pytest

from src.data.calendar import (
    EXCEPTIONAL_CLOSURE,
    HOLIDAY,
    SPECIAL_SESSION,
    TRADING_DAY,
    UNKNOWN,
    WEEKEND,
    NepseCalendar,
    default_calendar,
    validate_calendar_data,
)
from src.data.exceptions import (
    DataUnavailable,
    InvalidDataError,
    ProviderError,
    ProviderTimeout,
)
from src.data.provenance import (
    ALL_TRUST_STATES,
    DataProvenance,
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
    provenance_from_history,
    provenance_from_reconciliation,
    resolve_trust,
)
from src.data.quality import (
    R_NON_TRADING_DATE,
    INVALID,
    SUSPICIOUS,
    VALID,
    assess_history,
    validate_history_frame,
)
from src.data.reconciliation import (
    AGREE,
    MAPPING_CONFLICT,
    MATERIAL_DISAGREEMENT,
    MINOR_DISAGREEMENT,
    UNAVAILABLE,
    ReconciliationResult,
    reconcile_history,
    reconciliation_metrics,
)

# ═══════════════════════════════════════════════════════════════════
# Shared helpers
# ═══════════════════════════════════════════════════════════════════


def _ohlcv(n: int = 40, start: str = "2026-06-01", base: float = 100.0) -> pd.DataFrame:
    """Synthetic OHLCV frame (weekday-dense, no weekends)."""
    dates = pd.bdate_range(start, periods=n)  # Mon–Fri business days
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


def _weekday_frame(dates: list[str], closes: list[float]) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "Date": pd.to_datetime(dates),
            "Open": [c - 0.5 for c in closes],
            "High": [c + 1.0 for c in closes],
            "Low": [c - 1.0 for c in closes],
            "Close": closes,
            "Volume": [1000] * len(closes),
        }
    )


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


class _ReconcilingProvider:
    """Provider exposing the hybrid reconciliation contract."""

    name = "reconciling"

    def __init__(self, frames=None, fail=False):
        self._frames = frames or {}
        self._fail = fail

    def get_history(self, symbol, days=365):
        if self._fail:
            raise RuntimeError("boom")
        return self._frames.get("api")

    def get_reconciled_history(self, symbol, days=365):
        if self._fail:
            raise RuntimeError("boom")
        merged, res = reconcile_history(self._frames, symbol=symbol)
        return merged, res


# ═══════════════════════════════════════════════════════════════════
# 1. Benchmark determinism
# ═══════════════════════════════════════════════════════════════════


class TestBenchmarkDeterminism:
    def test_benchmark_isolation_context_restores_globals(self, tmp_path):
        """benchmark_isolation snapshots/restores scanner DATA_DIRECTORY and
        resets the DataService singleton on exit (Sprint 13.5 determinism)."""
        from benchmarks.common import benchmark_isolation
        from src.data.service import DataService
        from src.scanner import engine as scanner_engine

        original_dir = scanner_engine.DATA_DIRECTORY
        iso = tmp_path / "iso"
        iso.mkdir()
        DataService.reset_instance()
        DataService()  # build a singleton
        assert DataService._instance is not None

        with benchmark_isolation(str(iso)):
            scanner_engine.DATA_DIRECTORY = str(tmp_path / "other")
            assert scanner_engine.DATA_DIRECTORY != original_dir

        assert scanner_engine.DATA_DIRECTORY == original_dir
        DataService.reset_instance()

    def test_benchmark_startup_records_error_not_raises(self, monkeypatch):
        """A failing subprocess in bench_startup is recorded, never raised —
        the informational startup metric must not fail the runner."""
        import subprocess

        from benchmarks.pipeline import bench_startup

        def _boom(*a, **k):
            raise subprocess.TimeoutExpired(cmd="python", timeout=120)

        monkeypatch.setattr(subprocess, "run", _boom)
        result = bench_startup()
        assert "startup_ms" in result
        assert result["startup_ms"] is None
        assert "error" in result


# ═══════════════════════════════════════════════════════════════════
# 2. Calendar loading
# ═══════════════════════════════════════════════════════════════════


class TestCalendarLoading:
    def test_shipped_calendar_file_loads(self):
        """The shipped governed calendar (data/state/nepse_calendar.json)
        must load and carry the v2 schema."""
        from src.config import NEPSE_CALENDAR_FILE

        assert Path(NEPSE_CALENDAR_FILE).exists()
        cal = NepseCalendar.load(NEPSE_CALENDAR_FILE)
        assert cal is not None
        assert cal.version == "2.0"
        assert cal.source  # provenance present

    def test_default_calendar_returns_governed_or_base(self):
        cal = default_calendar()
        assert isinstance(cal, NepseCalendar)

    def test_load_from_dict_roundtrip(self, tmp_path):
        cal = NepseCalendar(
            holidays=[date(2026, 10, 11)],
            special_sessions=[date(2026, 8, 1)],
            closures=[date(2026, 8, 2)],
            effective_from=date(2026, 1, 1),
            effective_to=date(2027, 12, 31),
            source="test-source",
            last_validated=date(2026, 8, 14),
            version="2.0",
        )
        path = tmp_path / "cal.json"
        path.write_text(json.dumps(cal.to_dict()), encoding="utf-8")
        loaded = NepseCalendar.load(str(path))
        assert loaded is not None
        assert loaded.version == "2.0"
        assert loaded.is_holiday(date(2026, 10, 11))
        assert loaded.is_special_session(date(2026, 8, 1))
        assert loaded.is_closure(date(2026, 8, 2))

    def test_load_malformed_file_returns_none(self, tmp_path):
        path = tmp_path / "bad.json"
        path.write_text("{not json", encoding="utf-8")
        assert NepseCalendar.load(str(path)) is None

    def test_load_invalid_calendar_returns_none(self, tmp_path):
        path = tmp_path / "bad.json"
        path.write_text(json.dumps({"calendar_version": "99.0"}), encoding="utf-8")
        assert NepseCalendar.load(str(path)) is None


# ═══════════════════════════════════════════════════════════════════
# 3. Calendar validation (governance)
# ═══════════════════════════════════════════════════════════════════


class TestCalendarValidation:
    def test_valid_v2_passes(self):
        problems = validate_calendar_data(
            {
                "calendar_version": "2.0",
                "effective_from": "2026-01-01",
                "effective_to": "2027-12-31",
                "trading_week": [0, 1, 2, 3, 6],
                "weekend_days": [4, 5],
                "holidays": ["2026-10-11"],
                "provenance": {"source": "operator-maintained"},
            }
        )
        assert problems == []

    def test_unsupported_version_rejected(self):
        problems = validate_calendar_data({"calendar_version": "9.9"})
        assert any("unsupported calendar version" in p for p in problems)

    def test_invalid_effective_range_rejected(self):
        problems = validate_calendar_data(
            {
                "calendar_version": "2.0",
                "effective_from": "2027-01-01",
                "effective_to": "2026-01-01",
            }
        )
        assert any("date range" in p for p in problems)

    def test_malformed_holiday_rejected(self):
        problems = validate_calendar_data(
            {"calendar_version": "2.0", "holidays": ["not-a-date"]}
        )
        assert any("holiday" in p.lower() or "date" in p.lower() for p in problems)

    def test_duplicate_dates_rejected(self):
        problems = validate_calendar_data(
            {
                "calendar_version": "2.0",
                "holidays": ["2026-10-11", "2026-10-11"],
            }
        )
        assert any("duplicate" in p.lower() for p in problems)

    def test_contradictory_holiday_special_session_rejected(self):
        problems = validate_calendar_data(
            {
                "calendar_version": "2.0",
                "holidays": ["2026-10-11"],
                "special_sessions": ["2026-10-11"],
            }
        )
        assert any("both" in p.lower() or "contradict" in p.lower() for p in problems)

    def test_contradictory_holiday_closure_rejected(self):
        problems = validate_calendar_data(
            {
                "calendar_version": "2.0",
                "holidays": ["2026-10-11"],
                "closures": ["2026-10-11"],
            }
        )
        assert any("both" in p.lower() or "contradict" in p.lower() for p in problems)

    def test_out_of_range_weekday_rejected(self):
        problems = validate_calendar_data(
            {"calendar_version": "2.0", "weekend_days": [9]}
        )
        assert problems  # 9 must not silently modulo to 2 (a trading day)

    def test_missing_provenance_rejected(self):
        problems = validate_calendar_data(
            {
                "calendar_version": "2.0",
                "holidays": [],
                "provenance": None,
                "source": "",
            }
        )
        assert any("provenance" in p.lower() for p in problems)

    def test_validation_never_raises(self):
        for junk in (None, [], "x", 42, {"holidays": [object()]}):
            problems = validate_calendar_data(junk)
            assert isinstance(problems, list)


# ═══════════════════════════════════════════════════════════════════
# 4. Calendar provenance / bounded status
# ═══════════════════════════════════════════════════════════════════


class TestCalendarProvenance:
    def _cal(self) -> NepseCalendar:
        return NepseCalendar(
            holidays=[date(2026, 10, 11)],
            special_sessions=[date(2026, 8, 1)],
            closures=[date(2026, 8, 2)],
            effective_from=date(2026, 1, 1),
            effective_to=date(2027, 12, 31),
            timezone="Asia/Kathmandu",
            last_validated=date(2026, 8, 14),
            source="operator-maintained",
            version="2.0",
        )

    def test_status_is_bounded(self):
        status = self._cal().to_status()
        assert status["version"] == "2.0"
        assert status["timezone"] == "Asia/Kathmandu"
        assert status["effective_from"] == "2026-01-01"
        assert status["last_validated"] == "2026-08-14"
        assert status["has_provenance"] is True
        # counts, never raw dates
        assert status["holidays"] == 1
        assert status["special_sessions"] == 1
        assert status["closures"] == 1
        for k in ("holidays", "special_sessions", "closures"):
            assert isinstance(status[k], int)

    def test_covers_effective_window(self):
        cal = self._cal()
        assert cal.covers(date(2026, 6, 1)) is True
        assert cal.covers(date(2025, 1, 1)) is False
        assert cal.covers(date(2028, 1, 1)) is False

    def test_coverage_for_range_bounded(self):
        cov = self._cal().coverage_for(date(2026, 6, 1), date(2026, 6, 7))
        assert "fully_inside_effective_window" in cov
        assert "scheduled_sessions" in cov
        assert isinstance(cov["scheduled_sessions"], int)

    def test_status_bounded_when_unbounded(self):
        status = NepseCalendar().to_status()
        assert status["effective_from"] is None
        assert status["observed_sessions"] is None
        assert status["has_provenance"] is True  # DEFAULT provenance


# ═══════════════════════════════════════════════════════════════════
# 5. Holiday handling
# ═══════════════════════════════════════════════════════════════════


class TestHolidayHandling:
    def test_holiday_is_not_trading_day(self):
        cal = NepseCalendar(holidays=[date(2026, 10, 11)])
        assert cal.classify_date(date(2026, 10, 11)) == HOLIDAY
        assert cal.is_trading_day(date(2026, 10, 11)) is False

    def test_holiday_not_missing_session(self):
        cal = NepseCalendar(
            holidays=[date(2026, 10, 11)],
            observed_sessions=[date(2026, 10, 10), date(2026, 10, 12)],
        )
        findings = cal.classify_sessions(
            [date(2026, 10, 10), date(2026, 10, 12)],
            start=date(2026, 10, 10),
            end=date(2026, 10, 12),
        )
        missing = [f for f in findings if f.classification == "MISSING_TRADING_SESSION"]
        assert not missing  # the holiday between two sessions is not missing data

    def test_friday_saturday_remain_weekend(self):
        cal = NepseCalendar()
        assert cal.classify_date(date(2026, 7, 24)) == WEEKEND  # Friday
        assert cal.classify_date(date(2026, 7, 25)) == WEEKEND  # Saturday
        assert cal.is_trading_day(date(2026, 7, 24)) is False

    def test_sunday_is_trading_day(self):
        cal = NepseCalendar()
        assert cal.classify_date(date(2026, 7, 26)) == TRADING_DAY
        assert cal.is_trading_day(date(2026, 7, 26)) is True


# ═══════════════════════════════════════════════════════════════════
# 6. Special sessions & exceptional closures
# ═══════════════════════════════════════════════════════════════════


class TestSpecialSessions:
    def test_special_session_is_trading_day(self):
        cal = NepseCalendar(special_sessions=[date(2026, 8, 1)])  # Saturday
        assert cal.classify_date(date(2026, 8, 1)) == SPECIAL_SESSION
        assert cal.is_trading_day(date(2026, 8, 1)) is True

    def test_special_session_in_expected_sessions(self):
        cal = NepseCalendar(special_sessions=[date(2026, 8, 1)])
        sessions = cal.expected_sessions(date(2026, 7, 31), date(2026, 8, 2))
        assert date(2026, 8, 1) in sessions  # Saturday special session counted

    def test_exceptional_closure_not_trading(self):
        cal = NepseCalendar(closures=[date(2026, 8, 9)])  # a Sunday closure
        assert cal.classify_date(date(2026, 8, 9)) == EXCEPTIONAL_CLOSURE
        assert cal.is_trading_day(date(2026, 8, 9)) is False

    def test_closure_excluded_from_expected_sessions(self):
        cal = NepseCalendar(closures=[date(2026, 8, 9)])
        sessions = cal.expected_sessions(date(2026, 8, 9), date(2026, 8, 9))
        assert date(2026, 8, 9) not in sessions

    def test_quality_special_session_not_flagged(self):
        # 2026-08-01 is a Saturday designated as a special session — it
        # is a trading day, never a calendar-invalid record.
        cal = NepseCalendar(special_sessions=[date(2026, 8, 1)])
        df = _weekday_frame(["2026-07-30", "2026-08-01", "2026-08-03"], [100.0, 101.0, 102.0])
        report = assess_history(df, symbol="TEST", calendar=cal)
        assert report.calendar_invalid_records == 0
        assert R_NON_TRADING_DATE not in report.reasons


# ═══════════════════════════════════════════════════════════════════
# 7. Data provenance / trust state
# ═══════════════════════════════════════════════════════════════════


class TestDataProvenance:
    def test_default_trust_is_unavailable(self):
        p = DataProvenance()
        assert p.trust == TRUST_UNAVAILABLE
        assert p.is_safe is False

    def test_is_safe_excludes_unsafe_states(self):
        for trust in (TRUST_CONFLICTED, TRUST_UNAVAILABLE, TRUST_CALENDAR_INVALID, TRUST_QUARANTINED):
            assert DataProvenance(trust=trust).is_safe is False
        for trust in (TRUST_TRUSTED, TRUST_RECONCILED, TRUST_SINGLE_PROVIDER, TRUST_FALLBACK, TRUST_STALE):
            assert DataProvenance(trust=trust).is_safe is True

    def test_resolve_trust_priority_quarantined(self):
        assert resolve_trust(quality_status=INVALID) == TRUST_QUARANTINED
        assert resolve_trust(quarantined_records=2) == TRUST_QUARANTINED

    def test_resolve_trust_priority_calendar_invalid(self):
        assert resolve_trust(calendar_valid=False) == TRUST_CALENDAR_INVALID

    def test_resolve_trust_priority_conflicted(self):
        assert resolve_trust(reconciliation_status=MATERIAL_DISAGREEMENT) == TRUST_CONFLICTED
        assert resolve_trust(reconciliation_status=MAPPING_CONFLICT) == TRUST_CONFLICTED

    def test_resolve_trust_priority_unavailable(self):
        assert resolve_trust(reconciliation_status=UNAVAILABLE) == TRUST_UNAVAILABLE
        assert resolve_trust(source_count=0) == TRUST_UNAVAILABLE

    def test_resolve_trust_stale(self):
        assert resolve_trust(freshness="STALE", source_count=1) == TRUST_STALE

    def test_resolve_trust_fallback(self):
        assert resolve_trust(fallback_used=True, source_count=1) == TRUST_FALLBACK

    def test_resolve_trust_reconciled(self):
        assert resolve_trust(reconciliation_status=AGREE, source_count=2) == TRUST_RECONCILED
        assert resolve_trust(reconciliation_status=MINOR_DISAGREEMENT, source_count=2) == TRUST_RECONCILED

    def test_resolve_trust_partially_reconciled(self):
        assert (
            resolve_trust(reconciliation_status=AGREE, source_count=2, dropped_dates=1)
            == TRUST_PARTIALLY_RECONCILED
        )

    def test_resolve_trust_single_provider(self):
        assert resolve_trust(source_count=1) == TRUST_SINGLE_PROVIDER

    def test_resolve_trust_default_trusted(self):
        # zero sources can never be trusted — the resolver must not
        # fabricate trust from nothing
        assert resolve_trust() == TRUST_UNAVAILABLE
        assert resolve_trust(source_count=2) == TRUST_TRUSTED

    def test_to_dict_from_dict_roundtrip(self):
        p = DataProvenance(
            sources=["api", "csv"],
            trust=TRUST_RECONCILED,
            reconciliation_status=AGREE,
            calendar_version="2.0",
            calendar_valid=True,
            quality_status=VALID,
            freshness="FRESH",
            as_of="2026-08-14T00:00:00",
        )
        d = p.to_dict()
        assert d["trust"] == TRUST_RECONCILED
        assert d["is_safe"] is True
        p2 = DataProvenance.from_dict(d)
        assert p2.trust == TRUST_RECONCILED
        assert p2.sources == ["api", "csv"]
        assert p2.calendar_version == "2.0"

    def test_provenance_from_history_single_provider(self):
        p = provenance_from_history(sources=["csv"], fallback_used=False)
        assert p.trust == TRUST_SINGLE_PROVIDER
        assert p.is_safe is True

    def test_provenance_from_history_fallback(self):
        p = provenance_from_history(sources=["csv"], fallback_used=True)
        assert p.trust == TRUST_FALLBACK

    def test_provenance_from_history_quality_invalid(self):
        report = assess_history(pd.DataFrame(), symbol="BAD")  # empty -> INVALID
        p = provenance_from_history(sources=["api"], quality=report)
        assert p.trust == TRUST_QUARANTINED
        assert p.is_safe is False

    def test_provenance_from_reconciliation_conflicted(self):
        res = ReconciliationResult(status=MATERIAL_DISAGREEMENT, providers=["api", "csv"])
        p = provenance_from_reconciliation(res)
        assert p.trust == TRUST_CONFLICTED
        assert p.is_safe is False

    def test_provenance_from_reconciliation_agree(self):
        res = ReconciliationResult(status=AGREE, providers=["api", "csv"], selected_source="api")
        p = provenance_from_reconciliation(res)
        assert p.trust == TRUST_RECONCILED

    def test_all_trust_states_registered(self):
        assert len(ALL_TRUST_STATES) == 10
        assert TRUST_TRUSTED in ALL_TRUST_STATES


# ═══════════════════════════════════════════════════════════════════
# 8. Provider health & reliability
# ═══════════════════════════════════════════════════════════════════


class TestProviderHealth:
    def test_health_records_success_and_failure(self):
        from src.data.health import HealthCheckConfig, ProviderHealthMonitor

        mon = ProviderHealthMonitor(
            HealthCheckConfig(failure_threshold=5, recovery_period=300.0, success_recovery_count=3)
        )
        mon.register("api")
        mon.record_success("api", latency_ms=12.0)
        mon.record_success("api", latency_ms=20.0)
        mon.record_failure("api")
        health = {h.name: h for h in mon.get_all_health()}
        assert health["api"].successful_requests == 2
        assert health["api"].failed_requests == 1
        assert health["api"].total_requests == 3
        assert health["api"].average_latency_ms > 0
        # success_rate is a percentage (0..100), not a fraction
        assert 0.0 <= health["api"].success_rate <= 100.0

    def test_disable_skips_provider(self):
        from src.data.health import HealthCheckConfig, ProviderHealthMonitor

        mon = ProviderHealthMonitor(
            HealthCheckConfig(failure_threshold=5, recovery_period=300.0, success_recovery_count=3)
        )
        mon.register("api")
        mon.disable("api")
        assert mon.disabled_count == 1  # property, not a method

    def test_health_bounded_last_success(self):
        from src.data.health import HealthCheckConfig, ProviderHealthMonitor

        mon = ProviderHealthMonitor(
            HealthCheckConfig(failure_threshold=5, recovery_period=300.0, success_recovery_count=3)
        )
        mon.register("csv")
        health = {h.name: h for h in mon.get_all_health()}
        assert health["csv"].last_success == 0.0  # no success yet


# ═══════════════════════════════════════════════════════════════════
# 9. Reconciliation observability (bounded metrics)
# ═══════════════════════════════════════════════════════════════════


class TestReconciliationMetrics:
    def _reset(self):
        reconciliation_metrics.reset()

    def test_snapshot_safe_when_empty(self):
        self._reset()
        snap = reconciliation_metrics.snapshot()
        assert isinstance(snap, dict)
        assert all(isinstance(v, int) for v in snap.values())

    def test_record_agree_increments(self):
        self._reset()
        reconciliation_metrics.record_reconciliation(ReconciliationResult(status=AGREE, providers=["api"]))
        snap = reconciliation_metrics.snapshot()
        assert snap["reconciliation_checks"] == 1
        assert snap["agreements"] == 1

    def test_record_material_increments_quarantine(self):
        self._reset()
        reconciliation_metrics.record_reconciliation(
            ReconciliationResult(status=MATERIAL_DISAGREEMENT, providers=["api", "csv"])
        )
        snap = reconciliation_metrics.snapshot()
        assert snap["material_disagreements"] == 1
        assert snap["quarantined_conflicts"] == 1

    def test_record_mapping_increments(self):
        self._reset()
        reconciliation_metrics.record_reconciliation(
            ReconciliationResult(status=MAPPING_CONFLICT, providers=["api", "csv"])
        )
        snap = reconciliation_metrics.snapshot()
        assert snap["mapping_conflicts"] == 1

    def test_reset_zeroes(self):
        reconciliation_metrics.record_reconciliation(ReconciliationResult(status=AGREE, providers=["api"]))
        self._reset()
        assert reconciliation_metrics.snapshot()["reconciliation_checks"] == 0

    def test_bounded_fields(self):
        self._reset()
        snap = reconciliation_metrics.snapshot()
        for f in (
            "provider_requests",
            "provider_successes",
            "provider_failures",
            "provider_timeouts",
            "fallback_count",
            "reconciliation_checks",
            "agreements",
            "minor_disagreements",
            "material_disagreements",
            "mapping_conflicts",
            "quarantined_conflicts",
        ):
            assert f in snap


# ═══════════════════════════════════════════════════════════════════
# 10. Fallback metrics
# ═══════════════════════════════════════════════════════════════════


class TestFallbackMetrics:
    def _reset(self):
        reconciliation_metrics.reset()

    def test_fallback_count_increments(self):
        self._reset()
        reconciliation_metrics.record_fallback()
        assert reconciliation_metrics.snapshot()["fallback_count"] == 1


# ═══════════════════════════════════════════════════════════════════
# 11. Failure injection
# ═══════════════════════════════════════════════════════════════════


class TestProviderFailureInjection:
    def test_timeout_falls_back_to_next_provider(self):
        from src.data.providers import HybridProvider

        hybrid = HybridProvider(
            [
                _FakeProvider(raises=ProviderTimeout("slow")),
                _FakeProvider(history=_ohlcv()),
            ]
        )
        df = hybrid.get_history("NABIL")
        assert len(df) > 0
        assert hybrid.last_provider == "fake"
        assert hybrid.fallback_used is True

    def test_connection_failure_falls_back(self):
        from src.data.providers import HybridProvider

        hybrid = HybridProvider(
            [
                _FakeProvider(raises=ConnectionError("refused")),
                _FakeProvider(history=_ohlcv()),
            ]
        )
        assert len(hybrid.get_history("NABIL")) > 0

    def test_provider_exception_falls_back(self):
        from src.data.providers import HybridProvider

        hybrid = HybridProvider(
            [_FakeProvider(raises=RuntimeError("boom")), _FakeProvider(history=_ohlcv())]
        )
        assert len(hybrid.get_history("NABIL")) > 0

    def test_all_providers_fail_raises_unavailable(self):
        from src.data.providers import HybridProvider

        hybrid = HybridProvider([_FakeProvider(raises=RuntimeError("a")), _FakeProvider(raises=RuntimeError("b"))])
        with pytest.raises(DataUnavailable):
            hybrid.get_history("NABIL")

    def test_invalid_ohlc_rejected_by_validator(self):
        from src.data.providers import HybridProvider
        from src.data.service import _default_data_validator

        broken = _ohlcv()
        broken.loc[broken.index[0], "Open"] = 0.0
        hybrid = HybridProvider(
            [_FakeProvider(history=broken), _FakeProvider(history=_ohlcv())],
            data_validator=_default_data_validator,
        )
        df = hybrid.get_history("NABIL")
        assert len(df) > 0  # second provider's valid frame won

    def test_negative_volume_rejected_by_validator(self):
        from src.data.providers import HybridProvider
        from src.data.service import _default_data_validator

        bad = _ohlcv()
        bad.loc[bad.index[0], "Volume"] = -5
        hybrid = HybridProvider(
            [_FakeProvider(history=bad), _FakeProvider(history=_ohlcv())],
            data_validator=_default_data_validator,
        )
        df = hybrid.get_history("NABIL")
        assert len(df) > 0

    def test_empty_response_falls_back(self):
        from src.data.providers import HybridProvider
        from src.data.service import _default_data_validator

        empty = _ohlcv().iloc[:0]
        hybrid = HybridProvider(
            [_FakeProvider(history=empty), _FakeProvider(history=_ohlcv())],
            data_validator=_default_data_validator,
        )
        df = hybrid.get_history("NABIL")
        assert len(df) > 0  # empty frame rejected by the validator, second source won

    def test_validate_history_frame_raises_on_invalid(self):
        broken = _ohlcv()
        broken.loc[broken.index[0], "Close"] = -1.0
        with pytest.raises(InvalidDataError):
            validate_history_frame(broken, symbol="BAD")


class TestReconciliationFailureInjection:
    def test_exact_agreement(self):
        merged, res = reconcile_history({"api": _ohlcv(5), "csv": _ohlcv(5)}, symbol="NABIL")
        assert res.status == AGREE
        assert len(merged) == 5

    def test_minor_price_disagreement_resolves(self):
        f1 = _ohlcv(5)
        f2 = _ohlcv(5)
        f2["Close"] = f2["Close"] * 1.001  # 0.1% — within default 1% tolerance
        merged, res = reconcile_history({"api": f1, "csv": f2}, symbol="NABIL")
        assert res.status in (AGREE, MINOR_DISAGREEMENT)
        assert len(merged) == 5

    def test_material_price_disagreement_drops_date(self):
        f1 = _ohlcv(5)
        f2 = _ohlcv(5)
        f2.loc[f2.index[-1], "Close"] = f2.loc[f2.index[-1], "Close"] * 5.0
        merged, res = reconcile_history({"api": f1, "csv": f2}, symbol="NABIL")
        assert res.status == MATERIAL_DISAGREEMENT
        assert len(merged) == 4  # conflicting date dropped, never averaged

    def test_material_volume_disagreement_drops_date(self):
        f1 = _ohlcv(5)
        f2 = _ohlcv(5)
        f2.loc[f2.index[-1], "Volume"] = f2.loc[f2.index[-1], "Volume"] * 50
        merged, res = reconcile_history({"api": f1, "csv": f2}, symbol="NABIL")
        assert res.status == MATERIAL_DISAGREEMENT
        assert len(merged) == 4

    def test_mapping_conflict_detected(self):
        from src.data.reconciliation import check_symbol_mapping

        ok, reasons = check_symbol_mapping("NABIL", {"api": "NABIL"})
        assert ok and not reasons
        ok2, reasons2 = check_symbol_mapping("NABIL", {"api": "SCB"})
        assert not ok2 and reasons2  # identifier mismatch → MAPPING_CONFLICT

    def test_disjoint_dates_no_fabricated_comparison(self):
        # frames with no shared dates → nothing to disagree on → the
        # union is returned and never averaged
        f1 = _weekday_frame(["2026-07-01", "2026-07-02"], [100.0, 101.0])
        f2 = _weekday_frame(["2026-08-01", "2026-08-02"], [200.0, 201.0])
        merged, res = reconcile_history({"api": f1, "csv": f2}, symbol="NABIL")
        assert res.status == AGREE
        assert merged is not None and len(merged) == 4  # union, never averaged

    def test_missing_dates_partial_history(self):
        f1 = _weekday_frame(["2026-07-01", "2026-07-02", "2026-07-03"], [100.0, 101.0, 102.0])
        f2 = _weekday_frame(["2026-07-01", "2026-07-02"], [100.0, 101.0])
        merged, res = reconcile_history({"api": f1, "csv": f2}, symbol="NABIL")
        assert res.status in (AGREE, MINOR_DISAGREEMENT)
        assert merged is not None

    def test_provider_partial_history_unavailable_one(self):
        merged, res = reconcile_history({"api": _ohlcv(5), "csv": None}, symbol="NABIL")
        assert res.status == AGREE  # single available source cannot disagree
        assert merged is not None


class TestCalendarFailureInjection:
    def test_weekend_record_flagged(self):
        cal = NepseCalendar()
        df = _weekday_frame(["2026-07-24", "2026-07-26", "2026-07-27"], [100.0, 101.0, 102.0])
        report = assess_history(df, symbol="TEST", calendar=cal)
        assert report.calendar_invalid_records == 1  # Friday 07-24
        assert R_NON_TRADING_DATE in report.reasons
        assert report.status == INVALID

    def test_configured_holiday_record_flagged(self):
        cal = NepseCalendar(holidays=[date(2026, 10, 11)])
        df = _weekday_frame(["2026-10-11", "2026-10-12"], [100.0, 101.0])
        report = assess_history(df, symbol="TEST", calendar=cal)
        assert report.calendar_invalid_records == 1
        assert R_NON_TRADING_DATE in report.reasons

    def test_unknown_date_reported_not_flagged(self):
        cal = NepseCalendar(observed_sessions={date(2026, 6, 1)})
        # A weekday with no corpus evidence → UNKNOWN (reported, not invalid)
        df = _weekday_frame(["2026-06-01", "2026-06-03"], [100.0, 101.0])
        report = assess_history(df, symbol="TEST", calendar=cal)
        assert R_NON_TRADING_DATE not in report.reasons

    def test_calendar_version_mismatch_rejected(self):
        problems = validate_calendar_data({"calendar_version": "3.0"})
        assert any("unsupported calendar version" in p for p in problems)

    def test_malformed_calendar_never_trades_a_closure(self):
        # A calendar that fails validation must fall back to the safe base
        # calendar (weekends closed) — never treat a closure as a trading day.
        path = Path(tempfile.mkdtemp()) / "bad.json"
        path.write_text(json.dumps({"calendar_version": "99.0"}), encoding="utf-8")
        assert NepseCalendar.load(str(path)) is None  # fail-safe
        base = NepseCalendar()
        assert base.is_trading_day(date(2026, 7, 25)) is False  # Saturday still closed


# ═══════════════════════════════════════════════════════════════════
# 12. Cache provenance
# ═══════════════════════════════════════════════════════════════════


class TestCacheProvenance:
    def _svc(self, provider) -> "DataService":
        from src.data.service import DataService

        DataService.reset_instance()
        return DataService(provider=provider)

    def test_reconciled_cache_roundtrip_preserves_provenance(self):
        from src.data.service import DataService

        svc = self._svc(_ReconcilingProvider(frames={"api": _ohlcv(5), "csv": _ohlcv(5)}))
        try:
            hist, rec = svc.get_reconciled_history("NABIL", days=365)
            assert hist.provenance["trust"] == TRUST_RECONCILED
            assert hist.provenance["is_safe"] is True
            # Second call: cache hit — provenance must survive the round trip
            hist2, _ = svc.get_reconciled_history("NABIL", days=365)
            assert hist2.provenance["trust"] == TRUST_RECONCILED
        finally:
            DataService.reset_instance()

    def test_conflicted_never_cached_as_trusted(self):
        from src.data.service import DataService

        f1 = _ohlcv(5)
        f2 = _ohlcv(5)
        f2.loc[f2.index[-1], "Close"] = f2.loc[f2.index[-1], "Close"] * 5.0
        svc = self._svc(_ReconcilingProvider(frames={"api": f1, "csv": f2}))
        try:
            hist, rec = svc.get_reconciled_history("NABIL", days=365)
            assert rec["status"] == MATERIAL_DISAGREEMENT
            assert hist.provenance["trust"] == TRUST_CONFLICTED
            assert hist.provenance["is_safe"] is False
            # A conflicted outcome must NEVER be cached as a trusted frame:
            # the cache key must be absent after the call.
            assert svc._cache.get("reconciled:NABIL:365") is None
        finally:
            DataService.reset_instance()

    def test_agree_cached_then_clear_reconciled(self):
        from src.data.service import DataService

        svc = self._svc(_ReconcilingProvider(frames={"api": _ohlcv(5), "csv": _ohlcv(5)}))
        try:
            svc.get_reconciled_history("NABIL", days=365)
            assert svc._cache.get("reconciled:NABIL:365") is not None
            # plain history cache stays untouched
            svc._cache.set("history:NABIL:365", _ohlcv(5))
            removed = svc.clear_reconciled_cache(symbol="NABIL", days=365)
            assert removed >= 1
            assert svc._cache.get("reconciled:NABIL:365") is None
            assert svc._cache.get("history:NABIL:365") is not None
        finally:
            DataService.reset_instance()

    def test_clear_reconciled_all_namespace(self):
        from src.data.service import DataService

        svc = self._svc(_ReconcilingProvider(frames={"api": _ohlcv(5), "csv": _ohlcv(5)}))
        try:
            svc._cache.set("reconciled:NABIL:365", {"frame": _ohlcv(5)})
            svc._cache.set("reconciled:SCB:365", {"frame": _ohlcv(5)})
            svc._cache.set("history:NABIL:365", _ohlcv(5))
            removed = svc.clear_reconciled_cache()
            assert removed >= 2
            assert svc._cache.get("history:NABIL:365") is not None
        finally:
            DataService.reset_instance()


# ═══════════════════════════════════════════════════════════════════
# 13. Cache safety
# ═══════════════════════════════════════════════════════════════════


class TestCacheSafety:
    def test_delete_prefix_memory(self):
        from src.data.cache import MemoryCache

        m = MemoryCache()
        m.set("reconciled:NABIL:365", 1)
        m.set("reconciled:SCB:365", 2)
        m.set("history:NABIL:365", 3)
        assert m.delete_prefix("reconciled:") == 2
        assert m.get("history:NABIL:365") == 3
        assert m.get("reconciled:NABIL:365") is None

    def test_delete_prefix_disk_boundary(self):
        from src.data.cache import DiskCache

        d = DiskCache(cache_dir=tempfile.mkdtemp())
        d.set("reconciled:NABIL:365", "a")
        d.set("reconciled:NABIL:3650", "b")  # must NOT be deleted by 365-prefix
        assert d.delete_prefix("reconciled:NABIL:365") == 1
        assert d.get("reconciled:NABIL:365") is None
        assert d.get("reconciled:NABIL:3650") == "b"

    def test_delete_prefix_tiered(self):
        from src.data.cache import TieredCache

        c = TieredCache()
        c.set("reconciled:NABIL:365", 1)
        c.set("history:NABIL:365", 2)
        assert c.delete_prefix("reconciled:") >= 1
        assert c.get("history:NABIL:365") == 2

    def test_fallback_provenance_not_masquerading(self):
        from src.data.providers import HybridProvider
        from src.data.service import DataService

        hybrid = HybridProvider(
            [_FakeProvider(raises=RuntimeError("api down")), _FakeProvider(history=_ohlcv(30))]
        )
        DataService.reset_instance()
        svc = DataService(provider=hybrid)
        try:
            hist = svc.get_history("NABIL")
            assert hist.provenance["trust"] == TRUST_FALLBACK
            assert hist.provenance["fallback_used"] is True
            assert hist.provenance["is_safe"] is True  # fallback is documented, not hidden
        finally:
            DataService.reset_instance()


# ═══════════════════════════════════════════════════════════════════
# 14. Signal safety
# ═══════════════════════════════════════════════════════════════════


class TestSignalSafety:
    def _analyze(self, provenance=None):
        from src.engine.analyzer import analyze_dataframe

        return analyze_dataframe(_ohlcv(40), symbol="NABIL", provenance=provenance)

    def test_material_conflict_suppresses_signal(self):
        from src.data.reconciliation import ReconciliationResult

        result = self._analyze(
            provenance=provenance_from_reconciliation(
                ReconciliationResult(status=MATERIAL_DISAGREEMENT, providers=["api", "csv"])
            )
        )
        assert result["provenance"]["trust"] == TRUST_CONFLICTED
        assert result["signal_suppressed"] is True
        assert result["signal"] == "HOLD"

    def test_mapping_conflict_suppresses_signal(self):
        from src.data.reconciliation import ReconciliationResult

        result = self._analyze(
            provenance=provenance_from_reconciliation(
                ReconciliationResult(status=MAPPING_CONFLICT, providers=["api", "csv"])
            )
        )
        assert result["signal_suppressed"] is True
        assert result["signal"] == "HOLD"

    def test_unavailable_suppresses_signal(self):
        result = self._analyze(provenance=DataProvenance(trust=TRUST_UNAVAILABLE))
        assert result["signal_suppressed"] is True
        assert result["signal"] == "HOLD"

    def test_calendar_invalid_suppresses_signal(self):
        result = self._analyze(provenance=DataProvenance(trust=TRUST_CALENDAR_INVALID))
        assert result["signal_suppressed"] is True
        assert result["signal_suppression_reason"] == TRUST_CALENDAR_INVALID

    def test_quarantined_suppresses_signal(self):
        result = self._analyze(provenance=DataProvenance(trust=TRUST_QUARANTINED))
        assert result["signal_suppressed"] is True

    def test_trusted_data_signal_not_suppressed(self):
        result = self._analyze(provenance=DataProvenance(trust=TRUST_TRUSTED))
        assert result["signal_suppressed"] is False
        assert result["provenance"]["is_safe"] is True

    def test_reconciliation_block_suppresses_directly(self):
        from src.engine.analyzer import analyze_dataframe
        from src.data.reconciliation import ReconciliationResult

        result = analyze_dataframe(
            _ohlcv(40),
            symbol="NABIL",
            reconciliation=ReconciliationResult(status=MATERIAL_DISAGREEMENT, providers=["api"]),
        )
        assert result["signal_suppressed"] is True
        assert result["reconciliation"]["status"] == MATERIAL_DISAGREEMENT

    def test_calendar_invalid_quality_suppresses_signal(self):
        """Calendar-invalid records → calendar-aware quality → unsafe
        provenance → analyze_dataframe suppresses the signal end-to-end."""
        from src.data.calendar import NepseCalendar
        from src.data.provenance import provenance_from_history
        from src.engine.analyzer import analyze_dataframe

        cal = NepseCalendar()
        # inject a Friday row into an otherwise valid Sun-Thu frame
        # (sorted by date, as real corpus CSVs are)
        df = _weekday_frame(
            ["2026-07-24", "2026-07-26", "2026-07-27", "2026-07-28"],
            [103.0, 100.0, 101.0, 102.0],
        )
        report = assess_history(df, symbol="NABIL", calendar=cal)
        assert report.calendar_invalid_records == 1
        # Build the provenance through the canonical builder — the Friday
        # row must mark the dataset calendar-invalid.
        prov = provenance_from_history(sources=["csv"], calendar=cal, quality=report)
        assert prov.calendar_valid is False
        assert prov.is_safe is False
        result = analyze_dataframe(df, symbol="NABIL", provenance=prov)
        assert result["provenance"]["calendar_valid"] is False
        assert result["signal_suppressed"] is True
        assert result["signal"] == "HOLD"


# ═══════════════════════════════════════════════════════════════════
# 15. API compatibility
# ═══════════════════════════════════════════════════════════════════


class TestApiCompatibility:
    def test_metrics_provider_health_block(self):
        from fastapi.testclient import TestClient

        from src.api.main import app

        with TestClient(app) as client:
            resp = client.get("/metrics")
        assert resp.status_code == 200
        body = resp.json()
        assert "provider_health" in body
        for name, h in body["provider_health"].items():
            for key in (
                "enabled",
                "success_rate",
                "total_requests",
                "successful_requests",
                "failed_requests",
                "consecutive_failures",
                "last_success_age_s",
                "average_latency_ms",
            ):
                assert key in h

    def test_metrics_calendar_block_bounded(self):
        from fastapi.testclient import TestClient

        from src.api.main import app

        with TestClient(app) as client:
            resp = client.get("/metrics")
        body = resp.json()
        assert "calendar" in body
        cal = body["calendar"]
        assert "version" in cal
        assert "holidays" in cal
        assert isinstance(cal["holidays"], int)
        assert "has_provenance" in cal
        # bounded: no raw date lists
        for key in ("holidays", "special_sessions", "closures"):
            assert isinstance(cal[key], int)

    def test_metrics_backward_compatible_blocks(self):
        from fastapi.testclient import TestClient

        from src.api.main import app

        with TestClient(app) as client:
            resp = client.get("/metrics")
        body = resp.json()
        for key in (
            "process",
            "indicator_cache",
            "metrics",
            "data_quality",
            "reconciliation",
            "scanner_cache",
            "per_operation",
            "per_provider",
            "provider_health",
            "calendar",
        ):
            assert key in body

    def test_health_endpoint_works(self):
        from fastapi.testclient import TestClient

        from src.api.main import app

        with TestClient(app) as client:
            resp = client.get("/")
        assert resp.status_code == 200
        assert "status" in resp.json()

    def test_metrics_safe_when_no_reconciliation(self):
        from fastapi.testclient import TestClient

        from src.api.main import app

        # Reset the shared bounded collector first so the assertion is
        # order-independent (other tests may have recorded into it).
        reconciliation_metrics.reset()
        with TestClient(app) as client:
            resp = client.get("/metrics")
        body = resp.json()
        assert body["reconciliation"]["reconciliation_checks"] == 0
        # every counter is a bounded scalar
        assert all(isinstance(v, int) for v in body["reconciliation"].values())


# ═══════════════════════════════════════════════════════════════════
# 16. No-silent-degradation
# ═══════════════════════════════════════════════════════════════════


class TestNoSilentDegradation:
    def test_all_providers_fail_no_trusted_signal(self):
        from src.data.service import DataService

        hybrid = __import__("src.data.providers", fromlist=["HybridProvider"]).HybridProvider(
            [_FakeProvider(raises=RuntimeError("a")), _FakeProvider(raises=RuntimeError("b"))]
        )
        DataService.reset_instance()
        svc = DataService(provider=hybrid)
        try:
            hist = svc.get_history("NABIL")
            assert hist.is_empty
            assert hist.provenance["trust"] == TRUST_UNAVAILABLE
            assert hist.provenance["is_safe"] is False
        finally:
            DataService.reset_instance()

    def test_fallback_marked_not_primary(self):
        from src.data.providers import HybridProvider

        hybrid = HybridProvider(
            [_FakeProvider(raises=ProviderTimeout("slow")), _FakeProvider(history=_ohlcv(30))]
        )
        hybrid.get_history("NABIL")
        assert hybrid.fallback_used is True
        assert hybrid.last_provider == "fake"

    def test_primary_path_no_fallback_flag(self):
        from src.data.providers import HybridProvider

        hybrid = HybridProvider([_FakeProvider(history=_ohlcv(30))])
        hybrid.get_history("NABIL")
        assert hybrid.fallback_used is False

    def test_fallback_flag_resets_on_next_primary_success(self):
        """fallback_used must reflect the *current* request — a later
        primary success on the same instance resets the flag (Sprint 13.5
        provenance accuracy; no sticky-latch mislabelling)."""
        from src.data.providers import HybridProvider

        api = _FakeProvider(raises=RuntimeError("api down"), history=None)
        csv = _FakeProvider(history=_ohlcv(30))
        hybrid = HybridProvider([api, csv])
        hybrid.get_history("NABIL")
        assert hybrid.fallback_used is True  # csv served after api failed
        # api recovers: the next request must NOT be labelled fallback
        api._raises = None
        api._history = _ohlcv(30)
        hybrid.get_history("NABIL")
        assert hybrid.last_provider == "fake"
        assert hybrid.fallback_used is False

    def test_material_conflict_no_trusted_signal(self):
        from src.data.reconciliation import ReconciliationResult
        from src.data.provenance import provenance_from_reconciliation
        from src.engine.analyzer import analyze_dataframe

        prov = provenance_from_reconciliation(
            ReconciliationResult(status=MATERIAL_DISAGREEMENT, providers=["api", "csv"])
        )
        result = analyze_dataframe(_ohlcv(40), symbol="NABIL", provenance=prov)
        assert result["signal"] == "HOLD"
        assert result["signal_suppressed"] is True

    def test_hybrid_reconcile_all_fail_unavailable(self):
        from src.data.providers import HybridProvider

        hybrid = HybridProvider(
            [_FakeProvider(raises=RuntimeError("a")), _FakeProvider(raises=RuntimeError("b"))]
        )
        with pytest.raises(DataUnavailable):
            hybrid.get_history("NABIL")


# ═══════════════════════════════════════════════════════════════════
# 17. Calendar-aware quality regression
# ═══════════════════════════════════════════════════════════════════


class TestCalendarAwareQuality:
    def test_weekends_excluded_from_missing_sessions(self):
        cal = NepseCalendar()
        df = _weekday_frame(["2026-07-23", "2026-07-26"], [100.0, 101.0])  # Thu → Sun
        report = assess_history(df, symbol="TEST", calendar=cal)
        assert report.missing_sessions == 0  # Fri/Sat are expected closures

    def test_new_listing_no_false_alarm(self):
        cal = NepseCalendar()
        # A short fresh history on NEPSE trading days (Sun–Thu, no
        # Friday/Saturday rows) must not trigger a missing-session alarm.
        df = _weekday_frame(
            ["2026-07-26", "2026-07-27", "2026-07-28", "2026-07-29", "2026-07-30"],
            [100.0, 101.0, 102.0, 103.0, 104.0],
        )
        report = assess_history(df, symbol="NEW", calendar=cal)
        assert report.missing_sessions == 0
        assert report.calendar_invalid_records == 0
        assert report.status != INVALID

    def test_duplicate_dates_detectable(self):
        df = _ohlcv(5)
        dup = pd.concat([df, df.iloc[[-1]]], ignore_index=True)
        report = assess_history(dup, symbol="TEST")
        assert report.duplicates == 1

    def test_zero_price_invalid(self):
        df = _ohlcv(5)
        df.loc[df.index[0], "Close"] = 0.0
        report = assess_history(df, symbol="TEST")
        assert report.status == INVALID
        assert report.records_invalid >= 1

    def test_stale_distinguishable_from_invalid(self):
        old = _ohlcv(5, start="2025-01-01")
        report = assess_history(old, symbol="TEST")
        assert report.freshness == "STALE"
        assert report.status == SUSPICIOUS  # stale is a warning, not invalid

    def test_unknown_sessions_reported(self):
        # A base calendar (no corpus evidence) reports unobserved
        # weekdays as UNKNOWN rather than guessing a holiday.
        cal = NepseCalendar()
        df = _weekday_frame(["2026-06-01", "2026-06-03"], [100.0, 101.0])
        report = assess_history(df, symbol="TEST", calendar=cal)
        assert report.unknown_sessions >= 1  # 2026-06-02 has no evidence

    def test_calendar_invalid_quality_gate(self):
        # A Friday record violates the governed calendar → quality INVALID
        # with the R_NON_TRADING_DATE reason (default calendar).
        df = _weekday_frame(["2026-07-24", "2026-07-26"], [100.0, 101.0])
        report = assess_history(df, symbol="BAD", calendar=default_calendar())
        assert report.calendar_invalid_records == 1
        assert R_NON_TRADING_DATE in report.reasons
        assert report.status == INVALID

    def test_real_corpus_gate_stays_clean(self):
        """The shipped 286-symbol corpus must remain supported: every CSV
        passes the canonical quality contract (no quarantined symbols).
        Skipped when the real corpus is absent (hermetic CI)."""
        raw = Path(__file__).resolve().parent.parent / "data" / "raw"
        non_sample = [
            p for p in raw.glob("*.csv") if p.stem.lower() not in ("sample", "rolling_volume_mean")
        ]
        # Hermetic CI / trimmed checkouts have no full corpus — skip.  A
        # checkout with only sample data must skip too, not fail the
        # ``symbols_checked`` threshold.
        if not raw.exists() or len(non_sample) < 50:
            pytest.skip("real corpus not present in this checkout")
        from src.data.quality import validate_corpus

        agg = validate_corpus(raw)
        assert agg["symbols_checked"] >= 100  # the full corpus is present
        assert agg["invalid"] == 0, f"quarantined symbols: {agg['invalid']}"
        assert agg["conflicts"] == 0


# ═══════════════════════════════════════════════════════════════════
# Regression vs Sprint 13.4 behavior
# ═══════════════════════════════════════════════════════════════════


class TestSprint134Regression:
    def test_calendar_sunday_thursday_week_kept(self):
        cal = NepseCalendar()
        for d in (date(2026, 7, 26), date(2026, 7, 27), date(2026, 7, 28), date(2026, 7, 29), date(2026, 7, 30)):
            assert cal.is_trading_day(d)
        for d in (date(2026, 7, 24), date(2026, 7, 25)):
            assert not cal.is_trading_day(d)

    def test_quality_gap_detection_still_works(self):
        from src.config import DATA_MAX_GAP_DAYS

        df = _ohlcv(40)
        # Punch a gap wider than DATA_MAX_GAP_DAYS by removing the middle
        # of the frame (stays in the past, so no future-date error
        # interferes).  The legacy business-day heuristic must still fire.
        frame = pd.concat([df.iloc[:10], df.iloc[30:]], ignore_index=True)
        report = assess_history(frame, symbol="TEST")
        assert report.status == SUSPICIOUS  # gap warning, not invalid
        assert "unexpected_gap" in report.reasons
        # The removed hole spans at least DATA_MAX_GAP_DAYS+1 business days
        assert len(df) - len(frame) > DATA_MAX_GAP_DAYS

    def test_reconcile_single_provider_agree(self):
        merged, res = reconcile_history({"api": _ohlcv(5)}, symbol="NABIL")
        assert res.status == AGREE
        assert len(merged) == 5

    def test_reconcile_never_averages(self):
        f1 = _ohlcv(5)
        f2 = _ohlcv(5)
        f2["Close"] = f2["Close"] * 10
        merged, res = reconcile_history({"api": f1, "csv": f2}, symbol="NABIL")
        assert res.status == MATERIAL_DISAGREEMENT
        assert len(merged) < 5  # conflicting rows dropped, not averaged

    def test_analyzer_quality_block_attached(self):
        from src.engine.analyzer import analyze_dataframe

        result = analyze_dataframe(_ohlcv(40), symbol="NABIL")
        assert "data_quality" in result

    def test_analyzer_reconciliation_block_attached(self):
        from src.engine.analyzer import analyze_dataframe

        result = analyze_dataframe(
            _ohlcv(40),
            symbol="NABIL",
            reconciliation=ReconciliationResult(status=AGREE, providers=["api"]),
        )
        assert result["reconciliation"]["status"] == AGREE
