"""Real-corpus audit & validation for the NEPSE Quant Engine (Sprint 12.2, Phase 4).

Deterministic, read-only validation of the OHLCV CSV corpus under
``data/raw`` (287 scraped files at the time of writing).  The audit
answers the Sprint 12.2 corpus questions without touching production
state:

- how many CSV files exist (and how many are usable)
- duplicate symbols (case-insensitive stems)
- malformed / unparsable files
- files with too-few rows (insufficient history)
- schema problems (missing OHLCV columns)
- chronological ordering (dates must be ascending after ``load_csv``)
- OHLC sanity (High >= max(Open, Close), Low <= min(Open, Close),
  positive Close, non-negative Volume)
- date coverage (per-file first/last date)

Design:

- **Pure and testable** — every check is a small function that takes a
  path or DataFrame and returns a structured result dict; the suite is
  exercised by ``tests/test_sprint12_2.py`` against synthetic corpora.
- **Read-only** — the audit only opens files for reading.  It never
  writes into ``data/raw``, ``data/alerts`` or any other production
  directory.  ``audit_corpus`` takes an explicit ``raw_dir``.
- **Loader-independent** — the audit reads each file with the stdlib
  ``csv`` module (not ``load_csv``), so it can report *schema-level*
  problems the canonical loader would silently clean (ragged rows,
  non-numeric OHLCV, quoted commas).  The loader remains the authority
  for what the engine consumes; the audit describes the raw file.
- **Deterministic** — file order is sorted; the report is JSON-able.

The Sprint 12.2 conclusion (documented in ``docs/PERFORMANCE_BASELINE.md``
§17) is that the two configured upstream history sources were both
unreachable at audit time (``nepse_scraper`` -> HTTP 403,
``github_datasets`` -> HTTP 404), so the corpus could **not** be safely
expanded from live data and no data was fabricated.  This audit is the
evidence base for the current-corpus section of the report.
"""

from __future__ import annotations

import csv
import sys
from dataclasses import dataclass, field
from datetime import date as _date
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

DEFAULT_RAW_DIR = Path(__file__).resolve().parent.parent / "data" / "raw"

REQUIRED_COLUMNS = ["Date", "Open", "High", "Low", "Close", "Volume"]

# A file with fewer than this many data rows cannot support the longest
# indicator window (SMA_200 needs 200+ rows; the pipeline tolerates
# shorter series but the analysis is degenerate).  Sprint 12.1's corpus
# analysis used 5 as the "very few rows" threshold.
MIN_USABLE_ROWS = 5


@dataclass
class FileAudit:
    """Structured audit result for a single corpus file."""

    symbol: str = ""
    path: str = ""
    ok: bool = True
    rows: int = 0
    columns: list[str] = field(default_factory=list)
    first_date: str = ""
    last_date: str = ""
    sorted_dates: bool = True
    ohlc_sane: bool = True
    problems: list[str] = field(default_factory=list)


def _read_rows(path: Path) -> tuple[list[str] | None, list[list[str]]]:
    """Read *path* as CSV, returning ``(header, rows)`` or ``(None, [])``."""
    try:
        with path.open("r", encoding="utf-8-sig", errors="replace", newline="") as f:
            reader = csv.reader(f)
            all_rows = [r for r in reader if any(cell.strip() for cell in r)]
    except (OSError, csv.Error) as exc:
        return None, []
    if not all_rows:
        return None, []
    return all_rows[0], all_rows[1:]


