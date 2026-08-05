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

from src.config import DATA_DIRECTORY

_NUMERIC_COLUMNS = ["Open", "High", "Low", "Close", "Volume"]


def _parse_schema_csv(lines: list[str], ncols: int) -> pd.DataFrame:
    """Parse CSV lines into a DataFrame, merging overflow fields.

    Handles OHLCV files whose final column (e.g. Volume) is written
    with unquoted thousands separators (``1,200,000``): the overflow
    fields are merged back into the final column so pandas-style
    column misalignment cannot occur.
    """
    rows: list[list[str]] = []
    for row in csv.reader(lines):
        if not any(field.strip() for field in row):
            continue
        if len(row) > ncols:
            row = row[: ncols - 1] + ["".join(row[ncols - 1 :])]
        elif len(row) < ncols:
            row = row + [""] * (ncols - len(row))
        rows.append(row)
    if not rows:
        return pd.DataFrame()
    return pd.DataFrame(rows[1:], columns=rows[0])


def _looks_like_ohlcv(lines: list[str]) -> bool:
    """True when the header names a Close column (OHLCV-shaped file)."""
    return bool(lines) and "close" in lines[0].lower()


def _read_csv_robust(path: Path) -> pd.DataFrame:
    """Read an OHLCV CSV, tolerating unquoted thousands separators.

    Well-formed files are parsed by pandas directly.  Files whose
    rows contain more fields than the header (e.g. volumes written as
    ``1,200,000`` without quotes) are re-parsed line-by-line with the
    overflow merged into the final column.  Genuinely malformed
    non-OHLCV files still raise (preserving existing skip behaviour).
    """
    text = path.read_text(encoding="utf-8-sig", errors="replace")
    lines = text.splitlines()
    if not lines:
        return pd.DataFrame()
    if _looks_like_ohlcv(lines):
        return _parse_schema_csv(lines, len(lines[0].split(",")))
    return pd.read_csv(io.StringIO(text))



def load_csv(file_path: str | Path) -> pd.DataFrame:
    """Load and clean an OHLCV CSV file into a sorted DataFrame.

    This is the single canonical CSV parsing path for the platform —
    do not reimplement this logic elsewhere.

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

            df[col] = (
                df[col]
                .astype(str)
                .str.replace(",", "", regex=False)
            )

            df[col] = pd.to_numeric(
                df[col],
                errors="coerce"
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


    return df


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