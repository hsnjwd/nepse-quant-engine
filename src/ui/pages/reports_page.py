"""Reports page — generate and export portfolio, backtest, and market reports using ExportCenter."""

from __future__ import annotations

from typing import Any
from datetime import datetime

import streamlit as st
import pandas as pd

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
from src.data.export import ExportCenter, ExportFormat
from src.data import DataService as _DataService


def _svc() -> _DataService:
    return _DataService()


def render() -> None:
    """Render the Reports page with ExportCenter integration."""
    section_header("Reports", "Generate and export trading reports in multiple formats")

    report_type = st.selectbox(
        "Report Type",
        ["Portfolio Report", "Backtest Report", "Market Report", "AI Analysis Report"],
        key="report_type",
    )

    divider()

    if report_type == "Portfolio Report":
        _render_portfolio_report()
    elif report_type == "Backtest Report":
        _render_backtest_report()
    elif report_type == "Market Report":
        _render_market_report()
    elif report_type == "AI Analysis Report":
        _render_ai_report()


def _render_ai_report() -> None:
    """Generate an AI Analysis report (Part 13 — reports include AI analysis)."""
    section_header("AI Analysis Report")

    col1, col2 = st.columns([1, 2])
    with col1:
        generate = st.button("🤖 Generate AI Analysis", use_container_width=True, type="primary")
    with col2:
        symbol = st.text_input("Symbol", value="NABIL", key="ai_report_symbol")

    if not generate:
        st.info("👆 Click **Generate AI Analysis** for a plain-language market and signal summary.")
        return

    try:
        from src.ai.advisor import SignalAdvisor
        from src.ai.market_summary import MarketSummaryGenerator

        service = _svc()
        summary = service.get_market_summary()

        # Market commentary
        gainers = service.get_top_gainers(5)
        losers = service.get_top_losers(5)
        regime = "UNKNOWN"
        try:
            hist = service.get_nepse_index_history(days=120)
            if hist is not None and not hist.empty:
                from src.regime.detector import MarketRegimeDetector

                regime = MarketRegimeDetector().detect(hist).regime
        except Exception:
            pass

        commentary = MarketSummaryGenerator().generate(
            summary, top_gainers=gainers, top_losers=losers, regime=regime
        )

        # Signal explanation for the requested symbol
        explanation = None
        try:
            history = service.get_history(symbol, days=365)
            if not history.is_empty:
                from src.engine.analyzer import analyze_dataframe

                analysis = analyze_dataframe(history.df)
                explanation = SignalAdvisor().explain(
                    symbol=symbol,
                    analysis=analysis,
                    regime=regime,
                )
        except Exception:
            explanation = None

        st.markdown(f"### {commentary.headline}")
        for para in commentary.paragraphs:
            st.markdown(f"- {para}")
        st.caption(f"Sentiment: {commentary.sentiment}")

        if explanation:
            divider()
            st.markdown(f"#### {symbol.upper()} Signal")
            st.markdown(f"**{explanation.signal}** — confidence {explanation.confidence:.0f}%")
            for reason in explanation.reasons:
                st.markdown(f"- {reason}")
            if explanation.risks:
                st.markdown("##### ⚠️ Risks")
                for risk in explanation.risks:
                    st.markdown(f"- {risk}")
            st.info(explanation.summary)
            st.success(explanation.recommendation)

        # Export the AI report as JSON
        payload = {
            "type": "ai_analysis",
            "symbol": symbol.upper() if explanation else None,
            "regime": regime,
            "commentary": commentary.to_dict(),
            "explanation": explanation.to_dict() if explanation else None,
        }
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        st.download_button(
            "📥 Export AI Report (JSON)",
            data=ExportCenter.to_json(payload),
            file_name=f"ai_analysis_{ts}.json",
            mime="application/json",
            use_container_width=True,
        )
    except Exception as exc:
        st.error(f"AI analysis failed: {exc}")
        st.caption("Live data may be unavailable — the report needs market data.")


