#!/usr/bin/env python3
"""Generate ``data/state/release_manifest.json`` (Sprint 13.9 §5).

Usage:
    python3 scripts/build_release_manifest.py [--verify]

Writes the manifest atomically (fsync) and, with ``--verify``, checks
it against the checkout.  Deterministic except for the intentionally
dynamic fields (source revision, build id, timestamp, build python).
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))


def main() -> int:
    verify_only = "--verify" in sys.argv
    from src.utils.release_manifest import (
        build_manifest,
        verify_manifest,
        write_manifest,
    )

    if verify_only:
        manifest = build_manifest(REPO)
        problems = verify_manifest(manifest, REPO)
    else:
        manifest = build_manifest(REPO)
        path = write_manifest(manifest)
        print(f"Wrote {path}")
        problems = verify_manifest(manifest, REPO)

    if problems:
        for problem in problems:
            print(f"ERROR: {problem}", file=sys.stderr)
        return 1
    print("Release manifest OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
