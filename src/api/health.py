"""Health check API endpoints (Sprint 13.9 §8–9).

Three distinct probes:

* ``GET /``            — legacy status message (unchanged, back-compat).
* ``GET /health/live`` — liveness: the process is up.  Always 200 when
  the worker is serving; a temporary external provider outage must
  never flip liveness.
* ``GET /health/ready`` — readiness: the process can *safely* serve
  production requests.  200 only when critical initialization is sound:
  configuration validates, the governed calendar loads, and the cache
  round-trips.  Corrupted state, an invalid calendar, or broken
  critical configuration returns 503 — the engine never reports READY
  before those checks pass (Sprint 13.9 §8: "Do not start reporting the
  application as healthy before critical initialization succeeds").

Provider health is deliberately *informational* in the readiness
payload (``system`` block) and never gates readiness: an external data
source outage is a DEGRADED state the CSV fallback covers, not a
not-ready state (Sprint 13.9 §9).
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Response, status

from src.logging.logger import logger

router = APIRouter(tags=["Health"])


@router.get("/", status_code=status.HTTP_200_OK)
def home() -> dict[str, str]:
    """Retrieve system health status (legacy probe)."""
    logger.debug("Health check ping requested.")
    return {
        "status": "NEPSE Quant Engine Running",
    }


@router.get("/health/live", status_code=status.HTTP_200_OK)
def liveness() -> dict[str, str]:
    """Liveness probe: the worker process is alive and serving."""
    return {"status": "live"}


@router.get("/health/ready", status_code=status.HTTP_200_OK)
def readiness(response: Response) -> dict[str, Any]:
    """Readiness probe: critical initialization is sound.

    Returns 200 only when configuration validates, the governed
    calendar is loadable, and the cache round-trips.  Otherwise 503 with
    a per-block breakdown (states only — never values, never secrets).
    """
    from src.config.validation import validate_config  # noqa: PLC0415 - lazy
    from src.data.operational_status import (  # noqa: PLC0415 - lazy
        HEALTHY,
        UNAVAILABLE,
        cache_status,
        calendar_status,
        system_status,
    )
    from src.data.service import DataService  # noqa: PLC0415 - lazy

    config_problems = validate_config()

    try:
        svc = DataService()
        svc_unavailable = None
    except Exception as exc:  # noqa: BLE001 - readiness must never raise
        svc = None
        svc_unavailable = str(exc)

    cal = calendar_status()
    if svc is not None:
        cache = cache_status(svc)
    else:
        cache = {"state": UNAVAILABLE, "reason": "DataService unavailable"}

    if svc is not None:
        system = system_status(svc)
    else:
        system = {"overall": UNAVAILABLE, "reason": svc_unavailable}

    checks: dict[str, Any] = {
        "configuration": "OK" if not config_problems else "INVALID",
        "calendar": cal.get("state"),
        "cache": cache.get("state"),
        "system": system.get("overall"),
    }
    if config_problems:
        checks["configuration_problems"] = [key for key, _ in config_problems]

    ready = (
        not config_problems
        and cal.get("state") != UNAVAILABLE
        and cache.get("state") == HEALTHY
    )

    payload: dict[str, Any] = {"status": "ready" if ready else "not_ready", "checks": checks}
    if not ready:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return payload
