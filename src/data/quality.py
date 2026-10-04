"""Centralized market-data quality validation (Sprint 13.3).

The engine must never let raw provider data flow straight into
indicators, signals, ranking or backtests.  Every OHLCV payload that
enters the platform is assessed against the **canonical market-data
contract** defined here:

    status:      VALID | INVALID | SUSPICIOUS
    freshness:   FRESH | STALE | UNKNOWN

Records are checked for:

- OHLC integrity   (positive finite prices, ``high >= max(open, close)``,
                    ``low <= min(open, close)``, ``high >= low``)
- volume           (non-negative, finite)
- dates            (parseable, not in the future, unique where required)
- duplicates       (identical duplicates deduplicated safely; disagreeing
                    duplicates flagged as CONFLICT)
- continuity       (business-day gaps wider than ``DATA_MAX_GAP_DAYS``)
- staleness        (latest record older than ``DATA_STALE_AFTER_DAYS``)

The validator is **centralized** — providers, loaders and API layers
never scatter their own ad-hoc checks.  It returns structured,
machine-readable reports; it never raises raw exception tracebacks to
callers, and it never fabricates data.

Usage::

    from src.data.quality import assess_history, DataQualityReport

    report = assess_history(df, symbol="NABIL")
    if report.status == "INVALID":
        ...   # suppress the symbol / fall back

CLI (Phase 21)::

    python -m src.data.quality --data-dir data/raw
"""

from __future__ import annotations

import argparse
import math
import sys
import threading
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Iterable

import numpy as np
import pandas as pd

from src.config import (
    DATA_DIRECTORY,
    DATA_MAX_GAP_DAYS,
    DATA_STALE_AFTER_DAYS,
)

# ═══════════════════════════════════════════════════════════════════
# Status / freshness codes
# ═══════════════════════════════════════════════════════════════════

VALID = "VALID"
INVALID = "INVALID"
SUSPICIOUS = "SUSPICIOUS"

FRESH = "FRESH"
STALE = "STALE"
UNKNOWN = "UNKNOWN"

# Canonical OHLCV column contract.
OHLCV_COLUMNS = ("Date", "Open", "High", "Low", "Close", "Volume")
# Optional (not required) fields — their absence must not fail a record.
OPTIONAL_COLUMNS = (
    "previous_close",
    "change",
    "change_percent",
    "turnover",
    "transactions",
)

# Reason codes (machine-readable; stable for consumers)
R_MISSING_COLUMNS = "missing_columns"
R_NEGATIVE_OR_ZERO_PRICE = "non_positive_price"
R_NAN_PRICE = "nan_price"
R_INF_PRICE = "infinite_price"
R_NEGATIVE_VOLUME = "negative_volume"
R_NAN_VOLUME = "nan_volume"
R_HIGH_BELOW_MAX = "high_below_open_close"
R_LOW_ABOVE_MIN = "low_above_open_close"
R_HIGH_BELOW_LOW = "high_below_low"
R_EMPTY = "empty_data"
R_INVALID_DATE = "invalid_date"
R_FUTURE_DATE = "future_date"
R_DUPLICATE_DATE = "duplicate_date"
R_CONFLICT_DUPLICATE = "conflicting_duplicate"
R_GAP = "unexpected_gap"
R_STALE = "stale_data"
R_NON_TRADING_DATE = "record_on_non_trading_day"
R_SYMBOL_EMPTY = "empty_symbol"
R_SYMBOL_WHITESPACE = "whitespace_symbol"
R_SYMBOL_UNKNOWN = "unknown_symbol"


# ═══════════════════════════════════════════════════════════════════
# Report structures
# ═══════════════════════════════════════════════════════════════════


@dataclass
class QualityIssue:
    """A single machine-readable quality finding."""

    code: str
    message: str
    level: str = "error"  # "error" | "warning"
    count: int = 1

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "message": self.message,
            "level": self.level,
            "count": self.count,
        }


