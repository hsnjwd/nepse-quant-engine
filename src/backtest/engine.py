"""Orchestration layer for historical backtesting simulation."""

from __future__ import annotations

from typing import Any

from src.backtest.metrics import calculate_trade_statistics
from src.backtest.report import generate_report
from src.backtest.trade_simulator import BUY_SIGNAL, simulate_trade
from src.engine.analyzer import analyze_dataframe
from src.loaders.csv_loader import load_csv
from src.logging.logger import logger

DEFAULT_START_INDEX: int = 30
FORWARD_SIMULATION_BUFFER: int = 10


def run_backtest(
    csv_file: str,
    commission: float = 0.0,
    slippage: float = 0.0,
    start_index: int = DEFAULT_START_INDEX,
) -> dict[str, Any]:
    """Execute a backtest run over historical market data CSV.

    Orchestrates loading market data, replaying candles, generating signals,
    executing trades, calculating performance metrics, and building reports.

    Args:
        csv_file: Path or symbol name of the CSV file containing market data.
        commission: Per-side commission rate as a decimal fraction.
        slippage: Per-side adverse slippage rate as a decimal fraction.
        start_index: Index of initial candle to begin historical analysis.

    Returns:
        A dictionary containing:
            - trades: List of completed trade record dictionaries.
            - metrics: Performance statistics dictionary.
            - report: Summary report dictionary.
            - symbol: Path or symbol identifier of the data file.
            - candles: Total count of historical candles processed.
            - total_trades: Total count of executed trades.
            - history: Alias to trades for backward compatibility.
    """
    logger.info("Starting backtest run for %s", csv_file)

    df = load_csv(csv_file)
    logger.info("Loaded %d candles from %s", len(df), csv_file)

    trades: list[dict[str, Any]] = []

    # Iterate through historical candles while leaving space for forward simulation
    end_bound: int = max(len(df) - FORWARD_SIMULATION_BUFFER, start_index)
    for i in range(start_index, end_bound):
        history = df.iloc[: i + 1].copy()
        signal: dict[str, Any] = analyze_dataframe(history)

        date_val: Any = history.iloc[-1]["Date"].date() if "Date" in history.columns else i
        logger.debug(
            "%s | score=%s | signal=%s",
            date_val,
            signal.get("score"),
            signal.get("signal"),
        )

        # Execute BUY trades only
        if signal.get("signal") == BUY_SIGNAL:
            trade: dict[str, Any] | None = simulate_trade(
                df=df,
                start_index=i,
                signal=signal,
                commission=commission,
                slippage=slippage,
            )

            if trade is not None:
                if "Date" in df.columns:
                    trade["date"] = str(df.iloc[i]["Date"].date())
                trade["symbol"] = csv_file
                trades.append(trade)

    logger.info("Completed backtest for %s: %d trades executed", csv_file, len(trades))

    metrics: dict[str, float | int] = calculate_trade_statistics(trades)
    report: dict[str, Any] = generate_report(trades=trades, metrics=metrics, symbol=csv_file)
# TODO(v0.6): Remove "history" after all callers migrate to "trades".

    return {
        "symbol": csv_file,
        "candles": len(df),
        "total_trades": len(trades),
        "history": trades,
        "trades": trades,
        "metrics": metrics,
        "report": report,
    }

def backtest(csv_file: str) -> dict[str, Any]:
    """Legacy entry point for running a backtest on historical CSV data.

    Args:
        csv_file: Path or symbol name of the CSV file containing market data.

    Returns:
        A dictionary containing trades, metrics, and report outputs.
    """
    return run_backtest(csv_file)