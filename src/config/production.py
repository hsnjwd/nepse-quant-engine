"""Production configuration overrides for the NEPSE Quant Engine.

Import and apply these settings at application startup when running
in a production environment.  These values override the defaults in
``src/config.py``.

Usage::

    # At the top of app.py or the API entry point:
    from src.config.production import apply_production_config
    apply_production_config()
"""

from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)


# ── Production overrides ─────────────────────────────────────────
_PRODUCTION_SETTINGS: dict[str, str] = {
    # Cache — longer TTLs to reduce API pressure
    "CACHE_MEMORY_TTL": "120",
    "CACHE_DISK_TTL": "1800",
    "CACHE_REFRESH_INTERVAL": "120",
    # Rate limiter — conservative limits for shared deployments
    "RATE_LIMIT": "5",
    "API_TIMEOUT": "30",
    "RETRY_COUNT": "3",
    "BACKOFF_BASE": "2.0",
    # WebSocket — disabled by default in production
    "WEBSOCKET_ENABLED": "false",
    # Performance monitoring — enabled for observability
    "ENABLE_PERFORMANCE_MONITORING": "true",
    "HEALTH_CHECK_INTERVAL": "120",
}


def apply_production_config() -> None:
    """Apply production overrides to environment variables.

    Only sets values that are NOT already set in the environment,
    so explicit ``.env`` settings take precedence.
    """
    applied = 0
    for key, value in _PRODUCTION_SETTINGS.items():
        if key not in os.environ:
            os.environ[key] = value
            applied += 1
    logger.info(
        "[ProductionConfig] Applied %d production defaults (%d already set)",
        applied,
        len(_PRODUCTION_SETTINGS) - applied,
    )
