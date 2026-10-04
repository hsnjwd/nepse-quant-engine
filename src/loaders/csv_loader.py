"""Canonical CSV loading for the NEPSE Quant Engine.

This module is the single authoritative CSV parsing path. Every
component that reads OHLCV history from CSV files must go through
``load_csv`` (or ``resolve_stock_csv_path`` for path resolution) — do
not reimplement CSV parsing elsewhere.

- ``load_csv`` — parse, clean, and sort an OHLCV CSV file.
- ``resolve_stock_csv_path`` — resolve a symbol to its CSV file path
  (``None`` when the file does not exist).
"""

from __future__ import annotations

import csv
import io
from pathlib import Path

import pandas as pd
from pandas.errors import ParserError

from src.cache.scanner_cache import scanner_cache
from src.config import DATA_DIRECTORY

_NUMERIC_COLUMNS = ["Open", "High", "Low", "Close", "Volume"]


def _find_header_idx(lines: list[str], max_scan: int = 25) -> int:
    """Index of the OHLCV header row within the first *max_scan* lines.

    Returns the first line that names a ``close`` column; if none is
    found, falls back to the first non-empty line.  Scraped files
    sometimes begin with a blank or title line before the real column
    row, so detection must not assume line 0 is the header.
    """
    for i, line in enumerate(lines[:max_scan]):
        if line.strip() and "close" in line.lower():
            return i
    for i, line in enumerate(lines[:max_scan]):
        if line.strip():
            return i
    return 0


def _parse_schema_csv(lines: list[str], header_idx: int, ncols: int) -> pd.DataFrame:
    """Parse CSV lines into a DataFrame, merging overflow fields.

    Handles OHLCV files whose final column (e.g. Volume) is written
    with unquoted thousands separators (``1,200,000``): the overflow
    fields are merged back into the final column so pandas-style
    column misalignment cannot occur.  The row at *header_idx* is used
    as the column header; all other non-empty rows become data.
    """
    rows: list[list[str]] = []
    header_fields: list[str] | None = None
    for i, raw in enumerate(lines):
        # Scraped NEPSE files occasionally contain stray double-quote
        # characters (e.g. around thousands separators).  Numeric OHLCV
        # data never needs them, and an unbalanced quote makes both
        # ``csv.reader`` and pandas' C parser abort the whole file — so
        # strip them before splitting.
        line = raw.replace('"', "")
        if not line.strip():
            continue
        if i < header_idx:
            # Title/comment rows that appear before the real column row
            # are not data — skip them entirely.
            continue
        fields = line.split(",")
        if len(fields) > ncols:
            fields = fields[: ncols - 1] + ["".join(fields[ncols - 1 :])]
        elif len(fields) < ncols:
            fields = fields + [""] * (ncols - len(fields))
        if i == header_idx:
            header_fields = fields
            continue
        rows.append(fields)
    if header_fields is None:
        if not rows:
            return pd.DataFrame()
        header_fields = rows.pop(0)
    return pd.DataFrame(rows, columns=header_fields)


def _looks_like_ohlcv(lines: list[str], header_idx: int | None = None) -> bool:
    """True when the header names a Close column (OHLCV-shaped file)."""
    idx = _find_header_idx(lines) if header_idx is None else header_idx
    return bool(lines) and "close" in lines[idx].lower()


def _rows_match_header(lines: list[str], header_idx: int, ncols: int) -> bool:
    """True when every non-empty data row has exactly *ncols* fields.

    Gates the pandas C-parser fast path: a ragged row (e.g. an unquoted
    thousands separator like ``1,200,000``) can make pandas *silently*
    misalign columns instead of raising ``ParserError``, so the fast
    path is only attempted when every data row's raw comma count matches
    the header.  Quoted commas (``"1,100,000"``) inflate the raw count
    and therefore also fall through to the tolerant parser — matching
    the pre-Sprint-11.6 behaviour for every comma-formatting file.
    """
    for line in lines[header_idx + 1 :]:
        if line.strip() and line.count(",") + 1 != ncols:
            return False
    return True


