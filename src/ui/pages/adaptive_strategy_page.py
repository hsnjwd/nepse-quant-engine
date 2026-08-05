"""Adaptive Strategy page — current regime, selected strategies, weights."""

from __future__ import annotations

from src.ui.helpers import (
    display_kpi_row,
    display_data_table,
    render_plotly_chart,
    display_error_message,
    display_info_message,
    display_success_message,
    format_percent,
    format_number,
    color_for_regime,
)
from src.ui.state import get_state, set_state
from src.logging.logger import logger


def render_adaptive_strategy_page() -> None:
    """Render the adaptive strategy page."""
    import streamlit as st

    st.header("🧠 Adaptive Strategy")
    st.markdown("Strategies selected and weighted based on the market regime.")

    col1, col2 = st.columns(2)
    with col1:
        regime_input = st.text_input(
            "Market Regime",
            placeholder="e.g. BULL, BEAR, SIDEWAYS",
            value=str(get_state("ad_regime", "")),
        ).strip().upper()
    with col2:
        strategies_input = st.text_input(
            "Available Strategies (comma-separated)",
            placeholder="Momentum, Breakout, MeanReversion, Volume",
        ).strip()

    strategies_list = [s.strip() for s in strategies_input.split(",") if s.strip()] if strategies_input else [
        "MomentumStrategy", "BreakoutStrategy", "MeanReversionStrategy", "VolumeStrategy",
    ]

    if st.button("🧠 Analyse", type="primary", use_container_width=True):
        if not regime_input:
            display_error_message("Please enter a market regime.")
            return

        with st.spinner(f"Selecting strategies for '{regime_input}'..."):
            try:
                from src.adaptive.engine import AdaptiveStrategyEngine

                engine = AdaptiveStrategyEngine()
                decision = engine.select_strategies(
                    regime=regime_input,
                    strategies=strategies_list,
                )

                set_state("ad_regime", regime_input)
                set_state("ad_decision", decision)

                display_success_message(
                    f"Analysis complete: {len(decision.selected_strategies)} strategies selected "
                    f"(confidence={decision.confidence:.1f}%)"
                )

                # Regime display
                regime_color = color_for_regime(regime_input)
                st.markdown(
                    f"**Current Regime:** "
                    f"<span style='color:{regime_color};font-weight:bold;font-size:1.2em;'>"
                    f"{regime_input}</span> (confidence: {decision.confidence:.1f}%)",
                    unsafe_allow_html=True,
                )

                # Strategy weights
                weights = decision.strategy_weights
                if weights:
                    st.subheader("Strategy Weights")
                    w_table = []
                    for strat, weight in sorted(weights.items(), key=lambda x: -x[1]):
                        w_table.append({
                            "Strategy": strat,
                            "Weight": format_percent(weight),
                            "Status": (
                                "✅ Selected"
                                if strat in decision.selected_strategies
                                else "❌ Disabled"
                            ),
                        })
                    display_data_table(w_table)

                    # Radar chart
                    try:
                        from src.dashboard.dashboard import Dashboard

                        names = list(weights.keys())
                        vals = list(weights.values())
                        fig = Dashboard.adaptive_strategy_radar(names, vals)
                        render_plotly_chart(fig, title="Strategy Weights Radar")
                    except Exception:
                        display_info_message("Radar chart unavailable.")

                else:
                    display_info_message("No strategy weights computed.")

                # Disabled strategies
                if decision.disabled_strategies:
                    st.subheader("Disabled Strategies")
                    for s in decision.disabled_strategies:
                        st.markdown(f"- {s}")

                # Reasons
                if decision.reasons:
                    st.subheader("Reasons")
                    for r in decision.reasons:
                        st.markdown(f"- {r}")

            except ImportError:
                display_error_message(
                    "Adaptive Strategy Engine not available. "
                    "Ensure the adaptive module is installed."
                )
            except Exception as exc:
                logger.exception("Adaptive strategy failed: %s", exc)
                display_error_message(f"Error: {exc}")

    logger.info("Adaptive Strategy page rendered.")
