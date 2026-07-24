from pathlib import Path

from src.config import DATA_DIRECTORY
from src.engine.analyzer import analyze_stock
from src.logging.logger import logger
from src.scanner.ranking import rank_market


def get_stock_files():
    """
    Return every CSV file inside DATA_DIRECTORY.
    """
    data_path = Path(DATA_DIRECTORY)
    return sorted(data_path.glob("*.csv"))


def scan_market():

    files = get_stock_files()

    results = []
    skipped = []

    for file in files:

        if file.stem.lower() == "sample":
            continue

        try:
            analysis = analyze_stock(str(file))

            analysis["symbol"] = file.stem.upper()

            results.append(analysis)

        except Exception as e:
            skipped.append(
                {
                    "symbol": file.stem.upper(),
                    "error": str(e),
                }
            )

            logger.exception("Error analyzing %s", file.stem.upper())

    ranked_results = rank_market(results)

    return {
        "results": ranked_results,
        "skipped": skipped,
    }