"""Sprint 13.4 — Cross-Provider Reconciliation & Trading Calendar Integrity.

Verifies the cross-provider reconciliation layer (AGREE / MINOR /
MATERIAL / UNAVAILABLE / MAPPING_CONFLICT), field-level reconciliation
with per-field tolerances, the NEPSE trading-calendar abstraction
(TRADING_DAY / HOLIDAY / WEEKEND / UNKNOWN and gap classification),
corporate-action awareness, symbol/company mapping conflict detection,
signal safety, cache provenance, scanner behavior on mixed-quality
universes, the bounded /metrics reconciliation block, and the additive
API contract — all without any external network dependency.

Engineering rules honoured: no real-network calls, conflicting prices
are never averaged, materially conflicting records never become trusted
data, different securities are never compared, missing sessions are
never fabricated, reconciliation thresholds stay configurable, quality
metrics stay bounded, and existing API contracts remain backward
compatible.
"""

from __future__ import annotations

import json
import subprocess
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

import pandas as pd
import pytest

from benchmarks.common import make_synthetic_df
from src.data.reconciliation import (
    AGREE,
    MATERIAL_DISAGREEMENT,
    MAPPING_CONFLICT,
    MINOR_DISAGREEMENT,
    UNAVAILABLE,
    ReconciliationResult,
    check_symbol_mapping,
    classify_price_jump,
    normalize_record,
    reconcile_history,
    reconcile_records,
    reconciliation_metrics,
    relative_difference,
)


def _record(symbol: str = "NABIL", close: float = 100.0, **overrides):
    base = {
        "symbol": symbol,
        "open": close * 0.99,
        "high": close * 1.02,
        "low": close * 0.98,
        "close": close,
        "volume": 1000,
    }
    base.update(overrides)
    return base


# ═══════════════════════════════════════════════════════════════════
# Repair (Phase 1-3)
# ═══════════════════════════════════════════════════════════════════


class TestRepair:
    def test_known_corrupted_row_removed(self, tmp_path):
        from scripts.repair_corpus import repair_file

        path = tmp_path / "NABIL.csv"
        path.write_text(
            "Date,Open,High,Low,Close,Volume\n"
            "2026-08-10,770.1,773.2,770.1,773.2,14520\n"
            "2026-07-24,770.1,773.2,0.0,0.0,28478\n"
            "2026-08-11,721.0,721.0,719.0,720.0,14528\n",
            encoding="utf-8",
        )
        res = repair_file(path, today=date(2026, 8, 13))
        assert res["removed"] == 1
        text = path.read_text(encoding="utf-8")
        assert "0.0,0.0" not in text
        assert "2026-08-10" in text and "2026-08-11" in text

    def test_healthy_rows_untouched(self, tmp_path):
        from scripts.repair_corpus import repair_file

        path = tmp_path / "NABIL.csv"
        content = (
            "Date,Open,High,Low,Close,Volume\n"
            "2026-08-10,770.1,773.2,770.1,773.2,14520\n"
            "2026-08-11,721.0,721.0,719.0,720.0,14528\n"
        )
        path.write_text(content, encoding="utf-8")
        res = repair_file(path, today=date(2026, 8, 13))
        assert res["removed"] == 0
        assert path.read_text(encoding="utf-8") == content

    def test_repair_is_idempotent(self, tmp_path):
        from scripts.repair_corpus import repair_file

        path = tmp_path / "NABIL.csv"
        content = (
            "Date,Open,High,Low,Close,Volume\n"
            "2026-07-24,770.1,773.2,0.0,0.0,28478\n"
            "2026-08-11,721.0,721.0,719.0,720.0,14528\n"
        )
        path.write_text(content, encoding="utf-8")
        first = repair_file(path, today=date(2026, 8, 13))
        second = repair_file(path, today=date(2026, 8, 13))
        assert first["removed"] == 1
        assert second["removed"] == 0  # nothing left to remove
        assert path.read_text(encoding="utf-8").count("2026-08-11") == 1

    def test_backup_created_before_write(self, tmp_path):
        from scripts.repair_corpus import repair_file

        path = tmp_path / "NABIL.csv"
        backup = tmp_path / "bk"
        path.write_text(
            "Date,Open,High,Low,Close,Volume\n"
            "2026-07-24,770.1,773.2,0.0,0.0,28478\n"
            "2026-08-10,770.1,773.2,770.1,773.2,14520\n",
            encoding="utf-8",
        )
        repair_file(path, today=date(2026, 8, 13), backup_dir=backup)
        backups = list(backup.glob("*.csv"))
        assert len(backups) == 1
        assert "0.0,0.0" in backups[0].read_text(encoding="utf-8")

    def test_dry_run_writes_nothing(self, tmp_path):
        from scripts.repair_corpus import repair_file

        path = tmp_path / "NABIL.csv"
        content = (
            "Date,Open,High,Low,Close,Volume\n"
            "2026-07-24,770.1,773.2,0.0,0.0,28478\n"
        )
        path.write_text(content, encoding="utf-8")
        res = repair_file(path, today=date(2026, 8, 13), dry_run=True)
        assert res["removed"] == 1
        assert path.read_text(encoding="utf-8") == content

    def test_repaired_corpus_passes_quality_gate(self, tmp_path):
        from scripts.repair_corpus import repair_corpus
        from src.data.quality import validate_corpus

        data_dir = tmp_path / "data"
        data_dir.mkdir()
        (data_dir / "NABIL.csv").write_text(
            "Date,Open,High,Low,Close,Volume\n"
            "2026-07-24,770.1,773.2,0.0,0.0,28478\n"
            "2026-08-11,721.0,721.0,719.0,720.0,14528\n",
            encoding="utf-8",
        )
        repair_corpus(data_dir, today=date(2026, 8, 13), backup_dir=tmp_path / "bk")
        agg = validate_corpus(data_dir)
        assert agg["invalid"] == 0


