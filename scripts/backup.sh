#!/usr/bin/env bash
# =====================================================================
# NEPSE Quant Engine — Backup Script (Linux / macOS)
# =====================================================================
# Usage:
#   ./scripts/backup.sh                  # Default backup to ./backups/
#   ./scripts/backup.sh /path/to/dest    # Custom backup directory
#
# Creates a timestamped archive containing:
#   - Market data (CSV files)
#   - User configuration (.env)
#   - Cache directory (~/.nepse)
#   - Log files
# =====================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

# ── Configuration ──────────────────────────────────────────────────
BACKUP_DIR="${1:-${PROJECT_DIR}/backups}"
TIMESTAMP=$(date +"%Y%m%d_%H%M%S")
BACKUP_NAME="nepse_quant_engine_backup_${TIMESTAMP}"
BACKUP_PATH="${BACKUP_DIR}/${BACKUP_NAME}"
RETENTION_DAYS=30  # delete backups older than this

# ── Prerequisites ──────────────────────────────────────────────────
mkdir -p "${BACKUP_DIR}"
mkdir -p "${BACKUP_PATH}"

echo "========================================================"
echo "  NEPSE Quant Engine — Backup"
echo "========================================================"
echo "  Source:      ${PROJECT_DIR}"
echo "  Destination: ${BACKUP_PATH}"
echo "  Timestamp:   ${TIMESTAMP}"
echo "========================================================"

# ── 1. Market data (CSV files) ────────────────────────────────────
if [ -d "${PROJECT_DIR}/data" ]; then
    echo "[1/4] Backing up market data..."
    cp -r "${PROJECT_DIR}/data" "${BACKUP_PATH}/data"
    echo "      ✓ $(find "${PROJECT_DIR}/data" -name '*.csv' | wc -l) CSV files"
else
    echo "[1/4] No data directory found — skipping"
fi

# ── 2. Environment configuration ──────────────────────────────────
if [ -f "${PROJECT_DIR}/.env" ]; then
    echo "[2/4] Backing up .env configuration..."
    cp "${PROJECT_DIR}/.env" "${BACKUP_PATH}/.env"
    echo "      ✓ .env backed up (sensitive!)"
else
    echo "[2/4] No .env file found — skipping"
fi

# ── 3. User cache and settings ─────────────────────────────────────
NEPSE_HOME="${HOME}/.nepse"
if [ -d "${NEPSE_HOME}" ]; then
    echo "[3/4] Backing up cache and settings..."
    cp -r "${NEPSE_HOME}" "${BACKUP_PATH}/nepse_home"
    echo "      ✓ ~/.nepse backed up"
else
    echo "[3/4] No ~/.nepse found — skipping"
fi

# ── 4. Log files ───────────────────────────────────────────────────
if [ -d "${PROJECT_DIR}/logs" ]; then
    echo "[4/4] Backing up logs..."
    cp -r "${PROJECT_DIR}/logs" "${BACKUP_PATH}/logs"
    echo "      ✓ Logs backed up"
else
    echo "[4/4] No logs directory found — skipping"
fi

# ── Create archive ─────────────────────────────────────────────────
echo "--------------------------------------------------------"
echo "  Creating archive..."
cd "${BACKUP_DIR}"
tar -czf "${BACKUP_NAME}.tar.gz" "${BACKUP_NAME}"
rm -rf "${BACKUP_NAME}"  # remove uncompressed copy
echo "  ✓ Created: ${BACKUP_DIR}/${BACKUP_NAME}.tar.gz"

# Show size
SIZE=$(du -h "${BACKUP_NAME}.tar.gz" | cut -f1)
echo "  Size: ${SIZE}"

# ── Cleanup old backups ────────────────────────────────────────────
echo "--------------------------------------------------------"
echo "  Cleaning backups older than ${RETENTION_DAYS} days..."
find "${BACKUP_DIR}" -name "nepse_quant_engine_backup_*.tar.gz" \
    -type f -mtime "+${RETENTION_DAYS}" -delete
echo "  ✓ Done"

echo "========================================================"
echo "  Backup complete!"
echo "========================================================"