@dataclass
class DataQualityReport:
    """Structured result of validating a market-data payload.

    ``status`` combines all issue levels:

    - ``VALID``      — no issues
    - ``SUSPICIOUS`` — only warnings (gaps, staleness, identical dupes)
    - ``INVALID``    — at least one error (broken OHLC, NaN, conflicts)
    """

    status: str = VALID
    freshness: str = UNKNOWN
    issues: list[QualityIssue] = field(default_factory=list)
    records_checked: int = 0
    records_valid: int = 0
    records_invalid: int = 0
    latest_date: str | None = None
    as_of: str | None = None
    age_days: float | None = None
    symbol: str = ""
    source: str = ""
    duplicates: int = 0
    conflicts: int = 0
    # Sprint 13.4: calendar-aware missing trading sessions (count of
    # scheduled sessions with no record, per the NEPSE trading calendar).
    missing_sessions: int = 0
    expected_non_trading_days: int = 0
    unknown_sessions: int = 0
    # Sprint 13.5: calendar-invalid records — rows dated on a governed
    # non-trading day (weekend / configured holiday / exceptional
    # closure).  Such data must never drive trusted signals.
    calendar_invalid_records: int = 0

    # ── Queries ───────────────────────────────────────────────────

    @property
    def is_valid(self) -> bool:
        return self.status == VALID

    @property
    def reasons(self) -> list[str]:
        """Machine-readable reason codes (stable, sorted)."""
        return sorted({i.code for i in self.issues})

    def error_codes(self) -> list[str]:
        return sorted({i.code for i in self.issues if i.level == "error"})

    def warning_codes(self) -> list[str]:
        return sorted({i.code for i in self.issues if i.level == "warning"})

    # ── Serialization ─────────────────────────────────────────────

    def to_dict(self) -> dict[str, Any]:
        """JSON-serialisable report (no tracebacks, no paths)."""
        return {
            "status": self.status,
            "freshness": self.freshness,
            "records_checked": self.records_checked,
            "records_valid": self.records_valid,
            "records_invalid": self.records_invalid,
            "duplicates": self.duplicates,
            "conflicts": self.conflicts,
            "latest_date": self.latest_date,
            "as_of": self.as_of,
            "age_days": self.age_days,
            "symbol": self.symbol,
            "source": self.source,
            "reasons": self.reasons,
            "issues": [i.to_dict() for i in self.issues],
            # Sprint 13.4 calendar counters (always present; zero when no
            # calendar-aware assessment ran).
            "missing_sessions": self.missing_sessions,
            "expected_non_trading_days": self.expected_non_trading_days,
            "unknown_sessions": self.unknown_sessions,
            # Sprint 13.5 calendar-invalid record count.
            "calendar_invalid_records": self.calendar_invalid_records,
        }


# ═══════════════════════════════════════════════════════════════════
# Symbol helpers
# ═══════════════════════════════════════════════════════════════════


def normalize_symbol(symbol: Any) -> str:
    """Return the canonical form of a stock symbol (uppercase, stripped)."""
    if symbol is None:
        return ""
    return str(symbol).strip().upper()


def symbol_issues(symbol: Any) -> list[QualityIssue]:
    """Validate a symbol without cross-referencing a company mapping."""
    issues: list[QualityIssue] = []
    if symbol is None or str(symbol).strip() == "":
        issues.append(QualityIssue(R_SYMBOL_EMPTY, "Symbol is empty"))
        return issues
    if str(symbol) != str(symbol).strip():
        issues.append(
            QualityIssue(R_SYMBOL_WHITESPACE, "Symbol has surrounding whitespace", "warning")
        )
    if "/" in str(symbol) or "\\" in str(symbol) or ".." in str(symbol):
        issues.append(
            QualityIssue(
                "unsafe_symbol",
                "Symbol contains path separators or '..'",
            )
        )
    return issues


# ═══════════════════════════════════════════════════════════════════
# OHLC record / frame validation
# ═══════════════════════════════════════════════════════════════════


def _record_price_issues(
    o: float, h: float, l: float, c: float, v: float
) -> list[QualityIssue]:
    """OHLC integrity checks for one record (vectorizable helper)."""
    issues: list[QualityIssue] = []

    for name, value in (("Open", o), ("High", h), ("Low", l), ("Close", c)):
        if value is None or (isinstance(value, float) and math.isnan(value)):
            issues.append(QualityIssue(R_NAN_PRICE, f"{name} is NaN"))
        elif not math.isfinite(float(value)):
            issues.append(QualityIssue(R_INF_PRICE, f"{name} is infinite"))
        elif float(value) <= 0:
            issues.append(QualityIssue(R_NEGATIVE_OR_ZERO_PRICE, f"{name} is not positive"))

    if v is None or (isinstance(v, float) and math.isnan(v)):
        issues.append(QualityIssue(R_NAN_VOLUME, "Volume is NaN"))
    elif not math.isfinite(float(v)):
        issues.append(QualityIssue(R_NAN_VOLUME, "Volume is infinite"))
    elif float(v) < 0:
        issues.append(QualityIssue(R_NEGATIVE_VOLUME, "Volume is negative"))

    finite = [x for x in (o, h, l, c) if x is not None]
    if len(finite) == 4 and all(math.isfinite(float(x)) for x in finite):
        o, h, l, c = (float(x) for x in (o, h, l, c))
        if h < max(o, c):
            issues.append(QualityIssue(R_HIGH_BELOW_MAX, "High below max(open, close)"))
        if l > min(o, c):
            issues.append(QualityIssue(R_LOW_ABOVE_MIN, "Low above min(open, close)"))
        if h < l:
            issues.append(QualityIssue(R_HIGH_BELOW_LOW, "High below Low"))

    return issues


def validate_ohlcv_record(row: dict[str, Any]) -> list[QualityIssue]:
    """Validate a single OHLCV record dict against the canonical contract."""
    return _record_price_issues(
        row.get("Open"),
        row.get("High"),
        row.get("Low"),
        row.get("Close"),
        row.get("Volume"),
    )


