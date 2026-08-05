"""End-to-end market data pipeline verification for the NEPSE Quant Engine.

Traces the full data path:

    DATA_DIRECTORY (CSV files)
        -> src.loaders.csv_loader.load_csv      (canonical parser)
        -> src.data.providers.CSVProvider       (quotes / history)
        -> src.data.service.DataService         (HybridProvider + cache)
        -> scanner / dashboard consumers

Usage:
    python scripts/verify_data_pipeline.py

Exit code 0 when every stage passes, 1 otherwise.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.config import DATA_DIRECTORY  # noqa: E402
from src.data.providers import CSVProvider  # noqa: E402
from src.loaders.csv_loader import load_csv, resolve_stock_csv_path  # noqa: E402


RESULTS: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append((name, bool(ok), detail))
    status = "PASS" if ok else "FAIL"
    print(f"  [{status}] {name}" + (f"  ->  {detail}" if detail else ""))


def fmt_rows(df: pd.DataFrame) -> str:
    return f"{len(df)} rows x {len(df.columns)} cols"


def main() -> int:
    t0 = time.time()
    print("=" * 72)
    print("NEPSE Quant Engine — data pipeline verification")
    print("=" * 72)

    # ── Stage 1: directory resolution ────────────────────────────
    print("\n[1] DATA_DIRECTORY resolution")
    data_dir = Path(DATA_DIRECTORY)
    check("config.DATA_DIRECTORY resolves", data_dir.is_dir(),
          f"{DATA_DIRECTORY} exists={data_dir.is_dir()}")
    if not data_dir.is_dir():
        print("\n  ABORT: data directory missing. Point DATA_DIRECTORY at your CSVs.")
        return 1
    if data_dir.resolve() != Path("data/raw").resolve():
        print("  NOTE: DATA_DIRECTORY is overridden (likely via .env) "
              "— not the default data/raw.")

    csv_files = sorted(data_dir.glob("*.csv"))
    check("CSV files present in data dir", len(csv_files) > 0,
          f"{len(csv_files)} files in {data_dir}")
    if not csv_files:
        print("\n  ABORT: no CSVs found. Seed data/raw/*.csv from the scraper mirror.")
        return 1

    # ── Stage 2: canonical loader on real files ──────────────────
    print("\n[2] Canonical loader (src.loaders.csv_loader.load_csv)")
    sample_files = [f for f in csv_files if f.stem.lower() != "sample"][:5]
    for f in sample_files:
        try:
            df = load_csv(f)
            ok = not df.empty and "Close" in df.columns
            check(f"load_csv({f.name})", ok,
                  f"{fmt_rows(df)}  Close[0]={df['Close'].iloc[0] if not df.empty else 'n/a'}")
        except Exception as exc:
            check(f"load_csv({f.name})", False, f"{type(exc).__name__}: {exc}")

    # ── Stage 3: resolver ────────────────────────────────────────
    print("\n[3] Path resolver (resolve_stock_csv_path)")
    first_symbol = sample_files[0].stem if sample_files else "nabil"
    resolved = resolve_stock_csv_path(first_symbol, data_dir=data_dir)
    check(f"resolve_stock_csv_path({first_symbol})",
          resolved is not None and resolved.exists(), str(resolved))

    # ── Stage 4: CSVProvider quotes / history / movers ───────────
    print("\n[4] CSVProvider (src.data.providers.CSVProvider)")
    provider = CSVProvider(data_dir=data_dir)
    try:
        quotes = provider.get_live_quotes()
        check("get_live_quotes()", len(quotes) > 0, f"{len(quotes)} quotes")
        if quotes:
            q = quotes[0]
            check("quote fields populated", bool(q.symbol) and q.ltp > 0,
                  f"{q.symbol} ltp={q.ltp}")
    except Exception as exc:
        check("get_live_quotes()", False, f"{type(exc).__name__}: {exc}")
        quotes = []

    try:
        hist = provider.get_history(first_symbol)
        ok = hist is not None and not hist.empty
        check(f"get_history({first_symbol})", ok,
              fmt_rows(hist) if ok else "empty")
    except Exception as exc:
        check(f"get_history({first_symbol})", False, f"{type(exc).__name__}: {exc}")

    # Top movers — CSVProvider does not implement these today.
    for method in ("get_top_gainers", "get_top_losers", "get_top_turnover"):
        try:
            movers = getattr(provider, method)(5)
            check(f"{method}()", len(movers) > 0,
                  f"{len(movers)} movers" if movers else "empty (CSVProvider has no top-mover impl)")
        except Exception as exc:
            check(f"{method}()", False, f"{type(exc).__name__}: {exc}")

    # ── Stage 5: DataService end-to-end (CSV provider injected) ──
    print("\n[5] DataService (src.data.service.DataService)")
    try:
        from src.data import DataService
        DataService.reset_instance()
        # Inject CSVProvider directly so verification is deterministic
        # and does not wait on dead API hosts (15s timeouts).
        svc = DataService(provider=CSVProvider(data_dir=data_dir))

        summary = svc.get_market_summary()
        # NOTE: dashboard only renders the Market Snapshot when index > 0.
        check("get_market_summary() index>0", summary is not None and summary.index > 0,
              f"status={summary.status} index={summary.index} "
              "(CSVProvider returns index=0 — dashboard shows 'unavailable')")

        live = svc.get_live_market()
        check("get_live_market()", len(live) > 0, f"{len(live)} quotes")

        if live:
            sym = live[0].symbol
            h = svc.get_history(sym)
            check(f"get_history({sym}) via DataService",
                  h is not None and not h.is_empty,
                  fmt_rows(h.df) if h and not h.is_empty else "empty")

        scan = svc.scan_market()
        results = len(scan.results) if scan else 0
        skipped = len(scan.skipped) if scan else 0
        check("scan_market() produces results", results > 0,
              f"results={results} skipped={skipped}")
        if scan and scan.skipped:
            reasons: dict[str, int] = {}
            for s in scan.skipped:
                err = str(s.get("error", ""))[:80]
                reasons[err] = reasons.get(err, 0) + 1
            for err, count in sorted(reasons.items(), key=lambda kv: -kv[1])[:5]:
                print(f"      skipped x{count}: {err}")
    except Exception as exc:
        check("DataService flow", False, f"{type(exc).__name__}: {exc}")

    # ── Stage 6: dashboard module imports (render entrypoint) ────
    print("\n[6] Dashboard consumer imports")
    try:
        from src.ui.pages.dashboard_page import render  # noqa: F401
        check("dashboard_page imports cleanly", True)
    except Exception as exc:
        check("dashboard_page imports cleanly", False, f"{type(exc).__name__}: {exc}")

    # ── Summary ──────────────────────────────────────────────────
    passed = sum(1 for _, ok, _ in RESULTS if ok)
    failed = sum(1 for _, ok, _ in RESULTS if not ok)
    print("\n" + "=" * 72)
    print(f"RESULT: {passed} passed, {failed} failed "
          f"({time.time() - t0:.1f}s)")
    for name, ok, detail in RESULTS:
        if not ok:
            print(f"  FAILED: {name} {detail}")
    print("=" * 72)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
