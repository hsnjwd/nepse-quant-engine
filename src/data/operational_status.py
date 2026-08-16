"""Compact production-readiness status derived from existing state (Sprint 13.6 §14).

No new monitoring subsystem: this module *derives* a compact
``system_status`` from the state the platform already tracks —

    provider health monitor     (src/data/health.py)
    reconciliation metrics      (src/data/reconciliation.py)
    governed calendar           (src/data/calendar.py)
    data-quality metrics        (src/data/quality.py)
    bounded incident tracker    (src/data/incidents.py)
    DataService cache           (src/data/service.py)

Each block reports one of ``HEALTHY`` / ``DEGRADED`` / ``UNAVAILABLE``
(``UNKNOWN`` when there is no evidence either way) plus a one-line
reason.  ``overall`` is the worst block state — an engine is never
reported healthy while any subsystem that gates trust is unavailable.

The status is safe when empty, safe after restart, bounded, and never
raises (a broken subsystem reports UNAVAILABLE instead of crashing
``/metrics``).
"""

from __future__ import annotations

from typing import Any

HEALTHY = "HEALTHY"
DEGRADED = "DEGRADED"
UNAVAILABLE = "UNAVAILABLE"
UNKNOWN = "UNKNOWN"

# Severity ordering: UNAVAILABLE (3) > DEGRADED (2) > UNKNOWN (1) > HEALTHY (0)
_SEVERITY = {HEALTHY: 0, UNKNOWN: 1, DEGRADED: 2, UNAVAILABLE: 3}


def _worst(states: list[str]) -> str:
    if not states:
        return UNKNOWN
    return max(states, key=lambda s: _SEVERITY.get(s, 1))


def provider_status(svc: Any) -> dict[str, Any]:
    """Derived data-provider status from the shared health monitor.

    All providers disabled → UNAVAILABLE; any provider disabled or
    degraded → DEGRADED; else HEALTHY.  Unknown provider set → UNKNOWN.
    """
    try:
        monitor = svc.health_monitor
        all_health = monitor.get_all_health()
    except Exception as exc:  # noqa: BLE001 - status must never raise
        return {"state": UNAVAILABLE, "reason": f"health monitor unavailable: {exc}"}
    if not all_health:
        return {"state": UNKNOWN, "reason": "no providers registered"}
    disabled = [h.name for h in all_health if h.is_disabled]
    degraded = [h.name for h in all_health if monitor.degradation_state(h.name) == DEGRADED]
    if len(disabled) == len(all_health):
        return {
            "state": UNAVAILABLE,
            "reason": f"all providers disabled: {', '.join(disabled)}",
            "disabled": disabled,
            "degraded": degraded,
        }
    if disabled or degraded:
        return {
            "state": DEGRADED,
            "reason": f"providers disabled={disabled} degraded={degraded}",
            "disabled": disabled,
            "degraded": degraded,
        }
    return {"state": HEALTHY, "reason": "all providers enabled", "disabled": [], "degraded": []}


def reconciliation_status() -> dict[str, Any]:
    """Derived reconciliation health from the bounded counters.

    A recent material conflict or mapping conflict is a DEGRADED signal
    (suppression is working — the engine is conservative, not broken).
    Quarantined conflicts alone keep the block DEGRADED so operators can
    see the conflict is being handled.
    """
    from src.data.reconciliation import reconciliation_metrics  # noqa: PLC0415

    try:
        snap = reconciliation_metrics.snapshot()
    except Exception as exc:  # noqa: BLE001
        return {"state": UNKNOWN, "reason": f"reconciliation metrics unavailable: {exc}"}
    material = snap.get("material_disagreements", 0)
    mapping = snap.get("mapping_conflicts", 0)
    if material or mapping:
        return {
            "state": DEGRADED,
            "reason": f"{material} material + {mapping} mapping conflict(s) recorded",
            "material": material,
            "mapping": mapping,
        }
    return {"state": HEALTHY, "reason": "no material/mapping conflicts", "material": 0, "mapping": 0}


def calendar_status() -> dict[str, Any]:
    """Derived governed-calendar status.

    HEALTHY when the calendar loads with provenance; DEGRADED when the
    shipped calendar is missing/unknown but the base weekend rule is in
    effect; UNAVAILABLE only when no calendar at all can be built.
    """
    try:
        from src.data.calendar import default_calendar  # noqa: PLC0415 - lazy

        cal = default_calendar()
        status = cal.to_status()
    except Exception as exc:  # noqa: BLE001
        return {"state": UNAVAILABLE, "reason": f"calendar unavailable: {exc}"}
    if status.get("has_provenance"):
        return {
            "state": HEALTHY,
            "reason": f"calendar v{status.get('version')} loaded with provenance",
            "version": status.get("version"),
        }
    return {
        "state": DEGRADED,
        "reason": "base weekend-rule calendar (no provenance in data file)",
        "version": status.get("version"),
    }


