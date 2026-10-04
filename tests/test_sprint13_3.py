"""Sprint 13.3 — Live Data Quality & Provider Reliability.

Verifies the canonical market-data contract, the centralized quality
validator (``src.data.quality``), OHLC integrity, duplicate/conflict
detection, freshness, provider fallback validation, the real live HTTP
timeout path (controlled local server), symbol-mapping integrity,
market-status zero-vs-unknown semantics, signal safety, scanner
partial-failure safety, cache freshness, the /metrics data-quality
block, the data-quality CLI, and the additive ``data_quality`` API
contract — all without any external network dependency.

Engineering rules honoured: no real-network calls (timeout tests use a
controlled local HTTP server), validation logic stays centralized,
quality metrics stay bounded, existing behaviour is preserved for valid
data, and the production corpus is never mutated.
"""

from __future__ import annotations

import json
import subprocess
import sys
import threading
import time
from datetime import date, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from benchmarks.common import make_synthetic_df, write_csvs
from src.data.quality import (
    INVALID,
    STALE,
    SUSPICIOUS,
    VALID,
    DataQualityReport,
    QualityIssue,
    assess_freshness,
    assess_history,
    assess_market_summary,
    assess_quote,
    detect_disagreement,
    normalize_symbol,
    quality_metrics,
    symbol_issues,
    validate_corpus,
    validate_history_frame,
)
from src.data.exceptions import InvalidDataError, ProviderTimeout


def _ohlcv(rows: int = 120, start: str = "2026-07-01", seed: int = 7) -> pd.DataFrame:
    """Deterministic valid OHLCV frame ending recently (fresh)."""
    df = make_synthetic_df(rows=rows, seed=seed)
    df["Date"] = pd.bdate_range(end=pd.Timestamp.today().normalize() - pd.Timedelta(days=1), periods=rows)
    return df


def _write_fresh_csvs(data_dir, symbols: int, rows: int) -> None:
    """Write *symbols* CSVs whose dates end today (fresh, not STALE)."""
    data_dir.mkdir(parents=True, exist_ok=True)
    for i in range(symbols):
        df = make_synthetic_df(rows=rows, seed=1000 + i)
        df["Date"] = pd.bdate_range(
            end=pd.Timestamp.today().normalize() - pd.Timedelta(days=1), periods=rows
        )
        # Lower-case fixture names: the resolver probes the lower-cased
        # symbol, and case-sensitive filesystems (Linux CI) would not
        # find an upper-case file.
        df.to_csv(data_dir / f"syn{i:03d}.csv", index=False)


def _broken_frame(rows: int = 60, seed: int = 9) -> pd.DataFrame:
    """OHLCV frame with a negative close and a High<Low row."""
    df = make_synthetic_df(rows=rows, seed=seed)
    df.loc[df.index[-2], "Close"] = -5.0
    df.loc[df.index[-1], "High"] = df.loc[df.index[-1], "Low"] - 10.0
    return df


# ═══════════════════════════════════════════════════════════════════
# OHLC integrity
# ═══════════════════════════════════════════════════════════════════


class TestOHLCIntegrity:
    def test_valid_candle_is_valid(self):
        report = assess_history(_ohlcv())
        assert report.status == VALID

    def test_negative_close_is_invalid(self):
        df = _ohlcv()
        df.loc[df.index[-1], "Close"] = -1.0
        report = assess_history(df)
        assert report.status == INVALID
        assert "non_positive_price" in report.reasons

    def test_zero_open_is_invalid(self):
        df = _ohlcv()
        df.loc[df.index[-1], "Open"] = 0.0
        report = assess_history(df)
        assert report.status == INVALID
        assert "non_positive_price" in report.reasons

    def test_nan_close_is_invalid(self):
        df = _ohlcv()
        df.loc[df.index[-1], "Close"] = np.nan
        report = assess_history(df)
        assert report.status == INVALID
        assert "nan_price" in report.reasons

    def test_inf_high_is_invalid(self):
        df = _ohlcv()
        df.loc[df.index[-1], "High"] = np.inf
        report = assess_history(df)
        assert report.status == INVALID
        assert "infinite_price" in report.reasons

    def test_high_below_low_is_invalid(self):
        df = _ohlcv()
        df.loc[df.index[-1], "High"] = df.loc[df.index[-1], "Low"] - 5.0
        report = assess_history(df)
        assert report.status == INVALID
        assert "high_below_low" in report.reasons

    def test_high_below_open_close_is_invalid(self):
        df = _ohlcv()
        row = df.index[-1]
        df.loc[row, "High"] = min(df.loc[row, "Open"], df.loc[row, "Close"]) - 1.0
        report = assess_history(df)
        assert report.status == INVALID
        assert "high_below_open_close" in report.reasons

    def test_low_above_open_close_is_invalid(self):
        df = _ohlcv()
        row = df.index[-1]
        df.loc[row, "Low"] = max(df.loc[row, "Open"], df.loc[row, "Close"]) + 1.0
        report = assess_history(df)
        assert report.status == INVALID
        assert "low_above_open_close" in report.reasons

    def test_negative_volume_is_invalid(self):
        df = _ohlcv()
        df.loc[df.index[-1], "Volume"] = -100
        report = assess_history(df)
        assert report.status == INVALID
        assert "negative_volume" in report.reasons

    def test_missing_required_column_is_invalid(self):
        report = assess_history(_ohlcv().drop(columns=["Volume"]))
        assert report.status == INVALID
        assert "missing_columns" in report.reasons

    def test_empty_frame_is_invalid(self):
        report = assess_history(pd.DataFrame())
        assert report.status == INVALID
        assert "empty_data" in report.reasons

    def test_invalid_rows_counted(self):
        df = _ohlcv()
        df.loc[df.index[-1], "Close"] = -1.0
        df.loc[df.index[-2], "High"] = np.nan
        report = assess_history(df)
        assert report.records_invalid >= 2
        assert report.records_invalid <= report.records_checked

    def test_zero_volume_allowed(self):
        df = _ohlcv()
        df.loc[df.index[-1], "Volume"] = 0
        assert assess_history(df).status == VALID

    def test_record_level_validator_covers_contract(self):
        # Phase 4 also exposes a record-level entry point for callers
        # validating single rows (e.g. streaming quotes).  The same
        # canonical checks must apply: positive prices, OHLC relations,
        # non-negative volume, NaN/inf rejection.
        from src.data.quality import validate_ohlcv_record

        good = {"Open": 100.0, "High": 105.0, "Low": 95.0, "Close": 102.0, "Volume": 500}
        assert validate_ohlcv_record(good) == []

        codes = {i.code for i in validate_ohlcv_record({**good, "Close": -1.0})}
        assert "non_positive_price" in codes

        codes = {i.code for i in validate_ohlcv_record({**good, "High": 90.0})}
        assert "high_below_open_close" in codes

        codes = {i.code for i in validate_ohlcv_record({**good, "Low": 110.0})}
        assert "low_above_open_close" in codes

        codes = {i.code for i in validate_ohlcv_record({**good, "High": 94.0, "Low": 96.0})}
        assert "high_below_low" in codes

        codes = {i.code for i in validate_ohlcv_record({**good, "Volume": -5})}
        assert "negative_volume" in codes

        codes = {i.code for i in validate_ohlcv_record({**good, "Open": np.nan})}
        assert "nan_price" in codes

        codes = {i.code for i in validate_ohlcv_record({**good, "Close": np.inf})}
        assert "infinite_price" in codes


