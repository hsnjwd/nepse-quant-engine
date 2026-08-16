"""Shared, corruption-tolerant, atomic JSON persistence helpers.

Every persistent mutable JSON store in the platform (alert history,
portfolio holdings, watchlist, ...) should route its reads and writes
through :func:`load_json` / :func:`save_json` so that all stores share
the same guarantees:

* UTF-8 encoding on both read and write.
* Atomic writes — data is written to a temp file in the same directory
  and moved into place with ``os.replace``, so a crash mid-write can
  never truncate the store.
* Parent-directory creation on write.
* Safe handling of missing files (return the caller's default).
* Safe handling of empty files (backed up aside, default returned).
* Safe handling of malformed/truncated JSON (backed up aside so the
  evidence is preserved, default returned, error logged).
* Deterministic serialization where practical (``sort_keys``).

Recovery policy: a damaged file is *never* silently overwritten in
place.  It is renamed aside (``<name>.corrupt.bak``) first — preserving
the evidence for inspection — and the caller receives its default so
the application can continue operating.
"""

from __future__ import annotations

import contextlib
import errno
import json
import logging
import os
import tempfile
import threading
import time
from pathlib import Path
from typing import Any, Callable

logger = logging.getLogger(__name__)

DEFAULT_BACKUP_SUFFIX = ".corrupt.bak"

# ── Transactional lock machinery (Sprint 11.7) ────────────────────
#
# Mutable JSON stores are read-modify-write: load -> mutate -> save.
# Under ``uvicorn --workers=2`` (the Docker api stage) two *processes*
# can interleave those steps and silently lose each other's updates
# (the file stays valid JSON — a lost update, not corruption).  The
# fix is a per-file lock covering the whole transaction:
#
#   * an in-process per-path ``RLock`` serialises the scanner's
#     ThreadPoolExecutor threads cheaply (no filesystem churn), and
#
#   * a cross-process lock file created with ``os.open(O_CREAT|O_EXCL)``
#     — atomic on Windows and POSIX alike, so no ``fcntl`` and no
#     third-party dependency.  A crashed holder leaves a *stale* lock
#     file, which is broken after ``_LOCK_STALE_S`` so the application
#     can never deadlock permanently.
#
# ``save_json`` itself is unchanged (atomic temp + replace + the
# sharing-violation retry for WinError 5/32 / EACCES / EBUSY); the lock
# adds transaction serialisation, it does not replace atomicity.

_LOCK_SUFFIX = ".lock"
# Stale threshold must be BELOW the acquire timeout: a waiter that finds
# an older-than-stale lock breaks it (a crashed holder), and it must be
# able to reach that state within one acquisition cycle.  With 30 s
# stale > 10 s timeout (the Sprint 11.7 first pass) the breaker was
# unreachable and a crashed holder caused ~30 s of 500s.  8 s is still
# comfortably above the sub-second JSON transactions this serialises, so
# a live holder can never be falsely broken under normal load.
_LOCK_STALE_S = 8.0
_LOCK_ACQUIRE_TIMEOUT_S = 10.0
_LOCK_RETRY_DELAY_S = 0.02

_path_locks: dict[str, threading.RLock] = {}
_path_locks_guard = threading.Lock()

# Lock-contention observability counters (Sprint 11.8, Phase 5).
# Updated only on the *slow* path (lock acquisition contention / stale
# recovery / timeout), never on the hot read/write path, so they cost
# nothing under normal operation.  Exposed via :func:`lock_stats` and
# the ``/metrics`` endpoint so a Docker multi-worker operator can see
# whether shared JSON stores are contending, breaking stale locks, or
# timing out — without scanning the filesystem on every request.
_lock_stats_guard = threading.Lock()
_lock_stats: dict[str, int] = {
    "retries": 0,          # observed contention while acquiring a lock
    "stale_recoveries": 0, # stale lock files broken (crashed holder)
    "timeouts": 0,         # TimeoutError after the acquire deadline
}