def _frame_ohlcv_issues(df: pd.DataFrame) -> tuple[list[QualityIssue], pd.Series]:
    """Vectorized OHLC integrity over a whole frame.

    Returns ``(issues, invalid_mask)`` where *invalid_mask* is a boolean
    Series marking every row with at least one price/volume violation.
    """
    issues: list[QualityIssue] = []
    invalid_mask = pd.Series(False, index=df.index)

    # Missing required columns is a hard INVALID (the whole frame cannot
    # satisfy the canonical contract).
    missing = [c for c in ("Open", "High", "Low", "Close", "Volume") if c not in df.columns]
    if missing:
        issues.append(
            QualityIssue(R_MISSING_COLUMNS, f"Missing required columns: {', '.join(missing)}")
        )
        return issues, pd.Series(True, index=df.index)

    num = df[["Open", "High", "Low", "Close", "Volume"]].apply(pd.to_numeric, errors="coerce")

    # Non-finite prices / volumes
    for col in ("Open", "High", "Low", "Close"):
        nan = num[col].isna()
        inf = ~num[col].isna() & ~np.isfinite(num[col])
        if nan.any():
            issues.append(QualityIssue(R_NAN_PRICE, f"{col} NaN", "error", int(nan.sum())))
            invalid_mask |= nan
        if inf.any():
            issues.append(QualityIssue(R_INF_PRICE, f"{col} infinite", "error", int(inf.sum())))
            invalid_mask |= inf

    v_nan = num["Volume"].isna()
    v_inf = ~num["Volume"].isna() & ~np.isfinite(num["Volume"])
    v_neg = num["Volume"].lt(0)
    if v_nan.any():
        issues.append(QualityIssue(R_NAN_VOLUME, "Volume NaN", "error", int(v_nan.sum())))
        invalid_mask |= v_nan
    if v_inf.any():
        issues.append(QualityIssue(R_NAN_VOLUME, "Volume infinite", "error", int(v_inf.sum())))
        invalid_mask |= v_inf
    if v_neg.any():
        issues.append(QualityIssue(R_NEGATIVE_VOLUME, "Volume negative", "error", int(v_neg.sum())))
        invalid_mask |= v_neg

    # Non-positive prices
    for col in ("Open", "High", "Low", "Close"):
        nonpos = num[col].le(0) & num[col].notna()
        if nonpos.any():
            issues.append(
                QualityIssue(R_NEGATIVE_OR_ZERO_PRICE, f"{col} not positive", "error", int(nonpos.sum()))
            )
            invalid_mask |= nonpos

    # OHLC relationships (only where all four prices are present & finite)
    valid4 = (
        num[["Open", "High", "Low", "Close"]].notna().all(axis=1)
        & np.isfinite(num[["Open", "High", "Low", "Close"]]).all(axis=1)
    )
    if valid4.any():
        sub = num[valid4]
        hi_below_max = sub["High"] < sub[["Open", "Close"]].max(axis=1)
        lo_above_min = sub["Low"] > sub[["Open", "Close"]].min(axis=1)
        hi_below_lo = sub["High"] < sub["Low"]
        if hi_below_max.any():
            issues.append(QualityIssue(R_HIGH_BELOW_MAX, "High below max(open, close)", "error", int(hi_below_max.sum())))
            invalid_mask[valid4] |= hi_below_max
        if lo_above_min.any():
            issues.append(QualityIssue(R_LOW_ABOVE_MIN, "Low above min(open, close)", "error", int(lo_above_min.sum())))
            invalid_mask[valid4] |= lo_above_min
        if hi_below_lo.any():
            issues.append(QualityIssue(R_HIGH_BELOW_LOW, "High below Low", "error", int(hi_below_lo.sum())))
            invalid_mask[valid4] |= hi_below_lo

    return issues, invalid_mask


def _frame_date_issues(
    df: pd.DataFrame,
) -> tuple[list[QualityIssue], pd.Series, int, int]:
    """Date-validity checks: parseable, not future, duplicates, conflicts.

    Returns ``(issues, invalid_mask, identical_pairs, conflicts)``.
    *invalid_mask* marks rows with bad/future dates plus the extra rows
    of conflicting duplicate groups; *identical_pairs* / *conflicts* are
    the duplicate counts (extra rows) reused by the report counters so
    the grouping logic is computed exactly once.
    """
    issues: list[QualityIssue] = []
    invalid_mask = pd.Series(False, index=df.index)
    identical_pairs = 0
    conflicts = 0
    if "Date" not in df.columns:
        return issues, invalid_mask, identical_pairs, conflicts

    dates = pd.to_datetime(df["Date"], errors="coerce")
    today = pd.Timestamp.today().normalize()

    bad = dates.isna()
    if bad.any():
        issues.append(QualityIssue(R_INVALID_DATE, "Unparseable dates", "error", int(bad.sum())))
    future = dates.notna() & (dates.dt.normalize() > today)
    if future.any():
        issues.append(
            QualityIssue(R_FUTURE_DATE, "Future dates", "error", int(future.sum()))
        )

    invalid_mask |= bad | future

    # Duplicates by trading date.
    if dates.notna().any():
        dup_dates = dates[dates.duplicated(keep=False)].dropna()
        if not dup_dates.empty:
            seen_conflict = False
            for d in dup_dates.unique():
                group = df[dates == d]
                payload = group[["Open", "High", "Low", "Close", "Volume"]]
                unique_rows = payload.drop_duplicates().shape[0]
                extra = len(group) - 1
                if unique_rows == 1:
                    identical_pairs += extra  # safe to deduplicate
                else:
                    seen_conflict = True
                    conflicts += extra
                    # Every *extra* row of a conflicting group is invalid
                    # (they contradict the first row of the same date).
                    invalid_mask.loc[group.index[1:]] = True
            if identical_pairs:
                issues.append(
                    QualityIssue(
                        R_DUPLICATE_DATE,
                        "Identical duplicate dates (deduplicable)",
                        "warning",
                        identical_pairs,
                    )
                )
            if seen_conflict:
                issues.append(
                    QualityIssue(
                        R_CONFLICT_DUPLICATE,
                        "Conflicting duplicate dates",
                        "error",
                        1,
                    )
                )

    return issues, invalid_mask, identical_pairs, conflicts


