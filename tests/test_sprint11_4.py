"""Sprint 11.4 tests — indicator cache, copy-on-return, data isolation.

Covers:

- Phase 4/5 — ``IndicatorCache`` correctness: content fingerprint
  invalidation (modified value, added row, removed row), parameter and
  symbol separation, empty/short frames, concurrent access, value
  identity, copy-on-return semantics.
- Phase 9/10 — mutation-leak regression for ``DataService`` cached
  mutable dataclasses (``StockQuote``, ``TopMover``, ``MarketSummary``).
- Analyzer integration — ``analyze_dataframe`` hits the cache for
  identical OHLCV frames and returns value-identical results.
"""

from __future__ import annotations

import threading

import numpy as np
import pandas as pd
import pytest

from src.engine.analyzer import analyze_dataframe
from src.indicators.cache import IndicatorCache


# ═══════════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════════


def make_ohlcv(n: int = 200, seed: int = 1, start_close: float = 100.0) -> pd.DataFrame:
    """Deterministic synthetic OHLCV frame with *n* rows."""
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2025-01-01", periods=n, freq="D")
    close = start_close + np.cumsum(rng.normal(0, 1, n))
    return pd.DataFrame(
        {
            "Date": idx,
            "Open": close + rng.normal(0, 0.5, n),
            "High": close + 2 + np.abs(rng.normal(0, 1, n)),
            "Low": close - 2 - np.abs(rng.normal(0, 1, n)),
            "Close": close,
            "Volume": rng.integers(100000, 500000, n),
        }
    )


@pytest.fixture
def cache() -> IndicatorCache:
    return IndicatorCache(max_entries=16)


# ═══════════════════════════════════════════════════════════════════
# Phase 4/5 — IndicatorCache correctness
# ═══════════════════════════════════════════════════════════════════


class TestFingerprint:
    def test_same_data_same_key(self, cache: IndicatorCache) -> None:
        df = make_ohlcv()
        assert cache.build_key(df) == cache.build_key(df.copy())

    def test_modified_value_changes_key(self, cache: IndicatorCache) -> None:
        df = make_ohlcv()
        df2 = df.copy()
        df2.loc[df2.index[-1], "Close"] += 1.0
        assert cache.build_key(df) != cache.build_key(df2)

    def test_added_row_changes_key(self, cache: IndicatorCache) -> None:
        df = make_ohlcv(n=200)
        df2 = df.copy()
        # Append a new trading day (new index, one extra row).
        df2.loc[df2.index[-1] + 1] = df2.iloc[-1]
        assert cache.build_key(df) != cache.build_key(df2)

    def test_removed_row_changes_key(self, cache: IndicatorCache) -> None:
        df = make_ohlcv(n=200)
        assert cache.build_key(df) != cache.build_key(df.iloc[:-1])

    def test_different_parameters_change_key(self) -> None:
        c1 = IndicatorCache(config=(14, 12, 26, 9))
        c2 = IndicatorCache(config=(21, 12, 26, 9))
        df = make_ohlcv()
        assert c1.build_key(df) != c2.build_key(df)

    def test_pipeline_version_changes_key(self) -> None:
        c1 = IndicatorCache(version="11.4")
        c2 = IndicatorCache(version="11.5")
        df = make_ohlcv()
        assert c1.build_key(df) != c2.build_key(df)

    def test_different_symbols_do_not_collide(self, cache: IndicatorCache) -> None:
        """Identical prices for different symbols must not share a key."""
        df = make_ohlcv()
        assert cache.build_key(df, symbol="NABIL") != cache.build_key(df, symbol="SCB")

    def test_different_symbols_no_collision_even_same_data(self, cache: IndicatorCache) -> None:
        df1 = make_ohlcv(seed=7)
        df2 = make_ohlcv(seed=7)  # identical values, different namespace
        assert cache.build_key(df1, symbol="A") != cache.build_key(df2, symbol="B")

    def test_empty_frame_builds_key_safely(self, cache: IndicatorCache) -> None:
        key = cache.build_key(pd.DataFrame())
        assert isinstance(key, str) and key


