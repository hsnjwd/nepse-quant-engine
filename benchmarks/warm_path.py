"""Warm-path scanner benchmarking (Sprint 12.3, Phases 2/3/6/7/9).

The Sprint 12.2 cold-path profile showed a 500-symbol cold scan is
dominated by CSV I/O (~38%) and the pandas rolling/ewm indicator chain
(~36%).  The central Sprint 12.3 question is: **when the caches are
warm, where does the remaining runtime go?**

This module measures the production warm path directly:

    scan_market()
        └─ _analyze_file(file)
             ├─ scanner_cache.get_analysis(file)   ← warm: hit, returns
             │                                      cached analysis
             │                                      (cold: miss → analyze_stock)
             └─ rank_market(results)               ← always runs

A warm scan never calls ``load_csv``, ``analyze_dataframe`` or
``process_alert_batch`` (the scanner's ``fresh`` list is empty), so the
remaining cost is: the file glob, per-file fingerprinting (``stat`` +
``Path.resolve``), the analysis ``dict`` copy, and ranking.  This module
quantifies exactly that split with **observable counters**, not just
wall-clock deltas:

- CSV reads      : direct counter on ``csv_loader._read_csv_robust``
- cache hits/miss: ``scanner_cache.stats()`` / ``indicator_cache.stats()``
                   diffs across each phase
- indicator calc : ``indicator_cache`` miss diffs (each miss = one full
                   rolling/ewm chain)
- alert I/O      : read/write counters on ``load_history``/``save_history``
                   (both engine + history namespaces, as in
                   ``bench_scan_alert_batch``)
- failures       : ``skipped`` entries from the last scan run

Cold and warm phases measure the *same* production ``scan_market`` over
the *same* deterministic corpus, with the scanner + indicator caches
cleared between cold repetitions and left populated for warm ones.

Isolation: every run happens under ``benchmarks.pipeline._state_files``
so alert history / portfolio persistence are redirected to a private
temp dir — production ``data/alerts/history.json`` is never touched.
Synthetic corpora use ``benchmarks.common.write_csvs`` (seeded,
deterministic); real-corpus runs copy a seeded ``corpus.select_corpus``
subset into a temp dir (never modifying ``data/raw``).

Usage::

    python -m benchmarks.warm_path --sizes 50 200 500 --rows 500
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from benchmarks.common import write_csvs  # noqa: E402
from benchmarks.pipeline import _percentiles, _state_files  # noqa: E402

RESULTS_DIR = Path(__file__).resolve().parent / "results" / "warm"

DEFAULT_SIZES = (50, 200, 500)
DEFAULT_ROWS = 500

# Source files whose ``.copy()`` call sites the DataFrame-copy audit
# counts (Phase 6).  Files are relative to PROJECT_ROOT.
PIPELINE_SOURCE_FILES = (
    "src/engine/analyzer.py",
    "src/loaders/csv_loader.py",
    "src/cache/scanner_cache.py",
    "src/indicators/cache.py",
    "src/backtest/engine.py",
)


# ───────────────────────────────────────────────────────────────────
# Phase 6 — DataFrame copy audit (static, deterministic)
# ───────────────────────────────────────────────────────────────────


def audit_dataframe_copies() -> dict[str, Any]:
    """Count ``.copy()`` call sites per pipeline source file.

    A *static* audit that parses each file with ``ast`` and records the
    line number of every ``x.copy(...)`` *call* — docstring and comment
    mentions of ``.copy()`` are not AST call nodes, so they can never
    be miscounted (a naive line-grep over-counts docstring prose, e.g.
    the ``copy semantics`` bullet in ``indicators/cache.py``).  This is
    the concrete source-level answer to "where do DataFrame copies
    live".  Deterministic — same input, same output.

    Returns:
        ``{"files": {relpath: {"copy_sites": [line, ...], "count": n}}}``
    """
    import ast

    result: dict[str, Any] = {"files": {}}
    for rel in PIPELINE_SOURCE_FILES:
        path = PROJECT_ROOT / rel
        if not path.exists():
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
        except SyntaxError:
            continue
        sites: list[int] = []
        for node in ast.walk(tree):
            # ``df.copy()`` (or ``value.copy()``) — a call whose callee is
            # an attribute named ``copy``.
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "copy"
            ):
                sites.append(node.lineno)
        unique = sorted(set(sites))
        result["files"][rel] = {"copy_sites": unique, "count": len(unique)}
    return result


# ───────────────────────────────────────────────────────────────────
# Phase 2/3 — cold vs warm scan benchmark with observable counters
# ───────────────────────────────────────────────────────────────────


def bench_warm_scan(
    data_dir: Path,
    symbols: int,
    rows: int,
    populate: bool = True,
    workers: int | None = None,
    cold_reps: int = 3,
    warm_reps: int = 5,
    corpus: str = "synthetic",
) -> dict[str, Any]:
    """Cold vs warm ``scan_market`` over *data_dir* with counters.

    Both phases run the same production ``scan_market`` (worker count
    from *workers* or ``SCANNER_WORKERS``) over the same corpus:

    - **cold**: scanner + indicator caches cleared before every
      repetition → every symbol is a full CSV parse + indicator chain +
      fresh alert batch.
    - **warm**: caches left populated → every symbol is a scanner-cache
      analysis hit; ``load_csv`` / ``analyze_dataframe`` /
      ``process_alert_batch`` are never reached.

    Counters are captured as *diffs* across each phase, so numbers are
    per-phase totals (not polluted by earlier runs in the same process).
    Alert-history read/write counters patch ``load_history`` /
    ``save_history`` in both the ``src.alerts.engine`` and
    ``src.alerts.history`` namespaces (the engine resolves the batch
    write, ``update_state`` resolves the history-namespace write — the
    same double-patch used by ``bench_scan_alert_batch``).

    Args:
        data_dir: Directory containing the OHLCV CSVs.
        symbols: Symbol count (informational; real-corpus runs pass the
            actual selected count).
        rows: Rows per symbol (synthetic only).
        populate: Write synthetic CSVs first.  ``False`` for real-corpus
            runs so no synthetic files are mixed into the corpus.
        workers: Worker count override (``None`` = production
            ``SCANNER_WORKERS``).
        cold_reps: Cold-cache repetitions.
        warm_reps: Warm-cache repetitions.
        corpus: Label recorded in the result (``"synthetic"`` /
            ``"real"``) — the benchmark never mixes corpora.

    Returns:
        A dict with ``cold`` and ``warm`` phases, each carrying latency
        percentiles (p50/p95/p99/best/avg), per-symbol cost, CSV reads,
        scanner + indicator cache counters and hit rates, alert
        read/write counts, and failure counts.
    """
    from src.alerts import engine as alert_engine  # noqa: PLC0415
    from src.alerts import history as alert_history  # noqa: PLC0415
    from src.cache.scanner_cache import scanner_cache  # noqa: PLC0415
    from src.indicators.cache import indicator_cache  # noqa: PLC0415
    from src.loaders import csv_loader as csv_loader_mod  # noqa: PLC0415
    from src.scanner import engine as scanner_engine  # noqa: PLC0415

    from src.config import SCANNER_WORKERS  # noqa: PLC0415

    effective = max(1, SCANNER_WORKERS if workers is None else workers)

    if populate:
        write_csvs(data_dir, symbols, rows)

    # ── counters ──────────────────────────────────────────────────
    counts = {"csv_reads": 0, "alert_reads": 0, "alert_writes": 0}
    orig_read_robust = csv_loader_mod._read_csv_robust
    orig_load = alert_history.load_history
    orig_save = alert_history.save_history
    orig_engine_load = alert_engine.load_history
    orig_engine_save = alert_engine.save_history

    def counting_read_robust(path):
        counts["csv_reads"] += 1
        return orig_read_robust(path)

    def counting_load():
        counts["alert_reads"] += 1
        return orig_load()

    def counting_save(history):
        counts["alert_writes"] += 1
        return orig_save(history)

    def _apply_counters():
        csv_loader_mod._read_csv_robust = counting_read_robust
        alert_history.load_history = counting_load
        alert_history.save_history = counting_save
        alert_engine.load_history = counting_load
        alert_engine.save_history = counting_save

    def _restore_counters():
        csv_loader_mod._read_csv_robust = orig_read_robust
        alert_history.load_history = orig_load
        alert_history.save_history = orig_save
        alert_engine.load_history = orig_engine_load
        alert_engine.save_history = orig_engine_save

    def _scan_stats() -> dict[str, int]:
        s = scanner_cache.stats()
        return {
            "df_hits": s["dataframe_hits"],
            "df_misses": s["dataframe_misses"],
            "analysis_hits": s["analysis_hits"],
            "analysis_misses": s["analysis_misses"],
        }

    def _ind_stats() -> dict[str, int]:
        s = indicator_cache.stats()
        return {"hits": s["hits"], "misses": s["misses"]}

    old_dir = scanner_engine.DATA_DIRECTORY
    scanner_engine.DATA_DIRECTORY = str(data_dir)
    try:
        with tempfile.TemporaryDirectory(prefix="nepse_warm_iso_") as iso_tmp:
            with _state_files(Path(iso_tmp)):
                _apply_counters()
                try:
                    # ── cold phase ──────────────────────────────────
                    # Cache ``clear()`` drops *entries* but never the
                    # hit/miss counters, so capturing the cache stats
                    # once before the loop and once after yields true
                    # per-phase totals across all repetitions (the
                    # sprint asks for per-phase CSV/cache/alert counts).
                    counts["csv_reads"] = 0
                    counts["alert_reads"] = 0
                    counts["alert_writes"] = 0
                    sc_before = _scan_stats()
                    ind_before = _ind_stats()
                    cold_lat: list[float] = []
                    for _ in range(cold_reps):
                        scanner_cache.clear()
                        indicator_cache.clear()
                        start = time.perf_counter()
                        scanner_engine.scan_market(workers=effective)
                        cold_lat.append((time.perf_counter() - start) * 1000.0)
                    sc_after = _scan_stats()
                    ind_after = _ind_stats()
                    cold = _phase_summary(
                        cold_lat,
                        symbols,
                        counts,
                        _diff(sc_before, sc_after),
                        _diff(ind_before, ind_after),
                    )

                    # ── warm phase ──────────────────────────────────
                    counts["csv_reads"] = 0
                    counts["alert_reads"] = 0
                    counts["alert_writes"] = 0
                    sc_before = _scan_stats()
                    ind_before = _ind_stats()
                    warm_lat: list[float] = []
                    for _ in range(warm_reps):
                        start = time.perf_counter()
                        scanner_engine.scan_market(workers=effective)
                        warm_lat.append((time.perf_counter() - start) * 1000.0)
                    sc_after = _scan_stats()
                    ind_after = _ind_stats()
                    warm = _phase_summary(
                        warm_lat,
                        symbols,
                        counts,
                        _diff(sc_before, sc_after),
                        _diff(ind_before, ind_after),
                    )

                    # ── result accounting (inside _state_files) ─────
                    out = scanner_engine.scan_market(workers=effective)
                    failures = len(out.get("skipped", []))
                finally:
                    _restore_counters()

        cold_p50 = cold["latency"]["p50_ms"]
        warm_p50 = warm["latency"]["p50_ms"]
        return {
            "symbols": symbols,
            "rows": rows,
            "corpus": corpus,
            "workers": effective,
            "mode": "sequential" if effective == 1 else f"parallel-{effective}",
            "cold": cold,
            "warm": warm,
            "speedup_x": round(cold_p50 / warm_p50, 2) if warm_p50 else 0.0,
            "failures": failures,
        }
    finally:
        scanner_engine.DATA_DIRECTORY = old_dir
        scanner_cache.clear()


def _diff(before: dict[str, int], after: dict[str, int]) -> dict[str, int]:
    """Per-phase counter diff (after - before), floored at zero."""
    return {k: max(0, after[k] - before[k]) for k in before}


def _phase_summary(
    latencies_ms: list[float],
    symbols: int,
    counts: dict[str, int],
    sc: dict[str, int],
    ind: dict[str, int],
) -> dict[str, Any]:
    """Assemble one phase's result dict from latencies + counters."""
    lat = _percentiles(latencies_ms)
    total_scanner = sum(sc.values())
    scanner_hit_rate = (
        ((sc["df_hits"] + sc["analysis_hits"]) / total_scanner) * 100.0
        if total_scanner
        else 0.0
    )
    total_ind = ind["hits"] + ind["misses"]
    p50 = lat["p50_ms"]
    return {
        "latency": lat,
        "per_symbol_ms": round(p50 / symbols, 3) if symbols else 0.0,
        "csv_reads": counts["csv_reads"],
        "scanner": {
            **sc,
            "hit_rate_pct": round(scanner_hit_rate, 2),
        },
        # ``consulted`` distinguishes "0% hit rate" (misses) from "never
        # consulted": on a warm scan ``analyze_dataframe`` is never
        # reached (the scanner analysis cache answers first), so the
        # indicator cache counters do not move at all.
        "indicator": {
            **ind,
            "consulted": total_ind > 0,
            "hit_rate_pct": round((ind["hits"] / total_ind * 100.0), 2) if total_ind else None,
        },
        "alert_reads": counts["alert_reads"],
        "alert_writes": counts["alert_writes"],
    }