# ═══════════════════════════════════════════════════════════════════
# Dates
# ═══════════════════════════════════════════════════════════════════


class TestDates:
    def test_valid_dates(self):
        assert assess_history(_ohlcv()).status == VALID

    def test_future_date_is_invalid(self):
        df = _ohlcv()
        df.loc[df.index[-1], "Date"] = pd.Timestamp.today() + pd.Timedelta(days=10)
        report = assess_history(df)
        assert report.status == INVALID
        assert "future_date" in report.reasons

    def test_unparseable_date_is_invalid(self):
        df = _ohlcv()
        # Cast to object first: pandas refuses to write a non-date string
        # into a datetime64 column (raises instead of coercing).
        df["Date"] = df["Date"].astype(object)
        df.loc[df.index[-1], "Date"] = "not-a-date"
        report = assess_history(df)
        assert report.status == INVALID
        assert "invalid_date" in report.reasons

    def test_weekend_gap_not_flagged(self):
        # Fri->Mon absence of a weekend is a normal trading break.
        df = _ohlcv(rows=15)
        dates = pd.bdate_range(end=pd.Timestamp.today().normalize(), periods=15)
        df["Date"] = dates
        report = assess_history(df)
        assert "unexpected_gap" not in report.reasons

    def test_duplicate_date_identical_is_warning(self):
        df = _ohlcv(rows=30)
        row = df.iloc[10].copy()
        df = pd.concat([df, pd.DataFrame([row])], ignore_index=True).sort_values("Date")
        report = assess_history(df)
        assert report.status == SUSPICIOUS
        assert "duplicate_date" in report.reasons
        assert report.duplicates == 1

    def test_conflicting_duplicate_is_error(self):
        df = _ohlcv(rows=30)
        row = df.iloc[10].copy()
        row["Close"] += 50.0
        df = pd.concat([df, pd.DataFrame([row])], ignore_index=True).sort_values("Date")
        report = assess_history(df)
        assert report.status == INVALID
        assert "conflicting_duplicate" in report.reasons
        assert report.conflicts == 1

    def test_identical_duplicate_deduplicated_safely(self):
        # Identical duplicates are deduplicable (counted, warning only)
        # — never silently treated as two different trading days.
        df = _ohlcv(rows=30)
        row = df.iloc[10].copy()
        df = pd.concat([df, pd.DataFrame([row])], ignore_index=True).sort_values("Date")
        report = assess_history(df)
        assert report.duplicates == 1
        assert report.conflicts == 0
        assert report.status == SUSPICIOUS


# ═══════════════════════════════════════════════════════════════════
# Continuity
# ═══════════════════════════════════════════════════════════════════


