"""Configuration validation for the NEPSE Quant Engine (Sprint 13.9 §6).

Semantic validation layered on the *existing* ``src.config`` defaults —
no new configuration subsystem, no new state stores.  Two failure
layers work together:

1. **Import-time coercion** (unchanged): a non-numeric ``RATE_LIMIT``
   already fails fast with ``ValueError`` at import, so a bad value can
   never silently fall back to a default and change behaviour.
2. **Semantic validation** (this module): ``validate_config()`` checks
   the *parsed* values for nonsensical ranges (zero/negative timeouts,
   inverted MACD periods, out-of-bounds percentages, malformed URLs,
   garbage boolean strings) and returns ``(key, problem)`` pairs.

Contract:

* ``validate_config()`` never raises — it returns problems.
* Problem messages never include the configured *value* (a value could
  be a secret; config errors are surfaced through ``/health/ready`` and
  logs).
* Boolean env vars accept exactly ``true/1/yes`` and ``false/0/no``
  (case-insensitive); anything else is reported instead of silently
  becoming ``False`` and flipping production behaviour.
* ``SECOND_PROVIDER_URL`` is an operator opt-in: when non-empty it must
  be a valid ``http(s)`` URL, and ``SECOND_PROVIDER_NAME`` must be set.
"""

from __future__ import annotations

import os
from urllib.parse import urlparse

from src.config import (
    API_TIMEOUT,
    BACKOFF_BASE,
    CACHE_DISK_TTL,
    CACHE_MEMORY_TTL,
    CACHE_REFRESH_INTERVAL,
    DATA_MAX_GAP_DAYS,
    DATA_STALE_AFTER_DAYS,
    HEALTH_CHECK_INTERVAL,
    MACD_FAST,
    MACD_SIGNAL,
    MACD_SLOW,
    RATE_LIMIT,
    RECONCILE_CORPORATE_ACTION_MOVE_PCT,
    RECONCILE_MATERIAL_PRICE_PCT,
    RECONCILE_MATERIAL_VOLUME_PCT,
    RECONCILE_PRICE_TOLERANCE_PCT,
    RECONCILE_VOLUME_TOLERANCE_PCT,
    RETRY_COUNT,
    RSI_PERIOD,
    SCANNER_CACHE_MAX_ENTRIES,
    SCANNER_CACHE_TTL,
    SCANNER_WORKERS,
    SECOND_PROVIDER_NAME,
    SECOND_PROVIDER_URL,
    WEBSOCKET_URL,
    WORKER_METRICS_TTL_S,
)

_BOOL_TRUE = frozenset({"true", "1", "yes"})
_BOOL_FALSE = frozenset({"false", "0", "no"})
_BOOL_KEYS = (
    "WEBSOCKET_ENABLED",
    "ENABLE_PERFORMANCE_MONITORING",
    "ENFORCE_SIGNAL_FRESHNESS",
    "INDICATOR_CACHE_ENABLED",
    "ENABLE_ANALYZE_ALERT_BATCH",
)
_URL_SCHEMES = frozenset({"ws", "wss", "http", "https"})


def _check_positive(
    problems: list[tuple[str, str]],
    key: str,
    value: int | float,
    *,
    minimum: float = 1,
) -> None:
    if value < minimum:
        problems.append((key, f"must be >= {minimum:g}"))


def _check_pct(problems: list[tuple[str, str]], key: str, value: float) -> None:
    if not 0 < value <= 100:
        problems.append((key, "must be in (0, 100]"))


def _check_url(problems: list[tuple[str, str]], key: str, value: str) -> None:
    if not value:
        return
    parsed = urlparse(value)
    if parsed.scheme not in _URL_SCHEMES or not parsed.netloc:
        problems.append((key, "must be a URL with scheme ws/wss/http/https"))


