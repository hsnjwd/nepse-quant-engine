"""Portfolio analysis utilities."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from src.config import DATA_DIRECTORY
from src.engine.analyzer import analyze_stock
from src.portfolio.advisor import build_advice
from src.portfolio.decisions import portfolio_decision
from src.portfolio.holdings import load_portfolio


def analyze_portfolio() -> dict[str, Any]:
    """Analyze every stock holding in the portfolio.

    Returns:
        A dictionary containing portfolio cost, value, PnL, return percentage,
        and individual holding analysis records.
    """
    holdings = load_portfolio()
    results: list[dict[str, Any]] = []

    total_value = 0.0
    total_cost = 0.0

    for holding in holdings:
        symbol = holding["symbol"].lower()
        file_path = Path(DATA_DIRECTORY) / f"{symbol}.csv"

        if not file_path.exists():
            continue

        analysis = analyze_stock(str(file_path))

        qty = holding["quantity"]
        avg = holding["average_price"]
        ltp = analysis.get("price") or 0.0

        cost = qty * avg
        value = qty * ltp

        pnl = value - cost
        pnl_pct = round((pnl / cost) * 100, 2) if cost else 0.0

        total_cost += cost
        total_value += value

        holding_result = {
            "symbol": holding["symbol"],
            "quantity": qty,
            "average_price": avg,
            "ltp": ltp,
            "investment": round(cost, 2),
            "current_value": round(value, 2),
            "pnl": round(pnl, 2),
            "pnl_pct": pnl_pct,
            "analysis": analysis,
            "decision": None,
        }

        holding_result["decision"] = portfolio_decision(holding_result)
        holding_result["advisor"] = build_advice(holding_result)
        results.append(holding_result)

    return {
        "portfolio_cost": round(total_cost, 2),
        "portfolio_value": round(total_value, 2),
        "portfolio_pnl": round(total_value - total_cost, 2),
        "portfolio_return_pct": (
            round(((total_value - total_cost) / total_cost) * 100, 2)
            if total_cost
            else 0.0
        ),
        "holdings": results,
    }