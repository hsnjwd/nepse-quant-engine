"""AI Strategy Advisor subsystem.

Provides rule-based natural-language explanations for trading signals,
market summaries, trade decisions, portfolio reviews, and daily
reports.  The rule-based implementations are designed to be replaced
with LLM-backed generators later without changing the public API.
"""

from __future__ import annotations

from src.ai.advisor import SignalAdvisor, SignalExplanation
from src.ai.daily_report import DailyReport, DailyReportGenerator
from src.ai.market_summary import MarketCommentary, MarketSummaryGenerator
from src.ai.portfolio_review import PortfolioReview, PortfolioReviewer
from src.ai.trade_explainer import TradeExplanation, TradeExplainer

__all__ = [
    "SignalAdvisor",
    "SignalExplanation",
    "DailyReport",
    "DailyReportGenerator",
    "MarketCommentary",
    "MarketSummaryGenerator",
    "PortfolioReview",
    "PortfolioReviewer",
    "TradeExplanation",
    "TradeExplainer",
]
