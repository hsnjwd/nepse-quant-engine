#!/usr/bin/env python3
"""Build the release source tarball (Sprint 13.9 §25).

Usage:
    python3 scripts/build_source_tarball.py [output.tar.gz]
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))


def main() -> int:
    from src.utils.release_manifest import load_version
    from src.utils.release_artifact import build_source_tarball

    if len(sys.argv) > 1:
        out = Path(sys.argv[1])
    else:
        version = load_version(REPO)
        out = REPO / "dist" / f"nepse-quant-engine-{version}.tar.gz"

    prefix = f"nepse-quant-engine-{load_version(REPO)}/"
    path = build_source_tarball(REPO, out, prefix=prefix)
    print(f"Wrote {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
