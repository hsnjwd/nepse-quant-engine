"""Cold-path profiling for the NEPSE indicator pipeline (Sprint 12.2, Phase 1).

Profiles the *first-time* cost of the analysis pipeline — the path that
dominates a cold market scan and the per-symbol /api/analyze cost before
the scanner/indicator caches warm up:

    CSV -> DataFrame -> indicators -> analysis -> scoring -> ranking -> alerts

Two workloads:

- **single**  : one ``analyze_stock`` on a representative OHLCV file.
  Every function is individually attributable (good for identifying
  specific hot functions).
- **scan**    : a full ``scan_market(workers=1)`` over N synthetic files.
  The sequential fallback runs the *entire* pipeline (CSV load, indicator
  chain, score, signal, rank, batch alerts) in the calling thread, so
  cProfile attributes every call to the main thread — the scanner's
  thread pool (workers=4) would scatter profile records across threads
  and make per-function attribution unreliable.

Both workloads run under ``benchmarks.pipeline._state_files`` isolation
so alert history / portfolio persistence are redirected to a private
temp dir — profiling never touches ``data/alerts/history.json`` or other
production state.

Outputs (JSON + pstats dump + human-readable top-N report) land under
``benchmarks/results/profile/``.  The profiling helpers are pure enough
to be exercised by ``tests/test_sprint12_2.py`` (no wall-clock
assertions — only structure/determinism checks).

Usage::

    python -m benchmarks.profile_cold_path --sizes 50 200 500 --rows 500
"""

from __future__ import annotations

import argparse
import cProfile
import json
import pstats
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from benchmarks.common import write_csvs  # noqa: E402
from benchmarks.pipeline import _state_files  # noqa: E402

RESULTS_DIR = Path(__file__).resolve().parent / "results" / "profile"

# Default representative rows/symbol counts (500 rows matches the
# existing benchmark corpus and the ~25-26 ms/symbol scaling figures).
DEFAULT_SIZES = (50, 200, 500)
DEFAULT_ROWS = 500

# Top-N functions included in the summary JSON / text report.
TOP_N = 30


# ───────────────────────────────────────────────────────────────────
# Pure profile helpers (unit-testable)
# ───────────────────────────────────────────────────────────────────


def run_profiled(fn: Callable[[], Any], dump_path: Path | None = None) -> dict[str, Any]:
    """Run *fn* under cProfile and return a compact summary dict.

    The profile is dumped to *dump_path* (pstats format) when given.
    Returns a dict with ``total_s`` (wall time of the profiled call) and
    ``top`` — the top-TopN functions by cumulative time, each with
    ``function``, ``module``, ``line``, ``cumtime_s``, ``tottime_s``,
    ``ncalls`` (primitive calls), ``calls`` (total calls incl.
    recursion), ``cumtime_pct``, ``tottime_pct`` (percentages of the
    profiled wall time).  The module for built-in / extension code
    (``{built-in method ...}``, ``~``) is reported as-is.
    """
    start = time.perf_counter()
    profiler = cProfile.Profile()
    try:
        profiler.enable()
        try:
            fn()
        finally:
            profiler.disable()
    finally:
        total_s = time.perf_counter() - start

    stats = pstats.Stats(profiler)
    if dump_path is not None:
        dump_path.parent.mkdir(parents=True, exist_ok=True)
        stats.dump_stats(str(dump_path))

    # Top-N functions by cumulative time (the standard "where is the
    # time going" view for a cold pipeline).  Each stats entry is keyed
    # by ``(module, line, name)`` with value ``(cc, nc, tt, ct, callers)``
    # — ccalls (total incl. recursion), ncalls (primitive), tottime,
    # cumtime.
    sorted_funcs = sorted(
        stats.stats.items(),
        key=lambda kv: (kv[1][3], kv[1][2]),  # cumtime desc, tottime desc
        reverse=True,
    )
    rows: list[dict[str, Any]] = []
    for (mod, line, name), (cc, nc, tt, ct, _callers) in sorted_funcs[:TOP_N]:
        rows.append(
            {
                "function": name,
                "module": mod,
                "line": line,
                "ncalls": nc,
                "calls": cc,
                "tottime_s": round(tt, 6),
                "cumtime_s": round(ct, 6),
                "tottime_pct": round(tt / total_s * 100.0, 2) if total_s else 0.0,
                "cumtime_pct": round(ct / total_s * 100.0, 2) if total_s else 0.0,
            }
        )

    return {"total_s": round(total_s, 4), "top": rows}


