"""AI Advisor page — rule-based signal explanations and daily summaries."""

from __future__ import annotations

from typing import Any

import pandas as pd
import streamlit as st

from src.ui.helpers import (
    section_header,
    divider,
    breadcrumb,
    fmt_pct,
    fmt_rupees,
    safe_get,
    safe_float,
    fmt_number,
)
from src.ui.components import kpi_card, signal_badge


# ── Pure helpers (testable without Streamlit) ─────────────────────

_BULLISH_WORDS = (
    "bullish", "above", "recover", "accumulation", "crossover",
    "oversold", "spike", "confirm", "support", "strength", "breakout",
)
_BEARISH_WORDS = (
    "bearish", "below", "distribution", "overbought", "pullback",
    "resistance", "weak", "risk", "thin", "decline",
)


def _reason_score(reason: str) -> int:
    """Classify a reason bullet as bullish (+1), bearish (-1), or neutral (0)."""
    text = reason.lower()
    bull = sum(1 for w in _BULLISH_WORDS if w in text)
    bear = sum(1 for w in _BEARISH_WORDS if w in text)
    if bull > bear:
        return 1
    if bear > bull:
        return -1
    return 0


def bull_bear_scores(reasons: list[str]) -> dict[str, float]:
    """Compute bullish vs bearish factor scores (0–100) from reason bullets."""
    bull = sum(1 for r in reasons if _reason_score(r) == 1)
    bear = sum(1 for r in reasons if _reason_score(r) == -1)
    total = bull + bear
    if total == 0:
        return {"bull": 0.0, "bear": 0.0}
    return {
        "bull": round(bull / total * 100, 1),
        "bear": round(bear / total * 100, 1),
    }


def risk_level(analysis: dict[str, Any]) -> tuple[str, float]:
    """Return (risk_label, risk_score_0_to_1) from analysis data.

    Risk rises with ATR% of price and falls when support is nearby.
    """
    price = safe_float(analysis.get("price"))
    atr = safe_float(analysis.get("atr"))
    score = 0.3
    if price and atr:
        atr_pct = atr / price
        if atr_pct > 0.05:
            score += 0.4
        elif atr_pct > 0.03:
            score += 0.2
    support = safe_float(analysis.get("support"))
    if price and support:
        distance = abs(price - support) / price
        if distance < 0.02:
            score -= 0.2
    if analysis.get("volume_signal") == "LOW_VOLUME":
        score += 0.1
    score = max(0.0, min(1.0, score))
    if score >= 0.7:
        label = "High"
    elif score >= 0.4:
        label = "Medium"
    else:
        label = "Low"
    return label, round(score, 2)


def trade_setup(analysis: dict[str, Any], signal: str) -> dict[str, float | None]:
    """Derive suggested entry / stop / target from analysis + signal.

    Uses price, support/resistance and ATR when available.
    """
    price = safe_float(analysis.get("price"))
    support = safe_float(analysis.get("support")) or None
    resistance = safe_float(analysis.get("resistance")) or None
    atr = safe_float(analysis.get("atr")) or 0.0
    if not price:
        return {"entry": None, "stop": None, "target": None}
    signal = (signal or "HOLD").upper()
    if signal in ("BUY", "STRONG_BUY"):
        entry = price
        stop = support if support else (price - max(atr * 1.5, price * 0.02))
        target = resistance if resistance else (price + max(atr * 3.0, price * 0.06))
    elif signal in ("SELL", "STRONG_SELL"):
        entry = price
        stop = resistance if resistance else (price + max(atr * 1.5, price * 0.02))
        target = support if support else (price - max(atr * 3.0, price * 0.06))
    else:
        entry = price
        stop = support if support else (price - max(atr * 1.5, price * 0.02))
        target = resistance if resistance else (price + max(atr * 3.0, price * 0.06))
    return {
        "entry": round(entry, 2),
        "stop": round(stop, 2),
        "target": round(target, 2),
    }


