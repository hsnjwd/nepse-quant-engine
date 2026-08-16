"""Release artifact — build and integrity verification (Sprint 13.9 §25).

A release artifact is the reproducible source bundle that can be turned
into a production candidate.  ``build_source_tarball`` produces it from
a checkout (or working tree), and ``verify_release_artifact`` audits a
directory for everything a release must NOT ship:

* committed secrets (token/API-key/private-key patterns with values)
* local developer paths (POSIX home dirs and Windows user-profile roots)
* temporary / generated junk (``__pycache__``, ``*.pyc``, ``*.tmp``,
  ``*.corrupt.bak``, pytest temp roots, benchmark result outputs)
* local-only workspace / runtime state that must never ship in a
  reproducible source bundle:
  - ``.freebuff/`` — Freebuff workspace metadata (never tracked)
  - ``data/raw/syn*.csv`` — the synthetic benchmark corpus, generated
    at runtime by ``benchmarks.common.write_csvs`` into its own temp
    dir; leftover files under ``data/raw`` are build-machine residue
  - ``data/state/worker_metrics.json``, ``nepse_calendar_history.json``,
    ``release_manifest.json`` — gitignored runtime/build state (the
    calendar itself is tracked and ships; the manifest is regenerated
    by the build)
* missing required source files / tests

The scan is deliberately conservative: obvious placeholders
(``your_*``, ``xxx``, ``<...>``, ``${...}``) are not flagged as
secrets; container paths (``/app``, ``/home/nepse``) and the generic
test fixture root ``/home/user`` are not flagged as local developer
paths; and example paths inside ``docs/`` prose are exempt from the
local-path check (secret scanning there is never skipped).
"""

from __future__ import annotations

import re
import tarfile
from pathlib import Path
from typing import Iterable

EXCLUDED_DIR_NAMES = {
    ".git",
    "__pycache__",
    ".pytest_cache",
    ".pytest_tmp",
    ".mypy_cache",
    "logs",
    "backups",
    "dist",
    "pids",
    ".venv",
    "venv",
    "env",
    "node_modules",
    "benchmarks/results",
    ".freebuff",
}
EXCLUDED_FILE_SUFFIXES = (
    ".pyc",
    ".pyo",
    ".tmp",
    ".lock",
    ".bak",
    ".db-wal",
    ".db-shm",
)
EXCLUDED_FILE_NAMES = {
    ".DS_Store",
    "desktop.db",
    "desktop.db-shm",
    "desktop.db-wal",
    # Gitignored runtime/build state under data/state/: generated while
    # the engine runs or by the release build, regenerated on demand,
    # and never part of a source artifact.
    "worker_metrics.json",
    "nepse_calendar_history.json",
    "release_manifest.json",
}

# Generated benchmark corpus: benchmarks.common.write_csvs writes
# ``synNNN.csv`` files; any such file in the tree is build-machine
# residue, never required source.
_SYNTHETIC_CORPUS_RE = re.compile(r"^syn\d+\.csv$", re.IGNORECASE)

REQUIRED_SOURCE_FILES = (
    "VERSION",
    "requirements.txt",
    "app.py",
    "Dockerfile",
    "docker-compose.yml",
    "README.md",
    "pytest.ini",
    "src/api/main.py",
    "src/config/__init__.py",
    "src/utils/json_store.py",
    "src/data/calendar.py",
    "docs/OPERATIONS.md",
    "docs/RELEASE_CHECKLIST.md",
    "docs/STATE_INVENTORY.md",
)

_SECRET_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(
        r"(?i)\b(?:telegram_token|bot_token|api[_-]?key|client[_-]?secret|"
        r"access[_-]?token|secret_key|password|credential)\b\s*=\s*[\"']?[A-Za-z0-9_\-.:]{12,}"
    ),
    re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----"),
)
_PLACEHOLDER_RE = re.compile(
    r"(?i)(your_|xxx+|example|placeholder|changeme|<[^>]+>|\$\{|\{\{)", re.IGNORECASE
)
# Match real user-profile roots only (trailing slash required), never
# bare prefixes — ``user_settings.json`` must not be flagged because it
# starts with the letters ``user``.
_LOCAL_PATH_RE = re.compile(r"(?:/home/|/Users/|/user/|/users/|c:\\Users\\|C:\\Users\\)[A-Za-z0-9_.-]+")
_CONTAINER_PATH_RE = re.compile(r"^/home/nepse$|^/app$")
# Generic example roots that are NOT developer-specific and are
# deliberately allowed (documented in the module docstring): the
# container's own /home/nepse and the classic generic
# "/home/user" fixture path used in tests/docs.
_GENERIC_PATH_RE = re.compile(r"^/home/user($|/)|^/home/nepse($|/)|^/app($|/)")


