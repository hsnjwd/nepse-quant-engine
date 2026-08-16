"""Performance benchmarks — compatibility wrapper (DEPRECATED).

Sprint 11.2 consolidated the benchmark suite: the canonical
implementation now lives in :mod:`benchmarks` (``python -m
benchmarks.runner``).  This module is kept as a thin compatibility
wrapper so existing imports and tests keep working:

- ``make_synthetic_df`` re-exported from :mod:`benchmarks.common`
- ``run_all`` delegates to :mod:`benchmarks.pipeline` and reshapes the
  results into the legacy schema (keys ``scan`` / ``analysis`` /
  ``history`` / ``backtest`` / ``api`` / ``memory`` / ``startup``) and
  writes ``benchmark_*.json`` snapshots exactly like the original
  Sprint 11 script.

No benchmark logic lives here anymore — every measurement is delegated
to :mod:`benchmarks`.

Usage::

    python scripts/benchmarks.py --symbols 50 --rows 500
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.config import BENCHMARK_RESULTS_DIR, BENCHMARK_ROWS, BENCHMARK_SYMBOLS  # noqa: E402

from benchmarks import pipeline  # noqa: E402
from benchmarks.common import make_synthetic_df, write_csvs  # noqa: E402,F401 (re-exported)

# Re-export so ``from scripts.benchmarks import make_synthetic_df`` keeps
# working for the legacy test suite.
__all__ = ["make_synthetic_df", "run_all", "write_csvs"]


def _latest_result(results_dir: Path) -> dict[str, Any] | None:
    files = sorted(results_dir.glob("benchmark_*.json"))
    if not files:
        return None
    try:
        return json.loads(files[-1].read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def _compare(new: dict[str, Any], old: dict[str, Any]) -> dict[str, Any]:
    keys: list[tuple[str, str]] = [
        ("scan", "scan_cold_ms"),
        ("scan", "scan_warm_avg_ms"),
        ("analysis", "analysis_best_ms"),
        ("history", "history_miss_ms"),
        ("history", "history_hit_avg_ms"),
        ("history", "history_batch_ms"),
        ("backtest", "backtest_best_ms"),
        ("api", "api_health_avg_ms"),
        ("startup", "startup_ms"),
    ]
    comparison: dict[str, Any] = {}
    for section, key in keys:
        old_val = old.get(section, {}).get(key)
        new_val = new.get(section, {}).get(key)
        if isinstance(old_val, (int, float)) and isinstance(new_val, (int, float)) and old_val:
            pct = (new_val - old_val) / old_val * 100.0
            comparison[f"{section}.{key}"] = {
                "previous": round(old_val, 2),
                "current": round(new_val, 2),
                "pct_change": round(pct, 2),
            }
    return comparison


def run_all(symbols: int, rows: int, results_dir: Path) -> dict[str, Any]:
    """Run the benchmark suite (legacy schema) and persist results.

    Delegates every measurement to :mod:`benchmarks.pipeline`; the
    result dict and ``benchmark_*.json`` filename keep the Sprint 11
    output format for backward compatibility.
    """
    import platform  # noqa: PLC0415

    results: dict[str, Any] = {
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "environment": {
            "python": sys.version.split()[0],
            "platform": platform.platform(),
        },
        "config": {"symbols": symbols, "rows": rows},
    }

    from benchmarks.common import benchmark_isolation  # noqa: PLC0415 - lazy

    # Suite-level isolation (Sprint 13.5): the whole run redirects
    # alert/portfolio persistence into a temp dir, restores the scanner
    # DATA_DIRECTORY global, clears the shared scanner cache, and resets
    # the DataService singleton on exit — so an in-process invocation
    # (e.g. from the test suite) can never leak synthetic state that
    # would make later tests order-dependent.  The individual pipeline
    # legs already isolate themselves (``_state_files``); this wraps the
    # suite as one unit.
    with tempfile.TemporaryDirectory(prefix="nepse_bench_") as tmp:
        with benchmark_isolation(Path(tmp) / "iso"):
            data_dir = Path(tmp) / "data"
            write_csvs(data_dir, symbols, rows)

            scan = pipeline.bench_scan(data_dir, symbols, rows)
            results["scan"] = {
                "symbols": scan["symbols"],
                "scan_cold_ms": scan["scan_cold_ms"],
                "scan_warm_avg_ms": scan["scan_warm_avg_ms"],
                "scan_warm_best_ms": scan["scan_warm_best_ms"],
            }
            analysis = pipeline.bench_single_analysis(rows)
            results["analysis"] = {
                "analysis_best_ms": analysis["analysis_best_ms"],
                "analysis_avg_ms": analysis["analysis_avg_ms"],
            }
            cache = pipeline.bench_cache_hit_miss(data_dir, symbols, rows)
            batch = pipeline.bench_history_batch(data_dir, symbols, rows)
            results["history"] = {
                "history_miss_ms": cache["cache_miss_ms"],
                "history_hit_avg_ms": cache["cache_hit_avg_ms"],
                "history_batch_ms": batch["history_batch_ms"],
            }
            backtest = pipeline.bench_backtest(rows)
            results["backtest"] = {
                "backtest_best_ms": backtest["backtest_best_ms"],
                "backtest_avg_ms": backtest["backtest_avg_ms"],
            }
            memory = pipeline.bench_memory_scan(data_dir, symbols)
            results["memory"] = {
                "scan_peak_memory_mb": memory["scan_peak_memory_mb"],
                "scan_cold_ms": memory["scan_cold_ms"],
            }

    results["api"] = pipeline.bench_api_health()
    results["startup"] = pipeline.bench_startup()

    results_dir.mkdir(parents=True, exist_ok=True)
    results["_path"] = str(results_dir.resolve())
    latest = _latest_result(results_dir)
    if latest:
        results["previous"] = _compare(results, latest)

    path = results_dir / f"benchmark_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    path.write_text(json.dumps(results, indent=2), encoding="utf-8")
    _print_report(results)
    return results


def _print_report(results: dict[str, Any]) -> None:
    line = "─" * 78
    print(f"\n{line}\n  NEPSE Quant Engine — Performance Benchmark Report (legacy wrapper)\n{line}")
    print(f"  Timestamp : {results['timestamp']}")
    print(f"  Symbols   : {results['config']['symbols']}  Rows: {results['config']['rows']}")
    print(line)

    rows: list[tuple[str, Any]] = [
        ("Market scan (cold)", results.get("scan", {}).get("scan_cold_ms")),
        ("Market scan (warm avg)", results.get("scan", {}).get("scan_warm_avg_ms")),
        ("Analysis (per stock)", results.get("analysis", {}).get("analysis_best_ms")),
        ("History load (miss)", results.get("history", {}).get("history_miss_ms")),
        ("History load (hit)", results.get("history", {}).get("history_hit_avg_ms")),
        ("History batch", results.get("history", {}).get("history_batch_ms")),
        ("Backtest", results.get("backtest", {}).get("backtest_best_ms")),
        ("API /health avg", results.get("api", {}).get("api_health_avg_ms")),
        ("API /health p95", results.get("api", {}).get("api_health_p95_ms")),
        ("Startup (subprocess)", results.get("startup", {}).get("startup_ms")),
        ("Scan peak memory (MB)", results.get("memory", {}).get("scan_peak_memory_mb")),
    ]
    for label, value in rows:
        unit = "ms" if label != "Scan peak memory (MB)" else "MiB"
        if isinstance(value, (int, float)):
            print(f"  {label:<28} {value:>12.2f} {unit}")
        else:
            print(f"  {label:<28} {'n/a':>12}")

    if results.get("previous"):
        print(f"\n{line}\n  vs previous run ({len(results['previous'])} metrics)\n{line}")
        for key, vals in results["previous"].items():
            print(f"  {key:<38} {vals['previous']:>10.2f} -> {vals['current']:>10.2f}  ({vals['pct_change']:+.1f}%)")

    print(line)
    print(f"  Results saved to: {results.get('_path', BENCHMARK_RESULTS_DIR)}")
    print(f"{line}\n")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="NEPSE Quant Engine performance benchmarks (legacy wrapper — use `python -m benchmarks.runner`)"
    )
    parser.add_argument("--symbols", type=int, default=BENCHMARK_SYMBOLS)
    parser.add_argument("--rows", type=int, default=BENCHMARK_ROWS)
    parser.add_argument("--out", type=str, default=BENCHMARK_RESULTS_DIR)
    args = parser.parse_args()

    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass

    run_all(symbols=args.symbols, rows=args.rows, results_dir=Path(args.out))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