def _read_csv_robust(path: Path) -> pd.DataFrame:
    """Read an OHLCV CSV, tolerating unquoted thousands separators.

    Well-formed files are parsed by pandas directly.  Files whose
    rows contain more fields than the header (e.g. volumes written as
    ``1,200,000`` without quotes) are re-parsed line-by-line with the
    overflow merged into the final column.  Genuinely malformed
    non-OHLCV files still raise (preserving existing skip behaviour).

    Sprint 11.6 fast path: OHLCV-shaped files whose rows all match the
    header's field count are handed to pandas' C parser first
    (``skiprows`` moves past any title/blank rows).  The C parser
    returns already-numeric columns, which makes the per-column
    ``str.contains``/``pd.to_numeric`` pass in :func:`load_csv` a pure
    no-op — measured ~36% of the cold load on the real corpus (287
    scraped files, 0 of which actually contain commas).  Any file with
    comma formatting, ragged rows, unbalanced quotes or a column-count
    mismatch falls back to the tolerant line-by-line schema parser, so
    the observable output is unchanged for every input the schema path
    could handle (verified value-identical across all 286 real files
    and the Phase 6 fixture set).
    """
    text = path.read_text(encoding="utf-8-sig", errors="replace")
    lines = text.splitlines()
    if not lines:
        return pd.DataFrame()
    header_idx = _find_header_idx(lines)
    # Column count must be inferred from the same quote-stripped form
    # _parse_schema_csv splits, so a stray quote in the header row can
    # never make the two disagree.
    header_clean = lines[header_idx].replace('"', "")
    ncols = len(header_clean.split(","))
    if _looks_like_ohlcv(lines, header_idx):
        # Only attempt the C parser for *trivially clean* files: every
        # data row matches the header field count AND no row (header
        # included) contains a quote character.  Pre-11.6 the schema
        # parser stripped ALL quotes (``line.replace('"', '')``), so any
        # file with a quote anywhere — even a stray mid/end-field quote
        # with no comma (``1000"``) that the comma gate cannot see, or
        # a quote tainting a header column name — takes the schema
        # path, guaranteeing identical output by construction.
        if _rows_match_header(lines, header_idx, ncols) and not any(
            '"' in row for row in lines[header_idx:]
        ):
            try:
                fast = pd.read_csv(io.StringIO(text), skiprows=header_idx)
                # Column-count guard: a stray quote in the header row
                # could make the C parser see a different number of
                # fields than the quote-stripped split — fall back
                # rather than guess.
                if len(fast.columns) == ncols:
                    return fast
            except (ParserError, ValueError, csv.Error):
                pass
        return _parse_schema_csv(lines, header_idx, ncols)
    try:
        return pd.read_csv(io.StringIO(text))
    except (ParserError, ValueError, csv.Error):
        # pandas' strict parser rejected the file (e.g. an unbalanced
        # quote in scraped data) — fall back to the tolerant line-by-line
        # parser instead of failing the whole file.
        return _parse_schema_csv(lines, header_idx, ncols)



def load_csv(file_path: str | Path) -> pd.DataFrame:
    """Load and clean an OHLCV CSV file into a sorted DataFrame.

    This is the single canonical CSV parsing path for the platform —
    do not reimplement this logic elsewhere.

    Parsed results are cached by file fingerprint (path, mtime, size)
    so an unchanged file is never re-parsed; see
    :class:`src.cache.scanner_cache.ScannerCache` for the cache policy.
    Callers must treat the returned frame as read-only.

    Args:
        file_path: Path to the source CSV file.

    Returns:
        Cleaned OHLCV DataFrame sorted by date.

    Raises:
        FileNotFoundError: If the CSV file does not exist.
    """

    path = Path(file_path)
    if not path.exists():
        raise FileNotFoundError(f"CSV file not found: {path}")

    cached = scanner_cache.get_dataframe(path)
    if cached is not None:
        return cached

    df = _read_csv_robust(path)


    # Clean column names
    df.columns = df.columns.str.strip()


    # Convert numeric columns
    numeric_columns = [
        "Open",
        "High",
        "Low",
        "Close",
        "Volume"
    ]


    for col in numeric_columns:

        if col in df.columns:

            s = df[col]

            # Fast paths (Sprint 11.1, measured):
            # 1. Already-numeric columns are passed through untouched —
            #    numeric dtypes can never contain thousands separators.
            # 2. Object columns without commas convert directly via
            #    ``pd.to_numeric`` — the expensive ``astype(str) ->
            #    str.replace -> to_numeric`` round-trip only runs when a
            #    column actually contains thousands separators.
            # Clean A/B on 10 files x 300 rows: 54.3 ms -> 43.0 ms best
            # (~1.26x) on the conversion loop; values are identical
            # (regression-tested in tests/test_cache_correctness.py).
            if s.dtype.kind in "iuf":
                continue

            if s.dtype.kind == "O":
                # Object columns come from the schema parser and hold
                # strings (or NaN).  Only run the expensive string
                # round-trip when a column actually contains thousands
                # separators.
                if s.str.contains(",", regex=False).any():
                    s = s.astype(str).str.replace(",", "", regex=False)
                df[col] = pd.to_numeric(s, errors="coerce")
                continue

            # Any other dtype (bool, datetime, ...): preserve historical
            # string round-trip behaviour for strict value identity.
            df[col] = pd.to_numeric(
                df[col].astype(str).str.replace(",", "", regex=False),
                errors="coerce",
            )


    # Sort by date
    if "Date" in df.columns:

        df["Date"] = pd.to_datetime(df["Date"], errors="coerce")

        df = df.sort_values(
            "Date"
        )


    # Remove invalid rows

    df = df.dropna(
        subset=["Close"]
    )

    scanner_cache.put_dataframe(path, df)

    # Return a copy on the miss path too: the caller must never be able
    # to mutate the frame that is now stored in the scanner cache
    # (Sprint 11.1 cache correctness — same guarantee as the warm path).
    return df.copy()


def resolve_stock_csv_path(
    symbol: str,
    data_dir: str | Path | None = None,
) -> Path | None:
    """Resolve *symbol* to its CSV file path under the data directory.

    Returns ``None`` when the file does not exist (callers decide how
    to surface that — e.g. HTTP 404). Symbols are lower-cased so that
    ``NABIL`` and ``nabil`` resolve to the same file.

    Args:
        symbol: Stock symbol (case-insensitive).
        data_dir: Optional override for ``DATA_DIRECTORY``.

    Returns:
        The resolved ``Path``, or ``None`` when the CSV is missing.
    """
    base = Path(data_dir) if data_dir is not None else Path(DATA_DIRECTORY)
    path = base / f"{symbol.strip().lower()}.csv"
    return path if path.exists() else None


__all__ = ["load_csv", "resolve_stock_csv_path"]