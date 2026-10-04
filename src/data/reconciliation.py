"""Cross-provider reconciliation (Sprint 13.4).

When multiple providers report different but individually-valid data for
the same security, the engine must **detect**, **classify** and
**safely resolve** the disagreement instead of blindly trusting provider
priority.  This module is the reconciliation layer:

    Provider A ─────┐
                    ├── Normalize ──► Reconcile ──► trusted result
    Provider B ─────┘

Explicit statuses (Phase 5)::

    AGREE                  — providers agree within tolerance → validated data
    MINOR_DISAGREEMENT     — small drift → documented preferred source + warning
    MATERIAL_DISAGREEMENT  — large drift → attempt independent validation;
                             if unresolved → quarantine (never silently trusted)
    UNAVAILABLE            — no provider data → fallback
    MAPPING_CONFLICT       — providers disagree about which security the
                             record belongs to → never reconciled

Field-level reconciliation (Phase 6) covers at minimum
``open``/``high``/``low``/``close``/``volume``; optional fields
(``previous_close``, ``turnover``, ``transactions``) are reconciled when
present but never required.  Price fields use a tight relative tolerance
while volume uses a wider one — never one fixed percentage for every
field (configurable via ``RECONCILE_*`` settings in ``src/config``).

Hard rules:

- Never silently average conflicting provider prices.
- Never compare records that belong to different securities.
- A materially conflicting record must not become a normal trusted
  record.
- All metrics are bounded scalars (no per-symbol history).
"""

from __future__ import annotations

import math
import threading
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Mapping, Sequence

from src.config import (
    RECONCILE_CORPORATE_ACTION_MOVE_PCT,
    RECONCILE_MATERIAL_PRICE_PCT,
    RECONCILE_MATERIAL_VOLUME_PCT,
    RECONCILE_PREFERRED_SOURCE,
    RECONCILE_PRICE_TOLERANCE_PCT,
    RECONCILE_VOLUME_TOLERANCE_PCT,
)

# ═══════════════════════════════════════════════════════════════════
# Status codes
# ═══════════════════════════════════════════════════════════════════

AGREE = "AGREE"
MINOR_DISAGREEMENT = "MINOR_DISAGREEMENT"
MATERIAL_DISAGREEMENT = "MATERIAL_DISAGREEMENT"
UNAVAILABLE = "UNAVAILABLE"
MAPPING_CONFLICT = "MAPPING_CONFLICT"

RECONCILIATION_STATUSES = (
    AGREE,
    MINOR_DISAGREEMENT,
    MATERIAL_DISAGREEMENT,
    UNAVAILABLE,
    MAPPING_CONFLICT,
)

# Fields reconciled at minimum (Phase 6).  Optional fields are
# reconciled only when both providers supply them.
PRICE_FIELDS = ("open", "high", "low", "close")
VOLUME_FIELDS = ("volume",)
OPTIONAL_FIELDS = ("previous_close", "turnover", "transactions")
ALL_RECONCILED_FIELDS = PRICE_FIELDS + VOLUME_FIELDS + OPTIONAL_FIELDS

# Field aliases accepted in normalized records (lowercased).
_FIELD_ALIASES = {
    "open": ("open", "open_price", "openprice"),
    "high": ("high", "high_price", "highprice"),
    "low": ("low", "low_price", "lowprice"),
    "close": ("close", "close_price", "closeprice", "ltp", "last"),
    "volume": ("volume", "total_traded_shares", "totaltradedshares", "vol"),
    "previous_close": ("previous_close", "prev_close", "previousclose", "prevclose"),
    "turnover": ("turnover", "total_turnover", "totalturnover"),
    "transactions": ("transactions", "total_transactions", "totaltransactions"),
}


def _norm_key(field: str) -> str | None:
    """Map a raw field name to a canonical reconciled field name."""
    key = str(field).strip().lower()
    for canonical, aliases in _FIELD_ALIASES.items():
        if key == canonical or key in aliases:
            return canonical
    return None


def relative_difference(a: float | None, b: float | None) -> float | None:
    """Relative difference between two values (Phase 6 formula).

    ``abs(a - b) / max(abs(a), abs(b))`` as a fraction in [0, 1]; a
    value of 0.01 means 1%.  Returns ``None`` when either value is
    missing or both are zero.
    """
    if a is None or b is None:
        return None
    if not (math.isfinite(a) and math.isfinite(b)):
        return None
    denom = max(abs(a), abs(b))
    if denom == 0.0:
        return None
    return abs(a - b) / denom


