from src.config import ENABLE_ANALYZE_ALERT_BATCH
from src.engine.analyzer import analyze_stock_batch
from src.loaders.csv_loader import resolve_stock_csv_path
from src.logging.logger import logger
from src.watchlist.manager import load_watchlist


def scan_watchlist():
    """
    Scan every watched stock.

    Returns:
    {
        "stocks_scanned": int,
        "alerts": [],
        "results": []
    }
    """

    results = []
    alerts = []
    watchlist = load_watchlist()

    if not watchlist:
        return {
            "stocks_scanned": 0,
            "alerts": [],
            "results": []
        }

    # Resolve every enabled symbol's CSV path first so the optional
    # batch-alert path (Sprint 11.9) can analyze them together.  When
    # ``ENABLE_ANALYZE_ALERT_BATCH`` is off (default), the batch helper
    # runs the exact legacy per-symbol ``process_alerts`` path, so the
    # watchlist scan is byte-identical to the pre-Sprint 11.9 behaviour
    # unless the operator opts in.  The final loop iterates the watchlist
    # in insertion order (enabled items only) and emits result/error
    # entries interleaved exactly like the legacy per-symbol loop, so
    # output ordering is preserved in both modes.
    enabled_items = [
        (symbol, meta)
        for symbol, meta in watchlist.items()
        if meta.get("enabled", True)
    ]
    missing: set[str] = set()
    pending: list[tuple[str, str]] = []  # (symbol, csv_path)
    for symbol, _meta in enabled_items:
        csv_path = resolve_stock_csv_path(symbol)
        if csv_path is None:
            missing.add(symbol.upper())
        else:
            pending.append((symbol.upper(), str(csv_path)))

    analyses = analyze_stock_batch(
        [path for _, path in pending],
        use_batch_alerts=ENABLE_ANALYZE_ALERT_BATCH,
    )
    analyses_by_symbol = {a["symbol"]: a for a in analyses}

    for symbol, _meta in enabled_items:
        upper = symbol.upper()
        if upper in missing:
            results.append({"symbol": upper, "error": "CSV file not found"})
            continue
        analysis = analyses_by_symbol.get(upper)
        if analysis is None or analysis.get("error"):
            results.append({
                "symbol": upper,
                "error": (analysis or {}).get("error", "Analysis failed"),
            })
            continue
        results.append(analysis)
        # collect only NEW alerts
        if analysis.get("new_alerts"):
            alerts.append({
                "symbol": upper,
                "alerts": analysis["new_alerts"]
            })

    return {
        "stocks_scanned": len(results),
        "alerts": alerts,
        "results": results
    }