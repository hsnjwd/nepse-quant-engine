"""Console report generators for the NEPSE Quant Engine Dashboard.

Generates human-readable, terminal-friendly reports from portfolio,
backtest, and strategy data using pretty-printed tables and structured
text output.
"""

from __future__ import annotations

from typing import Any


# ======================================================================
# Table helpers
# ======================================================================


def _separator(widths: list[int], char: str = "=") -> str:
    """Build a separator line from column widths.

    Args:
        widths: List of column character widths.
        char: Separator character (default ``"="``).

    Returns:
        Separator string, e.g. ``"======================"``.
    """
    return char * (sum(widths) + len(widths) * 3 + 1)


def _format_row(
    values: list[str],
    widths: list[int],
) -> str:
    """Format a single table row with aligned columns.

    Args:
        values: Cell values for this row.
        widths: Column widths for alignment.

    Returns:
        Formatted row string, e.g. ``"|  Name   |  Value  |"``.
    """
    parts: list[str] = []
    for val, w in zip(values, widths):
        parts.append(f" {val:<{w}} ")
    return "|" + "|".join(parts) + "|"


def _table(
    headers: list[str],
    rows: list[list[str]],
    title: str | None = None,
) -> str:
    """Build a pretty-printed table.

    Args:
        headers: Column header strings.
        rows: List of row cell-value lists.
        title: Optional title printed above the table.

    Returns:
        Formatted table string.
    """
    widths = [
        max(
            len(str(h)),
            max((len(str(r[i])) for r in rows), default=0),
        )
        for i, h in enumerate(headers)
    ]
    sep = _separator(widths, "=")
    thin_sep = _separator(widths, "-")

    lines: list[str] = []
    if title:
        lines.append(f"\n{title}")
        lines.append(_separator([len(title)], "="))

    lines.append(sep)
    lines.append(_format_row(headers, widths))
    lines.append(sep)
    for row in rows:
        lines.append(_format_row(row, widths))
        lines.append(thin_sep)
    lines.append(sep)
    return "\n".join(lines)


# ======================================================================
# Report generators
# ======================================================================


def portfolio_report(
    summary: dict[str, Any],
    title: str = "Portfolio Summary",
) -> str:
    """Generate a terminal-friendly portfolio summary report.

    Args:
        summary: Portfolio summary dict (e.g. from
            :func:`src.dashboard.metrics.portfolio_summary`).
        title: Report title.

    Returns:
        Pretty-printed portfolio report string.
    """
    rows: list[list[str]] = [
        ["Total Capital", f"NPR {summary.get('total_capital', 0):,.2f}"],
        ["Invested", f"NPR {summary.get('invested', 0):,.2f}"],
        ["Cash", f"NPR {summary.get('cash', 0):,.2f}"],
        ["Current Value", f"NPR {summary.get('current_value', 0):,.2f}"],
        ["Return", f"{summary.get('return_pct', 0):+.2f}%"],
        ["Total P/L", f"NPR {summary.get('total_pnl', 0):+,.2f}"],
        ["Win Rate", f"{summary.get('win_rate', 0):.1f}%"],
        ["Num Trades", str(summary.get("num_trades", 0))],
    ]

    sr = summary.get("sharpe_ratio")
    rows.append(["Sharpe Ratio", f"{sr:.4f}" if sr is not None else "N/A"])

    sor = summary.get("sortino_ratio")
    rows.append(["Sortino Ratio", f"{sor:.4f}" if sor is not None else "N/A"])

    rows.append(["Max Drawdown", f"{summary.get('max_drawdown', 0):.2f}%"])

    pf = summary.get("profit_factor", 0)
    rows.append(["Profit Factor", f"{pf:.4f}" if pf != float("inf") else "Inf"])

    car = summary.get("calmar_ratio")
    rows.append(["Calmar Ratio", f"{car:.4f}" if car is not None else "N/A"])

    rf = summary.get("recovery_factor")
    rows.append(["Recovery Factor", f"{rf:.4f}" if rf is not None else "N/A"])

    rows.append(["VaR (95%)", f"{summary.get('value_at_risk_95', 0):.4f}"])
    rows.append(["CVaR (95%)", f"{summary.get('conditional_var_95', 0):.4f}"])
    rows.append(["Volatility", f"{summary.get('volatility', 0):.4f}"])

    return _table(["Metric", "Value"], rows, title)