def _tolerance_for(field: str, tolerances: Mapping[str, float] | None) -> float:
    if tolerances and field in tolerances:
        return float(tolerances[field])
    if field in VOLUME_FIELDS:
        return RECONCILE_VOLUME_TOLERANCE_PCT / 100.0
    return RECONCILE_PRICE_TOLERANCE_PCT / 100.0


def _material_for(field: str, material: Mapping[str, float] | None) -> float:
    if material and field in material:
        return float(material[field])
    if field in VOLUME_FIELDS:
        return RECONCILE_MATERIAL_VOLUME_PCT / 100.0
    return RECONCILE_MATERIAL_PRICE_PCT / 100.0


# ═══════════════════════════════════════════════════════════════════
# Results
# ═══════════════════════════════════════════════════════════════════


@dataclass
class ReconciliationResult:
    """Structured outcome of reconciling provider records (Phase 5).

    Never exposes raw provider payloads — only canonical field values,
    difference metrics, provenance and warnings.
    """

    status: str = UNAVAILABLE
    symbol: str = ""
    date: str = ""  # ISO date of the reconciled record ('' for history-wide)
    providers: list[str] = field(default_factory=list)  # providers that responded
    selected_source: str | None = None
    disagreement_fields: list[str] = field(default_factory=list)
    difference_metrics: dict[str, float] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    as_of: str | None = None
    fallback_used: bool = False

    @property
    def is_trusted(self) -> bool:
        """True when the record may enter the analysis pipeline.

        Only AGREE and MINOR (resolved to the documented preferred
        source with a warning attached) are trusted.  MATERIAL and
        MAPPING_CONFLICT must never become normal trusted records;
        UNAVAILABLE has nothing to trust.
        """
        return self.status in (AGREE, MINOR_DISAGREEMENT)

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "symbol": self.symbol,
            "date": self.date,
            "providers": list(self.providers),
            "selected_source": self.selected_source,
            "disagreement_fields": list(self.disagreement_fields),
            "difference_metrics": dict(self.difference_metrics),
            "warnings": list(self.warnings),
            "as_of": self.as_of,
            "fallback_used": self.fallback_used,
        }


# ═══════════════════════════════════════════════════════════════════
# Symbol / company mapping (Phase 15)
# ═══════════════════════════════════════════════════════════════════


def normalize_identifier(value: Any) -> str:
    """Canonical provider identifier for a security (upper, stripped)."""
    if value is None:
        return ""
    return str(value).strip().upper()


def check_symbol_mapping(
    symbol: Any,
    provider_idents: Mapping[str, Any],
    company_names: Mapping[str, Any] | None = None,
) -> tuple[bool, list[str]]:
    """Verify provider records refer to the same security.

    Args:
        symbol: The requested canonical symbol (e.g. ``"NABIL"``).
        provider_idents: Mapping ``provider_name -> raw symbol/identifier``.
        company_names: Optional ``provider_name -> company name``.  When
            two providers name *different companies* for the same
            symbol, the mapping is a conflict even if identifiers match.

    Returns:
        ``(ok, reasons)``.  ``ok=False`` means MAPPING_CONFLICT — the
        records must never be reconciled or compared.
    """
    reasons: list[str] = []
    canonical = normalize_identifier(symbol)

    # Identifier check: every provider identifier must resolve to the
    # requested canonical symbol (case-insensitive).  A provider
    # identifier that is empty means the provider did not supply an
    # identity — treated as unknown, not a conflict.
    idents = {p: normalize_identifier(i) for p, i in provider_idents.items() if i is not None}
    for provider, ident in idents.items():
        if not ident:
            continue
        if canonical and ident != canonical:
            reasons.append(
                f"{provider} identifier '{ident}' does not match symbol '{canonical}'"
            )

    # Company-identity check: when both providers name a company, they
    # must agree.  Empty/missing names are unknown, not conflicts.
    if company_names:
        names = {
            p: str(n).strip().lower()
            for p, n in company_names.items()
            if n is not None and str(n).strip()
        }
        uniq = set(names.values())
        if len(names) >= 2 and len(uniq) > 1:
            reasons.append(
                "providers disagree on company identity: "
                + ", ".join(f"{p}='{n}'" for p, n in sorted(names.items()))
            )

    return (not reasons, reasons)


