"""Plotly chart generators for the NEPSE Quant Engine Dashboard.

Every chart function returns a ``plotly.graph_objects.Figure`` that can
be displayed in a browser, embedded in a Streamlit app, or saved to HTML.

Requires ``plotly>=5.18.0``.
"""

from __future__ import annotations

from typing import Any

from src.dashboard.metrics import drawdown_curve

try:
    import plotly.graph_objects as go
    import plotly.express as px

    _HAS_PLOTLY = True
except ImportError:  # pragma: no cover
    _HAS_PLOTLY = False


# ======================================================================
# Colour palette
# ======================================================================

_COLORS = {
    "bull": "#22c55e",
    "bear": "#ef4444",
    "sideways": "#eab308",
    "high_vol": "#f97316",
    "low_vol": "#06b6d4",
    "accumulation": "#a855f7",
    "distribution": "#ec4899",
    "recovery": "#22d3ee",
    "panic": "#dc2626",
    "overheated": "#f97316",
    "profit": "#22c55e",
    "loss": "#ef4444",
    "neutral": "#8892a8",
    "primary": "#3b82f6",
    "secondary": "#a855f7",
    "grid": "rgba(30,41,59,0.3)",
}

_TEMPLATE = "plotly_dark"


def _check_plotly() -> None:
    """Raise ImportError if Plotly is not available."""
    if not _HAS_PLOTLY:
        raise ImportError(
            "Plotly is required for chart generation. "
            "Install it with: pip install plotly>=5.18.0"
        )


def _layout(
    title: str,
    xlabel: str | None = None,
    ylabel: str | None = None,
    height: int = 400,
    show_legend: bool = True,
    **kwargs: Any,
) -> dict[str, Any]:
    """Build a standard layout dict for dashboard charts."""
    layout: dict[str, Any] = {
        "title": {"text": title, "font": {"size": 14}},
        "template": _TEMPLATE,
        "height": height,
        "margin": {"l": 60, "r": 20, "t": 40, "b": 60},
        "paper_bgcolor": "rgba(0,0,0,0)",
        "plot_bgcolor": "rgba(0,0,0,0)",
        "hovermode": "x unified",
        "showlegend": show_legend,
        "xaxis": {
            "gridcolor": _COLORS["grid"],
            "zerolinecolor": _COLORS["grid"],
        },
        "yaxis": {
            "gridcolor": _COLORS["grid"],
            "zerolinecolor": _COLORS["grid"],
        },
    }
    if xlabel:
        layout["xaxis"]["title"] = {"text": xlabel, "font": {"size": 11}}
    if ylabel:
        layout["yaxis"]["title"] = {"text": ylabel, "font": {"size": 11}}
    layout.update(kwargs)
    return layout


# ======================================================================
# 1. Portfolio Allocation Pie Chart
# ======================================================================


def allocation_pie(
    labels: list[str],
    values: list[float],
    title: str = "Portfolio Allocation",
) -> go.Figure:
    """Create a pie chart showing portfolio allocation.

    Args:
        labels: Slice labels (e.g. stock symbols or sector names).
        values: Slice values (e.g. invested amount or weight).
        title: Chart title.

    Returns:
        Plotly pie chart figure.
    """
    _check_plotly()
    fig = go.Figure(
        data=go.Pie(
            labels=labels,
            values=values,
            hole=0.4,
            marker={"line": {"color": "#111827", "width": 2}},
            textinfo="label+percent",
            textfont={"size": 11},
        ),
        layout=_layout(title, show_legend=True),
    )
    return fig


# ======================================================================
# 2. Equity Curve Line Chart
# ======================================================================