def _render_portfolio_report() -> None:
    """Generate and display a portfolio report using ExportCenter."""
    section_header("Portfolio Report")

    col1, col2, col3, col4 = st.columns(4)
    with col1:
        generate = st.button("📄 Generate Report", use_container_width=True, type="primary")

    if not generate:
        st.info("👆 Click **Generate Report** to create a portfolio report.")
        return

    with st.spinner("Generating portfolio report..."):
        try:
            portfolio = st.session_state.get("portfolio_data", {})
            trades = st.session_state.get("paper_trades", [])
            st.session_state["report_data"] = portfolio
        except Exception as e:
            st.error(f"Could not generate report: {e}")
            return

    portfolio = st.session_state.get("report_data", {})
    if not portfolio:
        # Try to get from DataService
        try:
            service = _svc()
            summary = service.get_market_summary()
            if summary and summary.index > 0:
                portfolio = {"market_summary": {
                    "index": summary.index,
                    "change": summary.change,
                    "change_pct": summary.change_pct,
                    "volume": summary.volume,
                    "turnover": summary.turnover,
                    "advances": summary.advances,
                    "declines": summary.declines,
                    "status": summary.status,
                }}
        except Exception:
            pass

    if not portfolio:
        st.warning("No portfolio data available.")
        return

    # Summary
    cols = st.columns(4)
    with cols[0]:
        kpi_card("💰 Total Value",
                 fmt_rupees(safe_float(safe_get(portfolio, "portfolio_value",
                                                safe_get(portfolio, "market_summary", {}).get("index", 0)))))
    with cols[1]:
        kpi_card("📈 Invested",
                 fmt_rupees(safe_float(safe_get(portfolio, "portfolio_cost", 0))))
    with cols[2]:
        pnl = safe_float(safe_get(portfolio, "portfolio_pnl", 0))
        kpi_card("📊 P&L", fmt_rupees(pnl))
    with cols[3]:
        ret = safe_float(safe_get(portfolio, "portfolio_return_pct", 0))
        kpi_card("📉 Return %", fmt_pct(ret / 100.0))

    divider()

    # Holdings table
    holdings = safe_get(portfolio, "holdings", [])
    if holdings:
        st.markdown("##### Holdings")
        rows = []
        for h in holdings:
            rows.append({
                "Symbol": h.get("symbol", "—"),
                "Qty": h.get("quantity", 0),
                "Avg Price": fmt_rupees(safe_float(h.get("average_price", 0))),
                "LTP": fmt_rupees(safe_float(h.get("ltp", 0))),
                "Investment": fmt_rupees(safe_float(h.get("investment", 0))),
                "Current Value": fmt_rupees(safe_float(h.get("current_value", 0))),
                "P&L": fmt_rupees(safe_float(h.get("pnl", 0))),
                "P&L %": fmt_pct(safe_float(h.get("pnl_pct", 0)) / 100.0),
            })
        df = pd.DataFrame(rows)
        st.dataframe(df, use_container_width=True, hide_index=True)

    # Export buttons
    divider()
    _export_buttons(
        portfolio,
        "portfolio",
        holdings,
        trades,
    )


def _render_backtest_report() -> None:
    """Display backtest report from session state with ExportCenter."""
    section_header("Backtest Report")

    result = st.session_state.get("backtest_result")
    if not result:
        st.info("Run a backtest first from the **Backtest** page.")
        return

    metrics = safe_get(result, "metrics", {})
    trades = safe_get(result, "trades", [])
    symbol = st.session_state.get("backtest_symbol", "BACKTEST")

    cols = st.columns(5)
    with cols[0]:
        kpi_card("📈 Return",
                 fmt_pct(safe_float(safe_get(metrics, "total_return_pct", 0)) / 100.0))
    with cols[1]:
        kpi_card("📊 Sharpe",
                 fmt_number(safe_float(safe_get(metrics, "sharpe_ratio", 0)), 2))
    with cols[2]:
        kpi_card("🏆 Win Rate",
                 fmt_pct(safe_float(safe_get(metrics, "win_rate", 0)) / 100.0))
    with cols[3]:
        kpi_card("📉 Max DD",
                 fmt_pct(safe_float(safe_get(metrics, "max_drawdown_pct", 0)) / 100.0))
    with cols[4]:
        kpi_card("🔄 Trades", str(len(trades)))

    _export_backtest_buttons(metrics, trades, symbol)