class TestCacheOps:
    def test_miss_returns_none(self, cache: IndicatorCache) -> None:
        assert cache.get("nope") is None

    def test_put_then_get_returns_copy(self, cache: IndicatorCache) -> None:
        df = make_ohlcv(n=60)
        key = cache.build_key(df)
        cache.put(key, df)
        got = cache.get(key)
        assert got is not None
        assert got.equals(df)
        # Copy semantics: mutating the returned frame must not change
        # the cached entry.
        got.loc[got.index[-1], "Close"] = 99999.0
        got2 = cache.get(key)
        assert got2 is not None
        assert got2.equals(df)

    def test_put_stores_copy(self, cache: IndicatorCache) -> None:
        df = make_ohlcv(n=60)
        key = cache.build_key(df)
        cache.put(key, df)
        # Mutate the caller's frame after storing — cache must be intact.
        df.loc[df.index[0], "Open"] = -1.0
        got = cache.get(key)
        assert got is not None
        assert got["Open"].iloc[0] != -1.0

    def test_empty_frame_not_cached(self, cache: IndicatorCache) -> None:
        cache.put("k", pd.DataFrame())
        assert cache.stats()["entries"] == 0

    def test_lru_eviction(self) -> None:
        c = IndicatorCache(max_entries=3)
        for i in range(5):
            df = make_ohlcv(n=50, seed=i)
            c.put(f"k{i}", df)
        stats = c.stats()
        assert stats["entries"] == 3
        # Oldest entries evicted first (k0, k1 gone).
        assert c.get("k0") is None
        assert c.get("k1") is None
        assert c.get("k4") is not None

    def test_clear(self, cache: IndicatorCache) -> None:
        df = make_ohlcv(n=60)
        cache.put(cache.build_key(df), df)
        cache.clear()
        assert cache.stats()["entries"] == 0

    def test_invalidate_config_drops_all(self, cache: IndicatorCache) -> None:
        df = make_ohlcv(n=60)
        cache.put(cache.build_key(df), df)
        cache.invalidate_config()
        assert cache.stats()["entries"] == 0

    def test_disabled_cache_never_stores(self) -> None:
        c = IndicatorCache(enabled=False)
        df = make_ohlcv(n=60)
        c.put(c.build_key(df), df)
        assert c.get(c.build_key(df)) is None
        assert c.stats()["entries"] == 0


