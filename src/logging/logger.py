"""Centralised logging configuration for the NEPSE Quant Engine.

Log level is configurable via the ``LOG_LEVEL`` environment variable
(default: ``INFO``).  Valid values: ``DEBUG``, ``INFO``, ``WARNING``,
``ERROR``, ``CRITICAL``.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

LOG_DIR = Path("logs")
LOG_DIR.mkdir(exist_ok=True)

LOG_FILE = LOG_DIR / "engine.log"

LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
_LEVEL_MAP: dict[str, int] = {
    "DEBUG": logging.DEBUG,
    "INFO": logging.INFO,
    "WARNING": logging.WARNING,
    "ERROR": logging.ERROR,
    "CRITICAL": logging.CRITICAL,
}
_level = _LEVEL_MAP.get(LOG_LEVEL, logging.INFO)

logging.basicConfig(
    level=_level,
    format="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE, encoding="utf-8"),
        logging.StreamHandler(),
    ],
)

logger = logging.getLogger("nepse")