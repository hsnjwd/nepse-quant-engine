"""Monte Carlo simulation page — run simulations, display path charts."""

from __future__ import annotations

from src.ui.helpers import (
    display_kpi_row,
    display_data_table,
    render_plotly_chart,
    display_error_message,
    display_info_message,
    display_success_message,
    format_currency,
    format_percent,
    format_number,
)
from src.ui.state import get_state, set_state
from src.logging.logger import logger


def render_monte_carlo_page() -> None:
    """Render the Monte Carlo simulation page."""
    import streamlit as st

    st.header("🎲 Monte Carlo Simulation")
    st.markdown("Simulate thousands of possible portfolio outcomes.")

    col1, col2 = st.columns(2)
    with col1:
        n_simulations = st.number_input(
            "Number of Simulations",
            min_value=100,
            max_value=10000,
            value=get_state("mc_simulations", 1000),
            step=100,
        )
        set_state("mc_simulations", n_simulations)
    with col2:
        initial_capital = st.number_input(
            "Initial Capital (NPR)",
            min_value=10_000.0,
            value=float(get_state("capital", 1_000_000.0)),
            step=100_000.0,
        )

    col3, col4 = st.columns(2)
    with col3:
        expected_return = st.number_input(
            "Expected Annual Return (%)",
            min_value=-50.0, max_value=100.0, value=12.0, step=1.0,
        ) / 100.0
    with col4:
        volatility = st.number_input(
            "Annual Volatility (%)",
            min_value=1.0, max_value=100.0, value=20.0, step=1.0,
        ) / 100.0

    if st.button("🎲 Run Simulation", type="primary", use_container_width=True):
        with st.spinner(f"Running {n_simulations} simulations..."):
            try:
                from src.simulation.monte_carlo import MonteCarloSimulator

                simulator = MonteCarloSimulator(
                    initial_capital=initial_capital,
                    expected_return=expected_return,
                    volatility=volatility,
                )

                result = simulator.run_simulation(
                    n_simulations=n_simulations,
                    n_days=252,
                )

                curves = result.get("curves", [])
                best = result.get("best_curve", [])
                worst = result.get("worst_curve", [])
                avg = result.get("average_curve", [])

                set_state("mc_result", result)

                display_success_message(
                    f"Simulation complete: {n_simulations} paths generated."
                )

                # KPI row
                final_values = [c[-1] for c in curves if c]
                if final_values:
                    median_final = sorted(final_values)[len(final_values) // 2]
                    worst_final = min(final_values)
                    best_final = max(final_values)

                    kpis = [
                        {"label": "Median Final Value", "value": format_currency(median_final)},
                        {"label": "Best Case", "value": format_currency(best_final)},
                        {"label": "Worst Case", "value": format_currency(worst_final)},
                        {"label": "Simulations", "value": str(n_simulations)},
                    ]
                    display_kpi_row(kpis, columns=4)

                # Charts
                col_a, col_b = st.columns(2)
                with col_a:
                    try:
                        from src.dashboard.dashboard import Dashboard

                        fig = Dashboard.monte_carlo_paths(
                            curves[:100],
                            best_curve=best,
                            worst_curve=worst,
                            average_curve=avg,
                        )
                        render_plotly_chart(fig, title="Simulated Paths")
                    except Exception:
                        display_info_message("Path chart unavailable.")

                with col_b:
                    try:
                        percentile_map = {"5%": 0.05, "25%": 0.25, "50%": 0.50, "75%": 0.75, "95%": 0.95}
                        fig = Dashboard.monte_carlo_confidence_bands(curves, percentile_map)
                        render_plotly_chart(fig, title="Confidence Bands")
                    except Exception:
                        display_info_message("Confidence band chart unavailable.")

                # Probability distribution of final values
                if final_values:
                    import plotly.graph_objects as go

                    fig_hist = go.Figure(
                        data=[go.Histogram(x=final_values, nbinsx=50)]
                    )
                    fig_hist.update_layout(
                        title="Distribution of Final Portfolio Values",
                        xaxis_title="Final Value",
                        yaxis_title="Frequency",
                        height=350,
                    )
                    render_plotly_chart(fig_hist, title="Final Value Distribution")

            except ImportError:
                display_error_message(
                    "Monte Carlo module not available. "
                    "Ensure the simulation module is installed."
                )
            except Exception as exc:
                logger.exception("Monte Carlo failed: %s", exc)
                display_error_message(f"Simulation error: {exc}")

    logger.info("Monte Carlo page rendered.")