def audit_csv_file(path: Path, symbol: str | None = None) -> FileAudit:
    """Audit a single OHLCV CSV file (read-only, deterministic).

    Uses the raw csv module (not the canonical loader) so the audit is
    independent of the loader's cleaning and can report *schema-level*
    problems the loader would silently fix.  A file that fails to parse
    is reported with ``ok=False`` and its problem listed, never raised.
    """
    audit = FileAudit(symbol=symbol or path.stem.lower(), path=str(path))
    header, rows = _read_rows(path)

    if header is None:
        audit.ok = False
        audit.problems.append("unreadable-or-empty")
        return audit

    header = [c.strip() for c in header]
    audit.columns = header
    audit.rows = len(rows)

    missing = [c for c in REQUIRED_COLUMNS if c not in header]
    if missing:
        audit.ok = False
        audit.problems.append(f"missing-columns:{','.join(missing)}")
        return audit

    if audit.rows < MIN_USABLE_ROWS:
        audit.ok = False
        audit.problems.append(f"too-few-rows:{audit.rows}")

    # Column indices (canonical names).
    idx = {c: header.index(c) for c in REQUIRED_COLUMNS}

    dates: list[str] = []
    non_numeric = False
    for row in rows:
        # Record the date cell regardless of numeric health so an
        # all-non-numeric file (e.g. the quoted sample.csv) still
        # reports first/last date and ordering.  Guarded: a ragged data
        # row (fewer fields than the header) must never raise — the
        # audit's documented contract is "every file is reported, not
        # thrown".
        try:
            date_cell = row[idx["Date"]].strip()
        except IndexError:
            date_cell = ""
        if date_cell:
            dates.append(date_cell)
        try:
            o = float(row[idx["Open"]])
            h = float(row[idx["High"]])
            l = float(row[idx["Low"]])
            c = float(row[idx["Close"]])
            v = float(row[idx["Volume"]])
        except (ValueError, IndexError):
            # A non-numeric / unparsable OHLCV field (e.g. quoted
            # thousands separators) is distinct from a *logic* violation
            # (High < Low etc.) — report it separately so the audit does
            # not mislabel scraped-format quirks as bad prices.
            non_numeric = True
            continue
        if not (h >= o and h >= c and l <= o and l <= c and c > 0 and v >= 0):
            audit.ohlc_sane = False

    if non_numeric:
        audit.problems.append("non-numeric")
    if not audit.ohlc_sane:
        audit.problems.append("ohlc-insanity")

    # Chronological ordering: dates must be ascending.  (The corpus is
    # scraped newest-first; ``load_csv`` sorts ascending, so the audit
    # reports the raw order without implying the loader is wrong.)
    parsed_dates: list[Any] = []
    for d in dates:
        try:
            y, m, day = (int(p) for p in d.split("-")[:3])
            parsed_dates.append(_date(y, m, day))
        except (ValueError, AttributeError):
            continue
    if parsed_dates:
        audit.first_date = parsed_dates[0].isoformat()
        audit.last_date = parsed_dates[-1].isoformat()
        if parsed_dates != sorted(parsed_dates):
            audit.sorted_dates = False
            audit.problems.append("dates-not-ascending")

    if not audit.problems:
        audit.ok = True
    return audit


def find_duplicate_symbols(symbols: list[str]) -> list[str]:
    """Return the sorted list of lowercase symbols that appear more than once.

    Pure helper (no filesystem access) so duplicate detection is testable
    on any platform — including Windows, where ``NABIL.csv`` and
    ``nabil.csv`` cannot physically coexist on a case-insensitive
    filesystem.
    """
    seen: dict[str, int] = {}
    for s in symbols:
        seen[s] = seen.get(s, 0) + 1
    return sorted(s for s, n in seen.items() if n > 1)


def audit_corpus(raw_dir: Path = DEFAULT_RAW_DIR) -> dict[str, Any]:
    """Audit every CSV under *raw_dir* (read-only).

    Returns a JSON-able report dict: ``file_count``, ``usable``,
    ``duplicates`` (list of duplicate lowercase stems), per-file
    ``files`` (list of :class:`FileAudit` dicts) and aggregate counters
    (``unusable``, ``too_few_rows``, ``ohlc_issues``, ``order_issues``).
    Never raises for a malformed file — every file is reported, not
    thrown.
    """
    raw_dir = Path(raw_dir)
    paths = sorted(raw_dir.glob("*.csv"))

    audits: list[FileAudit] = []
    for p in paths:
        audits.append(audit_csv_file(p))

    duplicates = find_duplicate_symbols([a.symbol for a in audits])

    usable = sum(1 for a in audits if a.ok)
    return {
        "raw_dir": str(raw_dir),
        "file_count": len(audits),
        "usable": usable,
        "duplicates": duplicates,
        "unusable": sum(1 for a in audits if not a.ok),
        "too_few_rows": sum(1 for a in audits if any(p.startswith("too-few-rows") for p in a.problems)),
        "non_numeric": sum(1 for a in audits if "non-numeric" in a.problems),
        "ohlc_issues": sum(1 for a in audits if "ohlc-insanity" in a.problems),
        "order_issues": sum(1 for a in audits if "dates-not-ascending" in a.problems),
        "files": [a.__dict__ for a in audits],
    }


def format_audit(report: dict[str, Any]) -> str:
    """Render an audit report as a short human-readable summary."""
    lines = [
        f"corpus audit: {report['raw_dir']}",
        f"  files         : {report['file_count']}",
        f"  usable        : {report['usable']}",
        f"  unusable      : {report['unusable']}",
        f"  duplicates    : {report['duplicates'] or 'none'}",
        f"  too-few-rows  : {report['too_few_rows']}",
        f"  non-numeric   : {report['non_numeric']}",
        f"  ohlc issues   : {report['ohlc_issues']}",
        f"  order issues  : {report['order_issues']}",
    ]
    for a in report["files"]:
        if not a["ok"]:
            lines.append(f"  ! {a['symbol']}: {','.join(a['problems'])}")
    return "\n".join(lines)


def main() -> int:
    import argparse
    import json

    parser = argparse.ArgumentParser(description="Audit the real NEPSE corpus (read-only)")
    parser.add_argument("--raw", type=str, default=str(DEFAULT_RAW_DIR))
    parser.add_argument("--out", type=str, default="", help="Optional JSON output path")
    args = parser.parse_args()

    report = audit_corpus(Path(args.raw))
    print(format_audit(report))
    if args.out:
        Path(args.out).write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(f"report written: {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
