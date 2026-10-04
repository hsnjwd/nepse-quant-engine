"""Strategy Marketplace page — discover and browse pluggable strategies."""

from __future__ import annotations

import pandas as pd
import streamlit as st

from src.ui.helpers import section_header, divider
from src.ui.components import kpi_card


def render() -> None:
    """Render the Strategy Marketplace page."""
    section_header(
        "🛒 Strategy Marketplace",
        "Discover pluggable strategies with metadata and instant registration",
    )

    try:
        from src.strategies.marketplace import (
            discover_strategies,
            marketplace_info,
            register_marketplace,
            strategy_metadata,
        )
        from src.strategies.registry import list_strategies
    except ImportError as exc:
        st.error(f"Marketplace module unavailable: {exc}")
        return

    col1, col2 = st.columns(2)
    with col1:
        if st.button("🔍 Scan Marketplace", type="primary", key="mp_scan"):
            info = marketplace_info()
            st.session_state["mp_info"] = info
            st.session_state["mp_scanned"] = True
    with col2:
        if st.button("📥 Register All", key="mp_register"):
            count = register_marketplace()
            st.success(f"Registered {count} new strategy(ies).")

    if st.session_state.get("mp_scanned"):
        info = st.session_state["mp_info"]
        strategies = info.get("strategies", [])
        registered = info.get("registered", [])

        cols = st.columns(3)
        with cols[0]:
            kpi_card("Discovered", str(info.get("count", 0)))
        with cols[1]:
            kpi_card("Registered", str(len(registered)))
        with cols[2]:
            kpi_card("Available", str(len(list_strategies())))

        divider()

        if strategies:
            st.markdown("#### Strategy Catalog")
            for i, strategy in enumerate(strategies):
                tags = ", ".join(strategy.get("tags", [])) or "—"
                with st.container():
                    st.markdown(
                        f"##### {strategy['name']} "
                        f"`v{strategy['version']}` — *{strategy.get('author', 'Unknown')}*"
                    )
                    st.caption(strategy.get("description", ""))
                    st.markdown(f"Tags: `{tags}`")
                    # Cross-page actions (Part 13): load into Builder / Backtest
                    a1, a2, a3 = st.columns([1, 1, 2])
                    with a1:
                        if st.button("🧩 Load into Builder", key=f"mp_load_{i}"):
                            st.session_state["sb_marketplace_load"] = dict(strategy)
                            st.session_state["page"] = "strategy_builder"
                            st.rerun()
                    with a2:
                        if st.button("⏪ Backtest", key=f"mp_backtest_{i}"):
                            st.session_state["sb_marketplace_load"] = dict(strategy)
                            st.session_state["page"] = "backtest"
                            st.rerun()
                    with a3:
                        st.caption(f"Win rate / PF / MaxDD available after backtest.")
                    st.markdown("---")
        else:
            st.info("No strategies discovered. Check the marketplace scan.")

        registered_df = pd.DataFrame(
            [{"Strategy": s} for s in registered]
        ) if registered else pd.DataFrame()
        if not registered_df.empty:
            st.markdown("#### Registered Strategies")
            st.dataframe(registered_df, use_container_width=True, hide_index=True)