def _bump_lock_stat(name: str) -> None:
    with _lock_stats_guard:
        _lock_stats[name] += 1


def lock_stats() -> dict[str, int]:
    """Return a copy of the cross-process lock-contention counters.

    Keys: ``retries`` (contention observed), ``stale_recoveries``
    (stale lock files broken), ``timeouts`` (acquire deadlines hit).
    Monotonic since process start; not persisted.
    """
    with _lock_stats_guard:
        return dict(_lock_stats)


def _path_lock(path: str | Path) -> threading.RLock:
    key = str(Path(path).resolve())
    with _path_locks_guard:
        lock = _path_locks.get(key)
        if lock is None:
            lock = _path_locks[key] = threading.RLock()
        return lock


def _lock_file_for(path: str | Path) -> Path:
    # Resolve so lock identity is spelling-independent: a worker holding
    # the lock via a relative path and another spelling the same file
    # absolutely must contend on the SAME lock file (both uvicorn
    # workers share a CWD today, but resolve() removes the fragility).
    p = Path(path).resolve()
    return p.parent / (p.name + _LOCK_SUFFIX)


def _acquire_file_lock(path: str | Path) -> Path:
    """Create the cross-process lock file; blocks up to the timeout.

    Uses ``os.open(..., O_CREAT | O_EXCL)`` which is atomic on Windows
    and POSIX.  On contention, waits and retries; a lock file older than
    ``_LOCK_STALE_S`` (a crashed holder) is removed and retried so a
    stale lock can never deadlock the app.  Raises ``TimeoutError`` if
    the lock cannot be acquired within ``_LOCK_ACQUIRE_TIMEOUT_S``.
    """
    lock_path = _lock_file_for(path)
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    deadline = time.monotonic() + _LOCK_ACQUIRE_TIMEOUT_S
    while True:
        try:
            fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            try:
                os.write(fd, str(os.getpid()).encode("ascii"))
            finally:
                os.close(fd)
            return lock_path
        except OSError as exc:
            # Windows quirk: when the lock file already exists and is
            # momentarily open by another process, ``O_CREAT|O_EXCL``
            # surfaces as EACCES ("Permission denied") instead of
            # EEXIST — the sharing check fires before the existence
            # check.  Treat EEXIST and EACCES alike as contention;
            # everything else is a genuine error.
            if exc.errno not in (errno.EEXIST, errno.EACCES):
                raise
            _bump_lock_stat("retries")
            try:
                # st_mtime is epoch wall-clock, so the age comparison
                # must use time.time() — time.monotonic() is relative to
                # an arbitrary origin and always yields a huge negative
                # "age", which silently disabled stale-lock recovery
                # (Sprint 11.8 test exposure; the recovery path was
                # dead code before this fix).
                age = time.time() - lock_path.stat().st_mtime
            except OSError:
                age = 0.0
            if age > _LOCK_STALE_S:
                try:
                    lock_path.unlink()
                except OSError:
                    pass
                _bump_lock_stat("stale_recoveries")
                continue
            if time.monotonic() >= deadline:
                _bump_lock_stat("timeouts")
                raise TimeoutError(f"timed out acquiring lock {lock_path}")
            time.sleep(_LOCK_RETRY_DELAY_S)


def _release_file_lock(lock_path: Path) -> None:
    try:
        lock_path.unlink()
    except OSError:
        pass


@contextlib.contextmanager
def locked_json(path: str | Path):
    """Serialise the full read -> mutate -> write transaction on *path*.

    Acquires the per-path in-process ``RLock`` then the cross-process
    lock file, and releases both on exit (even on exception).  Use this
    (or :func:`update_json`) for *mutable* shared JSON; plain reads of
    immutable/read-only files should keep using :func:`load_json`
    directly so they never pay for the lock.
    """
    lock = _path_lock(path)
    with lock:
        lock_path = _acquire_file_lock(path)
        try:
            yield
        finally:
            _release_file_lock(lock_path)