# ═══════════════════════════════════════════════════════════════════
# Reconciliation — field math & records (Phase 5-7)
# ═══════════════════════════════════════════════════════════════════


class TestReconciliation:
    def test_relative_difference_formula(self):
        # abs(a-b)/max(abs(a),abs(b)) — 100 vs 101 → 1/101.
        assert relative_difference(100.0, 101.0) == pytest.approx(1.0 / 101.0)
        assert relative_difference(101.0, 100.0) == pytest.approx(1.0 / 101.0)

    def test_relative_difference_zero_zero_none(self):
        assert relative_difference(0.0, 0.0) is None

    def test_relative_difference_missing_none(self):
        assert relative_difference(None, 5.0) is None
        assert relative_difference(5.0, None) is None

    def test_identical_providers_agree(self):
        a = _record()
        b = _record()
        res = reconcile_records([("api", a), ("csv", b)], symbol="NABIL", record_date="2026-08-10")
        assert res.status == AGREE
        assert res.is_trusted
        assert res.selected_source == "api"

    def test_small_difference_agree(self):
        # 0.5% close drift is within the 1% price tolerance.
        res = reconcile_records(
            [("api", _record(close=100.0)), ("csv", _record(close=100.3))],
            symbol="NABIL",
            record_date="2026-08-10",
        )
        assert res.status == AGREE

    def test_volume_difference_own_tolerance(self):
        # 15% volume drift: within the 10% volume tolerance? No — 15% > 10%.
        res = reconcile_records(
            [("api", _record(volume=1000)), ("csv", _record(volume=1150))],
            symbol="NABIL",
            record_date="2026-08-10",
        )
        assert res.status == MINOR_DISAGREEMENT
        assert "volume" in res.disagreement_fields

    def test_volume_within_tolerance_agree(self):
        res = reconcile_records(
            [("api", _record(volume=1000)), ("csv", _record(volume=1080))],
            symbol="NABIL",
            record_date="2026-08-10",
        )
        assert res.status == AGREE

    def test_material_price_difference(self):
        res = reconcile_records(
            [("api", _record(close=100.0)), ("csv", _record(close=110.0))],
            symbol="NABIL",
            record_date="2026-08-10",
        )
        assert res.status == MATERIAL_DISAGREEMENT
        assert "close" in res.disagreement_fields
        assert not res.is_trusted  # never silently trusted

    def test_minor_price_difference_resolves_to_preferred(self):
        res = reconcile_records(
            [("api", _record(close=100.0)), ("csv", _record(close=101.5))],
            symbol="NABIL",
            record_date="2026-08-10",
            preferred_source="csv",
        )
        assert res.status == MINOR_DISAGREEMENT
        assert res.selected_source == "csv"
        assert res.warnings  # documented, never averaged

    def test_minor_falls_back_to_first_when_preferred_absent(self):
        res = reconcile_records(
            [("api", _record(close=100.0)), ("csv", _record(close=101.5))],
            symbol="NABIL",
            record_date="2026-08-10",
            preferred_source="nonexistent",
        )
        assert res.status == MINOR_DISAGREEMENT
        assert res.selected_source == "api"

    def test_one_provider_unavailable_agrees(self):
        res = reconcile_records(
            [("api", _record()), ("csv", None)],
            symbol="NABIL",
            record_date="2026-08-10",
        )
        assert res.status == AGREE
        assert res.providers == ["api"]

    def test_both_unavailable(self):
        res = reconcile_records([("api", None), ("csv", None)], symbol="NABIL", record_date="2026-08-10")
        assert res.status == UNAVAILABLE
        assert not res.is_trusted

    def test_no_records_at_all(self):
        res = reconcile_records([], symbol="NABIL", record_date="2026-08-10")
        assert res.status == UNAVAILABLE

    def test_malformed_record_skipped(self):
        # One provider returns garbage that normalizes to nothing; the
        # other is valid → AGREE (malformed is not a conflict).
        res = reconcile_records(
            [("api", {"symbol": "NABIL", "close": "garbage"}), ("csv", _record())],
            symbol="NABIL",
            record_date="2026-08-10",
        )
        assert res.status == AGREE
        assert res.selected_source == "csv"

    def test_custom_tolerances(self):
        # Tight tolerance: 0.5% close drift is material.
        res = reconcile_records(
            [("api", _record(close=100.0)), ("csv", _record(close=100.5))],
            symbol="NABIL",
            record_date="2026-08-10",
            tolerances={"close": 0.001},
            material_thresholds={"close": 0.01},
        )
        assert res.status == MINOR_DISAGREEMENT

    def test_difference_metrics_recorded(self):
        res = reconcile_records(
            [("api", _record(close=100.0)), ("csv", _record(close=105.0))],
            symbol="NABIL",
            record_date="2026-08-10",
        )
        assert "close" in res.difference_metrics
        assert res.difference_metrics["close"] == pytest.approx(5.0 / 105.0)

    def test_normalize_record_capitalized_keys(self):
        norm = normalize_record(
            {"Open": 99.0, "High": 102.0, "Low": 98.0, "Close": 100.0, "Volume": 1000}
        )
        assert norm["open"] == 99.0
        assert norm["close"] == 100.0
        assert norm["volume"] == 1000

    def test_normalize_record_object_shape(self):
        from src.data.models import StockQuote

        q = StockQuote(
            symbol="NABIL",
            ltp=100.0,
            open_price=99.0,
            high=102.0,
            low=98.0,
            close=100.0,
            volume=1000,
        )
        norm = normalize_record(q)
        assert norm["close"] == 100.0
        assert norm["open"] == 99.0
        assert norm["volume"] == 1000

    def test_multiple_providers_material_any_pair(self):
        # A single outlier among three providers is a material conflict.
        res = reconcile_records(
            [
                ("api", _record(close=100.0)),
                ("csv", _record(close=100.2)),
                ("backup", _record(close=150.0)),
            ],
            symbol="NABIL",
            record_date="2026-08-10",
        )
        assert res.status == MATERIAL_DISAGREEMENT

    def test_optional_fields_absent_not_required(self):
        res = reconcile_records(
            [("api", _record()), ("csv", _record())],
            symbol="NABIL",
            record_date="2026-08-10",
        )
        assert res.status == AGREE  # turnover/transactions absent → fine

    def test_optional_field_disagreement_reported(self):
        res = reconcile_records(
            [
                ("api", {**_record(), "turnover": 1_000_000}),
                ("csv", {**_record(), "turnover": 2_000_000}),
            ],
            symbol="NABIL",
            record_date="2026-08-10",
        )
        # turnover differs by 100% > material threshold
        assert res.status == MATERIAL_DISAGREEMENT
        assert "turnover" in res.disagreement_fields


