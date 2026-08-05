"""Portfolio review generator for the AI Strategy Advisor.

Produces natural-language reviews of portfolio health, diversification,
concentration risk, and performance.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger("nepse.ai.portfolio_review")


@dataclass
class PortfolioReview:
    """Natural-language portfolio review.

    Attributes:
        summary: One-line portfolio summary.
        observations: List of observations about the portfolio.
        risks: List of identified risks.
        suggestions: List of actionable suggestions.
        metrics: Key numeric metrics captured during review.
    """

    summary: str = ""
    observations: list[str] = field(default_factory=list)
    risks: list[str] = field(default_factory=list)
    suggestions: list[str] = field(default_factory=list)
    metrics: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serialisable dictionary."""
        return {
            "summary": self.summary,
            "observations": self.observations,
            "risks": self.risks,
            "suggestions": self.suggestions,
            "metrics": self.metrics,
        }


class PortfolioReviewer:
    """Review a portfolio and produce plain-language commentary.

    Usage::

        reviewer = PortfolioReviewer()
        review = reviewer.review(holdings=[...], cash=100000.0)
    """

    def review(
        self,
        holdings: list[dict[str, Any]] | None = None,
        cash: float = 0.0,
        total_value: float | None = None,
    ) -> PortfolioReview:
        """Review a set of holdings.

        Args:
            holdings: List of holding dicts with ``symbol``,
                ``quantity``, ``current_price`` (or ``value``).
            cash: Available cash balance.
            total_value: Optional total portfolio value override.

        Returns:
            A :class:`PortfolioReview`.
        """
        holdings = holdings or []
        metrics = self._compute_metrics(holdings, cash, total_value)
        value = metrics["total_value"]

        observations = self._observations(holdings, metrics)
        risks = self._risks(holdings, metrics)
        suggestions = self._suggestions(holdings, metrics, risks)

        summary = (
            f"Portfolio holds {metrics['positions']} positions worth "
            f"{value:,.2f} across {metrics['sectors']} sector(s), "
            f"with {cash:,.2f} in cash."
        )

        return PortfolioReview(
            summary=summary,
            observations=observations,
            risks=risks,
            suggestions=suggestions,
            metrics=metrics,
        )

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    @staticmethod
    def _holding_value(h: dict[str, Any]) -> float:
        """Compute a holding's market value."""
        if h.get("value") is not None:
            return float(h["value"])
        qty = float(h.get("quantity", 0))
        price = float(h.get("current_price", h.get("price", 0)))
        return qty * price

    def _compute_metrics(
        self,
        holdings: list[dict[str, Any]],
        cash: float,
        total_value: float | None,
    ) -> dict[str, Any]:
        """Compute portfolio metrics."""
        values = [self._holding_value(h) for h in holdings]
        invested = sum(values)
        total = total_value if total_value is not None else invested + cash

        sectors: set[str] = set()
        for h in holdings:
            sector = h.get("sector")
            if sector:
                sectors.add(str(sector))

        weights = [v / invested if invested else 0.0 for v in values]
        top_weight = max(weights) if weights else 0.0

        return {
            "positions": len(holdings),
            "invested": invested,
            "cash": cash,
            "total_value": total,
            "sectors": len(sectors),
            "top_weight": top_weight,
            "cash_ratio": cash / total if total else 0.0,
        }

    def _observations(
        self,
        holdings: list[dict[str, Any]],
        metrics: dict[str, Any],
    ) -> list[str]:
        """Build neutral observations."""
        observations: list[str] = []

        if metrics["positions"] == 0:
            observations.append("The portfolio currently holds no positions.")
        else:
            observations.append(
                f"{metrics['positions']} position(s) held, "
                f"top holding is {metrics['top_weight'] * 100:.1f}% of "
                "invested capital."
            )

        if metrics["cash_ratio"] > 0.5:
            observations.append(
                f"More than half ({metrics['cash_ratio'] * 100:.0f}%) of "
                "the portfolio is in cash."
            )
        elif metrics["cash_ratio"] < 0.05:
            observations.append("The portfolio is almost fully invested.")

        if not observations:
            observations.append("Portfolio structure looks balanced.")
        return observations

    def _risks(
        self,
        holdings: list[dict[str, Any]],
        metrics: dict[str, Any],
    ) -> list[str]:
        """Identify concentration and structure risks."""
        risks: list[str] = []

        if metrics["positions"] == 1 and metrics["positions"]:
            risks.append("Portfolio is concentrated in a single position.")
        elif 0 < metrics["positions"] <= 2:
            risks.append("Portfolio is thinly diversified across few positions.")

        if metrics["top_weight"] > 0.4:
            risks.append(
                f"Concentration risk: the largest holding represents "
                f"{metrics['top_weight'] * 100:.0f}% of invested capital."
            )

        for h in holdings:
            sector = h.get("sector")
            if not sector:
                risks.append(
                    f"Missing sector classification for {h.get('symbol', '?')}."
                )

        return risks

    def _suggestions(
        self,
        holdings: list[dict[str, Any]],
        metrics: dict[str, Any],
        risks: list[str],
    ) -> list[str]:
        """Build actionable suggestions."""
        suggestions: list[str] = []
        if metrics["top_weight"] > 0.4:
            suggestions.append(
                "Consider trimming the largest position to reduce "
                "concentration risk."
            )
        if metrics["cash_ratio"] > 0.5:
            suggestions.append(
                "Excess cash could be deployed into diversified positions "
                "if opportunities exist."
            )
        if 0 < metrics["positions"] <= 2:
            suggestions.append("Add uncorrelated positions to improve diversification.")
        if not suggestions:
            suggestions.append("No major rebalancing required at this time.")
        return suggestions
