"""Monte Carlo Lab page — portfolio simulations, VaR, CVaR, ruin."""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd
import streamlit as st

from src.ui.helpers import section_header, divider, fmt_rupees, fmt_pct
from src.ui.components import kpi_card


def _returns_from_input(text: str) -> np.ndarray | None:
    """Parse newline-separated returns from a text area."""
    values: list[float] = []
    for line in text.strip().splitlines():
        line = line.strip().rstrip(",")
        if not line:
            continue
        try:
            values.append(float(line))
        except ValueError:
            continue
    return np.asarray(values, dtype=float) if len(values) >= 20 else None


def _demo_returns() -> np.ndarray:
    """Generate deterministic demo daily returns."""
    rng = np.random.default_rng(42)
    return rng.normal(0.0005, 0.015, 250)


def render() -> None:
    """Render the Monte Carlo Lab page."""
    section_header(
        "🎲 Monte Carlo Lab",
        "Value-at-Risk, Expected Shortfall, stress tests, ruin and recovery",
    )

    try:
        from src.risk.lab import RiskLab
    except ImportError as exc:
        st.error(f"Risk lab module unavailable: {exc}")
        return

    tab_overview, tab_sim, tab_stress = st.tabs(["📊 Overview", "🧮 Monte Carlo", "🧨 Stress Tests"])

    # Shared inputs
    returns_source = st.radio(
        "Returns source",
        ["Demo returns", "Manual returns", "Live history"],
        key="mc_source",
        horizontal=True,
    )

    manual_text = ""
    if returns_source == "Manual returns":
        manual_text = st.text_area(
            "Returns (one per line)",
            value="\n".join(f"{v:.6f}" for v in _demo_returns().tolist()[:60]),
            key="mc_manual",
            height=140,
        )

    portfolio_value = st.number_input("Portfolio value (₹)", value=1_000_000.0, key="mc_value")

    def _get_returns() -> np.ndarray | None:
        if returns_source == "Demo returns":
            return _demo_returns()
        if returns_source == "Manual returns":
            return _returns_from_input(manual_text)
        try:
            from src.data import DataService

            hist = DataService().get_nepse_index_history(days=250)
            if hist is not None and not hist.empty and "Close" in hist.columns:
                closes = hist["Close"].dropna()
                if len(closes) > 20:
                    return closes.pct_change().dropna().to_numpy(dtype=float)
        except Exception:
            pass
        return None

    # ── Tab 1: VaR overview ───────────────────────────────────────
    with tab_overview:
        col1, col2 = st.columns(2)
        with col1:
            confidence = st.selectbox("Confidence", [0.90, 0.95, 0.99], index=1, key="mc_conf")
        with col2:
            method = st.selectbox("Method", ["historical", "parametric", "monte_carlo"], key="mc_method")

        if st.button("Compute VaR", type="primary", key="mc_var_btn"):
            returns = _get_returns()
            if returns is None:
                st.warning("Insufficient returns data (need ≥ 20 observations).")
                return
            lab = RiskLab(returns, portfolio_value=portfolio_value)
            result = lab.var(confidence=float(confidence), method=method)
            st.session_state["mc_var"] = result

        result = st.session_state.get("mc_var")
        if result:
            cols = st.columns(3)
            with cols[0]:
                kpi_card("VaR", fmt_rupees(result.var))
            with cols[1]:
                kpi_card("CVaR / ES", fmt_rupees(result.cvar))
            with cols[2]:
                kpi_card("Std Dev", fmt_pct(result.std))
            for note in result.notes:
                st.caption(f"- {note}")

    # ── Tab 2: Monte Carlo simulation ─────────────────────────────
    with tab_sim:
        col1, col2, col3 = st.columns(3)
        with col1:
            simulations = st.number_input("Simulations", value=5_000, min_value=100, key="mc_sims")
        with col2:
            horizon = st.number_input("Horizon (days)", value=252, min_value=10, key="mc_horizon")
        with col3:
            ruin_pct = st.number_input("Ruin threshold %", value=50.0, key="mc_ruin")

        if st.button("Run Simulation", type="primary", key="mc_sim_btn"):
            returns = _get_returns()
            if returns is None:
                st.warning("Insufficient returns data.")
                return
            lab = RiskLab(returns, portfolio_value=portfolio_value)
            sim = lab.monte_carlo(
                simulations=int(simulations),
                horizon=int(horizon),
                ruin_threshold_pct=float(ruin_pct),
            )
            st.session_state["mc_sim"] = sim

        sim = st.session_state.get("mc_sim")
        if sim:
            cols = st.columns(4)
            with cols[0]:
                kpi_card("Mean Ending", fmt_rupees(sim.mean_ending))
            with cols[1]:
                kpi_card("P5 (95% VaR)", fmt_rupees(sim.p5))
            with cols[2]:
                kpi_card("P95", fmt_rupees(sim.p95))
            with cols[3]:
                kpi_card("Prob of Loss", fmt_pct(sim.probability_of_loss))

            cols = st.columns(3)
            with cols[0]:
                kpi_card("Prob of Ruin", fmt_pct(sim.probability_of_ruin))
            with cols[1]:
                kpi_card("Max Drawdown", fmt_pct(sim.max_drawdown_pct / 100))
            with cols[2]:
                kpi_card("CVaR 95%", fmt_rupees(sim.cvar_95))

            try:
                import plotly.graph_objects as go

                ends = sim.ending_values
                fig = go.Figure()
                fig.add_trace(
                    go.Histogram(x=ends, nbinsx=60, marker_color="#1F77B4")
                )
                fig.add_vline(x=portfolio_value, line_dash="dash", line_color="#FF5252")
                fig.update_layout(
                    xaxis_title="Ending value",
                    yaxis_title="Frequency",
                    paper_bgcolor="rgba(0,0,0,0)",
                    plot_bgcolor="rgba(0,0,0,0)",
                    font=dict(color="#FFFFFF"),
                    height=400,
                )
                st.plotly_chart(fig, use_container_width=True)
            except Exception as exc:
                st.info(f"Chart unavailable: {exc}")

    # ── Tab 3: Stress tests ───────────────────────────────────────
    with tab_stress:
        if st.button("Run Stress Tests", type="primary", key="mc_stress_btn"):
            returns = _get_returns()
            if returns is None:
                st.warning("Insufficient returns data.")
                return
            lab = RiskLab(returns, portfolio_value=portfolio_value)
            results = lab.stress_test()
            st.session_state["mc_stress"] = results

        results = st.session_state.get("mc_stress")
        if results:
            rows = [r.to_dict() for r in results]
            st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
