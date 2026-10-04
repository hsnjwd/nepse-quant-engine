"""Sprint 11.6 — Cold-path CSV fast-path correctness tests.

Sprint 11.6 added a pandas C-parser fast path to ``_read_csv_robust``:
OHLCV-shaped files are parsed by pandas directly (``skiprows`` past any
title/blank rows) and fall back to the tolerant line-by-line schema
parser only when the C parser fails or the column count disagrees.

These tests pin the invariant that the fast path is *value-identical*
to the tolerant schema path for every input the schema path can handle
— same columns, same dtypes, same values, same dates, same row counts,
same NaN locations.  The full 286-file real corpus is checked via a
representative sample (the complete corpus check runs in the benchmark
suite, not in CI); the fixture set covers the Phase 6 edge cases.
"""

from __future__ import annotations

import csv as _csv
import io
from pathlib import Path

import pandas as pd
import pytest
from pandas.errors import ParserError

from src.loaders import csv_loader
from src.loaders.csv_loader import (
    _find_header_idx,
    _parse_schema_csv,
    _rows_match_header,
    load_csv,
)

# ───────────────────────────────────────────────────────────────────
# Helpers
# ───────────────────────────────────────────────────────────────────


def _schema_only_read(path: Path) -> pd.DataFrame:
    """Reproduce the *pre-Sprint-11.6* read path exactly.

    OHLCV files always went through the tolerant schema parser; the C
    parser was only used for non-OHLCV files (with the schema fallback
    on failure).  Used as the reference implementation the fast path
    must match.
    """
    text = path.read_text(encoding="utf-8-sig", errors="replace")
    lines = text.splitlines()
    if not lines:
        return pd.DataFrame()
    hi = _find_header_idx(lines)
    header_clean = lines[hi].replace('"', "")
    ncols = len(header_clean.split(","))
    if csv_loader._looks_like_ohlcv(lines, hi):
        return _parse_schema_csv(lines, hi, ncols)
    try:
        return pd.read_csv(io.StringIO(text))
    except (ParserError, ValueError, _csv.Error):
        return _parse_schema_csv(lines, hi, ncols)


def _normalize(df: pd.DataFrame) -> pd.DataFrame:
    """Mirror load_csv's post-parse pipeline (strip, numerics, dates).

    Empty and non-OHLCV frames are returned unchanged (load_csv's
    OHLCV-only steps are skipped when the columns are absent) so the
    comparison focuses on the parse stage, which is what the fast path
    changes.
    """
    df = df.copy()
    if df.empty or len(df.columns) == 0:
        return df
    df.columns = df.columns.str.strip()
    for col in ("Open", "High", "Low", "Close", "Volume"):
        if col not in df.columns:
            continue
        s = df[col]
        if s.dtype.kind in "iuf":
            continue
        if s.dtype.kind == "O":
            if s.str.contains(",", regex=False).any():
                s = s.astype(str).str.replace(",", "", regex=False)
            df[col] = pd.to_numeric(s, errors="coerce")
        else:
            df[col] = pd.to_numeric(
                df[col].astype(str).str.replace(",", "", regex=False),
                errors="coerce",
            )
    if "Date" in df.columns:
        df["Date"] = pd.to_datetime(df["Date"], errors="coerce")
        df = df.sort_values("Date")
    if "Close" in df.columns:
        df = df.dropna(subset=["Close"])
    return df


def _assert_identical(new: pd.DataFrame, old: pd.DataFrame, label: str) -> None:
    pd.testing.assert_frame_equal(
        _normalize(new),
        _normalize(old),
        check_dtype=True,
        check_exact=True,
        obj=label,
    )


# ───────────────────────────────────────────────────────────────────
# Phase 6 edge-case fixtures — fast path vs schema path
# ───────────────────────────────────────────────────────────────────


