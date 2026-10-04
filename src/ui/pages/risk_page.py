"""Risk Analysis page — VaR, CVaR, beta, volatility, correlation."""

from __future__ import annotations

from typing import Any

from src.ui.helpers import (
    display_kpi_row,
    display_data_table,
    render_plotly_chart,
    display_error_message,
    display_info_message,
    format_percent,
    format_number,
    format_currency,
)
from src.ui.state import get_state
from src.logging.logger import logger


def render_risk_page() -> None:
    """Render the risk analysis page."""
    import streamlit as st

    st.header("⚠️ Risk Analysis")
    st.markdown("Portfolio risk metrics — VaR, CVaR, beta, volatility.")

    returns = get_state("returns", [])
    equity_curve = get_state("equity_curve", [])
    trades = get_state("trades", [])

    if not returns:
        display_info_message(
            "No return data available. Add trades or equity curve data "
            "to see risk metrics."
        )

        # Allow manual input
        st.subheader("Manual Risk Calculation")
        var_conf = st.slider("VaR Confidence Level", 0.90, 0.99, 0.95, 0.01)
        if st.button("Calculate Risk Metrics"):
            try:
                from src.risk.engine import RiskEngine

                engine = RiskEngine(
                    capital=get_state("capital", 1_000_000.0),
                    risk_per_trade_pct=0.02,
                )
                display_info_message("Risk metrics require returns data.")
            except Exception as exc:
                display_error_message(str(exc))
        return

    # Compute risk metrics
    risk_metrics: dict[str, Any] = {}
    try:
        from src.dashboard.dashboard import Dashboard

        dashboard = Dashboard()

        risk_metrics["var_95"] = dashboard.value_at_risk(returns, 0.95)
        risk_metrics["cvar_95"] = dashboard.conditional_var(returns, 0.95)
        risk_metrics["var_99"] = dashboard.value_at_risk(returns, 0.99)
        risk_metrics["cvar_99"] = dashboard.conditional_var(returns, 0.99)

        if equity_curve:
            risk_metrics["max_drawdown"] = dashboard.max_drawdown(equity_curve)

        if len(returns) >= 2:
            import statistics

            risk_metrics["volatility"] = statistics.stdev(returns)
    except Exception as exc:
        logger.exception("Risk calculation failed: %s", exc)
        display_error_message(f"Risk calculation error: {exc}")

    # ── KPI Cards ──
    kpis: list[dict[str, Any]] = [
        {
            "label": "Value at Risk (95%)",
            "value": format_percent(risk_metrics.get("var_95", 0.0)),
        },
        {
            "label": "Expected Shortfall (95%)",
            "value": format_percent(risk_metrics.get("cvar_95", 0.0)),
        },
        {
            "label": "Value at Risk (99%)",
            "value": format_percent(risk_metrics.get("var_99", 0.0)),
        },
        {
            "label": "Expected Shortfall (99%)",
            "value": format_percent(risk_metrics.get("cvar_99", 0.0)),
        },
        {
            "label": "Volatility",
            "value": format_percent(risk_metrics.get("volatility", 0.0)),
        },
        {
            "label": "Max Drawdown",
            "value": format_percent(abs(risk_metrics.get("max_drawdown", 0.0))),
        },
    ]
    display_kpi_row(kpis, columns=3)

    # ── Charts ──
    col1, col2 = st.columns(2)
    with col1:
        try:
            from src.dashboard.dashboard import Dashboard

            fig = Dashboard.trade_distribution(returns)
            render_plotly_chart(fig, title="Return Distribution")
        except Exception:
            display_info_message("Return distribution chart unavailable.")

    with col2:
        if equity_curve:
            try:
                dates = [str(i) for i in range(len(equity_curve))]
                dds = Dashboard.drawdown_curve(equity_curve)
                dd_dates = dates[: len(dds)]
                fig = Dashboard.drawdown_chart(dd_dates, equity_curve[: len(dds)])
                render_plotly_chart(fig, title="Drawdown")
            except Exception:
                display_info_message("Drawdown chart unavailable.")

    # ── Risk Metrics Table ──
    if risk_metrics:
        st.subheader("Risk Metrics Summary")
        table = [
            {"Metric": k.replace("_", " ").title(), "Value": format_percent(v) if abs(v) < 1 else format_number(v, 4)}
            for k, v in risk_metrics.items()
        ]
        display_data_table(table)

    logger.info("Risk Analysis page rendered.")
