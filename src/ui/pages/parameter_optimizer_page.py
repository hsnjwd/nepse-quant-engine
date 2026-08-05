"""Parameter Optimiser page — heatmaps, ranking, best parameters."""

from __future__ import annotations

from src.ui.helpers import (
    display_data_table,
    render_plotly_chart,
    display_error_message,
    display_info_message,
    format_number,
)
from src.ui.state import get_state, set_state
from src.logging.logger import logger


def render_parameter_optimizer_page() -> None:
    """Render the parameter optimiser page."""
    import streamlit as st

    st.header("🔧 Parameter Optimiser")
    st.markdown("Optimise strategy parameters for maximum performance.")

    col1, col2 = st.columns(2)
    with col1:
        symbol = st.text_input("Symbol", placeholder="NABIL").strip().upper()
        strategy = st.selectbox(
            "Strategy",
            ["momentum", "breakout", "mean_reversion", "volatility"],
        )
    with col2:
        param_a_min = st.number_input("Parameter A (min)", value=5, step=1)
        param_a_max = st.number_input("Parameter A (max)", value=50, step=1)
        param_b_min = st.number_input("Parameter B (min)", value=10, step=1)
        param_b_max = st.number_input("Parameter B (max)", value=100, step=10)

    if st.button("🔧 Optimise Parameters", type="primary", use_container_width=True):
        if not symbol:
            display_error_message("Please enter a symbol.")
            return

        with st.spinner(f"Optimising parameters for {symbol}..."):
            try:
                from src.config import DATA_DIRECTORY
                from pathlib import Path
                import pandas as pd

                data_path = Path(DATA_DIRECTORY) / f"{symbol.lower()}.csv"
                if not data_path.exists():
                    display_error_message(f"No data file for '{symbol}'.")
                    return

                df = pd.read_csv(str(data_path))

                from src.optimization.parameter_optimizer import ParameterOptimizer

                optimizer = ParameterOptimizer()
                result = optimizer.optimize(
                    df=df,
                    strategy=strategy,
                    param_ranges={
                        "param_a": list(range(param_a_min, param_a_max + 1, 5)),
                        "param_b": list(range(param_b_min, param_b_max + 1, 10)),
                    },
                )

                set_state("po_result", result)

                # Best parameters
                best_params = result.get("best_params", {})
                best_score = result.get("best_score", 0.0)

                st.subheader("Best Parameters")
                col_a, col_b = st.columns(2)
                with col_a:
                    for k, v in best_params.items():
                        st.metric(f"Best {k}", str(v))
                with col_b:
                    st.metric("Best Score", format_number(best_score, 4))

                # Parameter heatmap
                scores_grid = result.get("scores_grid", [])
                if scores_grid:
                    try:
                        from src.dashboard.dashboard import Dashboard

                        x_vals = [str(v) for v in result.get("x_labels", [])]
                        y_vals = [str(v) for v in result.get("y_labels", [])]
                        fig = Dashboard.parameter_heatmap(x_vals, y_vals, scores_grid)
                        render_plotly_chart(fig, title="Parameter Heatmap")
                    except Exception:
                        display_info_message("Heatmap chart unavailable.")

                # Ranking table
                all_results = result.get("all_results", [])
                if all_results:
                    st.subheader(f"All Results ({len(all_results)})")
                    ranking = []
                    for r in all_results[:50]:
                        ranking.append({
                            "Param A": str(r.get("param_a", "")),
                            "Param B": str(r.get("param_b", "")),
                            "Score": format_number(r.get("score", 0.0), 4),
                            "Return": format_number(r.get("return_pct", 0.0), 2),
                            "Sharpe": format_number(r.get("sharpe", 0.0), 2),
                        })
                    display_data_table(ranking)

            except ImportError:
                display_error_message("Parameter optimiser module not available.")
            except Exception as exc:
                logger.exception("Parameter optimisation failed: %s", exc)
                display_error_message(f"Error: {exc}")

    logger.info("Parameter Optimiser page rendered.")