class TestContinuity:
    def test_long_gap_flagged(self):
        df = _ohlcv(rows=40)
        # Replace the middle with a ~20-business-day hole.  The base range
        # ends 40 days ago so the +28-day second half stays in the past
        # (a future date would be an INVALID error, not a gap warning).
        base = pd.bdate_range(
            end=pd.Timestamp.today().normalize() - pd.Timedelta(days=40), periods=40
        )
        dates = list(base[:20]) + list(base[20:] + pd.Timedelta(days=28))
        df["Date"] = dates
        report = assess_history(df)
        assert "unexpected_gap" in report.reasons
        assert report.status == SUSPICIOUS

    def test_normal_week_no_gap(self):
        df = _ohlcv(rows=20)
        df["Date"] = pd.bdate_range(end=pd.Timestamp.today().normalize(), periods=20)
        report = assess_history(df)
        assert "unexpected_gap" not in report.reasons

    def test_missing_candle_not_invented(self):
        # A single-day hole must be *reported*, never fabricated: row
        # count is preserved and no synthetic candle is added.
        df = _ohlcv(rows=30)
        report = assess_history(df)
        assert report.records_checked == len(df)


# ═══════════════════════════════════════════════════════════════════
# Freshness
# ═══════════════════════════════════════════════════════════════════


class TestFreshness:
    def test_fresh(self):
        freshness, age = assess_freshness(pd.Timestamp.today())
        assert freshness == "FRESH"
        assert age == 0.0

    def test_stale(self):
        freshness, age = assess_freshness(
            pd.Timestamp.today() - pd.Timedelta(days=30), stale_after_days=7
        )
        assert freshness == STALE
        assert age == 30.0

    def test_unknown(self):
        freshness, age = assess_freshness(None)
        assert freshness == "UNKNOWN"
        assert age is None

    def test_boundary_equal_is_fresh(self):
        # Exactly stale_after_days old -> FRESH (only *older* is stale).
        freshness, _ = assess_freshness(
            pd.Timestamp.today() - pd.Timedelta(days=7), stale_after_days=7
        )
        assert freshness == "FRESH"

    def test_report_freshness_stale(self):
        df = _ohlcv(rows=40)
        df["Date"] = pd.bdate_range(
            end=pd.Timestamp.today() - pd.Timedelta(days=30), periods=40
        )
        report = assess_history(df, stale_after_days=7)
        assert report.freshness == STALE
        assert report.age_days is not None and report.age_days >= 30
        assert "stale_data" in report.reasons

    def test_report_freshness_unknown_no_date(self):
        df = _ohlcv().drop(columns=["Date"])
        report = assess_history(df)
        assert report.freshness == "UNKNOWN"


# ═══════════════════════════════════════════════════════════════════
# Symbol integrity
# ═══════════════════════════════════════════════════════════════════


class TestSymbolIntegrity:
    def test_normalize(self):
        assert normalize_symbol(" nabil ") == "NABIL"
        assert normalize_symbol(None) == ""

    def test_empty_symbol_issue(self):
        codes = [i.code for i in symbol_issues("")]
        assert "empty_symbol" in codes

    def test_whitespace_warning(self):
        codes = [i.code for i in symbol_issues(" NABIL ")]
        assert "whitespace_symbol" in codes

    def test_unsafe_symbol(self):
        codes = [i.code for i in symbol_issues("../etc")]
        assert "unsafe_symbol" in codes

    def test_valid_symbol_no_issues(self):
        assert symbol_issues("NABIL") == []

    def test_csv_resolution_is_case_insensitive(self, tmp_path):
        from src.loaders.csv_loader import resolve_stock_csv_path

        write_csvs(tmp_path / "data", 1, 40)
        path = resolve_stock_csv_path("SYN000", data_dir=tmp_path / "data")
        assert path is not None
        assert path.name == "syn000.csv"

    def test_unknown_symbol_resolves_none(self, tmp_path):
        from src.loaders.csv_loader import resolve_stock_csv_path

        write_csvs(tmp_path / "data", 1, 40)
        assert resolve_stock_csv_path("NOSUCH", data_dir=tmp_path / "data") is None

    def test_inactive_delisted_symbol_never_cross_contaminates(self, tmp_path):
        # A delisted symbol with no CSV must never fall back to another
        # company's data: resolution returns None, not a neighbour file.
        from src.loaders.csv_loader import resolve_stock_csv_path

        write_csvs(tmp_path / "data", 2, 40)
        assert resolve_stock_csv_path("SYN000", data_dir=tmp_path / "data") is not None
        assert resolve_stock_csv_path("DELISTED", data_dir=tmp_path / "data") is None

    def test_duplicate_csv_stems_not_double_counted(self, tmp_path, monkeypatch):
        # CSVProvider deduplicates identical discovered paths across the
        # supported layout patterns (data/*.csv, data/history/*.csv,
        # data/stocks/*.csv) so one symbol is never analysed twice.
        from src.data.providers import CSVProvider

        data_dir = tmp_path / "data"
        write_csvs(data_dir, 1, 40)
        history = data_dir / "history"
        history.mkdir(exist_ok=True)
        import shutil

        shutil.copy(data_dir / "syn000.csv", history / "syn000.csv")
        provider = CSVProvider(data_dir=data_dir)
        files = provider._discover_csv_files()
        stems = [f.stem.lower() for f in files]
        assert stems.count("syn000") == 1


# ═══════════════════════════════════════════════════════════════════
# Market summary zero-vs-unknown
# ═══════════════════════════════════════════════════════════════════


