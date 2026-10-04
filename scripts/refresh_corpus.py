"""Refresh the ``data/raw`` OHLCV corpus from the live yonepse daily shards.

The engine's CSV corpus is a frozen snapshot: nothing in the repo fetches
new bars, so prices go stale (observed: July 14 close of 530.1 for NABIL
while the live source quotes 551.0 on Aug 12).  This script closes that
gap by fetching the freshest ``ltp/daily/YYYY-MM-DD.json`` shard from the
live yonepse host and appending one daily OHLCV bar per symbol to the
matching ``data/raw/*.csv`` file.

Shard format (verified against the real 2026-08-12 payload)::

    {
      "date": "2026-08-12",
      "times": ["12:41", ..., "14:35"],        # intraday session times
      "columns": ["timeIndex", "ltp", "volume", "turnover", "trades"],
      "series": {
        "NABIL": [[0, 552.0, 100, 1000.0, 5],  # [timeIndex, ltp, volume, ...]
                  [1, 550.0, 200, 2100.0, 9],
                  [2, 551.0, 300, 3100.0, 12]],
        ...
      }
    }

The series rows are an *intraday session snapshot*; the daily bar is
derived as Open = first ltp, High = max ltp, Low = min ltp, Close = last
ltp, Volume = last cumulative volume.  ``turnover``/``trades`` are not
stored (the corpus schema has no columns for them).

Safety properties:

- **Dedup by date** — a shard date already present in a file is skipped;
  re-running on the same day is a no-op (idempotent).
- **Atomic writes** — the merged file is written to a ``*.tmp`` sibling
  and ``os.replace``d into place, so a crash mid-write can never truncate
  a corpus file.
- **Dry-run mode** — ``--dry-run`` reports exactly what would change and
  writes nothing.
- **Unchanged rows are preserved byte-for-byte** — existing lines are
  kept as-is (float formatting untouched); only the new bar's row is
  generated.

Usage::

    python scripts/refresh_corpus.py --dry-run          # what would change
    python scripts/refresh_corpus.py --date 2026-08-12  # explicit shard
    python scripts/refresh_corpus.py --days 14          # freshest within 14d
    python scripts/refresh_corpus.py --schedule         # daily scheduler loop
    python scripts/refresh_corpus.py --backfill --days 90 --dry-run
                                                        # recover the retained window

A one-time ``--backfill`` sweep fetches every daily shard in the retained
window (default 90 days) and merges all recovered bars into the corpus files
— one read + one atomic write per file — closing gaps like the May-July hole
while the daily scheduler keeps things current going forward.  Backfill is
idempotent: dates already present are skipped.
"""

from __future__ import annotations

import argparse
import datetime
import json
import os
import sys
import threading
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

# Default data directory (matches ``src.config.DATA_DIRECTORY``).  Kept as a
# literal so the CLI works even when ``src`` is not importable.
DEFAULT_DATA_DIR = "data/raw"

# Fallback header used when an existing file is empty (no header line).
DEFAULT_HEADER = "Date,Open,High,Low,Close,Volume"

# Known-live yonepse host.  Used as the final fallback when the engine
# config cannot be imported (e.g. ``python scripts/refresh_corpus.py`` from
# a bare shell where the repo root is not on sys.path).  Mirrors the
# configured ``github_datasets`` / ``nepse_client`` values.
DEFAULT_BASE_URL = "https://shubhamnpk.github.io/yonepse/data"

# How many consecutive network/HTTP failures abort the freshest-shard scan
# early (a dead host otherwise burns ``days`` full timeouts).
MAX_CONSECUTIVE_ERRORS = 2

# Session-fetch timeout per shard probe (seconds).
FETCH_TIMEOUT = 20.0

# Max length of one sleep slice in run_scheduler.  Kept small so the stop
# event is honoured promptly (a 60 s floor on the delay must not translate
# into a 60 s stop latency).
SLEEP_SLICE_SECONDS = 5.0

# Sentinel files excluded from the corpus scan (matches the scanner).
EXCLUDED_FILES = {"sample.csv"}

# Test seam: monkeypatched by tests to avoid any real network access.
_urlopen = urllib.request.urlopen


