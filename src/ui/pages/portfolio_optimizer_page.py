"""Portfolio Optimizer page — MPT, efficient frontier, risk parity, Kelly."""

from __future__ import annotations

from typing import Any

import pandas as pd
import streamlit as st

from src.ui.helpers import section_header, safe_float, fmt_pct
from src.ui.components import kpi_card


def _sample_returns(n_periods: int = 120, seed: int = 42) -> pd.DataFrame:
    """Build a small deterministic returns frame for demos."""
    import numpy as np

    rng = np.random.default_rng(seed)
    assets = ["NABIL", "NRIC", "NTC", "ADBL"]
    data: dict[str, list[float]] = {}
    for asset in assets:
        drift = rng.normal(0.0004, 0.0002)
        data[asset] = list(rng.normal(drift, 0.02, n_periods))
    return pd.DataFrame(data)


@st.cache_data(show_spinner=False, ttl=300)
def _cached_sample_returns(n_periods: int = 120, seed: int = 42) -> pd.DataFrame:
    """Cached variant of :func:`_sample_returns` (Part 8 — performance).

    Deterministic demo data is regenerated at most every 5 minutes.
    """
    return _sample_returns(n_periods=n_periods, seed=seed)


# ── Pure helpers (testable without Streamlit) ─────────────────────


def diversification_score(weights: dict[str, float]) -> float:
    """Compute the effective number of assets (Herfindahl inverse).

    ``1 / sum(w_i^2)`` — a value of 1 means full concentration; higher
    means better diversification (max = number of assets).

    Args:
        weights: Mapping of asset → weight (may not sum to 1).

    Returns:
        The effective number of assets (0 when empty).
    """
    values = [float(v) for v in weights.values() if v]
    if not values:
        return 0.0
    total = sum(values)
    if total <= 0:
        return 0.0
    normalized = [v / total for v in values]
    hhi = sum(v * v for v in normalized)
    return round(1.0 / hhi if hhi > 0 else 0.0, 3)


def allocation_drift(
    current: dict[str, float],
    target: dict[str, float],
) -> dict[str, float]:
    """Compute the absolute weight drift per asset (target − current).

    Args:
        current: Current portfolio weights per asset.
        target: Target (optimal) weights per asset.

    Returns:
        Mapping of asset → signed drift (positive = underweight).
    """
    assets = set(current) | set(target)
    return {
        a: round(float(target.get(a, 0.0)) - float(current.get(a, 0.0)), 4)
        for a in assets
    }


def rebalancing_suggestions(
    current: dict[str, float],
    target: dict[str, float],
    threshold: float = 0.05,
) -> list[dict[str, Any]]:
    """Build rebalancing suggestions for assets whose drift exceeds *threshold*.

    Args:
        current: Current weights per asset.
        target: Target weights per asset.
        threshold: Absolute drift that triggers a suggestion.

    Returns:
        List of dicts with ``symbol``, ``current``, ``target``, ``drift``,
        and ``action`` ("Add" / "Trim").
    """
    drift = allocation_drift(current, target)
    suggestions: list[dict[str, Any]] = []
    for symbol, d in drift.items():
        if abs(d) >= threshold:
            suggestions.append({
                "symbol": symbol,
                "current": round(float(current.get(symbol, 0.0)), 4),
                "target": round(float(target.get(symbol, 0.0)), 4),
                "drift": d,
                "action": "Add" if d > 0 else "Trim",
            })
    return suggestions


