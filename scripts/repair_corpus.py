"""One-time repair of corrupt rows in the ``data/raw`` OHLCV corpus.

Sprint 13.3's live data-quality report found **65 real-corpus symbols
each carrying one corrupt row** (2026-07-24: ``Low=0.0, Close=0.0`` with a
non-zero volume — a bad yonepse shard row that the refresh merged in).
The engine already quarantines those symbols (INVALID -> skipped, signal
suppressed); this script repairs the underlying files so the corpus is
clean again.

Engineering rules honoured (Sprint 13.3):

- **Never fabricate data** — corrupt rows are *removed*, never imputed.
- **Conservative by contract** — a row is removed only when it fails the
  canonical market-data contract: a non-positive / NaN / infinite OHLC
  price, a negative volume, or an unparseable / future date.  Rows that
  merely look unusual (but still satisfy the contract) are kept.
- **Centralized validation** — row-level checks reuse
  ``src.data.quality.validate_ohlcv_record`` (the same contract the
  engine enforces); no ad-hoc re-implementation here.
- **Byte preservation** — every surviving line is kept byte-for-byte;
  only corrupt lines are dropped.  The file's line terminator (CRLF vs
  LF) and header are preserved.
- **Atomic writes** — files are written via
  ``scripts.refresh_corpus._atomic_write`` (temp sibling + ``os.replace``
  with Windows antivirus retry), so a crash mid-write can never truncate
  a corpus file.
- **Backup before write** — originals are copied byte-for-byte into a
  timestamped backup directory (default ``data/_corpus_repair_backup/``)
  unless ``--no-backup``.
- **Dry-run mode** — ``--dry-run`` reports exactly what would be removed
  and writes nothing.
- **Never empties a file** — if every row of a file is corrupt, the file
  is left untouched and reported for manual attention (a header-only CSV
  would be INVALID ``empty_data``).

Usage::

    python scripts/repair_corpus.py --dry-run           # what would change
    python scripts/repair_corpus.py                     # repair + backup
    python scripts/repair_corpus.py --data-dir data/raw
    python scripts/repair_corpus.py --no-backup         # skip the backup copy
    python scripts/repair_corpus.py --backup-dir <path> # custom backup location

Exit code: 0 when the run leaves the corpus clean — in dry-run mode
that is always the case (nothing was written, nothing failed); for a
real run, 0 means every corrupt row was removed and no fully-corrupt
file required manual attention.  Non-zero when a real run could not
fully repair the corpus (e.g. a fully-corrupt file left untouched for
manual inspection).
"""

from __future__ import annotations

import argparse
import datetime
import shutil
import sys
from pathlib import Path
from typing import Any

# Keep the default in lock-step with ``src.config.DATA_DIRECTORY`` (a
# literal so the CLI works even when ``src`` is not importable).
DEFAULT_DATA_DIR = "data/raw"
# Default backup root (outside ``data/raw`` so a repair run can never be
# re-scanned as corpus data).  A fresh timestamped subdir is created per run.
DEFAULT_BACKUP_DIR = "data/_corpus_repair_backup"


# ---------------------------------------------------------------------------
# Row classification
# ---------------------------------------------------------------------------


def _row_is_corrupt(fields: list[str], today: datetime.date) -> tuple[bool, list[str]]:
    """Classify one CSV data line against the canonical contract.

    Args:
        fields: Split (but not stripped-of-meaning) fields of one line.
        today: Local date; future dates are contract errors.

    Returns:
        ``(corrupt, reasons)`` — *corrupt* True when the row must be
        removed, with machine-readable reason codes (mirroring the
        quality module's codes) for the report.
    """
    reasons: list[str] = []
    if len(fields) < 6:
        return True, ["too_few_fields"]

    raw = {"Open": fields[1], "High": fields[2], "Low": fields[3],
           "Close": fields[4], "Volume": fields[5]}
    # Convert to floats defensively: an unparseable cell is corrupt (the
    # row-level validator would raise; we must never let that abort the run).
    parsed: dict[str, Any] = {}
    for name, text in raw.items():
        try:
            parsed[name] = float(text)
        except (TypeError, ValueError):
            parsed[name] = float("nan")
            reasons.append(f"unparseable_{name.lower()}")

    from src.data.quality import validate_ohlcv_record  # noqa: PLC0415

    # Every OHLCV cell was pre-parsed to a float above (unparseable ->
    # NaN + reason), so the canonical validator always receives floats
    # and cannot raise here.
    issues = validate_ohlcv_record(parsed)
    reasons.extend(sorted({i.code for i in issues}))

    # Date field.
    date_field = fields[0].strip()
    try:
        row_date = datetime.date.fromisoformat(date_field)
        if row_date > today:
            reasons.append("future_date")
    except ValueError:
        reasons.append("invalid_date")

    return bool(reasons), sorted(set(reasons))


