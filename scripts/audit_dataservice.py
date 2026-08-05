#!/usr/bin/env python3
"""Migration Audit Tool — scan project for DataService compliance.

Scans every Python file in the project and reports any file that:
- Import ``requests``, ``urllib``, or ``httpx`` directly (outside approved modules)
- Import ``pd.read_csv`` directly
- Import from ``src.data.live_data``

Output is a clean summary showing which files pass and which violate.

Exit code:
    0 — no violations (clean)
    1 — violations found

Usage:
    python scripts/audit_dataservice.py
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path
from typing import Any

# Directories to skip (vendored code, generated files, etc.)
SKIP_DIRS: set[str] = {
    ".git",
    "__pycache__",
    ".venv",
    "venv",
    "env",
    "node_modules",
    ".agents",
    ".freebuff",
    ".pytest_cache",
    "dist",
    "build",
}

# Files/modules that are ALLOWED to import requests/urllib/httpx directly
APPROVED_MODULES: set[str] = {
    "src/data/providers.py",  # APIProvider uses requests
    "src/data/live_data.py",  # deprecated shim
    "src/api/explorer.py",  # API Explorer dev tool (Part 12)
}

# Patterns to flag
VIOLATION_PATTERNS: list[dict[str, Any]] = [
    {"pattern": "import requests", "label": "Direct 'import requests'"},
    {"pattern": "from requests import", "label": "Direct 'from requests'"},
    {"pattern": "import urllib", "label": "Direct 'import urllib'"},
    {"pattern": "from urllib import", "label": "Direct 'from urllib'"},
    {"pattern": "import httpx", "label": "Direct 'import httpx'"},
    {"pattern": "from httpx import", "label": "Direct 'from httpx'"},
    {"pattern": "pd.read_csv(", "label": "Direct 'pd.read_csv()'"},
    {"pattern": "pandas.read_csv(", "label": "Direct 'pandas.read_csv()'"},
    {"pattern": "from src.data.live_data import", "label": "Import from deprecated live_data"},
    {"pattern": "import src.data.live_data", "label": "Import from deprecated live_data"},
]


def _should_skip(path: Path) -> bool:
    """Return True if *path* should be skipped."""
    for part in path.parts:
        if part in SKIP_DIRS:
            return True
    return False


def _is_approved(path: Path) -> bool:
    """Return True if *path* is an approved module."""
    rel = path.relative_to(Path.cwd()).as_posix()
    return rel in APPROVED_MODULES


def _scan_file(path: Path) -> list[str]:
    """Scan a single Python file for violations. Return list of violation messages."""
    violations: list[str] = []
    try:
        content = path.read_text(encoding="utf-8", errors="ignore")
    except (OSError, UnicodeDecodeError) as exc:
        return [f"  ⚠ Could not read: {exc}"]

    for rule in VIOLATION_PATTERNS:
        for lineno, line in enumerate(content.splitlines(), 1):
            if rule["pattern"] in line:
                violations.append(
                    f"  ❌ Line {lineno}: {rule['label']}\n"
                    f"     {line.strip()}"
                )
    return violations


def main() -> int:
    """Run the audit and print results. Return exit code."""
    project_root = Path.cwd()
    py_files: list[Path] = []

    for path in project_root.rglob("*.py"):
        if _should_skip(path):
            continue
        py_files.append(path)

    total_files = len(py_files)
    clean_files: list[str] = []
    violating_files: list[tuple[str, list[str]]] = []

    print("=" * 60)
    print("  DataService Migration Audit")
    print("=" * 60)
    print(f"\nScanning {total_files} Python files...\n")

    for f in sorted(py_files):
        rel = f.relative_to(project_root).as_posix()
        if _is_approved(f):
            continue

        violations = _scan_file(f)
        if violations:
            violating_files.append((rel, violations))
        else:
            clean_files.append(rel)

    # Print results
    print("-" * 60)
    print(f"RESULTS: {len(clean_files)} clean, {len(violating_files)} violations\n")

    for rel, violations in violating_files:
        print(f"❌ {rel}")
        for v in violations:
            print(v)
        print()

    for rel in clean_files:
        print(f"✔ {rel}")

    # Summary
    print("\n" + "=" * 60)
    print(f"  Total files checked: {total_files}")
    print(f"  Clean (DataService): {len(clean_files)}")
    print(f"  Violations found:    {len(violating_files)}")
    print("=" * 60)

    if violating_files:
        print("\n❌ AUDIT FAILED — violations found")
        return 1
    else:
        print("\n✅ AUDIT PASSED — all files use DataService")
        return 0


if __name__ == "__main__":
    sys.exit(main())
