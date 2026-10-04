"""Benchmark runner for the NEPSE Quant Engine (Sprint 11.1).

Usage::

    python -m benchmarks.runner --symbols 50 --rows 500 [--out benchmarks/results]

Records a JSON snapshot under ``--out`` with environment metadata,
execution times, symbol/row counts, cache state and peak memory.
Running again prints a before/after comparison against the latest run.
"""

from __future__ import annotations

import argparse
import json
import platform
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from benchmarks.common import write_csvs
from benchmarks import corpus as corpus_mod
from benchmarks import pipeline

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.config import BENCHMARK_RESULTS_DIR, BENCHMARK_ROWS, BENCHMARK_SYMBOLS, SCANNER_WORKERS  # noqa: E402


def _prepare_data_dir(
    source: str,
    symbols: int,
    rows: int,
    tmp: Path,
    seed: int = 42,
) -> tuple[Path, dict[str, Any]]:
    """Create a benchmark data directory from real or synthetic CSVs.

    Returns ``(data_dir, corpus_info)`` where *corpus_info* describes
    what was measured (source, symbols, rows, seed).
    """
    if source == "real":
        spec = corpus_mod.select_corpus(symbols, seed=seed)
        data_dir = tmp / "data"
        corpus_mod.copy_corpus(spec, data_dir)
        info = spec.to_dict()
        info["rows_per_symbol"] = None  # real files vary in length
        return data_dir, info
    data_dir = tmp / "data"
    write_csvs(data_dir, symbols, rows)
    return data_dir, {
        "source": "synthetic",
        "symbol_count": symbols,
        "rows_per_symbol": rows,
        "total_rows": symbols * rows,
    }