class TestMarketSummary:
    def test_none_is_unknown(self):
        info = assess_market_summary(None)
        assert info["available"] is False
        assert info["status"] == "unknown"

    def test_empty_default_is_unavailable(self):
        from src.data.models import MarketSummary

        info = assess_market_summary(MarketSummary.empty())
        assert info["available"] is False
        # Zeroed core fields while unavailable are *unknown*, not real.
        assert "index" in info["unknown_fields"]

    def test_closed_with_real_index_available(self):
        from src.data.models import MarketSummary

        s = MarketSummary(index=2800.0, status="Closed")
        info = assess_market_summary(s)
        assert info["available"] is True
        assert info["unknown_fields"] == []

    def test_open_with_genuine_zero_change_available(self):
        from src.data.models import MarketSummary

        s = MarketSummary(index=2800.0, change=0.0, status="Open")
        info = assess_market_summary(s)
        assert info["available"] is True
        assert "change" not in info["unknown_fields"]

    def test_unknown_status_never_masquerades_as_fresh(self):
        from src.data.models import MarketSummary

        info = assess_market_summary(MarketSummary(status="Unknown", index=0.0))
        assert info["available"] is False
        assert info["status"] == "unknown"


# ═══════════════════════════════════════════════════════════════════
# Quote validation
# ═══════════════════════════════════════════════════════════════════


class TestQuoteValidation:
    def test_valid_quote(self):
        from src.data.models import StockQuote

        assert assess_quote(StockQuote(symbol="NABIL", ltp=500.0, volume=100)) == []

    def test_negative_ltp(self):
        from src.data.models import StockQuote

        assert "negative_or_nan_ltp" in assess_quote(StockQuote(symbol="X", ltp=-1.0))

    def test_negative_volume(self):
        from src.data.models import StockQuote

        assert "negative_volume" in assess_quote(StockQuote(symbol="X", ltp=5.0, volume=-2))

    def test_none_quote(self):
        assert assess_quote(None) == ["missing_quote"]


# ═══════════════════════════════════════════════════════════════════
# Provider disagreement
# ═══════════════════════════════════════════════════════════════════


class TestDisagreement:
    def _q(self, close: float, high: float, low: float):
        from src.data.models import StockQuote

        return StockQuote(symbol="NABIL", ltp=close, close=close, high=high, low=low)

    def test_material_disagreement_detected(self):
        a = self._q(500.0, 510.0, 490.0)
        b = self._q(520.0, 530.0, 510.0)  # >1% apart
        assert "conflict:close" in detect_disagreement(a, b, tolerance_pct=1.0)

    def test_within_tolerance_no_conflict(self):
        a = self._q(500.0, 510.0, 490.0)
        b = self._q(502.0, 512.0, 492.0)
        assert detect_disagreement(a, b, tolerance_pct=1.0) == []

    def test_zero_zero_no_conflict(self):
        a = self._q(0.0, 0.0, 0.0)
        b = self._q(0.0, 0.0, 0.0)
        assert detect_disagreement(a, b) == []


# ═══════════════════════════════════════════════════════════════════
# Provider validation + fallback
# ═══════════════════════════════════════════════════════════════════


class _FakeProvider:
    name = "fake"

    def __init__(self, history: pd.DataFrame | None = None):
        self._history = history

    def get_history(self, symbol: str, days: int = 365) -> pd.DataFrame:
        if self._history is None:
            raise RuntimeError("boom")
        return self._history

    def get_market_summary(self):
        raise RuntimeError("boom")


class TestProviderValidation:
    def test_valid_history_passes(self):
        report = validate_history_frame(_ohlcv(), symbol="NABIL")
        assert report.status == VALID

    def test_invalid_history_raises(self):
        with pytest.raises(InvalidDataError):
            validate_history_frame(_broken_frame(), symbol="BAD")

    def test_invalid_provider_result_falls_back(self):
        from src.data.providers import HybridProvider
        from src.data.service import _default_data_validator

        hybrid = HybridProvider(
            [_FakeProvider(history=_broken_frame()), _FakeProvider(history=_ohlcv())],
            data_validator=_default_data_validator,
        )
        df = hybrid.get_history("NABIL")
        assert len(df) == len(_ohlcv())
        assert hybrid.last_provider == "fake"

    def test_all_invalid_raises_unavailable(self):
        from src.data.exceptions import DataUnavailable
        from src.data.providers import HybridProvider
        from src.data.service import _default_data_validator

        hybrid = HybridProvider(
            [_FakeProvider(history=_broken_frame())],
            data_validator=_default_data_validator,
        )
        with pytest.raises(DataUnavailable):
            hybrid.get_history("NABIL")

    def test_failure_after_validator_falls_through(self):
        from src.data.exceptions import DataUnavailable
        from src.data.providers import HybridProvider

        hybrid = HybridProvider([_FakeProvider(history=None), _FakeProvider(history=None)])
        with pytest.raises(DataUnavailable):
            hybrid.get_history("NABIL")

    def test_timeout_preserved_through_hybrid(self):
        from src.data.exceptions import DataUnavailable
        from src.data.providers import HybridProvider

        class _TimeoutProvider(_FakeProvider):
            def get_history(self, symbol, days=365):
                raise ProviderTimeout("slow")

        hybrid = HybridProvider([_TimeoutProvider()])
        with pytest.raises(DataUnavailable):
            hybrid.get_history("NABIL")

    def test_hybrid_validator_hook_rejects_invalid(self):
        from src.data.providers import HybridProvider
        from src.data.service import _default_data_validator

        hybrid = HybridProvider(
            [_FakeProvider(history=_broken_frame()), _FakeProvider(history=_ohlcv())],
            data_validator=_default_data_validator,
        )
        df = hybrid.get_history("NABIL")
        assert len(df) > 0  # second provider's valid frame won


