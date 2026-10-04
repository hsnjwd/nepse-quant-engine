"""Bounded reconciliation / provider incident tracking (Sprint 13.6).

The engine must be able to explain *why* a production signal was trusted
or suppressed.  Beyond the scalar counters in ``reconciliation_metrics``
(how many), operators need compact event records (what happened, when,
to which symbol/provider, and what trust consequence followed).

This module adds exactly that — a **bounded** incident tracker:

- a ring buffer of the most recent incidents (``max_events``, default 200)
- aggregate per-kind counters (also bounded)
- a per-symbol "repeated minor disagreement" counter with a hard cap

Deliberate constraints (Sprint 13.6 §7):

- Incident records store **compact metadata only** — never raw provider
  payloads, never per-record market data.  No duplication of the
  dataset itself.
- The tracker is bounded by construction: the event deque is capped,
  the counters are scalars, and the repeated-minor map is capped.
- Safe when empty; safe after restart (in-memory; metrics re-derive).
- Recording is thread-safe and cheap (deque append + counter bump) so
  the normal single-provider path is unaffected.

Kinds (stable strings, exposed via ``/metrics.incidents``):

    material_disagreement    — MATERIAL record quarantine
    mapping_conflict         — providers disagree about the security
    repeated_minor_disagreement — same symbol keeps disagreeing (MINOR)
    provider_timeout         — a provider timed out
    provider_unavailable     — no provider could serve a request
    fallback                 — a lower-priority provider served data
    identity_mismatch        — symbol-mapping check failed
    malformed_response       — payload rejected by the data validator
"""

from __future__ import annotations

import logging
import threading
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

logger = logging.getLogger(__name__)

INCIDENT_MATERIAL_DISAGREEMENT = "material_disagreement"
INCIDENT_MAPPING_CONFLICT = "mapping_conflict"
INCIDENT_REPEATED_MINOR = "repeated_minor_disagreement"
INCIDENT_PROVIDER_TIMEOUT = "provider_timeout"
INCIDENT_PROVIDER_UNAVAILABLE = "provider_unavailable"
INCIDENT_FALLBACK = "fallback"
INCIDENT_IDENTITY_MISMATCH = "identity_mismatch"
INCIDENT_MALFORMED = "malformed_response"

ALL_INCIDENT_KINDS = (
    INCIDENT_MATERIAL_DISAGREEMENT,
    INCIDENT_MAPPING_CONFLICT,
    INCIDENT_REPEATED_MINOR,
    INCIDENT_PROVIDER_TIMEOUT,
    INCIDENT_PROVIDER_UNAVAILABLE,
    INCIDENT_FALLBACK,
    INCIDENT_IDENTITY_MISMATCH,
    INCIDENT_MALFORMED,
)

# A symbol is reported as "repeatedly disagreeing" after this many MINOR
# reconciliation outcomes for that symbol.  Kept small so a genuinely
# noisy symbol surfaces without flooding the tracker.
REPEATED_MINOR_THRESHOLD = 3

# Hard cap on distinct symbols tracked for repeated-minor detection.
# On overflow the oldest symbol's counter is dropped (bounded by design).
REPEATED_MINOR_MAX_SYMBOLS = 500


@dataclass
class Incident:
    """One compact operational event record."""

    kind: str
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat(timespec="seconds"))
    providers: list[str] = field(default_factory=list)
    symbol: str = ""
    trust_consequence: str = ""
    fallback_used: bool = False
    quarantined: bool = False
    detail: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "timestamp": self.timestamp,
            "providers": list(self.providers),
            "symbol": self.symbol,
            "trust_consequence": self.trust_consequence,
            "fallback_used": self.fallback_used,
            "quarantined": self.quarantined,
            "detail": self.detail,
        }