def render() -> None:
    """Render the Portfolio Optimizer page."""
    section_header(
        "⚖️ Portfolio Optimizer",
        "Modern Portfolio Theory, efficient frontier, risk parity, and Kelly",
    )

    tabs = st.tabs(["📈 MPT Optimizer", "⚖️ Risk Parity", "🎯 Kelly Criterion"])

    # ── Tab 1: MPT ────────────────────────────────────────────────
    with tabs[0]:
        col1, col2 = st.columns(2)
        with col1:
            objective = st.selectbox(
                "Objective",
                ["sharpe", "min_variance", "equally_weighted"],
                key="po_objective",
            )
            risk_free = st.number_input("Risk-free rate (%)", value=0.0, key="po_rf")
            n_points = st.number_input("Frontier points", value=30, key="po_points")
        with col2:
            st.caption("Use live portfolio prices or the demo dataset.")
            use_demo = st.checkbox("Use demo returns", value=True, key="po_demo")

        if st.button("Optimize Portfolio", type="primary", key="po_run"):
            try:
                if use_demo:
                    returns = _cached_sample_returns()
                else:
                    returns = _live_returns()
                if returns is None or returns.shape[1] < 2 or len(returns) < 30:
                    st.warning("Need at least 2 assets and 30 return periods.")
                    return

                from src.optimization.mpt import MarkowitzOptimizer

                optimizer = MarkowitzOptimizer(
                    returns, risk_free_rate=risk_free / 100
                )
                best = optimizer.optimize(objective)
                frontier = optimizer.efficient_frontier(n_points=int(n_points))
                random_portfolios = optimizer.random_portfolios(
                    n_portfolios=500, seed=7
                )

                st.session_state["po_best"] = best
                st.session_state["po_frontier"] = frontier
                st.session_state["po_random"] = random_portfolios
                st.session_state["po_assets"] = optimizer.assets
                st.session_state["po_returns"] = returns
            except Exception as exc:
                st.error(f"Optimization failed: {exc}")

        if st.session_state.get("po_best"):
            best = st.session_state["po_best"]
            frontier = st.session_state.get("po_frontier")
            returns = st.session_state.get("po_returns")
            cols = st.columns(4)
            with cols[0]:
                kpi_card("Expected Return", fmt_pct(best.expected_return))
            with cols[1]:
                kpi_card("Volatility", fmt_pct(best.volatility))
            with cols[2]:
                kpi_card("Sharpe", f"{best.sharpe:.3f}")
            with cols[3]:
                kpi_card("Assets", str(len(best.assets)))

            st.markdown("#### Optimal Weights")
            weights = best.to_dict()["weights"]
            st.dataframe(
                pd.DataFrame(
                    [{"Asset": k, "Weight": v} for k, v in weights.items()]
                ),
                use_container_width=True,
                hide_index=True,
            )

            st.markdown("#### Efficient Frontier")
            try:
                import plotly.graph_objects as go

                fig = go.Figure()
                random_pfs = st.session_state.get("po_random", [])
                if random_pfs:
                    fig.add_trace(
                        go.Scatter(
                            x=[p.volatility for p in random_pfs],
                            y=[p.expected_return for p in random_pfs],
                            mode="markers",
                            name="Random portfolios",
                            marker=dict(size=4, opacity=0.5, color="#888"),
                        )
                    )
                if frontier is not None:
                    fig.add_trace(
                        go.Scatter(
                            x=frontier.volatilities,
                            y=frontier.returns,
                            mode="lines",
                            name="Efficient frontier",
                            line=dict(color="#1F77B4", width=3),
                        )
                    )
                fig.add_trace(
                    go.Scatter(
                        x=[best.volatility],
                        y=[best.expected_return],
                        mode="markers",
                        name="Optimal",
                        marker=dict(size=12, color="#FF5252", symbol="star"),
                    )
                )
                fig.update_layout(
                    xaxis_title="Volatility",
                    yaxis_title="Expected Return",
                    paper_bgcolor="rgba(0,0,0,0)",
                    plot_bgcolor="rgba(0,0,0,0)",
                    font=dict(color="#FFFFFF"),
                    height=450,
                    hovermode="closest",
                )
                # Efficient Frontier hover details (Part 3)
                if frontier is not None and frontier.weights:
                    hovertext = []
                    for w in frontier.weights:
                        parts = [
                            f"{asset}: {float(wi):.2f}"
                            for asset, wi in zip(st.session_state.get("po_assets", []), w)
                        ]
                        hovertext.append("<br>".join(parts))
                    # Target the frontier trace by name (its index shifts
                    # when the random-portfolios trace is absent).
                    for trace in fig.data:
                        if trace.name == "Efficient frontier":
                            trace.hovertext = hovertext
                            trace.hoverinfo = "x+y+text"
                            break
                st.plotly_chart(fig, use_container_width=True)

                # ── Correlation heatmap + covariance (Part 3) ────
                if returns is not None and not returns.empty:
                    _render_correlation_and_covariance(returns)

                # ── Diversification score + drift + rebalancing ──
                _render_drift_analysis(best)
            except Exception as exc:
                st.info(f"Frontier chart unavailable: {exc}")

    # ── Tab 2: Risk parity ────────────────────────────────────────
    with tabs[1]:
        if st.button("Compute Risk Parity", key="po_rp"):
            try:
                if st.checkbox("Use demo returns", value=True, key="po_rp_demo"):
                    returns = _cached_sample_returns()
                else:
                    returns = _live_returns()
                if returns is None or returns.shape[1] < 2:
                    st.warning("Insufficient data.")
                    return
                from src.optimization.risk_parity import RiskParityOptimizer

                result = RiskParityOptimizer(returns).optimize()
                weights_df = pd.DataFrame(
                    [
                        {"Asset": k, "Weight": v}
                        for k, v in result["weights"].items()
                    ]
                )
                st.dataframe(weights_df, use_container_width=True, hide_index=True)
                st.caption(
                    f"Portfolio volatility: {result['portfolio_volatility']:.4f}"
                )
            except Exception as exc:
                st.error(f"Risk parity failed: {exc}")

    # ── Tab 3: Kelly ──────────────────────────────────────────────
    with tabs[2]:
        col1, col2, col3 = st.columns(3)
        with col1:
            win_rate = st.number_input("Win rate (%)", value=55.0, key="po_k_wr")
        with col2:
            avg_win = st.number_input("Avg win (₹)", value=100.0, key="po_k_aw")
        with col3:
            avg_loss = st.number_input("Avg loss (₹)", value=50.0, key="po_k_al")

        if st.button("Compute Kelly", key="po_kelly"):
            try:
                from src.optimization.kelly import KellyCriterion

                result = KellyCriterion().calculate(
                    win_rate=win_rate / 100, avg_win=avg_win, avg_loss=avg_loss
                )
                cols = st.columns(3)
                with cols[0]:
                    kpi_card("Full Kelly", fmt_pct(result.full_kelly))
                with cols[1]:
                    kpi_card("Half Kelly", fmt_pct(result.half_kelly))
                with cols[2]:
                    kpi_card("Quarter Kelly", fmt_pct(result.quarter_kelly))
                for note in result.notes:
                    st.caption(f"- {note}")
            except Exception as exc:
                st.error(str(exc))


