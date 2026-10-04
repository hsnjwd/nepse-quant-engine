"""Cache layer for the NEPSE Quant Engine data service.

Provides two cache implementations:

- ``MemoryCache`` — in-process dict with TTL expiry (fastest, no I/O)
- ``DiskCache`` — JSON-file-backed with TTL expiry (survives restarts)
- ``TieredCache`` — composes memory + disk for fast + persistent

All caches share the same ``get``/``set``/``delete``/``clear`` interface.
"""

from __future__ import annotations

import dataclasses
import json
import logging
import os
import threading
import time
from dataclasses import dataclass
from datetime import datetime, date
from pathlib import Path
from typing import Any, get_type_hints

import pandas as pd
import numpy as np

from src.utils.json_store import save_json

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════
# Internal entry type
# ═══════════════════════════════════════════════════════════════════


@dataclass
class _CacheEntry:
    data: Any = None
    timestamp: float = 0.0
    ttl: int = 300

    @property
    def is_expired(self) -> bool:
        return (time.time() - self.timestamp) > self.ttl


# ═══════════════════════════════════════════════════════════════════
# JSON serialization helpers for DiskCache
# ═══════════════════════════════════════════════════════════════════

# Type registry: maps type qualname -> constructor (for round-trip reconstruction)
_DATACLASS_TYPE_REGISTRY: dict[str, type] = {}


def _register_type(cls: type) -> type:
    """Register a dataclass or other type for DiskCache round-trip."""
    _DATACLASS_TYPE_REGISTRY[cls.__qualname__] = cls
    return cls


def _is_dataclass_instance(obj: Any) -> bool:
    """True if *obj* is an instance of a dataclass."""
    return dataclasses.is_dataclass(obj) and not isinstance(obj, type)


def _serialize_for_disk(value: Any) -> Any:
    """Convert *value* into a JSON-safe structure.

    Handles:
    - Dataclass instances → ``{"__type__": "...", "__dc__": True, ...fields...}``
    - ``pd.DataFrame``   → ``{"__type__": "DataFrame", "__pd__": True, "columns": [...], "data": [...]}``
    - ``pd.Timestamp``   → ISO-format string
    - ``datetime`` / ``date`` → ISO-format string
    - Everything else passed through unchanged (``json.dump`` will handle natively).
    """
    if _is_dataclass_instance(value):
        fields = dataclasses.asdict(value)
        # Handle nested dataclass fields (e.g. df in StockHistory)
        serialized_fields: dict[str, Any] = {}
        for fname, fval in fields.items():
            serialized_fields[fname] = _serialize_for_disk(fval)
        return {"__type__": type(value).__qualname__, "__dc__": True, **serialized_fields}

    if isinstance(value, pd.DataFrame):
        return {
            "__type__": "DataFrame",
            "__pd__": True,
            "columns": list(value.columns),
            "dtypes": {c: str(value[c].dtype) for c in value.columns},
            "data": _serialize_for_disk(value.to_dict(orient="records")),
        }

    if isinstance(value, pd.Timestamp):
        return value.isoformat()

    if isinstance(value, (datetime, date)):
        return value.isoformat()

    # Numpy scalars (e.g. from pd.to_numeric) are not JSON-serialisable.
    if isinstance(value, (np.integer, np.floating, np.bool_)):
        return value.item()

    # Recurse into containers so dataclass payloads (lists of quotes,
    # lists of top movers, DataFrame records, etc.) survive round-trips.
    if isinstance(value, (list, tuple)):
        return [_serialize_for_disk(item) for item in value]

    if isinstance(value, dict):
        return {k: _serialize_for_disk(v) for k, v in value.items()}

    return value


def _deserialize_from_disk(data: Any) -> Any:
    """Reverse ``_serialize_for_disk``.

    Recognises ``__type__`` / ``__dc__`` / ``__pd__`` markers and
    reconstructs the original Python objects.
    """
    if isinstance(data, list):
        return [_deserialize_from_disk(item) for item in data]

    if not isinstance(data, dict):
        return data

    type_marker = data.get("__type__")

    # Dataclass reconstruction
    if data.get("__dc__") and type_marker:
        cls = _DATACLASS_TYPE_REGISTRY.get(type_marker)
        if cls is not None:
            # Reconstruct nested fields first, then pass as dict
            raw: dict[str, Any] = {}
            init_fields = {f.name for f in dataclasses.fields(cls)}
            for key, val in data.items():
                if key.startswith("__"):
                    continue
                if key in init_fields:
                    raw[key] = _deserialize_from_disk(val)
            # Restore datetime / date fields serialised as ISO strings.
            try:
                hints = get_type_hints(cls)
            except Exception:
                hints = {}
            for key, val in list(raw.items()):
                hint = hints.get(key)
                if isinstance(val, str) and hint in (datetime, date):
                    try:
                        raw[key] = datetime.fromisoformat(val)
                    except ValueError:
                        pass
            try:
                return cls(**raw)
            except Exception as exc:
                logger.warning("[DiskCache] Failed to reconstruct %s: %s", type_marker, exc)
                return None
        logger.debug("[DiskCache] Unknown type marker: %s — returning raw dict", type_marker)
        return {k: _deserialize_from_disk(v) for k, v in data.items() if not k.startswith("__")}

    # DataFrame reconstruction
    if data.get("__pd__") and type_marker == "DataFrame":
        records = _deserialize_from_disk(data.get("data", []))
        df = pd.DataFrame(records)
        columns = data.get("columns", [])
        if columns and not df.empty and len(columns) == len(df.columns):
            df.columns = columns
        # Restore datetime columns (serialised as ISO strings).
        dtypes = data.get("dtypes", {})
        for col, dtype in dtypes.items():
            if col in df.columns and "datetime" in str(dtype):
                try:
                    df[col] = pd.to_datetime(df[col], errors="coerce")
                except Exception:
                    pass
        return df

    # Recurse into nested dicts/lists
    return {k: _deserialize_from_disk(v) for k, v in data.items()}