def format_report(summary: dict[str, Any], title: str) -> str:
    """Render a profiling *summary* dict as a human-readable report."""
    lines = [f"== {title} ==", f"total wall time : {summary['total_s']:.3f} s", ""]
    lines.append(
        f"{'function':<42} {'module':<32} {'ncalls':>6} {'tottime_s':>10} "
        f"{'cumtime_s':>10} {'tot%':>6} {'cum%':>6}"
    )
    lines.append("-" * 122)
    for r in summary["top"]:
        lines.append(
            f"{r['function'][:42]:<42} {r['module'][:32]:<32} {r['ncalls']:>6} "
            f"{r['tottime_s']:>10.4f} {r['cumtime_s']:>10.4f} "
            f"{r['tottime_pct']:>6.2f} {r['cumtime_pct']:>6.2f}"
        )
    return "\n".join(lines)


# ───────────────────────────────────────────────────────────────────
# Workloads
# ───────────────────────────────────────────────────────────────────


def profile_single_symbol(csv_path: Path, dump_path: Path | None = None) -> dict[str, Any]:
    """Profile one cold ``analyze_stock`` (full single-symbol path)."""
    from src.engine.analyzer import analyze_stock  # noqa: PLC0415

    def _workload() -> None:
        analyze_stock(str(csv_path))

    return run_profiled(_workload, dump_path=dump_path)


def profile_cold_scan(
    data_dir: Path,
    dump_path: Path | None = None,
) -> dict[str, Any]:
    """Profile a full cold ``scan_market(workers=1)`` over *data_dir*.

    Sequential workers=1 keeps every call in the profiled main thread
    (the parallel path runs worker threads that cProfile does not
    attribute to the top-level call).  This exercises the complete
    pipeline: CSV parse -> indicator chain -> score/signal -> ranking ->
    scan-level alert batching.
    """
    from src.cache.scanner_cache import scanner_cache  # noqa: PLC0415
    from src.indicators.cache import indicator_cache  # noqa: PLC0415
    from src.scanner import engine as scanner_engine  # noqa: PLC0415

    old_dir = scanner_engine.DATA_DIRECTORY
    scanner_engine.DATA_DIRECTORY = str(data_dir)
    # Self-isolating (Sprint 12.3 review fix): the scan fires
    # ``process_alert_batch`` for fresh analyses, so alert history must
    # be redirected even when this function is called directly (tests,
    # scripts) — not only via ``run_profiling``'s ``_state_files``.  A
    # direct call previously wrote synthetic symbols into the production
    # ``data/alerts/history.json``.
    try:
        with tempfile.TemporaryDirectory(prefix="nepse_cold_iso_") as iso_tmp:
            with _state_files(Path(iso_tmp)):
                try:
                    scanner_cache.clear()
                    indicator_cache.clear()

                    def _workload() -> None:
                        scanner_engine.scan_market(workers=1)

                    return run_profiled(_workload, dump_path=dump_path)
                finally:
                    # Leave no fingerprints behind: the temp corpus is
                    # deleted when the orchestrator's TemporaryDirectory
                    # exits, so any cached entries would be stale.  Clear
                    # to match the benchmark suite's cache hygiene
                    # (conftest only clears the indicator cache).
                    scanner_cache.clear()
    finally:
        scanner_engine.DATA_DIRECTORY = old_dir


# ───────────────────────────────────────────────────────────────────
# Orchestration
# ───────────────────────────────────────────────────────────────────


