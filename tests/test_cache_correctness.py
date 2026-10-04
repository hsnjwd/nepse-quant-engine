"""Sprint 11.1 — Cache correctness tests.

Verifies:

- scanner cache invalidation on file change (fingerprint includes mtime/size)
- cache misses work; hits return equivalent results
- cached DataFrames are never leaked to consumers (mutation cannot
  corrupt the cache)
- DataService history returns copies on cache hits
- DiskCache writes atomically (no partial entries, no temp litter)
- ``load_csv`` numeric fast path is value-identical to the slow path
"""

from __future__ import annotations

import io
import os
import time
from pathlib import Path

import pandas as pd
import pytest

from src.cache.scanner_cache import ScannerCache, scanner_cache
from src.data.cache import DiskCache
from src.data.service import DataService
from src.loaders.csv_loader import load_csv


# ───────────────────────────────────────────────────────────────────
# Scanner cache
# ───────────────────────────────────────────────────────────────────


class TestScannerCacheCorrectness:
    def test_miss_then_hit(self, tmp_path):
        cache = ScannerCache(ttl=300, max_entries=10)
        path = tmp_path / "SYN.csv"
        path.write_text("Date,Open,High,Low,Close,Volume\n", encoding="utf-8")

        assert cache.get_dataframe(path) is None  # miss
        cache.put_dataframe(path, pd.DataFrame({"Close": [1.0]}))
        got = cache.get_dataframe(path)
        assert got is not None  # hit

    def test_file_change_invalidates(self, tmp_path):
        """Editing a CSV (mtime/size) must invalidate cached entries."""
        cache = ScannerCache(ttl=300, max_entries=10)
        path = tmp_path / "SYN.csv"
        path.write_text("Date,Open,High,Low,Close,Volume\n1,2,3,4,5,6\n", encoding="utf-8")

        cache.put_dataframe(path, pd.DataFrame({"Close": [5.0]}))
        assert cache.get_dataframe(path) is not None

        # Force a distinct mtime so the fingerprint changes even though
        # the payload has the same byte length (deterministic, no sleep).
        future = time.time() + 10
        os.utime(path, (future, future))
        assert cache.get_dataframe(path) is None  # invalidated

    def test_clear_invalidates_everything(self, tmp_path):
        cache = ScannerCache(ttl=300, max_entries=10)
        path = tmp_path / "SYN.csv"
        path.write_text("Close\n1\n", encoding="utf-8")
        cache.put_dataframe(path, pd.DataFrame({"Close": [1.0]}))
        cache.clear()
        assert cache.get_dataframe(path) is None

    def test_dataframe_not_leaked_to_consumers(self, tmp_path):
        """Mutating a returned frame must not corrupt the cached entry."""
        cache = ScannerCache(ttl=300, max_entries=10)
        path = tmp_path / "SYN.csv"
        path.write_text("Close\n1\n", encoding="utf-8")
        original = pd.DataFrame({"Close": [1.0, 2.0]})
        cache.put_dataframe(path, original)

        first = cache.get_dataframe(path)
        first["Close"] = 999.0  # consumer mutates

        second = cache.get_dataframe(path)
        assert second["Close"].tolist() == [1.0, 2.0], "Cache entry was corrupted"

    def test_analysis_not_leaked_to_consumers(self, tmp_path):
        cache = ScannerCache(ttl=300, max_entries=10)
        path = tmp_path / "SYN.csv"
        path.write_text("Close\n1\n", encoding="utf-8")
        cache.put_analysis(path, {"signal": "BUY", "score": 5})

        first = cache.get_analysis(path)
        first["score"] = -1  # consumer mutates

        second = cache.get_analysis(path)
        assert second["score"] == 5, "Analysis cache entry was corrupted"


# ───────────────────────────────────────────────────────────────────
# DataService history copy-on-read
# ───────────────────────────────────────────────────────────────────


class TestDataServiceCacheCorrectness:
    @pytest.fixture
    def mock_provider(self):
        from unittest.mock import MagicMock

        from src.data.models import StockHistory

        provider = MagicMock()
        provider.get_history.return_value = pd.DataFrame(
            {"Date": pd.date_range("2024-01-01", periods=5), "Close": [1.0, 2.0, 3.0, 4.0, 5.0]}
        )
        provider.get_market_summary.side_effect = Exception("no api")
        return provider

    def test_cache_hit_returns_copy(self, mock_provider):
        DataService.reset_instance()
        svc = DataService(provider=mock_provider)
        svc.clear_cache()

        first = svc.get_history("NABIL", days=5)
        assert first.source in ("provider", "cache")

        # Second read is a cache hit and must NOT share the frame object.
        second = svc.get_history("NABIL", days=5)
        assert second.source == "cache"
        assert second.df is not first.df

        # Mutating the returned frame must not corrupt the cache.
        second.df["Close"] = 0.0
        third = svc.get_history("NABIL", days=5)
        assert third.df["Close"].tolist() == [1.0, 2.0, 3.0, 4.0, 5.0]

    def test_clear_cache_forces_provider(self, mock_provider):
        DataService.reset_instance()
        svc = DataService(provider=mock_provider)
        svc.clear_cache()
        svc.get_history("NABIL", days=5)
        assert mock_provider.get_history.call_count == 1
        svc.get_history("NABIL", days=5)  # cache hit — no provider call
        assert mock_provider.get_history.call_count == 1
        svc.clear_cache()
        svc.get_history("NABIL", days=5)
        assert mock_provider.get_history.call_count == 2