def _check_bool_env(problems: list[tuple[str, str]]) -> None:
    for key in _BOOL_KEYS:
        raw = os.environ.get(key)
        if raw is None:
            continue
        if raw.strip().lower() not in _BOOL_TRUE | _BOOL_FALSE:
            problems.append(
                (key, "must be one of true/false/1/0/yes/no (case-insensitive)")
            )


def validate_config() -> list[tuple[str, str]]:
    """Return ``(key, problem)`` pairs for every invalid configuration value.

    Never raises.  Never includes configured values in messages.  A clean
    configuration returns ``[]``.
    """
    problems: list[tuple[str, str]] = []

    # Positive scalars.
    for key, value, minimum in (
        ("RATE_LIMIT", RATE_LIMIT, 1),
        ("API_TIMEOUT", API_TIMEOUT, 1),
        ("RETRY_COUNT", RETRY_COUNT, 0),
        ("CACHE_MEMORY_TTL", CACHE_MEMORY_TTL, 1),
        ("CACHE_DISK_TTL", CACHE_DISK_TTL, 1),
        ("CACHE_REFRESH_INTERVAL", CACHE_REFRESH_INTERVAL, 1),
        ("HEALTH_CHECK_INTERVAL", HEALTH_CHECK_INTERVAL, 1),
        ("SCANNER_WORKERS", SCANNER_WORKERS, 1),
        ("SCANNER_CACHE_TTL", SCANNER_CACHE_TTL, 1),
        ("SCANNER_CACHE_MAX_ENTRIES", SCANNER_CACHE_MAX_ENTRIES, 1),
        ("DATA_STALE_AFTER_DAYS", DATA_STALE_AFTER_DAYS, 1),
        ("DATA_MAX_GAP_DAYS", DATA_MAX_GAP_DAYS, 1),
        ("RSI_PERIOD", RSI_PERIOD, 2),
        ("MACD_FAST", MACD_FAST, 1),
        ("MACD_SLOW", MACD_SLOW, 1),
        ("MACD_SIGNAL", MACD_SIGNAL, 1),
        ("BACKOFF_BASE", BACKOFF_BASE, 0.1),
        ("WORKER_METRICS_TTL_S", WORKER_METRICS_TTL_S, 1.0),
    ):
        _check_positive(problems, key, value, minimum=minimum)

    # Indicator coherence.
    if MACD_SLOW <= MACD_FAST:
        problems.append(("MACD_SLOW", "must be greater than MACD_FAST"))

    # Reconciliation / data-quality percentages.
    for key, value in (
        ("RECONCILE_PRICE_TOLERANCE_PCT", RECONCILE_PRICE_TOLERANCE_PCT),
        ("RECONCILE_VOLUME_TOLERANCE_PCT", RECONCILE_VOLUME_TOLERANCE_PCT),
        ("RECONCILE_MATERIAL_PRICE_PCT", RECONCILE_MATERIAL_PRICE_PCT),
        ("RECONCILE_MATERIAL_VOLUME_PCT", RECONCILE_MATERIAL_VOLUME_PCT),
        ("RECONCILE_CORPORATE_ACTION_MOVE_PCT", RECONCILE_CORPORATE_ACTION_MOVE_PCT),
    ):
        _check_pct(problems, key, value)

    # URLs.
    _check_url(problems, "WEBSOCKET_URL", WEBSOCKET_URL)
    _check_url(problems, "SECOND_PROVIDER_URL", SECOND_PROVIDER_URL)
    if SECOND_PROVIDER_URL and not SECOND_PROVIDER_NAME:
        problems.append(
            ("SECOND_PROVIDER_NAME", "must be set when SECOND_PROVIDER_URL is set")
        )

    # Boolean env values must be explicit when present.
    _check_bool_env(problems)

    return sorted(problems, key=lambda item: item[0])


def config_valid() -> bool:
    """True when the current configuration passes semantic validation."""
    return not validate_config()


__all__ = ["validate_config", "config_valid"]
