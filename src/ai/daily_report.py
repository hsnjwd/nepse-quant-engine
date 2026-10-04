"""Daily report generator for the AI Strategy Advisor.

Composes market commentary, portfolio review, watchlist commentary,
and risk notes into a structured daily report.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from src.ai.market_summary import MarketCommentary, MarketSummaryGenerator
from src.ai.portfolio_review import PortfolioReview, PortfolioReviewer

logger = logging.getLogger("nepse.ai.daily_report")


@dataclass
class DailyReport:
    """Aggregate daily report.

    Attributes:
        date: Report date string.
        market: :class:`MarketCommentary` section.
        portfolio: :class:`PortfolioReview` section.
        watchlist_commentary: List of watchlist commentary strings.
        top_signals: List of signal summaries.
        risk_notes: List of risk notes.
        sections: Raw ordered sections for rendering.
    """

    date: str = ""
    market: MarketCommentary | None = None
    portfolio: PortfolioReview | None = None
    watchlist_commentary: list[str] = field(default_factory=list)
    top_signals: list[dict[str, Any]] = field(default_factory=list)
    risk_notes: list[str] = field(default_factory=list)
    sections: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serialisable dictionary."""
        return {
            "date": self.date,
            "market": self.market.to_dict() if self.market else None,
            "portfolio": self.portfolio.to_dict() if self.portfolio else None,
            "watchlist_commentary": self.watchlist_commentary,
            "top_signals": self.top_signals,
            "risk_notes": self.risk_notes,
        }


class DailyReportGenerator:
    """Compose the full daily report.

    Usage::

        generator = DailyReportGenerator()
        report = generator.generate(market_summary=summary, holdings=[...])
    """

    def __init__(
        self,
        market_generator: MarketSummaryGenerator | None = None,
        portfolio_reviewer: PortfolioReviewer | None = None,
    ) -> None:
        """Initialise the generator.

        Args:
            market_generator: Optional market summary generator.
            portfolio_reviewer: Optional portfolio reviewer.
        """
        self._market = market_generator or MarketSummaryGenerator()
        self._portfolio = portfolio_reviewer or PortfolioReviewer()

    def generate(
        self,
        market_summary: Any = None,
        holdings: list[dict[str, Any]] | None = None,
        cash: float = 0.0,
        top_gainers: list[Any] | None = None,
        top_losers: list[Any] | None = None,
        regime: str | None = None,
        watchlist_signals: list[dict[str, Any]] | None = None,
        risk_notes: list[str] | None = None,
    ) -> DailyReport:
        """Generate the daily report.

        Args:
            market_summary: Market summary object.
            holdings: Portfolio holdings.
            cash: Cash balance.
            top_gainers: Top gainers list.
            top_losers: Top losers list.
            regime: Market regime label.
            watchlist_signals: Watchlist signal payloads.
            risk_notes: Additional risk notes.

        Returns:
            A :class:`DailyReport`.
        """
        from datetime import date

        market = (
            self._market.generate(
                market_summary,
                top_gainers=top_gainers,
                top_losers=top_losers,
                regime=regime,
            )
            if market_summary is not None
            else None
        )

        portfolio = (
            self._portfolio.review(holdings=holdings, cash=cash)
            if holdings
            else None
        )

        watchlist_commentary = self._watchlist_commentary(watchlist_signals)
        top_signals = self._top_signals(watchlist_signals)

        report = DailyReport(
            date=date.today().isoformat(),
            market=market,
            portfolio=portfolio,
            watchlist_commentary=watchlist_commentary,
            top_signals=top_signals,
            risk_notes=risk_notes or [],
        )
        logger.info("Generated daily report for %s.", report.date)
        return report

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    @staticmethod
    def _watchlist_commentary(
        watchlist_signals: list[dict[str, Any]] | None,
    ) -> list[str]:
        """Compose per-symbol watchlist commentary."""
        if not watchlist_signals:
            return []
        lines: list[str] = []
        for item in watchlist_signals:
            symbol = item.get("symbol", "?")
            signal = item.get("signal", "HOLD")
            confidence = item.get("confidence")
            if confidence is not None:
                lines.append(
                    f"{symbol}: {signal} signal with {confidence:.0f}% confidence."
                )
            else:
                lines.append(f"{symbol}: {signal} signal.")
        return lines

    @staticmethod
    def _top_signals(
        watchlist_signals: list[dict[str, Any]] | None,
    ) -> list[dict[str, Any]]:
        """Rank watchlist signals by confidence."""
        if not watchlist_signals:
            return []
        scored = [
            item
            for item in watchlist_signals
            if item.get("signal") in ("BUY", "STRONG_BUY", "SELL", "STRONG_SELL")
        ]
        scored.sort(
            key=lambda i: i.get("confidence", 0.0) or 0.0,
            reverse=True,
        )
        return scored[:5]