def _default_base_url() -> str | None:
    """Return the live yonepse base URL, preferring the engine config.

    Prefers ``github_datasets`` then ``nepse_client`` — the two keys whose
    configured default is the live yonepse host (the scraper is dead, all
    404, as verified by the provider probe).  Falls back to
    :data:`DEFAULT_BASE_URL` when ``src.config`` is not importable or the
    keys are unset.
    """
    try:
        from src.config import DATA_SERVICE_API_URLS  # noqa: PLC0415
    except Exception:  # noqa: BLE001 - config must never break the CLI
        return DEFAULT_BASE_URL
    for key in ("github_datasets", "nepse_client"):
        url = (DATA_SERVICE_API_URLS or {}).get(key)
        if url:
            return url
    return DEFAULT_BASE_URL


def fetch_shard(base_url: str, date_str: str) -> tuple[dict | None, str]:
    """Fetch ``ltp/daily/{date_str}.json`` from *base_url*.

    Returns ``(payload, kind)`` where *kind* is one of:

    - ``"ok"``        — payload is a dict containing a ``series`` key
    - ``"not_found"`` — the shard does not exist (HTTP 404)
    - ``"http"``      — any other HTTP/network failure
    - ``"shape"``     — 200 but the payload is not a series-bearing dict

    Never raises.  *base_url* is joined to ``ltp/daily/{date}.json``,
    mirroring ``src.data.providers.APIProvider._do_history``.
    """
    url = f"{base_url.rstrip('/')}/ltp/daily/{date_str}.json"
    try:
        with _urlopen(url, timeout=FETCH_TIMEOUT) as resp:  # noqa: S310 - public data source
            if getattr(resp, "status", 200) != 200:
                return None, "http"
            payload = json.load(resp)
    except urllib.error.HTTPError as exc:
        return None, "not_found" if exc.code == 404 else "http"
    except Exception:  # noqa: BLE001 - URLError/timeouts/socket errors
        return None, "http"
    if not isinstance(payload, dict) or "series" not in payload:
        return None, "shape"
    return payload, "ok"


def parse_shard_bars(shard: dict) -> dict[str, dict]:
    """Derive one daily OHLCV bar per symbol from a shard's intraday series.

    Returns ``{SYMBOL: {"Date", "Open", "High", "Low", "Close", "Volume"}}``.
    Symbols whose series is empty or contains no well-formed rows are
    skipped (a symbol that did not trade that day is not added).  The date
    is taken from the shard's ``date`` field.
    """
    series = shard.get("series", {})
    date = shard.get("date")
    bars: dict[str, dict] = {}
    if not isinstance(series, dict) or not date:
        return bars
    for symbol, rows in series.items():
        if not isinstance(rows, list):
            continue
        ltps: list[float] = []
        vols: list[float] = []
        for row in rows:
            if not isinstance(row, (list, tuple)) or len(row) < 3:
                continue
            try:
                ltps.append(float(row[1]))
                vols.append(float(row[2]))
            except (TypeError, ValueError):
                # Corrupt cell — skip the row (and thus the symbol), never
                # abort the whole refresh over one bad value.
                continue
        if not ltps:
            continue
        bars[str(symbol).upper()] = {
            "Date": str(date),
            "Open": ltps[0],
            "High": max(ltps),
            "Low": min(ltps),
            "Close": ltps[-1],
            "Volume": int(vols[-1]) if vols else 0,
        }
    return bars


def find_freshest_shard(
    base_url: str,
    days: int = 14,
    today: datetime.date | None = None,
) -> tuple[str | None, dict | None, str | None]:
    """Scan back from *today* for the freshest available daily shard.

    Probes ``today``, ``today - 1``, ... up to *days* days.  404s are
    treated as non-trading days and skipped; after
    :data:`MAX_CONSECUTIVE_ERRORS` consecutive network/HTTP failures the
    scan aborts early (dead host).

    Returns ``(date_str, shard, note)`` — ``(None, None, note)`` on
    failure with a human-readable *note*.
    """
    today = today or datetime.date.today()
    consecutive_errors = 0
    for i in range(days):
        date_str = (today - datetime.timedelta(days=i)).isoformat()
        shard, kind = fetch_shard(base_url, date_str)
        if kind == "ok":
            return date_str, shard, None
        if kind == "not_found":
            # A 404 proves the host answered — reset the fail-fast counter.
            consecutive_errors = 0
            continue
        # "shape" (200 but wrong payload) and "http" both mean the source
        # is not serving usable data right now — count both toward the
        # fail-fast abort.
        consecutive_errors += 1
        if consecutive_errors >= MAX_CONSECUTIVE_ERRORS:
            return None, None, (
                f"source unreachable: {consecutive_errors} consecutive "
                f"failures, last probe {date_str}"
            )
    return None, None, f"no daily shard found within the last {days} days"