def _render_correlation_and_covariance(returns: pd.DataFrame) -> None:
    """Render correlation heatmap and covariance matrix (Part 3)."""
    try:
        import plotly.graph_objects as go

        corr = returns.corr().round(3)
        cov = returns.cov().round(5)

        st.markdown("#### Correlation Heatmap")
        fig = go.Figure(
            go.Heatmap(
                z=corr.values,
                x=list(corr.columns),
                y=list(corr.index),
                colorscale="RdBu",
                zmid=0,
                text=[[str(v) for v in row] for row in corr.values],
                texttemplate="%{text}",
            )
        )
        fig.update_layout(
            paper_bgcolor="rgba(0,0,0,0)",
            plot_bgcolor="rgba(0,0,0,0)",
            font=dict(color="#FFFFFF"),
            height=380,
        )
        st.plotly_chart(fig, use_container_width=True)

        st.markdown("#### Covariance Matrix")
        st.dataframe(cov, use_container_width=True)
    except Exception as exc:
        st.info(f"Correlation/covariance unavailable: {exc}")


def _render_drift_analysis(best: Any) -> None:
    """Show diversification score, allocation drift, and rebalancing (Part 3)."""
    try:
        weights = best.to_dict()["weights"]
        score = diversification_score(weights)
        kpi_card("🌐 Diversification Score", f"{score:.2f} assets")

        # Current portfolio weights (from session state) vs optimal
        portfolio = st.session_state.get("portfolio", {"holdings": []})
        holdings = portfolio.get("holdings", [])
        if holdings:
            invested = sum(
                safe_float(
                    h.get(
                        "investment",
                        safe_float(h.get("quantity", 0)) * safe_float(h.get("average_price", 0)),
                    )
                )
                for h in holdings
            )
            current: dict[str, float] = {}
            for h in holdings:
                sym = str(h.get("symbol", ""))
                if not sym:
                    continue
                val = safe_float(
                    h.get(
                        "current_value",
                        safe_float(h.get("quantity", 0)) * safe_float(h.get("average_price", 0)),
                    )
                )
                current[sym] = val / invested if invested else 0.0

            target = dict(weights)
            suggestions = rebalancing_suggestions(current, target, threshold=0.05)
            if suggestions:
                st.markdown("#### Rebalancing Suggestions")
                rows = [
                    {
                        "Symbol": s["symbol"],
                        "Current": f"{s['current']:.1%}",
                        "Target": f"{s['target']:.1%}",
                        "Drift": f"{s['drift']:+.1%}",
                        "Action": s["action"],
                    }
                    for s in suggestions
                ]
                st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
                # One-shot notification: only once per optimization run
                if not st.session_state.get("_po_rebalance_notified"):
                    try:
                        from src.ui.notifications import notification_manager

                        notification_manager.notify_rebalance(
                            f"Rebalance: {len(suggestions)} asset(s) outside ±5% target",
                            message=", ".join(s["symbol"] for s in suggestions[:5]),
                        )
                        st.session_state["_po_rebalance_notified"] = True
                    except Exception:
                        pass
            else:
                st.success("✅ Portfolio is within ±5% of the optimal allocation.")
        else:
            st.caption(
                "Add holdings in the Portfolio page to see allocation drift vs the optimal."
            )
    except Exception as exc:
        st.info(f"Drift analysis unavailable: {exc}")


def _live_returns() -> pd.DataFrame | None:
    """Build a returns frame from live watchlist prices (best effort).

    Symbols come from the current Portfolio holdings first, then the
    watchlist (Part 13 — the optimizer receives the current portfolio).
    """
    try:
        import numpy as np

        from src.data import DataService

        svc = DataService()
        portfolio = st.session_state.get("portfolio", {"holdings": []})
        holdings = portfolio.get("holdings", [])
        symbols = [str(h.get("symbol", "")) for h in holdings if h.get("symbol")]
        if not symbols:
            symbols = svc.get_watchlist()
        if not symbols:
            return None
        data: dict[str, list[float]] = {}
        for symbol in symbols[:8]:
            history = svc.get_history(symbol, days=120)
            if history.is_empty or "Close" not in history.df.columns:
                continue
            closes = history.df["Close"].dropna()
            if len(closes) < 30:
                continue
            data[symbol] = list(closes.pct_change().dropna().tolist()[-120:])
        return pd.DataFrame(data) if data else None
    except Exception:
        return None
