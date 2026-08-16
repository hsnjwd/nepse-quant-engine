#!/bin/sh
# =====================================================================
# NEPSE Quant Engine — Release Artifact Builder (Sprint 13.9 §25)
# =====================================================================
# Usage:
#   sh scripts/build_release_artifact.sh [output_dir]
#
# Produces:
#   <output_dir>/nepse-quant-engine-<VERSION>.tar.gz   (source artifact)
#   data/state/release_manifest.json                   (release manifest)
#
# The artifact is content-reproducible from a clean checkout: it ships
# source + tests + docs only (runtime dirs, caches, logs, backups and
# generated junk are excluded), and is verified for secrets, local
# paths, missing required files and junk before the script succeeds.
#
# POSIX sh only (no bash-isms): CI and container entrypoints invoke
# this as ``sh scripts/build_release_artifact.sh``, and dash rejects
# ``set -o pipefail`` / ``BASH_SOURCE``.
# =====================================================================

set -eu

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

VERSION="$(cat VERSION)"
OUT="${1:-dist}"
mkdir -p "$OUT"
ARCHIVE="$OUT/nepse-quant-engine-${VERSION}.tar.gz"

echo "============================================================"
echo "  NEPSE Quant Engine — Release Artifact"
echo "  Version:  ${VERSION}"
echo "  Output:   ${ARCHIVE}"
echo "============================================================"

# 1. Source artifact (content-complete, junk-free).
python3 scripts/build_source_tarball.py "$ARCHIVE"

# 2. Release manifest (deterministic except dynamic fields).
python3 scripts/build_release_manifest.py

# 3. Verify the artifact (secrets / local paths / junk / required files).
python3 scripts/verify_release_artifact.py "$ARCHIVE"

echo "============================================================"
echo "  Release artifact OK: ${ARCHIVE}"
echo "============================================================"