def render() -> None:
    """Render the AI Advisor page."""
    breadcrumb("Home", "AI & Risk", "AI Advisor")
    section_header(
        "🤖 AI Advisor",
        "Rule-based explanations for signals, markets, and portfolios",
    )

    try:
        from src.ai.advisor import SignalAdvisor
        from src.ai.market_summary import MarketSummaryGenerator
        from src.ai.portfolio_review import PortfolioReviewer
    except ImportError as exc:
        st.error(f"AI advisor module unavailable: {exc}")
        return

    tabs = st.tabs(["💡 Signal Explanation", "📰 Market Summary", "💼 Portfolio Review"])

    # ── Tab 1: Signal explanation ─────────────────────────────────
    with tabs[0]:
        symbol = st.text_input("Symbol", value="NABIL", key="ai_symbol")
        col1, col2 = st.columns(2)
        with col1:
            rsi = st.number_input("RSI", value=42.0, step=1.0, key="ai_rsi")
            price = st.number_input("Price", value=500.0, step=1.0, key="ai_price")
            score = st.number_input("Score", value=65.0, step=1.0, key="ai_score")
        with col2:
            macd = st.number_input("MACD", value=1.5, step=0.1, key="ai_macd")
            macd_signal = st.number_input("MACD Signal", value=0.8, step=0.1, key="ai_macd_sig")
            confidence = st.number_input("Confidence %", value=80.0, step=1.0, key="ai_conf")

        regime = st.selectbox(
            "Market Regime",
            ["BULL", "BEAR", "PANIC", "RECOVERY", "ACCUMULATION", "DISTRIBUTION", "SIDEWAYS", "HIGH_VOLATILITY", "LOW_VOLATILITY"],
            key="ai_regime",
        )

        if st.button("Generate Explanation", type="primary", key="ai_explain_btn"):
            analysis = {
                "rsi": rsi,
                "macd": macd,
                "macd_signal": macd_signal,
                "price": price,
                "score": score,
                "confidence": confidence,
                "support": price * 0.95,
                "resistance": price * 1.05,
                "atr": price * 0.02,
            }
            advisor = SignalAdvisor()
            explanation = advisor.explain(
                symbol=symbol,
                analysis=analysis,
                regime=regime,
            )
            st.session_state["ai_explanation"] = explanation
            st.session_state["ai_analysis"] = analysis

            # Notification Center: surface AI events — one-shot, inside
            # the button handler (avoid spamming on every rerun).
            try:
                from src.ui.notifications import notification_manager

                notification_manager.notify_ai(
                    f"AI Advisor: {symbol.upper()} → {explanation.signal}",
                    message=explanation.summary,
                    priority=("success" if explanation.signal in ("BUY", "STRONG_BUY") else "info"),
                )
            except Exception:
                pass

        explanation = st.session_state.get("ai_explanation")
        if explanation:
            analysis: dict[str, Any] = st.session_state.get("ai_analysis", {})
            signal = explanation.signal

            st.markdown("---")
            signal_badge(signal)

            # ── Confidence gauge + probability bar ─────────────────
            col_g, col_p = st.columns([1, 1])
            with col_g:
                _render_confidence_gauge(explanation.confidence)
            with col_p:
                st.markdown("##### Signal Probability")
                _render_probability_bar(explanation.confidence, signal)

            # ── Bull / Bear score ─────────────────────────────────
            scores = bull_bear_scores(explanation.reasons)
            c1, c2, c3 = st.columns(3)
            with c1:
                kpi_card("🐂 Bullish Factors", f"{scores['bull']:.0f}%")
            with c2:
                kpi_card("🐻 Bearish Factors", f"{scores['bear']:.0f}%")
            with c3:
                risk_label, risk_score = risk_level(analysis)
                kpi_card("⚠️ Risk Meter", risk_label, help_text=f"Risk score {risk_score:.2f}/1.00")

            st.markdown("##### Why?")
            for reason in explanation.reasons:
                st.markdown(f"- {reason}")

            if explanation.risks:
                st.markdown("##### ⚠️ Risks")
                for risk in explanation.risks:
                    st.markdown(f"- {risk}")

            st.markdown("##### 💬 Summary")
            st.info(explanation.summary)
            st.markdown("##### 🎯 Recommendation")
            st.success(explanation.recommendation)

            # ── Suggested entry / stop / target ───────────────────
            setup = trade_setup(analysis, signal)
            if setup["entry"]:
                st.markdown("##### 📐 Trade Setup")
                s1, s2, s3 = st.columns(3)
                with s1:
                    kpi_card("Entry", fmt_rupees(setup["entry"]))
                with s2:
                    kpi_card("Stop Loss", fmt_rupees(setup["stop"]))
                with s3:
                    kpi_card("Target", fmt_rupees(setup["target"]))
                rr = 0.0
                stop = safe_float(setup["stop"])
                entry = safe_float(setup["entry"])
                target = safe_float(setup["target"])
                if stop and entry:
                    risk_amt = abs(entry - stop)
                    rr = abs(target - entry) / risk_amt if risk_amt > 0 else 0.0
                st.caption(f"Risk/reward ≈ {rr:.2f}:1")

            # ── Export + copy ──────────────────────────────────────
            st.markdown("##### 💾 Export Analysis")
            _render_ai_export(explanation, analysis, symbol, regime)

            # Cross-page: open Advanced Charts / Analyze for this symbol
            st.markdown("##### 🔗 Jump To")
            j1, j2 = st.columns(2)
            with j1:
                if st.button("📈 Open Advanced Charts", use_container_width=True, key="ai_open_charts"):
                    st.session_state["current_symbol"] = symbol
                    st.session_state["page"] = "advanced_charts"
                    st.rerun()
            with j2:
                if st.button("📊 Full Analysis", use_container_width=True, key="ai_open_analyze"):
                    st.session_state["current_symbol"] = symbol
                    st.session_state["page"] = "analyze"
                    st.rerun()

    # ── Tab 2: Market summary ─────────────────────────────────────
    with tabs[1]:
        st.caption("Uses live market data via DataService when available.")
        if st.button("Generate Market Summary", type="primary", key="ai_market_btn"):
            try:
                from src.data import DataService

                svc = DataService()
                summary = svc.get_market_summary()
                gainers = svc.get_top_gainers(limit=5)
                losers = svc.get_top_losers(limit=5)

                regime = "UNKNOWN"
                try:
                    from src.regime.detector import MarketRegimeDetector

                    hist = svc.get_nepse_index_history(days=120)
                    if hist is not None and not hist.empty:
                        regime = MarketRegimeDetector().detect(hist).regime
                except Exception:
                    pass

                commentary = MarketSummaryGenerator().generate(
                    summary, top_gainers=gainers, top_losers=losers, regime=regime
                )
                st.markdown(f"### {commentary.headline}")
                for para in commentary.paragraphs:
                    st.markdown(f"- {para}")
                st.caption(f"Sentiment: {commentary.sentiment}")

                # Notification Center: market AI event (Part 13)
                try:
                    from src.ui.notifications import notification_manager

                    notification_manager.notify_market(
                        f"AI Market Summary: {commentary.sentiment}",
                        message=commentary.headline,
                    )
                except Exception:
                    pass
            except Exception as exc:
                st.warning(f"Live market unavailable ({exc}). Showing cached/mock data.")

    # ── Tab 3: Portfolio review ───────────────────────────────────
    with tabs[2]:
        st.caption("Enter holdings to get a plain-language portfolio review.")
        holdings_text = st.text_area(
            "Holdings (symbol,qty,price per line)",
            value="NABIL,100,500\nNRIC,50,350",
            key="ai_holdings",
        )
        cash = st.number_input("Cash", value=100_000.0, key="ai_cash")

        if st.button("Review Portfolio", type="primary", key="ai_portfolio_btn"):
            holdings: list[dict[str, Any]] = []
            for line in holdings_text.strip().splitlines():
                parts = [p.strip() for p in line.split(",")]
                if len(parts) >= 3:
                    holdings.append(
                        {
                            "symbol": parts[0],
                            "quantity": safe_float(parts[1]),
                            "current_price": safe_float(parts[2]),
                        }
                    )
            review = PortfolioReviewer().review(holdings=holdings, cash=cash)
            st.markdown(f"### {review.summary}")
            st.markdown("##### Observations")
            for obs in review.observations:
                st.markdown(f"- {obs}")
            if review.risks:
                st.markdown("##### ⚠️ Risks")
                for risk in review.risks:
                    st.markdown(f"- {risk}")
            st.markdown("##### Suggestions")
            for suggestion in review.suggestions:
                st.markdown(f"- {suggestion}")