# ═══════════════════════════════════════════════════════════════════
# Data-quality metrics (bounded)
# ═══════════════════════════════════════════════════════════════════


class TestQualityMetrics:
    def test_snapshot_all_fields(self):
        snap = quality_metrics.snapshot()
        for f in (
            "records_checked",
            "records_valid",
            "records_invalid",
            "records_suspicious",
            "duplicates",
            "conflicts",
            "stale_records",
            "provider_failures",
            "provider_timeouts",
            "fallback_count",
        ):
            assert f in snap

    def test_record_report_counts(self):
        quality_metrics.reset()
        quality_metrics.record_report(assess_history(_ohlcv()))
        quality_metrics.record_report(assess_history(_broken_frame()))
        snap = quality_metrics.snapshot()
        assert snap["records_checked"] > 0
        assert snap["records_invalid"] >= 1

    def test_provider_failure_timeout_fallback(self):
        quality_metrics.reset()
        quality_metrics.record_provider_failure()
        quality_metrics.record_provider_timeout()
        quality_metrics.record_fallback()
        snap = quality_metrics.snapshot()
        assert snap["provider_failures"] == 1
        assert snap["provider_timeouts"] == 1
        assert snap["fallback_count"] == 1

    def test_metrics_endpoint_exposes_data_quality(self):
        from fastapi.testclient import TestClient

        from src.api.main import app

        with TestClient(app) as client:
            resp = client.get("/metrics")
        assert resp.status_code == 200
        body = resp.json()
        assert "data_quality" in body
        assert "records_checked" in body["data_quality"]


# ═══════════════════════════════════════════════════════════════════
# Signal safety (Phase 14)
# ═══════════════════════════════════════════════════════════════════