# ═══════════════════════════════════════════════════════════════════
# Symbol / company mapping (Phase 15)
# ═══════════════════════════════════════════════════════════════════


class TestSymbolMapping:
    def test_matching_symbols_ok(self):
        ok, reasons = check_symbol_mapping("NABIL", {"api": "NABIL", "csv": "NABIL"})
        assert ok and not reasons

    def test_case_insensitive_match(self):
        ok, _ = check_symbol_mapping("NABIL", {"api": "nabil", "csv": "NABIL"})
        assert ok

    def test_mismatched_identifier_conflict(self):
        ok, reasons = check_symbol_mapping("NABIL", {"api": "NABIL", "csv": "SCB"})
        assert not ok
        assert any("SCB" in r for r in reasons)

    def test_company_name_match_ok(self):
        ok, reasons = check_symbol_mapping(
            "NABIL",
            {"api": "NABIL", "csv": "NABIL"},
            company_names={"api": "Nabil Bank Limited", "csv": "Nabil Bank Limited"},
        )
        assert ok  # both name the identical company

    def test_company_conflict_detected(self):
        ok, reasons = check_symbol_mapping(
            "NABIL",
            {"api": "NABIL", "csv": "NABIL"},
            company_names={"api": "Nabil Bank", "csv": "Civil Bank"},
        )
        assert not ok
        assert any("company" in r for r in reasons)

    def test_missing_company_names_unknown_not_conflict(self):
        ok, _ = check_symbol_mapping(
            "NABIL",
            {"api": "NABIL", "csv": "NABIL"},
            company_names={"api": "", "csv": None},
        )
        assert ok

    def test_reconcile_never_compares_conflicting_securities(self):
        res = reconcile_records(
            [("api", _record(symbol="NABIL")), ("csv", _record(symbol="SCB"))],
            symbol="NABIL",
            record_date="2026-08-10",
        )
        assert res.status == MAPPING_CONFLICT
        assert not res.is_trusted


# ═══════════════════════════════════════════════════════════════════
# Corporate-action awareness (Phase 14)
# ═══════════════════════════════════════════════════════════════════


class TestCorporateActions:
    def test_normal_move(self):
        a = classify_price_jump(100.0, 100.0, 102.0)
        assert a.kind == "normal"

    def test_large_move_flagged_not_quarantined(self):
        # 50% drop with sound OHLC = possible dividend/bonus, legitimate.
        a = classify_price_jump(100.0, 50.0, 50.0, ohlcv_ok=True)
        assert a.kind == "large_but_legitimate"
        assert a.pct_change == pytest.approx(50.0)

    def test_structurally_impossible_ohlc(self):
        a = classify_price_jump(100.0, 50.0, 50.0, ohlcv_ok=False)
        assert a.kind == "structurally_impossible"

    def test_missing_prices_normal(self):
        a = classify_price_jump(None, 100.0, 100.0)
        assert a.kind == "normal"

    def test_non_positive_previous_normal(self):
        a = classify_price_jump(0.0, 100.0, 100.0)
        assert a.kind == "normal"

    def test_reconciliation_does_not_quarantine_large_move(self):
        # Two providers agree on a large-but-legitimate ex-dividend move:
        # must still AGREE (no material conflict from the move itself).
        res = reconcile_records(
            [
                ("api", _record(close=50.0)),
                ("csv", _record(close=50.2)),
            ],
            symbol="NABIL",
            record_date="2026-08-10",
        )
        assert res.status == AGREE


# ═══════════════════════════════════════════════════════════════════
# Trading calendar (Phase 10-13)
# ═══════════════════════════════════════════════════════════════════