# ── Visualisation helpers ─────────────────────────────────────────


def _render_confidence_gauge(confidence: float) -> None:
    """Render a Plotly gauge for the AI signal confidence."""
    try:
        import plotly.graph_objects as go

        fig = go.Figure(
            go.Indicator(
                mode="gauge+number",
                value=float(confidence),
                number={"suffix": "%", "font": {"color": "#FFFFFF", "size": 28}},
                title={"text": "Confidence", "font": {"color": "#9DA3B0", "size": 14}},
                gauge={
                    "axis": {"range": [0, 100], "tickcolor": "#9DA3B0"},
                    "bar": {"color": "#1F77B4"},
                    "steps": [
                        {"range": [0, 40], "color": "#FF525233"},
                        {"range": [40, 70], "color": "#FFC10733"},
                        {"range": [70, 100], "color": "#00C85333"},
                    ],
                    "threshold": {
                        "line": {"color": "#00C853", "width": 4},
                        "thickness": 0.75,
                        "value": 70,
                    },
                },
            )
        )
        fig.update_layout(
            paper_bgcolor="rgba(0,0,0,0)",
            plot_bgcolor="rgba(0,0,0,0)",
            height=220,
            margin=dict(l=20, r=20, t=40, b=10),
        )
        st.plotly_chart(fig, use_container_width=True)
    except Exception:
        kpi_card("Confidence", fmt_pct(safe_float(confidence) / 100))


