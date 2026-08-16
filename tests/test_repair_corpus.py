"""Tests for scripts/repair_corpus.py (Sprint 13.3 — corpus repair tooling).

Hermetic by construction: every test uses ``tmp_path`` data dirs with
byte-exact synthetic CSVs; nothing touches the real ``data/raw`` corpus.
The engineering rules under test: corrupt rows are *removed* (never
fabricated), surviving lines are preserved byte-for-byte, writes are
atomic, originals are backed up before writing, dry-run writes nothing,
and a fully-corrupt file is left for manual attention rather than being
emptied to a header-only CSV.
"""

from __future__ import annotations

import datetime
import subprocess
import sys
from pathlib import Path

from scripts.repair_corpus import (
    _classify_lines,
    _row_is_corrupt,
    repair_corpus,
    repair_file,
)

HEADER = "Date,Open,High,Low,Close,Volume"
# The documented real-corpus corruption: zero Low/Close with nonzero volume.
BAD_ROW = "2026-07-24,770.1,773.2,0.0,0.0,28478"
# Rows end 2026-08-12 — one day before TODAY — so the quality gate
# classifies them FRESH (a STALE/SUSPICIOUS frame would fail the
# TestQualityIntegration assertion on ``valid``).
GOOD_ROWS = [
    "2026-08-10,728.0,730.0,728.0,730.0,14520",
    "2026-08-11,721.0,721.0,719.0,720.0,14528",
    "2026-08-12,718.0,720.0,715.2,715.2,7076",
]

TODAY = datetime.date(2026, 8, 13)


