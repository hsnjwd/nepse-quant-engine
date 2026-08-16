#!/usr/bin/env python3
"""Verify a release artifact (Sprint 13.9 §25).

Usage:
    python3 scripts/verify_release_artifact.py [PATH]

PATH may be a directory (a checkout) or a ``.tar.gz`` source artifact.
Scans for secrets, local developer paths, temp/generated junk, and
missing required sources/tests.  Exits 0 when the artifact is clean.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))


def main() -> int:
    from src.utils.release_artifact import verify_release_artifact, verify_tarball

    target = Path(sys.argv[1]) if len(sys.argv) > 1 else REPO
    if target.suffix == ".gz" or target.name.endswith(".tar.gz"):
        problems = verify_tarball(target)
    else:
        problems = verify_release_artifact(target)

    if problems:
        for problem in problems:
            print(f"ERROR: {problem}", file=sys.stderr)
        print(f"Release artifact INVALID: {len(problems)} problem(s)", file=sys.stderr)
        return 1
    print("Release artifact OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