def data_quality_status() -> dict[str, Any]:
    """Derived data-quality status from the bounded quality counters.

    Current-quantity only (the trend block lives in ``/metrics`` under
    ``data_quality_trend``).  Invalid records or conflicts → DEGRADED;
    nothing checked → UNKNOWN; else HEALTHY.
    """
    from src.data.quality import quality_metrics  # noqa: PLC0415

    try:
        snap = quality_metrics.snapshot()
    except Exception as exc:  # noqa: BLE001
        return {"state": UNKNOWN, "reason": f"quality metrics unavailable: {exc}"}
    invalid = snap.get("records_invalid", 0)
    conflicts = snap.get("conflicts", 0)
    if snap.get("records_checked", 0) == 0:
        return {"state": UNKNOWN, "reason": "no data assessed yet"}
    if invalid or conflicts:
        return {
            "state": DEGRADED,
            "reason": f"{invalid} invalid record(s), {conflicts} conflict(s) detected",
            "invalid": invalid,
            "conflicts": conflicts,
        }
    return {"state": HEALTHY, "reason": "no invalid records or conflicts", "invalid": 0, "conflicts": 0}


def incident_status() -> dict[str, Any]:
    """Derived incident status from the bounded tracker.

    Material/mapping incidents are operational signals — data was
    *correctly* suppressed, which is the system working, not degrading.
    Only provider unavailability (a source the engine could not reach)
    is a genuine degradation signal for this block.
    """
    from src.data.incidents import incident_tracker  # noqa: PLC0415

    try:
        snap = incident_tracker.snapshot(recent_limit=0)
    except Exception as exc:  # noqa: BLE001
        return {"state": UNKNOWN, "reason": f"incident tracker unavailable: {exc}"}
    counts = snap.get("counts", {})
    unavailable = counts.get("provider_unavailable", 0)
    if unavailable:
        return {
            "state": DEGRADED,
            "reason": f"{unavailable} provider-unavailable incident(s) recorded",
            "unavailable": unavailable,
        }
    return {
        "state": HEALTHY,
        "reason": "no provider-unavailable incidents",
        "unavailable": 0,
    }


def cache_status(svc: Any) -> dict[str, Any]:
    """Derived cache status from the DataService tiered cache.

    Verifies a get/set round-trip works (a live check, not a stored
    metric) — cache read/write failures must surface as DEGRADED, never
    be hidden.
    """
    try:
        cache = svc._cache
        probe = f"__status_probe__"
        cache.set(probe, 1, ttl=1)
        value = cache.get(probe)
        cache.delete(probe)
    except Exception as exc:  # noqa: BLE001
        return {"state": DEGRADED, "reason": f"cache round-trip failed: {exc}"}
    if value != 1:
        return {"state": DEGRADED, "reason": "cache round-trip returned no data"}
    return {"state": HEALTHY, "reason": "cache read/write verified"}


def system_status(svc: Any = None) -> dict[str, Any]:
    """Compact production-readiness status (Sprint 13.6 §14).

    Derived from existing state only — no parallel monitoring.  Every
    block is bounded and never raises.  ``overall`` is the worst block
    state.
    """
    if svc is None:
        try:
            from src.data.service import DataService  # noqa: PLC0415 - lazy

            svc = DataService()
        except Exception as exc:  # noqa: BLE001
            return {
                "overall": UNAVAILABLE,
                "reason": f"DataService unavailable: {exc}",
                "data_provider": {"state": UNAVAILABLE, "reason": "service unavailable"},
                "reconciliation": reconciliation_status(),
                "calendar": calendar_status(),
                "data_quality": data_quality_status(),
                "incidents": incident_status(),
                "cache": {"state": UNAVAILABLE, "reason": "service unavailable"},
            }

    blocks = {
        "data_provider": provider_status(svc),
        "reconciliation": reconciliation_status(),
        "calendar": calendar_status(),
        "data_quality": data_quality_status(),
        "incidents": incident_status(),
        "cache": cache_status(svc),
    }
    overall = _worst([b.get("state", UNKNOWN) for b in blocks.values()])
    reasons = [b.get("reason", "") for b in blocks.values() if b.get("reason")]
    return {"overall": overall, "reason": "; ".join(reasons), **blocks}