def _frame_continuity_issues(
    df: pd.DataFrame,
    max_gap_days: int = DATA_MAX_GAP_DAYS,
    calendar: Any = None,
) -> list[QualityIssue]:
    """Detect missing trading sessions between consecutive records.

    Sprint 13.4 (Phase 12/13): when a NEPSE trading *calendar* is
    supplied, gaps are classified with trading-session semantics — a
    legitimate closure (weekend / holiday / corpus-verified non-trading
    day) is ``EXPECTED_NON_TRADING_DAY`` and is **not** a data problem,
    while a scheduled session with no record is ``MISSING_TRADING_SESSION``.
    Without a calendar the historical business-day heuristic is kept
    (backward compatible).
    """
    if "Date" not in df.columns:
        return []
    dates = pd.to_datetime(df["Date"], errors="coerce").dropna().drop_duplicates().sort_values()
    if len(dates) < 2:
        return []

    if calendar is not None:
        issues: list[QualityIssue] = []
        prev = dates.iloc[0]
        for cur in dates.iloc[1:]:
            if cur <= prev:
                continue
            findings = calendar.classify_sessions(
                [prev, cur], start=prev.date(), end=cur.date()
            )
            missing = sum(
                1 for f in findings if f.classification == "MISSING_TRADING_SESSION"
            )
            if missing > max_gap_days:
                issues.append(
                    QualityIssue(
                        R_GAP,
                        f"{missing} missing trading session(s) before {cur.date()} "
                        f"(calendar {calendar.version})",
                        "warning",
                    )
                )
            prev = cur
        return issues

    # Legacy business-day heuristic (kept as the default so existing
    # callers behave byte-identically).
    issues = []
    prev = dates.iloc[0]
    for cur in dates.iloc[1:]:
        if cur <= prev:
            continue
        # Business days strictly between prev and cur.
        gap = int(np.busday_count(prev.date(), cur.date()))
        if gap > max_gap_days:
            issues.append(
                QualityIssue(
                    R_GAP,
                    f"Gap of {gap} business days before {cur.date()}",
                    "warning",
                )
            )
        prev = cur
    return issues


# ═══════════════════════════════════════════════════════════════════
# Freshness
# ═══════════════════════════════════════════════════════════════════


def assess_freshness(
    latest: pd.Timestamp | date | None,
    *,
    stale_after_days: int = DATA_STALE_AFTER_DAYS,
    now: datetime | date | None = None,
) -> tuple[str, float | None]:
    """Classify *latest* as FRESH / STALE / UNKNOWN.

    Returns ``(freshness, age_days)`` — age in days from the latest
    record to *now* (``None`` when the latest date is unknown).
    """
    if latest is None:
        return UNKNOWN, None
    try:
        latest_ts = pd.Timestamp(latest)
    except (ValueError, TypeError):
        return UNKNOWN, None
    now_ts = pd.Timestamp(now) if now is not None else pd.Timestamp.now()
    if pd.isna(latest_ts):
        return UNKNOWN, None
    age_days = (now_ts.normalize() - latest_ts.normalize()).days
    if age_days > stale_after_days:
        return STALE, float(age_days)
    return FRESH, float(age_days)


# ═══════════════════════════════════════════════════════════════════
# Top-level frame assessment
# ═══════════════════════════════════════════════════════════════════