# ───────────────────────────────────────────────────────────────────
# Orchestration (synthetic + real corpora)
# ───────────────────────────────────────────────────────────────────


def _select_usable_real_corpus(
    n: int,
    raw_dir: Path | None = None,
    seed: int = 42,
) -> tuple[Any, set[str]]:
    """Deterministically select *n* symbols from the audit's usable set.

    Uses the Sprint 12.2 corpus audit as the authority for what is
    usable (schema + rows + OHLC + duplicates).  Symbols flagged by the
    audit (e.g. the seven too-few-rows files) are excluded from the
    selection and returned so the benchmark can record the exclusion.

    Returns:
        ``(CorpusSpec, excluded)`` — the seeded selection plus the set
        of audited-but-excluded lowercase stems.
    """
    import random

    from benchmarks.corpus import CorpusSpec, DEFAULT_RAW_DIR  # noqa: PLC0415
    from benchmarks.corpus_audit import audit_corpus  # noqa: PLC0415

    from benchmarks.corpus import EXCLUDED  # noqa: PLC0415

    raw = Path(raw_dir) if raw_dir is not None else DEFAULT_RAW_DIR
    report = audit_corpus(raw)
    audited = {f["symbol"] for f in report["files"]}
    # The audit's ``ok`` flag is *not* disqualifying for warning-level
    # problems (non-numeric fields, OHLC sanity, date order): those files
    # still load and analyse.  ``sample.csv`` is the one real file the
    # audit reports non-numeric but the *scanner* itself always excludes
    # (``scan_market`` filters ``stem.lower() != "sample"``), so it must
    # be dropped from the selection here too — otherwise a real-corpus
    # warm run reports 280 symbols but only scans 279 (and its warm hit
    # count never reaches 2n per n symbols).
    usable = sorted(
        f["symbol"]
        for f in report["files"]
        if f["ok"] and f["symbol"] not in EXCLUDED
    )
    excluded = audited - set(usable)

    rng = random.Random(seed)
    selected = rng.sample(usable, min(n, len(usable)))
    return CorpusSpec(symbols=sorted(selected), raw_dir=raw, seed=seed), excluded