def _write(path: Path, lines: list[str], terminator: str = "\n") -> None:
    """Write *lines* with an exact terminator (no newline translation)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(
        terminator.join(lines).encode("utf-8") + terminator.encode("utf-8")
    )


# ---------------------------------------------------------------------------
# Row classification
# ---------------------------------------------------------------------------


class TestRowClassification:
    def test_good_row_not_corrupt(self):
        assert _row_is_corrupt("2026-08-03,728,730,728,730,14520".split(","), TODAY) == (False, [])

    def test_zero_close_is_corrupt(self):
        corrupt, reasons = _row_is_corrupt(BAD_ROW.split(","), TODAY)
        assert corrupt is True
        assert "non_positive_price" in reasons

    def test_negative_volume_is_corrupt(self):
        corrupt, reasons = _row_is_corrupt(
            "2026-08-03,728,730,728,730,-5".split(","), TODAY
        )
        assert corrupt is True
        assert "negative_volume" in reasons

    def test_future_date_is_corrupt(self):
        corrupt, reasons = _row_is_corrupt(
            "2026-12-01,728,730,728,730,5".split(","), TODAY
        )
        assert corrupt is True
        assert "future_date" in reasons

    def test_invalid_date_is_corrupt(self):
        corrupt, reasons = _row_is_corrupt(
            "not-a-date,728,730,728,730,5".split(","), TODAY
        )
        assert corrupt is True
        assert "invalid_date" in reasons

    def test_unparseable_price_is_corrupt(self):
        corrupt, reasons = _row_is_corrupt(
            "2026-08-03,abc,730,728,730,5".split(","), TODAY
        )
        assert corrupt is True

    def test_too_few_fields_is_corrupt(self):
        assert _row_is_corrupt("2026-08-03,728,730".split(","), TODAY)[0] is True

    def test_classify_lines_finds_only_bad_row(self):
        lines = [HEADER] + GOOD_ROWS + [BAD_ROW]
        corrupt_indices, corrupt_rows = _classify_lines(lines, TODAY)
        assert corrupt_indices == [len(GOOD_ROWS) + 1]
        assert corrupt_rows[0]["date"] == "2026-07-24"
        assert "non_positive_price" in corrupt_rows[0]["reasons"]


# ---------------------------------------------------------------------------
# Per-file repair
# ---------------------------------------------------------------------------


class TestRepairFile:
    def test_clean_file_untouched(self, tmp_path):
        p = tmp_path / "ADBL.csv"
        _write(p, [HEADER] + GOOD_ROWS)
        before = p.read_bytes()
        res = repair_file(p, today=TODAY)
        assert res["removed"] == 0
        assert res["written"] is False
        assert p.read_bytes() == before  # byte-identical

    def test_bad_row_removed_bytes_preserved(self, tmp_path):
        p = tmp_path / "ADBL.csv"
        _write(p, [HEADER] + GOOD_ROWS + [BAD_ROW])
        res = repair_file(p, today=TODAY)
        assert res["removed"] == 1
        assert res["kept"] == len(GOOD_ROWS)
        assert res["written"] is True
        text = p.read_bytes().decode()
        assert BAD_ROW not in text
        # Surviving lines byte-identical (no reformatting, no sort).
        for row in GOOD_ROWS:
            assert row in text
        assert text.count("2026-07-24") == 0

    def test_crlf_terminator_preserved(self, tmp_path):
        p = tmp_path / "CRLF.csv"
        _write(p, [HEADER] + GOOD_ROWS + [BAD_ROW], terminator="\r\n")
        res = repair_file(p, today=TODAY)
        assert res["removed"] == 1
        text = p.read_bytes().decode()
        assert "\r\n" in text
        assert BAD_ROW not in text

    def test_dry_run_writes_nothing(self, tmp_path):
        p = tmp_path / "ADBL.csv"
        _write(p, [HEADER] + GOOD_ROWS + [BAD_ROW])
        before = p.read_bytes()
        res = repair_file(p, dry_run=True, today=TODAY)
        assert res["removed"] == 1
        assert res["written"] is False
        assert p.read_bytes() == before

    def test_backup_created_before_write(self, tmp_path):
        p = tmp_path / "ADBL.csv"
        _write(p, [HEADER] + GOOD_ROWS + [BAD_ROW])
        backup = tmp_path / "backup"
        res = repair_file(p, backup_dir=backup, today=TODAY)
        assert res["written"] is True
        backed = backup / "ADBL.csv"
        assert backed.exists()
        # Backup holds the ORIGINAL (with the bad row), untouched.
        assert BAD_ROW in backed.read_text()

    def test_all_rows_corrupt_skipped_for_manual(self, tmp_path):
        p = tmp_path / "ALLBAD.csv"
        _write(p, [HEADER] + [BAD_ROW, BAD_ROW, BAD_ROW])
        before = p.read_bytes()
        res = repair_file(p, today=TODAY)
        assert res["skipped"] == "all_rows_corrupt"
        assert res["written"] is False
        assert p.read_bytes() == before  # never emptied to a header-only file

    def test_missing_file(self, tmp_path):
        res = repair_file(tmp_path / "NOPE.csv", today=TODAY)
        assert res["skipped"] == "missing_file"

    def test_sample_excluded_from_corpus(self, tmp_path):
        _write(tmp_path / "sample.csv", [HEADER] + [BAD_ROW])
        agg = repair_corpus(tmp_path, backup_dir=tmp_path / "bk", today=TODAY)
        assert agg["symbols_checked"] == 0

    def test_corpus_repair_aggregate(self, tmp_path):
        _write(tmp_path / "ADBL.csv", [HEADER] + GOOD_ROWS + [BAD_ROW])
        _write(tmp_path / "CLEAN.csv", [HEADER] + GOOD_ROWS)
        _write(tmp_path / "ALLBAD.csv", [HEADER] + [BAD_ROW, BAD_ROW])
        agg = repair_corpus(tmp_path, backup_dir=tmp_path / "bk", today=TODAY)
        assert agg["symbols_checked"] == 3
        assert agg["total_removed"] == 3
        assert agg["files_cleaned"] == 1
        assert agg["files_requiring_manual"] == ["ALLBAD"]
        # Repaired file no longer carries the corrupt row.
        assert "2026-07-24" not in (tmp_path / "ADBL.csv").read_text()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


class TestCli:
    def test_cli_exits_zero_when_clean(self, tmp_path):
        _write(tmp_path / "CLEAN.csv", [HEADER] + GOOD_ROWS)
        proc = subprocess.run(
            [
                sys.executable,
                "-m",
                "scripts.repair_corpus",
                "--data-dir",
                str(tmp_path),
                "--dry-run",
            ],
            capture_output=True,
            text=True,
            cwd=Path(__file__).resolve().parent.parent,
            timeout=120,
        )
        assert proc.returncode == 0
        assert "DRY RUN" in proc.stdout

    def test_cli_dry_run_exits_zero_with_manual_attention(self, tmp_path):
        _write(tmp_path / "ALLBAD.csv", [HEADER] + [BAD_ROW, BAD_ROW])
        proc = subprocess.run(
            [
                sys.executable,
                "-m",
                "scripts.repair_corpus",
                "--data-dir",
                str(tmp_path),
                "--dry-run",
            ],
            capture_output=True,
            text=True,
            cwd=Path(__file__).resolve().parent.parent,
            timeout=120,
        )
        # Dry-run reports but never writes; manual-attention is a *report*
        # outcome, and dry-run still exits 0 (nothing was written).
        assert proc.returncode == 0
        assert "ALLBAD" in proc.stdout

    def test_cli_exits_nonzero_on_unrepairable(self, tmp_path):
        _write(tmp_path / "ALLBAD.csv", [HEADER] + [BAD_ROW, BAD_ROW])
        proc = subprocess.run(
            [
                sys.executable,
                "-m",
                "scripts.repair_corpus",
                "--data-dir",
                str(tmp_path),
                "--no-backup",
            ],
            capture_output=True,
            text=True,
            cwd=Path(__file__).resolve().parent.parent,
            timeout=120,
        )
        assert proc.returncode == 1
        assert "manual attention" in proc.stdout

    def test_cli_repairs_and_backs_up(self, tmp_path):
        _write(tmp_path / "ADBL.csv", [HEADER] + GOOD_ROWS + [BAD_ROW])
        backup = tmp_path / "bk"
        proc = subprocess.run(
            [
                sys.executable,
                "-m",
                "scripts.repair_corpus",
                "--data-dir",
                str(tmp_path),
                "--backup-dir",
                str(backup),
            ],
            capture_output=True,
            text=True,
            cwd=Path(__file__).resolve().parent.parent,
            timeout=120,
        )
        assert proc.returncode == 0
        assert "2026-07-24" not in (tmp_path / "ADBL.csv").read_text()
        backed = list(backup.rglob("ADBL.csv"))
        assert len(backed) == 1
        assert BAD_ROW in backed[0].read_text()


# ---------------------------------------------------------------------------
# Contract integration: a repaired corpus passes the quality gate
# ---------------------------------------------------------------------------


class TestQualityIntegration:
    def test_repaired_corpus_passes_quality_gate(self, tmp_path):
        from src.data.quality import validate_corpus

        _write(tmp_path / "ADBL.csv", [HEADER] + GOOD_ROWS + [BAD_ROW])
        _write(tmp_path / "NABIL.csv", [HEADER] + GOOD_ROWS)
        assert validate_corpus(tmp_path)["invalid"] == 1
        repair_corpus(tmp_path, backup_dir=tmp_path / "bk", today=TODAY)
        agg = validate_corpus(tmp_path)
        # The contract under test: no symbol stays quarantined.  Assert on
        # invalid == 0 and the total classified, NOT ``valid == 2`` —
        # ``validate_corpus`` measures freshness against the real wall
        # clock (a fixed TODAY here would make this test time-bombed once
        # the rows age past DATA_STALE_AFTER_DAYS and become SUSPICIOUS).
        assert agg["invalid"] == 0
        assert agg["valid"] + agg["suspicious"] == 2