def assess_history(
    df: pd.DataFrame,
    symbol: str | None = None,
    *,
    source: str = "",
    stale_after_days: int = DATA_STALE_AFTER_DAYS,
    max_gap_days: int = DATA_MAX_GAP_DAYS,
    calendar: Any = None,
) -> DataQualityReport:
    """Assess a history DataFrame against the canonical OHLCV contract.

    This is the primary entry point for history frames.  It never
    raises; it returns a structured :class:`DataQualityReport`.

    Args:
        calendar: Optional NEPSE trading calendar (Sprint 13.4).  When
            provided, gap detection uses trading-session semantics
            (weekends/holidays are not missing data; scheduled sessions
            with no record are counted in ``report.missing_sessions``).
    """
    report = DataQualityReport(symbol=normalize_symbol(symbol), source=source)
    report.records_checked = 0 if df is None else len(df)

    if df is None or df.empty:
        report.issues.append(QualityIssue(R_EMPTY, "No data rows"))
        report.status = INVALID
        return report

    price_issues, invalid_price_mask = _frame_ohlcv_issues(df)
    date_issues, invalid_date_mask, identical_pairs, conflicts = _frame_date_issues(df)
    continuity_issues = _frame_continuity_issues(df, max_gap_days=max_gap_days, calendar=calendar)
    report.issues = price_issues + date_issues + continuity_issues

    # Calendar-aware counters (Phase 12): classify every day spanned by
    # the frame so callers can distinguish legitimate closures from
    # missing market data.
    if calendar is not None and "Date" in df.columns:
        observed = pd.to_datetime(df["Date"], errors="coerce").dropna()
        if len(observed):
            findings = calendar.classify_sessions(
                observed, start=observed.min().date(), end=observed.max().date()
            )
            for f in findings:
                if f.classification == "MISSING_TRADING_SESSION":
                    report.missing_sessions += 1
                elif f.classification == "EXPECTED_NON_TRADING_DAY":
                    report.expected_non_trading_days += 1
                elif f.classification == "UNKNOWN":
                    report.unknown_sessions += 1

    # Union of the row masks: price *and* date violations on different
    # rows are all counted (max() would undercount).
    report.records_invalid = int((invalid_price_mask | invalid_date_mask).sum())
    report.duplicates = identical_pairs
    report.conflicts = conflicts

    # Sprint 13.5 calendar-invalid records: a row dated on a governed
    # non-trading day (weekend / configured holiday / exceptional
    # closure) is a quality failure — data printed on a closed session
    # must never drive a trusted signal.  Special sessions are trading
    # days; UNKNOWN days (no corpus evidence either way) are reported
    # in ``unknown_sessions`` but never flagged as invalid.
    if calendar is not None and "Date" in df.columns:
        cal_dates = pd.to_datetime(df["Date"], errors="coerce").dropna()
        if len(cal_dates):
            from src.data.calendar import (  # noqa: PLC0415 - lazy
                EXCEPTIONAL_CLOSURE,
                HOLIDAY,
                WEEKEND,
            )

            non_trading_days: set[date] = set()
            for ts in cal_dates.unique():
                cls = calendar.classify_date(ts.date())
                if cls in (WEEKEND, HOLIDAY, EXCEPTIONAL_CLOSURE):
                    non_trading_days.add(ts.date())
            if non_trading_days:
                mask = cal_dates.dt.date.isin(non_trading_days)
                count = int(mask.sum())
                report.calendar_invalid_records = count
                # A row that is both price/date-invalid AND
                # calendar-invalid must be counted once: only the
                # calendar-invalid rows not already covered by the
                # price/date union mask add to ``records_invalid``.
                # ``cal_dates`` was dropna'd so its index is a subset of
                # the frame's — reindex the union mask to align.
                union_mask = invalid_price_mask | invalid_date_mask
                extra = int(
                    (mask & ~union_mask.reindex(mask.index, fill_value=False)).sum()
                )
                report.records_invalid += extra
                report.issues.append(
                    QualityIssue(
                        R_NON_TRADING_DATE,
                        f"{count} record(s) on non-trading calendar day(s): "
                        + ", ".join(sorted(d.isoformat() for d in non_trading_days)[:5])
                        + f" (calendar {calendar.version})",
                        "error",
                    )
                )

    # Freshness from the latest record.
    if "Date" in df.columns:
        dates = pd.to_datetime(df["Date"], errors="coerce").dropna()
        if len(dates):
            latest = dates.max()
            report.latest_date = latest.date().isoformat()
            # ``as_of`` is the *data's* latest trading date — a
            # deterministic property of the payload, so identical frames
            # always produce identical reports (cache hit == miss in
            # ``analyze_dataframe`` must compare equal; a wall-clock
            # assessment timestamp would break that at second
            # boundaries).  API consumers needing the assessment moment
            # can use the freshness ``age_days``/``age_seconds``.
            report.as_of = report.latest_date
            freshness, age_days = assess_freshness(latest, stale_after_days=stale_after_days)
            report.freshness = freshness
            report.age_days = age_days
            if freshness == STALE:
                report.issues.append(
                    QualityIssue(
                        R_STALE,
                        f"Latest record {report.latest_date} is {age_days:.0f} days old",
                        "warning",
                    )
                )

    # Status composition: any error -> INVALID; else any warning -> SUSPICIOUS.
    if any(i.level == "error" for i in report.issues):
        report.status = INVALID
    elif report.issues:
        report.status = SUSPICIOUS

    report.records_valid = max(0, report.records_checked - report.records_invalid)
    return report


