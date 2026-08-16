"""Release manifest — build and verify (Sprint 13.9 §5).

The manifest is a small, machine-readable JSON document written to
``data/state/release_manifest.json`` at release/build time.  It records
what a release actually is: application version, source revision, build
identifier, dependency declarations, Python compatibility, and the
schema versions of the configuration and calendar formats the release
was built against.

Contract:

* **Deterministic** except for the intentionally dynamic fields
  (``source_revision``, ``build_id``, ``release_timestamp``,
  ``build_python``).  Given the same checkout and timestamps, the
  manifest is byte-identical.
* **No secrets** — only package names + version constraints from
  ``requirements.txt`` and version numbers; never env values.
* Written **atomically with fsync** via ``save_json`` (it is critical,
  low-frequency state).
* ``verify_manifest()`` checks every field an operator or CI gate
  depends on and never raises.
"""

from __future__ import annotations

import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.utils.json_store import save_json

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
MANIFEST_PATH = REPO_ROOT / "data" / "state" / "release_manifest.json"
MANIFEST_SCHEMA_VERSION = "1.0"
PYTHON_SUPPORTED = ">=3.10"

_NAME_RE = re.compile(r"^([A-Za-z0-9_.-]+)")


def load_version(repo: Path = REPO_ROOT) -> str:
    """Read the application version from the VERSION file."""
    version_file = repo / "VERSION"
    if not version_file.exists():
        return "unknown"
    text = version_file.read_text(encoding="utf-8").strip()
    return text or "unknown"


def _git(repo: Path, *args: str) -> str:
    try:
        out = subprocess.run(
            ["git", "-C", str(repo), *args],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    return out.stdout.strip()


def parse_requirements(repo: Path = REPO_ROOT) -> dict[str, str]:
    """Parse ``requirements.txt`` into ``{package: constraint}``.

    Comments and blank lines are skipped; only the declared requirement
    line is recorded (extras and environment markers are preserved in
    the constraint string so the declaration is never lossy).
    """
    req_file = repo / "requirements.txt"
    if not req_file.exists():
        return {}
    deps: dict[str, str] = {}
    for raw in req_file.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        name_match = _NAME_RE.match(line)
        if not name_match:
            continue
        name = name_match.group(1)
        constraint = line[len(name_match.group(0)) :].strip()
        deps[name] = constraint or "*"
    return deps


def calendar_schema(repo: Path = REPO_ROOT) -> dict[str, Any]:
    """Calendar schema versions from the governed calendar module."""
    try:
        import src.data.calendar as calendar  # noqa: PLC0415 - lazy import

        return {
            "default_version": calendar.DEFAULT_CALENDAR_VERSION,
            "supported_versions": list(calendar.SUPPORTED_CALENDAR_VERSIONS),
        }
    except Exception:  # noqa: BLE001 - manifest build must not hard-fail
        return {"default_version": "unknown", "supported_versions": []}


def config_schema(repo: Path = REPO_ROOT) -> str:
    """Configuration schema version."""
    try:
        import src.config  # noqa: PLC0415 - lazy import

        return getattr(src.config, "CONFIG_SCHEMA_VERSION", "unknown")
    except Exception:  # noqa: BLE001
        return "unknown"


def build_manifest(
    repo: Path = REPO_ROOT,
    *,
    revision: str | None = None,
    timestamp: str | None = None,
    build_python: str | None = None,
) -> dict[str, Any]:
    """Build the release manifest dict.

    Args:
        repo: Repository root to read VERSION/requirements from.
        revision: Source revision override (injected for determinism
            tests).  Defaults to ``git rev-parse HEAD``.
        timestamp: ISO-8601 UTC timestamp override.  Defaults to now.
        build_python: Python version string override.  Defaults to the
            running interpreter.

    Returns:
        The manifest as a JSON-serialisable dict.
    """
    if revision is None:
        revision = _git(repo, "rev-parse", "HEAD") or "unknown"
    if timestamp is None:
        timestamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
    if build_python is None:
        build_python = sys.version.split()[0]

    short_rev = revision[:8] if revision and revision != "unknown" else "unknown"
    return {
        "schema": "nepse-quant-engine-release-manifest",
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "application": "NEPSE Quant Engine",
        "version": load_version(repo),
        "build_id": f"{short_rev}-{timestamp.replace(':', '').replace('-', '')[:12]}",
        "source_revision": revision,
        "release_timestamp": timestamp,
        "python_versions_supported": PYTHON_SUPPORTED,
        "build_python": build_python,
        "config_schema_version": config_schema(repo),
        "calendar": calendar_schema(repo),
        "dependencies": parse_requirements(repo),
    }


def write_manifest(manifest: dict[str, Any], path: Path = MANIFEST_PATH) -> Path:
    """Write *manifest* atomically (fsync) to *path*."""
    save_json(
        path,
        manifest,
        indent=2,
        sort_keys=True,
        log_name="ReleaseManifest",
        fsync=True,
    )
    return path


def verify_manifest(manifest: dict[str, Any], repo: Path = REPO_ROOT) -> list[str]:
    """Return a list of problems with *manifest* (empty when valid).

    Never raises.  Checks every field CI/operators depend on, including
    that the recorded version matches the VERSION file and that the
    recorded dependency declarations cover the declared requirements.
    """
    problems: list[str] = []

    if manifest.get("schema") != "nepse-quant-engine-release-manifest":
        problems.append("missing or wrong manifest schema marker")
    if manifest.get("schema_version") != MANIFEST_SCHEMA_VERSION:
        problems.append(f"unexpected manifest schema version (expected {MANIFEST_SCHEMA_VERSION})")

    expected_version = load_version(repo)
    if manifest.get("version") != expected_version:
        problems.append(f"version mismatch: manifest {manifest.get('version')!r} vs VERSION {expected_version!r}")

    if not manifest.get("source_revision") or manifest["source_revision"] == "unknown":
        problems.append("source revision is unknown")
    if not manifest.get("build_id"):
        problems.append("missing build identifier")
    if not manifest.get("release_timestamp"):
        problems.append("missing release timestamp")
    if manifest.get("python_versions_supported") != PYTHON_SUPPORTED:
        problems.append("python_versions_supported does not match the declared support contract")

    deps = manifest.get("dependencies")
    if not isinstance(deps, dict) or not deps:
        problems.append("missing dependency declarations")
    else:
        declared = parse_requirements(repo)
        if set(deps) != set(declared) or any(
            deps[k] != declared[k] for k in declared
        ):
            problems.append("dependency declarations do not match requirements.txt")

    config_ver = config_schema(repo)
    if manifest.get("config_schema_version") != config_ver:
        problems.append("config schema version does not match src.config")

    cal = manifest.get("calendar") or {}
    expected_cal = calendar_schema(repo)
    if cal.get("default_version") != expected_cal.get("default_version"):
        problems.append("calendar default version does not match src.data.calendar")

    return problems


__all__ = [
    "REPO_ROOT",
    "MANIFEST_PATH",
    "MANIFEST_SCHEMA_VERSION",
    "PYTHON_SUPPORTED",
    "load_version",
    "parse_requirements",
    "build_manifest",
    "write_manifest",
    "verify_manifest",
]
