"""Main Dashboard class for the NEPSE Quant Engine Dashboard.

Provides a unified interface to all dashboard components:

- Portfolio metrics and charts
- Backtest and trade visualisation
- Market regime detection display
- Adaptive strategy dashboard
- Monte Carlo simulation visualisation
- Walk-forward and parameter optimisation charts
- Recommendation and risk dashboards
- Report generation and export
"""

from __future__ import annotations

from typing import Any

from src.dashboard import metrics as _metrics
from src.dashboard import reports as _reports
from src.dashboard import exporter as _exporter
from src.logging.logger import logger


class Dashboard:
    """Unified dashboard interface for the NEPSE Quant Engine.

    Provides convenience methods for computing metrics, generating
    charts, building reports, and exporting results.

    Usage::

        from src.dashboard.dashboard import Dashboard

        dashboard = Dashboard()

        # Compute portfolio summary
        summary = dashboard.portfolio_summary(
            invested=1_000_000, cash=500_000, current_value=1_200_000
        )

        # Generate a console report
        print(dashboard.console_report("executive", summary))

        # Export to JSON
        dashboard.export("json", summary, "report.json")
    """

    # ------------------------------------------------------------------
    # Metrics
    # ------------------------------------------------------------------

    @staticmethod
    def portfolio_summary(
        invested: float = 0.0,
        cash: float = 0.0,
        current_value: float = 0.0,
        trades: list[dict[str, Any]] | None = None,
        equity_curve: list[float] | None = None,
        returns: list[float] | None = None,
    ) -> dict[str, Any]:
        """Compute a comprehensive portfolio summary.

        Args:
            invested: Total amount invested.
            cash: Remaining cash.
            current_value: Current portfolio value.
            trades: Optional list of trade dicts.
            equity_curve: Optional equity curve values.
            returns: Optional list of returns.

        Returns:
            Portfolio summary dict with all metrics.
        """
        return _metrics.portfolio_summary(
            invested=invested,
            cash=cash,
            current_value=current_value,
            trades=trades,
            equity_curve=equity_curve,
            returns=returns,
        )

    @staticmethod
    def win_rate(trades: list[dict[str, Any]]) -> float:
        """Calculate win rate from trades.

        Args:
            trades: List of trade dicts.

        Returns:
            Win rate percentage (0–100).
        """
        return _metrics.win_rate(trades)

    @staticmethod
    def sharpe_ratio(
        returns: list[float],
        risk_free_rate: float = 0.0,
    ) -> float | None:
        """Calculate the Sharpe ratio.

        Args:
            returns: List of returns.
            risk_free_rate: Risk-free rate (default 0.0).

        Returns:
            Sharpe ratio, or ``None`` if insufficient data.
        """
        return _metrics.sharpe_ratio(returns, risk_free_rate)

    @staticmethod
    def max_drawdown(equity_curve: list[float]) -> float:
        """Calculate maximum drawdown.

        Args:
            equity_curve: Ordered equity values.

        Returns:
            Max drawdown percentage.
        """
        return _metrics.max_drawdown(equity_curve)

    @staticmethod
    def drawdown_curve(equity_curve: list[float]) -> list[float]:
        """Calculate the drawdown curve.

        Args:
            equity_curve: Ordered equity values.

        Returns:
            List of drawdown percentages.
        """
        return _metrics.drawdown_curve(equity_curve)

    @staticmethod
    def value_at_risk(
        returns: list[float],
        confidence: float = 0.95,
    ) -> float:
        """Calculate Value at Risk.

        Args:
            returns: List of returns.
            confidence: Confidence level (default 0.95).

        Returns:
            VaR value.
        """
        return _metrics.value_at_risk(returns, confidence)

    @staticmethod
    def conditional_var(
        returns: list[float],
        confidence: float = 0.95,
    ) -> float:
        """Calculate Conditional VaR (Expected Shortfall).

        Args:
            returns: List of returns.
            confidence: Confidence level (default 0.95).

        Returns:
            CVaR value.
        """
        return _metrics.conditional_var(returns, confidence)

    @staticmethod
    def monthly_returns(
        dates: list[str],
        values: list[float],
    ) -> dict[str, dict[str, float]]:
        """Calculate monthly returns from date-value pairs.

        Args:
            dates: Date strings (YYYY-MM-DD).
            values: Portfolio values.

        Returns:
            Nested dict ``{year: {month: return_pct}}``.
        """
        return _metrics.monthly_returns(dates, values)

    # ------------------------------------------------------------------
    # Console reports
    # ------------------------------------------------------------------

    @staticmethod
    def console_report(
        report_type: str = "executive",
        **kwargs: Any,
    ) -> str:
        """Generate a terminal-friendly console report.

        Args:
            report_type:
                Type of report. One of ``"portfolio"``, ``"trade"``,
                ``"strategy"``, ``"weights"``, ``"regime"``,
                ``"allocation"``, ``"executive"``, ``"detailed"``.
            **kwargs: Arguments forwarded to the report generator.

        Returns:
            Formatted report string ready for printing.

        Raises:
            ValueError: If *report_type* is unknown.
        """
        report_map: dict[str, str] = {
            "portfolio": "portfolio",
            "trade": "trade",
            "strategy": "strategy",
            "weights": "weights",
            "regime": "regime",
            "allocation": "allocation",
            "executive": "executive",
            "detailed": "detailed",
        }

        if report_type not in report_map:
            raise ValueError(
                f"Unknown report type '{report_type}'. "
                f"Valid types: {sorted(report_map)}."
            )

        if report_type == "portfolio":
            summary = kwargs.get("summary", {})
            title = kwargs.get("title", "Portfolio Summary")
            return _reports.portfolio_report(summary, title)

        elif report_type == "trade":
            trades = kwargs.get("trades", [])
            title = kwargs.get("title", "Trade Report")
            return _reports.trade_report(trades, title)

        elif report_type == "strategy":
            strategies = kwargs.get("strategies", {})
            title = kwargs.get("title", "Strategy Comparison")
            return _reports.strategy_report(strategies, title)

        elif report_type == "weights":
            weights = kwargs.get("weights", {})
            title = kwargs.get("title", "Strategy Weights")
            return _reports.weights_report(weights, title)

        elif report_type == "regime":
            regime_label = kwargs.get("regime_label", "UNKNOWN")
            confidence = kwargs.get("confidence", 0.0)
            reasons = kwargs.get("reasons")
            metrics = kwargs.get("metrics")
            title = kwargs.get("title", "Market Regime Report")
            return _reports.regime_report(
                regime_label, confidence, reasons, metrics, title
            )

        elif report_type == "allocation":
            allocations = kwargs.get("allocations", [])
            title = kwargs.get("title", "Portfolio Allocations")
            return _reports.allocation_report(allocations, title)

        elif report_type == "executive":
            return _reports.executive_report(
                portfolio_summary=kwargs.get("portfolio_summary", {}),
                regime_label=kwargs.get("regime_label", "UNKNOWN"),
                regime_confidence=kwargs.get("regime_confidence", 0.0),
                trades=kwargs.get("trades"),
                allocations=kwargs.get("allocations"),
            )

        elif report_type == "detailed":
            return _reports.detailed_report(
                portfolio_summary=kwargs.get("portfolio_summary", {}),
                regime_label=kwargs.get("regime_label", "UNKNOWN"),
                regime_confidence=kwargs.get("regime_confidence", 0.0),
                regime_reasons=kwargs.get("regime_reasons"),
                regime_metrics=kwargs.get("regime_metrics"),
                trades=kwargs.get("trades"),
                allocations=kwargs.get("allocations"),
                strategy_weights=kwargs.get("strategy_weights"),
                risk_metrics=kwargs.get("risk_metrics"),
            )

        # Should never reach here
        return ""

    # ------------------------------------------------------------------
    # Chart generation
    # ------------------------------------------------------------------

    @staticmethod
    def allocation_pie(
        labels: list[str],
        values: list[float],
        title: str = "Portfolio Allocation",
    ) -> Any:
        """Generate a portfolio allocation pie chart.

        Requires ``plotly``.  Returns a ``plotly.graph_objects.Figure``.

        Args:
            labels: Slice labels.
            values: Slice values.
            title: Chart title.

        Returns:
            Plotly figure.
        """
        from src.dashboard.charts import allocation_pie as _pie

        return _pie(labels, values, title)

    @staticmethod
    def equity_curve_chart(
        dates: list[str],
        values: list[float],
        title: str = "Equity Curve",
    ) -> Any:
        """Generate an equity curve line chart.

        Args:
            dates: Date labels.
            values: Portfolio values.
            title: Chart title.

        Returns:
            Plotly figure.
        """
        from src.dashboard.charts import equity_curve as _ec

        return _ec(dates, values, title)

    @staticmethod
    def drawdown_chart(
        dates: list[str],
        equity_values: list[float],
        title: str = "Drawdown",
    ) -> Any:
        """Generate a drawdown area chart.

        Args:
            dates: Date labels.
            equity_values: Portfolio equity values.
            title: Chart title.

        Returns:
            Plotly figure.
        """
        from src.dashboard.charts import drawdown_chart as _dd

        return _dd(dates, equity_values, title)

    @staticmethod
    def monthly_returns_heatmap(
        monthly_data: dict[str, dict[str, float]],
        title: str = "Monthly Returns",
    ) -> Any:
        """Generate a monthly returns heatmap.

        Args:
            monthly_data: Nested dict ``{year: {month: return_pct}}``.
            title: Chart title.

        Returns:
            Plotly figure.
        """
        from src.dashboard.charts import monthly_returns_heatmap as _mh

        return _mh(monthly_data, title)

    @staticmethod
    def trade_distribution(
        returns: list[float],
        title: str = "Trade Distribution",
    ) -> Any:
        """Generate a trade return histogram.

        Args:
            returns: Trade return percentages.
            title: Chart title.

        Returns:
            Plotly figure.
        """
        from src.dashboard.charts import trade_distribution as _td

        return _td(returns, title)

    @staticmethod
    def win_loss_pie(
        wins: int,
        losses: int,
        breakeven: int = 0,
        title: str = "Win / Loss",
    ) -> Any:
        """Generate a win/loss pie chart.

        Args:
            wins: Winning trades.
            losses: Losing trades.
            breakeven: Breakeven trades.
            title: Chart title.

        Returns:
            Plotly figure.
        """
        from src.dashboard.charts import win_loss_pie as _wl

        return _wl(wins, losses, breakeven, title)

    @staticmethod
    def strategy_comparison(
        strategy_names: list[str],
        metrics: dict[str, list[float]],
        title: str = "Strategy Comparison",
    ) -> Any:
        """Generate a strategy comparison bar chart.

        Args:
            strategy_names: Strategy names.
            metrics: Dict mapping metric name to values per strategy.
            title: Chart title.

        Returns:
            Plotly figure.
        """
        from src.dashboard.charts import strategy_comparison as _sc

        return _sc(strategy_names, metrics, title)

    @staticmethod
    def monte_carlo_paths(
        curves: list[list[float]],
        best_curve: list[float] | None = None,
        worst_curve: list[float] | None = None,
        average_curve: list[float] | None = None,
        title: str = "Monte Carlo Paths",
    ) -> Any:
        """Generate a Monte Carlo path chart.

        Args:
            curves: Simulated equity curves.
            best_curve: Best-case curve.
            worst_curve: Worst-case curve.
            average_curve: Average curve.
            title: Chart title.

        Returns:
            Plotly figure.
        """
        from src.dashboard.charts import monte_carlo_paths as _mc

        return _mc(curves, best_curve, worst_curve, average_curve, title)

    @staticmethod
    def monte_carlo_confidence_bands(
        curves: list[list[float]],
        percentiles: dict[str, float],
        title: str = "Confidence Bands",
    ) -> Any:
        """Generate a Monte Carlo confidence band chart.

        Args:
            curves: Simulated equity curves.
            percentiles: Dict of percentile values.
            title: Chart title.

        Returns:
            Plotly figure.
        """
        from src.dashboard.charts import monte_carlo_confidence_bands as _mcb

        return _mcb(curves, percentiles, title)

    @staticmethod
    def efficient_frontier(
        risks: list[float],
        returns: list[float],
        optimal_risk: float | None = None,
        optimal_return: float | None = None,
        title: str = "Efficient Frontier",
    ) -> Any:
        """Generate an efficient frontier scatter plot.

        Args:
            risks: Portfolio risks.
            returns: Portfolio returns.
            optimal_risk: Optimal portfolio risk.
            optimal_return: Optimal portfolio return.
            title: Chart title.

        Returns:
            Plotly figure.
        """
        from src.dashboard.charts import efficient_frontier as _ef

        return _ef(risks, returns, optimal_risk, optimal_return, title)

    @staticmethod
    def portfolio_weights(
        symbols: list[str],
        weights: list[float],
        title: str = "Portfolio Weights",
    ) -> Any:
        """Generate a portfolio weights bar chart.

        Args:
            symbols: Stock symbols.
            weights: Allocation weights.
            title: Chart title.

        Returns:
            Plotly figure.
        """
        from src.dashboard.charts import portfolio_weights as _pw

        return _pw(symbols, weights, title)

    @staticmethod
    def risk_contribution_pie(
        labels: list[str],
        contributions: list[float],
        title: str = "Risk Contribution",
    ) -> Any:
        """Generate a risk contribution pie chart.

        Args:
            labels: Slice labels.
            contributions: Risk contributions.
            title: Chart title.

        Returns:
            Plotly figure.
        """
        from src.dashboard.charts import risk_contribution_pie as _rc

        return _rc(labels, contributions, title)

    @staticmethod
    def regime_timeline(
        regimes: list[dict[str, Any]],
        title: str = "Market Regime Timeline",
    ) -> Any:
        """Generate a market regime timeline chart.

        Args:
            regimes: List of regime dicts with ``date`` and ``regime``.
            title: Chart title.

        Returns:
            Plotly figure.
        """
        from src.dashboard.charts import regime_timeline as _rt

        return _rt(regimes, title)

    @staticmethod
    def optimisation_results(
        risks: list[float],
        returns: list[float],
        scores: list[float] | None = None,
        best_idx: int | None = None,
        title: str = "Optimisation Results",
    ) -> Any:
        """Generate an optimisation results scatter plot.

        Args:
            risks: Risk values.
            returns: Return values.
            scores: Optional score values for colouring.
            best_idx: Index of the best result.
            title: Chart title.

        Returns:
            Plotly figure.
        """
        from src.dashboard.charts import optimisation_results as _or

        return _or(risks, returns, scores, best_idx, title)

    @staticmethod
    def adaptive_strategy_radar(
        strategy_names: list[str],
        weights: list[float],
        title: str = "Strategy Weights",
    ) -> Any:
        """Generate an adaptive strategy radar chart.

        Args:
            strategy_names: Strategy names.
            weights: Strategy weights.
            title: Chart title.

        Returns:
            Plotly figure.
        """
        from src.dashboard.charts import adaptive_strategy_radar as _ar

        return _ar(strategy_names, weights, title)

    @staticmethod
    def risk_gauge(
        value: float,
        title: str = "Risk Gauge",
        max_val: float = 100.0,
        threshold_warn: float = 50.0,
        threshold_danger: float = 80.0,
    ) -> Any:
        """Generate a risk gauge chart.

        Args:
            value: Current value.
            title: Chart title.
            max_val: Gauge maximum.
            threshold_warn: Warning threshold.
            threshold_danger: Danger threshold.

        Returns:
            Plotly figure.
        """
        from src.dashboard.charts import risk_gauge as _rg

        return _rg(value, title, max_val, threshold_warn, threshold_danger)

    @staticmethod
    def parameter_heatmap(
        param_x: list[str],
        param_y: list[str],
        scores: list[list[float]],
        title: str = "Parameter Optimisation",
    ) -> Any:
        """Generate a parameter optimisation heatmap.

        Args:
            param_x: X-axis parameter values.
            param_y: Y-axis parameter values.
            scores: 2D grid of scores.
            title: Chart title.

        Returns:
            Plotly figure.
        """
        from src.dashboard.charts import parameter_heatmap as _ph

        return _ph(param_x, param_y, scores, title)

    @staticmethod
    def walk_forward_performance(
        window_labels: list[str],
        in_sample_scores: list[float],
        out_sample_scores: list[float],
        title: str = "Walk-Forward Performance",
    ) -> Any:
        """Generate a walk-forward performance chart.

        Args:
            window_labels: Window labels.
            in_sample_scores: In-sample scores.
            out_sample_scores: Out-of-sample scores.
            title: Chart title.

        Returns:
            Plotly figure.
        """
        from src.dashboard.charts import walk_forward_performance as _wf

        return _wf(window_labels, in_sample_scores, out_sample_scores, title)

    @staticmethod
    def recommendation_gauge(
        confidence: float,
        signal: str = "HOLD",
        title: str = "Recommendation",
    ) -> Any:
        """Generate a recommendation gauge chart.

        Args:
            confidence: Confidence score (0–100).
            signal: Signal label (BUY, SELL, HOLD).
            title: Chart title.

        Returns:
            Plotly figure.
        """
        from src.dashboard.charts import recommendation_gauge as _rg

        return _rg(confidence, signal, title)

    @staticmethod
    def sector_treemap(
        sectors: list[str],
        values: list[float],
        title: str = "Sector Allocation",
    ) -> Any:
        """Generate a sector allocation treemap.

        Args:
            sectors: Sector names.
            values: Allocation values.
            title: Chart title.

        Returns:
            Plotly figure.
        """
        from src.dashboard.charts import sector_treemap as _st

        return _st(sectors, values, title)

    # ------------------------------------------------------------------
    # Export
    # ------------------------------------------------------------------

    @staticmethod
    def export(
        export_type: str,
        data: Any,
        path: str,
        **kwargs: Any,
    ) -> str:
        """Export data to a file.

        Supported export types:

        - ``"json"`` — serialise dict to JSON
        - ``"csv"`` — write list of dicts as CSV
        - ``"html"`` — generate self-contained HTML report
        - ``"pdf"`` — generate simple PDF report

        Args:
            export_type: One of ``"json"``, ``"csv"``, ``"html"``,
                ``"pdf"``.
            data: Data to export.  Type depends on the export format:
                ``dict`` for JSON, ``list[dict]`` for CSV, ``dict`` with
                ``title`` and ``sections`` for HTML/PDF.
            path: Output file path.
            **kwargs: Additional arguments forwarded to the exporter.

        Returns:
            Absolute path of the written file.

        Raises:
            ValueError: If *export_type* is unknown.
            OSError: If the file cannot be written.
        """
        export_types = {"json", "csv", "html", "pdf"}

        if export_type not in export_types:
            raise ValueError(
                f"Unknown export type '{export_type}'. "
                f"Valid types: {sorted(export_types)}."
            )

        logger.info(
            "Exporting dashboard data as %s to '%s'.",
            export_type,
            path,
        )

        if export_type == "json":
            if not isinstance(data, dict):
                raise ValueError(
                    "JSON export requires a dict, "
                    f"got {type(data).__name__}."
                )
            result = _exporter.export_json(data, path, **kwargs)
            logger.info("JSON export complete: %s", result)
            return result

        elif export_type == "csv":
            if not isinstance(data, list):
                raise ValueError(
                    "CSV export requires a list of dicts, "
                    f"got {type(data).__name__}."
                )
            fieldnames = kwargs.get("fieldnames")
            result = _exporter.export_csv(data, path, fieldnames)
            logger.info("CSV export complete: %s", result)
            return result

        elif export_type == "html":
            if not isinstance(data, dict):
                raise ValueError(
                    "HTML export requires a dict with 'title' and "
                    f"'sections', got {type(data).__name__}."
                )
            title = data.get("title", "Dashboard Report")
            sections = data.get("sections", [])
            result = _exporter.export_html(title, sections, path)
            logger.info("HTML export complete: %s", result)
            return result

        elif export_type == "pdf":
            if not isinstance(data, dict):
                raise ValueError(
                    "PDF export requires a dict with 'title' and "
                    f"'sections', got {type(data).__name__}."
                )
            title = data.get("title", "Dashboard Report")
            sections = data.get("sections", [])
            result = _exporter.export_pdf(title, sections, path)
            logger.info("PDF export complete: %s", result)
            return result

        # Should never reach here
        return ""