class TestFastPathVsSchemaPath:
    """C-parser fast path output must equal the schema path output."""

    @pytest.mark.parametrize(
        "name,text",
        [
            ("basic", "Date,Open,High,Low,Close,Volume\n2024-01-01,100,105,95,102,1000\n2024-01-02,101,106,96,103,1100\n"),
            ("quoted_comma", 'Date,Open,High,Low,Close,Volume\n2024-01-01,100,105,95,102,"1,200,000"\n2024-01-02,101,106,96,103,"1,300,000"\n'),
            ("title_line", "NABBC - Daily OHLCV\nDate,Open,High,Low,Close,Volume\n2024-01-01,100,105,95,102,1000\n"),
            ("blank_lines", "\n\nDate,Open,High,Low,Close,Volume\n2024-01-01,100,105,95,102,1000\n"),
            ("unbalanced_quote", 'Date,Open,High,Low,Close,Volume\n2024-01-01,100,105,95,102,"1,200,000\n'),
            ("missing_values", "Date,Open,High,Low,Close,Volume\n2024-01-01,100,,95,102,1000\n2024-01-02,,105,96,103,\n"),
            ("empty", ""),
            ("only_header", "Date,Open,High,Low,Close,Volume\n"),
            ("non_ohlcv", "name,value\nfoo,1\nbar,2\n"),
            ("int_cols", "Date,Open,High,Low,Close,Volume\n2024-01-01,100,105,95,102,1000\n"),
            ("float_cols", "Date,Open,High,Low,Close,Volume\n2024-01-01,100.5,105.5,95.5,102.25,1000.75\n"),
            ("dup_rows", "Date,Open,High,Low,Close,Volume\n2024-01-01,100,105,95,102,1000\n2024-01-01,100,105,95,102,1000\n"),
            ("crlf", "Date,Open,High,Low,Close,Volume\r\n2024-01-01,100,105,95,102,1000\r\n"),
            ("unquoted_comma", "Date,Open,High,Low,Close,Volume\n2024-01-01,100,105,95,102,1,200,000\n2024-01-02,101,106,96,103,1,300,000\n"),
            ("na_literal", "Date,Open,High,Low,Close,Volume\n2024-01-01,100,NA,95,102,1000\n2024-01-02,101,106,96,NA,1100\n"),
            ("nan_literal", "Date,Open,High,Low,Close,Volume\n2024-01-01,100,NaN,95,102,1000\n2024-01-02,101,106,96,NaN,1100\n"),
            ("null_literal", "Date,Open,High,Low,Close,Volume\n2024-01-01,100,NULL,95,102,1000\n2024-01-02,101,106,96,NULL,1100\n"),
            ("header_quote", 'Date,Open,High,Low,Close,Vol"ume\n2024-01-01,100,105,95,102,1000\n'),
        ],
    )
    def test_fast_matches_schema(self, tmp_path: Path, name: str, text: str) -> None:
        path = tmp_path / f"{name}.csv"
        path.write_text(text, encoding="utf-8")
        new = csv_loader._read_csv_robust(path)
        old = _schema_only_read(path)
        _assert_identical(new, old, f"{name}: fast vs schema")

    def test_load_csv_end_to_end_identical(self, tmp_path: Path) -> None:
        """The public load_csv path still matches the schema reference."""
        path = tmp_path / "SYN.csv"
        path.write_text(
            "Date,Open,High,Low,Close,Volume\n"
            "2024-01-02,101,106,96,103,1100\n"
            "2024-01-01,100,105,95,102,1000\n",
            encoding="utf-8",
        )
        loaded = load_csv(path)
        reference = _normalize(_schema_only_read(path))
        pd.testing.assert_frame_equal(
            _normalize(loaded),
            reference,
            check_dtype=True,
            check_exact=True,
        )

    def test_unquoted_thousands_separator_still_correct(self, tmp_path: Path) -> None:
        """Unquoted ``1,200,000`` must parse to 1_200_000 (regression gate).

        The pandas C parser silently misaligns columns on unquoted
        thousands separators instead of raising (verified: pandas 2.2.3
        returns ``Close=[200, 0, 100]`` with no error).  The fast path
        is therefore gated on ``_rows_match_header`` so ragged files
        always take the tolerant schema parser — this test pins the
        exact regression the gate exists for.
        """
        path = tmp_path / "COMMA.csv"
        path.write_text(
            "Date,Open,High,Low,Close,Volume\n"
            "2025-01-03,106,110,102,108,1,200,000\n"
            "2025-01-02,103,108,98,106,1,100,000\n"
            "2025-01-01,100,105,95,102,1,000,000\n",
            encoding="utf-8",
        )
        df = load_csv(path)
        assert list(df["Close"]) == [102.0, 106.0, 108.0]
        assert list(df["Volume"]) == [1_000_000, 1_100_000, 1_200_000]

    def test_fast_path_is_actually_taken(self, tmp_path: Path) -> None:
        """A clean OHLCV file must hit the C parser, not silently fall back.

        The schema parser returns object-dtype columns; the C parser
        returns numeric (iuf) columns directly.  Asserting iuf dtypes
        after ``_read_csv_robust`` pins that the fast path is live — if
        pandas ever starts failing on clean files, this test turns red
        instead of silently losing the ~1.4x cold-load win.
        """
        path = tmp_path / "CLEAN.csv"
        path.write_text(
            "Date,Open,High,Low,Close,Volume\n"
            "2024-01-01,100.5,105,95,102.25,1000\n"
            "2024-01-02,101.5,106,96,103.5,1100\n",
            encoding="utf-8",
        )
        df = csv_loader._read_csv_robust(path)
        for col in ("Open", "High", "Low", "Close", "Volume"):
            assert df[col].dtype.kind in "iuf", (
                f"fast path not taken: {col} is object dtype"
            )

    def test_rows_match_header_gate(self, tmp_path: Path) -> None:
        """``_rows_match_header`` rejects ragged and comma-formatted files."""
        clean = ["Date,Open,High,Low,Close,Volume", "2024-01-01,100,105,95,102,1000"]
        assert _rows_match_header(clean, 0, 6) is True
        ragged = [
            "Date,Open,High,Low,Close,Volume",
            "2024-01-01,100,105,95,102,1,200,000",
        ]
        assert _rows_match_header(ragged, 0, 6) is False
        quoted = [
            "Date,Open,High,Low,Close,Volume",
            '2024-01-01,100,105,95,102,"1,200,000"',
        ]
        assert _rows_match_header(quoted, 0, 6) is False
        blank_lines = ["", "Date,Open,High,Low,Close,Volume", "2024-01-01,100,105,95,102,1000"]
        assert _rows_match_header(blank_lines, 1, 6) is True