# ═══════════════════════════════════════════════════════════════════
# In-memory cache
# ═══════════════════════════════════════════════════════════════════


class MemoryCache:
    """Thread-safe in-memory cache with per-key TTL expiry.

    Usage::

        cache = MemoryCache()
        cache.set("market_summary", summary, ttl=30)
        data = cache.get("market_summary")  # None if expired
    """

    def __init__(self) -> None:
        self._store: dict[str, _CacheEntry] = {}
        self._lock = threading.RLock()

    def get(self, key: str) -> Any:
        with self._lock:
            entry = self._store.get(key)
            if entry is None:
                return None
            if entry.is_expired:
                del self._store[key]
                return None
            return entry.data

    def set(self, key: str, value: Any, ttl: int = 300) -> None:
        with self._lock:
            self._store[key] = _CacheEntry(
                data=value,
                timestamp=time.time(),
                ttl=ttl,
            )

    def delete(self, key: str) -> None:
        with self._lock:
            self._store.pop(key, None)

    def clear(self) -> None:
        with self._lock:
            self._store.clear()

    def delete_prefix(self, prefix: str) -> int:
        """Delete every key beginning with *prefix*; return count removed.

        Sprint 13.5 cache safety: targeted invalidation (e.g. only the
        ``reconciled:*`` namespace) must not evict the rest of the
        cache — a material-conflict outcome must be purged without
        dropping plain history, quotes or summaries.
        """
        with self._lock:
            keys = [k for k in self._store if k.startswith(prefix)]
            for k in keys:
                del self._store[k]
            return len(keys)

    def has(self, key: str) -> bool:
        return self.get(key) is not None

    @property
    def size(self) -> int:
        with self._lock:
            return len(self._store)


# ═══════════════════════════════════════════════════════════════════
# Disk-backed cache
# ═══════════════════════════════════════════════════════════════════


# User-state root. Defaults to ``~/.nepse``; overridable via NEPSE_HOME
# so tests can isolate from real user data.
NEPSE_HOME = Path(os.environ.get("NEPSE_HOME", str(Path.home() / ".nepse")))

DISK_CACHE_DIR = NEPSE_HOME / "cache"