def _render_probability_bar(confidence: float, signal: str) -> None:
    """Render a stacked horizontal bar showing bull/bear probability split."""
    try:
        import plotly.graph_objects as go

        bull = max(5.0, float(confidence))
        bear = 100.0 - bull
        color = "#00C853" if signal in ("BUY", "STRONG_BUY") else "#FF5252"
        fig = go.Figure(
            go.Bar(
                x=[bull, bear],
                y=["Direction"],
                orientation="h",
                marker_color=[color, "#2D3138"],
                text=[f"Bull {bull:.0f}%", f"Bear {bear:.0f}%"],
                textposition="inside",
                textfont=dict(color="#FFFFFF"),
            )
        )
        fig.update_layout(
            barmode="stack",
            paper_bgcolor="rgba(0,0,0,0)",
            plot_bgcolor="rgba(0,0,0,0)",
            showlegend=False,
            height=120,
            margin=dict(l=20, r=20, t=20, b=20),
            xaxis=dict(range=[0, 100], showticklabels=False, showgrid=False),
            yaxis=dict(showticklabels=False, showgrid=False),
        )
        st.plotly_chart(fig, use_container_width=True)
        st.caption(f"Signal: {signal} — directional conviction")
    except Exception:
        st.progress(min(float(confidence) / 100.0, 1.0))


def _render_ai_export(
    explanation: Any,
    analysis: dict[str, Any],
    symbol: str,
    regime: str,
) -> None:
    """Render export (JSON / HTML / PDF) and copy buttons for an AI analysis."""
    from datetime import datetime

    from src.data.export import ExportCenter

    payload: dict[str, Any] = {
        "type": "ai_analysis",
        "symbol": symbol.upper(),
        "regime": regime,
        "generated": datetime.now().isoformat(),
        "explanation": explanation.to_dict(),
        "input_analysis": {k: v for k, v in analysis.items() if isinstance(v, (int, float, str, type(None)))},
    }
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")

    col1, col2, col3, col4 = st.columns(4)
    with col1:
        st.download_button(
            "📥 JSON",
            data=ExportCenter.to_json(payload),
            file_name=f"ai_{symbol.lower()}_{ts}.json",
            mime="application/json",
            use_container_width=True,
            key=f"ai_export_json_{symbol}",
        )
    with col2:
        sections = [
            {
                "title": "Signal",
                "headers": ["Field", "Value"],
                "rows": [
                    ["Symbol", symbol.upper()],
                    ["Signal", explanation.signal],
                    ["Confidence", f"{explanation.confidence:.1f}%"],
                    ["Regime", regime],
                    ["Summary", explanation.summary],
                    ["Recommendation", explanation.recommendation],
                ],
            },
            {
                "title": "Reasons",
                "headers": ["#", "Reason"],
                "rows": [[str(i + 1), r] for i, r in enumerate(explanation.reasons)],
            },
            {
                "title": "Risks",
                "headers": ["#", "Risk"],
                "rows": [[str(i + 1), r] for i, r in enumerate(explanation.risks)] or [["—", "None"]],
            },
        ]
        st.download_button(
            "📄 HTML",
            data=ExportCenter.to_html(f"AI Analysis — {symbol.upper()}", sections).encode("utf-8"),
            file_name=f"ai_{symbol.lower()}_{ts}.html",
            mime="text/html",
            use_container_width=True,
            key=f"ai_export_html_{symbol}",
        )
    with col3:
        st.download_button(
            "📕 PDF",
            data=ExportCenter.to_pdf(f"AI Analysis — {symbol.upper()}", sections),
            file_name=f"ai_{symbol.lower()}_{ts}.pdf",
            mime="application/pdf",
            use_container_width=True,
            key=f"ai_export_pdf_{symbol}",
        )
    with col4:
        copy_text = (
            f"{symbol.upper()} → {explanation.signal} ({explanation.confidence:.0f}%)\n\n"
            f"Summary: {explanation.summary}\n"
            f"Recommendation: {explanation.recommendation}\n\n"
            f"Reasons:\n" + "\n".join(f"• {r}" for r in explanation.reasons)
        )
        st.download_button(
            "📋 Copy",
            data=copy_text.encode("utf-8"),
            file_name=f"ai_{symbol.lower()}_{ts}.txt",
            mime="text/plain",
            use_container_width=True,
            key=f"ai_copy_{symbol}",
        )