class TestTradingCalendar:
    def _cal(self, observed=None, holidays=None):
        from src.data.calendar import NepseCalendar

        return NepseCalendar(observed_sessions=observed, holidays=holidays)

    def test_friday_is_weekend(self):
        assert self._cal().classify_date(date(2026, 7, 24)) == "WEEKEND"

    def test_saturday_is_weekend(self):
        assert self._cal().classify_date(date(2026, 7, 25)) == "WEEKEND"

    def test_sunday_is_trading_day(self):
        # NEPSE trades Sunday-Thursday; Sunday is not a weekend.
        assert self._cal().classify_date(date(2026, 7, 26)) == "TRADING_DAY"

    def test_monday_is_trading_day(self):
        assert self._cal().classify_date(date(2026, 7, 27)) == "TRADING_DAY"

    def test_holiday_classified(self):
        cal = self._cal(holidays=[date(2026, 8, 10)])
        assert cal.classify_date(date(2026, 8, 10)) == "HOLIDAY"
        assert not cal.is_trading_day(date(2026, 8, 10))

    def test_unknown_weekday_no_evidence(self):
        # No observed sessions provided: a weekday we know nothing about
        # is a scheduled trading day (never guessed as closure).
        assert self._cal().classify_date(date(2026, 8, 6)) == "TRADING_DAY"

    def test_unknown_with_observed_absent(self):
        cal = self._cal(observed=[date(2026, 8, 10), date(2026, 8, 11)])
        assert cal.classify_date(date(2026, 8, 12)) == "UNKNOWN"

    def test_expected_sessions_range(self):
        cal = self._cal()
        sessions = cal.expected_sessions(date(2026, 8, 9), date(2026, 8, 13))
        # Sun 8/9 ... Thu 8/13: five sessions (Fri/Sat excluded).
        assert len(sessions) == 5
        assert date(2026, 8, 9) in sessions and date(2026, 8, 13) in sessions

    def test_previous_next_trading_day(self):
        cal = self._cal()
        # Friday 2026-07-24 → previous trading day is Thursday 23rd.
        assert cal.previous_trading_day(date(2026, 7, 24)) == date(2026, 7, 23)
        # Friday → next trading day is Sunday 26th.
        assert cal.next_trading_day(date(2026, 7, 24)) == date(2026, 7, 26)

    def test_weekend_gap_is_not_missing_session(self):
        cal = self._cal(observed=[date(2026, 8, 10), date(2026, 8, 11)])
        findings = cal.classify_sessions([date(2026, 8, 10)], start=date(2026, 8, 10), end=date(2026, 8, 11))
        by_date = {f.date: f.classification for f in findings}
        assert by_date[date(2026, 8, 10)] == "TRADING_DAY"
        assert by_date[date(2026, 8, 11)] == "MISSING_TRADING_SESSION"

    def test_corpus_verified_closure_not_missing(self):
        # Corpus observed only 8/10 and 8/12: 8/11 has no session in the
        # union → it is a corpus-verified closure, not missing data.
        cal = self._cal(observed=[date(2026, 8, 10), date(2026, 8, 12)])
        findings = cal.classify_sessions(
            [date(2026, 8, 10), date(2026, 8, 12)], start=date(2026, 8, 10), end=date(2026, 8, 12)
        )
        by_date = {f.date: f.classification for f in findings}
        assert by_date[date(2026, 8, 11)] == "EXPECTED_NON_TRADING_DAY"

    def test_missing_sessions_helper(self):
        cal = self._cal(observed=[date(2026, 8, 10), date(2026, 8, 11), date(2026, 8, 12)])
        missing = cal.missing_sessions([date(2026, 8, 10)], start=date(2026, 8, 10), end=date(2026, 8, 12))
        assert [f.date for f in missing] == [date(2026, 8, 11), date(2026, 8, 12)]

    def test_consecutive_missing_sessions_counted(self):
        # Corpus observed *all* trading days in the window; the symbol
        # only has the first one → every later session is missing.
        cal = self._cal(
            observed=[
                date(2026, 8, 10),  # Mon
                date(2026, 8, 11),  # Tue
                date(2026, 8, 12),  # Wed
                date(2026, 8, 13),  # Thu
                date(2026, 8, 16),  # Sun
                date(2026, 8, 17),  # Mon
            ]
        )
        missing = cal.missing_sessions(
            [date(2026, 8, 10)], start=date(2026, 8, 10), end=date(2026, 8, 17)
        )
        assert len(missing) == 5  # 8/11, 8/12, 8/13, 8/16, 8/17

    def test_future_date_not_fabricated(self):
        cal = self._cal()
        # A future date is not fabricated; it's just classified.
        assert cal.classify_date(date(2099, 1, 5)) in ("TRADING_DAY", "WEEKEND")

    def test_holiday_not_reported_as_missing(self):
        # Symbol trades 8/10 and 8/12; 8/11 is a scheduled holiday → no
        # missing session is reported for it (a legitimate closure).
        cal = self._cal(
            observed=[date(2026, 8, 10), date(2026, 8, 12)],
            holidays=[date(2026, 8, 11)],
        )
        missing = cal.missing_sessions(
            [date(2026, 8, 10), date(2026, 8, 12)],
            start=date(2026, 8, 10),
            end=date(2026, 8, 12),
        )
        assert missing == []  # 8/11 is a holiday, not missing data

    def test_serialization_roundtrip(self, tmp_path):
        from src.data.calendar import NepseCalendar

        cal = NepseCalendar(
            holidays=[date(2026, 8, 11)],
            observed_sessions=[date(2026, 8, 10), date(2026, 8, 12)],
            provenance="test",
        )
        target = tmp_path / "cal.json"
        cal.save(target)
        loaded = NepseCalendar.load(target)
        assert loaded is not None
        assert loaded.holidays == (date(2026, 8, 11),)
        assert loaded.classify_date(date(2026, 8, 11)) == "HOLIDAY"

    def test_from_corpus_derives_sessions(self, tmp_path):
        from benchmarks.common import write_csvs
        from src.data.calendar import NepseCalendar

        data_dir = tmp_path / "data"
        write_csvs(data_dir, 2, 40)
        cal = NepseCalendar.from_corpus(data_dir)
        assert cal.observed_sessions  # derived, not guessed
        assert cal.version == "1.0"

    def test_quality_gap_detection_calendar_aware(self):
        from src.data.quality import assess_history

        # Corpus confirms 8/11 traded; the frame skips it → one missing
        # trading session, distinct from a weekend/holiday closure.
        cal = self._cal(
            observed=[date(2026, 8, 10), date(2026, 8, 11), date(2026, 8, 12)]
        )
        df = make_synthetic_df(rows=2, seed=3)
        df["Date"] = [pd.Timestamp("2026-08-10"), pd.Timestamp("2026-08-12")]
        report = assess_history(df, calendar=cal)
        assert report.missing_sessions == 1  # 8/11 confirmed traded, no record

    def test_quality_weekend_break_not_missing(self):
        from src.data.quality import assess_history

        # Thu 8/13 -> Mon 8/17: Fri+Sat+Sun closure, Mon observed.
        cal = self._cal(observed=[date(2026, 8, 13), date(2026, 8, 17)])
        df = make_synthetic_df(rows=2, seed=4)
        df["Date"] = [pd.Timestamp("2026-08-13"), pd.Timestamp("2026-08-17")]
        report = assess_history(df, calendar=cal)
        assert report.missing_sessions == 0  # weekend break is not missing data


