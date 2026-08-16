"""Scanner cache for the NEPSE Quant Engine.

Caches the two most expensive scanner artefacts:

- **Parsed DataFrames** — ``load_csv`` output keyed by the file's
  (path, mtime, size) fingerprint, so an unchanged file is never
  re-parsed.
- **Analysis outputs** — ``analyze_dataframe`` result keyed by the
  file fingerprint, so an unchanged stock is never re-analysed.

Cache policy (documented for operators):

- **Invalidated on source change** — the fingerprint embeds mtime and
  size, so editing a CSV immediately invalidates both entries.
- **Invalidated on expiry** — entries older than
  ``SCANNER_CACHE_TTL`` seconds are treated as misses.
- **Invalidated on configuration change** — analysis keys embed the
  indicator parameters (RSI/MACD periods), so tuning the engine
  invalidates stale analysis automatically.
- **Bounded** — at most ``SCANNER_CACHE_MAX_ENTRIES`` per tier (LRU
  eviction), so memory stays flat even for very large data sets.

Alert side effects:

- Analysis entries are produced by ``analyze_stock`` which calls
  ``process_alerts`` (alert history + ``new_alerts``).  A cache hit
  returns the *first* scan's ``new_alerts`` and does not re-run
  ``process_alerts``, so alert state is only advanced on cache misses
  — the same semantics as the whole-scan ``market_cache`` (which
  already froze alert processing for its 300 s window).  To force a
  fresh alert pass, clear the cache or touch the CSV.

Thread-safe: every public method is guarded by a single lock, which
makes the cache safe to share across the parallel scanner workers.
"""

from __future__ import annotations

import os
import threading
import time
from collections import OrderedDict
from pathlib import Path
from typing import Any

from src.config import (
    MACD_FAST,
    MACD_SIGNAL,
    MACD_SLOW,
    RSI_PERIOD,
    SCANNER_CACHE_MAX_ENTRIES,
    SCANNER_CACHE_TTL,
)