class IncidentTracker:
    """Thread-safe, bounded tracker of reconciliation/provider incidents."""

    def __init__(self, max_events: int = 200) -> None:
        self._max_events = max(1, int(max_events))
        self._lock = threading.RLock()
        self._events: deque[Incident] = deque(maxlen=self._max_events)
        self._counts: dict[str, int] = {k: 0 for k in ALL_INCIDENT_KINDS}
        self._minor_counts: dict[str, int] = {}

    # ── Recording ────────────────────────────────────────────────

    def record(
        self,
        kind: str,
        *,
        providers: list[str] | tuple[str, ...] | None = None,
        symbol: str = "",
        trust_consequence: str = "",
        fallback_used: bool = False,
        quarantined: bool = False,
        detail: str = "",
    ) -> Incident:
        """Record one incident (compact metadata; bounded by construction)."""
        if kind not in self._counts:
            # An unknown/typo'd kind must not inflate malformed counts
            # invisibly — surface it so callers can fix the call site.
            logger.warning("IncidentTracker: unknown incident kind %r mapped to %s", kind, INCIDENT_MALFORMED)
            kind = INCIDENT_MALFORMED  # unknown kinds are never trusted
        evt = Incident(
            kind=kind,
            providers=list(providers or ()),
            symbol=symbol,
            trust_consequence=trust_consequence,
            fallback_used=fallback_used,
            quarantined=quarantined,
            detail=detail,
        )
        with self._lock:
            self._events.append(evt)
            self._counts[kind] += 1
        return evt

    def _record_minor_signal(self, symbol: str, kind: str) -> None:
        """Track repeated MINOR outcomes per symbol (bounded map).

        ``symbol == ""`` (aggregate reconciliations without a symbol)
        never triggers the repeated-minor incident — only concrete
        symbols can be "repeatedly disagreeing".
        """
        if not symbol:
            return
        count = self._minor_counts.get(symbol, 0) + 1
        self._minor_counts[symbol] = count
        if len(self._minor_counts) > REPEATED_MINOR_MAX_SYMBOLS:
            # Drop the oldest tracked symbol (dicts preserve insertion
            # order) so the map stays bounded.
            self._minor_counts.pop(next(iter(self._minor_counts)))
        if count == REPEATED_MINOR_THRESHOLD:
            self._events.append(
                Incident(
                    kind=INCIDENT_REPEATED_MINOR,
                    providers=[],
                    symbol=symbol,
                    trust_consequence="reconciled (preferred source retained)",
                    detail=(
                        f"symbol {symbol} reached {REPEATED_MINOR_THRESHOLD} "
                        "minor disagreements; no average was ever applied"
                    ),
                )
            )
            self._counts[INCIDENT_REPEATED_MINOR] += 1

    def record_minor(self, symbol: str, *, providers: list[str] | None = None) -> None:
        """Record one MINOR reconciliation outcome for *symbol*.

        The first ``REPEATED_MINOR_THRESHOLD - 1`` occurrences are
        normal documented MINOR outcomes (already visible through
        ``reconciliation_metrics.minor_disagreements``); the threshold
        crossing emits the ``repeated_minor_disagreement`` incident.
        """
        if not symbol:
            return
        with self._lock:
            self._record_minor_signal(symbol, INCIDENT_REPEATED_MINOR)

    # ── Queries (bounded) ────────────────────────────────────────

    def snapshot(self, recent_limit: int = 20) -> dict[str, Any]:
        """Bounded snapshot for ``/metrics.incidents``.

        ``recent`` is capped at ``recent_limit`` entries (default 20), so
        the payload stays bounded even with ``max_events`` incidents
        recorded.  ``recent_limit <= 0`` returns no recent events (only
        scalar counts) — note that ``list(...)[-0:]`` would return the
        *entire* deque, so the slice is guarded explicitly.  All counts
        are scalars.  Never exposes raw payloads.
        """
        with self._lock:
            limit = min(max(0, int(recent_limit)), len(self._events))
            recent = [e.to_dict() for e in list(self._events)[-limit:]] if limit > 0 else []
            return {
                "max_events": self._max_events,
                "recent_limit": limit,
                "total": sum(self._counts.values()),
                "counts": dict(sorted(self._counts.items())),
                "recent": recent,
            }

    def recent(self, limit: int = 10) -> list[Incident]:
        with self._lock:
            return list(self._events)[-limit:]

    def count(self, kind: str) -> int:
        with self._lock:
            return self._counts.get(kind, 0)

    def reset(self) -> None:
        """Clear all incidents and counters (test / operator use)."""
        with self._lock:
            self._events.clear()
            self._counts = {k: 0 for k in ALL_INCIDENT_KINDS}
            self._minor_counts.clear()


# Shared singleton so providers / DataService / API report into one
# bounded tracker.
incident_tracker = IncidentTracker()