def validate_history_frame(
    df: pd.DataFrame,
    symbol: str | None = None,
    *,
    source: str = "",
) -> DataQualityReport:
    """Assess a frame and raise ``InvalidDataError`` when INVALID.

    Used by the provider-fallback chain so a malformed provider payload
    is rejected and the next provider is tried, instead of being
    silently consumed (Sprint 13.3 Phase 9).
    """
    from src.data.exceptions import InvalidDataError  # noqa: PLC0415 - lazy

    report = assess_history(df, symbol=symbol, source=source)
    if report.status == INVALID:
        raise InvalidDataError(
            f"Data quality validation failed for {report.symbol or '?'}: "
            + ", ".join(report.error_codes())
        )
    return report


# ═══════════════════════════════════════════════════════════════════
# Quote / market summary validation
# ═══════════════════════════════════════════════════════════════════


def assess_quote(quote: Any) -> list[str]:
    """Return reason codes for an invalid live quote (empty = valid)."""
    if quote is None:
        return ["missing_quote"]
    codes: list[str] = []
    try:
        ltp = float(quote.ltp)
    except (TypeError, ValueError):
        ltp = float("nan")
    if math.isnan(ltp) or ltp < 0:
        codes.append("negative_or_nan_ltp")
    try:
        vol = int(quote.volume)
    except (TypeError, ValueError):
        vol = -1
    if vol < 0:
        codes.append("negative_volume")
    return codes


def assess_market_summary(summary: Any, *, source: str = "") -> dict[str, Any]:
    """Validate a market summary, distinguishing real zero from unknown.

    The engine must never present ``NEPSE 0.00`` when the provider was
    unavailable: a genuine zero (a real market print) is different from
    an unknown value (provider failure).  Returns a machine-readable
    dict with ``available`` / ``status`` / ``unknown_fields``.

    Semantics (Sprint 13.3 Phase 12): the summary ``status`` is
    **authoritative**.  ``MarketSummary.empty()`` (returned by
    ``DataService`` when the provider fails) carries ``status="Unknown"``
    and zeroed core fields — that is *unavailable*, not a real zero, so
    ``available=False`` even though the numeric fields are 0.0.  A
    summary whose status is ``Open``/``Closed``/``Holiday`` is available;
    its core fields may legitimately be zero (e.g. ``change == 0.0`` on a
    flat session).
    """
    if summary is None:
        return {
            "status": "unknown",
            "available": False,
            "reason": "no_summary",
            "unknown_fields": ["index", "change", "change_pct", "volume", "turnover"],
            "as_of": None,
            "age_seconds": None,
            "freshness": "unknown",
            "source": source,
        }
    status = str(getattr(summary, "status", "Unknown"))
    available = status.lower() != "unknown"

    unknown_fields: list[str] = []
    for f in ("index", "change", "change_pct", "volume", "turnover"):
        try:
            val = float(getattr(summary, f, float("nan")))
        except (TypeError, ValueError):
            val = float("nan")
        if math.isnan(val):
            unknown_fields.append(f)
        elif not available and float(val) == 0.0:
            # When the provider is unavailable, zero-valued core fields
            # are *unknown*, not genuine prints.
            unknown_fields.append(f)

    # Age of the snapshot: derived from the summary's own timestamp so a
    # cached summary served after a provider failure is *labelled stale*
    # instead of masquerading as a fresh live print (Phase 16/17).  A
    # ``None``/missing timestamp yields age_seconds=None (unknown age).
    age_seconds: float | None = None
    freshness = "unknown"
    ts = getattr(summary, "timestamp", None)
    if ts is not None:
        try:
            age_seconds = max(0.0, (datetime.now() - ts).total_seconds())
        except (TypeError, ValueError):
            age_seconds = None
        freshness = "fresh" if (age_seconds is not None and age_seconds <= DATA_STALE_AFTER_DAYS * 86400) else "stale"
    if not available:
        freshness = "unknown"

    return {
        "status": "fresh" if available else "unknown",
        "available": available,
        "unknown_fields": unknown_fields,
        "as_of": ts.isoformat() if ts is not None else None,
        "age_seconds": age_seconds,
        "freshness": freshness,
        "source": source,
    }


# ═══════════════════════════════════════════════════════════════════
# Provider disagreement detection (Phase 9)
# ═══════════════════════════════════════════════════════════════════


def detect_disagreement(
    a: Any,
    b: Any,
    *,
    fields: tuple[str, ...] = ("close", "high", "low"),
    tolerance_pct: float = 1.0,
) -> list[str]:
    """Detect material disagreement between two provider payloads.

    Two values *disagree* when they differ by more than
    *tolerance_pct* percent.  Returns a list of ``field`` reason codes
    (empty = no material disagreement).  The engine must never silently
    average conflicting provider prices — callers decide the documented
    resolution policy (usually: prefer the more authoritative source, or
    mark the value SUSPICIOUS).
    """
    def _val(obj: Any, f: str) -> float | None:
        try:
            v = float(getattr(obj, f, float("nan")))
        except (TypeError, ValueError):
            return None
        return None if math.isnan(v) else v

    disagreements: list[str] = []
    for f in fields:
        va, vb = _val(a, f), _val(b, f)
        if va is None or vb is None:
            continue
        if va == 0 and vb == 0:
            continue
        denom = max(abs(va), abs(vb))
        if denom == 0:
            continue
        if abs(va - vb) / denom * 100.0 > tolerance_pct:
            disagreements.append(f"conflict:{f}")
    return disagreements