class ScannerCache:
    """LRU cache with TTL for parsed DataFrames and analysis outputs.

    Args:
        ttl: Entry lifetime in seconds.
        max_entries: Maximum number of entries per tier (LRU eviction).
    """

    def __init__(self, ttl: int = SCANNER_CACHE_TTL, max_entries: int = SCANNER_CACHE_MAX_ENTRIES) -> None:
        self._ttl = ttl
        self._max_entries = max_entries
        self._df_cache: OrderedDict[str, tuple[float, Any]] = OrderedDict()
        self._analysis_cache: OrderedDict[str, tuple[float, Any]] = OrderedDict()
        self._lock = threading.RLock()
        # Metrics: cache hits / misses per tier.
        self._df_hits = 0
        self._df_misses = 0
        self._analysis_hits = 0
        self._analysis_misses = 0

    # ── Fingerprints ─────────────────────────────────────────────

    @staticmethod
    def _file_fingerprint(path: str | Path) -> str | None:
        """Fingerprint (path, mtime_ns, size) of *path*.

        Returns ``None`` when the file cannot be stat'ed (missing or
        permission error) — callers treat that as a cache miss.

        Sprint 13.8: the identity component is the *absolute* path via
        string-only normalisation (``os.path.abspath`` for relative
        inputs) — the per-call ``Path.resolve()`` realpath syscall was
        removed from this hot path.  It performed no change detection
        (the ``mtime_ns``/``size`` stat below is what detects change),
        added ~0.5 ms of filesystem work per file on every cache
        operation, and made the benchmark's light warm-cache leg
        vulnerable to filesystem/AV interference that inflated the
        warm/cold speedup ratio.  Consequence of dropping realpath:
        a symlinked file is keyed by the path it was reached through
        (bounded duplicate entries are possible — harmless under the
        LRU cap, and no consumer relies on symlink-canonical identity).
        """
        p = Path(path)
        try:
            stat = p.stat()
        except OSError:
            return None
        # String-only normalisation: no syscall.  Absolute inputs are
        # already process-stable; relative inputs are anchored to the
        # cwd exactly as ``resolve()`` did, minus symlink following.
        identity = os.path.abspath(p) if not p.is_absolute() else str(p)
        return f"{identity}:{stat.st_mtime_ns}:{stat.st_size}"

    @staticmethod
    def _analysis_key(fp: str) -> str:
        """Analysis key = file fingerprint + indicator configuration.

        Embedding the indicator parameters means changing the RSI or
        MACD settings automatically invalidates every analysis entry.
        """
        return f"RSI{RSI_PERIOD}:MACD{MACD_FAST}_{MACD_SLOW}_{MACD_SIGNAL}:{fp}"

    # ── DataFrame tier ───────────────────────────────────────────

    def get_dataframe(self, path: str | Path) -> Any | None:
        fp = self._file_fingerprint(path)
        if fp is None:
            with self._lock:
                self._df_misses += 1
            return None
        with self._lock:
            entry = self._df_cache.get(fp)
            if entry is None:
                self._df_misses += 1
                return None
            ts, value = entry
            if time.monotonic() - ts > self._ttl:
                del self._df_cache[fp]
                self._df_misses += 1
                return None
            self._df_cache.move_to_end(fp)
            self._df_hits += 1
            # Return a copy so a consumer that mutates the frame can never
            # corrupt the cached entry (Sprint 11.1 cache correctness).
            return value.copy() if hasattr(value, "copy") else value

    def put_dataframe(self, path: str | Path, df: Any) -> None:
        fp = self._file_fingerprint(path)
        if fp is None:
            return
        with self._lock:
            self._df_cache[fp] = (time.monotonic(), df)
            self._df_cache.move_to_end(fp)
            self._evict(self._df_cache)

    # ── Analysis tier ────────────────────────────────────────────

    def get_analysis(self, path: str | Path) -> dict[str, Any] | None:
        fp = self._file_fingerprint(path)
        if fp is None:
            with self._lock:
                self._analysis_misses += 1
            return None
        key = self._analysis_key(fp)
        with self._lock:
            entry = self._analysis_cache.get(key)
            if entry is None:
                self._analysis_misses += 1
                return None
            ts, value = entry
            if time.monotonic() - ts > self._ttl:
                del self._analysis_cache[key]
                self._analysis_misses += 1
                return None
            self._analysis_cache.move_to_end(key)
            self._analysis_hits += 1
            return dict(value)  # defensive copy so callers may mutate

    def put_analysis(self, path: str | Path, analysis: dict[str, Any]) -> None:
        fp = self._file_fingerprint(path)
        if fp is None:
            return
        key = self._analysis_key(fp)
        with self._lock:
            self._analysis_cache[key] = (time.monotonic(), dict(analysis))
            self._analysis_cache.move_to_end(key)
            self._evict(self._analysis_cache)

    # ── Maintenance ──────────────────────────────────────────────

    def invalidate(self, path: str | Path | None = None) -> None:
        """Drop entries for *path* (or the whole cache when ``None``)."""
        if path is None:
            with self._lock:
                self._df_cache.clear()
                self._analysis_cache.clear()
            return
        fp = self._file_fingerprint(path)
        if fp is None:
            return
        with self._lock:
            self._df_cache.pop(fp, None)
            self._analysis_cache.pop(self._analysis_key(fp), None)

    def clear(self) -> None:
        """Alias for ``invalidate(None)`` — drop every entry."""
        self.invalidate(None)

    def _evict(self, cache: OrderedDict) -> None:
        while len(cache) > self._max_entries:
            cache.popitem(last=False)

    # ── Metrics ──────────────────────────────────────────────────

    def stats(self) -> dict[str, Any]:
        """Return hit/miss counters and current sizes for observability."""
        with self._lock:
            return {
                "dataframe_hits": self._df_hits,
                "dataframe_misses": self._df_misses,
                "analysis_hits": self._analysis_hits,
                "analysis_misses": self._analysis_misses,
                "dataframe_entries": len(self._df_cache),
                "analysis_entries": len(self._analysis_cache),
                "ttl_seconds": self._ttl,
                "max_entries": self._max_entries,
            }

    @property
    def cache_hit_rate(self) -> float:
        total = self._df_hits + self._df_misses + self._analysis_hits + self._analysis_misses
        if total == 0:
            return 0.0
        return ((self._df_hits + self._analysis_hits) / total) * 100.0


# Module-level singleton shared by the scanner, loader and metrics.
scanner_cache = ScannerCache()