# ───────────────────────────────────────────────────────────────────
# Real-corpus sample — the invariant that actually matters
# ───────────────────────────────────────────────────────────────────


class TestRealCorpusSample:
    """A representative sample of the shipped scraped corpus stays identical."""

    @pytest.fixture(scope="class")
    def corpus_sample(self) -> list[Path]:
        raw = Path(__file__).resolve().parent.parent / "data" / "raw"
        files = sorted(
            p for p in raw.glob("*.csv")
            if p.stem.lower() not in {"sample", "rolling_volume_mean"}
        )
        # Deterministic, cheap sample (10 of ~286) — the full corpus is
        # checked in the benchmark suite.
        return [files[i] for i in range(0, len(files), max(1, len(files) // 10))]

    def test_sample_identical(self, corpus_sample: list[Path]) -> None:
        mismatches: list[str] = []
        for path in corpus_sample:
            new = csv_loader._read_csv_robust(path)
            old = _schema_only_read(path)
            try:
                _assert_identical(new, old, path.name)
            except AssertionError:
                mismatches.append(path.name)
        assert not mismatches, f"Fast path diverged on: {mismatches}"

    def test_sample_loads_through_public_api(self, corpus_sample: list[Path]) -> None:
        for path in corpus_sample:
            df = load_csv(path)
            assert not df.empty
            assert {"Date", "Open", "High", "Low", "Close", "Volume"} <= set(df.columns)
            assert df["Close"].notna().all()