# ═══════════════════════════════════════════════════════════════════
# Corporate-action awareness (Phase 14)
# ═══════════════════════════════════════════════════════════════════


@dataclass
class PriceJumpAssessment:
    """Classification of a large price movement."""

    kind: str  # "normal" | "large_but_legitimate" | "structurally_impossible"
    reason: str = ""
    pct_change: float | None = None


def classify_price_jump(
    previous_close: float | None,
    current_open: float | None,
    current_close: float | None,
    *,
    large_move_pct: float | None = None,
    ohlcv_ok: bool = True,
) -> PriceJumpAssessment:
    """Distinguish large-but-potentially-legitimate moves from bad data.

    Corporate actions (dividends, right shares, bonus shares, splits)
    produce legitimate single-day price jumps that are *not* data
    corruption.  The reconciliation layer must never quarantine such a
    move simply because it is large.

    Args:
        large_move_pct: Threshold (percent) above which a move is flagged
            as large-but-legitimate; defaults to the configurable
            ``RECONCILE_CORPORATE_ACTION_MOVE_PCT``.

    Returns:
        - ``structurally_impossible`` — the OHLC itself is broken
          (``ohlcv_ok=False``); this is a data problem regardless of size.
        - ``large_but_legitimate`` — the day-over-day change exceeds
          *large_move_pct* but the OHLC structure is sound; flagged so
          callers can annotate, never reject.
        - ``normal`` — no large jump.
    """
    threshold = (
        float(large_move_pct)
        if large_move_pct is not None
        else RECONCILE_CORPORATE_ACTION_MOVE_PCT
    )
    if not ohlcv_ok:
        return PriceJumpAssessment(
            "structurally_impossible",
            "OHLC structure is invalid (cannot be a legitimate corporate action)",
        )
    if previous_close is None or current_close is None:
        return PriceJumpAssessment("normal", "insufficient data to assess movement")
    if not (math.isfinite(previous_close) and math.isfinite(current_close)):
        return PriceJumpAssessment("normal", "non-finite prices")
    if previous_close <= 0:
        return PriceJumpAssessment("normal", "previous close not positive")
    change = abs(current_close - previous_close) / previous_close * 100.0
    if change > threshold:
        return PriceJumpAssessment(
            "large_but_legitimate",
            f"{change:.1f}% day-over-day move — possible corporate action "
            "(dividend/rights/bonus/split); OHLC structurally sound, not quarantined",
            change,
        )
    return PriceJumpAssessment("normal", "", change)


# ═══════════════════════════════════════════════════════════════════
# Record reconciliation (Phase 6)
# ═══════════════════════════════════════════════════════════════════


def normalize_record(
    raw: Mapping[str, Any] | Any,
    *,
    provider: str = "",
) -> dict[str, float]:
    """Normalize a provider record (dict or object) to canonical fields.

    Only canonical OHLCV (+ optional) fields are extracted — raw
    provider payloads are never exposed downstream.
    """
    out: dict[str, float] = {}
    if raw is None:
        return out
    if isinstance(raw, Mapping):
        for key, value in raw.items():
            canon = _norm_key(key)
            if canon is None or value is None:
                continue
            try:
                num = float(value)
            except (TypeError, ValueError):
                continue
            if math.isfinite(num):
                out[canon] = num
        return out
    # Object shape (dataclass / StockQuote-like): reflect over known fields.
    for canon, aliases in _FIELD_ALIASES.items():
        for attr in aliases:
            if hasattr(raw, attr):
                try:
                    num = float(getattr(raw, attr))
                except (TypeError, ValueError):
                    continue
                if math.isfinite(num):
                    out[canon] = num
                break
    return out