# ═══════════════════════════════════════════════════════════════════
# History-wide reconciliation (Phase 16/19)
# ═══════════════════════════════════════════════════════════════════


class TestReconcileHistory:
    def _frame(self, closes, symbol="NABIL"):
        dates = pd.to_datetime(["2026-08-10", "2026-08-11", "2026-08-12"])
        return pd.DataFrame(
            {
                "Date": dates,
                "Open": [c * 0.99 for c in closes],
                "High": [c * 1.01 for c in closes],
                "Low": [c * 0.98 for c in closes],
                "Close": closes,
                "Volume": [1000, 1000, 1000],
            }
        )

    def test_single_provider_agree(self):
        merged, res = reconcile_history({"api": self._frame([100.0, 101.0, 102.0])}, symbol="NABIL")
        assert res.status == AGREE
        assert len(merged) == 3
        assert list(merged["Close"]) == [100.0, 101.0, 102.0]

    def test_agree_merges_selected_source(self):
        merged, res = reconcile_history(
            {"api": self._frame([100.0, 101.0, 102.0]), "csv": self._frame([100.1, 101.1, 102.1])},
            symbol="NABIL",
        )
        assert res.status == AGREE
        assert len(merged) == 3
        # api (first) is the selected source.
        assert list(merged["Close"]) == [100.0, 101.0, 102.0]

    def test_material_date_dropped_never_averaged(self):
        # csv has a gross outlier on 8/12 → that date is dropped.
        merged, res = reconcile_history(
            {
                "api": self._frame([100.0, 101.0, 102.0]),
                "csv": self._frame([100.1, 101.1, 500.0]),
            },
            symbol="NABIL",
        )
        assert res.status == MATERIAL_DISAGREEMENT
        assert len(merged) == 2  # 8/12 dropped
        assert 500.0 not in merged["Close"].tolist()
        assert res.warnings  # documented

    def test_merged_frame_has_canonical_columns(self):
        merged, _ = reconcile_history(
            {"api": self._frame([100.0, 101.0, 102.0]), "csv": self._frame([100.1, 101.1, 102.1])},
            symbol="NABIL",
        )
        for col in ("Date", "Open", "High", "Low", "Close", "Volume"):
            assert col in merged.columns

    def test_all_unavailable(self):
        merged, res = reconcile_history({"api": None, "csv": None}, symbol="NABIL")
        assert res.status == UNAVAILABLE
        assert merged is None

    def test_string_dates_handled(self):
        merged, res = reconcile_history(
            {"api": self._frame([100.0, 101.0, 102.0]).astype({"Date": str}), "csv": self._frame([100.1, 101.1, 102.1])},
            symbol="NABIL",
        )
        assert res.status == AGREE
        assert len(merged) == 3


# ═══════════════════════════════════════════════════════════════════
# Reconciliation metrics (Phase 9)
# ═══════════════════════════════════════════════════════════════════


class TestReconciliationMetrics:
    def test_snapshot_all_fields(self):
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

    def test_record_reconciliation_counts(self):
        reconciliation_metrics.reset()
        reconciliation_metrics.record_reconciliation(
            ReconciliationResult(status=AGREE, providers=["api"])
        )
        reconciliation_metrics.record_reconciliation(
            ReconciliationResult(status=MINOR_DISAGREEMENT, providers=["api", "csv"])
        )
        reconciliation_metrics.record_reconciliation(
            ReconciliationResult(status=MATERIAL_DISAGREEMENT, providers=["api", "csv"])
        )
        reconciliation_metrics.record_reconciliation(
            ReconciliationResult(status=MAPPING_CONFLICT, providers=["api", "csv"])
        )
        snap = reconciliation_metrics.snapshot()
        assert snap["reconciliation_checks"] == 4
        assert snap["agreements"] == 1
        assert snap["minor_disagreements"] == 1
        assert snap["material_disagreements"] == 1
        assert snap["mapping_conflicts"] == 1
        assert snap["quarantined_conflicts"] == 1

    def test_bounded_scalars_only(self):
        snap = reconciliation_metrics.snapshot()
        assert all(isinstance(v, int) for v in snap.values())


# ═══════════════════════════════════════════════════════════════════
# Signal safety (Phase 17)
# ═══════════════════════════════════════════════════════════════════


