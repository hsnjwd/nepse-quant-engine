"""Explicit data provenance / trust state (Sprint 13.5).

Downstream consumers need to know, for any dataset, whether it is:

- **trusted** — validated, calendar-valid, no conflicts
- **reconciled** — produced by the cross-provider reconciliation layer
- **single-provider** — only one provider supplied data (AGREE by construction)
- **fallback** — a provider failed and a lower-priority source served it
- **partially_reconciled** — reconciled but some dates were dropped
- **conflicted** — material disagreement / mapping conflict (never trusted)
- **unavailable** — no provider supplied data
- **stale** — data older than the freshness threshold
- **calendar_invalid** — records violate the governed trading calendar
- **quarantined** — invalid OHLC / conflicting duplicates (quality INVALID)

The trust state is **compact metadata attached to the existing result**
(``StockHistory.provenance`` / the analysis ``provenance`` block) — never
a duplicated dataset.  It survives the full pipeline:

    Provider → DataService → Reconciliation → Quality → Analyzer → Signal

Safety rule: a signal must never appear fully trusted when its underlying
data is explicitly conflicted, unavailable, calendar-invalid or
quarantined — ``is_safe()`` encodes that gate and the analyzer enforces it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Mapping


# ═══════════════════════════════════════════════════════════════════
# Trust states
# ═══════════════════════════════════════════════════════════════════

TRUST_TRUSTED = "trusted"
TRUST_RECONCILED = "reconciled"
TRUST_SINGLE_PROVIDER = "single_provider"
TRUST_FALLBACK = "fallback"
TRUST_PARTIALLY_RECONCILED = "partially_reconciled"
TRUST_CONFLICTED = "conflicted"
TRUST_UNAVAILABLE = "unavailable"
TRUST_STALE = "stale"
TRUST_CALENDAR_INVALID = "calendar_invalid"
TRUST_QUARANTINED = "quarantined"

ALL_TRUST_STATES = (
    TRUST_TRUSTED,
    TRUST_RECONCILED,
    TRUST_SINGLE_PROVIDER,
    TRUST_FALLBACK,
    TRUST_PARTIALLY_RECONCILED,
    TRUST_CONFLICTED,
    TRUST_UNAVAILABLE,
    TRUST_STALE,
    TRUST_CALENDAR_INVALID,
    TRUST_QUARANTINED,
)

# Trust states whose data must NEVER produce a normal BUY/SELL signal.
# The analyzer forces such signals to HOLD (``signal_suppressed=True``).
UNSAFE_TRUST_STATES = frozenset(
    {
        TRUST_CONFLICTED,
        TRUST_UNAVAILABLE,
        TRUST_CALENDAR_INVALID,
        TRUST_QUARANTINED,
    }
)

# Trust states that are safe but *not* fully trusted (documented, not
# suppressed).  Used by observability to distinguish quality levels.
LIMITED_TRUST_STATES = frozenset(
    {TRUST_FALLBACK, TRUST_PARTIALLY_RECONCILED, TRUST_STALE}
)


@dataclass
class DataProvenance:
    """Compact provenance / trust state attached to a dataset or result.

    Scalar fields only — never raw provider payloads, never per-record
    history — so provenance stays bounded and JSON-serialisable.
    """

    sources: list[str] = field(default_factory=list)
    trust: str = TRUST_UNAVAILABLE
    reconciliation_status: str | None = None
    fallback_used: bool = False
    calendar_version: str | None = None
    calendar_valid: bool | None = None
    quality_status: str | None = None
    freshness: str | None = None
    quarantined_records: int = 0
    as_of: str | None = None

    # ── Queries ───────────────────────────────────────────────────

    @property
    def is_safe(self) -> bool:
        """True when this dataset may drive a normal trading signal."""
        return self.trust not in UNSAFE_TRUST_STATES

    @property
    def is_fully_trusted(self) -> bool:
        return self.trust in (TRUST_TRUSTED, TRUST_RECONCILED, TRUST_SINGLE_PROVIDER)

    # ── Serialization ─────────────────────────────────────────────

    def to_dict(self) -> dict[str, Any]:
        return {
            "sources": list(self.sources),
            "trust": self.trust,
            "reconciliation_status": self.reconciliation_status,
            "fallback_used": self.fallback_used,
            "calendar_version": self.calendar_version,
            "calendar_valid": self.calendar_valid,
            "quality_status": self.quality_status,
            "freshness": self.freshness,
            "quarantined_records": self.quarantined_records,
            "as_of": self.as_of,
            "is_safe": self.is_safe,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "DataProvenance":
        return cls(
            sources=list(data.get("sources") or ()),
            trust=str(data.get("trust") or TRUST_UNAVAILABLE),
            reconciliation_status=data.get("reconciliation_status"),
            fallback_used=bool(data.get("fallback_used")),
            calendar_version=data.get("calendar_version"),
            calendar_valid=data.get("calendar_valid"),
            quality_status=data.get("quality_status"),
            freshness=data.get("freshness"),
            quarantined_records=int(data.get("quarantined_records") or 0),
            as_of=data.get("as_of"),
        )


# ═══════════════════════════════════════════════════════════════════
# Trust resolver
# ═══════════════════════════════════════════════════════════════════


def resolve_trust(
    *,
    quality_status: str | None = None,
    freshness: str | None = None,
    reconciliation_status: str | None = None,
    fallback_used: bool = False,
    calendar_valid: bool | None = None,
    quarantined_records: int = 0,
    dropped_dates: int = 0,
    source_count: int = 0,
) -> str:
    """Resolve the trust state from pipeline signals (worst state wins).

    Priority (most severe first):

    - quarantined        — quality INVALID (broken OHLC / conflicts)
    - calendar_invalid   — records violate the governed trading calendar
    - conflicted         — MATERIAL_DISAGREEMENT / MAPPING_CONFLICT
    - unavailable        — UNAVAILABLE or zero usable sources
    - stale              — STALE freshness (flagged, not suppressed)
    - fallback           — provider failure, lower-priority source used
    - partially_reconciled — reconciled with dropped dates
    - reconciled         — AGREE / MINOR from multiple providers
    - single_provider    — exactly one source
    - trusted            — everything else
    """
    if quarantined_records > 0 or quality_status == "INVALID":
        return TRUST_QUARANTINED
    if calendar_valid is False:
        return TRUST_CALENDAR_INVALID
    if reconciliation_status in ("MATERIAL_DISAGREEMENT", "MAPPING_CONFLICT"):
        return TRUST_CONFLICTED
    if reconciliation_status == "UNAVAILABLE" or source_count == 0:
        return TRUST_UNAVAILABLE
    if freshness == "STALE":
        return TRUST_STALE
    if fallback_used:
        return TRUST_FALLBACK
    if reconciliation_status in ("AGREE", "MINOR_DISAGREEMENT"):
        if dropped_dates > 0:
            return TRUST_PARTIALLY_RECONCILED
        return TRUST_RECONCILED
    if source_count == 1:
        return TRUST_SINGLE_PROVIDER
    return TRUST_TRUSTED


def _iso_now() -> str:
    return datetime.now().isoformat(timespec="seconds")


# ═══════════════════════════════════════════════════════════════════
# Builders for each pipeline stage
# ═══════════════════════════════════════════════════════════════════


def provenance_from_history(
    *,
    sources: list[str],
    fallback_used: bool = False,
    reconciliation_status: str | None = None,
    dropped_dates: int = 0,
    calendar: Any = None,
    quality: Any = None,
    as_of: str | None = None,
) -> DataProvenance:
    """Build provenance for a history frame flowing through the service.

    Combines the reconciliation outcome, an optional governed calendar
    (``NepseCalendar``) and an optional quality report
    (``DataQualityReport``) into one compact trust state.
    """
    calendar_version: str | None = None
    calendar_valid: bool | None = None
    if calendar is not None:
        calendar_version = getattr(calendar, "version", None)
        # Sprint 13.5: a calendar that flags records on non-trading days
        # marks the dataset calendar-invalid — the safety gate must see
        # it even though ``resolve_trust`` ranks quarantine (INVALID)
        # above calendar-invalid.  The flag is honest metadata; the
        # trust resolver still resolves INVALID-quality data to
        # quarantined (also unsafe), so the signal stays suppressed.
        calendar_valid = not (
            quality is not None
            and int(getattr(quality, "calendar_invalid_records", 0) or 0) > 0
        )
    quality_status = None
    freshness = None
    quarantined = 0
    if quality is not None:
        quality_status = getattr(quality, "status", None)
        freshness = getattr(quality, "freshness", None)
        quarantined = int(getattr(quality, "records_invalid", 0) or 0)

    trust = resolve_trust(
        quality_status=quality_status,
        freshness=freshness,
        reconciliation_status=reconciliation_status,
        fallback_used=fallback_used,
        calendar_valid=calendar_valid,
        quarantined_records=quarantined,
        dropped_dates=dropped_dates,
        source_count=len(sources),
    )
    return DataProvenance(
        sources=sources,
        trust=trust,
        reconciliation_status=reconciliation_status,
        fallback_used=fallback_used,
        calendar_version=calendar_version,
        calendar_valid=calendar_valid,
        quality_status=quality_status,
        freshness=freshness,
        quarantined_records=quarantined,
        as_of=as_of or _iso_now(),
    )


def provenance_from_reconciliation(
    result: Any,
    *,
    calendar: Any = None,
    quality: Any = None,
    fallback_used: bool = False,
    as_of: str | None = None,
) -> DataProvenance:
    """Build provenance from a ``ReconciliationResult`` (+ optional extras)."""
    status = getattr(result, "status", None)
    sources = list(getattr(result, "providers", None) or [])
    dropped = (
        len(getattr(result, "dropped_dates", None) or ())
        if hasattr(result, "dropped_dates")
        else 0
    )
    return provenance_from_history(
        sources=sources,
        fallback_used=fallback_used,
        reconciliation_status=status,
        dropped_dates=dropped,
        calendar=calendar,
        quality=quality,
        as_of=as_of or getattr(result, "as_of", None),
    )