def _render_market_report() -> None:
    """Generate a market-wide summary report using DataService."""
    section_header("Market Report")

    col1, col2 = st.columns([1, 2])
    with col1:
        generate = st.button("📄 Generate Market Report", use_container_width=True, type="primary")

    if not generate:
        st.info("👆 Click **Generate Market Report** to scan the entire market.")
        return

    with st.spinner("Scanning market for report..."):
        try:
            service = _svc()
            scan_result = service.scan_market()
            summary = service.get_market_summary()
            gainers = service.get_top_gainers(10)
            losers = service.get_top_losers(10)

            scan_data = {
                "results": scan_result.results if scan_result else [],
                "summary": {
                    "index": summary.index if summary else 0,
                    "change": summary.change if summary else 0,
                    "advances": summary.advances if summary else 0,
                    "declines": summary.declines if summary else 0,
                    "status": summary.status if summary else "Unknown",
                },
                "gainers": [{"symbol": s.symbol, "ltp": s.ltp, "change_pct": s.change_pct} for s in (gainers or [])],
                "losers": [{"symbol": s.symbol, "ltp": s.ltp, "change_pct": s.change_pct} for s in (losers or [])],
            }
            st.session_state["market_report_data"] = scan_data
        except Exception as e:
            st.error(f"Market scan failed: {e}")
            return

    scan_data = st.session_state.get("market_report_data", {})
    results = safe_get(scan_data, "results", [])
    gainers = safe_get(scan_data, "gainers", [])
    losers = safe_get(scan_data, "losers", [])

    cols = st.columns(4)
    with cols[0]:
        kpi_card("📊 Total Stocks", str(len(results)))
    with cols[1]:
        scan_summary = safe_get(scan_data, "summary", {})
        kpi_card("📈 NEPSE",
                 f"{safe_float(safe_get(scan_summary, 'index', 0)):,.2f}")
    with cols[2]:
        kpi_card("🟢 Advances", str(safe_get(scan_summary, "advances", 0)))
    with cols[3]:
        kpi_card("🔴 Declines", str(safe_get(scan_summary, "declines", 0)))

    if gainers or losers:
        col1, col2 = st.columns(2)
        with col1:
            st.markdown("##### 🟢 Top Gainers")
            for s in gainers:
                st.markdown(
                    f"**{s.get('symbol', '?')}** — "
                    f"<span style='color: #00C853;'>+{s.get('change_pct', 0):.2f}%</span>",
                    unsafe_allow_html=True,
                )
        with col2:
            st.markdown("##### 🔴 Top Losers")
            for s in losers:
                st.markdown(
                    f"**{s.get('symbol', '?')}** — "
                    f"<span style='color: #FF5252;'>{s.get('change_pct', 0):.2f}%</span>",
                    unsafe_allow_html=True,
                )

    # Export
    _export_buttons(scan_data, "market", results, [])


