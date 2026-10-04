"""Walk Forward Analysis page — display training/testing windows and performance."""

from __future__ import annotations

from src.ui.helpers import (
    display_data_table,
    render_plotly_chart,
    display_error_message,
    display_info_message,
    display_success_message,
    format_number,
)
from src.ui.state import get_state, set_state
from src.logging.logger import logger


def render_walk_forward_page() -> None:
    """Render the walk-forward analysis page."""
    import streamlit as st

    st.header("🏃 Walk Forward Analysis")
    st.markdown("Walk-forward optimisation to validate strategy robustness.")

    col1, col2 = st.columns(2)
    with col1:
        window_size = st.number_input("Window Size (days)", min_value=30, value=252, step=30)
        step_size = st.number_input("Step Size (days)", min_value=10, value=63, step=10)
    with col2:
        symbol = st.text_input("Symbol", placeholder="NABIL").strip().upper()
        n_windows = st.number_input("Number of Windows", min_value=2, value=5, step=1)

    if st.button("🏃 Run Walk-Forward", type="primary", use_container_width=True):
        if not symbol:
            display_error_message("Please enter a symbol.")
            return

        with st.spinner(f"Running walk-forward for {symbol}..."):
            try:
                from src.config import DATA_DIRECTORY
                from pathlib import Path
                import pandas as pd

                data_path = Path(DATA_DIRECTORY) / f"{symbol.lower()}.csv"
                if not data_path.exists():
                    display_error_message(f"No data file for '{symbol}'.")
                    return

                df = pd.read_csv(str(data_path))
                if "Date" in df.columns:
                    df["Date"] = pd.to_datetime(df["Date"])
                    df = df.sort_values("Date")

                from src.optimization.walk_forward import WalkForwardOptimizer

                optimizer = WalkForwardOptimizer(
                    window_size=window_size,
                    step_size=step_size,
                )

                result = optimizer.run(df)
                set_state("wf_result", result)

                display_success_message(
                    f"Walk-forward complete: {len(result.get('windows', []))} windows."
                )

                # Performance over windows
                windows = result.get("windows", [])
                if windows:
                    st.subheader("Window Performance")
                    wf_table = []
                    for w in windows:
                        wf_table.append({
                            "Window": w.get("label", ""),
                            "In-Sample Score": format_number(w.get("in_sample_score", 0.0), 4),
                            "Out-of-Sample Score": format_number(w.get("out_sample_score", 0.0), 4),
                            "Return": format_number(w.get("return_pct", 0.0), 2),
                            "Sharpe": format_number(w.get("sharpe", 0.0), 2),
                        })
                    display_data_table(wf_table)

                    # Chart
                    try:
                        from src.dashboard.dashboard import Dashboard

                        labels = [w.get("label", f"W{i}") for i, w in enumerate(windows)]
                        in_sample = [w.get("in_sample_score", 0.0) for w in windows]
                        out_sample = [w.get("out_sample_score", 0.0) for w in windows]
                        fig = Dashboard.walk_forward_performance(labels, in_sample, out_sample)
                        render_plotly_chart(fig, title="Walk-Forward Performance")
                    except Exception:
                        display_info_message("Performance chart unavailable.")

                # Parameter evolution
                params = result.get("parameter_evolution", [])
                if params:
                    st.subheader("Parameter Evolution")
                    display_data_table(params)

            except ImportError:
                display_error_message("Walk-forward module not available.")
            except Exception as exc:
                logger.exception("Walk-forward failed: %s", exc)
                display_error_message(f"Error: {exc}")

    logger.info("Walk Forward page rendered.")