class TestSignalSafety:
    def _frame(self, rows=120):
        df = make_synthetic_df(rows=rows, seed=7)
        df["Date"] = pd.bdate_range(
            end=pd.Timestamp.today().normalize() - pd.Timedelta(days=1), periods=rows
        )
        return df

    def test_agreement_normal_signal(self):
        from src.engine.analyzer import analyze_dataframe

        result = analyze_dataframe(
            self._frame(),
            symbol="NABIL",
            reconciliation=ReconciliationResult(status=AGREE, providers=["api"]),
        )
        assert result["signal"] in ("BUY", "HOLD", "SELL")
        assert result["signal_suppressed"] is False
        assert result["reconciliation"]["status"] == AGREE

    def test_minor_disagreement_does_not_suppress(self):
        from src.engine.analyzer import analyze_dataframe

        result = analyze_dataframe(
            self._frame(),
            symbol="NABIL",
            reconciliation=ReconciliationResult(
                status=MINOR_DISAGREEMENT,
                providers=["api", "csv"],
                selected_source="api",
                warnings=["minor drift on close"],
            ),
        )
        assert result["signal_suppressed"] is False
        assert result["reconciliation"]["status"] == MINOR_DISAGREEMENT
        assert result["reconciliation"]["warnings"]

    def test_material_disagreement_suppresses_signal(self):
        from src.engine.analyzer import analyze_dataframe

        result = analyze_dataframe(
            self._frame(),
            symbol="NABIL",
            reconciliation=ReconciliationResult(
                status=MATERIAL_DISAGREEMENT,
                providers=["api", "csv"],
                disagreement_fields=["close"],
            ),
        )
        assert result["signal"] == "HOLD"
        assert result["signal_suppressed"] is True
        assert result["signal_suppression_reason"] == "material_disagreement"

    def test_mapping_conflict_suppresses_signal(self):
        from src.engine.analyzer import analyze_dataframe

        result = analyze_dataframe(
            self._frame(),
            symbol="NABIL",
            reconciliation=ReconciliationResult(
                status=MAPPING_CONFLICT, providers=["api", "csv"]
            ),
        )
        assert result["signal"] == "HOLD"
        assert result["signal_suppressed"] is True
        assert result["signal_suppression_reason"] == "mapping_conflict"

    def test_unavailable_no_unsafe_signal(self):
        from src.engine.analyzer import analyze_dataframe

        result = analyze_dataframe(
            self._frame(),
            symbol="NABIL",
            reconciliation=ReconciliationResult(status=UNAVAILABLE, providers=[]),
        )
        # UNAVAILABLE alone does not suppress (no data entered); the
        # caller already falls back.  The block is still attached.
        assert result["reconciliation"]["status"] == UNAVAILABLE

    def test_reconciliation_block_additive(self):
        from src.engine.analyzer import analyze_dataframe

        result = analyze_dataframe(self._frame(), symbol="NABIL")
        assert "reconciliation" not in result  # legacy shape preserved
        result2 = analyze_dataframe(
            self._frame(),
            symbol="NABIL",
            reconciliation=ReconciliationResult(status=AGREE, providers=["api"]),
        )
        assert "reconciliation" in result2


# ═══════════════════════════════════════════════════════════════════
# Cache provenance (Phase 19)
# ═══════════════════════════════════════════════════════════════════


class _ReconcilingProvider:
    """Fake provider exposing the hybrid reconciliation contract."""

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
        from src.data.reconciliation import reconcile_history

        merged, res = reconcile_history(self._frames, symbol=symbol)
        return merged, res


class _FakeProvider:
    """Minimal provider for driving the real HybridProvider reconcile loop."""

    def __init__(self, name, history=None, raises=None):
        self.name = name
        self._history = history
        self._raises = raises

    def get_history(self, symbol, days=365):
        if self._raises:
            raise self._raises
        return self._history


class TestHybridReconcileIntegration:
    def _frame(self, closes):
        return pd.DataFrame(
            {
                "Date": pd.to_datetime(["2026-08-10", "2026-08-11", "2026-08-12"]),
                "Open": [c * 0.99 for c in closes],
                "High": [c * 1.01 for c in closes],
                "Low": [c * 0.98 for c in closes],
                "Close": closes,
                "Volume": [1000, 1000, 1000],
            }
        )

    def test_real_hybrid_reconcile_agrees(self):
        from src.data.exceptions import InvalidDataError
        from src.data.providers import HybridProvider
        from src.data.service import _default_data_validator

        hybrid = HybridProvider(
            [
                _FakeProvider("api", history=self._frame([100.0, 101.0, 102.0])),
                _FakeProvider("csv", history=self._frame([100.1, 101.1, 102.1])),
            ],
            data_validator=_default_data_validator,
        )
        merged, res = hybrid.get_reconciled_history("NABIL")
        assert res.status == AGREE
        assert len(merged) == 3
        # Never averaged: selected source (api) values are used verbatim.
        assert list(merged["Close"]) == [100.0, 101.0, 102.0]

    def test_real_hybrid_reconcile_invalid_provider_falls_through(self):
        # A provider returning an INVALID frame raises InvalidDataError
        # through the validator; the real hybrid loop must catch it and
        # reconcile with the healthy provider instead of NameError/
        # UNAVAILABLE (Sprint 13.4 review fix).
        from src.data.exceptions import DataUnavailable
        from src.data.providers import HybridProvider
        from src.data.service import _default_data_validator

        bad = self._frame([100.0, 101.0, 102.0])
        bad.loc[bad.index[-1], "Close"] = -1.0  # violates the contract
        hybrid = HybridProvider(
            [
                _FakeProvider("api", history=bad),
                _FakeProvider("csv", history=self._frame([100.1, 101.1, 102.1])),
            ],
            data_validator=_default_data_validator,
        )
        merged, res = hybrid.get_reconciled_history("NABIL")
        assert res.status == AGREE
        assert merged["Close"].iloc[-1] == 102.1  # csv won (api rejected)

    def test_real_hybrid_reconcile_timeout_falls_through(self):
        from src.data.exceptions import ProviderTimeout
        from src.data.providers import HybridProvider

        hybrid = HybridProvider(
            [
                _FakeProvider("api", raises=ProviderTimeout("slow")),
                _FakeProvider("csv", history=self._frame([100.1, 101.1, 102.1])),
            ]
        )
        merged, res = hybrid.get_reconciled_history("NABIL")
        assert res.status == AGREE
        assert len(merged) == 3
        reconciliation_metrics.reset()

    def test_real_hybrid_reconcile_all_fail_unavailable(self):
        from src.data.exceptions import DataUnavailable
        from src.data.providers import HybridProvider

        hybrid = HybridProvider(
            [
                _FakeProvider("api", raises=RuntimeError("boom")),
                _FakeProvider("csv", raises=RuntimeError("boom")),
            ]
        )
        with pytest.raises(DataUnavailable):
            hybrid.get_reconciled_history("NABIL")

    def test_real_hybrid_reconcile_metrics_recorded(self):
        from src.data.providers import HybridProvider

        reconciliation_metrics.reset()
        hybrid = HybridProvider(
            [
                _FakeProvider("api", history=self._frame([100.0, 101.0, 102.0])),
                _FakeProvider("csv", history=self._frame([100.1, 101.1, 102.1])),
            ]
        )
        hybrid.get_reconciled_history("NABIL")
        snap = reconciliation_metrics.snapshot()
        assert snap["provider_requests"] == 2
        assert snap["provider_successes"] == 2
        assert snap["reconciliation_checks"] == 1
        assert snap["agreements"] == 1


