from src.engine.analyzer import analyze_stock
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

    for symbol, meta in watchlist.items():
        if not meta.get("enabled", True):
            continue

        csv_path = resolve_stock_csv_path(symbol)

        if csv_path is None:
            results.append({
                "symbol": symbol.upper(),
                "error": "CSV file not found"
            })
            continue

        try:
            result = analyze_stock(str(csv_path))
            result["symbol"] = symbol.upper()

            results.append(result)

            # collect only NEW alerts
            if result.get("new_alerts"):
                alerts.append({
                    "symbol": result["symbol"],
                    "alerts": result["new_alerts"]
                })

        except Exception as e:
            logger.exception("Error scanning watchlist symbol %s", symbol)
            results.append({
                "symbol": symbol.upper(),
                "error": str(e)
            })

    return {
        "stocks_scanned": len(results),
        "alerts": alerts,
        "results": results
    }