def trade_report(
    trades: list[dict[str, Any]],
    title: str = "Trade Report",
) -> str:
    """Generate a terminal-friendly trade report.

    Args:
        trades: List of trade dicts with keys like ``symbol``,
            ``return_pct``, ``net_profit``, ``entry_date``,
            ``exit_date``.
        title: Report title.

    Returns:
        Pretty-printed trade report string.
    """
    if not trades:
        return _table(["Trades", "Value"], [["Count", "0"]], title)

    headers = ["#", "Symbol", "Return %", "Net Profit", "Entry", "Exit"]
    rows: list[list[str]] = []
    for i, t in enumerate(trades[:50], 1):
        ret = float(t.get("return_pct", t.get("net_profit", 0)))
        profit = float(t.get("net_profit", 0))
        rows.append([
            str(i),
            str(t.get("symbol", "?")),
            f"{ret:+.2f}%",
            f"NPR {profit:+,.2f}",
            str(t.get("entry_date", t.get("date", ""))),
            str(t.get("exit_date", "")),
        ])

    if len(trades) > 50:
        rows.append(["...", f"{len(trades) - 50} more"] + [""] * 4)

    return _table(headers, rows, title)


def strategy_report(
    strategies: dict[str, Any],
    title: str = "Strategy Comparison",
) -> str:
    """Generate a terminal-friendly strategy comparison report.

    Args:
        strategies: Dict mapping strategy name to its metrics dict.
        title: Report title.

    Returns:
        Pretty-printed strategy report string.
    """
    if not strategies:
        return _table(["Strategy", "Metric", "Value"], [], title)

    headers = ["Strategy", "Metric", "Value"]
    rows: list[list[str]] = []
    for strat_name, metrics in strategies.items():
        if isinstance(metrics, dict):
            for metric_key, metric_val in metrics.items():
                if isinstance(metric_val, float):
                    rows.append([
                        strat_name,
                        metric_key.replace("_", " ").title(),
                        f"{metric_val:.4f}",
                    ])
                else:
                    rows.append([
                        strat_name,
                        metric_key.replace("_", " ").title(),
                        str(metric_val),
                    ])
        else:
            rows.append([strat_name, "Value", str(metrics)])

    return _table(headers, rows, title)


def weights_report(
    weights: dict[str, float],
    title: str = "Strategy Weights",
) -> str:
    """Generate a terminal-friendly strategy weights report.

    Args:
        weights: Dict mapping strategy name to weight.
        title: Report title.

    Returns:
        Pretty-printed weights table string.
    """
    headers = ["Strategy", "Weight"]
    rows = [
        [name, f"{w:.4f}"] for name, w in sorted(
            weights.items(), key=lambda x: -x[1]
        )
    ]
    return _table(headers, rows, title)


def regime_report(
    regime_label: str,
    confidence: float,
    reasons: list[str] | None = None,
    metrics: dict[str, Any] | None = None,
    title: str = "Market Regime Report",
) -> str:
    """Generate a terminal-friendly market regime report.

    Args:
        regime_label: Detected regime label.
        confidence: Confidence percentage (0–100).
        reasons: Optional list of human-readable reasons.
        metrics: Optional dict of diagnostic metrics.

    Returns:
        Pretty-printed regime report string.
    """
    rows: list[list[str]] = [
        ["Regime", regime_label],
        ["Confidence", f"{confidence:.1f}%"],
    ]

    if metrics:
        for key, val in metrics.items():
            if isinstance(val, float):
                rows.append([key.replace("_", " ").title(), f"{val:.4f}"])
            else:
                rows.append([key.replace("_", " ").title(), str(val)])

    report = _table(["Metric", "Value"], rows, title)

    if reasons:
        report += "\n\nReasons:\n"
        for r in reasons:
            report += f"  • {r}\n"

    return report