def equity_curve(
    dates: list[str],
    values: list[float],
    title: str = "Equity Curve",
) -> go.Figure:
    """Create an equity curve line chart.

    Args:
        dates: X-axis date labels.
        values: Y-axis portfolio values.
        title: Chart title.

    Returns:
        Plotly line chart figure.
    """
    _check_plotly()
    fig = go.Figure(
        data=go.Scatter(
            x=dates,
            y=values,
            mode="lines",
            name="Portfolio Value",
            line={"color": _COLORS["primary"], "width": 2},
            fill="tozeroy",
            fillcolor="rgba(59,130,246,0.1)",
        ),
        layout=_layout(title, xlabel="Date", ylabel="Value (NPR)"),
    )
    return fig


# ======================================================================
# 3. Drawdown Chart
# ======================================================================


def drawdown_chart(
    dates: list[str],
    equity_values: list[float],
    title: str = "Drawdown",
) -> go.Figure:
    """Create a drawdown chart from equity values.

    Args:
        dates: X-axis date labels.
        equity_values: Portfolio equity values.
        title: Chart title.

    Returns:
        Plotly area chart figure.
    """
    _check_plotly()
    dd = drawdown_curve(equity_values)
    fig = go.Figure(
        data=go.Scatter(
            x=dates[: len(dd)],
            y=dd,
            mode="lines",
            name="Drawdown %",
            line={"color": _COLORS["loss"], "width": 1.5},
            fill="tozeroy",
            fillcolor="rgba(239,68,68,0.15)",
        ),
        layout=_layout(title, xlabel="Date", ylabel="Drawdown (%)"),
    )
    return fig


# ======================================================================
# 4. Monthly Returns Heatmap
# ======================================================================


def monthly_returns_heatmap(
    monthly_data: dict[str, dict[str, float]],
    title: str = "Monthly Returns",
) -> go.Figure:
    """Create a monthly returns heatmap.

    Args:
        monthly_data: Nested dict ``{year: {month: return_pct}}``.
        title: Chart title.

    Returns:
        Plotly heatmap figure.
    """
    _check_plotly()
    months_order = ["01", "02", "03", "04", "05", "06", "07", "08", "09", "10", "11", "12"]
    month_labels = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]

    years = sorted(monthly_data.keys())
    z: list[list[float]] = []
    for year in years:
        row: list[float] = []
        for m in months_order:
            row.append(monthly_data[year].get(m, float("nan")))
        z.append(row)

    fig = go.Figure(
        data=go.Heatmap(
            z=z,
            x=month_labels,
            y=years,
            colorscale="RdYlGn",
            zmid=0,
            texttemplate="%{z:.1f}%",
            textfont={"size": 10},
            hovertemplate="%{y} %{x}: %{z:.2f}%<extra></extra>",
        ),
        layout=_layout(title, show_legend=False, height=300),
    )
    return fig


# ======================================================================
# 5. Trade Distribution Histogram
# ======================================================================


def trade_distribution(
    returns: list[float],
    title: str = "Trade Distribution",
) -> go.Figure:
    """Create a histogram of trade returns.

    Args:
        returns: List of trade return percentages.
        title: Chart title.

    Returns:
        Plotly histogram figure.
    """
    _check_plotly()
    # Plotly's Histogram does not support per-data-point colors natively.
    # Use a single primary colour with automatic binning for clarity;
    # the marker colour will be inherited from the trace colour set.
    fig = go.Figure(
        data=go.Histogram(
            x=returns,
            nbinsx=20,
            marker={
                "color": _COLORS["primary"],
                "line": {"color": "#111827", "width": 0.5},
            },
            opacity=0.85,
        ),
        layout=_layout(title, xlabel="Return (%)", ylabel="Frequency"),
    )
    return fig


# ======================================================================
# 6. Win/Loss Pie Chart
# ======================================================================


