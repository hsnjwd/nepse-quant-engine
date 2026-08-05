"""Canonical package version for the NEPSE Quant Engine.

This module is the **single source of truth** for the project version.
It reads the repo-root ``VERSION`` file so that every component
(FastAPI metadata, UI, packaging, docs) reports the same version.

Usage::

    from src.version import __version__
"""

from __future__ import annotations

from pathlib import Path

_VERSION_FILE = Path(__file__).resolve().parent.parent / "VERSION"


def _read_version() -> str:
    """Read the version from the repo-root ``VERSION`` file."""
    try:
        text = _VERSION_FILE.read_text(encoding="utf-8").strip()
        return text or "0.0.0"
    except OSError:
        return "0.0.0"


__version__: str = _read_version()
