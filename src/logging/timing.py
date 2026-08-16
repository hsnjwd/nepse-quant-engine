"""Structured timing helpers for performance observability.

Provides a tiny context manager that logs a structured, single-line
timing record (``name=... duration_ms=... key=value ...``) so
operators can grep logs for slow operations without parsing prose.
"""

from __future__ import annotations

import logging
import time
from contextlib import contextmanager
from typing import Any, Iterator

logger = logging.getLogger("nepse.performance")


@contextmanager
def timed(
    name: str,
    level: int = logging.DEBUG,
    **extra: Any,
) -> Iterator[None]:
    """Log the wall-clock duration of the wrapped block.

    Args:
        name: Operation name (e.g. ``scan_market``).
        level: Log level for the timing record.
        **extra: Extra structured key/value fields.

    Example:
        with timed("scan_market", level=logging.INFO, files=250):
            scan_market()
    """
    start = time.perf_counter()
    try:
        yield
    finally:
        duration_ms = (time.perf_counter() - start) * 1000.0
        fields = " ".join(f"{key}={value}" for key, value in extra.items())
        logger.log(level, "timing name=%s duration_ms=%.2f %s", name, duration_ms, fields)