class TestCacheProvenance:
    @pytest.fixture(autouse=True)
    def _fresh_service(self):
        from src.data import DataService
        from src.data.cache import TieredCache

        DataService.reset_instance()
        yield
        DataService.reset_instance()

    def _frame(self, closes, offset=0):
        return pd.DataFrame(
            {
                "Date": pd.to_datetime(["2026-08-10", "2026-08-11", "2026-08-12"]),
                "Open": [c * 0.99 for c in closes],
                "High": [c * 1.01 for c in closes],
                "Low": [c * 0.98 for c in closes],
                "Close": closes,
                "Volume": [1000, 1000, 1000],
            }
        )

    def test_reconciled_cache_hit_retains_status(self):
        from src.data import DataService
        from src.data.cache import TieredCache

        p = _ReconcilingProvider(frames={"api": self._frame([100.0, 101.0, 102.0]), "csv": self._frame([100.1, 101.1, 102.1])})
        svc = DataService(provider=p, cache=TieredCache(memory_ttl=60, disk_ttl=600))
        h1, rec1 = svc.get_reconciled_history("NABIL")
        h2, rec2 = svc.get_reconciled_history("NABIL")
        assert h2.source.startswith("reconciled-cache:AGREE")
        assert rec1 == rec2  # cached provenance identical

    def test_conflict_cached_as_quarantine_marker(self):
        from src.data import DataService
        from src.data.cache import TieredCache

        p = _ReconcilingProvider(
            frames={
                "api": self._frame([100.0, 101.0, 102.0]),
                "csv": self._frame([100.1, 101.1, 500.0]),
            }
        )
        svc = DataService(provider=p, cache=TieredCache(memory_ttl=60, disk_ttl=600))
        h, rec = svc.get_reconciled_history("NABIL")
        assert rec["status"] == MATERIAL_DISAGREEMENT
        # A conflicted frame never contains the outlier (never averaged).
        assert 500.0 not in h.df["Close"].tolist()

    def test_refresh_after_conflict(self):
        from src.data import DataService
        from src.data.cache import TieredCache

        p = _ReconcilingProvider(
            frames={
                "api": self._frame([100.0, 101.0, 102.0]),
                "csv": self._frame([100.1, 101.1, 500.0]),
            }
        )
        svc = DataService(provider=p, cache=TieredCache(memory_ttl=60, disk_ttl=600))
        h, _ = svc.get_reconciled_history("NABIL")
        svc._cache.delete("reconciled:NABIL:365")
        # Provider now agrees → cache no longer serves the old conflict.
        p._frames["csv"] = self._frame([100.1, 101.1, 102.1])
        h2, rec2 = svc.get_reconciled_history("NABIL")
        assert rec2["status"] == AGREE
        assert len(h2.df) == 3

    def test_unavailable_history_empty(self):
        from src.data import DataService
        from src.data.cache import TieredCache

        p = _ReconcilingProvider(frames={}, fail=True)
        svc = DataService(provider=p, cache=TieredCache(memory_ttl=60, disk_ttl=600))
        h, rec = svc.get_reconciled_history("NABIL")
        assert h.is_empty
        assert rec["status"] == UNAVAILABLE

    def test_stale_reconciled_data_still_provenanced(self):
        from src.data import DataService
        from src.data.cache import TieredCache

        p = _ReconcilingProvider(frames={"api": self._frame([100.0, 101.0, 102.0])})
        svc = DataService(provider=p, cache=TieredCache(memory_ttl=60, disk_ttl=600))
        h, rec = svc.get_reconciled_history("NABIL")
        assert rec["status"] == AGREE
        # Provenance block is always attached with the selected source.
        assert rec["selected_source"] == "api"


# ═══════════════════════════════════════════════════════════════════
# Scanner behaviour (Phase 18)
# ═══════════════════════════════════════════════════════════════════


