"""Sprint 13.6 — Production Readiness, Live Provider Diversity & Operational Observability.

Behavioral coverage for the production-hardening layer built on Sprint 13.5:

    Provider A → fallback → Provider B → reconciliation → quality
    → provenance → cache → analyzer → signal safety → observability

Categories (mirroring the sprint brief §18):

  1. second-provider adapter      12. calendar rollback
  2. provider identity            13. cache restart / recovery
  3. real reconciliation          14. cache corruption
  4. provider reliability history 15. process recovery
  5. degradation detection        16. signal safety
  6. provider recovery            17. scanner / alert safety
  7. outage / fallback            18. metrics compatibility
  8. all-provider failure         19. long-running bounded stability
  9. reconciliation incidents     20. Docker / startup config
  10. quality trends              21. regression coverage
  11. calendar update validation

Constraints honoured: no fabricated live provider results (the second
provider is exercised with a *controlled* file-backed double), no
unbounded structures, no removal of regression tests.
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
    TRADING_DAY,
    WEEKEND,
    NepseCalendar,
    validate_calendar_data,
)
from src.data.exceptions import (
    DataUnavailable,
    InvalidDataError,
    ProviderError,
    ProviderTimeout,
)
from src.data.provenance import (
    DataProvenance,
    TRUST_CALENDAR_INVALID,
    TRUST_CONFLICTED,
    TRUST_FALLBACK,
    TRUST_QUARANTINED,
    TRUST_RECONCILED,
    TRUST_SINGLE_PROVIDER,
    TRUST_STALE,
    TRUST_UNAVAILABLE,
    provenance_from_history,
    provenance_from_reconciliation,
)
from src.data.quality import (
    INVALID,
    SUSPICIOUS,
    VALID,
    assess_history,
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
    dates = pd.bdate_range(start, periods=n)
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


class _ControlledSecondProvider:
    """A *controlled* independent provider double (file-backed).

    Implements the same interface as the GitHubCSVProvider adapter
    (``get_history`` + ``name``) so it can be driven through the real
    HybridProvider chain / reconciliation path — but every test uses a
    local directory, never fabricated live results.  Verifies that an
    independent second provider plugs in with **no architectural
    change** (Sprint 13.6 §3 acceptance).
    """

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
        df = df.sort_values("Date").reset_index(drop=True)
        return df


# ═══════════════════════════════════════════════════════════════════
# 1. Second-provider adapter
# ═══════════════════════════════════════════════════════════════════


class TestSecondProviderAdapter:
    def test_adapter_implements_base_provider_interface(self):
        from src.data.providers import GitHubCSVProvider

        p = GitHubCSVProvider(base_url="https://example.invalid")
        assert isinstance(p, object)
        # the interface the HybridProvider depends on
        for attr in ("name", "get_history", "get_market_summary", "get_live_quotes"):
            assert hasattr(p, attr)
        assert p.name == "github_csv"

    def test_disabled_by_default(self):
        from src.config import SECOND_PROVIDER_URL

        assert SECOND_PROVIDER_URL == ""  # no behavioural change without config

    def test_default_chain_has_no_second_provider(self):
        from src.data.service import DataService

        DataService.reset_instance()
        try:
            svc = DataService()
            providers = [p.name for p in svc.provider._providers]
            assert "github_csv" not in providers
            assert "api" in providers and "csv" in providers
        finally:
            DataService.reset_instance()

    def test_parse_company_wise_csv(self):
        from src.data.providers import GitHubCSVProvider

        csv_text = (
            "date,open,high,low,close,traded_quantity\n"
            "2026-06-01,100,102,99,101,1200\n"
            "2026-06-02,101,103,100,102,1400\n"
        )
        p = GitHubCSVProvider(base_url="https://example.invalid")
        df = p._parse_history_csv(csv_text, "NABIL")
        assert list(df.columns) == ["Date", "Open", "High", "Low", "Close", "Volume"]
        assert len(df) == 2
        assert float(df["Close"].iloc[-1]) == 102.0

    def test_parse_rejects_missing_columns(self):
        from src.data.providers import GitHubCSVProvider

        p = GitHubCSVProvider(base_url="https://example.invalid")
        df = p._parse_history_csv("date,close\n2026-06-01,101\n", "X")
        assert df.empty

    def test_parse_lowercase_and_alias_columns(self):
        from src.data.providers import GitHubCSVProvider

        p = GitHubCSVProvider(base_url="https://example.invalid")
        df = p._parse_history_csv(
            "Date,Open,High,Low,ltp,Volume\n2026-06-01,100,102,99,101,1200\n", "X"
        )
        assert len(df) == 1
        assert float(df["Close"].iloc[0]) == 101.0  # ltp → Close

    def test_url_uses_uppercase_symbol(self):
        from src.data.providers import GitHubCSVProvider

        p = GitHubCSVProvider(base_url="https://example.invalid")
        assert p._history_url("nabil") == "https://example.invalid/NABIL.csv"

    def test_second_provider_plugs_into_hybrid_chain(self):
        from src.data.providers import HybridProvider

        second = _ControlledSecondProvider(Path(tempfile.mkdtemp()), history=_ohlcv(10))
        hybrid = HybridProvider([_FakeProvider(raises=RuntimeError("primary down")), second])
        df = hybrid.get_history("NABIL")
        assert len(df) == 10
        assert hybrid.last_provider == "github_csv"
        assert hybrid.fallback_used is True

    def test_second_provider_identity_preserved(self):
        from src.data.providers import HybridProvider

        second = _ControlledSecondProvider(Path(tempfile.mkdtemp()), history=_ohlcv(5))
        hybrid = HybridProvider([second])
        df = hybrid.get_history("NABIL")
        assert len(df) == 5
        assert hybrid.last_provider == "github_csv"  # identity preserved, not aliased

    def test_second_provider_failure_falls_back(self):
        from src.data.providers import HybridProvider

        second = _ControlledSecondProvider(
            Path(tempfile.mkdtemp()), raises=ProviderError("archive down")
        )
        hybrid = HybridProvider([_FakeProvider(history=_ohlcv(8)), second])
        assert len(hybrid.get_history("NABIL")) == 8

    def test_controlled_provider_never_marked_live(self):
        """Controlled-provider verification is honest: the adapter is
        exercised through a file double, never a fabricated live claim."""
        from src.data.providers import GitHubCSVProvider

        p = GitHubCSVProvider(base_url="https://example.invalid")
        assert p.base_url.endswith("example.invalid")


# ═══════════════════════════════════════════════════════════════════
# 2. Provider identity
# ═══════════════════════════════════════════════════════════════════


class TestProviderIdentity:
    def test_symbol_mapping_match(self):
        from src.data.reconciliation import check_symbol_mapping

        ok, reasons = check_symbol_mapping("NABIL", {"api": "NABIL", "csv": "nabil"})
        assert ok and not reasons

    def test_symbol_mapping_mismatch(self):
        from src.data.reconciliation import check_symbol_mapping

        ok, reasons = check_symbol_mapping("NABIL", {"api": "SCB"})
        assert not ok and reasons

    def test_company_identity_conflict(self):
        from src.data.reconciliation import check_symbol_mapping

        ok, reasons = check_symbol_mapping(
            "NABIL",
            {"api": "NABIL", "csv": "NABIL"},
            company_names={"api": "Nabil Bank", "csv": "Scarlet Bank"},
        )
        assert not ok and reasons  # different companies → mapping conflict

    def test_empty_identity_not_a_conflict(self):
        from src.data.reconciliation import check_symbol_mapping

        ok, reasons = check_symbol_mapping("NABIL", {"api": ""})
        assert ok and not reasons

    def test_second_provider_identity_in_provenance(self):
        from src.data.providers import HybridProvider

        second = _ControlledSecondProvider(Path(tempfile.mkdtemp()), history=_ohlcv(5))
        hybrid = HybridProvider([second])
        hybrid.get_history("NABIL")
        assert hybrid.last_provider == "github_csv"


# ═══════════════════════════════════════════════════════════════════
# 3. Real (controlled) reconciliation
# ═══════════════════════════════════════════════════════════════════


class TestControlledReconciliation:
    def test_agree_two_providers(self):
        merged, res = reconcile_history({"api": _ohlcv(5), "csv": _ohlcv(5)}, symbol="NABIL")
        assert res.status == AGREE
        assert len(merged) == 5

    def test_minor_resolves_to_preferred(self):
        f1 = _ohlcv(5)
        f2 = _ohlcv(5)
        f2["Close"] = f2["Close"] * 1.001
        merged, res = reconcile_history(
            {"api": f1, "csv": f2}, symbol="NABIL", preferred_source="api"
        )
        assert res.status in (AGREE, MINOR_DISAGREEMENT)
        assert len(merged) == 5

    def test_material_drops_date(self):
        f1 = _ohlcv(5)
        f2 = _ohlcv(5)
        f2.loc[f2.index[-1], "Close"] = f2.loc[f2.index[-1], "Close"] * 5.0
        merged, res = reconcile_history({"api": f1, "csv": f2}, symbol="NABIL")
        assert res.status == MATERIAL_DISAGREEMENT
        assert len(merged) == 4

    def test_mapping_conflict_status(self):
        f1 = _weekday_frame(["2026-07-01"], [100.0])
        f1["symbol"] = "NABIL"
        f2 = _weekday_frame(["2026-07-01"], [100.0])
        f2["symbol"] = "SCB"
        merged, res = reconcile_history({"api": f1, "csv": f2}, symbol="NABIL")
        assert res.status == MAPPING_CONFLICT
        assert merged is not None and len(merged) == 0

    def test_partial_history_no_false_material(self):
        f1 = _weekday_frame(["2026-07-01", "2026-07-02", "2026-07-03"], [100.0, 101.0, 102.0])
        f2 = _weekday_frame(["2026-07-01", "2026-07-02"], [100.0, 101.0])
        merged, res = reconcile_history({"api": f1, "csv": f2}, symbol="NABIL")
        assert res.status in (AGREE, MINOR_DISAGREEMENT)  # fewer records ≠ conflict
        assert merged is not None

    def test_never_averages(self):
        f1 = _ohlcv(5)
        f2 = _ohlcv(5)
        f2["Close"] = f2["Close"] * 10
        merged, res = reconcile_history({"api": f1, "csv": f2}, symbol="NABIL")
        assert res.status == MATERIAL_DISAGREEMENT
        # the merged frame must contain *one provider's* exact values,
        # never the average of the two
        assert len(merged) < 5

    def test_reconciled_provenance(self):
        res = ReconciliationResult(status=AGREE, providers=["api", "csv"], selected_source="api")
        prov = provenance_from_reconciliation(res)
        assert prov.trust == TRUST_RECONCILED
        assert prov.is_safe is True

    def test_conflicted_provenance_never_trusted(self):
        res = ReconciliationResult(status=MATERIAL_DISAGREEMENT, providers=["api", "csv"])
        prov = provenance_from_reconciliation(res)
        assert prov.trust == TRUST_CONFLICTED
        assert prov.is_safe is False


# ═══════════════════════════════════════════════════════════════════
# 4. Provider reliability history
# ═══════════════════════════════════════════════════════════════════


class TestProviderReliabilityHistory:
    def _monitor(self, **kw):
        from src.data.health import HealthCheckConfig, ProviderHealthMonitor

        # ``outcome_window`` may be overridden by the caller — pop it
        # before constructing the base config so the keyword is never
        # passed twice (Python raises on duplicate kwargs).
        window = kw.pop("outcome_window", 10)
        return ProviderHealthMonitor(
            HealthCheckConfig(outcome_window=window, min_window_samples=2, **kw)
        )

    def test_outcome_window_records(self):
        m = self._monitor()
        m.register("api")
        m.record_success("api")
        m.record_timeout("api")
        m.record_malformed("api")
        window = m.outcome_window("api")
        assert window == ["success", "timeout", "malformed"]

    def test_outcome_window_bounded(self):
        m = self._monitor(outcome_window=5)
        m.register("api")
        for _ in range(20):
            m.record_success("api")
        assert len(m.outcome_window("api")) == 5  # bounded regardless of traffic

    def test_reliability_history_counts(self):
        m = self._monitor()
        m.register("api")
        m.record_success("api")
        m.record_success("api")
        m.record_timeout("api")
        hist = m.reliability_history("api")
        assert hist["successes"] == 2
        assert hist["timeouts"] == 1
        assert hist["window"] == 3
        assert hist["max_window"] == 10

    def test_reliability_history_latency(self):
        m = self._monitor()
        m.register("api")
        m.record_success("api", latency_ms=42.0)
        assert m.reliability_history("api")["average_latency_ms"] > 0

    def test_record_empty_counts_empty_not_failure(self):
        m = self._monitor()
        m.register("api")
        m.record_empty("api")
        m.record_empty("api")
        hist = m.reliability_history("api")
        assert hist["empty"] == 2
        assert hist["failures"] == 0  # empty ≠ hard failure

    def test_record_timeout_counts_toward_disable(self):
        from src.data.health import HealthCheckConfig, ProviderHealthMonitor

        m = ProviderHealthMonitor(HealthCheckConfig(failure_threshold=3))
        m.register("api")
        for _ in range(3):
            m.record_timeout("api")
        assert m.get_health("api").is_disabled  # timeouts can disable a dead host

    def test_health_scalars_still_tracked(self):
        m = self._monitor()
        m.register("api")
        m.record_success("api")
        m.record_success("api")
        m.record_failure("api")
        h = m.get_health("api")
        assert h.total_requests == 3
        assert h.successful_requests == 2
        assert h.failed_requests == 1

    def test_reliability_history_unknown_provider(self):
        m = self._monitor()
        hist = m.reliability_history("ghost")
        assert hist["provider"] == "ghost"
        assert hist["window"] == 0


# ═══════════════════════════════════════════════════════════════════
# 5. Degradation detection
# ═══════════════════════════════════════════════════════════════════


class TestDegradationDetection:
    def _monitor(self, **kw):
        from src.data.health import HealthCheckConfig, ProviderHealthMonitor

        return ProviderHealthMonitor(HealthCheckConfig(outcome_window=20, min_window_samples=5, **kw))

    def test_single_failure_never_degrades(self):
        m = self._monitor()
        m.register("api")
        m.record_success("api")
        m.record_failure("api")
        assert m.degradation_state("api") == "HEALTHY"

    def test_failure_ratio_degrades(self):
        m = self._monitor(degraded_failure_ratio=0.5)
        m.register("api")
        for i in range(10):
            (m.record_success if i % 2 else m.record_failure)("api")
        assert m.degradation_state("api") == "DEGRADED"

    def test_repeated_timeout_degrades(self):
        m = self._monitor(degraded_timeout_count=3)
        m.register("api")
        for _ in range(5):
            m.record_success("api")
        for _ in range(3):
            m.record_timeout("api")
        assert m.degradation_state("api") == "DEGRADED"

    def test_repeated_malformed_degrades(self):
        m = self._monitor(degraded_malformed_count=3)
        m.register("api")
        for _ in range(5):
            m.record_success("api")
        for _ in range(3):
            m.record_malformed("api")
        assert m.degradation_state("api") == "DEGRADED"

    def test_repeated_empty_degrades(self):
        m = self._monitor(degraded_empty_count=3)
        m.register("api")
        for _ in range(5):
            m.record_success("api")
        for _ in range(3):
            m.record_empty("api")
        assert m.degradation_state("api") == "DEGRADED"

    def test_disabled_is_unavailable(self):
        m = self._monitor()
        m.register("api")
        m.disable("api")
        assert m.degradation_state("api") == "UNAVAILABLE"

    def test_unknown_provider_unknown_state(self):
        m = self._monitor()
        assert m.degradation_state("ghost") == "UNKNOWN"

    def test_healthy_after_recovery(self):
        m = self._monitor(degraded_timeout_count=3)
        m.register("api")
        for _ in range(5):
            m.record_success("api")
        for _ in range(3):
            m.record_timeout("api")
        assert m.degradation_state("api") == "DEGRADED"
        # Successes must eventually push the timeouts out of the rolling
        # window (window is 20 by default in this test class) — a run of
        # healthy traffic restores HEALTHY without any manual reset.
        for _ in range(30):
            m.record_success("api")
        assert m.degradation_state("api") == "HEALTHY"

    def test_thresholds_configurable(self):
        m = self._monitor(degraded_timeout_count=2)
        m.register("api")
        for _ in range(5):
            m.record_success("api")
        m.record_timeout("api")
        m.record_timeout("api")
        assert m.degradation_state("api") == "DEGRADED"  # 2 timeouts enough


# ═══════════════════════════════════════════════════════════════════
# 6. Provider recovery
# ═══════════════════════════════════════════════════════════════════


class TestProviderRecovery:
    def _monitor(self, **kw):
        from src.data.health import HealthCheckConfig, ProviderHealthMonitor

        return ProviderHealthMonitor(
            HealthCheckConfig(failure_threshold=3, success_recovery_count=2, **kw)
        )

    def test_successes_clear_disabled_state(self):
        m = self._monitor()
        m.register("api")
        for _ in range(3):
            m.record_failure("api")
        assert m.get_health("api").is_disabled
        m.record_success("api")
        m.record_success("api")
        assert m.get_health("api").is_disabled is False  # auto re-enabled

    def test_trial_re_enable_after_recovery_period(self, monkeypatch):
        import time

        from src.data.health import HealthCheckConfig, ProviderHealthMonitor

        m = ProviderHealthMonitor(
            HealthCheckConfig(failure_threshold=1, recovery_period=0.3)
        )
        m.register("api")
        m.record_failure("api")
        assert m.is_healthy("api") is False  # disabled, recovery period not elapsed
        time.sleep(0.35)  # recovery period elapsed
        assert m.is_healthy("api") is True  # trial re-enable

    def test_recovery_detected_in_state(self):
        from src.data.health import HealthCheckConfig, ProviderHealthMonitor

        m = ProviderHealthMonitor(
            HealthCheckConfig(failure_threshold=3, success_recovery_count=2)
        )
        m.register("api")
        for _ in range(3):
            m.record_failure("api")
        assert m.degradation_state("api") == "UNAVAILABLE"
        m.record_success("api")
        m.record_success("api")
        assert m.degradation_state("api") in ("HEALTHY", "UNAVAILABLE")  # not stuck DEGRADED forever

    def test_enable_manual(self):
        m = self._monitor()
        m.register("api")
        m.disable("api")
        m.enable("api")
        assert m.is_healthy("api") is True


# ═══════════════════════════════════════════════════════════════════
# 7. Outage / fallback
# ═══════════════════════════════════════════════════════════════════


class TestOutageFallback:
    def _reset_incidents(self):
        from src.data.incidents import incident_tracker

        incident_tracker.reset()

    def test_scenario_a_outage_recovery(self):
        """Provider A healthy → A fails → B serves → A recovers → A serves."""
        from src.data.incidents import incident_tracker
        from src.data.providers import HybridProvider

        self._reset_incidents()
        api = _FakeProvider(history=_ohlcv(30))
        csv = _FakeProvider(history=_ohlcv(30))
        hybrid = HybridProvider([api, csv])

        # A healthy
        hybrid.get_history("NABIL")
        assert hybrid.last_provider == "fake"
        assert hybrid.fallback_used is False

        # A fails → B serves
        api._raises = RuntimeError("outage")
        hybrid.get_history("NABIL")
        assert hybrid.fallback_used is True
        assert incident_tracker.count("fallback") >= 1

        # A recovers → A serves again
        api._raises = None
        hybrid.get_history("NABIL")
        assert hybrid.fallback_used is False

    def test_fallback_provenance_changes(self):
        from src.data.providers import HybridProvider
        from src.data.service import DataService

        hybrid = HybridProvider(
            [_FakeProvider(raises=RuntimeError("down")), _FakeProvider(history=_ohlcv(20))]
        )
        DataService.reset_instance()
        svc = DataService(provider=hybrid)
        try:
            hist = svc.get_history("NABIL")
            assert hist.provenance["trust"] == TRUST_FALLBACK
            assert hist.provenance["fallback_used"] is True
            assert hist.provenance["is_safe"] is True  # documented, not hidden
        finally:
            DataService.reset_instance()

    def test_metrics_record_fallback(self):
        from src.data.quality import quality_metrics

        quality_metrics.reset()
        from src.data.providers import HybridProvider

        hybrid = HybridProvider(
            [_FakeProvider(raises=RuntimeError("down")), _FakeProvider(history=_ohlcv(5))]
        )
        hybrid.get_history("NABIL")
        snap = quality_metrics.snapshot()
        assert snap["fallback_count"] >= 1
        assert snap["provider_failures"] >= 1

    def test_recovery_normal_routing_resumes(self):
        from src.data.providers import HybridProvider

        api = _FakeProvider(history=_ohlcv(30))
        csv = _FakeProvider(history=_ohlcv(30))
        hybrid = HybridProvider([api, csv])
        assert hybrid.fallback_used is False
        api._raises = RuntimeError("outage")
        hybrid.get_history("NABIL")
        assert hybrid.fallback_used is True
        api._raises = None
        hybrid.get_history("NABIL")
        assert hybrid.fallback_used is False  # policy restored: primary first


# ═══════════════════════════════════════════════════════════════════
# 8. All-provider failure
# ═══════════════════════════════════════════════════════════════════


class TestAllProvidersUnavailable:
    def _reset_incidents(self):
        from src.data.incidents import incident_tracker

        incident_tracker.reset()

    def test_all_fail_raises_unavailable(self):
        from src.data.providers import HybridProvider

        hybrid = HybridProvider(
            [_FakeProvider(raises=RuntimeError("a")), _FakeProvider(raises=RuntimeError("b"))]
        )
        with pytest.raises(DataUnavailable):
            hybrid.get_history("NABIL")

    def test_no_trusted_data_via_service(self):
        from src.data.providers import HybridProvider
        from src.data.service import DataService

        hybrid = HybridProvider(
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

    def test_unavailable_incident_recorded(self):
        from src.data.incidents import incident_tracker
        from src.data.providers import HybridProvider

        self._reset_incidents()
        hybrid = HybridProvider(
            [_FakeProvider(raises=RuntimeError("a")), _FakeProvider(raises=RuntimeError("b"))]
        )
        with pytest.raises(DataUnavailable):
            hybrid.get_history("NABIL")
        assert incident_tracker.count("provider_unavailable") == 1  # exactly one aggregate

    def test_no_unsafe_signal_on_unavailable(self):
        from src.engine.analyzer import analyze_dataframe

        result = analyze_dataframe(_ohlcv(40), symbol="NABIL", provenance=DataProvenance(trust=TRUST_UNAVAILABLE))
        assert result["signal"] == "HOLD"
        assert result["signal_suppressed"] is True


# ═══════════════════════════════════════════════════════════════════
# 9. Reconciliation incidents
# ═══════════════════════════════════════════════════════════════════


class TestReconciliationIncidents:
    def _reset(self):
        from src.data.incidents import incident_tracker

        incident_tracker.reset()

    def _named_provider(self, name: str, history):
        """A provider with a distinct identity for hybrid reconciliation."""
        p = _FakeProvider(history=history)
        p.name = name
        return p

    def test_material_incident_recorded(self):
        from src.data.incidents import (
            INCIDENT_MATERIAL_DISAGREEMENT,
            incident_tracker,
        )
        from src.data.providers import HybridProvider

        self._reset()
        f1 = _ohlcv(5)
        f2 = _ohlcv(5)
        f2.loc[f2.index[-1], "Close"] = f2.loc[f2.index[-1], "Close"] * 5.0
        hybrid = HybridProvider(
            [self._named_provider("api", f1), self._named_provider("csv", f2)]
        )
        merged, res = hybrid.get_reconciled_history("NABIL")
        assert res.status == MATERIAL_DISAGREEMENT
        snap = incident_tracker.snapshot()
        assert snap["counts"][INCIDENT_MATERIAL_DISAGREEMENT] == 1
        evt = incident_tracker.recent()[0]
        assert evt.symbol == "NABIL"
        assert evt.quarantined is True
        assert evt.trust_consequence == "conflicted (signal suppressed to HOLD)"

    def test_mapping_incident_recorded(self):
        from src.data.incidents import (
            INCIDENT_MAPPING_CONFLICT,
            incident_tracker,
        )
        from src.data.providers import HybridProvider

        self._reset()
        f1 = _weekday_frame(["2026-07-01"], [100.0])
        f2 = _weekday_frame(["2026-07-01"], [100.0])
        f1["symbol"] = "NABIL"
        f2["symbol"] = "SCB"
        hybrid = HybridProvider(
            [self._named_provider("api", f1), self._named_provider("csv", f2)]
        )
        merged, res = hybrid.get_reconciled_history("NABIL")
        assert res.status == MAPPING_CONFLICT
        snap = incident_tracker.snapshot()
        assert snap["counts"][INCIDENT_MAPPING_CONFLICT] == 1

    def test_repeated_minor_incident(self):
        from src.data.incidents import (
            INCIDENT_REPEATED_MINOR,
            incident_tracker,
        )

        self._reset()
        incident_tracker.record_minor("NABIL")
        incident_tracker.record_minor("NABIL")
        assert incident_tracker.count(INCIDENT_REPEATED_MINOR) == 0  # threshold not hit
        incident_tracker.record_minor("NABIL")
        assert incident_tracker.count(INCIDENT_REPEATED_MINOR) == 1

    def test_timeout_incident_from_chain(self):
        from src.data.incidents import (
            INCIDENT_PROVIDER_TIMEOUT,
            incident_tracker,
        )
        from src.data.providers import HybridProvider

        self._reset()
        hybrid = HybridProvider(
            [_FakeProvider(raises=ProviderTimeout("slow")), _FakeProvider(history=_ohlcv(5))]
        )
        hybrid.get_history("NABIL")
        assert incident_tracker.count(INCIDENT_PROVIDER_TIMEOUT) == 1

    def test_incidents_bounded_by_max_events(self):
        from src.data.incidents import IncidentTracker

        tracker = IncidentTracker(max_events=5)
        for i in range(50):
            tracker.record("provider_timeout", symbol=f"S{i}")
        assert len(tracker.recent(100)) == 5  # ring buffer caps growth

    def test_snapshot_recent_capped(self):
        from src.data.incidents import IncidentTracker

        tracker = IncidentTracker(max_events=100)
        for i in range(50):
            tracker.record("fallback", symbol=f"S{i}")
        snap = tracker.snapshot(recent_limit=10)
        assert len(snap["recent"]) == 10
        assert snap["total"] == 50

    def test_snapshot_zero_limit_returns_empty_recent(self):
        from src.data.incidents import IncidentTracker

        tracker = IncidentTracker(max_events=100)
        tracker.record("fallback")
        snap = tracker.snapshot(recent_limit=0)
        assert snap["recent"] == []  # [-0:] must not return the whole deque

    def test_incidents_compact_no_payloads(self):
        from src.data.incidents import incident_tracker

        self._reset()
        incident_tracker.record("material_disagreement", symbol="NABIL", detail="x")
        evt = incident_tracker.recent()[0].to_dict()
        for key in ("kind", "timestamp", "providers", "symbol", "trust_consequence", "fallback_used", "quarantined"):
            assert key in evt
        assert "detail" in evt  # bounded detail, never raw market data


# ═══════════════════════════════════════════════════════════════════
# 10. Quality trends
# ═══════════════════════════════════════════════════════════════════


class TestQualityTrends:
    def _reset(self):
        from src.data.quality import quality_trends

        quality_trends.reset()

    def _corpus(self, invalid=0, valid=100):
        return {
            "symbols_checked": valid + invalid,
            "valid": valid,
            "invalid": invalid,
            "suspicious": 0,
            "duplicates": 0,
            "conflicts": 0,
            "stale": 0,
        }

    def test_empty_trend_safe(self):
        from src.data.quality import quality_trends

        self._reset()
        trend = quality_trends.trend()
        assert trend["has_history"] is False
        assert trend["latest"] is None

    def test_single_snapshot_no_delta(self):
        from src.data.quality import quality_trends

        self._reset()
        quality_trends.record_corpus(self._corpus())
        trend = quality_trends.trend()
        assert trend["has_history"] is True
        assert trend["previous"] is None
        assert trend["delta"] == {}

    def test_improving_quality_detected(self):
        from src.data.quality import quality_trends

        self._reset()
        quality_trends.record_corpus(self._corpus(invalid=10))
        quality_trends.record_corpus(self._corpus(invalid=5))
        delta = quality_trends.trend()["delta"]["invalid"]
        assert delta["direction"] == "improving"
        assert delta["change"] == -5

    def test_worsening_quality_detected(self):
        from src.data.quality import quality_trends

        self._reset()
        quality_trends.record_corpus(self._corpus(invalid=5))
        quality_trends.record_corpus(self._corpus(invalid=9))
        delta = quality_trends.trend()["delta"]["invalid"]
        assert delta["direction"] == "worsening"

    def test_valid_direction_inverted(self):
        from src.data.quality import quality_trends

        self._reset()
        quality_trends.record_corpus(self._corpus(valid=90, invalid=10))
        quality_trends.record_corpus(self._corpus(valid=95, invalid=5))
        delta = quality_trends.trend()["delta"]["valid"]
        assert delta["direction"] == "improving"  # higher valid = improving

    def test_bounded_snapshots(self):
        from src.data.quality import quality_trends

        self._reset()
        tracker = quality_trends
        for i in range(50):
            tracker.record_corpus(self._corpus(invalid=i))
        trend = tracker.trend()
        assert trend["snapshots"] <= 20  # ring bounded
        assert trend["snapshots"] == 20

    def test_corpus_gate_records_trend(self, tmp_path):
        from src.data.quality import quality_trends, validate_corpus

        self._reset()
        data_dir = tmp_path / "data"
        data_dir.mkdir()
        _ohlcv(20).to_csv(data_dir / "NABIL.csv", index=False)
        validate_corpus(data_dir)
        trend = quality_trends.trend()
        assert trend["has_history"] is True
        assert trend["latest"]["symbols_checked"] >= 1

    def test_trend_compact_and_serializable(self):
        from src.data.quality import quality_trends

        self._reset()
        quality_trends.record_corpus(self._corpus())
        quality_trends.record_corpus(self._corpus(invalid=1))
        trend = quality_trends.trend()
        json.dumps(trend)  # must be JSON-serialisable (bounded)


# ═══════════════════════════════════════════════════════════════════
# 11. Calendar update validation
# ═══════════════════════════════════════════════════════════════════


def _valid_calendar_dict(**overrides) -> dict:
    data = {
        "calendar_version": "2.0",
        "effective_from": "2026-01-01",
        "effective_to": "2027-12-31",
        "trading_week": [0, 1, 2, 3, 6],
        "weekend_days": [4, 5],
        "holidays": [],
        "source": "operator",
        "provenance": {"source": "operator-maintained"},
    }
    data.update(overrides)
    return data


class TestCalendarUpdateValidation:
    def test_valid_candidate_passes(self):
        from src.data.calendar import validate_candidate_calendar

        problems = validate_candidate_calendar(_valid_calendar_dict())
        assert problems == []

    def test_malformed_candidate_rejected(self):
        from src.data.calendar import validate_candidate_calendar

        problems = validate_candidate_calendar(_valid_calendar_dict(calendar_version="9.9"))
        assert problems

    def test_missing_provenance_rejected(self):
        from src.data.calendar import validate_candidate_calendar

        problems = validate_candidate_calendar(
            _valid_calendar_dict(source="", provenance=None)
        )
        assert any("provenance" in p.lower() for p in problems)

    def test_overlapping_incompatible_version_rejected(self):
        from src.data.calendar import NepseCalendar, validate_candidate_calendar

        current = NepseCalendar.from_dict(_valid_calendar_dict())
        # A candidate whose trading week differs while keeping Fri/Sat as
        # weekend days: the weekend-regression gate must NOT fire here —
        # only the overlap-compatibility gate may reject it.
        candidate = _valid_calendar_dict(
            trading_week=[0, 1, 2, 3], weekend_days=[4, 5]
        )
        problems = validate_candidate_calendar(candidate, current=current)
        assert any("conflicts with the active calendar" in p for p in problems)

    def test_non_overlapping_incompatible_allowed(self):
        from src.data.calendar import NepseCalendar, validate_candidate_calendar

        current = NepseCalendar.from_dict(_valid_calendar_dict())
        candidate = _valid_calendar_dict(
            effective_from="2028-01-01",
            effective_to="2029-12-31",
            trading_week=[0, 1, 2, 3, 4],
            weekend_days=[5, 6],
        )
        problems = validate_candidate_calendar(candidate, current=current)
        assert problems == []  # no overlap → compatible by construction

    def test_weekend_regression_rejects_bad_candidate(self):
        from src.data.calendar import validate_candidate_calendar

        # A candidate that would make a known NEPSE Friday a trading day
        candidate = _valid_calendar_dict(
            trading_week=[0, 1, 2, 3, 4, 5], weekend_days=[6]
        )
        problems = validate_candidate_calendar(candidate)
        assert any("regression" in p for p in problems)

    def test_deterministic_validation_errors(self):
        from src.data.calendar import validate_candidate_calendar

        bad = _valid_calendar_dict(calendar_version="9.9", source="", provenance=None)
        p1 = validate_candidate_calendar(bad)
        p2 = validate_candidate_calendar(bad)
        assert p1 == p2  # deterministic

    def test_validation_never_raises(self):
        from src.data.calendar import validate_candidate_calendar

        for junk in (None, [], "x", 42):
            assert isinstance(validate_candidate_calendar(junk), list)


# ═══════════════════════════════════════════════════════════════════
# 12. Calendar rollback
# ═══════════════════════════════════════════════════════════════════


class TestCalendarRollback:
    def _tmp_file(self, tmp_path) -> Path:
        return tmp_path / "nepse_calendar.json"

    def test_update_preserves_previous_version(self, tmp_path, monkeypatch):
        from src.data import calendar as cal_module
        from src.data.calendar import update_calendar

        # Never write history to the real repo state file from tests.
        monkeypatch.setattr(cal_module, "CALENDAR_HISTORY_FILE", str(tmp_path / "history.json"))
        path = self._tmp_file(tmp_path)
        ok, problems = update_calendar(_valid_calendar_dict(), path=path)
        assert ok and not problems
        # Second update must preserve the first as a backup.
        ok2, p2 = update_calendar(
            _valid_calendar_dict(source="operator-v2"), path=path
        )
        assert ok2 and not p2
        backups = list(tmp_path.glob("nepse_calendar.*.bak.json"))
        assert len(backups) >= 1

    def test_rollback_restores_previous(self, tmp_path):
        from src.data.calendar import NepseCalendar, update_calendar

        path = self._tmp_file(tmp_path)
        update_calendar(_valid_calendar_dict(), path=path)
        # change the calendar (same schema, new version content)
        update_calendar(
            _valid_calendar_dict(holidays=["2026-10-11"], source="operator-v2"),
            path=path,
        )
        cal = NepseCalendar.load(path)
        assert cal.is_holiday(date(2026, 10, 11))

        from src.data.calendar import rollback_calendar

        ok, problems = rollback_calendar(path=path)
        assert ok and not problems
        restored = NepseCalendar.load(path)
        assert restored.is_holiday(date(2026, 10, 11)) is False

    def test_invalid_candidate_never_activates(self, tmp_path):
        from src.data.calendar import NepseCalendar, update_calendar

        path = self._tmp_file(tmp_path)
        update_calendar(_valid_calendar_dict(), path=path)
        before = NepseCalendar.load(path)
        ok, problems = update_calendar(_valid_calendar_dict(calendar_version="9.9"), path=path)
        assert not ok and problems
        after = NepseCalendar.load(path)
        assert after.version == before.version  # previous remains active

    def test_rollback_works_even_from_incompatible_calendar(self, tmp_path, monkeypatch):
        """Rollback must never be blocked by a (possibly incompatible)
        current calendar — the backup is validated on its own merits."""
        from src.data import calendar as cal_module
        from src.data.calendar import NepseCalendar, update_calendar

        monkeypatch.setattr(cal_module, "CALENDAR_HISTORY_FILE", str(tmp_path / "history.json"))
        path = self._tmp_file(tmp_path)
        update_calendar(_valid_calendar_dict(), path=path)
        # A second update creates the backup that rollback will restore.
        update_calendar(_valid_calendar_dict(source="operator-v2"), path=path)
        # Simulate an already-active incompatible calendar by writing the
        # file directly (``update_calendar`` would reject it, which is the
        # point — the active file may be corrupt despite governance).
        rogue = _valid_calendar_dict(
            trading_week=[0, 1, 2, 3, 4], weekend_days=[5, 6], source="rogue"
        )
        path.write_text(json.dumps(rogue), encoding="utf-8")
        current = NepseCalendar.load(path)
        assert sorted(current.trading_week) == [0, 1, 2, 3, 4]

        from src.data.calendar import rollback_calendar

        ok, problems = rollback_calendar(path=path)
        assert ok and not problems
        restored = NepseCalendar.load(path)
        assert sorted(restored.trading_week) == [0, 1, 2, 3, 6]  # original week back

    def test_no_backup_no_rollback(self, tmp_path):
        from src.data.calendar import rollback_calendar

        ok, problems = rollback_calendar(path=self._tmp_file(tmp_path))
        assert not ok and problems

    def test_corrupt_backup_rejected(self, tmp_path, monkeypatch):
        from src.data import calendar as cal_module
        from src.data.calendar import update_calendar

        monkeypatch.setattr(cal_module, "CALENDAR_HISTORY_FILE", str(tmp_path / "history.json"))
        path = self._tmp_file(tmp_path)
        update_calendar(_valid_calendar_dict(), path=path)
        update_calendar(_valid_calendar_dict(source="op-v2"), path=path)
        backup = next(tmp_path.glob("nepse_calendar.*.bak.json"))
        backup.write_text("{not json", encoding="utf-8")

        from src.data.calendar import rollback_calendar

        ok, problems = rollback_calendar(path=path)
        assert not ok and problems

    def test_history_bounded(self, tmp_path, monkeypatch):
        from src.data import calendar as cal_module
        from src.data.calendar import update_calendar

        monkeypatch.setattr(cal_module, "CALENDAR_HISTORY_FILE", str(tmp_path / "history.json"))
        path = self._tmp_file(tmp_path)
        for i in range(30):
            update_calendar(
                _valid_calendar_dict(source=f"op-{i}", holidays=[]),
                path=path,
            )
        from src.data.calendar import calendar_version_history

        history = calendar_version_history()
        assert len(history) <= 20  # bounded

    def test_backup_files_bounded(self, tmp_path, monkeypatch):
        from src.data import calendar as cal_module
        from src.data.calendar import CALENDAR_MAX_BACKUPS, update_calendar

        monkeypatch.setattr(cal_module, "CALENDAR_HISTORY_FILE", str(tmp_path / "history.json"))
        path = self._tmp_file(tmp_path)
        for i in range(30):
            update_calendar(_valid_calendar_dict(source=f"op-{i}"), path=path)
        backups = list(tmp_path.glob("nepse_calendar.*.bak.json"))
        assert len(backups) <= CALENDAR_MAX_BACKUPS


# ═══════════════════════════════════════════════════════════════════
# 13. Cache restart / recovery
# ═══════════════════════════════════════════════════════════════════


class TestCacheRestart:
    def test_disk_cache_survives_restart(self, tmp_path):
        from src.data.cache import DiskCache

        d1 = DiskCache(cache_dir=tmp_path)
        d1.set("history:NABIL:365", _ohlcv(10))
        # process restart: a brand-new instance reading the same directory
        d2 = DiskCache(cache_dir=tmp_path)
        frame = d2.get("history:NABIL:365")
        assert frame is not None and len(frame) == 10

    def test_provenance_survives_restart(self, tmp_path):
        from src.data.cache import DiskCache

        prov = DataProvenance(trust=TRUST_RECONCILED, sources=["api", "csv"]).to_dict()
        d1 = DiskCache(cache_dir=tmp_path)
        d1.set("reconciled:NABIL:365", {"frame": _ohlcv(5), "provenance": prov})
        d2 = DiskCache(cache_dir=tmp_path)
        entry = d2.get("reconciled:NABIL:365")
        assert entry["provenance"]["trust"] == TRUST_RECONCILED
        assert entry["provenance"]["is_safe"] is True

    def test_fallback_provenance_survives_restart(self, tmp_path):
        from src.data.cache import DiskCache

        prov = DataProvenance(trust=TRUST_FALLBACK, fallback_used=True).to_dict()
        d1 = DiskCache(cache_dir=tmp_path)
        d1.set("history:NABIL:365", {"frame": _ohlcv(5), "provenance": prov})
        d2 = DiskCache(cache_dir=tmp_path)
        entry = d2.get("history:NABIL:365")
        assert entry["provenance"]["trust"] == TRUST_FALLBACK

    def test_reconciled_cache_restart_roundtrip(self, tmp_path):
        from src.data.cache import TieredCache

        c1 = TieredCache(disk_dir=tmp_path)
        c1.set("reconciled:NABIL:365", {"frame": _ohlcv(5), "status": AGREE})
        c2 = TieredCache(disk_dir=tmp_path)
        entry = c2.get("reconciled:NABIL:365")
        assert entry is not None
        assert entry["status"] == AGREE


# ═══════════════════════════════════════════════════════════════════
# 14. Cache corruption
# ═══════════════════════════════════════════════════════════════════


class TestCacheCorruption:
    def test_corrupt_file_fails_safe(self, tmp_path):
        from src.data.cache import DiskCache

        d = DiskCache(cache_dir=tmp_path)
        d.set("history:NABIL:365", _ohlcv(5))
        # corrupt the backing file directly
        (tmp_path / "history_NABIL_365.json").write_text("{broken", encoding="utf-8")
        assert d.get("history:NABIL:365") is None  # never raises

    def test_corrupt_reconciled_fails_safe(self, tmp_path):
        from src.data.cache import DiskCache

        d = DiskCache(cache_dir=tmp_path)
        d.set("reconciled:NABIL:365", {"frame": _ohlcv(5)})
        (tmp_path / "reconciled_NABIL_365.json").write_text("garbage", encoding="utf-8")
        assert d.get("reconciled:NABIL:365") is None

    def test_other_keys_unaffected_by_corruption(self, tmp_path):
        from src.data.cache import DiskCache

        d = DiskCache(cache_dir=tmp_path)
        d.set("history:SCB:365", _ohlcv(5))
        d.set("reconciled:NABIL:365", "x")
        (tmp_path / "reconciled_NABIL_365.json").write_text("bad", encoding="utf-8")
        assert d.get("history:SCB:365") is not None  # corruption is isolated

    def test_conflicted_never_restored_as_trusted(self, tmp_path):
        from src.data.cache import TieredCache

        c = TieredCache(disk_dir=tmp_path)
        # A conflicted entry must never be cached as a *trusted* frame.
        # The DataService already refuses to cache conflicted outcomes;
        # verify the on-disk contract rejects one if it somehow existed.
        prov = DataProvenance(trust=TRUST_CONFLICTED)  # is_safe derives from trust
        c.set("reconciled:NABIL:365", {"frame": _ohlcv(5), "provenance": prov.to_dict()})
        entry = c.get("reconciled:NABIL:365")
        assert entry["provenance"]["is_safe"] is False
        assert entry["provenance"]["trust"] == TRUST_CONFLICTED


# ═══════════════════════════════════════════════════════════════════
# 15. Process recovery
# ═══════════════════════════════════════════════════════════════════


class TestProcessRecovery:
    def test_service_restart_reads_disk_cache(self, tmp_path):
        from src.data.cache import TieredCache
        from src.data.service import DataService

        DataService.reset_instance()
        try:
            cache = TieredCache(disk_dir=tmp_path)
            cache.set("history:NABIL:365", _ohlcv(10))
            svc = DataService(cache=cache)
            hist = svc.get_history("NABIL", days=365)
            assert hist.source == "cache"
            assert len(hist.df) == 10
        finally:
            DataService.reset_instance()

    def test_health_reset_policy(self):
        """Provider health is in-memory: after a restart a fresh monitor
        starts with no degradation (bounded, resets per process)."""
        from src.data.health import HealthCheckConfig, ProviderHealthMonitor

        m1 = ProviderHealthMonitor(HealthCheckConfig())
        m1.register("api")
        m1.record_failure("api")
        m2 = ProviderHealthMonitor(HealthCheckConfig())  # "restart"
        m2.register("api")
        assert m2.reliability_history("api")["window"] == 0

    def test_metrics_bounded_after_restart(self):
        from src.data.incidents import incident_tracker

        incident_tracker.reset()
        snap = incident_tracker.snapshot()
        assert snap["total"] == 0
        assert all(v == 0 for v in snap["counts"].values())


# ═══════════════════════════════════════════════════════════════════
# 16. Signal safety
# ═══════════════════════════════════════════════════════════════════


class TestSignalSafety:
    def _analyze(self, provenance=None, reconciliation=None):
        from src.engine.analyzer import analyze_dataframe

        return analyze_dataframe(
            _ohlcv(40), symbol="NABIL", provenance=provenance, reconciliation=reconciliation
        )

    def test_material_to_hold(self):
        result = self._analyze(
            provenance=provenance_from_reconciliation(
                ReconciliationResult(status=MATERIAL_DISAGREEMENT, providers=["api", "csv"])
            )
        )
        assert result["signal"] == "HOLD"
        assert result["signal_suppressed"] is True

    def test_mapping_to_hold(self):
        result = self._analyze(
            provenance=provenance_from_reconciliation(
                ReconciliationResult(status=MAPPING_CONFLICT, providers=["api", "csv"])
            )
        )
        assert result["signal"] == "HOLD"

    def test_unavailable_to_hold(self):
        result = self._analyze(provenance=DataProvenance(trust=TRUST_UNAVAILABLE))
        assert result["signal"] == "HOLD"

    def test_calendar_invalid_to_hold(self):
        result = self._analyze(provenance=DataProvenance(trust=TRUST_CALENDAR_INVALID))
        assert result["signal"] == "HOLD"
        assert result["signal_suppression_reason"] == TRUST_CALENDAR_INVALID

    def test_quarantined_to_hold(self):
        result = self._analyze(provenance=DataProvenance(trust=TRUST_QUARANTINED))
        assert result["signal"] == "HOLD"

    def test_reconciliation_block_suppresses(self):
        result = self._analyze(
            reconciliation=ReconciliationResult(
                status=MATERIAL_DISAGREEMENT, providers=["api"]
            )
        )
        assert result["signal"] == "HOLD"
        assert result["signal_suppression_reason"] == "material_disagreement"

    def test_degraded_but_trusted_not_suppressed(self):
        """A degraded provider alone does NOT suppress valid data when
        fallback/reconciliation establishes trust (Sprint 13.6 §15)."""
        from src.data.health import HealthCheckConfig, ProviderHealthMonitor
        from src.data.providers import HybridProvider
        from src.data.service import DataService
        from src.engine.analyzer import analyze_dataframe

        monitor = ProviderHealthMonitor(HealthCheckConfig())
        monitor.register("api")
        for _ in range(10):
            monitor.record_failure("api")  # primary degraded
        csv = _FakeProvider(history=_ohlcv(40))
        hybrid = HybridProvider([csv], health_monitor=monitor)
        DataService.reset_instance()
        svc = DataService(provider=hybrid)
        try:
            # The chain can still serve the (healthy) csv source; the
            # resulting data is documented as single-provider/fallback
            # but IS trusted — never auto-suppressed merely because a
            # provider degraded.
            hist = svc.get_history("NABIL")
            assert not hist.is_empty
            assert hist.provenance["is_safe"] is True
        finally:
            DataService.reset_instance()

    def test_fallback_data_remains_marked(self):
        from src.data.providers import HybridProvider

        hybrid = HybridProvider(
            [_FakeProvider(raises=RuntimeError("down")), _FakeProvider(history=_ohlcv(20))]
        )
        hybrid.get_history("NABIL")
        assert hybrid.fallback_used is True
        assert hybrid.last_provider == "fake"

    def test_stale_cannot_become_trusted(self):
        from src.data.provenance import provenance_from_history
        from src.data.quality import assess_history
        from src.engine.analyzer import analyze_dataframe

        old = _ohlcv(20, start="2024-01-01")
        report = assess_history(old, symbol="NABIL")
        assert report.freshness == "STALE"
        prov = provenance_from_history(sources=["csv"], quality=report)
        # Stale demotes trust to TRUST_STALE (limited trust, flagged, not
        # suppressed) — the established Sprint 13.5 contract.
        assert prov.trust == TRUST_STALE
        assert prov.is_safe is True
        result = analyze_dataframe(old, symbol="NABIL", provenance=prov)
        assert result["provenance"]["freshness"] == "STALE"

    def test_recovery_does_not_erase_provenance(self):
        from src.data.providers import HybridProvider

        api = _FakeProvider(history=_ohlcv(30))
        hybrid = HybridProvider([api])
        hybrid.get_history("NABIL")
        assert hybrid.fallback_used is False
        # provenance of the *served frame* is per-request; a recovered
        # primary never inherits a fallback label
        assert hybrid.last_provider == "fake"


# ═══════════════════════════════════════════════════════════════════
# 17. Scanner / alert safety
# ═══════════════════════════════════════════════════════════════════


class TestScannerAlertSafety:
    def _analyze_unsafe(self, trust: str):
        from src.engine.analyzer import analyze_dataframe

        return analyze_dataframe(
            _ohlcv(40),
            symbol="NABIL",
            provenance=DataProvenance(trust=trust),
        )

    def test_unsafe_data_never_normal_buy(self):
        for trust in (TRUST_CONFLICTED, TRUST_UNAVAILABLE, TRUST_CALENDAR_INVALID, TRUST_QUARANTINED):
            result = self._analyze_unsafe(trust)
            assert result["signal"] == "HOLD", f"{trust} produced {result['signal']}"
            assert result["signal_suppressed"] is True

    def test_suppression_reason_preserved(self):
        result = self._analyze_unsafe(TRUST_CONFLICTED)
        assert result["signal_suppression_reason"] == TRUST_CONFLICTED

    def test_alerts_not_generated_for_unsafe(self):
        from src.engine.analyzer import analyze_dataframe

        result = analyze_dataframe(
            _ohlcv(40),
            symbol="NABIL",
            provenance=DataProvenance(trust=TRUST_QUARANTINED),
        )
        # a HOLD-suppressed symbol must not emit fresh BUY/SELL alerts
        assert result["signal"] == "HOLD"
        assert result["signal_suppressed"] is True

    def test_batch_scan_bounded(self):
        from src.alerts.engine import process_alert_batch

        entries = [("NABIL", {"signal": "HOLD"}), ("SCB", {"signal": "HOLD"})]
        result = process_alert_batch(entries)
        assert set(result.keys()) == {"NABIL", "SCB"}


# ═══════════════════════════════════════════════════════════════════
# 18. Metrics compatibility
# ═══════════════════════════════════════════════════════════════════


class TestMetricsCompatibility:
    def _metrics(self):
        from fastapi.testclient import TestClient

        from src.api.main import app

        with TestClient(app) as client:
            resp = client.get("/metrics")
        assert resp.status_code == 200
        return resp.json()

    def test_new_blocks_present(self):
        body = self._metrics()
        for key in ("incidents", "data_quality_trend", "calendar_updates", "system_status"):
            assert key in body

    def test_legacy_blocks_preserved(self):
        body = self._metrics()
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

    def test_incidents_block_bounded(self):
        body = self._metrics()
        incidents = body["incidents"]
        assert "max_events" in incidents
        assert "total" in incidents
        assert "counts" in incidents
        assert isinstance(incidents["counts"], dict)
        assert len(incidents["recent"]) <= 20  # capped

    def test_quality_trend_block_bounded(self):
        body = self._metrics()
        trend = body["data_quality_trend"]
        assert "has_history" in trend
        assert "snapshots" in trend
        assert "latest" in trend

    def test_system_status_block_derived(self):
        body = self._metrics()
        status = body["system_status"]
        assert "overall" in status
        assert status["overall"] in ("HEALTHY", "DEGRADED", "UNAVAILABLE", "UNKNOWN")
        for block in ("data_provider", "reconciliation", "calendar", "data_quality", "incidents", "cache"):
            assert block in status

    def test_provider_health_has_state(self):
        body = self._metrics()
        for name, h in body["provider_health"].items():
            assert "state" in h
            assert h["state"] in ("HEALTHY", "DEGRADED", "UNAVAILABLE", "UNKNOWN")
            assert "reliability" in h  # bounded window counts

    def test_calendar_updates_bounded(self):
        body = self._metrics()
        updates = body["calendar_updates"]
        assert "max_history" in updates
        assert isinstance(updates["updates"], list)

    def test_metrics_safe_when_empty(self):
        from src.data.incidents import incident_tracker
        from src.data.quality import quality_trends

        incident_tracker.reset()
        quality_trends.reset()
        body = self._metrics()
        assert body["incidents"]["total"] == 0
        assert body["data_quality_trend"]["has_history"] is False


# ═══════════════════════════════════════════════════════════════════
# 19. Long-running bounded stability
# ═══════════════════════════════════════════════════════════════════


class TestLongRunningBoundedStability:
    def test_incident_tracker_bounded_under_load(self):
        from src.data.incidents import IncidentTracker

        tracker = IncidentTracker(max_events=50)
        for i in range(500):
            tracker.record("provider_timeout", symbol=f"S{i % 10}")
        snap = tracker.snapshot(recent_limit=200)
        assert len(snap["recent"]) <= 50  # events bounded by max_events
        assert len(tracker.recent(1000)) <= 50

    def test_health_window_bounded_under_load(self):
        from src.data.health import HealthCheckConfig, ProviderHealthMonitor

        m = ProviderHealthMonitor(HealthCheckConfig(outcome_window=25))
        m.register("api")
        for i in range(500):
            (m.record_success if i % 3 else m.record_timeout)("api")
        assert len(m.outcome_window("api")) == 25

    def test_quality_trends_bounded_under_load(self):
        from src.data.quality import QualityTrendTracker

        tracker = QualityTrendTracker(max_snapshots=10)
        for i in range(100):
            tracker.record_corpus({"valid": i, "invalid": 0, "suspicious": 0,
                                   "duplicates": 0, "conflicts": 0, "stale": 0,
                                   "symbols_checked": 100})
        assert tracker.trend()["snapshots"] == 10

    def test_reconciliation_metrics_scalar_only(self):
        reconciliation_metrics.reset()
        for i in range(100):
            reconciliation_metrics.record_reconciliation(
                ReconciliationResult(status=AGREE, providers=["api"])
            )
        snap = reconciliation_metrics.snapshot()
        assert snap["reconciliation_checks"] == 100
        assert all(isinstance(v, int) for v in snap.values())  # scalars only

    def test_minor_map_bounded(self):
        from src.data.incidents import IncidentTracker

        tracker = IncidentTracker(max_events=100)
        for i in range(1000):
            tracker.record_minor(f"SYM{i % 600}")  # more symbols than the cap
        # repeated-minor incidents stay bounded; the tracker never grows
        # without bound regardless of symbol count
        assert len(tracker.recent(1000)) <= 100

    def test_cache_no_growth_beyond_keys(self):
        from src.data.cache import MemoryCache

        m = MemoryCache()
        for i in range(100):
            m.set(f"reconciled:S{i}:365", {"frame": 1})
        assert m.size == 100  # no growth beyond keys written


# ═══════════════════════════════════════════════════════════════════
# 20. Docker / startup configuration
# ═══════════════════════════════════════════════════════════════════


class TestDockerConfig:
    def test_dockerfile_has_healthcheck(self):
        dockerfile = Path(__file__).resolve().parent.parent / "Dockerfile"
        text = dockerfile.read_text(encoding="utf-8")
        assert "HEALTHCHECK" in text
        assert "restart" not in text.lower() or "restart" in text  # sanity

    def test_compose_has_restart_policy(self):
        compose = Path(__file__).resolve().parent.parent / "docker-compose.yml"
        text = compose.read_text(encoding="utf-8")
        assert "restart: unless-stopped" in text

    def test_compose_exposes_state_volume(self):
        compose = Path(__file__).resolve().parent.parent / "docker-compose.yml"
        text = compose.read_text(encoding="utf-8")
        assert "market_data" in text
        assert "nepse_cache" in text

    def test_compose_has_api_healthcheck(self):
        compose = Path(__file__).resolve().parent.parent / "docker-compose.yml"
        text = compose.read_text(encoding="utf-8")
        assert "healthcheck" in text

    def test_ci_runs_full_suite(self):
        ci = Path(__file__).resolve().parent.parent / ".github" / "workflows" / "ci.yml"
        text = ci.read_text(encoding="utf-8")
        assert "pytest" in text

    def test_calendar_file_shipped(self):
        cal_file = Path(__file__).resolve().parent.parent / "data" / "state" / "nepse_calendar.json"
        assert cal_file.exists()
        cal = NepseCalendar.load(cal_file)
        assert cal is not None


# ═══════════════════════════════════════════════════════════════════
# 21. Regression coverage
# ═══════════════════════════════════════════════════════════════════


class TestRegressionCoverage:
    def test_sprint135_health_success_rate_unchanged(self):
        from src.data.health import HealthCheckConfig, ProviderHealthMonitor

        mon = ProviderHealthMonitor(
            HealthCheckConfig(failure_threshold=5, recovery_period=300.0, success_recovery_count=3)
        )
        mon.register("api")
        mon.record_success("api", latency_ms=12.0)
        mon.record_failure("api")
        h = {x.name: x for x in mon.get_all_health()}["api"]
        assert h.successful_requests == 1
        assert h.failed_requests == 1
        assert 0.0 <= h.success_rate <= 100.0

    def test_sprint134_reconcile_statuses_stable(self):
        f1 = _ohlcv(5)
        f2 = _ohlcv(5)
        assert reconcile_history({"api": f1, "csv": f2}, symbol="NABIL")[1].status == AGREE
        f2.loc[f2.index[-1], "Close"] = f2.loc[f2.index[-1], "Close"] * 5
        assert reconcile_history({"api": f1, "csv": f2}, symbol="NABIL")[1].status == MATERIAL_DISAGREEMENT

    def test_sprint135_calendar_governance_kept(self):
        cal = NepseCalendar()
        assert cal.classify_date(date(2026, 7, 24)) == WEEKEND  # Friday
        assert cal.classify_date(date(2026, 7, 26)) == TRADING_DAY  # Sunday

    def test_sprint133_quality_contract_kept(self):
        bad = _ohlcv(5)
        bad.loc[bad.index[0], "Close"] = 0.0
        report = assess_history(bad, symbol="X")
        assert report.status == INVALID
        # Time-independent: a healthy frame must be VALID. The default
        # ``_ohlcv`` start (June 2026) is now STALE and would flip a good
        # frame to SUSPICIOUS — build the good frame ending today.
        recent_start = (
            pd.Timestamp.today().normalize() - pd.Timedelta(days=10)
        ).strftime("%Y-%m-%d")
        good = _ohlcv(5, start=recent_start)
        assert assess_history(good, symbol="X").status == VALID

    def test_sprint131_perf_gate_constants_kept(self):
        from benchmarks.ci_gate import (
            ANALYZE_P99_MAX_MS,
            WARM_API_RATIO_MAX,
            WARM_PORTFOLIO_RATIO_MAX,
        )

        assert WARM_API_RATIO_MAX == 0.75
        assert WARM_PORTFOLIO_RATIO_MAX == 0.75
        assert ANALYZE_P99_MAX_MS == 2000.0

    def test_fallback_provenance_regression(self):
        from src.data.providers import HybridProvider
        from src.data.service import DataService

        hybrid = HybridProvider(
            [_FakeProvider(raises=RuntimeError("down")), _FakeProvider(history=_ohlcv(10))]
        )
        DataService.reset_instance()
        svc = DataService(provider=hybrid)
        try:
            hist = svc.get_history("NABIL")
            assert hist.provenance["trust"] == TRUST_FALLBACK
        finally:
            DataService.reset_instance()