def run_all(
    symbols: int,
    rows: int,
    results_dir: Path,
    source: str = "synthetic",
    workers: int | None = None,
    seed: int = 42,
    scale_sizes: tuple[int, ...] | None = None,
) -> dict[str, Any]:
    """Run every benchmark and persist + print the results.

    Args:
        symbols: Number of symbols (real corpus is clamped to available
            files when *source* is ``"real"``).
        rows: Rows per symbol (synthetic only).
        results_dir: Directory for the JSON snapshot.
        source: ``"synthetic"`` (default) or ``"real"`` (scraped CSVs
            from ``data/raw``).
        workers: Scanner worker count for the scaling benchmark
            (``1`` = sequential fallback; ``None`` = ``SCANNER_WORKERS``).
        seed: Corpus selection seed (real source only).
        scale_sizes: When given, run the Sprint 12.1 ``process_alert_batch``
            scaling benchmark at these symbol counts (e.g. ``(500, 750,
            1000)``) on a synthetic corpus and store it under
            ``alert_batch_scale``.  Optional — the standard runner stays
            fast when omitted.
    """
    results: dict[str, Any] = {
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "environment": {
            "python": sys.version.split()[0],
            "platform": platform.platform(),
            "pandas": pd.__version__,
            "numpy": np.__version__,
            "scanner_workers_default": SCANNER_WORKERS,
        },
        "config": {"symbols": symbols, "rows": rows, "source": source},
    }

    with tempfile.TemporaryDirectory(prefix="nepse_bench_") as tmp:
        data_dir, corpus_info = _prepare_data_dir(source, symbols, rows, Path(tmp), seed=seed)
        results["corpus"] = corpus_info
        actual = corpus_info.get("symbol_count", symbols)
        # For real corpora the data dir is already populated with scraped
        # CSVs — never mix synthetic frames into a real-data run.
        populate = source != "real"

        results["single_analysis"] = pipeline.bench_single_analysis(rows)
        results["history_load"] = pipeline.bench_history_load(data_dir, actual, rows, populate=populate)
        # Sprint 11.6 — cold-path CSV baseline: stage-by-stage profile
        # (filesystem read, parse, numeric, datetime, sort, dropna,
        # cache, copy) plus cold-load percentiles / peak memory measured
        # independently from analysis, so the dominant cold-load stage
        # is measured, not assumed.
        results["csv_stages"] = pipeline.bench_csv_stages(data_dir, actual)
        results["csv_cold_load"] = pipeline.bench_cold_load(data_dir, actual)
        results["scan"] = pipeline.bench_scan(data_dir, actual, rows, populate=populate)
        results["scan_scale"] = pipeline.bench_scan_scale(data_dir, actual, workers=workers)
        results["scan_scale_sequential"] = pipeline.bench_scan_scale(data_dir, actual, workers=1)
        results["backtest"] = pipeline.bench_backtest(rows)
        results["strategy"] = pipeline.bench_strategy_execution(rows)
        results["cache"] = pipeline.bench_cache_hit_miss(data_dir, actual, rows)
        # Sprint 11.3 — scan-level alert batching: per-symbol vs one-shot
        # processing (wall time + history read/write counts).  Owns its
        # own temp dir internally.
        results["alert_batch"] = pipeline.bench_alert_batch(actual)
        # Sprint 12.0 (Phase 5) — market-scan alert path: legacy
        # per-symbol vs batched processing with cold/warm percentiles,
        # read/write counts and peak memory over the same corpus.
        results["scan_alert_batch"] = pipeline.bench_scan_alert_batch(
            data_dir, actual, rows, populate=populate
        )
        # Sprint 12.0 (Phase 4) — legacy vs batch equivalence flags on a
        # deterministic corpus (analysis/alerts/ranking/errors).
        results["scan_alert_equivalence"] = pipeline.bench_scan_alert_equivalence(
            min(actual, 8), rows
        )
        # Sprint 11.4/11.5 — indicator cache: first vs repeated analysis.
        # Sprint 11.5: real-corpus runs feed the actual scraped CSVs
        # (loaded via the same load_csv path the scanner uses) instead
        # of synthetic frames, so the primary result is real data.
        results["indicator_cache"] = pipeline.bench_indicator_cache(
            actual,
            rows,
            data_dir=data_dir if source == "real" else None,
        )

    # API benchmarks own their temp dirs internally (the caller's temp
    # directory has already been torn down by this point).
    results["api_analyze"] = pipeline.bench_api_analyze(symbols, rows)
    results["api_portfolio"] = pipeline.bench_api_portfolio()
    # Sprint 11.9 — API multi-analysis alert path: legacy per-symbol
    # vs the optional batched path (ENABLE_ANALYZE_ALERT_BATCH).  Owns
    # its own temp dir; informational (never gates CI).
    results["api_alert_batch"] = pipeline.bench_api_alert_batch(actual)
    # Sprint 12.1 (Phase 3) — process_alert_batch scaling at 500+
    # symbols on a synthetic corpus (optional, opt-in via --scale).
    if scale_sizes:
        results["alert_batch_scale"] = pipeline.bench_alert_batch_scale(sizes=scale_sizes)

    results_dir.mkdir(parents=True, exist_ok=True)
    results["_path"] = str(results_dir.resolve())
    latest = _latest_result(results_dir)
    if latest:
        results["previous"] = _compare(results, latest)

    # Distinct filename prefix so the Sprint 11.1 suite never compares
    # against the older scripts/benchmarks.py snapshots in the same dir.
    path = results_dir / f"runner_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    path.write_text(json.dumps(results, indent=2), encoding="utf-8")
    _print_report(results)
    return results