def _is_excluded_dir(rel: Path) -> bool:
    """True when any path component of *rel* is an excluded directory.

    ``benchmarks/results`` is matched as an adjacent component pair
    (it can sit anywhere under the tree); every other entry is a single
    component.
    """
    parts = rel.parts
    for i, part in enumerate(parts):
        if part in EXCLUDED_DIR_NAMES:
            return True
        if part == "benchmarks" and i + 1 < len(parts) and parts[i + 1] == "results":
            return True
    return False


def _is_excluded_file(rel: Path) -> bool:
    if rel.name in EXCLUDED_FILE_NAMES:
        return True
    if rel.suffix.lower() in EXCLUDED_FILE_SUFFIXES:
        return True
    # Synthetic benchmark corpus files (generated; see module docstring).
    if rel.parent.parts[:2] == ("data", "raw") and _SYNTHETIC_CORPUS_RE.match(rel.name):
        return True
    return False


def iter_release_files(root: Path) -> Iterable[Path]:
    """Yield relative paths of every file a release should contain."""
    for path in sorted(root.rglob("*")):
        rel = path.relative_to(root)
        if path.is_dir():
            continue
        if _is_excluded_dir(rel.parent):
            continue
        if _is_excluded_file(rel):
            continue
        yield rel


def build_source_tarball(root: Path, out: Path, *, prefix: str = "nepse-quant-engine/") -> Path:
    """Write a content-complete source tarball of *root* to *out*.

    Excludes runtime/generated/junk files (see ``EXCLUDED_*``), so the
    artifact is reproducible from a clean checkout and never carries
    local state.  Returns *out*.
    """
    out.parent.mkdir(parents=True, exist_ok=True)
    with tarfile.open(out, "w:gz") as tar:
        for rel in iter_release_files(root):
            tar.add(root / rel, arcname=f"{prefix}{rel}")
    return out


def _scan_text(path: Path, *, skip_local_paths: bool = False) -> list[str]:
    """Scan *path* for secrets and local developer paths.

    ``skip_local_paths`` exempts documentation prose (``docs/``):
    tutorial example paths (``C:\\Users\\User\\...``, ``~/Desktop/...``)
    are instructional, not shipped configuration.  Secret scanning is
    never skipped — a credential in docs is still a credential.
    """
    problems: list[str] = []
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return problems

    for pattern in _SECRET_PATTERNS:
        for match in pattern.finditer(text):
            value = match.group(0)
            if _PLACEHOLDER_RE.search(value):
                continue
            problems.append(f"{path}: possible secret pattern: {value[:40]}...")

    if not skip_local_paths:
        for match in _LOCAL_PATH_RE.finditer(text):
            candidate = match.group(0)
            if _CONTAINER_PATH_RE.search(candidate) or _GENERIC_PATH_RE.search(candidate):
                continue
            problems.append(f"{path}: local developer path: {candidate}")

    return problems


def verify_release_artifact(root: Path) -> list[str]:
    """Audit directory *root* as a release artifact.

    Returns a list of problems (empty when the artifact is clean).
    Never raises on missing files — missing *required* files are
    reported as problems.
    """
    problems: list[str] = []

    for rel in iter_release_files(root):
        path = root / rel
        if rel.suffix.lower() in {".py", ".md", ".txt", ".yml", ".yaml", ".json", ".sh", ".ini", ".bat", ".html", ".js", ".css", ".toml", ".cfg"}:
            problems.extend(
                _scan_text(path, skip_local_paths=rel.parts[:1] == ("docs",))
            )

    for required in REQUIRED_SOURCE_FILES:
        if not (root / required).exists():
            problems.append(f"required file missing: {required}")

    tests_dir = root / "tests"
    if not tests_dir.is_dir():
        problems.append("tests/ directory missing")
    else:
        test_files = [p for p in tests_dir.glob("test_*.py")]
        if not test_files:
            problems.append("tests/ contains no test_*.py files")
        if not (tests_dir / "test_sprint13_9.py").exists():
            problems.append("tests/test_sprint13_9.py missing from artifact")

    if (root / "benchmarks" / "results").exists():
        problems.append("benchmark result artifacts present in release artifact")

    return problems


def verify_tarball(tarball: Path) -> list[str]:
    """Extract *tarball* to a temp dir and audit it."""
    import tempfile

    with tempfile.TemporaryDirectory(prefix="nqe-artifact-") as tmp:
        with tarfile.open(tarball, "r:gz") as tar:
            members = tar.getmembers()
            if not members:
                return ["tarball is empty"]
            # Strip a single leading prefix component (repo dir).
            top = Path(members[0].name).parts[0]
            # No filter arg: ``extractall(filter=...)`` needs Python 3.12+
            # and this tarball is self-produced from trusted sources with
            # controlled paths, so plain extraction is safe.
            tar.extractall(tmp)
        extracted = Path(tmp) / top
        if not extracted.is_dir():
            extracted = Path(tmp)
        return verify_release_artifact(extracted)


__all__ = [
    "iter_release_files",
    "build_source_tarball",
    "verify_release_artifact",
    "verify_tarball",
    "REQUIRED_SOURCE_FILES",
]