def _classify_lines(lines: list[str], today: datetime.date) -> tuple[list[int], list[dict]]:
    """Classify every data line; return ``(corrupt_indices, corrupt_rows)``.

    *corrupt_indices* are the line offsets (1-based; line 0 is the header)
    to drop; *corrupt_rows* is the machine-readable per-row detail used by
    the report.
    """
    corrupt_indices: list[int] = []
    corrupt_rows: list[dict] = []
    for idx, line in enumerate(lines[1:], start=1):
        stripped = line.strip()
        if not stripped:
            continue  # blank trailing line — never counted, never dropped
        fields = stripped.split(",")
        is_bad, reasons = _row_is_corrupt(fields, today)
        if is_bad:
            corrupt_indices.append(idx)
            corrupt_rows.append(
                {
                    "line": idx,
                    "date": fields[0].strip() if fields else "",
                    "reasons": reasons,
                }
            )
    return corrupt_indices, corrupt_rows


# ---------------------------------------------------------------------------
# Per-file repair
# ---------------------------------------------------------------------------


def repair_file(
    path: Path,
    *,
    dry_run: bool = False,
    backup_dir: Path | None = None,
    today: datetime.date | None = None,
) -> dict[str, Any]:
    """Remove contract-violating rows from one corpus CSV.

    Args:
        path: Corpus file to inspect/repair.
        dry_run: Report only — nothing is written.
        backup_dir: When given (and not dry_run), the original file is
            copied byte-for-byte here before the repaired file is written.
        today: Reference date for future-date detection (defaults to
            today, injectable for tests).

    Returns:
        A machine-readable result dict::

            {
              "symbol": "TTL",
              "total_rows": 71,
              "removed": 1,
              "kept": 70,
              "reasons": {"non_positive_price": 1},
              "skipped": None | "empty_file" | "no_header" | "all_rows_corrupt",
              "written": bool,
            }
    """
    path = Path(path)
    result: dict[str, Any] = {
        "symbol": path.stem.upper(),
        "total_rows": 0,
        "removed": 0,
        "kept": 0,
        "reasons": {},
        "skipped": None,
        "written": False,
    }

    if not path.exists():
        result["skipped"] = "missing_file"
        return result

    try:
        text = path.read_bytes().decode("utf-8-sig", errors="replace")
    except OSError as exc:  # pragma: no cover - defensive
        result["skipped"] = f"unreadable:{exc}"
        return result

    lines = text.splitlines()
    if not lines:
        result["skipped"] = "empty_file"
        return result
    header = lines[0].strip().lstrip("\ufeff")
    if "close" not in header.lower():
        result["skipped"] = "no_header"
        return result

    today = today or datetime.date.today()
    corrupt_indices, corrupt_rows = _classify_lines(lines, today)
    result["total_rows"] = len(lines) - 1
    result["removed"] = len(corrupt_indices)
    result["kept"] = result["total_rows"] - result["removed"]
    for row in corrupt_rows:
        for code in row["reasons"]:
            result["reasons"][code] = result["reasons"].get(code, 0) + 1

    if not corrupt_indices:
        return result  # nothing to do; never touch a clean file

    # Never reduce a file to just a header: a header-only CSV would be
    # INVALID ``empty_data``.  Leave it for manual attention instead.
    if result["kept"] == 0:
        result["skipped"] = "all_rows_corrupt"
        return result

    if dry_run:
        return result

    # Backup the original (byte-for-byte) before writing.
    if backup_dir is not None:
        backup_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, backup_dir / path.name)

    terminator = "\r\n" if "\r\n" in text else "\n"
    keep = [line for idx, line in enumerate(lines) if idx not in corrupt_indices]
    body = terminator.join(keep)
    # ``_atomic_write`` (temp sibling + os.replace with retry) is the
    # exact helper the corpus refresh tooling uses.
    from scripts.refresh_corpus import _atomic_write  # noqa: PLC0415

    _atomic_write(path, body + terminator)
    result["written"] = True
    return result