def update_json(
    path: str | Path,
    mutator: Callable[[Any], Any],
    default: Any,
    *,
    log_name: str = "json_store",
) -> Any:
    """Atomically apply *mutator* to the JSON store at *path*.

    Runs ``load -> mutator -> save`` under :func:`locked_json`, so
    concurrent processes/threads can never lose each other's updates.

    Args:
        path: JSON file to mutate.
        mutator: Callable receiving the current state (or *default*
            when missing) and returning the new state.  May also return
            a ``(new_state, result)`` tuple — then the second element
            is returned to the caller.
        default: Value passed to *mutator* when the file is missing.
        log_name: Component name used in log messages.

    Returns:
        The mutator's ``result`` when it returned a ``(new_state,
        result)`` tuple, otherwise the new state.
    """
    with locked_json(path):
        state = load_json(path, default, log_name=log_name)
        outcome = mutator(state)
        if isinstance(outcome, tuple) and len(outcome) == 2 and isinstance(
            outcome[0], (dict, list)
        ):
            new_state, result = outcome
        else:
            new_state, result = outcome, None
        save_json(path, new_state, log_name=log_name)
    return result if result is not None else new_state


# Multi-process write retries (Sprint 11.6 Phase 14).
#
# On Windows, ``os.replace`` raises ``PermissionError`` (WinError 5) when
# the destination file is momentarily open by *another process* — e.g. a
# second uvicorn worker holding the file open for a read while this
# worker atomically replaces it (the Docker api stage runs
# ``uvicorn --workers=2``).  The rename itself is atomic and never
# corrupts state; the failure is a transient sharing violation, so a
# short bounded retry makes concurrent multi-process writers reliable
# without weakening any atomicity or corruption-tolerance guarantee.
_WRITE_RETRY_ATTEMPTS = 6
_WRITE_RETRY_DELAY_S = 0.05


def _is_sharing_violation(exc: OSError) -> bool:
    """True for a transient Windows sharing violation (WinError 5 / 32).

    Only these failures are worth retrying: another process momentarily
    holds the destination open (e.g. a second uvicorn worker's reader).
    ``os.replace`` reports the classic ERROR_ACCESS_DENIED as WinError 5
    but a true sharing conflict surfaces as ERROR_SHARING_VIOLATION
    (WinError 32, errno 13) — both are transient.  Every other OSError
    (missing source, real permission problems, ...) is permanent and
    must surface immediately.  POSIX fallback: EACCES/EBUSY are retried
    (transient file-busy), everything else raises at once.
    """
    winerror = getattr(exc, "winerror", None)
    if winerror is not None:
        return winerror in (5, 32)
    return exc.errno in (errno.EACCES, errno.EBUSY)


def _replace_atomic(src: str | Path, dst: str | Path, log_name: str) -> None:
    """Replace *dst* with *src* atomically, retrying transient OSErrors.

    Keeps the temp-file +    ``os.replace`` atomic-write contract while
    absorbing transient sharing violations from concurrent
    readers/writers in other processes (e.g. the second uvicorn worker).
    Only ``_is_sharing_violation`` errors (WinError 5/32, EACCES/EBUSY)
    are retried; every other OSError is a permanent failure and raises
    immediately.  Raises the last error after exhausting the retries.
    """
    last: OSError | None = None
    for attempt in range(_WRITE_RETRY_ATTEMPTS):
        try:
            os.replace(src, dst)
            return
        except OSError as exc:
            if not _is_sharing_violation(exc):
                raise
            last = exc
            if attempt + 1 < _WRITE_RETRY_ATTEMPTS:
                logger.debug(
                    "[%s] os.replace retry %d/%d (%s)",
                    log_name, attempt + 1, _WRITE_RETRY_ATTEMPTS, exc,
                )
                time.sleep(_WRITE_RETRY_DELAY_S)
    if last is not None:
        raise last