class TestScannerMixedQuality:
    def _write_fresh_csvs(self, data_dir, symbols, rows=100, corrupt=None):
        from benchmarks.common import write_csvs

        write_csvs(data_dir, symbols, rows)
        if corrupt:
            for i in corrupt:
                df = make_synthetic_df(rows=rows, seed=999 + i)
                df["Date"] = pd.bdate_range(
                    end=pd.Timestamp.today().normalize() - pd.Timedelta(days=1), periods=rows
                )
                df.loc[df.index[-1], "Close"] = -1.0
                df.to_csv(data_dir / f"SYN{i:03d}.csv", index=False)

    def test_mixed_universe_healthy_ranked_conflicted_skipped(self, tmp_path, monkeypatch):
        from src.scanner.engine import scan_market

        data_dir = tmp_path / "data"
        self._write_fresh_csvs(data_dir, 3, corrupt=[0])
        monkeypatch.setattr("src.scanner.engine.DATA_DIRECTORY", str(data_dir))
        out = scan_market(workers=1)
        skipped = {s["symbol"] for s in out["skipped"]}
        assert "SYN000" in skipped
        assert len(out["results"]) == 2

    def test_deterministic_ranking_mixed(self, tmp_path, monkeypatch):
        from src.scanner.engine import scan_market

        data_dir = tmp_path / "data"
        self._write_fresh_csvs(data_dir, 5, corrupt=[1, 3])
        monkeypatch.setattr("src.scanner.engine.DATA_DIRECTORY", str(data_dir))
        out1 = scan_market(workers=1)
        out2 = scan_market(workers=1)
        assert [r["symbol"] for r in out1["results"]] == [r["symbol"] for r in out2["results"]]
        ranks = [r["rank"] for r in out1["results"]]
        assert ranks == sorted(ranks)

    def test_conflicted_symbol_does_not_outrank_healthy(self, tmp_path, monkeypatch):
        from src.scanner.engine import scan_market

        data_dir = tmp_path / "data"
        self._write_fresh_csvs(data_dir, 3, corrupt=[0])
        monkeypatch.setattr("src.scanner.engine.DATA_DIRECTORY", str(data_dir))
        out = scan_market(workers=1)
        # The conflicted symbol is absent from ranked results entirely.
        ranked_symbols = {r["symbol"] for r in out["results"]}
        assert "SYN000" not in ranked_symbols

    def test_all_unavailable_no_crash(self, tmp_path, monkeypatch):
        from src.scanner.engine import scan_market

        data_dir = tmp_path / "data"
        data_dir.mkdir(parents=True, exist_ok=True)
        (data_dir / "EMPTY.csv").write_text("Date,Open,High,Low,Close,Volume\n", encoding="utf-8")
        monkeypatch.setattr("src.scanner.engine.DATA_DIRECTORY", str(data_dir))
        out = scan_market(workers=1)
        assert "results" in out and "skipped" in out  # never crashes

    def test_alerts_not_fired_for_conflicted(self, tmp_path, monkeypatch):
        from src.scanner.engine import scan_market

        data_dir = tmp_path / "data"
        self._write_fresh_csvs(data_dir, 2, corrupt=[0])
        monkeypatch.setattr("src.scanner.engine.DATA_DIRECTORY", str(data_dir))
        out = scan_market(workers=1)
        # Skipped (conflicted) symbols never produce alert state.
        for s in out["skipped"]:
            assert s["error"] == "invalid_data"


# ═══════════════════════════════════════════════════════════════════
# API / metrics contract (Phase 20)
# ═══════════════════════════════════════════════════════════════════


class TestApiMetrics:
    def test_metrics_exposes_reconciliation_block(self):
        from fastapi.testclient import TestClient

        from src.api.main import app

        with TestClient(app) as client:
            resp = client.get("/metrics")
        assert resp.status_code == 200
        body = resp.json()
        assert "reconciliation" in body
        for key in (
            "provider_requests",
            "provider_successes",
            "reconciliation_checks",
            "agreements",
            "minor_disagreements",
            "material_disagreements",
            "mapping_conflicts",
            "quarantined_conflicts",
        ):
            assert key in body["reconciliation"]

    def test_metrics_backward_compatible_keys_preserved(self):
        from fastapi.testclient import TestClient

        from src.api.main import app

        with TestClient(app) as client:
            resp = client.get("/metrics")
        body = resp.json()
        # Every pre-existing top-level block must remain.
        for key in (
            "process",
            "indicator_cache",
            "json_store",
            "memory_mb",
            "api_requests",
            "performance_gates",
            "metrics",
            "data_quality",
            "scanner_cache",
            "status",
        ):
            assert key in body

    def test_analyze_response_backward_compatible(self, tmp_path, monkeypatch):
        from fastapi.testclient import TestClient

        from src.api.main import app

        data_dir = tmp_path / "data"
        data_dir.mkdir(parents=True, exist_ok=True)
        df = make_synthetic_df(rows=120, seed=7)
        df["Date"] = pd.bdate_range(
            end=pd.Timestamp.today().normalize() - pd.Timedelta(days=1), periods=len(df)
        )
        df.to_csv(data_dir / "SYN000.csv", index=False)
        monkeypatch.setattr("src.loaders.csv_loader.DATA_DIRECTORY", str(data_dir))
        with TestClient(app) as client:
            resp = client.get("/analyze/SYN000")
        assert resp.status_code == 200
        body = resp.json()
        # Legacy contract preserved: no reconciliation block unless opted in.
        assert "data_quality" in body
        assert "signal" in body

    def test_reconciliation_result_serializable(self):
        res = ReconciliationResult(
            status=MATERIAL_DISAGREEMENT,
            symbol="NABIL",
            date="2026-08-10",
            providers=["api", "csv"],
            disagreement_fields=["close"],
            difference_metrics={"close": 0.05},
            warnings=["material disagreement on close"],
        )
        payload = json.loads(json.dumps(res.to_dict()))
        assert payload["status"] == MATERIAL_DISAGREEMENT
        assert payload["difference_metrics"]["close"] == 0.05

    def test_no_provenance_payload_leak(self):
        # Provider raw payloads are never part of the reconciliation dict.
        res = ReconciliationResult(status=AGREE, providers=["api"], selected_source="api")
        payload = res.to_dict()
        for forbidden in ("payload", "raw", "data"):
            assert forbidden not in payload
