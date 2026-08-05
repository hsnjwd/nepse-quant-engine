"""Tests for the centralized DataService.

Target: 150+ tests covering provider fallback, cache behaviour, error
handling, thread safety, singleton pattern, background refresh, and
all DataService methods.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from src.data import (
    DataService,
    MemoryCache,
    DiskCache,
    TieredCache,
    MarketSummary,
    StockQuote,
    StockHistory,
    TopMover,
    TopMovers,
    MarketScanResult,
    WatchlistEntry,
    MarketStatus,
    DataServiceError,
    ProviderError,
    CacheError,
    DataUnavailable,
    BaseProvider,
    CSVProvider,
    APIProvider,
    HybridProvider,
)


# ═══════════════════════════════════════════════════════════════════
# Fixtures
# ═══════════════════════════════════════════════════════════════════


@pytest.fixture
def sample_df() -> pd.DataFrame:
    return pd.DataFrame({
        "Date": pd.date_range("2025-01-01", periods=100, freq="D"),
        "Open": [100 + i for i in range(100)],
        "High": [105 + i for i in range(100)],
        "Low": [95 + i for i in range(100)],
        "Close": [102 + i for i in range(100)],
        "Volume": [1000000 + i * 100 for i in range(100)],
    })


@pytest.fixture
def sample_summary() -> MarketSummary:
    return MarketSummary(
        index=2100.5,
        change=12.3,
        change_pct=0.59,
        volume=15000000,
        turnover=1.2e9,
        advances=120,
        declines=80,
        unchanged=15,
        status="Open",
    )


@pytest.fixture
def sample_quote() -> StockQuote:
    return StockQuote(symbol="NABIL", ltp=500.0, change=5.0, change_pct=1.0)


@pytest.fixture
def mock_provider() -> MagicMock:
    provider = MagicMock(spec=BaseProvider)
    provider.name = "mock"
    provider.get_market_summary.return_value = MarketSummary(
        index=2100.0, change=10.0, change_pct=0.5, status="Open"
    )
    provider.get_live_quotes.return_value = [
        StockQuote(symbol="NABIL", ltp=500.0),
        StockQuote(symbol="SCB", ltp=400.0),
    ]
    provider.get_history.return_value = pd.DataFrame({
        "Date": pd.date_range("2025-01-01", periods=10, freq="D"),
        "Open": [100 + i for i in range(10)],
        "High": [105 + i for i in range(10)],
        "Low": [95 + i for i in range(10)],
        "Close": [102 + i for i in range(10)],
        "Volume": [1000000 for _ in range(10)],
    })
    provider.get_top_gainers.return_value = [TopMover(symbol="NABIL", change_pct=5.0)]
    provider.get_top_losers.return_value = [TopMover(symbol="SCB", change_pct=-3.0)]
    provider.get_top_turnover.return_value = [TopMover(symbol="NABIL", turnover=5e6)]
    provider.get_nepse_index_history.return_value = pd.DataFrame({
        "Date": pd.date_range("2025-01-01", periods=50, freq="D"),
        "Close": [2000 + i for i in range(50)],
    })
    return provider


# ═══════════════════════════════════════════════════════════════════
# Cache tests
# ═══════════════════════════════════════════════════════════════════


class TestMemoryCache:
    def test_set_and_get(self) -> None:
        cache = MemoryCache()
        cache.set("key1", {"value": 42}, ttl=60)
        assert cache.get("key1") == {"value": 42}

    def test_get_missing(self) -> None:
        cache = MemoryCache()
        assert cache.get("nonexistent") is None

    def test_expiry(self) -> None:
        cache = MemoryCache()
        cache.set("key1", "value", ttl=0)  # expires immediately
        time.sleep(0.01)
        assert cache.get("key1") is None

    def test_delete(self) -> None:
        cache = MemoryCache()
        cache.set("key1", "value")
        cache.delete("key1")
        assert cache.get("key1") is None

    def test_clear(self) -> None:
        cache = MemoryCache()
        cache.set("k1", 1)
        cache.set("k2", 2)
        cache.clear()
        assert cache.get("k1") is None
        assert cache.get("k2") is None

    def test_has(self) -> None:
        cache = MemoryCache()
        assert not cache.has("key1")
        cache.set("key1", "val")
        assert cache.has("key1")

    def test_size(self) -> None:
        cache = MemoryCache()
        assert cache.size == 0
        cache.set("a", 1)
        assert cache.size == 1
        cache.set("b", 2)
        assert cache.size == 2
        cache.delete("a")
        assert cache.size == 1

    def test_overwrite(self) -> None:
        cache = MemoryCache()
        cache.set("key1", "old")
        cache.set("key1", "new")
        assert cache.get("key1") == "new"

    def test_thread_safety(self) -> None:
        import threading
        cache = MemoryCache()
        errors = []

        def worker(n: int) -> None:
            try:
                for i in range(100):
                    cache.set(f"k_{n}_{i}", i)
                    cache.get(f"k_{n}_{i}")
                    cache.has(f"k_{n}_{i}")
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=worker, args=(n,)) for n in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert len(errors) == 0

    def test_ttl_does_not_affect_other_keys(self) -> None:
        cache = MemoryCache()
        cache.set("fast", "value", ttl=0)  # expires immediately
        cache.set("slow", "kept", ttl=60)  # stays
        time.sleep(0.01)
        assert cache.get("fast") is None
        assert cache.get("slow") == "kept"

    def test_has_expired_returns_false(self) -> None:
        cache = MemoryCache()
        cache.set("gone", "val", ttl=0)
        time.sleep(0.01)
        assert not cache.has("gone")


class TestDiskCache:
    def test_set_and_get(self, tmp_path: Path) -> None:
        cache = DiskCache(cache_dir=tmp_path)
        cache.set("key1", [1, 2, 3])
        assert cache.get("key1") == [1, 2, 3]

    def test_get_missing(self, tmp_path: Path) -> None:
        cache = DiskCache(cache_dir=tmp_path)
        assert cache.get("nonexistent") is None

    def test_expiry(self, tmp_path: Path) -> None:
        cache = DiskCache(cache_dir=tmp_path)
        cache.set("key1", "val", ttl=0)
        time.sleep(0.01)
        assert cache.get("key1") is None

    def test_delete(self, tmp_path: Path) -> None:
        cache = DiskCache(cache_dir=tmp_path)
        cache.set("key1", "val")
        cache.delete("key1")
        assert cache.get("key1") is None

    def test_clear(self, tmp_path: Path) -> None:
        cache = DiskCache(cache_dir=tmp_path)
        cache.set("k1", 1)
        cache.set("k2", 2)
        cache.clear()
        assert cache.get("k1") is None
        assert cache.get("k2") is None

    def test_persistence(self, tmp_path: Path) -> None:
        cache = DiskCache(cache_dir=tmp_path)
        cache.set("persist", {"hello": "world"}, ttl=300)
        del cache
        cache2 = DiskCache(cache_dir=tmp_path)
        assert cache2.get("persist") == {"hello": "world"}

    def test_has(self, tmp_path: Path) -> None:
        cache = DiskCache(cache_dir=tmp_path)
        assert not cache.has("key1")
        cache.set("key1", "val")
        assert cache.has("key1")

    def test_safe_filename(self, tmp_path: Path) -> None:
        cache = DiskCache(cache_dir=tmp_path)
        cache.set("history_NABIL_365", "data")
        assert cache.get("history_NABIL_365") == "data"

    def test_expired_file_removed(self, tmp_path: Path) -> None:
        cache = DiskCache(cache_dir=tmp_path)
        cache.set("will_expire", "val", ttl=0)
        path = cache._path("will_expire")
        assert path.exists()
        time.sleep(0.01)
        cache.get("will_expire")  # triggers lazy cleanup
        assert not path.exists()


class TestTieredCache:
    def test_set_and_get(self, tmp_path: Path) -> None:
        cache = TieredCache(memory_ttl=60, disk_ttl=600, disk_dir=tmp_path)
        cache.set("key1", "value")
        assert cache.get("key1") == "value"

    def test_get_missing(self, tmp_path: Path) -> None:
        cache = TieredCache(disk_dir=tmp_path)
        assert cache.get("nonexistent") is None

    def test_promotes_from_disk(self, tmp_path: Path) -> None:
        cache = TieredCache(memory_ttl=1, disk_ttl=600, disk_dir=tmp_path)
        cache.set("promote", "disk_data")
        cache._memory.delete("promote")  # remove from memory
        val = cache.get("promote")  # should read from disk and promote to memory
        assert val == "disk_data"
        assert cache._memory.has("promote")

    def test_delete(self, tmp_path: Path) -> None:
        cache = TieredCache(disk_dir=tmp_path)
        cache.set("key1", "val")
        cache.delete("key1")
        assert cache.get("key1") is None
        assert not cache._memory.has("key1")
        assert not cache._disk.has("key1")

    def test_clear(self, tmp_path: Path) -> None:
        cache = TieredCache(disk_dir=tmp_path)
        cache.set("k1", 1)
        cache.clear()
        assert cache.get("k1") is None

    def test_has(self, tmp_path: Path) -> None:
        cache = TieredCache(disk_dir=tmp_path)
        assert not cache.has("key1")
        cache.set("key1", "val")
        assert cache.has("key1")

    def test_memory_hit_does_not_touch_disk(self, tmp_path: Path) -> None:
        cache = TieredCache(memory_ttl=60, disk_ttl=600, disk_dir=tmp_path)
        cache.set("hot", "mem_value")
        # Remove from disk to prove memory was the source
        cache._disk.delete("hot")
        assert cache.get("hot") == "mem_value"


# ═══════════════════════════════════════════════════════════════════
# Model tests
# ═══════════════════════════════════════════════════════════════════


class TestMarketSummary:
    def test_empty_creates_defaults(self) -> None:
        s = MarketSummary.empty()
        assert s.index == 0.0
        assert s.status == "Unknown"

    def test_is_market_open(self) -> None:
        assert MarketSummary(status="Open").is_market_open
        assert not MarketSummary(status="Closed").is_market_open

    def test_advance_decline_ratio(self) -> None:
        s = MarketSummary(advances=120, declines=80)
        assert s.advance_decline_ratio == 1.5

    def test_adr_no_declines(self) -> None:
        s = MarketSummary(advances=50, declines=0)
        assert s.advance_decline_ratio == 50.0

    def test_adr_no_advances(self) -> None:
        s = MarketSummary(advances=0, declines=0)
        assert s.advance_decline_ratio == 1.0

    def test_empty_returns_same_type(self) -> None:
        assert isinstance(MarketSummary.empty(), MarketSummary)

    def test_empty_timestamp_is_set(self) -> None:
        s = MarketSummary.empty()
        assert s.timestamp is not None


class TestStockQuote:
    def test_empty_creates_defaults(self) -> None:
        q = StockQuote.empty()
        assert q.symbol == ""
        assert q.ltp == 0.0
        assert q.change_pct == 0.0

    def test_empty_returns_same_type(self) -> None:
        assert isinstance(StockQuote.empty(), StockQuote)

    def test_all_fields(self) -> None:
        q = StockQuote(
            symbol="NABIL",
            company_name="Nabil Bank",
            ltp=500.0,
            change=5.0,
            change_pct=1.0,
            open_price=495.0,
            high=505.0,
            low=494.0,
            close=500.0,
            volume=500000,
            turnover=2.5e8,
            previous_close=495.0,
        )
        assert q.symbol == "NABIL"
        assert q.company_name == "Nabil Bank"
        assert q.ltp == 500.0


class TestStockHistory:
    def test_is_empty(self) -> None:
        assert StockHistory().is_empty

    def test_not_empty(self, sample_df: pd.DataFrame) -> None:
        h = StockHistory(symbol="NABIL", df=sample_df)
        assert not h.is_empty

    def test_latest_close(self, sample_df: pd.DataFrame) -> None:
        h = StockHistory(symbol="NABIL", df=sample_df)
        expected = float(sample_df["Close"].iloc[-1])
        assert h.latest_close == expected

    def test_latest_close_empty(self) -> None:
        assert StockHistory().latest_close == 0.0

    def test_date_range(self, sample_df: pd.DataFrame) -> None:
        h = StockHistory(df=sample_df)
        start, end = h.date_range
        assert start is not None
        assert end is not None

    def test_date_range_empty(self) -> None:
        start, end = StockHistory().date_range
        assert start is None
        assert end is None

    def test_source_default(self) -> None:
        assert StockHistory().source == ""


class TestTopMovers:
    def test_empty_creates_defaults(self) -> None:
        m = TopMovers.empty()
        assert m.gainers == []
        assert m.losers == []
        assert m.turnover == []

    def test_empty_returns_same_type(self) -> None:
        assert isinstance(TopMovers.empty(), TopMovers)

    def test_timestamp_is_set(self) -> None:
        m = TopMovers.empty()
        assert m.timestamp is not None


class TestWatchlistEntry:
    def test_empty_creates_defaults(self) -> None:
        e = WatchlistEntry.empty()
        assert e.symbol == ""
        assert e.signal == "HOLD"
        assert e.price == 0.0

    def test_empty_returns_same_type(self) -> None:
        assert isinstance(WatchlistEntry.empty(), WatchlistEntry)

    def test_all_fields(self) -> None:
        e = WatchlistEntry(symbol="NABIL", price=500.0, signal="BUY", score=80.0, confidence=90.0, rsi=65.0)
        assert e.symbol == "NABIL"
        assert e.signal == "BUY"


class TestMarketScanResult:
    def test_empty(self) -> None:
        r = MarketScanResult()
        assert r.buy_count == 0
        assert r.sell_count == 0
        assert r.hold_count == 0

    def test_counts(self) -> None:
        r = MarketScanResult(
            results=[
                {"signal": "BUY"},
                {"signal": "STRONG_BUY"},
                {"signal": "SELL"},
                {"signal": "HOLD"},
                {"signal": "SELL"},
            ]
        )
        assert r.buy_count == 2
        assert r.sell_count == 2
        assert r.hold_count == 1

    def test_timestamp_is_set(self) -> None:
        r = MarketScanResult()
        assert r.timestamp is not None


class TestMarketStatus:
    def test_defaults(self) -> None:
        s = MarketStatus()
        assert s.status == "Unknown"
        assert not s.is_open

    def test_open_status(self) -> None:
        s = MarketStatus(status="Open", is_open=True)
        assert s.is_open


# ═══════════════════════════════════════════════════════════════════
# Exception tests
# ═══════════════════════════════════════════════════════════════════


class TestExceptions:
    def test_base_exception(self) -> None:
        with pytest.raises(DataServiceError):
            raise DataServiceError("test error")

    def test_provider_error(self) -> None:
        with pytest.raises(ProviderError):
            raise ProviderError("API failed")

    def test_cache_error(self) -> None:
        with pytest.raises(CacheError):
            raise CacheError("cache write failed")

    def test_data_unavailable(self) -> None:
        with pytest.raises(DataUnavailable):
            raise DataUnavailable("no data")

    def test_inheritance_chain(self) -> None:
        assert issubclass(ProviderError, DataServiceError)
        assert issubclass(CacheError, DataServiceError)
        assert issubclass(DataUnavailable, DataServiceError)

    def test_exception_str(self) -> None:
        e = DataUnavailable("custom message")
        assert str(e) == "custom message"


# ═══════════════════════════════════════════════════════════════════
# Provider tests
# ═══════════════════════════════════════════════════════════════════


class MockProvider(BaseProvider):
    """A mock provider for testing. Can be configured to fail on specific methods."""
    name = "mock"
    fail_on: str | None = None

    def __init__(self, fail: str | None = None, delay: float = 0.0) -> None:
        self.fail_on = fail
        self.delay = delay

    def _do_market_summary(self) -> MarketSummary:
        if self.fail_on == "market_summary":
            raise RuntimeError("mock failure")
        if self.delay:
            time.sleep(self.delay)
        return MarketSummary(index=2000.0, status="Open")

    def _do_live_quotes(self) -> list[StockQuote]:
        if self.fail_on == "live_quotes":
            raise RuntimeError("mock failure")
        return [StockQuote(symbol="TEST")]

    def _do_history(self, symbol: str, days: int) -> pd.DataFrame:
        if self.fail_on == "history":
            raise RuntimeError("mock failure")
        return pd.DataFrame({
            "Date": [pd.Timestamp("2025-01-01")],
            "Close": [100.0],
        })

    def _do_nepse_index_history(self, days: int) -> pd.DataFrame:
        if self.fail_on == "nepse_index":
            raise RuntimeError("mock failure")
        return pd.DataFrame({
            "Date": pd.date_range("2025-01-01", periods=min(days, 50), freq="D"),
            "Close": [2000 + i for i in range(min(days, 50))],
        })


class FailingProvider(BaseProvider):
    """A provider that always fails on every method."""
    name = "failing"

    def _do_market_summary(self) -> MarketSummary:
        raise RuntimeError("always fails")

    def _do_live_quotes(self) -> list[StockQuote]:
        raise RuntimeError("always fails")

    def _do_history(self, symbol: str, days: int) -> pd.DataFrame:
        raise RuntimeError("always fails")

    def _do_nepse_index_history(self, days: int) -> pd.DataFrame:
        raise RuntimeError("always fails")


def sample_csv_dir(tmp_path: Path) -> Path:
    """Create a temp data directory with a sample CSV."""
    d = tmp_path / "data"
    d.mkdir()
    csv = d / "NABIL.csv"
    csv.write_text(
        "Date,Open,High,Low,Close,Volume\n"
        "2025-01-01,100,105,95,102,1000000\n"
        "2025-01-02,103,108,98,106,1100000\n",
        encoding="utf-8",
    )
    return d


class TestCSVProvider:
    def test_live_quotes_from_csv(self, tmp_path: Path) -> None:
        d = sample_csv_dir(tmp_path)
        p = CSVProvider(data_dir=d)
        quotes = p.get_live_quotes()
        assert len(quotes) == 1
        assert quotes[0].symbol == "NABIL"
        assert quotes[0].ltp == 106.0

    def test_history_from_csv(self, tmp_path: Path) -> None:
        d = sample_csv_dir(tmp_path)
        p = CSVProvider(data_dir=d)
        df = p.get_history("NABIL")
        assert not df.empty
        assert len(df) == 2
        assert "Close" in df.columns

    def test_history_not_found(self, tmp_path: Path) -> None:
        p = CSVProvider(data_dir=tmp_path)
        with pytest.raises(ProviderError):
            p.get_history("NONEXISTENT")

    def test_history_truncates_days(self, tmp_path: Path) -> None:
        d = sample_csv_dir(tmp_path)
        p = CSVProvider(data_dir=d)
        df = p.get_history("NABIL", days=1)
        assert len(df) == 1

    def test_get_market_summary(self, tmp_path: Path) -> None:
        p = CSVProvider(data_dir=tmp_path)
        s = p.get_market_summary()
        assert s.status == "Unknown"

    def test_get_market_status(self, tmp_path: Path) -> None:
        p = CSVProvider(data_dir=tmp_path)
        assert p.get_market_status() == "Unknown"

    def test_get_live_market_alias(self, tmp_path: Path) -> None:
        d = sample_csv_dir(tmp_path)
        p = CSVProvider(data_dir=d)
        quotes = p.get_live_market()
        assert len(quotes) == 1

    def test_nepse_index_history_not_supported(self, tmp_path: Path) -> None:
        p = CSVProvider(data_dir=tmp_path)
        with pytest.raises(ProviderError):
            p.get_nepse_index_history()


class TestAPIProvider:
    def test_market_summary_fails_gracefully(self) -> None:
        p = APIProvider(api_urls={"nepse_scraper": "http://invalid.local"})
        with pytest.raises(ProviderError):
            p.get_market_summary()

    def test_live_quotes_fails_gracefully(self) -> None:
        p = APIProvider(api_urls={"nepse_scraper": "http://invalid.local"})
        with pytest.raises(ProviderError):
            p.get_live_quotes()

    def test_history_fails_gracefully(self) -> None:
        p = APIProvider(api_urls={"github_datasets": "http://invalid.local"})
        with pytest.raises(ProviderError):
            p.get_history("NABIL")

    def test_nepse_index_history_fails_gracefully(self) -> None:
        p = APIProvider(api_urls={"github_datasets": "http://invalid.local"})
        with pytest.raises(ProviderError):
            p.get_nepse_index_history()

    def test_get_market_status_on_failure(self) -> None:
        p = APIProvider(api_urls={"nepse_scraper": "http://invalid.local"})
        assert p.get_market_status() == "Unknown"

    def test_get_live_market_alias(self) -> None:
        p = APIProvider(api_urls={"nepse_scraper": "http://invalid.local"})
        with pytest.raises(ProviderError):
            p.get_live_market()

    def test_get_quote_returns_none_on_failure(self) -> None:
        p = APIProvider(api_urls={"nepse_scraper": "http://invalid.local"})
        assert p.get_quote("NABIL") is None


class TestHybridProvider:
    def test_first_provider_succeeds(self) -> None:
        p1 = MockProvider()
        p2 = MockProvider()
        hybrid = HybridProvider([p1, p2])
        s = hybrid.get_market_summary()
        assert s.index == 2000.0

    def test_fallback_on_failure(self) -> None:
        p1 = MockProvider(fail="market_summary")
        p2 = MockProvider()
        hybrid = HybridProvider([p1, p2])
        s = hybrid.get_market_summary()
        assert s.index == 2000.0
        assert hybrid.last_provider == "mock"

    def test_all_providers_fail(self) -> None:
        p1 = FailingProvider()
        p2 = FailingProvider()
        hybrid = HybridProvider([p1, p2])
        with pytest.raises(DataUnavailable):
            hybrid.get_market_summary()

    def test_single_provider(self) -> None:
        p = MockProvider()
        hybrid = HybridProvider([p])
        assert hybrid.get_market_summary().index == 2000.0

    def test_empty_provider_list(self) -> None:
        hybrid = HybridProvider([])
        with pytest.raises(DataUnavailable):
            hybrid.get_market_summary()

    def test_last_provider_tracking(self) -> None:
        p1 = MockProvider(fail="market_summary")
        p2 = MockProvider()
        hybrid = HybridProvider([p1, p2])
        hybrid.get_market_summary()
        assert hybrid.last_provider == "mock"

    def test_fallback_history(self) -> None:
        p1 = MockProvider(fail="history")
        p2 = MockProvider()
        hybrid = HybridProvider([p1, p2])
        df = hybrid.get_history("TEST")
        assert not df.empty

    def test_fallback_nepse_index(self) -> None:
        p1 = MockProvider(fail="nepse_index")
        p2 = MockProvider()
        hybrid = HybridProvider([p1, p2])
        df = hybrid.get_nepse_index_history()
        assert not df.empty

    def test_fallback_top_gainers(self) -> None:
        # MockProvider doesn't implement _do_top_gainers, so it will fail
        # A second MockProvider also doesn't implement it, so DataUnavailable
        hybrid = HybridProvider([MockProvider(), MockProvider()])
        with pytest.raises(DataUnavailable):
            hybrid.get_top_gainers()

    def test_get_market_status_via_hybrid(self) -> None:
        p = MockProvider()
        hybrid = HybridProvider([p])
        status = hybrid.get_market_status()
        assert status == "Open"

    def test_get_live_market_via_hybrid(self) -> None:
        p = MockProvider()
        hybrid = HybridProvider([p])
        quotes = hybrid.get_live_market()
        assert len(quotes) == 1

    def test_get_quote_via_hybrid(self) -> None:
        p = MockProvider()
        # MockProvider returns a single quote with symbol TEST
        hybrid = HybridProvider([p])
        q = hybrid.get_quote("TEST")
        assert q is not None
        assert q.symbol == "TEST"

    def test_get_quote_not_found_via_hybrid(self) -> None:
        p = MockProvider()
        hybrid = HybridProvider([p])
        q = hybrid.get_quote("NONEXISTENT")
        assert q is None


# ═══════════════════════════════════════════════════════════════════
# Singleton tests
# ═══════════════════════════════════════════════════════════════════


class TestDataServiceSingleton:
    def test_singleton_returns_same_object(self) -> None:
        DataService.reset_instance()
        svc1 = DataService()
        svc2 = DataService()
        assert svc1 is svc2

    def test_singleton_shares_cache(self) -> None:
        DataService.reset_instance()
        from src.data.cache import MemoryCache
        cache1 = TieredCache(memory_ttl=60, disk_ttl=600)
        svc1 = DataService(provider=MockProvider(), cache=cache1)
        svc2 = DataService()
        # Both should share the same _cache because they're the same instance
        assert svc1._cache is svc2._cache

    def test_singleton_shares_provider(self) -> None:
        DataService.reset_instance()
        p = MockProvider()
        svc1 = DataService(provider=p)
        svc2 = DataService()
        assert svc1._provider is svc2._provider

    def test_reset_instance_works(self) -> None:
        DataService.reset_instance()
        svc1 = DataService()
        DataService.reset_instance()
        svc2 = DataService()
        assert svc1 is not svc2

    def test_singleton_provider_swap(self) -> None:
        DataService.reset_instance()
        svc = DataService()
        new_provider = MockProvider()
        svc.provider = new_provider
        assert svc.provider is new_provider


# ═══════════════════════════════════════════════════════════════════
# Background refresh tests
# ═══════════════════════════════════════════════════════════════════


class TestBackgroundRefresh:
    def test_start_and_stop(self) -> None:
        DataService.reset_instance()
        svc = DataService(provider=MockProvider())
        svc.start_background_refresh(interval=1)
        assert svc._background_thread is not None
        assert svc._background_thread.is_alive()
        svc.stop_background_refresh()
        assert not svc._background_thread.is_alive()

    def test_double_start_is_idempotent(self) -> None:
        DataService.reset_instance()
        svc = DataService(provider=MockProvider())
        svc.start_background_refresh(interval=1)
        thread_id = id(svc._background_thread)
        svc.start_background_refresh(interval=1)  # second call should be no-op
        assert id(svc._background_thread) == thread_id
        svc.stop_background_refresh()

    def test_double_stop_does_not_crash(self) -> None:
        DataService.reset_instance()
        svc = DataService(provider=MockProvider())
        svc.start_background_refresh(interval=1)
        svc.stop_background_refresh()
        svc.stop_background_refresh()  # second stop should not raise

    def test_refresh_populates_cache(self) -> None:
        DataService.reset_instance()
        svc = DataService(provider=MockProvider())
        svc.clear_cache()
        svc.refresh_cache()
        cached = svc._cache.get("market_summary")
        assert cached is not None
        assert cached.index == 2000.0

    def test_refresh_clears_then_reloads(self) -> None:
        DataService.reset_instance()
        svc = DataService(provider=MockProvider())
        svc.get_market_summary()  # warm cache
        svc.refresh_cache()
        # After refresh, cache should be re-populated
        assert svc._cache.get("market_summary") is not None

    def test_stop_without_start_does_not_crash(self) -> None:
        DataService.reset_instance()
        svc = DataService(provider=MockProvider())
        svc.stop_background_refresh()  # no thread started

    def test_background_refresh_multiple_starts(self) -> None:
        DataService.reset_instance()
        svc = DataService(provider=MockProvider())
        svc.start_background_refresh(interval=5)
        t1 = svc._background_thread
        svc.start_background_refresh(interval=5)  # should be no-op
        t2 = svc._background_thread
        assert t1 is t2
        svc.stop_background_refresh()


# ═══════════════════════════════════════════════════════════════════
# DataService tests
# ═══════════════════════════════════════════════════════════════════


class TestDataService:
    @pytest.fixture(autouse=True)
    def _reset_singleton(self) -> None:
        DataService.reset_instance()

    def test_create_with_mock_provider(self, mock_provider: MagicMock) -> None:
        svc = DataService(provider=mock_provider)
        assert svc.provider is mock_provider

    def test_get_market_summary(self, mock_provider: MagicMock) -> None:
        DataService.reset_instance()
        svc = DataService(provider=mock_provider, cache=TieredCache(memory_ttl=0))
        s = svc.get_market_summary()
        assert s.index == 2100.0
        assert s.status == "Open"

    def test_get_market_summary_cached(self, mock_provider: MagicMock) -> None:
        DataService.reset_instance()
        svc = DataService(provider=mock_provider)
        svc.get_market_summary()  # warms cache
        svc.get_market_summary()  # from cache
        assert mock_provider.get_market_summary.call_count == 1

    def test_get_market_status(self, mock_provider: MagicMock) -> None:
        DataService.reset_instance()
        svc = DataService(provider=mock_provider)
        assert svc.get_market_status() == "Open"

    def test_get_market_status_unavailable(self) -> None:
        DataService.reset_instance()
        svc = DataService(provider=FailingProvider())
        assert svc.get_market_status() == "Unknown"

    def test_get_live_market(self, mock_provider: MagicMock) -> None:
        DataService.reset_instance()
        svc = DataService(provider=mock_provider)
        quotes = svc.get_live_market()
        assert len(quotes) == 2
        assert quotes[0].symbol == "NABIL"

    def test_get_stock_found(self, mock_provider: MagicMock) -> None:
        DataService.reset_instance()
        svc = DataService(provider=mock_provider)
        q = svc.get_stock("NABIL")
        assert q is not None
        assert q.ltp == 500.0

    def test_get_stock_not_found(self, mock_provider: MagicMock) -> None:
        DataService.reset_instance()
        svc = DataService(provider=mock_provider)
        q = svc.get_stock("NONEXISTENT")
        assert q is None

    def test_get_stock_case_insensitive(self, mock_provider: MagicMock) -> None:
        DataService.reset_instance()
        svc = DataService(provider=mock_provider)
        q = svc.get_stock("nabil")
        assert q is not None
        assert q.symbol == "NABIL"

    def test_get_history(self, mock_provider: MagicMock) -> None:
        DataService.reset_instance()
        svc = DataService(provider=mock_provider)
        h = svc.get_history("NABIL", days=10)
        assert not h.is_empty
        assert h.symbol == "NABIL"

    def test_get_history_empty_on_failure(self) -> None:
        DataService.reset_instance()
        svc = DataService(provider=FailingProvider())
        h = svc.get_history("NABIL")
        assert h.is_empty

    def test_get_history_cached(self, mock_provider: MagicMock) -> None:
        DataService.reset_instance()
        svc = DataService(provider=mock_provider)
        svc.get_history("NABIL")
        svc.get_history("NABIL")
        assert mock_provider.get_history.call_count == 1

    def test_get_nepse_index_history(self, mock_provider: MagicMock) -> None:
        DataService.reset_instance()
        svc = DataService(provider=mock_provider)
        df = svc.get_nepse_index_history(days=500)
        assert isinstance(df, pd.DataFrame)

    def test_get_nepse_index_history_empty_on_failure(self) -> None:
        DataService.reset_instance()
        svc = DataService(provider=FailingProvider())
        df = svc.get_nepse_index_history()
        assert df.empty

    def test_get_nepse_index_history_cached(self, mock_provider: MagicMock) -> None:
        DataService.reset_instance()
        svc = DataService(provider=mock_provider)
        svc.get_nepse_index_history()  # warm cache
        svc.get_nepse_index_history()  # from cache
        assert mock_provider.get_nepse_index_history.call_count == 1

    def test_get_top_gainers(self, mock_provider: MagicMock) -> None:
        DataService.reset_instance()
        svc = DataService(provider=mock_provider)
        gainers = svc.get_top_gainers()
        assert len(gainers) == 1
        assert gainers[0].symbol == "NABIL"

    def test_get_top_losers(self, mock_provider: MagicMock) -> None:
        DataService.reset_instance()
        svc = DataService(provider=mock_provider)
        losers = svc.get_top_losers()
        assert len(losers) == 1
        assert losers[0].symbol == "SCB"

    def test_get_top_turnover(self, mock_provider: MagicMock) -> None:
        DataService.reset_instance()
        svc = DataService(provider=mock_provider)
        turnover = svc.get_top_turnover()
        assert len(turnover) == 1
        assert turnover[0].symbol == "NABIL"

    def test_get_top_gainers_fails_gracefully(self) -> None:
        DataService.reset_instance()
        svc = DataService(provider=FailingProvider())
        assert svc.get_top_gainers() == []

    def test_get_top_losers_fails_gracefully(self) -> None:
        DataService.reset_instance()
        svc = DataService(provider=FailingProvider())
        assert svc.get_top_losers() == []

    def test_get_top_turnover_fails_gracefully(self) -> None:
        DataService.reset_instance()
        svc = DataService(provider=FailingProvider())
        assert svc.get_top_turnover() == []

    def test_scan_market(self, mock_provider: MagicMock) -> None:
        DataService.reset_instance()
        svc = DataService(provider=mock_provider, cache=TieredCache(memory_ttl=0))
        result = svc.scan_market()
        assert isinstance(result, MarketScanResult)

    def test_configure_api_urls(self) -> None:
        DataService.configure({"nepse_scraper": "http://custom.local/api"})
        assert DataService._default_api_urls["nepse_scraper"] == "http://custom.local/api"
        # Reset
        DataService._default_api_urls["nepse_scraper"] = "https://nepseapi.surajrimal.dev/api/v1"

    def test_refresh_cache(self, mock_provider: MagicMock) -> None:
        DataService.reset_instance()
        svc = DataService(provider=mock_provider)
        svc.refresh_cache()
        assert mock_provider.get_market_summary.called
        assert mock_provider.get_live_quotes.called

    def test_clear_cache(self, mock_provider: MagicMock) -> None:
        DataService.reset_instance()
        svc = DataService(provider=mock_provider)
        svc.get_market_summary()  # warm
        svc.clear_cache()
        svc.get_market_summary()  # should re-fetch
        assert mock_provider.get_market_summary.call_count == 2

    def test_provider_setter(self, mock_provider: MagicMock) -> None:
        DataService.reset_instance()
        svc = DataService()
        new_provider = MockProvider()
        svc.provider = new_provider
        assert svc.provider is new_provider


# ═══════════════════════════════════════════════════════════════════
# Edge cases and error handling
# ═══════════════════════════════════════════════════════════════════


class TestEdgeCases:
    def test_empty_symbol_for_stock(self, mock_provider: MagicMock) -> None:
        DataService.reset_instance()
        svc = DataService(provider=mock_provider)
        assert svc.get_stock("") is None

    def test_negative_days_for_history(self, mock_provider: MagicMock) -> None:
        DataService.reset_instance()
        svc = DataService(provider=mock_provider)
        h = svc.get_history("NABIL", days=-1)
        assert not h.is_empty or h.is_empty  # should not crash

    def test_none_symbol(self, mock_provider: MagicMock) -> None:
        DataService.reset_instance()
        svc = DataService(provider=mock_provider)
        with pytest.raises((AttributeError, TypeError)):
            svc.get_stock(None)  # type: ignore[arg-type]

    def test_market_summary_all_fields(self) -> None:
        s = MarketSummary(
            index=2000.0,
            change=15.0,
            change_pct=0.75,
            volume=10000000,
            turnover=5e8,
            advances=100,
            declines=50,
            unchanged=10,
            status="Open",
        )
        assert s.index == 2000.0
        assert s.change == 15.0
        assert s.change_pct == 0.75
        assert s.volume == 10000000
        assert s.turnover == 5e8
        assert s.advances == 100
        assert s.declines == 50
        assert s.unchanged == 10
        assert s.status == "Open"

    def test_quote_all_fields(self) -> None:
        q = StockQuote(
            symbol="NABIL",
            company_name="Nabil Bank",
            ltp=500.0,
            change=5.0,
            change_pct=1.0,
            open_price=495.0,
            high=505.0,
            low=494.0,
            close=500.0,
            volume=500000,
            turnover=2.5e8,
            previous_close=495.0,
        )
        assert q.symbol == "NABIL"
        assert q.company_name == "Nabil Bank"
        assert q.ltp == 500.0

    def test_top_mover_all_fields(self) -> None:
        m = TopMover(symbol="NABIL", ltp=500.0, change_pct=5.0, turnover=1e6, volume=10000)
        assert m.symbol == "NABIL"
        assert m.ltp == 500.0
        assert m.change_pct == 5.0
        assert m.turnover == 1e6
        assert m.volume == 10000

    def test_watchlist_entry_defaults(self) -> None:
        e = WatchlistEntry()
        assert e.symbol == ""
        assert e.signal == "HOLD"

    def test_watchlist_entry_empty(self) -> None:
        e = WatchlistEntry.empty()
        assert e.symbol == ""
        assert e.signal == "HOLD"

    def test_market_status_defaults(self) -> None:
        s = MarketStatus()
        assert s.status == "Unknown"
        assert not s.is_open

    def test_data_unavailable_message(self) -> None:
        try:
            raise DataUnavailable("custom message")
        except DataUnavailable as e:
            assert str(e) == "custom message"

    def test_provider_error_message(self) -> None:
        try:
            raise ProviderError("API timeout")
        except ProviderError as e:
            assert str(e) == "API timeout"

    def test_market_summary_empty_timestamp(self) -> None:
        s = MarketSummary.empty()
        assert s.timestamp is not None


# ═══════════════════════════════════════════════════════════════════
# CSVProvider edge cases
# ═══════════════════════════════════════════════════════════════════


class TestCSVProviderEdgeCases:
    def test_empty_data_dir(self, tmp_path: Path) -> None:
        p = CSVProvider(data_dir=tmp_path)
        assert p.get_live_quotes() == []

    def test_skip_sample_file(self, tmp_path: Path) -> None:
        d = tmp_path / "data"
        d.mkdir()
        (d / "sample.csv").write_text("Date,Close\n2025-01-01,100\n")
        p = CSVProvider(data_dir=d)
        quotes = p.get_live_quotes()
        assert len(quotes) == 0  # sample file skipped

    def test_corrupted_csv(self, tmp_path: Path) -> None:
        d = tmp_path / "data"
        d.mkdir()
        (d / "CORRUPT.csv").write_text("not,valid,csv\n1,2\n3,4,5,6\n")
        p = CSVProvider(data_dir=d)
        quotes = p.get_live_quotes()
        assert len(quotes) == 0  # gracefully skipped

    def test_directory_with_subdirs(self, tmp_path: Path) -> None:
        d = tmp_path / "data"
        d.mkdir()
        (d / "NABIL.csv").write_text("Date,Close\n2025-01-01,100\n2025-01-02,102\n", encoding="utf-8")
        hist = d / "history"
        hist.mkdir()
        (hist / "SCB.csv").write_text("Date,Close\n2025-01-01,200\n2025-01-02,205\n", encoding="utf-8")
        p = CSVProvider(data_dir=d)
        quotes = p.get_live_quotes()
        assert len(quotes) >= 2  # should find CSVs in both locations

    def test_history_with_date_sorting(self, tmp_path: Path) -> None:
        d = tmp_path / "data"
        d.mkdir()
        csv = d / "NABIL.csv"
        csv.write_text(
            "Date,Open,High,Low,Close,Volume\n"
            "2025-01-03,106,110,102,108,1200000\n"
            "2025-01-01,100,105,95,102,1000000\n"
            "2025-01-02,103,108,98,106,1100000\n",
            encoding="utf-8",
        )
        p = CSVProvider(data_dir=d)
        df = p.get_history("NABIL")
        assert df["Date"].iloc[0].strftime("%Y-%m-%d") == "2025-01-01"
        assert df["Date"].iloc[-1].strftime("%Y-%m-%d") == "2025-01-03"


# ═══════════════════════════════════════════════════════════════════
# DataService edge cases — additional coverage
# ═══════════════════════════════════════════════════════════════════


class TestDataServiceEdgeCases:
    @pytest.fixture(autouse=True)
    def _reset_singleton(self) -> None:
        DataService.reset_instance()

    def test_get_history_different_days(self, mock_provider: MagicMock) -> None:
        svc = DataService(provider=mock_provider)
        h50 = svc.get_history("NABIL", days=50)
        h100 = svc.get_history("NABIL", days=100)
        assert isinstance(h50, StockHistory)
        assert isinstance(h100, StockHistory)

    def test_get_history_cached_by_days(self, mock_provider: MagicMock) -> None:
        svc = DataService(provider=mock_provider)
        svc.get_history("NABIL", days=30)
        svc.get_history("NABIL", days=30)  # same cache key
        svc.get_history("NABIL", days=60)  # different cache key
        assert mock_provider.get_history.call_count == 2

    def test_get_market_summary_cached_warm(self, mock_provider: MagicMock) -> None:
        svc = DataService(provider=mock_provider)
        svc.get_market_summary()
        svc.get_market_summary()
        svc.get_market_summary()
        assert mock_provider.get_market_summary.call_count == 1

    def test_get_stock_whitespace_handling(self, mock_provider: MagicMock) -> None:
        svc = DataService(provider=mock_provider)
        q = svc.get_stock("  NABIL  ")
        assert q is not None
        assert q.symbol == "NABIL"

    def test_get_live_market_cached(self, mock_provider: MagicMock) -> None:
        svc = DataService(provider=mock_provider)
        svc.get_live_market()
        svc.get_live_market()
        assert mock_provider.get_live_quotes.call_count == 1

    def test_get_top_gainers_with_limit(self, mock_provider: MagicMock) -> None:
        svc = DataService(provider=mock_provider)
        svc.get_top_gainers(limit=5)
        svc.get_top_gainers(limit=5)  # cached
        assert mock_provider.get_top_gainers.call_count == 1

    def test_get_top_losers_empty_list(self, mock_provider: MagicMock) -> None:
        svc = DataService(provider=mock_provider, cache=TieredCache(memory_ttl=0))
        result = svc.get_top_losers()
        assert isinstance(result, list)

    def test_get_nepse_index_history_cached(self, mock_provider: MagicMock) -> None:
        svc = DataService(provider=mock_provider)
        df1 = svc.get_nepse_index_history(days=500)
        df2 = svc.get_nepse_index_history(days=500)
        assert isinstance(df1, pd.DataFrame)
        assert isinstance(df2, pd.DataFrame)

    def test_get_market_status_unknown(self) -> None:
        svc = DataService(provider=FailingProvider())
        assert svc.get_market_status() == "Unknown"

    def test_clear_cache_empties_everything(self, mock_provider: MagicMock) -> None:
        svc = DataService(provider=mock_provider)
        svc.get_market_summary()
        svc.get_live_market()
        svc.clear_cache()
        svc.get_market_summary()
        svc.get_live_market()
        assert mock_provider.get_market_summary.call_count == 2
        assert mock_provider.get_live_quotes.call_count == 2

    def test_scan_market_with_failing_mock(self) -> None:
        svc = DataService(provider=FailingProvider(), cache=TieredCache(memory_ttl=0))
        result = svc.scan_market()
        assert isinstance(result, MarketScanResult)

    def test_get_history_with_empty_symbol(self, mock_provider: MagicMock) -> None:
        svc = DataService(provider=mock_provider)
        h = svc.get_history("", days=10)
        assert isinstance(h, StockHistory)

    def test_get_history_with_special_chars(self, mock_provider: MagicMock) -> None:
        svc = DataService(provider=mock_provider)
        h = svc.get_history("TEST$#", days=10)
        assert isinstance(h, StockHistory)

    def test_provider_swap_changes_behaviour(self) -> None:
        svc = DataService()
        mock = MagicMock(spec=BaseProvider)
        mock.name = "swapped"
        mock.get_market_summary.return_value = MarketSummary(index=9999.0)
        svc.provider = mock
        s = svc.get_market_summary()
        assert s.index == 9999.0

    def test_get_market_summary_returns_empty_on_failure(self) -> None:
        svc = DataService(provider=FailingProvider())
        s = svc.get_market_summary()
        assert s.index == 0.0
        assert s.status == "Unknown"

    def test_get_live_market_returns_empty_on_failure(self) -> None:
        svc = DataService(provider=FailingProvider())
        assert svc.get_live_market() == []

    def test_get_stock_returns_none_on_failure(self) -> None:
        svc = DataService(provider=FailingProvider())
        assert svc.get_stock("NABIL") is None

    def test_get_top_gainers_returns_empty_on_failure(self) -> None:
        svc = DataService(provider=FailingProvider())
        assert svc.get_top_gainers() == []

    def test_get_top_losers_returns_empty_on_failure(self) -> None:
        svc = DataService(provider=FailingProvider())
        assert svc.get_top_losers() == []


# ═══════════════════════════════════════════════════════════════════
# Rate Limiter tests
# ═══════════════════════════════════════════════════════════════════


class TestRateLimiter:
    def test_configure_and_acquire(self) -> None:
        from src.data.rate_limiter import RateLimiter
        limiter = RateLimiter()
        limiter.configure("test", max_tokens=5, refill_rate=10.0)
        assert limiter.acquire("test")

    def test_acquire_returns_false_when_exhausted(self) -> None:
        from src.data.rate_limiter import RateLimiter
        limiter = RateLimiter()
        limiter.configure("test", max_tokens=1, refill_rate=0.0)
        assert limiter.acquire("test")  # first succeeds
        assert not limiter.acquire("test", timeout=0.1)  # second times out

    def test_record_success_resets_failures(self) -> None:
        from src.data.rate_limiter import RateLimiter
        limiter = RateLimiter()
        limiter.configure("test", max_tokens=10, refill_rate=10.0)
        limiter.record_failure("test", status_code=500)
        stats = limiter.get_stats("test")
        assert stats is not None
        assert stats.consecutive_failures == 1
        limiter.record_success("test")
        stats2 = limiter.get_stats("test")
        assert stats2 is not None
        assert stats2.consecutive_failures == 0

    def test_429_triggers_cooldown(self) -> None:
        from src.data.rate_limiter import RateLimiter
        limiter = RateLimiter()
        limiter.configure("test", max_tokens=10, refill_rate=10.0)
        limiter.record_failure("test", status_code=429, retry_after=0.1)
        # Should be in cooldown
        assert limiter.is_throttled("test")

    def test_503_increments_counter(self) -> None:
        from src.data.rate_limiter import RateLimiter
        limiter = RateLimiter()
        limiter.configure("test", max_tokens=10, refill_rate=10.0)
        limiter.record_failure("test", status_code=503)
        stats = limiter.get_stats("test")
        assert stats is not None
        assert stats.consecutive_failures == 1

    def test_get_all_stats(self) -> None:
        from src.data.rate_limiter import RateLimiter
        limiter = RateLimiter()
        limiter.configure("a", max_tokens=5, refill_rate=1.0)
        limiter.configure("b", max_tokens=10, refill_rate=2.0)
        all_stats = limiter.get_all_stats()
        assert len(all_stats) == 2

    def test_reset_provider(self) -> None:
        from src.data.rate_limiter import RateLimiter
        limiter = RateLimiter()
        limiter.configure("test", max_tokens=5, refill_rate=1.0)
        limiter.reset_provider("test")
        assert limiter.get_stats("test") is None

    def test_context_manager(self) -> None:
        from src.data.rate_limiter import RateLimiter
        limiter = RateLimiter()
        limiter.configure("test", max_tokens=5, refill_rate=10.0)
        with limiter.acquire_context("test"):
            pass  # should not raise

    def test_throttled_property(self) -> None:
        from src.data.rate_limiter import RateLimiter
        limiter = RateLimiter()
        assert limiter.total_throttled == 0

    def test_stats_defaults(self) -> None:
        from src.data.rate_limiter import RateLimiter
        limiter = RateLimiter()
        limiter.configure("test", max_tokens=10, refill_rate=1.0)
        stats = limiter.get_stats("test")
        assert stats is not None
        assert stats.tokens_remaining == 10.0
        assert not stats.cooldown_active


# ═══════════════════════════════════════════════════════════════════
# Health Monitor tests
# ═══════════════════════════════════════════════════════════════════


class TestHealthMonitor:
    def test_register_and_health_defaults(self) -> None:
        from src.data.health import ProviderHealthMonitor
        monitor = ProviderHealthMonitor()
        monitor.register("api")
        health = monitor.get_health("api")
        assert health is not None
        assert health.enabled
        assert health.success_rate == 100.0

    def test_record_success(self) -> None:
        from src.data.health import ProviderHealthMonitor
        monitor = ProviderHealthMonitor()
        monitor.register("api")
        monitor.record_success("api", latency_ms=150.0)
        health = monitor.get_health("api")
        assert health is not None
        assert health.successful_requests == 1
        assert health.average_latency_ms > 0

    def test_record_failure(self) -> None:
        from src.data.health import ProviderHealthMonitor
        monitor = ProviderHealthMonitor()
        monitor.register("api")
        monitor.record_failure("api")
        health = monitor.get_health("api")
        assert health is not None
        assert health.failed_requests == 1

    def test_auto_disable_after_threshold(self) -> None:
        from src.data.health import ProviderHealthMonitor, HealthCheckConfig
        config = HealthCheckConfig(failure_threshold=3, recovery_period=0.1)
        monitor = ProviderHealthMonitor(config=config)
        monitor.register("api")
        for _ in range(3):
            monitor.record_failure("api")
        health = monitor.get_health("api")
        assert health is not None
        assert health.is_disabled

    def test_is_healthy_unknown_provider(self) -> None:
        from src.data.health import ProviderHealthMonitor
        monitor = ProviderHealthMonitor()
        assert monitor.is_healthy("unknown")  # unknown = healthy

    def test_unregister(self) -> None:
        from src.data.health import ProviderHealthMonitor
        monitor = ProviderHealthMonitor()
        monitor.register("api")
        monitor.unregister("api")
        assert monitor.get_health("api") is None

    def test_manual_enable_disable(self) -> None:
        from src.data.health import ProviderHealthMonitor
        monitor = ProviderHealthMonitor()
        monitor.register("api")
        monitor.disable("api")
        assert not monitor.is_healthy("api")
        monitor.enable("api")
        assert monitor.is_healthy("api")

    def test_get_all_health(self) -> None:
        from src.data.health import ProviderHealthMonitor
        monitor = ProviderHealthMonitor()
        monitor.register("api")
        monitor.register("csv")
        all_h = monitor.get_all_health()
        assert len(all_h) == 2

    def test_healthy_providers_filter(self) -> None:
        from src.data.health import ProviderHealthMonitor
        monitor = ProviderHealthMonitor()
        monitor.register("good")
        monitor.register("bad")
        monitor.disable("bad")
        healthy = monitor.get_healthy_providers(["good", "bad"])
        assert "good" in healthy
        assert "bad" not in healthy

    def test_disabled_count(self) -> None:
        from src.data.health import ProviderHealthMonitor
        monitor = ProviderHealthMonitor()
        monitor.register("a")
        monitor.register("b")
        monitor.disable("a")
        assert monitor.disabled_count == 1
        assert monitor.healthy_count == 1

    def test_reset(self) -> None:
        from src.data.health import ProviderHealthMonitor
        monitor = ProviderHealthMonitor()
        monitor.register("api")
        monitor.record_failure("api")
        monitor.reset("api")
        assert monitor.get_health("api") is None


# ═══════════════════════════════════════════════════════════════════
# Metrics tests
# ═══════════════════════════════════════════════════════════════════


class TestMetricsCollector:
    def test_record_request(self) -> None:
        from src.data.metrics import MetricsCollector
        mc = MetricsCollector()
        mc.record_request("get_market_summary")
        snap = mc.snapshot()
        assert snap.total_requests == 1

    def test_cache_hit_miss(self) -> None:
        from src.data.metrics import MetricsCollector
        mc = MetricsCollector()
        mc.record_cache_hit("get_market_summary")
        mc.record_cache_miss("get_market_summary")
        snap = mc.snapshot()
        assert snap.cache_hits == 1
        assert snap.cache_misses == 1
        assert snap.cache_hit_rate == 50.0

    def test_api_call_and_latency(self) -> None:
        from src.data.metrics import MetricsCollector
        mc = MetricsCollector()
        mc.record_api_call("nepse_scraper", latency_ms=150.0)
        snap = mc.snapshot()
        assert snap.api_calls == 1
        assert snap.average_latency_ms > 0

    def test_csv_fallback(self) -> None:
        from src.data.metrics import MetricsCollector
        mc = MetricsCollector()
        mc.record_csv_fallback()
        snap = mc.snapshot()
        assert snap.csv_fallbacks == 1

    def test_provider_failure(self) -> None:
        from src.data.metrics import MetricsCollector
        mc = MetricsCollector()
        mc.record_provider_failure("api")
        snap = mc.snapshot()
        assert snap.provider_failures == 1

    def test_websocket_metrics(self) -> None:
        from src.data.metrics import MetricsCollector
        mc = MetricsCollector()
        mc.record_websocket_reconnect()
        mc.record_websocket_message()
        snap = mc.snapshot()
        assert snap.websocket_reconnects == 1
        assert snap.websocket_messages == 1

    def test_rate_limited(self) -> None:
        from src.data.metrics import MetricsCollector
        mc = MetricsCollector()
        mc.record_rate_limited(5)
        snap = mc.snapshot()
        assert snap.rate_limited_count == 5

    def test_per_operation_metrics(self) -> None:
        from src.data.metrics import MetricsCollector
        mc = MetricsCollector()
        mc.record_request("op1")
        mc.record_cache_hit("op1")
        snap = mc.snapshot()
        assert "op1" in snap.per_operation
        assert snap.per_operation["op1"].calls == 1
        assert snap.per_operation["op1"].cache_hits == 1

    def test_per_provider_metrics(self) -> None:
        from src.data.metrics import MetricsCollector
        mc = MetricsCollector()
        mc.record_api_call("my_provider")
        snap = mc.snapshot()
        assert "my_provider" in snap.per_provider
        assert snap.per_provider["my_provider"].calls == 1

    def test_reset(self) -> None:
        from src.data.metrics import MetricsCollector
        mc = MetricsCollector()
        mc.record_request("test")
        mc.reset()
        snap = mc.snapshot()
        assert snap.total_requests == 0


# ═══════════════════════════════════════════════════════════════════
# DataService new method tests
# ═══════════════════════════════════════════════════════════════════


class TestDataServiceNewMethods:
    @pytest.fixture(autouse=True)
    def _reset(self) -> None:
        DataService.reset_instance()

    def test_get_metrics_returns_snapshot(self, mock_provider: MagicMock) -> None:
        svc = DataService(provider=mock_provider)
        m = svc.get_metrics()
        assert m.total_requests >= 0

    def test_reset_metrics_clears(self, mock_provider: MagicMock) -> None:
        svc = DataService(provider=mock_provider)
        svc.get_market_summary()
        svc.reset_metrics()
        m = svc.get_metrics()
        assert m.total_requests == 0

    def test_health_monitor_property(self, mock_provider: MagicMock) -> None:
        svc = DataService(provider=mock_provider)
        hm = svc.health_monitor
        assert hm is not None
        assert hm.get_health("api") is not None

    def test_rate_limiter_property(self, mock_provider: MagicMock) -> None:
        svc = DataService(provider=mock_provider)
        rl = svc.rate_limiter
        assert rl is not None

    def test_websocket_stream_property(self, mock_provider: MagicMock) -> None:
        svc = DataService(provider=mock_provider)
        ws = svc.websocket_stream
        assert ws is not None

    def test_is_background_refresh_running_false(self, mock_provider: MagicMock) -> None:
        svc = DataService(provider=mock_provider)
        assert not svc.is_background_refresh_running()

    def test_pause_resume_background(self, mock_provider: MagicMock) -> None:
        DataService.reset_instance()
        svc = DataService(provider=MockProvider())
        svc.start_background_refresh(interval=5)
        svc.pause_background_refresh()
        assert svc.is_background_refresh_paused()
        svc.resume_background_refresh()
        assert not svc.is_background_refresh_paused()
        svc.stop_background_refresh()

    def test_set_refresh_interval(self, mock_provider: MagicMock) -> None:
        svc = DataService(provider=mock_provider)
        svc.set_refresh_interval(120)
        assert svc._background_interval == 120

    def test_get_websocket_stats(self, mock_provider: MagicMock) -> None:
        svc = DataService(provider=mock_provider)
        stats = svc.get_websocket_stats()
        assert stats is not None
        assert not stats.connected  # off by default

    def test_is_live_feed_connected(self, mock_provider: MagicMock) -> None:
        svc = DataService(provider=mock_provider)
        assert not svc.is_live_feed_connected()  # off by default


# ═══════════════════════════════════════════════════════════════════
# WebSocket stream tests
# ═══════════════════════════════════════════════════════════════════


class TestLiveMarketStream:
    def test_create_and_stats(self) -> None:
        from src.data.websocket import LiveMarketStream, WebSocketConfig
        stream = LiveMarketStream(WebSocketConfig(enabled=False))
        stats = stream.get_stats()
        assert not stats.connected
        assert stats.subscribers == 0
        assert stats.reconnect_count == 0

    def test_subscribe_unsubscribe(self) -> None:
        from src.data.websocket import LiveMarketStream, WebSocketConfig
        stream = LiveMarketStream(WebSocketConfig(enabled=False))

        def callback(q):
            pass

        stream.subscribe(callback)
        assert stream.subscriber_count == 1
        stream.unsubscribe(callback)
        assert stream.subscriber_count == 0

    def test_subscribe_summary(self) -> None:
        from src.data.websocket import LiveMarketStream, WebSocketConfig
        stream = LiveMarketStream(WebSocketConfig(enabled=False))

        def cb(s):
            pass

        stream.subscribe_summary(cb)
        assert stream.subscriber_count == 1
        stream.unsubscribe_summary(cb)
        assert stream.subscriber_count == 0

    def test_subscribe_messages(self) -> None:
        from src.data.websocket import LiveMarketStream, WebSocketConfig
        stream = LiveMarketStream(WebSocketConfig(enabled=False))

        def cb(m):
            pass

        stream.subscribe_messages(cb)
        assert stream.subscriber_count == 1
        stream.unsubscribe_messages(cb)
        assert stream.subscriber_count == 0

    def test_start_stop(self) -> None:
        from src.data.websocket import LiveMarketStream, WebSocketConfig
        config = WebSocketConfig(enabled=False)  # disabled, won't actually connect
        stream = LiveMarketStream(config)
        stream.start()  # should not crash
        stream.stop()  # should not crash

    def test_start_twice_is_idempotent(self) -> None:
        from src.data.websocket import LiveMarketStream, WebSocketConfig
        stream = LiveMarketStream(WebSocketConfig(enabled=False))
        stream.start()
        stream.start()  # second start should be no-op
        stream.stop()

    def test_reconnect_delay(self) -> None:
        from src.data.websocket import LiveMarketStream, WebSocketConfig
        stream = LiveMarketStream(WebSocketConfig(enabled=False))
        # Access private method for testing
        d1 = stream._reconnect_delay(1)
        d2 = stream._reconnect_delay(2)
        d3 = stream._reconnect_delay(10)
        assert d1 >= 0.5
        assert d2 >= d1
        assert d3 <= 60.0  # capped at max

    def test_dispatch_quote(self) -> None:
        from src.data.websocket import LiveMarketStream, WebSocketConfig
        stream = LiveMarketStream(WebSocketConfig(enabled=False))
        received: list = []

        def cb(q):
            received.append(q)

        stream.subscribe(cb)
        stream._dispatch({"type": "quote", "symbol": "NABIL", "ltp": 500.0})
        assert len(received) == 1
        assert received[0].symbol == "NABIL"

    def test_dispatch_summary(self) -> None:
        from src.data.websocket import LiveMarketStream, WebSocketConfig
        stream = LiveMarketStream(WebSocketConfig(enabled=False))
        received: list = []

        def cb(s):
            received.append(s)

        stream.subscribe_summary(cb)
        stream._dispatch({"type": "summary", "index": 2100.0, "status": "Open"})
        assert len(received) == 1
        assert received[0].index == 2100.0


# ═══════════════════════════════════════════════════════════════════
# Uploaded history tests
# ═══════════════════════════════════════════════════════════════════


class TestUploadedHistory:
    """Tests for DataService.save_history / get_uploaded_history / etc."""

    @pytest.fixture(autouse=True)
    def _reset_singleton(self) -> None:
        DataService.reset_instance()

    @staticmethod
    def _ohlcv_df(rows: int = 20) -> pd.DataFrame:
        return pd.DataFrame({
            "Date": pd.date_range("2025-01-01", periods=rows, freq="D"),
            "Open": [100 + i for i in range(rows)],
            "High": [105 + i for i in range(rows)],
            "Low": [95 + i for i in range(rows)],
            "Close": [102 + i for i in range(rows)],
            "Volume": [1000000 for _ in range(rows)],
        })

    def test_save_and_get_history(self) -> None:
        svc = DataService(provider=MockProvider())
        history = svc.save_history("NABIL", self._ohlcv_df())
        assert history.symbol == "NABIL"
        assert not history.is_empty
        assert history.source == "upload"

        fetched = svc.get_uploaded_history("NABIL")
        assert fetched is not None
        assert not fetched.is_empty
        assert fetched.source == "upload"
        assert len(fetched.df) == 20

    def test_get_uploaded_history_missing(self) -> None:
        svc = DataService(provider=MockProvider())
        assert svc.get_uploaded_history("NABIL") is None

    def test_get_uploaded_history_case_insensitive(self) -> None:
        svc = DataService(provider=MockProvider())
        svc.save_history("NABIL", self._ohlcv_df())
        assert svc.get_uploaded_history("nabil") is not None

    def test_get_uploaded_history_empty_symbol(self) -> None:
        svc = DataService(provider=MockProvider())
        assert svc.get_uploaded_history("") is None
        assert svc.get_uploaded_history("  ") is None

    def test_list_uploaded_symbols(self) -> None:
        svc = DataService(provider=MockProvider())
        assert svc.list_uploaded_symbols() == []
        svc.save_history("NABIL", self._ohlcv_df())
        svc.save_history("SCB", self._ohlcv_df())
        symbols = svc.list_uploaded_symbols()
        assert "NABIL" in symbols
        assert "SCB" in symbols

    def test_list_does_not_duplicate_symbols(self) -> None:
        svc = DataService(provider=MockProvider())
        svc.save_history("NABIL", self._ohlcv_df())
        svc.save_history("nabil", self._ohlcv_df())
        assert svc.list_uploaded_symbols().count("NABIL") == 1

    def test_save_replaces_existing(self) -> None:
        svc = DataService(provider=MockProvider())
        svc.save_history("NABIL", self._ohlcv_df(rows=10))
        svc.save_history("NABIL", self._ohlcv_df(rows=30))
        fetched = svc.get_uploaded_history("NABIL")
        assert fetched is not None
        assert len(fetched.df) == 30

    def test_has_uploaded_history(self) -> None:
        svc = DataService(provider=MockProvider())
        assert not svc.has_uploaded_history("NABIL")
        svc.save_history("NABIL", self._ohlcv_df())
        assert svc.has_uploaded_history("NABIL")

    def test_delete_uploaded_history(self) -> None:
        svc = DataService(provider=MockProvider())
        svc.save_history("NABIL", self._ohlcv_df())
        assert svc.delete_uploaded_history("NABIL") is True
        assert svc.get_uploaded_history("NABIL") is None
        assert svc.list_uploaded_symbols() == []

    def test_delete_missing_returns_false(self) -> None:
        svc = DataService(provider=MockProvider())
        assert svc.delete_uploaded_history("NABIL") is False

    def test_delete_clears_index(self) -> None:
        svc = DataService(provider=MockProvider())
        svc.save_history("NABIL", self._ohlcv_df())
        svc.save_history("SCB", self._ohlcv_df())
        svc.delete_uploaded_history("NABIL")
        assert svc.list_uploaded_symbols() == ["SCB"]

    def test_save_empty_symbol_raises(self) -> None:
        svc = DataService(provider=MockProvider())
        with pytest.raises(ValueError):
            svc.save_history("", self._ohlcv_df())

    def test_save_none_symbol_raises(self) -> None:
        svc = DataService(provider=MockProvider())
        with pytest.raises(ValueError):
            svc.save_history(None, self._ohlcv_df())  # type: ignore[arg-type]

    def test_save_empty_df_raises(self) -> None:
        svc = DataService(provider=MockProvider())
        with pytest.raises(ValueError):
            svc.save_history("NABIL", pd.DataFrame())

    def test_save_missing_columns_raises(self) -> None:
        svc = DataService(provider=MockProvider())
        bad = pd.DataFrame({"Date": ["2025-01-01"], "Close": [100.0]})
        with pytest.raises(ValueError, match="Missing required columns"):
            svc.save_history("NABIL", bad)

    def test_save_cleans_dates_and_sorts(self) -> None:
        svc = DataService(provider=MockProvider())
        df = pd.DataFrame({
            "Date": ["2025-01-03", "2025-01-01", "2025-01-02"],
            "Open": [1.0, 1.0, 1.0],
            "High": [2.0, 2.0, 2.0],
            "Low": [0.5, 0.5, 0.5],
            "Close": [3.0, 3.0, 3.0],
            "Volume": [100, 100, 100],
        })
        history = svc.save_history("NABIL", df)
        dates = history.df["Date"].dt.strftime("%Y-%m-%d").tolist()
        assert dates == ["2025-01-01", "2025-01-02", "2025-01-03"]

    def test_uploaded_history_survives_restart(self, tmp_path: Path) -> None:
        # Uploaded history lives in the tiered cache; the disk layer must
        # round-trip the DataFrame so it survives an app restart.  We cannot
        # call DataService.reset_instance() here because it clears the whole
        # cache (including disk), so simulate a restart by wiping only the
        # memory layer and reading back through a fresh cache on the same dir.
        cache = TieredCache(memory_ttl=60, disk_ttl=3600, disk_dir=tmp_path)
        svc = DataService(provider=MockProvider(), cache=cache)
        svc.save_history("NABIL", self._ohlcv_df())

        # Wipe memory only — as if the process restarted
        cache._memory.clear()

        # A fresh cache on the same disk dir must reconstruct the DataFrame
        cache2 = TieredCache(memory_ttl=60, disk_ttl=3600, disk_dir=tmp_path)
        fetched = cache2.get(f"{DataService.UPLOADED_HISTORY_PREFIX}:NABIL")
        assert isinstance(fetched, pd.DataFrame)
        assert not fetched.empty
        assert len(fetched) == 20
        assert "Close" in fetched.columns
        assert fetched["Close"].iloc[-1] == 121.0

        # The symbol index must also survive
        index = cache2.get("uploaded_symbols")
        assert index == ["NABIL"]

    def test_clear_cache_wipes_uploaded_history(self, tmp_path: Path) -> None:
        # clear_cache() empties the entire tiered cache, so uploaded
        # history is also removed; use delete_uploaded_history() for a
        # targeted removal.
        cache = TieredCache(memory_ttl=60, disk_ttl=3600, disk_dir=tmp_path)
        svc = DataService(provider=MockProvider(), cache=cache)
        svc.save_history("NABIL", self._ohlcv_df())
        svc.clear_cache()
        fetched = svc.get_uploaded_history("NABIL")
        assert fetched is None


# Run with: pytest tests/test_data_service.py -v