class TestConcurrency:
    def test_concurrent_put_get_same_key(self) -> None:
        cache = IndicatorCache(max_entries=16)
        df = make_ohlcv(n=80)
        key = cache.build_key(df)
        results: list[pd.DataFrame | None] = []
        errors: list[Exception] = []
        lock = threading.Lock()

        def worker() -> None:
            try:
                cache.put(key, df)
                for _ in range(50):
                    got = cache.get(key)
                    if got is not None:
                        with lock:
                            results.append(got)
            except Exception as exc:  # noqa: BLE001
                with lock:
                    errors.append(exc)

        threads = [threading.Thread(target=worker) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors
        assert results
        # Every returned frame must be a correct copy of the stored one.
        for got in results:
            assert got.equals(df)

    def test_concurrent_distinct_keys_no_corruption(self) -> None:
        cache = IndicatorCache(max_entries=16)
        errors: list[Exception] = []

        def worker(seed: int) -> None:
            try:
                for _ in range(30):
                    df = make_ohlcv(n=50, seed=seed)
                    key = cache.build_key(df)
                    cache.put(key, df)
                    got = cache.get(key)
                    assert got is not None and got.equals(df)
            except Exception as exc:  # noqa: BLE001
                errors.append(exc)

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert not errors


# ═══════════════════════════════════════════════════════════════════
# Analyzer integration
# ═══════════════════════════════════════════════════════════════════


class TestAnalyzerCacheIntegration:
    def test_repeated_analysis_hits_cache(self, monkeypatch) -> None:
        from src.engine import analyzer
        from src.indicators.cache import indicator_cache

        indicator_cache.clear()
        calls = {"n": 0}
        original = analyzer.add_moving_averages

        def counting(df, inplace=False):
            calls["n"] += 1
            return original(df, inplace=inplace)

        monkeypatch.setattr(analyzer, "add_moving_averages", counting)
        df = make_ohlcv(n=200)
        r1 = analyzer.analyze_dataframe(df, symbol="NABIL")
        assert calls["n"] == 1
        r2 = analyzer.analyze_dataframe(df, symbol="NABIL")
        assert calls["n"] == 1  # cache hit — indicator chain not re-run
        assert r1 == r2

    def test_modified_data_recomputes(self, monkeypatch) -> None:
        from src.engine import analyzer
        from src.indicators.cache import indicator_cache

        indicator_cache.clear()
        calls = {"n": 0}
        original = analyzer.add_moving_averages

        def counting(df, inplace=False):
            calls["n"] += 1
            return original(df, inplace=inplace)

        monkeypatch.setattr(analyzer, "add_moving_averages", counting)
        df = make_ohlcv(n=200)
        analyzer.analyze_dataframe(df, symbol="NABIL")
        assert calls["n"] == 1
        df2 = df.copy()
        df2.loc[df2.index[-1], "Close"] += 5.0
        analyzer.analyze_dataframe(df2, symbol="NABIL")
        assert calls["n"] == 2  # different fingerprint -> recompute

    def test_symbols_do_not_collide_in_pipeline(self) -> None:
        from src.indicators.cache import indicator_cache

        indicator_cache.clear()
        df = make_ohlcv(n=200, seed=3)
        analyze_dataframe(df, symbol="NABIL")
        before = indicator_cache.stats()
        analyze_dataframe(df, symbol="SCB")  # same data, different symbol
        after = indicator_cache.stats()
        # Second symbol must miss (different namespace) — recomputed.
        assert after["misses"] == before["misses"] + 1

    def test_use_cache_false_skips_cache(self) -> None:
        from src.indicators.cache import indicator_cache

        indicator_cache.clear()
        df = make_ohlcv(n=120)
        r1 = analyze_dataframe(df, symbol="X", use_cache=False)
        r2 = analyze_dataframe(df, symbol="X", use_cache=True)
        assert r1 == r2  # same values either way
        assert indicator_cache.stats()["entries"] >= 1  # only use_cache=True stored

    def test_empty_frame_raises(self) -> None:
        # OHLCV columns but zero rows — the indicator chain produces an
        # empty frame and the pipeline raises (matches pre-cache
        # behaviour in test_analyzer.py).
        empty = pd.DataFrame(columns=["Date", "Open", "High", "Low", "Close", "Volume"])
        with pytest.raises(ValueError, match="No rows remaining"):
            analyze_dataframe(empty)

    def test_short_frame_preserves_nan_behaviour(self) -> None:
        df = make_ohlcv(n=30)
        r1 = analyze_dataframe(df, symbol="SHORT")
        r2 = analyze_dataframe(df, symbol="SHORT")  # cache hit
        assert r1 == r2

    def test_value_identity_cached_vs_uncached(self) -> None:
        from src.indicators.cache import indicator_cache

        indicator_cache.clear()
        df = make_ohlcv(n=250, seed=11)
        # Force a cold run with cache disabled, then a cached run.
        cold = analyze_dataframe(df, symbol="ID", use_cache=False)
        warm1 = analyze_dataframe(df, symbol="ID", use_cache=True)
        warm2 = analyze_dataframe(df, symbol="ID", use_cache=True)
        assert warm1 == cold
        assert warm2 == cold

    def test_input_frame_not_mutated(self) -> None:
        df = make_ohlcv(n=200)
        snapshot = df.copy()
        analyze_dataframe(df, symbol="MUT")
        pd.testing.assert_frame_equal(df, snapshot)

    def test_cached_frame_not_leaked_to_caller(self) -> None:
        from src.indicators.cache import indicator_cache

        indicator_cache.clear()
        df = make_ohlcv(n=150)
        r1 = analyze_dataframe(df, symbol="LEAK")
        key = indicator_cache.build_key(df, symbol="LEAK")

        # The internal cached frame must be handed out as a copy: a
        # caller mutating what ``get`` returned cannot corrupt the
        # entry, and a subsequent analysis is unchanged.
        got = indicator_cache.get(key)
        assert got is not None
        got.loc[got.index[-1], "Close"] = -1.0
        got2 = indicator_cache.get(key)
        assert got2 is not None
        assert got2["Close"].iloc[-1] != -1.0
        r2 = analyze_dataframe(df, symbol="LEAK")
        assert r2 == r1


# ═══════════════════════════════════════════════════════════════════
# Phase 9/10 — DataService mutation-leak regression
# ═══════════════════════════════════════════════════════════════════


class TestLiveMarketIsolation:
    def test_mutating_returned_quote_does_not_corrupt_cache(self) -> None:
        from src.data import DataService, TieredCache
        from src.data.models import StockQuote

        DataService.reset_instance()
        svc = DataService(
            provider=_FixedQuoteProvider(),
            cache=TieredCache(memory_ttl=0, disk_ttl=0, disk_dir=None),
        )
        quotes = svc.get_live_market()
        assert quotes
        quotes[0].ltp = 99999.0  # caller mutates the returned quote

        quotes2 = svc.get_live_market()
        # Internal cache must be unchanged.
        assert quotes2[0].ltp == 500.0

    def test_mutating_list_does_not_corrupt_cache(self) -> None:
        from src.data import DataService, TieredCache

        DataService.reset_instance()
        svc = DataService(
            provider=_FixedQuoteProvider(),
            cache=TieredCache(memory_ttl=0, disk_ttl=0, disk_dir=None),
        )
        quotes = svc.get_live_market()
        quotes.append(StockQuoteFixture(symbol="CORRUPTED"))
        quotes.clear()

        quotes2 = svc.get_live_market()
        assert len(quotes2) == 1
        assert quotes2[0].symbol == "NABIL"

    def test_empty_market(self) -> None:
        from src.data import DataService, TieredCache

        DataService.reset_instance()
        svc = DataService(
            provider=_EmptyProvider(),
            cache=TieredCache(memory_ttl=0, disk_ttl=0, disk_dir=None),
        )
        assert svc.get_live_market() == []


class TestTopMoversIsolation:
    def test_mutating_top_gainers_does_not_corrupt_cache(self) -> None:
        from src.data import DataService, TieredCache

        DataService.reset_instance()
        svc = DataService(
            provider=_FixedTopMoversProvider(),
            cache=TieredCache(memory_ttl=0, disk_ttl=0, disk_dir=None),
        )
        gainers = svc.get_top_gainers()
        assert gainers
        gainers[0].change_pct = 99.0
        gainers.clear()

        gainers2 = svc.get_top_gainers()
        assert len(gainers2) == 1
        assert gainers2[0].change_pct == 5.0

    def test_mutating_top_losers_and_turnover(self) -> None:
        from src.data import DataService, TieredCache

        DataService.reset_instance()
        svc = DataService(
            provider=_FixedTopMoversProvider(),
            cache=TieredCache(memory_ttl=0, disk_ttl=0, disk_dir=None),
        )
        losers = svc.get_top_losers()
        losers[0].symbol = "MUTATED"
        turnover = svc.get_top_turnover()
        turnover.clear()

        assert svc.get_top_losers()[0].symbol == "SCB"
        assert len(svc.get_top_turnover()) == 1


class TestMarketSummaryIsolation:
    def test_mutating_returned_summary_does_not_corrupt_cache(self) -> None:
        from src.data import DataService, TieredCache

        DataService.reset_instance()
        svc = DataService(
            provider=_FixedSummaryProvider(),
            cache=TieredCache(memory_ttl=0, disk_ttl=0, disk_dir=None),
        )
        s = svc.get_market_summary()
        s.index = -1.0
        s2 = svc.get_market_summary()
        assert s2.index == 2100.0


# ═══════════════════════════════════════════════════════════════════
# Minimal deterministic providers (isolated from real data/raw)
# ═══════════════════════════════════════════════════════════════════


from src.data.models import StockQuote as StockQuoteFixture  # noqa: E402
from src.data.models import TopMover as TopMoverFixture  # noqa: E402


class _FixedQuoteProvider:
    """Provider returning a single deterministic NABIL quote."""

    name = "fixed_quotes"

    def get_live_quotes(self):
        return [StockQuoteFixture(symbol="NABIL", ltp=500.0)]

    def get_market_summary(self):
        raise NotImplementedError

    def get_history(self, symbol, days):
        raise NotImplementedError


class _FixedSummaryProvider(_FixedQuoteProvider):
    def get_market_summary(self):
        from src.data.models import MarketSummary

        return MarketSummary(index=2100.0, status="Open")


class _FixedTopMoversProvider(_FixedQuoteProvider):
    def get_top_gainers(self, limit=10):
        return [TopMoverFixture(symbol="NABIL", change_pct=5.0)]

    def get_top_losers(self, limit=10):
        return [TopMoverFixture(symbol="SCB", change_pct=-3.0)]

    def get_top_turnover(self, limit=10):
        return [TopMoverFixture(symbol="NABIL", turnover=5e6)]


class _EmptyProvider(_FixedQuoteProvider):
    def get_live_quotes(self):
        return []