class DiskCache:
    """JSON-file-backed cache that survives application restarts.

    Each key is stored as a separate ``.json`` file under
    ``~/.nepse/cache/``.  Expired entries are lazily cleaned on read.

    Dataclass objects, ``pd.DataFrame``, ``pd.Timestamp``, ``datetime``,
    and ``date`` values are serialised/deserialised automatically so
    that round-trips preserve the original Python type.
    """

    def __init__(self, cache_dir: str | Path | None = None) -> None:
        self._cache_dir = Path(cache_dir) if cache_dir else DISK_CACHE_DIR
        self._cache_dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        logger.info("DiskCache root: %s", self._cache_dir)

    def _path(self, key: str) -> Path:
        safe = "".join(c if c.isalnum() or c in ("-", "_") else "_" for c in key)
        return self._cache_dir / f"{safe}.json"

    def get(self, key: str) -> Any:
        path = self._path(key)
        if not path.exists():
            return None
        try:
            with open(path, encoding="utf-8") as f:
                entry = json.load(f)
            ttl = entry.get("ttl", 300)
            ts = entry.get("timestamp", 0)
            if (time.time() - ts) > ttl:
                path.unlink(missing_ok=True)
                return None
            raw = entry.get("data")
            return _deserialize_from_disk(raw)
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("DiskCache read error for %s: %s", key, exc)
            return None

    def set(self, key: str, value: Any, ttl: int = 300) -> None:
        path = self._path(key)
        try:
            serialized = _serialize_for_disk(value)
            # Atomic write (Sprint 11.1): temp file in the same directory
            # + os.replace so a crash mid-write can never corrupt a cache
            # entry that other processes/reads depend on.
            save_json(
                path,
                {"data": serialized, "timestamp": time.time(), "ttl": ttl},
                indent=None,
                log_name="DiskCache",
            )
        except (OSError, TypeError, ValueError) as exc:
            # A cache-write failure must never corrupt the caller's
            # response — log and skip caching instead.
            logger.warning("DiskCache write error for %s: %s", key, exc)

    def delete(self, key: str) -> None:
        path = self._path(key)
        try:
            path.unlink(missing_ok=True)
        except OSError as exc:
            logger.warning("DiskCache delete error for %s: %s", key, exc)

    def clear(self) -> None:
        with self._lock:
            for f in self._cache_dir.glob("*.json"):
                try:
                    f.unlink()
                except OSError as exc:
                    logger.warning("DiskCache clear error: %s", exc)

    def delete_prefix(self, prefix: str) -> int:
        """Delete every key beginning with *prefix*; return count removed.

        Disk keys are sanitised file names (non-alphanumerics become
        ``_``), so the prefix is sanitised the same way and matched
        against file stems.  Sprint 13.5 targeted invalidation.
        """
        safe = "".join(c if c.isalnum() or c in ("-", "_") else "_" for c in prefix)
        count = 0
        with self._lock:
            for f in self._cache_dir.glob("*.json"):
                stem = f.stem
                if not stem.startswith(safe):
                    continue
                # Boundary guard (Sprint 13.5): when the sanitised prefix
                # ends in an alphanumeric, the next character must be a
                # separator or end-of-name — so clearing
                # ``reconciled:NABIL:365`` (sanitised ``reconciled_NABIL_365``)
                # never also deletes ``reconciled:NABIL:3650``
                # (``reconciled_NABIL_3650``).  A trailing ``_`` separator
                # needs no guard: everything after it legitimately belongs
                # to the prefix namespace.
                if safe and safe[-1].isalnum():
                    rest = stem[len(safe):]
                    if rest and rest[0].isalnum():
                        continue
                try:
                    f.unlink()
                    count += 1
                except OSError as exc:
                    logger.warning("DiskCache delete error for %s: %s", f.name, exc)
        return count

    def has(self, key: str) -> bool:
        return self.get(key) is not None


# ═══════════════════════════════════════════════════════════════════
# Composite cache (tiered)
# ═══════════════════════════════════════════════════════════════════


class TieredCache:
    """Two-level cache: fast memory backed by persistent disk.

    Writes go to both levels.  Reads check memory first, then disk,
    promoting disk hits back into memory.
    """

    def __init__(
        self,
        memory_ttl: int = 60,
        disk_ttl: int = 600,
        disk_dir: str | Path | None = None,
    ) -> None:
        self._memory = MemoryCache()
        self._disk = DiskCache(cache_dir=disk_dir)
        self._memory_ttl = memory_ttl
        self._disk_ttl = disk_ttl

    def get(self, key: str) -> Any:
        val = self._memory.get(key)
        if val is not None:
            return val
        val = self._disk.get(key)
        if val is not None:
            self._memory.set(key, val, ttl=self._memory_ttl)
        return val

    def set(self, key: str, value: Any, ttl: int | None = None) -> None:
        mem_ttl = ttl or self._memory_ttl
        disk_ttl_val = ttl or self._disk_ttl
        self._memory.set(key, value, ttl=mem_ttl)
        self._disk.set(key, value, ttl=disk_ttl_val)

    def delete(self, key: str) -> None:
        self._memory.delete(key)
        self._disk.delete(key)

    def clear(self) -> None:
        self._memory.clear()
        self._disk.clear()

    def delete_prefix(self, prefix: str) -> int:
        """Delete every key beginning with *prefix* in both tiers.

        Returns the total number of entries removed (memory + disk).
        Sprint 13.5 cache safety: targeted invalidation of e.g. the
        ``reconciled:*`` namespace without clearing the whole cache.
        """
        return self._memory.delete_prefix(prefix) + self._disk.delete_prefix(prefix)

    def has(self, key: str) -> bool:
        return self._memory.has(key) or self._disk.has(key)


# ═══════════════════════════════════════════════════════════════════
# Register known dataclass types for DiskCache round-trip.
# ═══════════════════════════════════════════════════════════════════


def _register_known_types() -> None:
    """Register DataService dataclass types for DiskCache round-trip."""
    from src.data.models import (  # noqa: PLC0415
        MarketSummary,
        MarketStatus,
        StockQuote,
        StockHistory,
        TopMover,
        TopMovers,
        MarketScanResult,
        WatchlistEntry,
    )
    for _cls in (
        MarketSummary,
        MarketStatus,
        StockQuote,
        StockHistory,
        TopMover,
        TopMovers,
        MarketScanResult,
        WatchlistEntry,
    ):
        _register_type(_cls)


_register_known_types()