def _export_buttons(
    data: dict[str, Any],
    prefix: str = "report",
    holdings: list[dict[str, Any]] | None = None,
    trades: list[dict[str, Any]] | None = None,
) -> None:
    """Render export buttons using ExportCenter."""
    col1, col2, col3, col4 = st.columns(4)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")

    with col1:
        json_bytes = ExportCenter.to_json(data)
        st.download_button(
            "📥 Export JSON",
            data=json_bytes,
            file_name=f"{prefix}_{ts}.json",
            mime="application/json",
            use_container_width=True,
        )
    with col2:
        holdings_list = holdings or []
        trades_list = trades or []
        if holdings_list or trades_list:
            csv_bytes = ExportCenter.portfolio_to_csv(holdings_list, trades_list)
            label = "📥 Export CSV"
        else:
            try:
                flat = pd.json_normalize(data, max_level=1)
                csv_bytes = flat.to_csv(index=False).encode("utf-8")
                label = "📥 Export CSV"
            except Exception:
                st.button("📥 CSV (N/A)", disabled=True, use_container_width=True)
                csv_bytes = b""
                label = ""
        if csv_bytes:
            st.download_button(
                label,
                data=csv_bytes,
                file_name=f"{prefix}_{ts}.csv",
                mime="text/csv",
                use_container_width=True,
            )
    with col3:
        # HTML report
        sections = []
        if data:
            flat_section = {k: str(v) if not isinstance(v, (list, dict)) else f"<{type(v).__name__}>"
                           for k, v in list(data.items())[:10]}
            sections.append({
                "title": "Summary",
                "headers": ["Field", "Value"],
                "rows": [[k, v] for k, v in flat_section.items()],
            })
        if holdings:
            sections.append({
                "title": f"Holdings ({len(holdings)})",
                "headers": list(holdings[0].keys()) if holdings else [],
                "rows": [[str(h.get(hk, "")) for hk in (list(holdings[0].keys()) if holdings else [])]
                         for h in holdings],
            })
        if trades:
            sections.append({
                "title": f"Trades ({len(trades)})",
                "headers": list(trades[0].keys()) if trades else [],
                "rows": [[str(t.get(tk, "")) for tk in (list(trades[0].keys()) if trades else [])]
                         for t in trades],
            })
        html_str = ExportCenter.to_html(f"{prefix.title()} Report", sections)
        st.download_button(
            "📄 Export HTML",
            data=html_str.encode("utf-8"),
            file_name=f"{prefix}_{ts}.html",
            mime="text/html",
            use_container_width=True,
        )
    with col4:
        # Excel (if holdings or trades present)
        if holdings or trades:
            try:
                excel_bytes = ExportCenter.portfolio_to_excel(holdings or [], trades or [])
                st.download_button(
                    "📕 Export Excel",
                    data=excel_bytes,
                    file_name=f"{prefix}_{ts}.xlsx",
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    use_container_width=True,
                )
            except Exception:
                st.button("📕 Excel (N/A)", disabled=True, use_container_width=True)
        else:
            st.button("📕 Excel (N/A)", disabled=True, use_container_width=True)


def _export_backtest_buttons(
    metrics: dict[str, Any],
    trades: list[dict[str, Any]],
    symbol: str,
) -> None:
    """Render backtest-specific export buttons."""
    col1, col2, col3 = st.columns(3)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")

    with col1:
        csv_bytes = ExportCenter.backtest_to_csv(trades, metrics)
        st.download_button(
            "📥 Download CSV",
            data=csv_bytes,
            file_name=f"backtest_{symbol.lower()}_{ts}.csv",
            mime="text/csv",
            use_container_width=True,
        )
    with col2:
        json_bytes = ExportCenter.to_json({"metrics": metrics, "trades": trades})
        st.download_button(
            "📥 Export JSON",
            data=json_bytes,
            file_name=f"backtest_{symbol.lower()}_{ts}.json",
            mime="application/json",
            use_container_width=True,
        )
    with col3:
        headers = list(trades[0].keys()) if trades else []
        trade_rows = [[str(t.get(h, "")) for h in headers] for t in trades] if trades else []
        metric_rows = [[k, f"{v:.4f}" if isinstance(v, float) else str(v)]
                       for k, v in metrics.items()]
        html = ExportCenter.to_html(
            f"Backtest Report — {symbol}",
            [
                {"title": "Summary", "headers": ["Metric", "Value"], "rows": metric_rows},
                {"title": f"Trades ({len(trades)})", "headers": headers, "rows": trade_rows},
            ],
        )
        st.download_button(
            "📄 Download HTML",
            data=html.encode("utf-8"),
            file_name=f"backtest_{symbol.lower()}_{ts}.html",
            mime="text/html",
            use_container_width=True,
        )