def allocation_report(
    allocations: list[dict[str, Any]],
    title: str = "Portfolio Allocations",
) -> str:
    """Generate a terminal-friendly allocation report.

    Args:
        allocations: List of allocation dicts with ``symbol``,
            ``weight``, ``shares``, ``capital``, ``expected_return``,
            ``risk``, ``sector`` keys.
        title: Report title.

    Returns:
        Pretty-printed allocation table string.
    """
    headers = ["Symbol", "Weight", "Shares", "Capital", "Return", "Risk", "Sector"]
    rows: list[list[str]] = []
    for a in allocations:
        rows.append([
            str(a.get("symbol", "?")),
            f"{float(a.get('weight', 0)):.4f}",
            str(a.get("shares", 0)),
            f"NPR {float(a.get('capital', 0)):,.2f}",
            f"{float(a.get('expected_return', 0)):.4f}",
            f"{float(a.get('risk', 0)):.4f}",
            str(a.get("sector", "?")),
        ])
    return _table(headers, rows, title)


# ======================================================================
# Composite reports
# ======================================================================


def executive_report(
    portfolio_summary: dict[str, Any],
    regime_label: str = "UNKNOWN",
    regime_confidence: float = 0.0,
    trades: list[dict[str, Any]] | None = None,
    allocations: list[dict[str, Any]] | None = None,
) -> str:
    """Generate a comprehensive executive report.

    Combines portfolio summary, market regime, recent trades, and
    current allocations into a single terminal-friendly report.

    Args:
        portfolio_summary: Portfolio summary dict.
        regime_label: Detected market regime.
        regime_confidence: Regime confidence.
        trades: Optional list of recent trades.
        allocations: Optional list of current allocations.

    Returns:
        Complete executive report string.
    """
    lines: list[str] = [
        _separator([60], "="),
        "  NEPSE QUANT ENGINE — EXECUTIVE REPORT",
        _separator([60], "="),
        "",
    ]

    lines.append(portfolio_report(portfolio_summary, "Portfolio Summary"))
    lines.append("")
    lines.append(
        regime_report(regime_label, regime_confidence, title="Market Regime")
    )

    if trades:
        lines.append("")
        lines.append(trade_report(trades, "Recent Trades"))

    if allocations:
        lines.append("")
        lines.append(allocation_report(allocations, "Current Allocations"))

    lines.append("")
    lines.append(_separator([60], "="))
    lines.append("")

    return "\n".join(lines)


def detailed_report(
    portfolio_summary: dict[str, Any],
    regime_label: str = "UNKNOWN",
    regime_confidence: float = 0.0,
    regime_reasons: list[str] | None = None,
    regime_metrics: dict[str, Any] | None = None,
    trades: list[dict[str, Any]] | None = None,
    allocations: list[dict[str, Any]] | None = None,
    strategy_weights: dict[str, float] | None = None,
    risk_metrics: dict[str, Any] | None = None,
) -> str:
    """Generate a detailed report with all available data.

    Args:
        portfolio_summary: Portfolio summary dict.
        regime_label: Detected market regime.
        regime_confidence: Regime confidence.
        regime_reasons: Regime detection reasons.
        regime_metrics: Regime diagnostic metrics.
        trades: Optional list of recent trades.
        allocations: Optional list of current allocations.
        strategy_weights: Optional strategy weight dict.
        risk_metrics: Optional risk metrics dict.

    Returns:
        Complete detailed report string.
    """
    lines: list[str] = [
        _separator([60], "="),
        "  NEPSE QUANT ENGINE — DETAILED REPORT",
        _separator([60], "="),
        "",
    ]

    lines.append(portfolio_report(portfolio_summary, "Portfolio Summary"))
    lines.append("")

    lines.append(
        regime_report(
            regime_label,
            regime_confidence,
            regime_reasons,
            regime_metrics,
            "Market Regime",
        )
    )
    lines.append("")

    if strategy_weights:
        lines.append(weights_report(strategy_weights, "Adaptive Strategy Weights"))
        lines.append("")

    if risk_metrics:
        risk_rows = [
            [k.replace("_", " ").title(), f"{v:.4f}" if isinstance(v, float) else str(v)]
            for k, v in risk_metrics.items()
        ]
        lines.append(
            _table(["Metric", "Value"], risk_rows, "Risk Metrics")
        )
        lines.append("")

    if trades:
        lines.append(trade_report(trades, "Trades"))
        lines.append("")

    if allocations:
        lines.append(allocation_report(allocations, "Allocations"))

    lines.append("")
    lines.append(_separator([60], "="))
    lines.append("")

    return "\n".join(lines)