# ═══════════════════════════════════════════════════════════════════
# Bounded quality metrics (Phase 13)
# ═══════════════════════════════════════════════════════════════════


class QualityMetricsCollector:
    """Thread-safe, bounded counters for data-quality observability.

    Only scalar counters are kept — never per-record history — so the
    collector is bounded regardless of how much data flows through it.
    """

    _FIELDS = (
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
    )

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._counts: dict[str, int] = {f: 0 for f in self._FIELDS}

    def record_report(self, report: DataQualityReport) -> None:
        with self._lock:
            self._counts["records_checked"] += report.records_checked
            self._counts["records_valid"] += report.records_valid
            self._counts["records_invalid"] += report.records_invalid
            self._counts["duplicates"] += report.duplicates
            self._counts["conflicts"] += report.conflicts
            if report.freshness == STALE:
                self._counts["stale_records"] += 1
            if report.status == SUSPICIOUS:
                self._counts["records_suspicious"] += report.records_checked

    def record_provider_failure(self, count: int = 1) -> None:
        with self._lock:
            self._counts["provider_failures"] += count

    def record_provider_timeout(self, count: int = 1) -> None:
        with self._lock:
            self._counts["provider_timeouts"] += count

    def record_fallback(self, count: int = 1) -> None:
        with self._lock:
            self._counts["fallback_count"] += count

    def snapshot(self) -> dict[str, int]:
        with self._lock:
            return dict(self._counts)

    def reset(self) -> None:
        with self._lock:
            self._counts = {f: 0 for f in self._FIELDS}


# Shared singleton so DataService / providers / API all report into one
# bounded collector.
quality_metrics = QualityMetricsCollector()


# ═══════════════════════════════════════════════════════════════════
# Bounded data-quality trends (Sprint 13.6 §8)
# ═══════════════════════════════════════════════════════════════════


class QualityTrendTracker:
    """Bounded historical aggregates of corpus quality (Sprint 13.6).

    Answers "is data quality getting better or worse compared with the
    previous refresh?" by keeping a bounded ring of recent corpus
    snapshots (default 20) and deriving a compact latest-vs-previous
    delta.  Only scalar aggregates are stored — never per-record
    history — so the tracker is bounded regardless of corpus size.

    Usage::

        quality_trends.record_corpus(aggregate)   # after a corpus gate
        trend = quality_trends.trend()            # bounded comparison
    """

    _FIELDS = (
        "symbols_checked",
        "valid",
        "invalid",
        "suspicious",
        "duplicates",
        "conflicts",
        "stale",
    )

    def __init__(self, max_snapshots: int = 20) -> None:
        self._lock = threading.RLock()
        self._max = max(2, int(max_snapshots))
        self._snapshots: list[dict[str, Any]] = []

    def record_corpus(self, aggregate: Mapping[str, Any]) -> dict[str, Any]:
        """Record one corpus-quality snapshot (bounded ring)."""
        snapshot = {
            f: int(aggregate.get(f, 0) or 0) for f in self._FIELDS
        }
        snapshot["timestamp"] = datetime.now().isoformat(timespec="seconds")
        with self._lock:
            self._snapshots.append(snapshot)
            if len(self._snapshots) > self._max:
                self._snapshots = self._snapshots[-self._max:]
        return snapshot

    def reset(self) -> None:
        with self._lock:
            self._snapshots.clear()

    def _direction(self, field: str, latest: int, previous: int) -> str:
        """improving / worsening / stable for one quality metric.

        For most fields lower is better (invalid, suspicious, stale,
        duplicates, conflicts); for ``valid`` higher is better.
        """
        if latest == previous:
            return "stable"
        better_if_lower = field not in ("valid", "symbols_checked")
        improved = latest < previous if better_if_lower else latest > previous
        return "improving" if improved else "worsening"

    def trend(self) -> dict[str, Any]:
        """Bounded latest-vs-previous comparison (empty-safe)."""
        with self._lock:
            if not self._snapshots:
                return {"has_history": False, "snapshots": 0, "latest": None, "previous": None, "delta": {}}
            latest = self._snapshots[-1]
            previous = self._snapshots[-2] if len(self._snapshots) >= 2 else None
            delta: dict[str, Any] = {}
            if previous is not None:
                for f in self._FIELDS:
                    delta[f] = {
                        "latest": latest[f],
                        "previous": previous[f],
                        "change": latest[f] - previous[f],
                        "direction": self._direction(f, latest[f], previous[f]),
                    }
            return {
                "has_history": True,
                "snapshots": len(self._snapshots),
                "max_snapshots": self._max,
                "latest": {f: latest[f] for f in self._FIELDS},
                "previous": {f: previous[f] for f in self._FIELDS} if previous else None,
                "delta": delta,
            }


