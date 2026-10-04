"""Backtest page — run historical backtests with strategy selection, date ranges, parameter config, and export."""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path
from typing import Any

import streamlit as st
import pandas as pd

from src.config import DATA_DIRECTORY
from src.backtest.engine import run_backtest
from src.ui.components import kpi_card
from src.ui.helpers import (
    section_header,
    divider,
    fmt_rupees,
    fmt_pct,
    safe_get,
    safe_float,
    fmt_number,
)
from src.data.export import ExportCenter
from src.ui.theme import theme


def render() -> None:
    """Render the Backtest page with strategy selection, date range, and parameter configuration."""
    section_header("Backtest", "Historical strategy performance simulation")

    data_path = Path(DATA_DIRECTORY)
    csv_files = sorted(data_path.glob("*.csv"))
    symbols = [f.stem.upper() for f in csv_files if f.stem.lower() != "sample"]

    if not symbols:
        st.warning("No market data files found.")
        return

    # ── Input Configuration ─────────────────────────────────────
    st.markdown("##### ⚙️ Configuration")

    col1, col2 = st.columns(2)
    with col1:
        # Strategy selection
        strategy_opts = ["Momentum", "Mean Reversion", "Breakout", "Moving Average Crossover", "Volatility"]
        strategy = st.selectbox("Strategy", strategy_opts, key="bt_strategy")

        # Stock selection (multi-select)
        selected_symbols = st.multiselect("Stocks", symbols, default=[symbols[0]], key="bt_symbols")
        capital = st.number_input("Capital (₹)", min_value=1000.0, value=100000.0, step=10000.0, key="bt_capital")

    with col2:
        # Date range
        today = date.today()
        start_date = st.date_input(
            "Start Date",
            value=today - timedelta(days=365),
            max_value=today - timedelta(days=30),
            key="bt_start_date",
        )
        end_date = st.date_input(
            "End Date",
            value=today,
            max_value=today,
            min_value=start_date,
            key="bt_end_date",
        )
        commission = st.number_input("Commission (%)", min_value=0.0, value=0.1, step=0.05, key="bt_commission") / 100.0
        slippage = st.number_input("Slippage (%)", min_value=0.0, value=0.0, step=0.05, key="bt_slippage") / 100.0

    # Strategy-specific parameters
    st.markdown("##### 📐 Strategy Parameters")
    if strategy == "Momentum":
        col1, col2 = st.columns(2)
        with col1:
            lookback = st.slider("Lookback Period (days)", 5, 120, 20, key="bt_lookback")
        with col2:
            threshold = st.slider("Momentum Threshold (%)", 0.0, 20.0, 5.0, key="bt_threshold") / 100.0
    elif strategy == "Moving Average Crossover":
        col1, col2 = st.columns(2)
        with col1:
            fast_ma = st.slider("Fast MA", 5, 50, 10, key="bt_fast_ma")
        with col2:
            slow_ma = st.slider("Slow MA", 20, 200, 50, key="bt_slow_ma")
    else:
        lookback = st.slider("Lookback Period (days)", 5, 120, 20, key="bt_lookback")

    # Run button
    col1, col2 = st.columns([1, 3])
    with col1:
        run_btn = st.button("▶️ Run Backtest", use_container_width=True, type="primary")
    with col2:
        run_all_btn = st.button("▶️▶️ Run All Stocks", use_container_width=True, type="secondary")

    divider()

    if not run_btn and not run_all_btn:
        st.info("👆 Configure parameters and click **Run Backtest** or **Run All Stocks**.")
        return

    # ── Execute Backtest ─────────────────────────────────────────
    if run_all_btn:
        selected_symbols = symbols
        st.info(f"Running backtest on all {len(symbols)} stocks...")

    all_results: list[dict[str, Any]] = []
    progress_bar = st.progress(0, text="Running backtest...")

    for idx, sym in enumerate(selected_symbols):
        progress_bar.progress((idx + 1) / len(selected_symbols), text=f"Testing {sym}...")
        try:
            file_path = str(data_path / f"{sym.lower()}.csv")
            result = run_backtest(
                csv_file=file_path,
                commission=commission,
                slippage=slippage,
            )
            metrics = safe_get(result, "metrics", {})
            trades = safe_get(result, "trades", [])
            report = safe_get(result, "report", {})
            all_results.append({
                "symbol": sym,
                "result": result,
                "total_return": safe_float(safe_get(metrics, "total_return_pct", 0)),
                "sharpe": safe_float(safe_get(metrics, "sharpe_ratio", 0)),
                "win_rate": safe_float(safe_get(metrics, "win_rate", 0)),
                "max_dd": safe_float(safe_get(metrics, "max_drawdown_pct", 0)),
                "num_trades": len(trades),
            })
        except Exception as exc:
            all_results.append({"symbol": sym, "error": str(exc)})
            continue

    progress_bar.empty()

    # Sort by total return
    valid_results = [r for r in all_results if "error" not in r]
    valid_results.sort(key=lambda r: r["total_return"], reverse=True)

    # ── Summary Table ────────────────────────────────────────────
    if valid_results:
        section_header("📊 Backtest Results")
        summary_rows = []
        for r in valid_results:
            summary_rows.append({
                "Symbol": r["symbol"],
                "Return %": fmt_pct(r["total_return"] / 100.0),
                "Sharpe": fmt_number(r["sharpe"], 2),
                "Win Rate": fmt_pct(r["win_rate"] / 100.0),
                "Max DD": fmt_pct(r["max_dd"] / 100.0),
                "Trades": str(r["num_trades"]),
            })

        df_summary = pd.DataFrame(summary_rows)

        def color_return(val: str) -> str:
            if val.startswith("-") or val.startswith("−"):
                return "color: #FF5252"
            if val.startswith("+"):
                return "color: #00C853"
            return ""

        st.dataframe(
            df_summary.style.applymap(color_return, subset=["Return %"]),
            use_container_width=True,
            hide_index=True,
        )

        # Best performer details
        best = valid_results[0]
        section_header(f"🏆 Best Performer: {best['symbol']}")

        cols = st.columns(5)
        with cols[0]:
            kpi_card("📈 Return", fmt_pct(best["total_return"] / 100.0))
        with cols[1]:
            kpi_card("📊 Sharpe", fmt_number(best["sharpe"], 2))
        with cols[2]:
            kpi_card("🏆 Win Rate", fmt_pct(best["win_rate"] / 100.0))
        with cols[3]:
            kpi_card("📉 Max DD", fmt_pct(best["max_dd"] / 100.0))
        with cols[4]:
            kpi_card("🔄 Trades", str(best["num_trades"]))

        st.session_state["backtest_result"] = best["result"]
        st.session_state["backtest_symbol"] = best["symbol"]

        # Detail charts for best performer
        report = safe_get(best["result"], "report", {})
        trades = safe_get(best["result"], "trades", [])

        # Equity curve
        equity_curve = safe_get(report, "equity_curve", [])
        if equity_curve:
            divider()
            section_header("📈 Equity Curve")
            try:
                import plotly.graph_objects as go
                fig = go.Figure()
                fig.add_trace(go.Scatter(
                    x=list(range(len(equity_curve))),
                    y=equity_curve,
                    mode="lines",
                    name="Equity",
                    line=dict(color=theme.success, width=2),
                    fill="tozeroy",
                    fillcolor=f"{theme.success}22",
                ))
                fig.update_layout(
                    title=f"{best['symbol']} — Portfolio Equity",
                    paper_bgcolor="rgba(0,0,0,0)",
                    plot_bgcolor="rgba(0,0,0,0)",
                    font=dict(color=theme.text),
                    xaxis=dict(title="Trade #", gridcolor=theme.border),
                    yaxis=dict(title="Equity (₹)", gridcolor=theme.border),
                    height=350,
                    hovermode="x unified",
                )
                st.plotly_chart(fig, use_container_width=True)
            except ImportError:
                line_chart(
                    x=list(range(len(equity_curve))),
                    y=equity_curve,
                    title="Portfolio Equity Over Time",
                    x_label="Trade #",
                    y_label="Equity (₹)",
                    height=350,
                )

        # Drawdown
        drawdown_curve = safe_get(report, "drawdown_curve", [])
        if drawdown_curve:
            divider()
            section_header("📉 Drawdown")
            from src.ui.components import line_chart as lc
            lc(
                x=list(range(len(drawdown_curve))),
                y=drawdown_curve,
                title="Drawdown Over Time",
                x_label="Trade #",
                y_label="Drawdown %",
                height=250,
            )

        # Trades table
        if trades:
            divider()
            section_header("📋 Trades")
            rows = []
            for t in trades:
                rows.append({
                    "Date": t.get("date", "—"),
                    "Symbol": t.get("symbol", "—"),
                    "Type": t.get("type", "BUY"),
                    "Entry": fmt_rupees(safe_float(t.get("entry_price", 0))),
                    "Exit": fmt_rupees(safe_float(t.get("exit_price", 0))),
                    "P&L": fmt_rupees(safe_float(t.get("pnl", 0))),
                    "Return %": fmt_pct(safe_float(t.get("return_pct", 0)) / 100.0),
                    "Bars": str(t.get("bars", "—")),
                })
            df_trades = pd.DataFrame(rows)

            def color_pnl(val: str) -> str:
                if val.startswith("₹ -") or val.startswith("-"):
                    return "color: #FF5252"
                if val.startswith("₹") and not val.startswith("₹ -"):
                    return "color: #00C853"
                return ""

            st.dataframe(
                df_trades.style.applymap(color_pnl, subset=["P&L", "Return %"]),
                use_container_width=True,
                hide_index=True,
            )

            # ── Export ─────────────────────────────────────────
            divider()
            section_header("📥 Export Results")
            col1, col2, col3 = st.columns(3)

            with col1:
                # CSV export using ExportCenter
                metrics_data = {
                    "total_return_pct": best["total_return"],
                    "sharpe_ratio": best["sharpe"],
                    "win_rate": best["win_rate"],
                    "max_drawdown_pct": best["max_dd"],
                    "total_trades": best["num_trades"],
                    "symbol": best["symbol"],
                }
                csv_bytes = ExportCenter.backtest_to_csv(trades, metrics_data)
                st.download_button(
                    "📥 Download Trades CSV",
                    data=csv_bytes,
                    file_name=f"backtest_{best['symbol'].lower()}.csv",
                    mime="text/csv",
                    use_container_width=True,
                )
            with col2:
                # JSON
                import json
                json_data = json.dumps({"metrics": metrics_data, "trades": trades}, default=str, indent=2)
                st.download_button(
                    "📥 Export JSON",
                    data=json_data.encode("utf-8"),
                    file_name=f"backtest_{best['symbol'].lower()}.json",
                    mime="application/json",
                    use_container_width=True,
                )
            with col3:
                # HTML report
                headers = list(trades[0].keys()) if trades else []
                trade_rows = [[str(t.get(h, "")) for h in headers] for t in trades] if trades else []
                html = ExportCenter.to_html(
                    f"Backtest Report — {best['symbol']}",
                    [
                        {"title": "Summary", "headers": ["Metric", "Value"],
                         "rows": [[k, str(v)] for k, v in metrics_data.items()]},
                        {"title": f"Trades ({len(trades)})", "headers": headers, "rows": trade_rows},
                    ],
                )
                st.download_button(
                    "📄 Download HTML Report",
                    data=html.encode("utf-8"),
                    file_name=f"backtest_{best['symbol'].lower()}.html",
                    mime="text/html",
                    use_container_width=True,
                )

    # Errors
    errors = [r for r in all_results if "error" in r]
    if errors:
        divider()
        st.warning(f"⚠️ {len(errors)} stocks failed:")
        for e in errors:
            st.caption(f"- {e['symbol']}: {e['error']}")
