"""Optimizer page — strategy parameter optimisation with heatmaps."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import streamlit as st
import pandas as pd

from src.config import DATA_DIRECTORY
from src.ui.helpers import (
    section_header,
    divider,
    fmt_rupees,
    fmt_pct,
    safe_get,
    safe_float,
    fmt_number,
)
from src.ui.components import kpi_card, line_chart


def render() -> None:
    """Render the Optimizer page."""
    section_header("Strategy Optimizer", "Find optimal parameters for your strategy")

    data_path = Path(DATA_DIRECTORY)
    csv_files = sorted(data_path.glob("*.csv"))
    symbols = [f.stem.upper() for f in csv_files if f.stem.lower() != "sample"]

    if not symbols:
        st.warning("No market data files found.")
        return

    # ── Inputs ──────────────────────────────────────────────────
    col1, col2 = st.columns(2)
    with col1:
        symbol = st.selectbox("Symbol", symbols, key="opt_symbol")
        param1_name = st.text_input("Parameter 1", value="fast_ma", key="opt_p1_name")
        param1_min = st.number_input("Min", value=5, key="opt_p1_min")
        param1_max = st.number_input("Max", value=50, key="opt_p1_max")
        param1_step = st.number_input("Step", value=5, key="opt_p1_step")
    with col2:
        capital = st.number_input("Capital (₹)", min_value=1000.0, value=100000.0, key="opt_capital")
        param2_name = st.text_input("Parameter 2", value="slow_ma", key="opt_p2_name")
        param2_min = st.number_input("Min", value=20, key="opt_p2_min")
        param2_max = st.number_input("Max", value=200, key="opt_p2_max")
        param2_step = st.number_input("Step", value=10, key="opt_p2_step")

    col1, col2 = st.columns([1, 3])
    with col1:
        optimize_btn = st.button("⚡ Optimize", use_container_width=True, type="primary")

    divider()

    if not optimize_btn:
        st.info("👆 Configure parameters and click **Optimize**.")
        return

    # ── Run parameter scan ──────────────────────────────────────
    with st.spinner(f"Optimizing parameters for {symbol}..."):
        try:
            from src.optimization.parameter_optimizer import ParameterOptimizer
            from src.loaders.csv_loader import load_csv

            df = load_csv(str(data_path / f"{symbol.lower()}.csv"))

            param_grid = {
                param1_name: list(range(param1_min, param1_max + 1, param1_step)),
            }
            if param2_name.strip():
                param_grid[param2_name] = list(range(param2_min, param2_max + 1, param2_step))

            optimizer = ParameterOptimizer()
            results = optimizer.optimize(
                df=df,
                param_grid=param_grid,
                capital=capital,
            )
            st.session_state["optimizer_result"] = results
            st.session_state["optimizer_symbol"] = symbol
        except ImportError:
            st.error("Parameter optimizer module not available. Install with: pip install nepse-quant-engine[optimization]")
            return
        except Exception as e:
            st.error(f"Optimization failed: {e}")
            return

    results = st.session_state.get("optimizer_result")

    if not results:
        return

    # ── Best Parameters ─────────────────────────────────────────
    section_header("Best Parameters")
    best = results.get("best_params", {})

    cols = st.columns(4)
    for i, (k, v) in enumerate(best.items()):
        with cols[i % 4]:
            kpi_card(k.replace("_", " ").title(), str(v))

    divider()

    # ── Performance Metrics ─────────────────────────────────────
    best_metrics = results.get("best_metrics", {})
    if best_metrics:
        section_header("Best Performance")
        cols = st.columns(5)
        with cols[0]:
            kpi_card("📈 Return", fmt_pct(safe_float(safe_get(best_metrics, "total_return_pct", 0)) / 100.0))
        with cols[1]:
            kpi_card("📊 Sharpe", fmt_number(safe_float(safe_get(best_metrics, "sharpe_ratio", 0)), 2))
        with cols[2]:
            kpi_card("🏆 Win Rate", fmt_pct(safe_float(safe_get(best_metrics, "win_rate", 0)) / 100.0))
        with cols[3]:
            kpi_card("📉 Max DD", fmt_pct(safe_float(safe_get(best_metrics, "max_drawdown_pct", 0)) / 100.0))
        with cols[4]:
            kpi_card("🔄 Trades", str(safe_get(best_metrics, "total_trades", 0)))

    divider()

    # ── Parameter Rankings ──────────────────────────────────────
    rankings = results.get("rankings", [])
    if rankings:
        section_header("Parameter Rankings")
        df_rank = pd.DataFrame(rankings)
        st.dataframe(df_rank, use_container_width=True, hide_index=True)

    # ── Performance Heatmap ─────────────────────────────────────
    heatmap_data = results.get("heatmap", {})
    if heatmap_data:
        section_header("Performance Heatmap")
        try:
            import plotly.express as px

            # Convert heatmap dict to matrix
            param1_vals = sorted(set(k[0] for k in heatmap_data.keys()))
            param2_vals = sorted(set(k[1] for k in heatmap_data.keys()))

            if len(param2_vals) > 1:
                matrix = [[heatmap_data.get((p1, p2), 0) for p2 in param2_vals] for p1 in param1_vals]
                fig = px.imshow(
                    matrix,
                    x=[str(v) for v in param2_vals],
                    y=[str(v) for v in param1_vals],
                    title="Performance Heatmap (Return %)",
                    color_continuous_scale="RdYlGn",
                    aspect="auto",
                )
                fig.update_layout(
                    paper_bgcolor="rgba(0,0,0,0)",
                    plot_bgcolor="rgba(0,0,0,0)",
                    font=dict(color="#FFFFFF"),
                    height=400,
                )
                st.plotly_chart(fig, use_container_width=True)
            else:
                # Single-parameter: line chart
                x_vals = list(heatmap_data.keys())
                y_vals = list(heatmap_data.values())
                fig = px.line(
                    x=[str(k[0]) for k in x_vals], y=y_vals,
                    title="Parameter Performance",
                    markers=True,
                )
                fig.update_layout(
                    paper_bgcolor="rgba(0,0,0,0)",
                    plot_bgcolor="rgba(0,0,0,0)",
                    font=dict(color="#FFFFFF"),
                    height=350,
                )
                st.plotly_chart(fig, use_container_width=True)
        except Exception as e:
            st.info(f"Heatmap not available: {e}")