def _fmt(value: float | int) -> str:
    """Format a numeric OHLCV value like the corpus (ints without a '.0')."""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def _format_bar_row(bar: dict) -> str:
    return ",".join(
        [
            str(bar["Date"]),
            _fmt(bar["Open"]),
            _fmt(bar["High"]),
            _fmt(bar["Low"]),
            _fmt(bar["Close"]),
            _fmt(bar["Volume"]),
        ]
    )


# Bounded retry for the atomic-write replace.  On Windows, antivirus
# real-time scanning can briefly hold a handle on the freshly written
# temp file, making ``os.replace`` fail intermittently with
# ``PermissionError`` — retried a few times before giving up.
_ATOMIC_WRITE_ATTEMPTS = 4
_ATOMIC_WRITE_RETRY_DELAY = 0.05


def _atomic_write(path: Path, text: str) -> None:
    """Write *text* to *path* atomically (temp sibling + ``os.replace``).

    ``os.replace`` is retried on ``PermissionError`` (Windows antivirus
    lock flake) with a small linear backoff; the final attempt lets any
    error propagate to the caller.
    """
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8", newline="")
    for attempt in range(_ATOMIC_WRITE_ATTEMPTS - 1):
        try:
            os.replace(tmp, path)
            return
        except PermissionError:
            time.sleep(_ATOMIC_WRITE_RETRY_DELAY * (attempt + 1))
    # Final attempt — any error propagates to the caller.
    os.replace(tmp, path)


def merge_bars(
    path: Path,
    bars: list[dict],
    dry_run: bool = False,
) -> tuple[int, int]:
    """Merge *bars* (daily rows) into one OHLCV CSV, deduplicated by date.

    The file is read once, every missing date added in a single pass, and
    written once — the backfill path, where calling :func:`append_bar` per
    day would mean ~90 read-modify-write cycles per symbol.

    Args:
        path: Target corpus CSV (must exist; missing files are skipped so
            the shard can never create new corpus symbols behind the
            engine's back).
        bars: Rows from :func:`parse_shard_bars` in any order; deduped by
            date, output written date-sorted.
        dry_run: When ``True``, report only — nothing is written.

    Returns:
        ``(appended, duplicated)`` — how many rows would be/were added and
        how many were skipped because the date already existed.

    Existing rows are preserved byte-for-byte; the file's line terminator
    (CRLF vs LF) is detected and preserved; the merged file is written
    atomically.
    """
    path = Path(path)
    if not path.exists():
        return 0, 0
    # Read as raw bytes then decode: ``read_text`` applies universal-newline
    # translation (CRLF -> LF) on read, which would defeat the CRLF
    # detection below.  ``utf-8-sig`` also strips a BOM if present.
    text = path.read_bytes().decode("utf-8-sig", errors="replace")
    terminator = "\r\n" if "\r\n" in text else "\n"
    lines = text.splitlines()
    if not lines:
        header = DEFAULT_HEADER
        rows_by_date: dict[str, str] = {}
    else:
        header = lines[0].strip().lstrip("\ufeff")
        if "close" not in header.lower():
            print(
                f"WARN: {path.name}: unrecognized header {header!r} - skipped",
                file=sys.stderr,
            )
            return 0, 0
        rows_by_date = {}
        for line in lines[1:]:
            line = line.strip()
            if not line:
                continue
            fields = line.split(",")
            date_field = fields[0].strip() if fields else ""
            if not date_field:
                continue
            rows_by_date[date_field] = line

    appended = duplicated = 0
    for bar in bars:
        date_str = str(bar["Date"])
        if date_str in rows_by_date:
            duplicated += 1
            continue
        rows_by_date[date_str] = _format_bar_row(bar)
        appended += 1

    if dry_run or appended == 0:
        return appended, duplicated

    body = terminator.join(rows_by_date[d] for d in sorted(rows_by_date))
    _atomic_write(path, header + terminator + body + terminator)
    return appended, duplicated