def reconcile_records(
    records: Sequence[tuple[str, Mapping[str, Any] | Any]],
    *,
    symbol: str = "",
    record_date: Any = None,
    tolerances: Mapping[str, float] | None = None,
    material_thresholds: Mapping[str, float] | None = None,
    preferred_source: str | None = None,
    company_names: Mapping[str, Any] | None = None,
    as_of: str | None = None,
) -> ReconciliationResult:
    """Reconcile multiple providers' records for one symbol+date.

    Args:
        records: ``(provider_name, raw_record)`` pairs in provider
            priority order.  A ``None`` record is skipped (provider had
            nothing for this date) — that is ``UNAVAILABLE`` for that
            provider, not a disagreement.
        symbol: Canonical symbol being reconciled.
        record_date: Date of the record (ISO string or date).
        tolerances: Per-field relative tolerances (fractions).
        material_thresholds: Per-field material thresholds (fractions).
        preferred_source: Provider name used to resolve MINOR
            disagreements (default ``RECONCILE_PREFERRED_SOURCE``).
        company_names: Optional ``provider -> company`` map for the
            symbol-mapping check.
        as_of: ISO timestamp of the reconciliation.

    Policy (Phase 7):

    - AGREE            → validated data (first provider in order wins)
    - MINOR            → documented preferred source + warning
    - MATERIAL         → never silently trusted; caller quarantines
    - UNAVAILABLE      → nothing to compare
    - MAPPING_CONFLICT → never compared (checked first)
    """
    providers = [p for p, _ in records if p is not None]
    if not providers:
        return ReconciliationResult(
            status=UNAVAILABLE,
            symbol=symbol,
            date=_iso_date(record_date),
            as_of=as_of,
            warnings=["no provider returned a record"],
        )

    # Symbol mapping first (Phase 15): never compare different securities.
    ident_map = {
        p: (r.get("symbol") if isinstance(r, Mapping) else getattr(r, "symbol", None))
        for p, r in records
    }
    ok, reasons = check_symbol_mapping(symbol, ident_map, company_names)
    if not ok:
        return ReconciliationResult(
            status=MAPPING_CONFLICT,
            symbol=symbol,
            date=_iso_date(record_date),
            providers=providers,
            as_of=as_of,
            warnings=reasons,
        )

    # Normalize each non-None record.  A record that normalizes to *no*
    # usable canonical field (e.g. an unparseable payload) contributed
    # no data and is treated as unavailable for that provider — a
    # malformed payload must never masquerade as a competing record.
    normalized: list[tuple[str, dict[str, float]]] = []
    for p, r in records:
        if r is None:
            continue
        norm = normalize_record(r, provider=p)
        if norm:
            normalized.append((p, norm))
    available = [p for p, _ in normalized]
    if not available:
        return ReconciliationResult(
            status=UNAVAILABLE,
            symbol=symbol,
            date=_iso_date(record_date),
            providers=providers,
            as_of=as_of,
            warnings=["providers returned no usable records"],
        )

    # Single provider → AGREE by construction (nothing to disagree with).
    if len(available) == 1:
        name = available[0]
        return ReconciliationResult(
            status=AGREE,
            symbol=symbol,
            date=_iso_date(record_date),
            providers=available,
            selected_source=name,
            as_of=as_of,
        )

    # Field-level comparison across all available providers.
    disagreement_fields: list[str] = []
    difference_metrics: dict[str, float] = {}
    material_fields: list[str] = []
    for field_name in ALL_RECONCILED_FIELDS:
        values: dict[str, float] = {}
        for name, norm in normalized:
            if field_name in norm:
                values[name] = norm[field_name]
        if len(values) < 2:
            continue  # optional / missing on one side → not a conflict
        pairs = [
            relative_difference(va, vb)
            for va in values.values()
            for vb in values.values()
            if va is not None and vb is not None
        ]
        pairs = [p for p in pairs if p is not None]
        if not pairs:
            continue
        max_diff = max(pairs)
        difference_metrics[field_name] = round(max_diff, 6)
        if max_diff > _material_for(field_name, material_thresholds):
            material_fields.append(field_name)
            disagreement_fields.append(field_name)
        elif max_diff > _tolerance_for(field_name, tolerances):
            disagreement_fields.append(field_name)

    if material_fields:
        return ReconciliationResult(
            status=MATERIAL_DISAGREEMENT,
            symbol=symbol,
            date=_iso_date(record_date),
            providers=available,
            selected_source=None,
            disagreement_fields=disagreement_fields,
            difference_metrics=difference_metrics,
            warnings=[
                f"material disagreement on {', '.join(material_fields)} "
                f"(exceeds material threshold)"
            ],
            as_of=as_of,
        )

    if disagreement_fields:
        # MINOR: resolve to the documented preferred source (never
        # average).  Fall back to the first provider in priority order.
        preferred = preferred_source or RECONCILE_PREFERRED_SOURCE
        chosen = preferred if preferred in available else available[0]
        return ReconciliationResult(
            status=MINOR_DISAGREEMENT,
            symbol=symbol,
            date=_iso_date(record_date),
            providers=available,
            selected_source=chosen,
            disagreement_fields=disagreement_fields,
            difference_metrics=difference_metrics,
            warnings=[
                f"minor disagreement on {', '.join(disagreement_fields)}; "
                f"resolved to preferred source '{chosen}'"
            ],
            as_of=as_of,
        )

    return ReconciliationResult(
        status=AGREE,
        symbol=symbol,
        date=_iso_date(record_date),
        providers=available,
        selected_source=available[0],
        difference_metrics=difference_metrics,
        as_of=as_of,
    )