def win_loss_pie(
    wins: int,
    losses: int,
    breakeven: int = 0,
    title: str = "Win / Loss",
) -> go.Figure:
    """Create a win/loss/breakeven pie chart.

    Args:
        wins: Number of winning trades.
        losses: Number of losing trades.
        breakeven: Number of breakeven trades.
        title: Chart title.

    Returns:
        Plotly pie chart figure.
    """
    _check_plotly()
    labels: list[str] = []
    values: list[int] = []
    colors: list[str] = []

    if wins:
        labels.append("Wins")
        values.append(wins)
        colors.append(_COLORS["profit"])
    if losses:
        labels.append("Losses")
        values.append(losses)
        colors.append(_COLORS["loss"])
    if breakeven:
        labels.append("Breakeven")
        values.append(breakeven)
        colors.append(_COLORS["neutral"])

    if not values:
        labels = ["No Trades"]
        values = [1]
        colors = [_COLORS["neutral"]]

    fig = go.Figure(
        data=go.Pie(
            labels=labels,
            values=values,
            marker={"colors": colors, "line": {"color": "#111827", "width": 2}},
            textinfo="label+percent",
            textfont={"size": 11},
            hole=0.4,
        ),
        layout=_layout(title),
    )
    return fig


# ======================================================================
# 7. Strategy Comparison Bar Chart
# ======================================================================


def strategy_comparison(
    strategy_names: list[str],
    metrics: dict[str, list[float]],
    title: str = "Strategy Comparison",
) -> go.Figure:
    """Create a grouped bar chart comparing multiple strategies.

    Args:
        strategy_names: List of strategy names.
        metrics: Dict mapping metric name to list of values per strategy.
        title: Chart title.

    Returns:
        Plotly grouped bar chart figure.
    """
    _check_plotly()
    fig = go.Figure()
    colors = [_COLORS["primary"], _COLORS["profit"], _COLORS["secondary"],
              _COLORS["loss"], _COLORS["sideways"], _COLORS["high_vol"]]

    for i, (metric_name, values) in enumerate(metrics.items()):
        fig.add_trace(go.Bar(
            name=metric_name,
            x=strategy_names,
            y=values,
            marker_color=colors[i % len(colors)],
        ))

    fig.update_layout(
        **_layout(title, xlabel="Strategy", ylabel="Value", barmode="group"),
    )
    return fig


# ======================================================================
# 8. Monte Carlo Equity Paths
# ======================================================================


def monte_carlo_paths(
    curves: list[list[float]],
    best_curve: list[float] | None = None,
    worst_curve: list[float] | None = None,
    average_curve: list[float] | None = None,
    title: str = "Monte Carlo Paths",
) -> go.Figure:
    """Create a Monte Carlo simulation equity path chart.

    Args:
        curves: List of simulated equity curves.
        best_curve: Best-case equity curve.
        worst_curve: Worst-case equity curve.
        average_curve: Average equity curve.
        title: Chart title.

    Returns:
        Plotly line chart figure.
    """
    _check_plotly()
    fig = go.Figure()

    # Faded simulation paths
    for i, curve in enumerate(curves):
        fig.add_trace(go.Scatter(
            x=list(range(len(curve))),
            y=curve,
            mode="lines",
            name=f"Path {i + 1}" if i < 10 else "",
            line={"width": 0.5, "color": "rgba(59,130,246,0.15)"},
            showlegend=i < 10,
        ))

    if best_curve:
        fig.add_trace(go.Scatter(
            x=list(range(len(best_curve))),
            y=best_curve,
            mode="lines",
            name="Best",
            line={"width": 2.5, "color": _COLORS["profit"]},
        ))
    if worst_curve:
        fig.add_trace(go.Scatter(
            x=list(range(len(worst_curve))),
            y=worst_curve,
            mode="lines",
            name="Worst",
            line={"width": 2.5, "color": _COLORS["loss"]},
        ))
    if average_curve:
        fig.add_trace(go.Scatter(
            x=list(range(len(average_curve))),
            y=average_curve,
            mode="lines",
            name="Average",
            line={"width": 2.5, "color": _COLORS["sideways"], "dash": "dash"},
        ))

    fig.update_layout(
        **_layout(title, xlabel="Trade Number", ylabel="Equity (NPR)"),
    )
    return fig