def run_profiling(
    sizes: tuple[int, ...] = DEFAULT_SIZES,
    rows: int = DEFAULT_ROWS,
    out_dir: Path = RESULTS_DIR,
    single: bool = True,
) -> dict[str, Any]:
    """Run the Sprint 12.2 profiling workloads and write artifacts.

    Every workload runs in its own private temp dir with alert-history /
    portfolio persistence redirected via ``_state_files`` — production
    state is never read or written.

    Returns:
        A dict with ``environment`` (python/platform), ``single``
        (summary + dump path) and ``sizes`` (list of ``{symbols, rows,
        summary, dump_path, report_path}``).  All values are
        deterministic in *structure* (function ordering may vary
        slightly run-to-run, but the workload, corpus seed and output
        layout are reproducible).
    """
    import platform

    env = {
        "python": sys.version.split()[0],
        "platform": platform.platform(),
        "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }

    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    results: dict[str, Any] = {"environment": env, "sizes": []}

    if single:
        with tempfile.TemporaryDirectory(prefix="nepse_prof_single_") as tmp:
            data_dir = Path(tmp) / "data"
            write_csvs(data_dir, 1, rows)
            csv_path = sorted(data_dir.glob("*.csv"))[0]
            dump = out_dir / "single_symbol.prof"
            with _state_files(Path(tmp)):
                summary = profile_single_symbol(csv_path, dump_path=dump)
            # Same cache hygiene as the scan path: the temp corpus file is
            # deleted when this context exits, so any scanner-cache entry
            # for it would be stale (harmless, but inconsistent).
            from src.cache.scanner_cache import scanner_cache  # noqa: PLC0415

            scanner_cache.clear()
            report = format_report(summary, f"single-symbol cold analyze_stock ({rows} rows)")
            (out_dir / "single_symbol.txt").write_text(report, encoding="utf-8")
            results["single"] = {
                "rows": rows,
                "summary": summary,
                "dump_path": str(dump),
                "report_path": str(out_dir / "single_symbol.txt"),
            }

    for n in sizes:
        with tempfile.TemporaryDirectory(prefix=f"nepse_prof_scan_{n}_") as tmp:
            data_dir = Path(tmp) / "data"
            write_csvs(data_dir, n, rows)
            dump = out_dir / f"scan_{n}.prof"
            with _state_files(Path(tmp)):
                summary = profile_cold_scan(data_dir, dump_path=dump)
            report = format_report(
                summary, f"cold scan_market(workers=1) — {n} symbols x {rows} rows"
            )
            (out_dir / f"scan_{n}.txt").write_text(report, encoding="utf-8")
            results["sizes"].append(
                {
                    "symbols": n,
                    "rows": rows,
                    "summary": summary,
                    "dump_path": str(dump),
                    "report_path": str(out_dir / f"scan_{n}.txt"),
                }
            )

    # The orchestrator writes the machine-readable summary JSON (the
    # same file ``main()`` prints the reports from).
    (out_dir / "profile_summary.json").write_text(
        json.dumps(results, indent=2), encoding="utf-8"
    )
    return results


def main() -> int:
    parser = argparse.ArgumentParser(description="NEPSE cold-path profiler (Sprint 12.2)")
    parser.add_argument(
        "--sizes",
        type=int,
        nargs="+",
        default=list(DEFAULT_SIZES),
        help="Multi-symbol scan profile sizes (default 50 200 500)",
    )
    parser.add_argument("--rows", type=int, default=DEFAULT_ROWS, help="Rows per symbol")
    parser.add_argument(
        "--out",
        type=str,
        default=str(RESULTS_DIR),
        help="Output directory (default benchmarks/results/profile)",
    )
    parser.add_argument(
        "--no-single", action="store_true", help="Skip the single-symbol profile"
    )
    args = parser.parse_args()

    results = run_profiling(
        sizes=tuple(args.sizes),
        rows=args.rows,
        out_dir=Path(args.out),
        single=not args.no_single,
    )

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    json_path = out_dir / "profile_summary.json"

    print(f"Profiling complete — summary: {json_path}")
    if results.get("single"):
        s = results["single"]
        print(format_report(s["summary"], f"single-symbol cold analyze_stock ({s['rows']} rows)"))
    for size_result in results["sizes"]:
        print(format_report(
            size_result["summary"],
            f"cold scan_market(workers=1) — {size_result['symbols']} symbols x {size_result['rows']} rows",
        ))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