def _latest_result(results_dir: Path) -> dict[str, Any] | None:
    files = sorted(results_dir.glob("runner_*.json"))
    if not files:
        return None
    try:
        return json.loads(files[-1].read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


_COMPARE_KEYS: list[tuple[str, str]] = [
    ("single_analysis", "analysis_best_ms"),
    ("single_analysis", "analysis_avg_ms"),
    ("history_load", "history_cold_ms"),
    ("history_load", "history_warm_avg_ms"),
    ("scan", "scan_cold_ms"),
    ("scan", "scan_warm_avg_ms"),
    ("backtest", "backtest_best_ms"),
    ("strategy", "strategy_best_ms"),
    ("cache", "cache_miss_ms"),
    ("cache", "cache_hit_avg_ms"),
    ("scan_scale_sequential", "cold.p50_ms"),
    ("scan_scale", "cold.p50_ms"),
    ("alert_batch", "batch_ms"),
    ("alert_batch", "per_symbol_ms"),
    ("scan_alert_batch", "batch.warm.p50_ms"),
    ("scan_alert_batch", "legacy.warm.p50_ms"),
    ("indicator_cache", "indicator_cold_avg_ms"),
    ("indicator_cache", "indicator_warm_avg_ms"),
    ("api_analyze", "api_analyze_avg_ms"),
    ("api_portfolio", "api_portfolio_avg_ms"),
]


def _compare(new: dict[str, Any], old: dict[str, Any]) -> dict[str, Any]:
    comparison: dict[str, Any] = {}
    for section, key in _COMPARE_KEYS:
        old_val = _nested_get(old, section, key)
        new_val = _nested_get(new, section, key)
        if isinstance(old_val, (int, float)) and isinstance(new_val, (int, float)) and old_val:
            pct = (new_val - old_val) / old_val * 100.0
            comparison[f"{section}.{key}"] = {
                "previous": round(old_val, 2),
                "current": round(new_val, 2),
                "pct_change": round(pct, 2),
            }
    return comparison


def _nested_get(results: dict[str, Any], section: str, key: str) -> Any:
    """Look up ``results[section][key]`` where *key* may be dotted
    (e.g. ``"cold.p50_ms"`` for nested metric dicts)."""
    value: Any = results.get(section, {})
    for part in key.split("."):
        if not isinstance(value, dict):
            return None
        value = value.get(part)
    return value


def _print_report(results: dict[str, Any]) -> None:
    line = "─" * 78
    print(f"\n{line}\n  NEPSE Quant Engine — Performance Baseline\n{line}")
    print(f"  Timestamp : {results['timestamp']}")
    print(f"  Python    : {results['environment']['python']}")
    print(f"  pandas    : {results['environment']['pandas']}")
    print(f"  Symbols   : {results['config']['symbols']}  Rows: {results['config']['rows']}  Source: {results['config'].get('source', 'synthetic')}")
    print(f"  Synthetic microbenchmarks: single_analysis, strategy, backtest")
    print(line)

    rows: list[tuple[str, Any]] = [
        ("Single-stock analysis (best)", results.get("single_analysis", {}).get("analysis_best_ms")),
        ("Single-stock analysis (avg)", results.get("single_analysis", {}).get("analysis_avg_ms")),
        ("History load (cold)", results.get("history_load", {}).get("history_cold_ms")),
        ("History load (warm avg)", results.get("history_load", {}).get("history_warm_avg_ms")),
        ("Scan (cold)", results.get("scan", {}).get("scan_cold_ms")),
        ("Scan (warm avg)", results.get("scan", {}).get("scan_warm_avg_ms")),
        ("Scan peak memory (MiB)", results.get("scan", {}).get("scan_peak_mib")),
        ("Backtest (best)", results.get("backtest", {}).get("backtest_best_ms")),
        ("Strategy exec (best)", results.get("strategy", {}).get("strategy_best_ms")),
        ("DataService cache miss", results.get("cache", {}).get("cache_miss_ms")),
        ("DataService cache hit (avg)", results.get("cache", {}).get("cache_hit_avg_ms")),
        ("API /analyze (avg)", results.get("api_analyze", {}).get("api_analyze_avg_ms")),
        ("API /portfolio (avg)", results.get("api_portfolio", {}).get("api_portfolio_avg_ms")),
    ]
    for label, value in rows:
        unit = "MiB" if "memory" in label else "ms"
        if isinstance(value, (int, float)):
            print(f"  {label:<30} {value:>12.2f} {unit}")
        else:
            print(f"  {label:<30} {'n/a':>12}")

    scale = results.get("scan_scale") or {}
    seq = results.get("scan_scale_sequential") or {}
    if scale.get("cold") and seq.get("cold"):
        print(f"\n{line}\n  Scanner scaling (cold p50, ms)\n{line}")
        print(f"  {'Sequential':<30} {seq['cold'].get('p50_ms', 0):>12.2f} ms")
        print(f"  {'Parallel':<30} {scale['cold'].get('p50_ms', 0):>12.2f} ms")
        workers_label = scale.get('workers')
        workers_label = workers_label if workers_label is not None else 'default'
        print(f"  {'Workers':<30} {workers_label:>12}")
        print(f"  {'Successful / skipped':<30} {scale.get('successful', 0):>6} / {scale.get('skipped', 0):<6}")

    alerts = results.get("alert_batch") or {}
    if alerts:
        print(f"\n{line}\n  Alert batching (Sprint 11.3, {alerts.get('symbols', '?')} symbols)\n{line}")
        print(f"  {'Per-symbol (ms)':<30} {alerts.get('per_symbol_ms', 0):>12.2f} ms")
        print(f"  {'  reads / writes':<30} {alerts.get('per_symbol_reads', 0):>6} / {alerts.get('per_symbol_writes', 0):<6}")
        print(f"  {'Batch (ms)':<30} {alerts.get('batch_ms', 0):>12.2f} ms")
        print(f"  {'  reads / writes':<30} {alerts.get('batch_reads', 0):>6} / {alerts.get('batch_writes', 0):<6}")
        print(f"  {'Speedup':<30} {alerts.get('speedup_x', 0):>12.2f}x")

    ind_cache = results.get("indicator_cache") or {}
    if ind_cache:
        print(f"\n{line}\n  Indicator cache (Sprint 11.4/11.5, {ind_cache.get('symbols', '?')} symbols)\n{line}")
        print(f"  {'Data source':<30} {str(ind_cache.get('data_source', 'synthetic')):>12}")
        print(f"  {'First analysis (avg ms)':<30} {ind_cache.get('indicator_cold_avg_ms', 0):>12.2f} ms")
        print(f"  {'Repeated analysis (avg ms)':<30} {ind_cache.get('indicator_warm_avg_ms', 0):>12.2f} ms")
        print(f"  {'Speedup':<30} {ind_cache.get('speedup_x', 0):>12.2f}x")
        print(f"  {'Hit rate':<30} {ind_cache.get('hit_rate', 0):>12.1%}")

    if results.get("previous"):
        print(f"\n{line}\n  vs previous run ({len(results['previous'])} metrics)\n{line}")
        for key, vals in results["previous"].items():
            print(f"  {key:<40} {vals['previous']:>10.2f} -> {vals['current']:>10.2f}  ({vals['pct_change']:+.1f}%)")

    print(line)
    print(f"  Results saved to: {results.get('_path', BENCHMARK_RESULTS_DIR)}")
    print(f"{line}\n")


def main() -> int:
    parser = argparse.ArgumentParser(description="NEPSE Quant Engine performance benchmarks")
    parser.add_argument("--symbols", type=int, default=BENCHMARK_SYMBOLS, help="Number of symbols")
    parser.add_argument("--rows", type=int, default=BENCHMARK_ROWS, help="Rows per symbol (synthetic only)")
    parser.add_argument("--out", type=str, default=BENCHMARK_RESULTS_DIR, help="Results directory")
    parser.add_argument(
        "--source",
        type=str,
        default="synthetic",
        choices=["synthetic", "real"],
        help="Benchmark corpus: synthetic frames or real scraped NEPSE CSVs",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=None,
        help="Scanner worker count for the scaling benchmark (1 = sequential; default = SCANNER_WORKERS)",
    )
    parser.add_argument("--seed", type=int, default=42, help="Corpus selection seed (real source)")
    parser.add_argument(
        "--scale",
        type=int,
        nargs="+",
        default=None,
        help=(
            "Symbol counts for the Sprint 12.1 process_alert_batch scaling "
            "benchmark, e.g. --scale 500 750 1000 (synthetic corpus)"
        ),
    )
    args = parser.parse_args()

    # Windows consoles default to cp1252 which cannot encode the report's
    # box-drawing characters — switch to UTF-8 (or replace) for output.
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass

    run_all(
        symbols=args.symbols,
        rows=args.rows,
        results_dir=Path(args.out),
        source=args.source,
        workers=args.workers,
        seed=args.seed,
        scale_sizes=tuple(args.scale) if args.scale else None,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
