"""Fingerprint-based indicator cache (Sprint 11.4).

Caches the indicator-augmented DataFrame produced by the analysis
pipeline, keyed by a deterministic *content* fingerprint of the input
OHLCV data plus the indicator configuration and pipeline version.

Motivation (measured, Phase 1 of Sprint 11.4)
---------------------------------------------
``analyze_dataframe`` recomputes the full rolling/ewm indicator stack
(SMA/EMA/RSI/MACD/Bollinger/ATR/volume) every time identical historical
data is analysed — once per ``/api/analyze`` request, once per watchlist
scan, once per ``_live_market_scan``.  Profiling shows ~17 ms/analysis
at 500 rows, dominated by these indicator passes; the content
fingerprint costs < 1 ms.  For unchanged data the indicator columns are
deterministic, so caching them removes the recomputation entirely.

Cache policy (documented for operators)
---------------------------------------
- **key**         : sha256 over (OHLCV values + index, row count) +
                    indicator configuration (RSI/MACD periods) +
                    pipeline version + optional symbol namespace.
- **lifetime**    : process lifetime (in-memory).  Rebuilt on restart.
- **max size**    : ``INDICATOR_CACHE_MAX_ENTRIES`` (default 200), LRU
                    eviction.
- **eviction**    : least-recently-used first.
- **invalidation**: any change to the OHLCV values, the row count, the
                    index/date range, the indicator periods, or the
                    pipeline version changes the fingerprint — the key
                    is content-derived, never timestamp- or symbol-only.
- **copy semantics**: ``get`` returns ``df.copy()`` and ``put`` stores a
                    copy — cached mutable DataFrames never leak to
                    callers (Sprint 11.1 copy-on-return rule).
- **thread safety**: every public method is guarded by a single RLock,
                    safe to share across scanner workers / API threads.
"""

from __future__ import annotations

import hashlib
import threading
from collections import OrderedDict
from typing import Any

import pandas as pd

from src.config import (
    INDICATOR_CACHE_ENABLED,
    INDICATOR_CACHE_MAX_ENTRIES,
    MACD_FAST,
    MACD_SIGNAL,
    MACD_SLOW,
    RSI_PERIOD,
)

# Bump whenever an indicator formula or default period changes so cached
# columns computed under an older pipeline are never reused.
INDICATOR_PIPELINE_VERSION = "11.4"

_OHLCV_COLUMNS = ("Date", "Open", "High", "Low", "Close", "Volume")


def _content_fingerprint(df: pd.DataFrame) -> str:
    """Deterministic content hash of an OHLCV frame.

    Hashes every row value plus the index (dates), so any change to the
    data, the row count, or the date range changes the digest.  Falls
    back to the frame's repr bytes for exotic dtypes.
    """
    try:
        # Vectorised, deterministic; includes the index when requested.
        hashes = pd.util.hash_pandas_object(df, index=True)
        payload = hashes.to_numpy().tobytes()
    except Exception:  # noqa: BLE001 - exotic dtype fallback
        payload = repr(df).encode("utf-8", errors="replace")
    return hashlib.sha256(payload).hexdigest()


class IndicatorCache:
    """Bounded, thread-safe LRU cache for indicator-augmented frames.

    Args:
        enabled: Master switch (config ``INDICATOR_CACHE_ENABLED``).
        max_entries: LRU bound (config ``INDICATOR_CACHE_MAX_ENTRIES``).
        config: Indicator-config tuple embedded in the key.  Overridable
            for tests that want to simulate a config change.
        version: Pipeline-version string embedded in the key.
    """

    def __init__(
        self,
        enabled: bool | None = None,
        max_entries: int | None = None,
        config: tuple[int, int, int, int] | None = None,
        version: str | None = None,
    ) -> None:
        self._enabled = INDICATOR_CACHE_ENABLED if enabled is None else enabled
        self._max_entries = max(
            1, INDICATOR_CACHE_MAX_ENTRIES if max_entries is None else max_entries
        )
        self._config = config or (RSI_PERIOD, MACD_FAST, MACD_SLOW, MACD_SIGNAL)
        self._version = version or INDICATOR_PIPELINE_VERSION
        self._cache: OrderedDict[str, pd.DataFrame] = OrderedDict()
        self._lock = threading.RLock()
        self._hits = 0
        self._misses = 0

    # ── Key ──────────────────────────────────────────────────────

    def build_key(self, df: pd.DataFrame, symbol: str | None = None) -> str:
        """Return the cache key for *df* (raw OHLCV frame, no indicators).

        The key is a content fingerprint, never symbol-only: two frames
        with identical values share a key only when their indicator
        columns would be identical.  The optional *symbol* namespace
        prevents different symbols with coincidentally identical prices
        from sharing entries (defensive, per Sprint 11.4 Phase 5).
        """
        digest = _content_fingerprint(df)
        cfg = ":".join(str(c) for c in self._config)
        ns = symbol or ""
        return f"{self._version}|{cfg}|{ns}|{digest}"

    # ── Read / write ─────────────────────────────────────────────

    def get(self, key: str) -> pd.DataFrame | None:
        """Return a *copy* of the cached frame, or ``None`` on a miss."""
        if not self._enabled:
            return None
        with self._lock:
            entry = self._cache.get(key)
            if entry is None:
                self._misses += 1
                return None
            self._cache.move_to_end(key)
            self._hits += 1
            return entry.copy()

    def put(self, key: str, df: pd.DataFrame) -> None:
        """Store a *copy* of the indicator-augmented frame under *key*."""
        if not self._enabled or df is None or df.empty:
            return
        with self._lock:
            self._cache[key] = df.copy()
            self._cache.move_to_end(key)
            self._evict()

    # ── Maintenance ──────────────────────────────────────────────

    def _evict(self) -> None:
        while len(self._cache) > self._max_entries:
            self._cache.popitem(last=False)

    def clear(self) -> None:
        with self._lock:
            self._cache.clear()

    def reset_stats(self) -> None:
        """Zero the hit/miss counters (entries are left intact).

        Benchmarks use this so a warm-only hit rate can be measured
        without cold-phase misses polluting the ratio (Sprint 11.4).
        """
        with self._lock:
            self._hits = 0
            self._misses = 0

    def invalidate_config(self) -> None:
        """Drop all entries (config/version changed)."""
        self.clear()

    # ── Metrics ──────────────────────────────────────────────────

    def stats(self) -> dict[str, Any]:
        with self._lock:
            total = self._hits + self._misses
            return {
                "hits": self._hits,
                "misses": self._misses,
                "entries": len(self._cache),
                "max_entries": self._max_entries,
                "hit_rate": (self._hits / total) if total else 0.0,
                "enabled": self._enabled,
                "pipeline_version": self._version,
            }


# Module-level singleton shared by the analyzer / API / live-market scan.
indicator_cache = IndicatorCache()
