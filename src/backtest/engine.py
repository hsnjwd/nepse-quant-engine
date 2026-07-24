from typing import Any

from src.loaders.csv_loader import load_csv
from src.engine.analyzer import analyze_dataframe
from src.backtest.trade_simulator import simulate_trade
from src.logging.logger import logger


def backtest(csv_file: str) -> dict[str, Any]:
    """Replay historical candles and simulate trades.

    Args:
        csv_file: Path or name of the CSV file containing historical market data.

    Returns:
        A dictionary summarizing the backtest run, including the symbol,
        candle count, total trades, and trade history.
    """

    df = load_csv(csv_file)

    trades = []

    # Need enough history for indicators
    start = 30

    for i in range(start, len(df) - 10):

        history = df.iloc[: i + 1].copy()

        signal = analyze_dataframe(history)

        logger.info(
            "%s | score=%s | signal=%s",
            history.iloc[-1]["Date"].date(),
            signal["score"],
            signal["signal"],
        )

        trade = simulate_trade(
            df,
            i,
            signal
        )

        if trade is not None:

            trade["date"] = str(
                df.iloc[i]["Date"].date()
            )

            trade["symbol"] = csv_file

            trades.append(trade)

    return {
        "symbol": csv_file,
        "candles": len(df),
        "total_trades": len(trades),
        "history": trades,
    }