def append_bar(path: Path, bar: dict, dry_run: bool = False) -> tuple[int, int]:
    """Append a single *bar* (one daily row) to an OHLCV CSV.

    Thin wrapper over :func:`merge_bars` kept for the single-day refresh
    path and its existing tests.
    """
    return merge_bars(path, [bar], dry_run=dry_run)


def next_run_delay(
    at_time: str,
    now: datetime.datetime | None = None,
    interval_hours: float = 24.0,
) -> float:
    """Seconds until the next scheduled refresh.

    With *at_time* (``HH:MM``), the delay is until the next occurrence of
    that wall-clock time (today if still ahead, tomorrow otherwise).  With
    an empty/invalid *at_time* the delay is ``interval_hours * 3600`` — the
    pure-interval fallback.
    """
    if now is None:
        now = datetime.datetime.now()
    if not at_time:
        # Floor at 60 s: a 0/negative interval (operator misconfig) must
        # never turn the scheduler into a busy loop hammering the network.
        return max(interval_hours * 3600.0, 60.0)
    try:
        hour, minute = (int(part) for part in at_time.split(":"))
        scheduled = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    except (AttributeError, TypeError, ValueError):
        return max(interval_hours * 3600.0, 60.0)
    delay = (scheduled - now).total_seconds()
    if delay <= 0:
        delay += 86400.0  # already past today — next occurrence is tomorrow
    return delay


def run_scheduler(
    on_run: Callable[[], None],
    at_time: str = "16:00",
    interval_hours: float = 24.0,
    stop_event: threading.Event | None = None,
    sleep_fn: Callable[[float], None] = time.sleep,
    now_fn: Callable[[], datetime.datetime] | None = None,
) -> int:
    """Run *on_run* immediately, then on a schedule until stopped.

    Args:
        on_run: Callable performing one refresh pass (e.g. a wrapper around
            :func:`refresh_once`).
        at_time: Daily wall-clock time ``HH:MM``; empty/invalid falls back
            to the *interval_hours* cadence.
        interval_hours: Fallback interval (hours) between runs.
        stop_event: When set, the loop ends after the current sleep slice;
            created internally when ``None``.
        sleep_fn: Injectable sleep for tests (defaults to ``time.sleep``).
        now_fn: Injectable clock for tests (defaults to
            ``datetime.datetime.now``).

    Returns:
        ``0`` when stopped.

    Robustness: a failed cycle (exception) is logged and does NOT kill the
    loop — the next scheduled run still happens.  Sleeps happen in <= 5 s
    slices (:data:`SLEEP_SLICE_SECONDS`) so the stop event is honoured
    promptly, and a CLI ``Ctrl+C`` exits cleanly.
    """
    if stop_event is None:
        stop_event = threading.Event()
    if now_fn is None:
        now_fn = datetime.datetime.now
    try:
        while not stop_event.is_set():
            try:
                on_run()
            except Exception as exc:  # noqa: BLE001 - a bad cycle must not kill the scheduler
                print(
                    f"WARN: corpus refresh cycle failed: {exc}",
                    file=sys.stderr,
                )
            if stop_event.is_set():
                break
            delay = next_run_delay(at_time, now_fn(), interval_hours)
            target = now_fn() + datetime.timedelta(seconds=delay)
            while not stop_event.is_set():
                remaining = (target - now_fn()).total_seconds()
                if remaining <= 0:
                    break
                sleep_fn(min(remaining, SLEEP_SLICE_SECONDS))
    except KeyboardInterrupt:
        pass  # CLI Ctrl+C — clean exit
    return 0


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Fetch the freshest yonepse daily shard and append new OHLCV "
            "bars to data/raw/*.csv (dedup by date, atomic writes)."
        )
    )
    parser.add_argument(
        "--date",
        help="explicit shard date YYYY-MM-DD (default: freshest within --days)",
    )
    parser.add_argument(
        "--days",
        type=int,
        default=None,
        help="how far back to scan: 14 for refresh, 90 for backfill "
        "(defaults: 14 / 90)",
    )
    parser.add_argument(
        "--data-dir",
        default=DEFAULT_DATA_DIR,
        help=f"corpus directory (default: {DEFAULT_DATA_DIR})",
    )
    parser.add_argument(
        "--base-url",
        default=None,
        help="yonepse base URL override (default: engine config github_datasets)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="report what would change without writing any file",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="print one line per updated file",
    )
    parser.add_argument(
        "--schedule",
        action="store_true",
        help="run on a daily schedule instead of once (scheduler loop)",
    )
    parser.add_argument(
        "--backfill",
        action="store_true",
        help="sweep daily shards for the last --days days and merge every "
        "recovered bar into the corpus (one write per file)",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=4,
        help="parallel shard fetches during backfill (default: 4; 1 = "
        "sequential)",
    )
    parser.add_argument(
        "--at-time",
        default="16:00",
        help="daily refresh time HH:MM (local); empty disables and uses "
        "--interval-hours (default: 16:00)",
    )
    parser.add_argument(
        "--interval-hours",
        type=float,
        default=24.0,
        help="fallback cadence in hours when --at-time is unset/invalid "
        "(default: 24)",
    )
    return parser.parse_args(argv)


