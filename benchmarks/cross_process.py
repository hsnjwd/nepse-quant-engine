"""Sprint 11.5 Phase 3 — cross-process indicator-cache duplication benchmark.

The Docker API deployment (``Dockerfile`` ``api`` stage) runs
``uvicorn src.api.main:app --workers=2``, so two independent API
processes can analyse the same symbols, each with its own
process-local ``IndicatorCache``.  This benchmark spawns two such
"workers" against the real scraped NEPSE corpus and measures:

- how much indicator computation each worker performs (cache misses),
- the one-time cold cost per worker (CSV parse + full indicator chain),
- the warm repeated-analysis cost *within* a worker (local LRU hit),
- the upper bound a shared (L1 -> L2) cache could save.

Usage::

    python -m benchmarks.cross_process --symbols 50 [--seed 42]

Read-only: the real corpus is copied into a private temp directory and
never modified.  No production state files are touched.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from benchmarks import corpus as corpus_mod  # noqa: E402

_CHILD_CODE = r"""
import json
import sys
import time
from pathlib import Path

from src.engine.analyzer import analyze_dataframe
from src.indicators.cache import indicator_cache
from src.loaders.csv_loader import load_csv

data_dir = Path(sys.argv[1])
files = sorted(data_dir.glob("*.csv"))
indicator_cache.clear()

# State-file isolation (suite hygiene): the analysis chain only
# persists via the alert/portfolio stores, so redirect those module
# constants to the temp root before running.  Guards against any
# future persistence added to analyze_dataframe as well.
state_root = Path(sys.argv[2])
(state_root / "alerts").mkdir(parents=True, exist_ok=True)
from src.alerts import history as _alerts_history  # noqa: E402
from src.portfolio import holdings as _portfolio_holdings  # noqa: E402

_alerts_history.HISTORY_FILE = state_root / "alerts" / "history.json"
_portfolio_holdings.PORTFOLIO_FILE = state_root / "portfolio.json"

# Pass 1 — cold: every symbol is a fresh miss (parse + full indicator
# chain).  This is what a worker pays the first time it sees a symbol.
t0 = time.perf_counter()
analyze_cold: list[float] = []
for p in files:
    df = load_csv(p)
    s = time.perf_counter()
    analyze_dataframe(df, symbol=p.stem)
    analyze_cold.append((time.perf_counter() - s) * 1000.0)
full_cold_ms = (time.perf_counter() - t0) * 1000.0

# Pass 2 — warm: the process-local LRU now serves every symbol.
t0 = time.perf_counter()
analyze_warm: list[float] = []
for p in files:
    df = load_csv(p)
    s = time.perf_counter()
    analyze_dataframe(df, symbol=p.stem)
    analyze_warm.append((time.perf_counter() - s) * 1000.0)
full_warm_ms = (time.perf_counter() - t0) * 1000.0

stats = indicator_cache.stats()
print(
    json.dumps(
        {
            "symbols": len(files),
            "full_cold_ms": round(full_cold_ms, 2),
            "full_warm_ms": round(full_warm_ms, 2),
            "analyze_cold_avg_ms": round(sum(analyze_cold) / len(analyze_cold), 3),
            "analyze_warm_avg_ms": round(sum(analyze_warm) / len(analyze_warm), 3),
            "misses": stats["misses"],
            "hits": stats["hits"],
        }
    )
)
"""


def _run_worker(data_dir: Path, state_root: Path, label: str) -> dict:
    """Spawn one fresh process and return its JSON measurement dict.

    Args:
        data_dir: Temp directory holding the real-corpus CSVs.
        state_root: Temp directory the child redirects its state files
            (alert history / portfolio) into, keeping it off
            production state.
        label: Human-readable worker label for the report.
    """
    proc = subprocess.run(
        [sys.executable, "-c", _CHILD_CODE, str(data_dir), str(state_root)],
        cwd=str(PROJECT_ROOT),
        capture_output=True,
        text=True,
        timeout=1800,
        check=True,
    )
    out = proc.stdout.strip().splitlines()[-1]
    result = json.loads(out)
    print(f"  {label}: cold {result['full_cold_ms']} ms  "
          f"warm {result['full_warm_ms']} ms  "
          f"analyze cold/warm {result['analyze_cold_avg_ms']} / "
          f"{result['analyze_warm_avg_ms']} ms  "
          f"misses {result['misses']}  hits {result['hits']}")
    return result


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Cross-process indicator-cache duplication (Sprint 11.5)"
    )
    parser.add_argument("--symbols", type=int, default=50)
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()

    with tempfile.TemporaryDirectory(prefix="nepse_xproc_") as tmp:
        data_dir = Path(tmp) / "data"
        state_root = Path(tmp) / "state"
        spec = corpus_mod.select_corpus(args.symbols, seed=args.seed)
        corpus_mod.copy_corpus(spec, data_dir)
        n = len(spec.symbols)
        print(f"\nCross-process indicator-cache duplication "
              f"({n} real symbols, {args.symbols} requested, seed {args.seed})\n")

        a = _run_worker(data_dir, state_root, "Worker A (process 1)")
        b = _run_worker(data_dir, state_root, "Worker B (process 2)")

        # Worker B sees misses == symbols: it recalculates the entire
        # indicator pipeline — the local LRU of A is invisible to it.
        duplicated_analyze = n * (a["analyze_cold_avg_ms"] - a["analyze_warm_avg_ms"])
        per_worker_cold = a["full_cold_ms"]
        shared_saving = n * (b["analyze_cold_avg_ms"] - b["analyze_warm_avg_ms"])
        print(
            f"\n  Worker B recalculates the full pipeline: misses={b['misses']} "
            f"(== {n} symbols) confirms no cross-process reuse.\n"
        )
        print(f"  One-time cold cost per worker        : {per_worker_cold:>9.1f} ms")
        print(f"  Duplicated indicator compute (worker B): {duplicated_analyze:>9.1f} ms")
        print(
            f"  Upper bound a shared cache could save  : "
            f"{shared_saving:>9.1f} ms (one-time, per extra worker)\n"
        )
        print("  (Within one worker the local LRU already serves repeats: "
              f"analyze {a['analyze_cold_avg_ms']} ms -> "
              f"{a['analyze_warm_avg_ms']} ms.)")
        print("  (full_warm_ms still includes the CSV re-parse; the local LRU "
              "benefit is the analyze warm-vs-cold delta.)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