# ======================================================================
# 9. Monte Carlo Confidence Bands
# ======================================================================


def monte_carlo_confidence_bands(
    curves: list[list[float]],
    percentiles: dict[str, float],
    title: str = "Confidence Bands",
) -> go.Figure:
    """Create a Monte Carlo confidence band chart.

    Args:
        curves: List of simulated equity curves.
        percentiles: Dict with percentile values
            (e.g. ``{"p5": ..., "p50": ..., "p95": ...}``).
        title: Chart title.

    Returns:
        Plotly chart with confidence bands.
    """
    _check_plotly()
    fig = go.Figure()

    if curves:
        # Build percentile curves at each step
        max_len = max(len(c) for c in curves)
        step_values: list[list[float]] = []
        for i in range(max_len):
            vals = [c[i] for c in curves if i < len(c)]
            vals.sort()
            step_values.append(vals)

        x_vals = list(range(max_len))

        def _pct(values: list[float], p: float) -> float:
            if not values:
                return 0.0
            idx = int(len(values) * p / 100.0)
            return values[min(idx, len(values) - 1)]

        p5 = [_pct(vals, 5) for vals in step_values]
        p25 = [_pct(vals, 25) for vals in step_values]
        p50 = [_pct(vals, 50) for vals in step_values]
        p75 = [_pct(vals, 75) for vals in step_values]
        p95 = [_pct(vals, 95) for vals in step_values]

        # 50% confidence band (p25-p75)
        fig.add_trace(go.Scatter(
            x=x_vals + x_vals[::-1],
            y=p75 + p25[::-1],
            fill="toself",
            fillcolor="rgba(59,130,246,0.2)",
            line={"width": 0},
            name="50% CI",
        ))

        # 90% confidence band (p5-p95)
        fig.add_trace(go.Scatter(
            x=x_vals + x_vals[::-1],
            y=p95 + p5[::-1],
            fill="toself",
            fillcolor="rgba(59,130,246,0.1)",
            line={"width": 0},
            name="90% CI",
        ))

        fig.add_trace(go.Scatter(
            x=x_vals,
            y=p50,
            mode="lines",
            name="Median",
            line={"width": 2, "color": _COLORS["primary"]},
        ))

    fig.update_layout(
        **_layout(title, xlabel="Trade Number", ylabel="Equity (NPR)"),
    )
    return fig


# ======================================================================
# 10. Efficient Frontier Scatter Plot
# ======================================================================


def efficient_frontier(
    risks: list[float],
    returns: list[float],
    optimal_risk: float | None = None,
    optimal_return: float | None = None,
    title: str = "Efficient Frontier",
) -> go.Figure:
    """Create an efficient frontier scatter plot.

    Args:
        risks: List of portfolio risk values.
        returns: List of portfolio return values.
        optimal_risk: Risk of the optimal portfolio.
        optimal_return: Return of the optimal portfolio.
        title: Chart title.

    Returns:
        Plotly scatter chart figure.
    """
    _check_plotly()
    fig = go.Figure()

    fig.add_trace(go.Scatter(
        x=risks,
        y=returns,
        mode="markers",
        name="Portfolios",
        marker={"color": _COLORS["primary"], "size": 6, "opacity": 0.7},
    ))

    if optimal_risk is not None and optimal_return is not None:
        fig.add_trace(go.Scatter(
            x=[optimal_risk],
            y=[optimal_return],
            mode="markers",
            name="Optimal",
            marker={"color": _COLORS["profit"], "size": 14, "symbol": "star"},
        ))

    fig.update_layout(
        **_layout(title, xlabel="Risk (Volatility)", ylabel="Expected Return"),
    )
    return fig


# ======================================================================
# 11. Portfolio Weights Bar Chart
# ======================================================================


