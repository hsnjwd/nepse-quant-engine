"""Strategy Builder page — construct rule-based strategies visually."""

from __future__ import annotations

import json
from typing import Any

import streamlit as st

from src.ui.helpers import section_header, divider, safe_get, safe_float
from src.ui.components import kpi_card


def render() -> None:
    """Render the Strategy Builder page."""
    section_header(
        "🧩 Strategy Builder",
        "Build trading strategies from visual indicator rules",
    )

    # Cross-page: strategy loaded from the Marketplace (Part 13)
    marketplace_load = st.session_state.pop("sb_marketplace_load", None)
    if marketplace_load:
        st.info(
            f"🧩 Marketplace strategy **{marketplace_load.get('name', '')}** "
            f"(v{marketplace_load.get('version', '?')} by "
            f"{marketplace_load.get('author', 'Unknown')}) selected. "
            "Use the Builder tab to recreate its rules, or the Backtest page "
            "to run it on a symbol."
        )

    try:
        from src.strategy_builder.builder import RuleBasedStrategy
        from src.strategy_builder.rules import IndicatorRule, LogicOp, RuleGroup
        from src.strategy_builder.serialization import load
        from src.strategy_builder.validation import KNOWN_INDICATORS, validate_tree
        from src.strategy_builder.backtest import InstantBacktester
    except ImportError as exc:
        st.error(f"Strategy builder module unavailable: {exc}")
        return

    tabs = st.tabs(["🛠️ Builder", "📜 Saved Strategies", "⚡ Instant Backtest"])

    # ── Tab 1: Builder ────────────────────────────────────────────
    with tabs[0]:
        name = st.text_input("Strategy Name", value="My Strategy", key="sb_name")
        description = st.text_input("Description", value="", key="sb_desc")

        st.markdown("#### Entry Rules")
        st.caption("A rule fires when indicator OP value. Groups combine rules with AND/OR/NOT.")

        rule_count = st.number_input("Number of rules", min_value=1, max_value=6, value=2, key="sb_count")
        logic = st.selectbox("Combine with", ["AND", "OR"], key="sb_logic")

        rules: list[dict[str, Any]] = []
        cols = st.columns(2)
        for i in range(int(rule_count)):
            with cols[i % 2]:
                st.markdown(f"**Rule {i + 1}**")
                indicator = st.selectbox(
                    "Indicator",
                    sorted(KNOWN_INDICATORS),
                    key=f"sb_ind_{i}",
                    index=max(0, [x for x in sorted(KNOWN_INDICATORS)].index("RSI")),
                )
                operator = st.selectbox("Operator", [">", "<", ">=", "<=", "==", "!="], key=f"sb_op_{i}")
                value = st.number_input("Value", value=50.0, key=f"sb_val_{i}")
                rules.append({"indicator": indicator, "operator": operator, "value": value})

        if st.button("Validate & Save Strategy", type="primary", key="sb_save"):
            entry_rules = RuleGroup(
                logic=LogicOp(logic),
                children=[IndicatorRule(**r) for r in rules],
            )
            issues = validate_tree(entry_rules)
            if issues:
                st.error("Validation failed:")
                for issue in issues:
                    st.markdown(f"- `{issue.path}`: {issue.message}")
            else:
                strategy = RuleBasedStrategy(
                    name=name, entry_rules=entry_rules, description=description
                )
                st.session_state["sb_strategy"] = strategy
                # to_rule_dict() returns a plain dict — serialize it directly
                # (to_json() expects a SerializedStrategy, so it is not used here).
                st.session_state["sb_json"] = json.dumps(
                    strategy.to_rule_dict(), indent=2
                )
                st.success("Strategy validated and saved to session.")

        if st.session_state.get("sb_json"):
            st.markdown("#### Serialized JSON")
            st.code(st.session_state["sb_json"], language="json")
            st.download_button(
                "Download strategy JSON",
                data=st.session_state["sb_json"],
                file_name=f"{name.lower().replace(' ', '_')}.json",
                mime="application/json",
                key="sb_download",
            )

    # ── Tab 2: Saved strategies ───────────────────────────────────
    with tabs[1]:
        saved_dir = st.text_input("Strategy directory", value="strategies", key="sb_dir")
        if st.button("List Strategies", key="sb_list"):
            from pathlib import Path

            path = Path(saved_dir)
            files = sorted(path.glob("*.json")) if path.is_dir() else []
            if not files:
                st.info("No saved strategies found.")
            for f in files:
                try:
                    strategy = load(f)
                    st.markdown(f"**{strategy.name}** `v{strategy.version}` — {f.name}")
                    if st.button("Load", key=f"sb_load_{f.name}"):
                        st.session_state["sb_loaded"] = strategy
                        st.success(f"Loaded '{strategy.name}'")
                except Exception as exc:
                    st.warning(f"Could not load {f.name}: {exc}")

        if st.session_state.get("sb_loaded"):
            loaded = st.session_state["sb_loaded"]
            st.markdown("#### Loaded Strategy Rules")
            st.json(loaded.to_dict())

    # ── Tab 3: Instant backtest ───────────────────────────────────
    with tabs[2]:
        strategy = st.session_state.get("sb_strategy")
        if strategy is None:
            st.info("Build a strategy in the Builder tab first.")
            return

        symbol = st.text_input("Symbol", value="NABIL", key="sb_symbol")
        capital = st.number_input("Initial Capital", value=100_000.0, key="sb_capital")

        if st.button("Run Instant Backtest", type="primary", key="sb_backtest"):
            try:
                from src.data import DataService

                history = DataService().get_history(symbol, days=365)
                if history.is_empty:
                    st.warning(f"No history available for {symbol}.")
                    return
                result = InstantBacktester().run(strategy, history.df, initial_capital=capital)
                st.session_state["sb_result"] = result
            except Exception as exc:
                st.error(f"Backtest failed: {exc}")

        result = st.session_state.get("sb_result")
        if result:
            cols = st.columns(5)
            with cols[0]:
                kpi_card("📈 Return", f"{result.total_return_pct:+.2f}%")
            with cols[1]:
                kpi_card("🏆 Win Rate", f"{result.win_rate:.1f}%")
            with cols[2]:
                kpi_card("📉 Max DD", f"{result.max_drawdown_pct:.2f}%")
            with cols[3]:
                kpi_card("📊 Sharpe", f"{result.sharpe_ratio:.2f}")
            with cols[4]:
                kpi_card("🔄 Trades", str(result.trade_count))

            if result.trades:
                st.markdown("#### Trades")
                import pandas as pd

                st.dataframe(pd.DataFrame(result.trades), use_container_width=True)