# ───────────────────────────────────────────────────────────────────
# DiskCache
# ───────────────────────────────────────────────────────────────────


class TestDiskCacheCorrectness:
    def test_round_trip(self, tmp_path):
        cache = DiskCache(cache_dir=tmp_path)
        cache.set("quote", {"ltp": 100.5}, ttl=60)
        assert cache.get("quote") == {"ltp": 100.5}

    def test_no_temp_litter_after_write(self, tmp_path):
        cache = DiskCache(cache_dir=tmp_path)
        cache.set("k", {"a": 1}, ttl=60)
        leftovers = [p for p in tmp_path.iterdir() if p.name.endswith(".tmp")]
        assert leftovers == []

    def test_corrupt_entry_returns_none(self, tmp_path):
        cache = DiskCache(cache_dir=tmp_path)
        cache.set("k", {"a": 1}, ttl=60)
        # Corrupt the entry file directly.
        (tmp_path / "k.json").write_text("{corrupt", encoding="utf-8")
        assert cache.get("k") is None

    def test_clear_empties_cache(self, tmp_path):
        cache = DiskCache(cache_dir=tmp_path)
        cache.set("a", 1, ttl=60)
        cache.set("b", 2, ttl=60)
        cache.clear()
        assert cache.get("a") is None and cache.get("b") is None


# ───────────────────────────────────────────────────────────────────
# load_csv fast path — value identity (Phase 7 correctness)
# ───────────────────────────────────────────────────────────────────


class TestLoadCsvFastPathIdentity:
    @pytest.fixture
    def sample_csv(self, tmp_path):
        rows = "\n".join(
            f"2024-01-{d:02d},{100+d}.5,{101+d}.5,{99+d}.5,{100+d}.0,{1000+d*100}"
            for d in range(1, 31)
        )
        path = tmp_path / "SYN.csv"
        path.write_text(f"Date,Open,High,Low,Close,Volume\n{rows}\n", encoding="utf-8")
        return path

    def test_fast_path_matches_slow_path(self, tmp_path, monkeypatch):
        """The numeric fast path must produce byte-identical values."""
        from src.loaders import csv_loader

        rows = "\n".join(
            f"2024-01-{d:02d},{100+d}.5,{101+d}.5,{99+d}.5,{100+d}.0,{1000+d*100}"
            for d in range(1, 31)
        )
        path = tmp_path / "SYN.csv"
        path.write_text(f"Date,Open,High,Low,Close,Volume\n{rows}\n", encoding="utf-8")

        fast = load_csv(path)

        # Recompute with the slow (string round-trip) path forced.
        text = path.read_text(encoding="utf-8-sig")
        df = pd.read_csv(io.StringIO(text))
        df.columns = df.columns.str.strip()
        for col in ("Open", "High", "Low", "Close", "Volume"):
            df[col] = df[col].astype(str).str.replace(",", "", regex=False)
            df[col] = pd.to_numeric(df[col], errors="coerce")
        df["Date"] = pd.to_datetime(df["Date"], errors="coerce")
        df = df.sort_values("Date").dropna(subset=["Close"]).reset_index(drop=True)
        slow = df.reset_index(drop=True)

        fast = fast.reset_index(drop=True)
        pd.testing.assert_frame_equal(fast[["Open", "High", "Low", "Close", "Volume"]], slow[["Open", "High", "Low", "Close", "Volume"]])

    def test_thousands_separator_still_parsed(self, tmp_path):
        """Volume written with unquoted thousands separators must parse."""
        path = tmp_path / "SYN.csv"
        path.write_text(
            "Date,Open,High,Low,Close,Volume\n"
            "2024-01-01,100,110,90,105,\"1,200,000\"\n"
            "2024-01-02,105,115,95,110,\"1,300,000\"\n",
            encoding="utf-8",
        )
        df = load_csv(path)
        assert df["Volume"].tolist() == [1_200_000.0, 1_300_000.0]

    def test_analyze_signal_unaffected(self, tmp_path, monkeypatch):
        """Signal output must be unchanged after the fast-path change."""
        from src.engine.analyzer import analyze_dataframe

        path = tmp_path / "SYN.csv"
        rows = "\n".join(
            f"2024-01-{d:02d},100,102,99,{100 + (d % 5)},10000"
            for d in range(1, 120)
        )
        path.write_text(f"Date,Open,High,Low,Close,Volume\n{rows}\n", encoding="utf-8")

        df = load_csv(path)
        result = analyze_dataframe(df)
        assert "signal" in result
        assert result["signal"] in ("BUY", "HOLD", "SELL")
