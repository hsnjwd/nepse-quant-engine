"""Profile the analysis pipeline with cProfile and print ranked bottlenecks.

Usage::

    python -m benchmarks.profile_pipeline [--rows 500] [--top 25]

Profiles:

1. ``analyze_dataframe`` on a synthetic OHLCV frame (the full indicator
   → score → signal → decision chain used by the scanner, API and UI).
2. A 10-stock cold scan (load + analyze + alert processing).

The output is a ranked list of functions by cumulative time — the
evidence base for any Sprint 11.1 optimization.
"""

from __future__ import annotations

import argparse
import cProfile
import io
import pstats
import sys
import tempfile
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from benchmarks.common import make_synthetic_df, write_csvs  # noqa: E402


def profile_analysis(rows: int, top: int) -> str:
    """Profile a single analyze_dataframe call."""
    from src.engine.analyzer import analyze_dataframe  # noqa: PLC0415

    df = make_synthetic_df(rows=rows)
    profiler = cProfile.Profile()
    profiler.enable()
    for _ in range(20):
        analyze_dataframe(df.copy())
    profiler.disable()

    stream = io.StringIO()
    stats = pstats.Stats(profiler, stream=stream)
    stats.sort_stats("cumulative").print_stats(top)
    return stream.getvalue()


def profile_scan(symbols: int, rows: int, top: int) -> str:
    """Profile a cold scan over *symbols* synthetic stocks."""
    from src.cache.scanner_cache import scanner_cache  # noqa: PLC0415
    from src.scanner import engine as scanner_engine  # noqa: PLC0415

    with tempfile.TemporaryDirectory(prefix="nepse_prof_") as tmp:
        data_dir = Path(tmp) / "data"
        write_csvs(data_dir, symbols, rows)
        old_dir = scanner_engine.DATA_DIRECTORY
        scanner_engine.DATA_DIRECTORY = str(data_dir)
        try:
            scanner_cache.clear()
            profiler = cProfile.Profile()
            profiler.enable()
            scanner_engine.scan_market()
            profiler.disable()
        finally:
            scanner_engine.DATA_DIRECTORY = old_dir

    stream = io.StringIO()
    stats = pstats.Stats(profiler, stream=stream)
    stats.sort_stats("cumulative").print_stats(top)
    return stream.getvalue()


def main() -> int:
    parser = argparse.ArgumentParser(description="Profile the NEPSE analysis pipeline")
    parser.add_argument("--rows", type=int, default=500)
    parser.add_argument("--top", type=int, default=25)
    args = parser.parse_args()

    # Windows consoles default to cp1252 which cannot encode unicode
    # arrows — write UTF-8 (or replace) so the report prints cleanly.
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass

    print("=" * 78)
    print("PROFILE 1 - analyze_dataframe (indicator -> score -> signal -> decision)")
    print("=" * 78)
    print(profile_analysis(args.rows, args.top))

    print("=" * 78)
    print("PROFILE 2 - 10-stock cold scan (load + analyze + alerts)")
    print("=" * 78)
    print(profile_scan(10, args.rows, args.top))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