# Shared singleton so the corpus gate and the API report into one bounded
# trend tracker.
quality_trends = QualityTrendTracker()


# ═══════════════════════════════════════════════════════════════════
# Corpus report + CLI (Phase 21)
# ═══════════════════════════════════════════════════════════════════


def validate_corpus(data_dir: str | Path) -> dict[str, Any]:
    """Validate every OHLCV CSV under *data_dir*; return aggregate stats.

    Never writes, never mutates, never fabricates.  Reads each CSV
    through the canonical loader (so scanner-cache semantics apply) and
    assesses each against the contract.
    """
    from src.loaders.csv_loader import load_csv  # noqa: PLC0415 - lazy

    base = Path(data_dir)
    files = sorted(base.glob("*.csv"))
    files = [f for f in files if f.stem.lower() != "sample"]

    aggregate = {
        "symbols_checked": len(files),
        "records_checked": 0,
        "valid": 0,
        "invalid": 0,
        "suspicious": 0,
        "duplicates": 0,
        "conflicts": 0,
        "stale": 0,
        "per_symbol": [],
    }

    for f in files:
        try:
            df = load_csv(f)
            report = assess_history(df, symbol=f.stem.upper(), source="csv")
        except Exception as exc:  # noqa: BLE001 - a broken file must not abort the report
            report = DataQualityReport(
                status=INVALID,
                symbol=f.stem.upper(),
                source="csv",
                issues=[QualityIssue("unreadable_file", str(exc))],
            )
        aggregate["records_checked"] += report.records_checked
        aggregate["duplicates"] += report.duplicates
        aggregate["conflicts"] += report.conflicts
        if report.status == VALID:
            aggregate["valid"] += 1
        elif report.status == INVALID:
            aggregate["invalid"] += 1
        else:
            aggregate["suspicious"] += 1
        if report.freshness == STALE:
            aggregate["stale"] += 1
        aggregate["per_symbol"].append(
            {
                "symbol": report.symbol or f.stem.upper(),
                "status": report.status,
                "freshness": report.freshness,
                "latest_date": report.latest_date,
                "age_days": report.age_days,
                "reasons": report.reasons,
            }
        )

    # Sprint 13.6: record this corpus gate into the bounded trend
    # tracker so operators can see whether quality is improving or
    # worsening across refreshes (scalar aggregates only).
    quality_trends.record_corpus(aggregate)
    return aggregate


def _print_corpus_report(aggregate: dict[str, Any]) -> None:
    print("=" * 72)
    print("NEPSE Quant Engine — Data Quality Report (Sprint 13.3)")
    print("=" * 72)
    print(f"{'symbol':<12}{'status':<11}{'fresh':<8}{'latest':<12}{'age_d':<7}reasons")
    print("-" * 72)
    for row in aggregate["per_symbol"]:
        reasons = ",".join(row["reasons"])[:36] if row["reasons"] else "-"
        age = f"{row['age_days']:.0f}" if row["age_days"] is not None else "-"
        print(
            f"{row['symbol']:<12}{row['status']:<11}{row['freshness']:<8}"
            f"{str(row['latest_date']):<12}{age:<7}{reasons}"
        )
    print("-" * 72)
    print(f"symbols checked : {aggregate['symbols_checked']}")
    print(f"records checked : {aggregate['records_checked']}")
    print(f"valid           : {aggregate['valid']}")
    print(f"invalid         : {aggregate['invalid']}")
    print(f"suspicious      : {aggregate['suspicious']}")
    print(f"duplicates      : {aggregate['duplicates']}")
    print(f"conflicts       : {aggregate['conflicts']}")
    print(f"stale           : {aggregate['stale']}")


def main(argv: list[str] | None = None) -> int:
    """CLI entry point: ``python -m src.data.quality``."""
    parser = argparse.ArgumentParser(
        prog="python -m src.data.quality",
        description="Validate market-data files against the canonical OHLCV contract.",
    )
    parser.add_argument(
        "--data-dir",
        default=DATA_DIRECTORY,
        help=f"Directory of OHLCV CSVs (default: {DATA_DIRECTORY})",
    )
    parser.add_argument(
        "--stale-days",
        type=int,
        default=DATA_STALE_AFTER_DAYS,
        help="Stale threshold in days",
    )
    parser.add_argument(
        "--max-gap-days",
        type=int,
        default=DATA_MAX_GAP_DAYS,
        help="Continuity gap threshold in business days",
    )
    parser.add_argument(
        "--allow-invalid",
        action="store_true",
        help="Exit 0 even when critical conditions fail (report only)",
    )
    args = parser.parse_args(argv)

    aggregate = validate_corpus(args.data_dir)
    _print_corpus_report(aggregate)

    critical = aggregate["invalid"] > 0 or aggregate["conflicts"] > 0
    if critical and not args.allow_invalid:
        print("\nCRITICAL: invalid records or conflicting duplicates found.", file=sys.stderr)
        return 1
    print("\nData quality gate: PASS" if not critical else "\nData quality gate: FAIL")
    return 0


if __name__ == "__main__":
    sys.exit(main())