def refresh_once(args: argparse.Namespace) -> int:
    """Run one refresh pass from parsed *args*; returns an exit code.

    Extracted from :func:`main` so the scheduler loop and the launcher
    thread can reuse the exact same pass.
    """
    data_dir = Path(args.data_dir)
    if not data_dir.is_dir():
        print(f"ERROR: data directory not found: {data_dir}", file=sys.stderr)
        return 1

    base_url = args.base_url or _default_base_url()
    if not base_url:
        print(
            "ERROR: no base URL (pass --base-url or set github_datasets "
            "in src/config)",
            file=sys.stderr,
        )
        return 1

    if args.date:
        shard, kind = fetch_shard(base_url, args.date)
        if kind != "ok":
            print(f"ERROR: shard {args.date} unavailable ({kind})", file=sys.stderr)
            return 1
        shard_date, note = args.date, "explicit"
    else:
        days = args.days if args.days is not None else 14
        shard_date, shard, note = find_freshest_shard(base_url, days=days)
        if shard is None:
            print(f"ERROR: {note}", file=sys.stderr)
            return 1
        note = f"freshest within the last {days} days"

    bars = parse_shard_bars(shard)
    if not bars:
        print(
            f"ERROR: shard {shard_date} contains no parseable series",
            file=sys.stderr,
        )
        return 1

    files = sorted(
        p for p in data_dir.glob("*.csv") if p.name not in EXCLUDED_FILES
    )
    corpus_symbols = {p.stem.upper() for p in files}

    appended = duplicated = updated = 0
    for path in files:
        bar = bars.get(path.stem.upper())
        if bar is None:
            continue  # symbol has no data in this shard — leave unchanged
        added, dup = append_bar(path, bar, dry_run=args.dry_run)
        appended += added
        duplicated += dup
        if added:
            updated += 1
            if args.verbose:
                print(f"  updated {path.name}: +1 row ({bar['Date']})")

    missing = sorted(set(bars) - corpus_symbols)
    print(f"Shard date:         {shard_date}  ({note})")
    print(f"Symbols in shard:   {len(bars)}")
    print(f"Corpus files:       {len(files)}")
    print(f"Rows appended:      {appended}")
    print(f"Rows already present (skipped): {duplicated}")
    print(f"Files updated:      {updated}")
    print(f"Shard symbols with no corpus file: {len(missing)}")
    if args.dry_run:
        print("DRY RUN - no files were written.")
    return 0