def portfolio_weights(
    symbols: list[str],
    weights: list[float],
    title: str = "Portfolio Weights",
) -> go.Figure:
    """Create a bar chart of portfolio weights.

    Args:
        symbols: Stock symbols.
        weights: Allocation weights (summing to 1.0).
        title: Chart title.

    Returns:
        Plotly bar chart figure.
    """
    _check_plotly()
    colors = [_COLORS["profit"] if w > 0 else _COLORS["loss"] for w in weights]
    fig = go.Figure(
        data=go.Bar(
            x=symbols,
            y=weights,
            marker_color=colors,
            text=[f"{w:.1%}" for w in weights],
            textposition="outside",
        ),
        layout=_layout(title, xlabel="Symbol", ylabel="Weight"),
    )
    return fig


# ======================================================================
# 12. Risk Contribution Pie Chart
# ======================================================================


def risk_contribution_pie(
    labels: list[str],
    contributions: list[float],
    title: str = "Risk Contribution",
) -> go.Figure:
    """Create a risk contribution pie/donut chart.

    Args:
        labels: Slice labels (sectors or symbols).
        contributions: Risk contribution values.
        title: Chart title.

    Returns:
        Plotly pie chart figure.
    """
    _check_plotly()
    fig = go.Figure(
        data=go.Pie(
            labels=labels,
            values=contributions,
            hole=0.4,
            marker={"line": {"color": "#111827", "width": 2}},
            textinfo="label+percent",
            textfont={"size": 11},
        ),
        layout=_layout(title),
    )
    return fig


# ======================================================================
# 13. Market Regime Timeline
# ======================================================================


def regime_timeline(
    regimes: list[dict[str, Any]],
    title: str = "Market Regime Timeline",
) -> go.Figure:
    """Create a coloured market regime timeline.

    Args:
        regimes: List of regime dicts with ``date`` and ``regime`` keys.
        title: Chart title.

    Returns:
        Plotly scatter/bar chart with regime-coloured segments.
    """
    _check_plotly()
    regime_colors = {
        "BULL": _COLORS["bull"],
        "BEAR": _COLORS["bear"],
        "SIDEWAYS": _COLORS["sideways"],
        "HIGH_VOLATILITY": _COLORS["high_vol"],
        "LOW_VOLATILITY": _COLORS["low_vol"],
        "ACCUMULATION": _COLORS["accumulation"],
        "DISTRIBUTION": _COLORS["distribution"],
        "RECOVERY": _COLORS["recovery"],
        "PANIC": _COLORS["panic"],
        "OVERHEATED": _COLORS["overheated"],
    }

    dates = [r.get("date", "") for r in regimes]
    labels = [r.get("regime", "UNKNOWN") for r in regimes]
    colors = [regime_colors.get(l, _COLORS["neutral"]) for l in labels]

    fig = go.Figure(
        data=go.Scatter(
            x=dates,
            y=[1] * len(regimes),
            mode="markers",
            marker={
                "color": colors,
                "size": 12,
                "symbol": "square",
                "line": {"width": 1, "color": "#111827"},
            },
            text=labels,
            hovertemplate="%{x}<br>Regime: %{text}<extra></extra>",
            name="Regime",
        ),
        layout=_layout(title, xlabel="Date", ylabel="", show_legend=False, height=200),
    )
    fig.update_yaxes(visible=False, showticklabels=False)
    return fig


# ======================================================================
# 14. Optimisation Results Scatter (Risk vs Return)
# ======================================================================