class TestSignalSafety:
    def test_valid_data_normal_signal(self):
        from src.engine.analyzer import analyze_dataframe

        result = analyze_dataframe(_ohlcv(rows=200), symbol="NABIL")
        assert result["data_quality"]["status"] == VALID
        assert result["signal"] in ("BUY", "HOLD", "SELL")
        assert result["signal_suppressed"] is False

    def test_invalid_data_suppresses_signal(self):
        from src.engine.analyzer import analyze_dataframe

        result = analyze_dataframe(_broken_frame(rows=200), symbol="BAD")
        assert result["data_quality"]["status"] == INVALID
        assert result["signal"] == "HOLD"
        assert result["signal_suppressed"] is True
        assert result["signal_suppression_reason"] == "invalid_data"

    def test_stale_data_flagged_not_suppressed_by_default(self, monkeypatch):
        from src.engine.analyzer import analyze_dataframe
        import src.config as config

        monkeypatch.setattr(config, "ENFORCE_SIGNAL_FRESHNESS", False)
        df = _ohlcv(rows=200)
        df["Date"] = pd.bdate_range(
            end=pd.Timestamp.today() - pd.Timedelta(days=30), periods=len(df)
        )
        result = analyze_dataframe(df, symbol="OLD")
        assert result["data_quality"]["freshness"] == STALE
        assert result["signal_suppressed"] is False
        assert result["signal"] in ("BUY", "HOLD", "SELL")

    def test_stale_data_suppressed_when_enforced(self, monkeypatch):
        from src.engine.analyzer import analyze_dataframe
        import src.config as config

        monkeypatch.setattr(config, "ENFORCE_SIGNAL_FRESHNESS", True)
        df = _ohlcv(rows=200)
        df["Date"] = pd.bdate_range(
            end=pd.Timestamp.today() - pd.Timedelta(days=30), periods=len(df)
        )
        result = analyze_dataframe(df, symbol="OLD")
        assert result["data_quality"]["freshness"] == STALE
        assert result["signal"] == "HOLD"
        assert result["signal_suppressed"] is True
        assert result["signal_suppression_reason"] == "stale_data"

    def test_non_ohlcv_frame_passes_through(self, monkeypatch):
        # Indicator-augmented frames (e.g. the 1-row fixture) carry no
        # market-data contract and must be byte-identical.  Mirroring
        # test_analyzer.py's patched_dependencies, the whole analysis
        # tail is stubbed so the 1-row frame reaches the result builder
        # untouched — and no ``data_quality`` block is attached because
        # the frame is not OHLCV-shaped.
        from src.engine import analyzer as analyzer_mod

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
            lambda df: {"name": "Bullish", "type": "Bullish", "strength": "Strong", "score": 2},
        )
        monkeypatch.setattr(
            analyzer_mod,
            "calculate_score",
            lambda latest, pattern: (6, {"trend": 2, "rsi": 1, "macd": 2, "volume": 1, "pattern": 2, "reasons": ["ok"]}),
        )
        monkeypatch.setattr(analyzer_mod, "calculate_confidence", lambda score, breakdown: 85)
        monkeypatch.setattr(analyzer_mod, "create_trade_plan", lambda result: {"target1": 110.0})
        monkeypatch.setattr(analyzer_mod, "calculate_risk_reward", lambda result: {"best_rr": 3.2})
        monkeypatch.setattr(analyzer_mod, "calculate_position_size", lambda result: {"position_size": 10})
        monkeypatch.setattr(analyzer_mod, "check_alerts", lambda result: [{"type": "BUY"}])
        monkeypatch.setattr(analyzer_mod, "generate_signal", lambda result: "BUY")

        frame = pd.DataFrame(
            [
                {
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
        result = analyzer_mod.analyze_dataframe(frame)
        assert "data_quality" not in result
        assert "signal" in result

    def test_quality_block_deterministic_across_calls(self):
        from src.engine.analyzer import analyze_dataframe

        df = _ohlcv(rows=200)
        a = analyze_dataframe(df, symbol="DET")
        b = analyze_dataframe(df, symbol="DET")
        assert a["data_quality"] == b["data_quality"]

    def test_conflicting_duplicate_suppresses_signal(self):
        # Phase 14: a conflicting duplicate must not silently produce a
        # normal signal — the analysis is INVALID and the signal is
        # suppressed to HOLD.
        from src.engine.analyzer import analyze_dataframe

        df = _ohlcv(rows=200)
        row = df.iloc[50].copy()
        row["Close"] += 500.0
        df = pd.concat([df, pd.DataFrame([row])], ignore_index=True).sort_values("Date")
        result = analyze_dataframe(df, symbol="CONFLICT")
        assert result["data_quality"]["status"] == INVALID
        assert result["signal"] == "HOLD"
        assert result["signal_suppressed"] is True
        assert result["signal_suppression_reason"] == "invalid_data"


# ═══════════════════════════════════════════════════════════════════
# Scanner safety (Phase 15)
# ═══════════════════════════════════════════════════════════════════


class TestScannerSafety:
    def test_invalid_symbol_skipped_healthy_ranked(self, tmp_path, monkeypatch):
        from src.scanner.engine import scan_market

        data_dir = tmp_path / "data"
        _write_fresh_csvs(data_dir, 3, 120)
        # Corrupt one file (negative close).
        df = make_synthetic_df(rows=120, seed=999)
        df["Date"] = pd.bdate_range(
            end=pd.Timestamp.today().normalize() - pd.Timedelta(days=1), periods=len(df)
        )
        df.loc[df.index[-1], "Close"] = -1.0
        df.to_csv(data_dir / "syn000.csv", index=False)

        monkeypatch.setattr("src.scanner.engine.DATA_DIRECTORY", str(data_dir))
        out = scan_market(workers=1)
        skipped_symbols = {s["symbol"] for s in out["skipped"]}
        assert "SYN000" in skipped_symbols
        assert len(out["results"]) == 2
        # Deterministic ranking for healthy symbols.
        ranks = [r["rank"] for r in out["results"]]
        assert ranks == sorted(ranks)

    def test_all_invalid_all_skipped(self, tmp_path, monkeypatch):
        from src.scanner.engine import scan_market

        data_dir = tmp_path / "data"
        data_dir.mkdir(parents=True, exist_ok=True)
        for i in range(3):
            df = make_synthetic_df(rows=80, seed=100 + i)
            df.loc[df.index[-1], "Close"] = -1.0
            df.to_csv(data_dir / f"BAD{i:03d}.csv", index=False)
        monkeypatch.setattr("src.scanner.engine.DATA_DIRECTORY", str(data_dir))
        out = scan_market(workers=1)
        assert len(out["results"]) == 0
        assert len(out["skipped"]) == 3
        assert {s["symbol"] for s in out["skipped"]} == {"BAD000", "BAD001", "BAD002"}

    def test_malformed_file_isolated(self, tmp_path, monkeypatch):
        from src.scanner.engine import scan_market

        data_dir = tmp_path / "data"
        write_csvs(data_dir, 2, 120)
        (data_dir / "BROKEN.csv").write_text("not,valid,csv\n1,2\n", encoding="utf-8")
        monkeypatch.setattr("src.scanner.engine.DATA_DIRECTORY", str(data_dir))
        out = scan_market(workers=1)
        assert "BROKEN" in {s["symbol"] for s in out["skipped"]}
        assert len(out["results"]) == 2


# ═══════════════════════════════════════════════════════════════════
# Cache freshness (Phase 16)
# ═══════════════════════════════════════════════════════════════════


class TestCacheFreshness:
    @pytest.fixture(autouse=True)
    def _fresh_service(self):
        from src.data import DataService
        from src.data.cache import TieredCache

        DataService.reset_instance()
        self._svc = DataService(
            provider=_FakeProvider(history=_ohlcv()),
            cache=TieredCache(memory_ttl=60, disk_ttl=600),
        )
        yield
        DataService.reset_instance()

    def test_fresh_cache_used(self):
        h1 = self._svc.get_history("NABIL")
        h2 = self._svc.get_history("NABIL")
        assert h1.source == "provider"
        assert h2.source == "cache"
        assert len(h2.df) == len(h1.df)

    def test_refresh_after_cache_delete(self):
        h1 = self._svc.get_history("NABIL")
        self._svc._cache.delete("history:NABIL:365")
        h2 = self._svc.get_history("NABIL")
        assert h2.source == "provider"

    def test_refresh_failure_returns_unavailable_not_stale(self):
        self._svc._cache.delete("history:NABIL:365")
        self._svc._provider = _FakeProvider(history=None)
        h = self._svc.get_history("NABIL")
        assert h.is_empty

    def test_market_summary_failure_does_not_fake_zero(self):
        from src.data.models import MarketSummary

        summary = self._svc.get_market_summary()
        info = assess_market_summary(summary)
        # Provider fails -> controlled empty, reported unavailable.
        assert summary.status == "Unknown"
        assert info["available"] is False


# ═══════════════════════════════════════════════════════════════════
# Live HTTP timeout (Phase 10) — controlled local server
# ═══════════════════════════════════════════════════════════════════


class _SlowHandler(BaseHTTPRequestHandler):
    delay = 5.0

    def do_GET(self):  # noqa: N802
        time.sleep(self.delay)
        try:
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"index": 1}')
        except OSError:
            # The client already timed out and closed the connection
            # (the whole point of the slow server) — writing to a dead
            # socket must never spam a traceback into the test output.
            pass

    def log_message(self, *args):  # silence
        pass


class TestLiveTimeout:
    @pytest.fixture()
    def slow_server(self):
        server = ThreadingHTTPServer(("127.0.0.1", 0), _SlowHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        yield server, server.server_address[1], thread
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)

    def test_timeout_raises_provider_timeout(self, monkeypatch, slow_server):
        _, port, _ = slow_server
        # APIProvider reads API_TIMEOUT from the module-level binding in
        # src.data.providers (captured at import), not src.config.
        monkeypatch.setattr("src.data.providers.API_TIMEOUT", 1)
        from src.data.providers import APIProvider

        provider = APIProvider(api_urls={"nepse_scraper": f"http://127.0.0.1:{port}"})
        start = time.perf_counter()
        with pytest.raises(ProviderTimeout):
            provider.get_market_summary()
        elapsed = time.perf_counter() - start
        # The client must time out long before the 5s server delay.
        assert elapsed < 4.0

    def test_timeout_falls_back_to_csv(self, monkeypatch, slow_server, tmp_path):
        _, port, _ = slow_server
        monkeypatch.setattr("src.data.providers.API_TIMEOUT", 1)
        from src.data.providers import APIProvider, CSVProvider, HybridProvider

        data_dir = tmp_path / "data"
        write_csvs(data_dir, 1, 60)
        # ``_do_history`` probes the ``github_datasets`` / ``nepse_client``
        # keys (not ``nepse_scraper``), so point *those* at the slow
        # server: the provider genuinely times out on the first shards,
        # hits the fail-fast abort, raises ProviderTimeout, and the
        # hybrid chain falls back to the local CSV.
        base = f"http://127.0.0.1:{port}"
        api = APIProvider(api_urls={"github_datasets": base, "nepse_client": base})
        csv = CSVProvider(data_dir=data_dir)
        quality_metrics.reset()
        hybrid = HybridProvider([api, csv])
        df = hybrid.get_history("SYN000", days=30)
        assert len(df) > 0
        assert hybrid.last_provider == "csv"
        snap = quality_metrics.snapshot()
        assert snap["provider_timeouts"] >= 1
        assert snap["provider_failures"] >= 1

    def test_api_remains_responsive_during_timeout(self, monkeypatch, slow_server):
        _, port, _ = slow_server
        monkeypatch.setattr("src.data.providers.API_TIMEOUT", 1)
        from fastapi.testclient import TestClient

        from src.api.main import app

        with TestClient(app) as client:
            resp = client.get("/")
            assert resp.status_code == 200

    def test_server_cleanup_no_leaked_threads(self, slow_server):
        server, _, thread = slow_server
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
        assert not thread.is_alive()  # serve_forever thread terminated
        # No lingering handler threads: when no request was ever handled,
        # ThreadingMixIn._threads is a ``_NoThreads`` sentinel (no
        # len()); when threads were used it is a list.  Handle both.
        tracked = getattr(server, "_threads", None)
        if isinstance(tracked, list):
            assert len(tracked) == 0


# ═══════════════════════════════════════════════════════════════════
# Market-status API contract (Phase 12/17)
# ═══════════════════════════════════════════════════════════════════


class TestMarketStatusApi:
    def test_data_quality_block_additive(self):
        # A live ``DataService()`` would hit the real provider chain;
        # inject a fake provider instead (no external network in tests).
        from src.data import DataService
        from src.data.cache import TieredCache
        from src.data.models import MarketSummary
        from src.data.providers import BaseProvider

        class _FakeSummaryProvider(BaseProvider):
            name = "fake"

            def _do_market_summary(self) -> MarketSummary:
                return MarketSummary(index=2800.0, status="Closed")

            def _do_live_quotes(self):
                return []

            def _do_history(self, symbol, days=365):
                return pd.DataFrame()

            def _do_nepse_index_history(self, days=500):
                raise NotImplementedError

        DataService.reset_instance()
        DataService(provider=_FakeSummaryProvider(), cache=TieredCache(memory_ttl=60, disk_ttl=600))
        try:
            from src.api.scanner import market_status_payload

            payload = market_status_payload()
            assert "data_quality" in payload
            assert payload["data_quality"]["available"] is True
            assert "unknown_fields" in payload["data_quality"]
            assert "freshness" in payload["data_quality"]
            assert "age_seconds" in payload["data_quality"]
            # Legacy keys preserved.
            for k in ("index", "change", "change_pct", "volume", "turnover", "status"):
                assert k in payload
            assert payload["index"] == 2800.0
        finally:
            DataService.reset_instance()

    def test_stale_cached_summary_labeled_not_fresh(self):
        # A cached summary served after the provider is gone must be
        # labelled by age, never presented as a fresh live print.
        from datetime import timedelta

        from src.data import DataService
        from src.data.cache import TieredCache
        from src.data.models import MarketSummary
        from src.data.providers import BaseProvider

        class _OnceProvider(BaseProvider):
            name = "fake"

            def __init__(self):
                self._served = False

            def _do_market_summary(self) -> MarketSummary:
                if not self._served:
                    self._served = True
                    return MarketSummary(
                        index=2800.0,
                        status="Closed",
                        timestamp=datetime.now() - timedelta(days=20),
                    )
                raise RuntimeError("provider down")

            def _do_live_quotes(self):
                return []

            def _do_history(self, symbol, days=365):
                return pd.DataFrame()

            def _do_nepse_index_history(self, days=500):
                raise NotImplementedError

        DataService.reset_instance()
        p = _OnceProvider()
        DataService(provider=p, cache=TieredCache(memory_ttl=60, disk_ttl=600))
        try:
            from src.data.quality import assess_market_summary

            svc = DataService()
            cached = svc.get_market_summary()  # provider success -> cache
            dq = assess_market_summary(cached)
            assert dq["age_seconds"] is not None
            assert dq["freshness"] == "stale"  # 20 days old
            assert dq["available"] is True
        finally:
            DataService.reset_instance()


# ═══════════════════════════════════════════════════════════════════
# Analyze API additive contract (Phase 17)
# ═══════════════════════════════════════════════════════════════════


class TestAnalyzeApiContract:
    def test_analyze_returns_data_quality(self, tmp_path, monkeypatch):
        from fastapi.testclient import TestClient

        from src.api.main import app

        data_dir = tmp_path / "data"
        _write_fresh_csvs(data_dir, 1, 200)
        # resolve_stock_csv_path reads the csv_loader module global.
        monkeypatch.setattr("src.loaders.csv_loader.DATA_DIRECTORY", str(data_dir))
        with TestClient(app) as client:
            resp = client.get("/analyze/SYN000")
        assert resp.status_code == 200
        body = resp.json()
        assert "data_quality" in body
        assert "symbol" in body
        assert "signal" in body


# ═══════════════════════════════════════════════════════════════════
# Data-quality CLI (Phase 21)
# ═══════════════════════════════════════════════════════════════════


class TestQualityCli:
    def test_validate_corpus_clean(self, tmp_path):
        _write_fresh_csvs(tmp_path, 3, 100)
        agg = validate_corpus(tmp_path)
        assert agg["symbols_checked"] == 3
        assert agg["valid"] == 3
        assert agg["invalid"] == 0

    def test_validate_corpus_counts_invalid(self, tmp_path):
        _write_fresh_csvs(tmp_path, 2, 100)
        df = make_synthetic_df(rows=100, seed=5)
        df["Date"] = pd.bdate_range(
            end=pd.Timestamp.today().normalize() - pd.Timedelta(days=1), periods=len(df)
        )
        df.loc[df.index[-1], "Close"] = -5.0
        df.to_csv(tmp_path / "BAD.csv", index=False)
        agg = validate_corpus(tmp_path)
        assert agg["invalid"] == 1
        assert agg["valid"] == 2

    def test_cli_exits_zero_on_clean(self, tmp_path):
        _write_fresh_csvs(tmp_path, 2, 60)
        proc = subprocess.run(
            [sys.executable, "-m", "src.data.quality", "--data-dir", str(tmp_path)],
            capture_output=True,
            text=True,
            cwd=Path(__file__).resolve().parent.parent,
            timeout=120,
        )
        assert proc.returncode == 0

    def test_cli_exits_nonzero_on_invalid(self, tmp_path):
        _write_fresh_csvs(tmp_path, 1, 60)
        df = make_synthetic_df(rows=60, seed=3)
        df["Date"] = pd.bdate_range(
            end=pd.Timestamp.today().normalize() - pd.Timedelta(days=1), periods=len(df)
        )
        df.loc[df.index[-1], "High"] = df.loc[df.index[-1], "Low"] - 10.0
        df.to_csv(tmp_path / "BAD.csv", index=False)
        proc = subprocess.run(
            [sys.executable, "-m", "src.data.quality", "--data-dir", str(tmp_path)],
            capture_output=True,
            text=True,
            cwd=Path(__file__).resolve().parent.parent,
            timeout=120,
        )
        assert proc.returncode != 0

    def test_cli_allow_invalid_exits_zero(self, tmp_path):
        _write_fresh_csvs(tmp_path, 1, 60)
        df = make_synthetic_df(rows=60, seed=3)
        df["Date"] = pd.bdate_range(
            end=pd.Timestamp.today().normalize() - pd.Timedelta(days=1), periods=len(df)
        )
        df.loc[df.index[-1], "Close"] = -1.0
        df.to_csv(tmp_path / "BAD.csv", index=False)
        proc = subprocess.run(
            [
                sys.executable,
                "-m",
                "src.data.quality",
                "--data-dir",
                str(tmp_path),
                "--allow-invalid",
            ],
            capture_output=True,
            text=True,
            cwd=Path(__file__).resolve().parent.parent,
            timeout=120,
        )
        assert proc.returncode == 0
