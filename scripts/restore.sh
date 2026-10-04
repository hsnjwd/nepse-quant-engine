#!/usr/bin/env bash
# =====================================================================
# NEPSE Quant Engine — Restore Script (Linux / macOS)
# =====================================================================
# Usage:
#   ./scripts/restore.sh                                # Latest backup
#   ./scripts/restore.sh backups/nepse_..._20240730.tar.gz  # Specific file
#   ./scripts/restore.sh /path/to/backup.tar.gz         # Full path
# =====================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

echo "========================================================"
echo "  NEPSE Quant Engine — Restore"
echo "========================================================"

# ── Locate backup archive ────────────────────────────────────────
BACKUP_FILE="${1:-}"
if [ -z "${BACKUP_FILE}" ]; then
    # Auto-detect the most recent backup
    BACKUP_FILE=$(ls -t "${PROJECT_DIR}/backups"/nepse_quant_engine_backup_*.tar.gz 2>/dev/null | head -1)
    if [ -z "${BACKUP_FILE}" ]; then
        echo "ERROR: No backup found in ${PROJECT_DIR}/backups/" >&2
        echo "Usage: $0 [path/to/backup.tar.gz]" >&2
        exit 1
    fi
    echo "  Auto-detected: $(basename "${BACKUP_FILE}")"
else
    if [ ! -f "${BACKUP_FILE}" ]; then
        echo "ERROR: File not found: ${BACKUP_FILE}" >&2
        exit 1
    fi
fi

echo "  Source: ${BACKUP_FILE}"

# ── Extract to temp directory ────────────────────────────────────
TEMP_DIR=$(mktemp -d)
echo "  Extracting to temporary directory..."

tar -xzf "${BACKUP_FILE}" -C "${TEMP_DIR}"

# Find the extracted directory (strip .tar.gz extension)
EXTRACTED_DIR="${TEMP_DIR}/$(basename "${BACKUP_FILE}" .tar.gz)"
if [ ! -d "${EXTRACTED_DIR}" ]; then
    # The archive might have a single directory inside
    EXTRACTED_DIR=$(find "${TEMP_DIR}" -maxdepth 1 -type d | tail -1)
fi

echo "  Extracted to: ${EXTRACTED_DIR}"

# ── Confirm ──────────────────────────────────────────────────────
echo "--------------------------------------------------------"
echo "  This will OVERWRITE current data with backup data."
echo "  Source:      ${EXTRACTED_DIR}"
echo "  Destination: ${PROJECT_DIR}"
echo "--------------------------------------------------------"
read -r -p "  Continue? [y/N] " CONFIRM
if [ "${CONFIRM}" != "y" ] && [ "${CONFIRM}" != "Y" ]; then
    echo "  Restore cancelled."
    rm -rf "${TEMP_DIR}"
    exit 0
fi

# ── Restore data ─────────────────────────────────────────────────
if [ -d "${EXTRACTED_DIR}/data" ]; then
    echo "[1/4] Restoring market data..."
    rm -rf "${PROJECT_DIR}/data/raw" 2>/dev/null || true
    cp -r "${EXTRACTED_DIR}/data" "${PROJECT_DIR}/data"
    echo "      ✓ Data restored"
fi

# ── Restore .env ─────────────────────────────────────────────────
if [ -f "${EXTRACTED_DIR}/.env" ]; then
    echo "[2/4] Restoring .env configuration..."
    cp "${EXTRACTED_DIR}/.env" "${PROJECT_DIR}/.env"
    echo "      ✓ .env restored"
fi

# ── Restore cache ────────────────────────────────────────────────
NEPSE_HOME="${HOME}/.nepse"
if [ -d "${EXTRACTED_DIR}/nepse_home" ]; then
    echo "[3/4] Restoring cache and settings..."
    rm -rf "${NEPSE_HOME}" 2>/dev/null || true
    cp -r "${EXTRACTED_DIR}/nepse_home" "${NEPSE_HOME}"
    echo "      ✓ ~/.nepse restored"
fi

# ── Restore logs ─────────────────────────────────────────────────
if [ -d "${EXTRACTED_DIR}/logs" ]; then
    echo "[4/4] Restoring logs..."
    cp -r "${EXTRACTED_DIR}/logs" "${PROJECT_DIR}/logs"
    echo "      ✓ Logs restored"
fi

# ── Cleanup ──────────────────────────────────────────────────────
rm -rf "${TEMP_DIR}"

echo "========================================================"
echo "  Restore complete!"
echo "========================================================"
echo "  Restart the application to pick up the restored data."
echo "    docker compose restart web"
echo "    # or: ./launcher/run_app.sh"