def optimisation_results(
    risks: list[float],
    returns: list[float],
    scores: list[float] | None = None,
    best_idx: int | None = None,
    title: str = "Optimisation Results",
) -> go.Figure:
    """Create an optimisation results scatter plot.

    Args:
        risks: Risk values for each parameter combination.
        returns: Return values for each combination.
        scores: Score values (used for colouring).
        best_idx: Index of the best result.
        title: Chart title.

    Returns:
        Plotly scatter chart figure.
    """
    _check_plotly()
    fig = go.Figure()

    marker: dict[str, Any] = {"size": 8, "opacity": 0.7}
    if scores:
        marker["color"] = scores
        marker["colorscale"] = "Viridis"
        marker["colorbar"] = {"title": "Score", "font": {"size": 10}}
        marker["showscale"] = True

    fig.add_trace(go.Scatter(
        x=risks,
        y=returns,
        mode="markers",
        name="Results",
        marker=marker,
    ))

    if best_idx is not None and best_idx < len(risks):
        fig.add_trace(go.Scatter(
            x=[risks[best_idx]],
            y=[returns[best_idx]],
            mode="markers",
            name="Best",
            marker={"color": _COLORS["profit"], "size": 14, "symbol": "star"},
        ))

    fig.update_layout(
        **_layout(title, xlabel="Risk", ylabel="Return"),
    )
    return fig


# ======================================================================
# 15. Adaptive Strategy Dashboard Radar
# ======================================================================


def adaptive_strategy_radar(
    strategy_names: list[str],
    weights: list[float],
    title: str = "Strategy Weights",
) -> go.Figure:
    """Create a radar/polar chart of strategy weights.

    Args:
        strategy_names: Strategy names.
        weights: Corresponding weights (summing to 1.0).
        title: Chart title.

    Returns:
        Plotly polar chart figure.
    """
    _check_plotly()
    fig = go.Figure(
        data=go.Scatterpolar(
            r=weights + [weights[0]] if weights else [],
            theta=strategy_names + [strategy_names[0]] if strategy_names else [],
            fill="toself",
            name="Weights",
            line={"color": _COLORS["primary"]},
            fillcolor="rgba(59,130,246,0.2)",
        ),
        layout=_layout(title, show_legend=False),
    )
    return fig


# ======================================================================
# 16. Risk Gauge (single value)
# ======================================================================


def risk_gauge(
    value: float,
    title: str = "Risk Gauge",
    max_val: float = 100.0,
    threshold_warn: float = 50.0,
    threshold_danger: float = 80.0,
) -> go.Figure:
    """Create a gauge chart for a risk metric.

    Args:
        value: Current value to display.
        title: Chart title.
        max_val: Maximum value on the gauge.
        threshold_warn: Warning threshold.
        threshold_danger: Danger threshold.

    Returns:
        Plotly gauge/indicator figure.
    """
    _check_plotly()
    fig = go.Figure(
        data=go.Indicator(
            mode="gauge+number+delta",
            value=value,
            title={"text": title, "font": {"size": 14}},
            delta={"reference": threshold_warn},
            gauge={
                "axis": {"range": [0, max_val], "tickwidth": 1},
                "bar": {"color": _COLORS["primary"]},
                "steps": [
                    {"range": [0, threshold_warn], "color": "rgba(34,197,94,0.15)"},
                    {"range": [threshold_warn, threshold_danger], "color": "rgba(234,179,8,0.15)"},
                    {"range": [threshold_danger, max_val], "color": "rgba(239,68,68,0.15)"},
                ],
                "threshold": {
                    "line": {"color": _COLORS["loss"], "width": 4},
                    "thickness": 0.75,
                    "value": threshold_danger,
                },
            },
        ),
        layout=_layout(title, show_legend=False, height=250),
    )
    return fig


# ======================================================================
# 17. Parameter Optimisation Heatmap
# ======================================================================


def parameter_heatmap(
    param_x: list[str],
    param_y: list[str],
    scores: list[list[float]],
    title: str = "Parameter Optimisation",
) -> go.Figure:
    """Create a parameter optimisation heatmap.

    Args:
        param_x: X-axis parameter values.
        param_y: Y-axis parameter values.
        scores: 2D grid of scores.
        title: Chart title.

    Returns:
        Plotly heatmap figure.
    """
    _check_plotly()
    fig = go.Figure(
        data=go.Heatmap(
            z=scores,
            x=param_x,
            y=param_y,
            colorscale="Viridis",
            texttemplate="%{z:.2f}",
            textfont={"size": 9},
            hovertemplate="%{y} × %{x}: %{z:.2f}<extra></extra>",
        ),
        layout=_layout(title, xlabel="Parameter 1", ylabel="Parameter 2"),
    )
    return fig


