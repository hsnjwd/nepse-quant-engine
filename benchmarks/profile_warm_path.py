"""Warm-path profiling for the NEPSE indicator pipeline (Sprint 12.3, Phases 4-5).

Sprint 12.2 profiled the *cold* path (CSV I/O ~38%, rolling/ewm ~36%,
column insertion ~14%).  Sprint 12.3 asks the complementary question:
when the scanner cache is warm, where does the remaining runtime go?

Workloads (all reuse ``benchmarks.profile_cold_path`` helpers):

- **warm scan**  : pre-warm the scanner cache with one unprofiled
  ``scan_market(workers=1)``, then profile a second scan — every symbol
  is now a scanner-cache analysis hit, so the profile shows the true
  warm-path cost (fingerprinting, dict copies, ranking) with
  zero CSV/indicator work.
- **single warm** : the same, over a 1-symbol corpus — the per-symbol
  warm cost in isolation.
- **indicator families** : profile one *cold* ``analyze_dataframe`` and
  attribute cumulative time to the four indicator families
  (moving averages / momentum / volume / volatility) by entry-point
  function name — the per-family split the sprint asks for, without
  touching production code.

Both workloads run under ``benchmarks.pipeline._state_files`` isolation
so alert history / portfolio persistence never touch production state.
The scanner cache is cleared on exit so no stale fingerprints for the
deleted temp corpus are left behind (same hygiene as
``profile_cold_path``).

Outputs (JSON + pstats dump + text report) land under
``benchmarks/results/profile_warm/``.  Pure helpers are exercised by
``tests/test_sprint12_3.py`` (structure/determinism only — no wall-clock
assertions).

Usage::

    python -m benchmarks.profile_warm_path --sizes 50 200 500 --rows 500
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from benchmarks.common import make_synthetic_df, write_csvs  # noqa: E402
from benchmarks.pipeline import _state_files  # noqa: E402
from benchmarks.profile_cold_path import format_report, run_profiled  # noqa: E402

RESULTS_DIR = Path(__file__).resolve().parent / "results" / "profile_warm"

DEFAULT_SIZES = (50, 200, 500)
DEFAULT_ROWS = 500

# Indicator-family entry points as ``(module_path, function)`` tuples.
# pstats module strings are *file paths* (e.g. ``src/indicators/moving_average.py``),
# NOT dotted module names — matching on ``(path, func)`` with a
# slash-normalised ``endswith`` is robust to absolute/backslash paths.
# The family's cumulative time is its top-level ``add_*_indicators``
# function, which includes every pandas rolling/ewm subcall beneath it.
INDICATOR_FAMILIES = {
    "moving_averages": ("src/indicators/moving_average.py", "add_moving_averages"),
    "momentum": ("src/indicators/momentum.py", "add_momentum_indicators"),
    "volume": ("src/indicators/volume.py", "add_volume_indicators"),
    "volatility": ("src/indicators/volatility.py", "add_volatility_indicators"),
}


# ───────────────────────────────────────────────────────────────────
# Warm workloads
# ───────────────────────────────────────────────────────────────────


def profile_warm_scan(
    data_dir: Path,
    dump_path: Path | None = None,
    single_symbol: bool = False,
) -> dict[str, Any]:
    """Profile a *warm* ``scan_market(workers=1)`` over *data_dir*.

    The scanner cache is populated with one unprofiled scan first, so
    the profiled scan hits ``scanner_cache.get_analysis`` for every
    file — ``load_csv`` / ``analyze_dataframe`` / ``process_alert_batch``
    are never reached.  Sequential workers=1 keeps every call in the
    profiled main thread (thread-pool records scatter across workers).

    Args:
        data_dir: Directory containing the OHLCV CSVs.
        dump_path: Optional pstats dump location.
        single_symbol: Informational flag recorded in the result (the
            orchestrator uses it for the 1-symbol corpus).

    Returns:
        The ``run_profiled`` summary dict (``total_s`` + ``top``).
    """
    from src.cache.scanner_cache import scanner_cache  # noqa: PLC0415
    from src.indicators.cache import indicator_cache  # noqa: PLC0415
    from src.scanner import engine as scanner_engine  # noqa: PLC0415

    old_dir = scanner_engine.DATA_DIRECTORY
    scanner_engine.DATA_DIRECTORY = str(data_dir)
    # Self-isolating: the warm-up scan fires ``process_alert_batch``
    # (fresh analyses), so alert history must be redirected even when
    # this function is called directly (tests, scripts) — not just from
    # the orchestrator.  Sprint 12.3 review fix: the previous version
    # only isolated via ``run_warm_profiling``'s ``_state_files``, so a
    # direct call wrote synthetic symbols into the production
    # ``data/alerts/history.json``.
    try:
        with tempfile.TemporaryDirectory(prefix="nepse_warm_iso_") as iso_tmp:
            with _state_files(Path(iso_tmp)):
                try:
                    scanner_cache.clear()
                    indicator_cache.clear()
                    # Warm the cache: one unprofiled scan (cold for
                    # every symbol, caching analysis outputs as it goes).
                    scanner_engine.scan_market(workers=1)
                    warm_stats_before = scanner_cache.stats()

                    def _workload() -> None:
                        scanner_engine.scan_market(workers=1)

                    summary = run_profiled(_workload, dump_path=dump_path)
                    warm_stats_after = scanner_cache.stats()
                    summary["warm_phase_analysis_hits"] = (
                        warm_stats_after["analysis_hits"]
                        - warm_stats_before["analysis_hits"]
                    )
                    summary["warm_phase_analysis_misses"] = (
                        warm_stats_after["analysis_misses"]
                        - warm_stats_before["analysis_misses"]
                    )
                    return summary
                finally:
                    scanner_cache.clear()
    finally:
        scanner_engine.DATA_DIRECTORY = old_dir


def profile_indicator_families(
    rows: int = DEFAULT_ROWS,
    dump_path: Path | None = None,
) -> dict[str, Any]:
    """Attribute one cold ``analyze_dataframe`` to the indicator families.

    Profiles a single cold analysis and reports each family's cumulative
    time (the family's top-level ``add_*_indicators`` entry point, which
    includes every pandas rolling/ewm subcall) as a percentage of the
    profiled total — the per-family split the sprint asks for.  The
    indicator cache is cleared before the run so the chain is genuinely
    cold.  The frame is synthetic and deterministic (seeded).

    Returns:
        ``{total_s, total_ms, families: {name: {cumtime_s,
        cumtime_pct, ncalls}}, warm_hit_rate}`` — ``warm_hit_rate`` is
        the indicator-cache hit rate for the *same* frame analysed a
        second time (documents that the chain is fully cached warm).
    """
    from src.engine.analyzer import analyze_dataframe  # noqa: PLC0415
    from src.indicators.cache import indicator_cache  # noqa: PLC0415

    indicator_cache.clear()
    df = make_synthetic_df(rows=rows)

    def _cold() -> None:
        analyze_dataframe(df, symbol="SYN")

    summary = run_profiled(_cold, dump_path=dump_path)

    def _matches(row: dict[str, Any], mod_path: str, func: str) -> bool:
        """Match a profile row whose module is *mod_path* (slash-normalised)."""
        return row["function"] == func and row["module"].replace("\\", "/").endswith(mod_path)

    families: dict[str, dict[str, Any]] = {}
    for name, (mod_path, func) in INDICATOR_FAMILIES.items():
        row = next((r for r in summary["top"] if _matches(r, mod_path, func)), None)
        families[name] = (
            {
                "cumtime_s": row["cumtime_s"],
                "cumtime_pct": row["cumtime_pct"],
                "ncalls": row["ncalls"],
            }
            if row is not None
            else {"cumtime_s": 0.0, "cumtime_pct": 0.0, "ncalls": 0}
        )

    # Warm behaviour: the same frame again is an indicator-cache hit, so
    # every family's warm contribution is ~0 (chain never re-runs).
    indicator_cache.reset_stats()
    analyze_dataframe(df, symbol="SYN")
    warm_stats = indicator_cache.stats()

    return {
        "rows": rows,
        "total_s": summary["total_s"],
        "total_ms": round(summary["total_s"] * 1000.0, 2),
        "families": families,
        "warm_hit_rate": round(warm_stats["hit_rate"], 3),
    }


def format_families_report(profile: dict[str, Any]) -> str:
    """Render the per-family indicator profile as a table."""
    lines = [
        "== Indicator families (cold analyze_dataframe) ==",
        f"rows={profile['rows']}  total={profile['total_ms']} ms (profiled)  "
        f"warm hit rate={profile['warm_hit_rate']*100:.1f}%",
        "",
        f"{'family':<18} {'cumtime_s':>10} {'% of total':>10} {'ncalls':>8}",
        "-" * 48,
    ]
    for name, data in profile["families"].items():
        lines.append(
            f"{name:<18} {data['cumtime_s']:>10.4f} {data['cumtime_pct']:>9.2f}% "
            f"{data['ncalls']:>8}"
        )
    return "\n".join(lines)


# ───────────────────────────────────────────────────────────────────
# Orchestration
# ───────────────────────────────────────────────────────────────────


def run_warm_profiling(
    sizes: tuple[int, ...] = DEFAULT_SIZES,
    rows: int = DEFAULT_ROWS,
    out_dir: Path = RESULTS_DIR,
) -> dict[str, Any]:
    """Run the warm-scan profiles + indicator-family profile.

    Every workload runs in its own private temp dir with alert-history /
    portfolio persistence redirected via ``_state_files``.  Returns a
    deterministic-in-structure dict with ``single`` (1-symbol warm scan),
    ``sizes`` (per-size warm scan summaries + dump/report paths) and
    ``indicator_families``.
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

    # 1-symbol warm scan (per-symbol warm cost in isolation).
    with tempfile.TemporaryDirectory(prefix="nepse_warm_single_") as tmp:
        data_dir = Path(tmp) / "data"
        write_csvs(data_dir, 1, rows)
        dump = out_dir / "warm_single.prof"
        with _state_files(Path(tmp)):
            summary = profile_warm_scan(data_dir, dump_path=dump, single_symbol=True)
        report = format_report(summary, f"warm scan_market(workers=1) — 1 symbol x {rows} rows")
        (out_dir / "warm_single.txt").write_text(report, encoding="utf-8")
        results["single"] = {
            "rows": rows,
            "summary": summary,
            "dump_path": str(dump),
            "report_path": str(out_dir / "warm_single.txt"),
        }

    for n in sizes:
        with tempfile.TemporaryDirectory(prefix=f"nepse_warm_scan_{n}_") as tmp:
            data_dir = Path(tmp) / "data"
            write_csvs(data_dir, n, rows)
            dump = out_dir / f"warm_scan_{n}.prof"
            with _state_files(Path(tmp)):
                summary = profile_warm_scan(data_dir, dump_path=dump)
            report = format_report(
                summary, f"warm scan_market(workers=1) — {n} symbols x {rows} rows"
            )
            (out_dir / f"warm_scan_{n}.txt").write_text(report, encoding="utf-8")
            results["sizes"].append(
                {
                    "symbols": n,
                    "rows": rows,
                    "summary": summary,
                    "dump_path": str(dump),
                    "report_path": str(out_dir / f"warm_scan_{n}.txt"),
                }
            )

    # Indicator-family attribution (Phase 5).  ``_state_files`` needs a
    # directory for the redirected alert history; a TemporaryDirectory
    # context (like every other workload) guarantees cleanup.
    fam_dump = out_dir / "indicator_families.prof"
    with tempfile.TemporaryDirectory(prefix="nepse_families_") as fam_tmp:
        with _state_files(Path(fam_tmp)):
            fam = profile_indicator_families(rows=rows, dump_path=fam_dump)
    (out_dir / "indicator_families.txt").write_text(
        format_families_report(fam), encoding="utf-8"
    )
    results["indicator_families"] = {**fam, "dump_path": str(fam_dump)}

    (out_dir / "profile_warm_summary.json").write_text(
        json.dumps(results, indent=2), encoding="utf-8"
    )
    return results


def main() -> int:
    parser = argparse.ArgumentParser(description="NEPSE warm-path profiler (Sprint 12.3)")
    parser.add_argument(
        "--sizes", type=int, nargs="+", default=list(DEFAULT_SIZES),
        help="Warm scan profile sizes (default 50 200 500)",
    )
    parser.add_argument("--rows", type=int, default=DEFAULT_ROWS, help="Rows per symbol")
    parser.add_argument("--out", type=str, default=str(RESULTS_DIR), help="Output directory")
    args = parser.parse_args()

    results = run_warm_profiling(
        sizes=tuple(args.sizes), rows=args.rows, out_dir=Path(args.out)
    )
    out_dir = Path(args.out)
    json_path = out_dir / "profile_warm_summary.json"
    print(f"Warm profiling complete — summary: {json_path}")
    if results.get("single"):
        s = results["single"]
        print(format_report(s["summary"], f"warm scan — 1 symbol x {s['rows']} rows"))
    for size_result in results["sizes"]:
        print(format_report(
            size_result["summary"],
            f"warm scan_market(workers=1) — {size_result['symbols']} symbols x {size_result['rows']} rows",
        ))
    print(format_families_report(results["indicator_families"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