# Canonical (capitalized) OHLCV column names for merged output.
_CANONICAL_COLUMN = {
    "open": "Open",
    "high": "High",
    "low": "Low",
    "close": "Close",
    "volume": "Volume",
}


def _normalize_frame_dates(df: Any) -> Any:
    """Return a copy of *df* with a dtype-safe, midnight-normalized Date column."""
    import pandas as pd  # noqa: PLC0415 - lazy

    out = df.copy()
    out["Date"] = pd.to_datetime(out["Date"], errors="coerce").dt.normalize()
    return out.dropna(subset=["Date"])


def _iso_date(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    return str(value)[:10]


# ═══════════════════════════════════════════════════════════════════
# History-wide reconciliation (Phase 16/19)
# ═══════════════════════════════════════════════════════════════════


def reconcile_history(
    frames: Mapping[str, Any],
    *,
    symbol: str = "",
    tolerances: Mapping[str, float] | None = None,
    material_thresholds: Mapping[str, float] | None = None,
    preferred_source: str | None = None,
    as_of: str | None = None,
) -> tuple[Any, ReconciliationResult]:
    """Reconcile full history frames from multiple providers.

    Args:
        frames: ``provider_name -> DataFrame`` (or None for unavailable
            providers).  Frames must be OHLCV-shaped with a ``Date``
            column; they are compared per-date using the canonical
            record contract.

    Returns:
        ``(merged_frame, result)`` where *merged_frame* is the trusted
        merged history (rows from the selected source per date; a
        MATERIAL-disagreeing date is dropped and warned about — never
        averaged), and *result* is the history-wide reconciliation
        outcome (worst status observed).
    """
    import pandas as pd  # noqa: PLC0415 - lazy

    available = {p: f for p, f in frames.items() if f is not None and not getattr(f, "empty", True)}
    if not available:
        return (
            None,
            ReconciliationResult(
                status=UNAVAILABLE,
                symbol=symbol,
                providers=[p for p in frames if frames[p] is not None],
                as_of=as_of,
                warnings=["no provider returned a history frame"],
            ),
        )
    if len(available) == 1:
        name, df = next(iter(available.items()))
        return (
            df,
            ReconciliationResult(
                status=AGREE,
                symbol=symbol,
                providers=[name],
                selected_source=name,
                as_of=as_of,
            ),
        )

    # Normalize every frame's Date column once so the per-date lookup
    # below is dtype-safe (strings / time components would otherwise
    # silently match nothing).  Frames keep their canonical (capitalized)
    # OHLCV column names.  Re-filter afterwards: a frame whose dates are
    # all malformed (NaT after coercion) contributes zero sessions and
    # must be treated as unavailable, not silently ``AGREE``-empty.
    available = {
        p: df
        for p, df in (
            (name, _normalize_frame_dates(frame)) for name, frame in available.items()
        )
        if df is not None and not df.empty
    }
    if not available:
        return (
            None,
            ReconciliationResult(
                status=UNAVAILABLE,
                symbol=symbol,
                providers=list(frames),
                as_of=as_of,
                warnings=["no provider returned a usable history frame"],
            ),
        )

    # Union of dates across providers (sorted, normalized to midnight).
    dates = sorted(
        {
            pd.Timestamp(ts).normalize()
            for df in available.values()
            for ts in df["Date"].tolist()
        }
    )
    merged_rows: list[dict[str, Any]] = []
    worst: str = AGREE
    warnings: list[str] = []
    dropped_dates: list[str] = []

    for ts in dates:
        date_records: list[tuple[str, dict[str, Any]]] = []
        for name, df in available.items():
            row = df[df["Date"] == ts]
            if row.empty:
                continue
            # Raw row (original column names); ``reconcile_records``
            # normalizes via ``_norm_key`` (capitalized -> canonical).
            date_records.append((name, row.iloc[0].to_dict()))
        if not date_records:
            continue
        res = reconcile_records(
            date_records,
            symbol=symbol,
            record_date=ts,
            tolerances=tolerances,
            material_thresholds=material_thresholds,
            preferred_source=preferred_source,
            as_of=as_of,
        )
        if res.status in (MATERIAL_DISAGREEMENT, MAPPING_CONFLICT):
            dropped_dates.append(res.date)
            if res.status == MATERIAL_DISAGREEMENT:
                worst = MATERIAL_DISAGREEMENT
            elif worst != MATERIAL_DISAGREEMENT:
                worst = MAPPING_CONFLICT
            warnings.extend(res.warnings)
            continue
        if res.status == MINOR_DISAGREEMENT and worst != MATERIAL_DISAGREEMENT:
            worst = MINOR_DISAGREEMENT
            warnings.extend(res.warnings)
        # Row from the selected source (never an average).  Emit with
        # the canonical capitalized OHLCV columns the analyzer expects.
        src = res.selected_source or date_records[0][0]
        source_row = next(r for n, r in date_records if n == src)
        canonical = normalize_record(source_row)
        merged_rows.append(
            {
                "Date": ts,
                **{
                    _CANONICAL_COLUMN[f]: canonical[f]
                    for f in PRICE_FIELDS + VOLUME_FIELDS
                    if f in canonical
                },
            }
        )

    # Sprint 13.5 failure-injection hardening: when *every* date was
    # materially conflicting (all rows dropped), ``merged_rows`` is
    # empty — an empty DataFrame must be returned with canonical columns
    # (never a bare ``pd.DataFrame([])`` that has no ``Date`` column and
    # would crash callers sorting/indexing it).
    if merged_rows:
        merged = pd.DataFrame(merged_rows).sort_values("Date").reset_index(drop=True)
    else:
        merged = pd.DataFrame(columns=["Date", "Open", "High", "Low", "Close", "Volume"])
    if dropped_dates:
        warnings.append(f"dropped {len(dropped_dates)} materially-conflicting date(s)")
    # Deduplicate warnings (per-date MINOR warnings can repeat at scale).
    warnings = list(dict.fromkeys(warnings))
    return (
        merged,
        ReconciliationResult(
            status=worst,
            symbol=symbol,
            providers=list(available),
            selected_source=(
                preferred_source if preferred_source in available else next(iter(available))
            ),
            disagreement_fields=[],  # aggregated in per-date results
            warnings=warnings,
            as_of=as_of,
        ),
    )


# ═══════════════════════════════════════════════════════════════════
# Bounded reconciliation metrics (Phase 9)
# ═══════════════════════════════════════════════════════════════════


class ReconciliationMetricsCollector:
    """Thread-safe, bounded reconciliation counters.

    Scalar totals only — never per-symbol or per-date history — so the
    collector is bounded regardless of data volume.
    """

    _FIELDS = (
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
    )

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._counts: dict[str, int] = {f: 0 for f in self._FIELDS}

    def record_provider_request(self, count: int = 1) -> None:
        with self._lock:
            self._counts["provider_requests"] += count

    def record_provider_success(self, count: int = 1) -> None:
        with self._lock:
            self._counts["provider_successes"] += count

    def record_provider_failure(self, count: int = 1) -> None:
        with self._lock:
            self._counts["provider_failures"] += count

    def record_provider_timeout(self, count: int = 1) -> None:
        with self._lock:
            self._counts["provider_timeouts"] += count

    def record_fallback(self, count: int = 1) -> None:
        with self._lock:
            self._counts["fallback_count"] += count

    def record_reconciliation(self, result: ReconciliationResult) -> None:
        with self._lock:
            self._counts["reconciliation_checks"] += 1
            status = result.status
            if status == AGREE:
                self._counts["agreements"] += 1
            elif status == MINOR_DISAGREEMENT:
                self._counts["minor_disagreements"] += 1
            elif status == MATERIAL_DISAGREEMENT:
                self._counts["material_disagreements"] += 1
                self._counts["quarantined_conflicts"] += 1
            elif status == MAPPING_CONFLICT:
                self._counts["mapping_conflicts"] += 1

    def snapshot(self) -> dict[str, int]:
        with self._lock:
            return dict(self._counts)

    def reset(self) -> None:
        with self._lock:
            self._counts = {f: 0 for f in self._FIELDS}


# Shared singleton so providers / DataService / API report into one
# bounded collector.
reconciliation_metrics = ReconciliationMetricsCollector()