# ======================================================================
# 18. Walk-Forward Performance Chart
# ======================================================================


def walk_forward_performance(
    window_labels: list[str],
    in_sample_scores: list[float],
    out_sample_scores: list[float],
    title: str = "Walk-Forward Performance",
) -> go.Figure:
    """Create a walk-forward analysis chart.

    Args:
        window_labels: Labels for each walk-forward window.
        in_sample_scores: In-sample performance scores.
        out_sample_scores: Out-of-sample performance scores.
        title: Chart title.

    Returns:
        Plotly line chart figure.
    """
    _check_plotly()
    fig = go.Figure()

    fig.add_trace(go.Scatter(
        x=window_labels,
        y=in_sample_scores,
        mode="lines+markers",
        name="In-Sample",
        line={"color": _COLORS["primary"], "width": 2},
    ))

    fig.add_trace(go.Scatter(
        x=window_labels,
        y=out_sample_scores,
        mode="lines+markers",
        name="Out-of-Sample",
        line={"color": _COLORS["profit"], "width": 2},
    ))

    fig.update_layout(
        **_layout(title, xlabel="Window", ylabel="Score"),
    )
    return fig


# ======================================================================
# 19. Recommendation Signal Gauge
# ======================================================================


def recommendation_gauge(
    confidence: float,
    signal: str = "HOLD",
    title: str = "Recommendation",
) -> go.Figure:
    """Create a gauge chart for a trading recommendation.

    Args:
        confidence: Confidence score (0–100).
        signal: Signal label (BUY, SELL, or HOLD).
        title: Chart title.

    Returns:
        Plotly indicator figure.
    """
    _check_plotly()
    signal_color = {
        "BUY": _COLORS["profit"],
        "SELL": _COLORS["loss"],
        "HOLD": _COLORS["sideways"],
    }.get(signal, _COLORS["neutral"])

    fig = go.Figure(
        data=go.Indicator(
            mode="number+gauge+delta",
            value=confidence,
            title={
                "text": f"{title}<br><span style='font-size:0.8em'>{signal}</span>",
                "font": {"size": 14},
            },
            delta={"reference": 50},
            gauge={
                "axis": {"range": [0, 100], "tickwidth": 1},
                "bar": {"color": signal_color},
                "steps": [
                    {"range": [0, 40], "color": "rgba(239,68,68,0.12)"},
                    {"range": [40, 70], "color": "rgba(234,179,8,0.12)"},
                    {"range": [70, 100], "color": "rgba(34,197,94,0.12)"},
                ],
                "threshold": {
                    "line": {"color": signal_color, "width": 4},
                    "thickness": 0.75,
                    "value": confidence,
                },
            },
        ),
        layout=_layout(title, show_legend=False, height=250),
    )
    return fig


# ======================================================================
# 20. Sector Allocation Treemap
# ======================================================================


def sector_treemap(
    sectors: list[str],
    values: list[float],
    title: str = "Sector Allocation",
) -> go.Figure:
    """Create a treemap of sector allocations.

    Args:
        sectors: Sector names.
        values: Allocation values or percentages.
        title: Chart title.

    Returns:
        Plotly treemap figure.
    """
    _check_plotly()
    fig = go.Figure(
        data=go.Treemap(
            labels=sectors,
            parents=[""] * len(sectors),
            values=values,
            textinfo="label+percent entry",
            textfont={"size": 12},
            marker={"line": {"width": 2}},
            hovertemplate="%{label}: %{value:.1f}%<extra></extra>",
        ),
        layout=_layout(title, show_legend=False),
    )
    return fig