def repair_corpus(
    data_dir: str | Path,
    *,
    dry_run: bool = False,
    backup_dir: str | Path | None = DEFAULT_BACKUP_DIR,
    today: datetime.date | None = None,
) -> dict[str, Any]:
    """Repair every ``*.csv`` under *data_dir*; return the aggregate report.

    The ``sample`` file (if any) is excluded, mirroring the scanner and
    the quality CLI.
    """
    base = Path(data_dir)
    files = sorted(base.glob("*.csv"))
    files = [f for f in files if f.stem.lower() != "sample"]

    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    run_backup = Path(backup_dir) / stamp if backup_dir else None

    per_file: list[dict[str, Any]] = []
    total_removed = 0
    total_rows = 0
    for f in files:
        res = repair_file(
            f,
            dry_run=dry_run,
            backup_dir=run_backup,
            today=today,
        )
        per_file.append(res)
        total_removed += res["removed"]
        total_rows += res["total_rows"]

    cleaned = [r for r in per_file if r["removed"] > 0 and r["skipped"] is None]
    remaining = [r for r in per_file if r["skipped"] in ("all_rows_corrupt",)]
    return {
        "data_dir": str(base),
        "symbols_checked": len(files),
        "total_rows": total_rows,
        "total_removed": total_removed,
        "files_cleaned": len(cleaned),
        "files_requiring_manual": [r["symbol"] for r in remaining],
        "backup_dir": str(run_backup) if run_backup and not dry_run else None,
        "per_file": per_file,
    }


# ---------------------------------------------------------------------------
# Report + CLI
# ---------------------------------------------------------------------------


def _print_report(agg: dict[str, Any]) -> None:
    print("=" * 72)
    print("NEPSE Quant Engine — Corpus Repair Report (Sprint 13.3)")
    print("=" * 72)
    print(f"{'symbol':<12}{'rows':>6}{'removed':>9}{'kept':>7}  reasons")
    print("-" * 72)
    for row in agg["per_file"]:
        if row["removed"] == 0 and row["skipped"] is None:
            continue
        reasons = ",".join(f"{k}={v}" for k, v in sorted(row["reasons"].items()))
        status = row["skipped"] or reasons
        print(
            f"{row['symbol']:<12}{row['total_rows']:>6}{row['removed']:>9}"
            f"{row['kept']:>7}  {status}"
        )
    print("-" * 72)
    print(f"symbols checked : {agg['symbols_checked']}")
    print(f"rows checked    : {agg['total_rows']}")
    print(f"rows removed    : {agg['total_removed']}")
    print(f"files cleaned   : {agg['files_cleaned']}")
    print(f"manual attention: {', '.join(agg['files_requiring_manual']) or 'none'}")
    if agg.get("backup_dir"):
        print(f"backup kept at  : {agg['backup_dir']}")
    print(f"data directory  : {agg['data_dir']}")


def main(argv: list[str] | None = None) -> int:
    """CLI entry point; returns a process exit code (testable).

    Exit 0 when the corpus is clean after the run; non-zero when corrupt
    rows remain unrepaired (fully-corrupt files) or a file could not be
    handled.
    """
    parser = argparse.ArgumentParser(
        prog="python -m scripts.repair_corpus",
        description="Remove contract-violating rows from the OHLCV corpus.",
    )
    parser.add_argument(
        "--data-dir",
        default=DEFAULT_DATA_DIR,
        help=f"Directory of OHLCV CSVs (default: {DEFAULT_DATA_DIR})",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Report what would be removed; write nothing.",
    )
    parser.add_argument(
        "--no-backup",
        action="store_true",
        help="Do not copy originals to the backup directory before writing.",
    )
    parser.add_argument(
        "--backup-dir",
        default=None,
        help=f"Backup root (default: {DEFAULT_BACKUP_DIR}); a timestamped "
        "subdirectory is created per run.",
    )
    args = parser.parse_args(argv)

    backup = None if args.no_backup else (args.backup_dir or DEFAULT_BACKUP_DIR)
    agg = repair_corpus(args.data_dir, dry_run=args.dry_run, backup_dir=backup)
    _print_report(agg)
    if args.dry_run:
        print("\nDRY RUN - no files were written.")
    else:
        print(
            "\nRepair complete."
            if not agg["files_requiring_manual"]
            else "\nRepair incomplete - files require manual attention."
        )

    # Dry-run is a pure report: nothing was written, nothing failed.
    if args.dry_run:
        return 0
    return 1 if agg["files_requiring_manual"] else 0


if __name__ == "__main__":
    sys.exit(main())
