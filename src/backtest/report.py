"""Backtest report generation utilities.

Converts raw trade history and calculated metrics into human-readable summaries.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any


def generate_report(
    trades: Sequence[Mapping[str, Any]],
    metrics: Mapping[str, Any],
    symbol: str = "",
) -> dict[str, Any]:
    """Generate a human-readable backtest summary report.

    Args:
        trades: Collection of completed trade execution records.
        metrics: Calculated performance metrics dictionary.
        symbol: Optional target financial symbol or data file path.

    Returns:
        A dictionary containing structured summary attributes and a formatted
        text report overview.
    """
    total_trades = int(metrics.get("total_trades", len(trades)))
    winning_trades = int(metrics.get("winning_trades", 0))
    losing_trades = int(metrics.get("losing_trades", 0))
    win_rate = float(metrics.get("win_rate", 0.0))
    profit_factor = float(metrics.get("profit_factor", 0.0))
    expectancy = float(metrics.get("expectancy", 0.0))

    summary_lines = [
        f"Backtest Report Summary for {symbol or 'Asset'}",
        "----------------------------------------",
        f"Total Trades    : {total_trades}",
        f"Winning Trades  : {winning_trades}",
        f"Losing Trades   : {losing_trades}",
        f"Win Rate        : {win_rate:.2f}%",
        f"Profit Factor   : {profit_factor:.2f}",
        f"Expectancy      : {expectancy:.2f}%",
    ]
    summary_text = "\n".join(summary_lines)

    return {
        "symbol": symbol,
        "summary": summary_text,
        "total_trades": total_trades,
        "winning_trades": winning_trades,
        "losing_trades": losing_trades,
        "win_rate": round(win_rate, 2),
        "profit_factor": round(profit_factor, 2),
        "expectancy": round(expectancy, 2),
    }