def run_warm_benchmark(
    sizes: tuple[int, ...] = DEFAULT_SIZES,
    rows: int = DEFAULT_ROWS,
    out_dir: Path = RESULTS_DIR,
    real_sizes: tuple[int, ...] = (50,),
    cold_reps: int = 3,
    warm_reps: int = 5,
    workers: int | None = None,
) -> dict[str, Any]:
    """Run the cold-vs-warm benchmark over synthetic + real corpora.

    Synthetic runs write a seeded corpus into private temp dirs.
    Real-corpus runs copy a seeded ``corpus.select_corpus`` subset of
    the shipped ``data/raw`` CSVs into a private temp dir (read-only
    w.r.t. the source) — sizes clamp to the number of available real
    files.  Results are labelled ``corpus`` = ``"synthetic"`` /
    ``"real"`` and never mixed.
    """
    import platform

    env = {
        "python": sys.version.split()[0],
        "platform": platform.platform(),
        "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    runs: list[dict[str, Any]] = []

    for n in sizes:
        with tempfile.TemporaryDirectory(prefix=f"nepse_warm_syn_{n}_") as tmp:
            data_dir = Path(tmp) / "data"
            write_csvs(data_dir, n, rows)
            result = bench_warm_scan(
                data_dir,
                n,
                rows,
                populate=False,
                workers=workers,
                cold_reps=cold_reps,
                warm_reps=warm_reps,
                corpus="synthetic",
            )
            runs.append(result)

    if real_sizes:
        from benchmarks.corpus import copy_corpus  # noqa: PLC0415

        # Real-corpus runs must use the *usable* corpus (Sprint 12.3
        # Phase 9): select deterministically from the audit's usable set
        # and record the excluded (insufficient-history / malformed)
        # symbols so the exclusion is documented, never silent.  Uses the
        # existing Sprint 12.2 corpus audit as the authority.
        with tempfile.TemporaryDirectory(prefix="nepse_warm_real_") as tmp:
            for n in real_sizes:
                spec, excluded = _select_usable_real_corpus(n)
                if not spec.symbols:
                    continue
                # One private data dir per size: copying into a *shared*
                # dir would leave the previous size's files behind and
                # silently scan more symbols than ``n`` (Sprint 12.3
                # review fix).
                data_dir = Path(tmp) / f"data_{n}"
                copy_corpus(spec, data_dir)
                # The actual scanned count is the number of files in the
                # private dir (drives per-symbol math, not the requested
                # ``n``).
                scanned = len(list(data_dir.glob("*.csv")))
                result = bench_warm_scan(
                    data_dir,
                    scanned,
                    0,
                    populate=False,
                    workers=workers,
                    cold_reps=cold_reps,
                    warm_reps=warm_reps,
                    corpus="real",
                )
                result["seed"] = spec.seed
                result["requested"] = n
                result["excluded_symbols"] = sorted(excluded)
                runs.append(result)

    summary = {
        "environment": env,
        "runs": runs,
        "copies_audit": audit_dataframe_copies(),
    }
    (out_dir / "warm_summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    return summary


def format_warm_report(summary: dict[str, Any]) -> str:
    """Render the warm-benchmark *summary* dict as a human-readable table."""
    lines = [
        "== Warm-path benchmark (Sprint 12.3) ==",
        f"python {summary['environment']['python']} — {summary['environment']['platform']}",
        "",
        f"{'corpus':<10} {'n':>4} {'mode':<10} {'cold p50 ms':>11} {'warm p50 ms':>11} "
        f"{'warm p95':>9} {'warm p99':>9} {'/sym warm':>9} {'csvR c/w':>9} {'hits c/w':>9} "
        f"{'aR c/w':>7} {'speedup':>8}",
        "-" * 122,
    ]
    for run in summary["runs"]:
        c, w = run["cold"], run["warm"]
        lines.append(
            f"{run['corpus']:<10} {run['symbols']:>4} {run['mode']:<10} "
            f"{c['latency']['p50_ms']:>11.1f} {w['latency']['p50_ms']:>11.1f} "
            f"{w['latency']['p95_ms']:>9.1f} {w['latency']['p99_ms']:>9.1f} "
            f"{w['per_symbol_ms']:>9.3f} "
            f"{c['csv_reads']}/{w['csv_reads']:>4} "
            f"{int(c['scanner']['df_hits'] + c['scanner']['analysis_hits'])}/"
            f"{int(w['scanner']['df_hits'] + w['scanner']['analysis_hits']):<4} "
            f"{c['alert_reads']}/{w['alert_reads']:>4} "
            f"{run['speedup_x']:>7.1f}x"
        )
    lines.append("")
    lines.append("copies audit (Phase 6):")
    for rel, info in summary["copies_audit"]["files"].items():
        lines.append(f"  {rel}: {info['count']} .copy() site(s)")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="NEPSE warm-path benchmark (Sprint 12.3)")
    parser.add_argument(
        "--sizes", type=int, nargs="+", default=list(DEFAULT_SIZES),
        help="Synthetic corpus sizes (default 50 200 500)",
    )
    parser.add_argument("--rows", type=int, default=DEFAULT_ROWS, help="Rows per symbol")
    parser.add_argument(
        "--real-sizes", type=int, nargs="+", default=(50,),
        help="Real-corpus sizes (clamped to available files; empty = skip)",
    )
    parser.add_argument("--out", type=str, default=str(RESULTS_DIR), help="Output directory")
    parser.add_argument("--cold-reps", type=int, default=3, help="Cold repetitions per size")
    parser.add_argument("--warm-reps", type=int, default=5, help="Warm repetitions per size")
    parser.add_argument(
        "--workers", type=int, default=None,
        help="Scanner workers (default: production SCANNER_WORKERS)",
    )
    args = parser.parse_args()

    summary = run_warm_benchmark(
        sizes=tuple(args.sizes),
        rows=args.rows,
        out_dir=Path(args.out),
        real_sizes=tuple(args.real_sizes) if args.real_sizes else (),
        cold_reps=args.cold_reps,
        warm_reps=args.warm_reps,
        workers=args.workers,
    )
    print(format_warm_report(summary))
    print(f"\nWarm benchmark complete — summary: {Path(args.out) / 'warm_summary.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