def run_backfill_once(args: argparse.Namespace) -> int:
    """Backfill: fetch daily shards for the last N days and merge every
    recovered bar into the corpus files (one write per file).

    The freshest-shard refresh only appends the *current* day; a one-time
    backfill sweeps the window the source retains (default 90 days) so
    gaps like the May-July hole in ``data/raw`` are closed before the
    daily scheduler takes over.  Idempotent: dates already present are
    skipped, and the whole pass is ``--dry-run``-able.

    Shards are fetched in parallel (``--workers``, bounded thread pool); a
    failed or 404 day is counted and skipped, never fatal.  Per-symbol
    bars are accumulated in memory and merged into each file exactly once.
    """
    data_dir = Path(args.data_dir)
    if not data_dir.is_dir():
        print(f"ERROR: data directory not found: {data_dir}", file=sys.stderr)
        return 1

    base_url = args.base_url or _default_base_url()
    if not base_url:
        print(
            "ERROR: no base URL (pass --base-url or set github_datasets "
            "in src/config)",
            file=sys.stderr,
        )
        return 1

    days = max(args.days if args.days is not None else 90, 1)
    today = datetime.date.today()
    dates = [(today - datetime.timedelta(days=i)).isoformat() for i in range(days)]

    def _safe_fetch(date_str: str):
        try:
            return date_str, fetch_shard(base_url, date_str)
        except Exception as exc:  # noqa: BLE001 - isolate per shard
            return date_str, (None, "error")

    if args.verbose:
        print(f"Fetching {len(dates)} daily shards (workers={args.workers})...")
    if args.workers > 1:
        with ThreadPoolExecutor(max_workers=args.workers) as pool:
            results = list(pool.map(_safe_fetch, dates))
    else:
        results = [_safe_fetch(d) for d in dates]
    if args.verbose:
        for date_str, (_shard, kind) in results:
            print(f"  {date_str}: {kind}")

    all_bars: dict[str, dict[str, dict]] = {}
    found = failed = 0
    for date_str, (shard, kind) in results:
        if kind == "ok":
            try:
                bars = parse_shard_bars(shard)
            except Exception as exc:  # noqa: BLE001 - isolate per shard
                # A pathological shard must cost one day, not the whole run
                # (same isolation as _safe_fetch on the fetch side).
                print(
                    f"WARN: shard {date_str} unparseable ({exc}) - skipped",
                    file=sys.stderr,
                )
                failed += 1
                continue
            found += 1
            for symbol, bar in bars.items():
                all_bars.setdefault(symbol, {})[date_str] = bar
        elif kind != "not_found":
            failed += 1

    if not all_bars:
        print(
            "ERROR: no bars recovered from any shard in the window",
            file=sys.stderr,
        )
        return 1

    files = sorted(
        p for p in data_dir.glob("*.csv") if p.name not in EXCLUDED_FILES
    )
    corpus_symbols = {p.stem.upper() for p in files}

    appended = duplicated = updated = 0
    for path in files:
        bars_by_date = all_bars.get(path.stem.upper())
        if not bars_by_date:
            continue  # no recovered rows for this symbol
        bar_list = [bars_by_date[d] for d in sorted(bars_by_date)]
        added, dup = merge_bars(path, bar_list, dry_run=args.dry_run)
        appended += added
        duplicated += dup
        if added:
            updated += 1
            if args.verbose:
                print(f"  updated {path.name}: +{added} rows")

    missing = sorted(set(all_bars) - corpus_symbols)
    print(f"Backfill window:     {dates[-1]} .. {dates[0]}  ({len(dates)} days)")
    print(f"Shards recovered:    {found}")
    print(f"Shards 404/empty:    {len(dates) - found - failed}")
    print(f"Shards failed:       {failed}")
    print(f"Symbols with data:   {len(all_bars)}")
    print(f"Corpus files:        {len(files)}")
    print(f"Rows appended:       {appended}")
    print(f"Rows already present (skipped): {duplicated}")
    print(f"Files updated:       {updated}")
    print(f"Shard symbols with no corpus file: {len(missing)}")
    if args.dry_run:
        print("DRY RUN - no files were written.")
    return 0


def main(argv: list[str] | None = None) -> int:
    """CLI entry point; returns a process exit code (testable).

    Modes: plain (one refresh pass), ``--schedule`` (daily scheduler
    loop), ``--backfill`` (one-time sweep of the retained shard window).
    In ``--backfill`` mode ``--days`` defaults to 90 (the source's
    retention window) instead of 14.
    """
    args = _parse_args(argv)
    if args.schedule:
        return run_scheduler(
            on_run=lambda: refresh_once(args),
            at_time=args.at_time,
            interval_hours=args.interval_hours,
        )
    if args.backfill:
        return run_backfill_once(args)
    return refresh_once(args)


if __name__ == "__main__":
    sys.exit(main())