def _backup(path: Path, backup_suffix: str, log_name: str) -> Path | None:
    """Rename *path* aside so corrupted state is preserved for inspection.

    If a backup already exists it is not overwritten — a timestamped
    variant is used instead, so successive corruptions are all kept.

    Returns:
        The backup path, or ``None`` when the rename failed.
    """
    backup = path.with_suffix(backup_suffix)
    if backup.exists():
        # Nanosecond timestamp avoids collisions when several
        # corruptions happen within the same wall-clock second.
        backup = backup.with_name(f"{backup.stem}.{time.time_ns()}{backup.suffix}")
    try:
        os.replace(path, backup)
    except OSError as exc:
        logger.error("[%s] Failed to back up corrupt file %s: %s", log_name, path, exc)
        return None
    logger.warning("[%s] Damaged file moved to %s", log_name, backup)
    return backup


def load_json(
    path: str | Path,
    default: Any,
    *,
    backup_suffix: str = DEFAULT_BACKUP_SUFFIX,
    log_name: str = "json_store",
) -> Any:
    """Load and parse *path*, returning *default* on any failure.

    Never raises for missing / empty / malformed input.  Empty and
    malformed files are renamed aside (evidence preserved) and logged
    before the default is returned.

    Args:
        path: JSON file to read.
        default: Value returned when the file is missing/empty/invalid.
        backup_suffix: Suffix used for the preserved corrupt file.
        log_name: Component name used in log messages.

    Returns:
        Parsed JSON value, or *default*.
    """
    p = Path(path)
    if not p.exists():
        logger.debug("[%s] %s not found; using default", log_name, p)
        return default

    try:
        text = p.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        # A file with invalid UTF-8 bytes is corrupt — preserve the
        # evidence and recover rather than crashing the caller.
        logger.error("[%s] %s is not valid UTF-8 (%s); backing up and using default", log_name, p, exc)
        _backup(p, backup_suffix, log_name)
        return default
    except OSError as exc:
        logger.error("[%s] Cannot read %s (%s); using default", log_name, p, exc)
        return default

    if not text.strip():
        logger.warning("[%s] %s is empty; backing up and using default", log_name, p)
        _backup(p, backup_suffix, log_name)
        return default

    try:
        return json.loads(text)
    except (json.JSONDecodeError, ValueError) as exc:
        logger.error("[%s] Malformed JSON in %s (%s); backing up and using default", log_name, p, exc)
        _backup(p, backup_suffix, log_name)
        return default


def save_json(
    path: str | Path,
    data: Any,
    *,
    indent: int = 4,
    sort_keys: bool = False,
    log_name: str = "json_store",
) -> None:
    """Atomically write *data* as UTF-8 JSON to *path*.

    The write goes to a temp file in the destination directory which is
    then moved into place with ``os.replace`` — readers can never
    observe a partially-written file.  Parent directories are created as
    needed.

    Args:
        path: Destination JSON file.
        data: JSON-serialisable value to persist.
        indent: Pretty-print indentation (``None`` for compact output).
        sort_keys: Sort object keys for deterministic output.
        log_name: Component name used in log messages.

    Raises:
        OSError: If the directory cannot be created or the write fails.
        TypeError: If *data* is not JSON-serialisable.
    """
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)

    fd, tmp_path = tempfile.mkstemp(dir=str(p.parent), prefix=f".{p.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=indent, sort_keys=sort_keys)
        _replace_atomic(tmp_path, p, log_name)
    except BaseException:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise
    logger.debug("[%s] Wrote %s atomically", log_name, p)


__all__ = [
    "load_json",
    "save_json",
    "locked_json",
    "update_json",
    "lock_stats",
    "DEFAULT_BACKUP_SUFFIX",
]
