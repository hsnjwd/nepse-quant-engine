"""Tests for the AI Strategy Advisor subsystem (src/ai)."""

from __future__ import annotations

import pytest

from src.ai.advisor import SignalAdvisor, SignalExplanation
from src.ai.daily_report import DailyReportGenerator
from src.ai.market_summary import MarketCommentary, MarketSummaryGenerator
from src.ai.portfolio_review import PortfolioReview, PortfolioReviewer
from src.ai.trade_explainer import TradeExplanation, TradeExplainer


class TestSignalAdvisor:
    def test_explain_buy(self) -> None:
        advisor = SignalAdvisor()
        explanation = advisor.explain(
            symbol="NABIL",
            analysis={
                "signal": "BUY",
                "confidence": 85.0,
                "rsi": 35.0,
                "macd": 1.5,
                "macd_signal": 0.8,
                "price": 500.0,
                "score": 70.0,
            },
            regime="BULL",
        )
        assert isinstance(explanation, SignalExplanation)
        assert explanation.symbol == "NABIL"
        assert explanation.signal == "BUY"
        assert explanation.confidence == 85.0
        assert explanation.reasons
        assert explanation.summary
        assert explanation.recommendation

    def test_explain_with_none_confidence(self) -> None:
        # The analyzer can return confidence=None; must not crash.
        advisor = SignalAdvisor()
        explanation = advisor.explain(
            symbol="NABIL",
            analysis={"signal": "HOLD", "confidence": None, "rsi": None},
        )
        assert explanation.confidence == 0.0
        assert explanation.signal == "HOLD"

    def test_explain_defaults(self) -> None:
        advisor = SignalAdvisor()
        explanation = advisor.explain(symbol="NRIC")
        assert explanation.signal == "HOLD"
        assert explanation.symbol == "NRIC"

    def test_explain_overbought(self) -> None:
        advisor = SignalAdvisor()
        explanation = advisor.explain(
            symbol="X",
            analysis={"rsi": 80.0, "signal": "SELL"},
        )
        assert any("overbought" in r for r in explanation.reasons)

    def test_explain_many(self) -> None:
        advisor = SignalAdvisor()
        results = advisor.explain_many(
            [
                {"symbol": "A", "signal": "BUY", "confidence": 90.0},
                {"symbol": "B", "signal": "SELL", "confidence": 60.0},
            ],
            regime="BULL",
        )
        assert len(results) == 2
        assert results[0].symbol == "A"

    def test_to_dict(self) -> None:
        advisor = SignalAdvisor()
        data = advisor.explain(symbol="X", signal="BUY", confidence=50.0).to_dict()
        assert data["signal"] == "BUY"
        assert "reasons" in data


class TestMarketSummaryGenerator:
    def test_generate(self) -> None:
        generator = MarketSummaryGenerator()
        commentary = generator.generate(
            summary=_FakeSummary(),
            top_gainers=[{"symbol": "A"}, {"symbol": "B"}],
            top_losers=[{"symbol": "C"}],
            regime="BULL",
        )
        assert isinstance(commentary, MarketCommentary)
        assert commentary.headline
        assert commentary.paragraphs
        assert commentary.sentiment in ("Bullish", "Bearish", "Neutral")
        assert commentary.key_levels

    def test_generate_none(self) -> None:
        generator = MarketSummaryGenerator()
        commentary = generator.generate(None)
        assert commentary.sentiment == "Neutral"
        assert commentary.key_levels == {}

    def test_bearish_sentiment(self) -> None:
        generator = MarketSummaryGenerator()
        commentary = generator.generate(
            _FakeSummary(index=1000.0, change=-20.0, change_pct=-2.0, advances=10, declines=50)
        )
        assert commentary.sentiment == "Bearish"

    def test_to_dict(self) -> None:
        generator = MarketSummaryGenerator()
        data = generator.generate(_FakeSummary()).to_dict()
        assert "headline" in data


class TestTradeExplainer:
    def test_explain_entry(self) -> None:
        explainer = TradeExplainer()
        explanation = explainer.explain_entry(
            symbol="NABIL",
            analysis={
                "price": 500.0,
                "rsi": 32.0,
                "support": 480.0,
                "stop_loss": 475.0,
            },
            side="BUY",
        )
        assert isinstance(explanation, TradeExplanation)
        assert explanation.side == "BUY"
        assert explanation.entry_reasons
        assert explanation.entry_price == 500.0
        assert explanation.stop_loss == 475.0

    def test_explain_exit(self) -> None:
        explainer = TradeExplainer()
        explanation = explainer.explain_exit(
            symbol="NABIL",
            analysis={"price": 550.0},
            realized_pnl=500.0,
        )
        assert explanation.side == "SELL"
        assert any("profit" in r for r in explanation.exit_reasons)

    def test_risk_notes(self) -> None:
        explainer = TradeExplainer()
        explanation = explainer.explain_entry(
            symbol="X",
            analysis={"price": 100.0, "atr": 5.0, "stop_loss": 95.0},
        )
        assert explanation.risk_notes

    def test_to_dict(self) -> None:
        explainer = TradeExplainer()
        data = explainer.explain_entry(symbol="X").to_dict()
        assert data["side"] == "BUY"


class TestPortfolioReviewer:
    def test_review(self) -> None:
        reviewer = PortfolioReviewer()
        review = reviewer.review(
            holdings=[
                {"symbol": "A", "quantity": 100, "current_price": 10.0, "sector": "Bank"},
                {"symbol": "B", "quantity": 50, "current_price": 20.0, "sector": "Bank"},
            ],
            cash=10_000.0,
        )
        assert isinstance(review, PortfolioReview)
        assert review.summary
        assert review.observations

    def test_concentration_risk(self) -> None:
        reviewer = PortfolioReviewer()
        review = reviewer.review(
            holdings=[
                {"symbol": "A", "quantity": 100, "current_price": 100.0, "sector": "Bank"},
                {"symbol": "B", "quantity": 10, "current_price": 10.0, "sector": "Bank"},
            ],
            cash=0.0,
        )
        assert any("concentration" in r.lower() for r in review.risks)

    def test_empty_holdings(self) -> None:
        reviewer = PortfolioReviewer()
        review = reviewer.review(holdings=[], cash=100.0)
        assert review.metrics["positions"] == 0

    def test_to_dict(self) -> None:
        reviewer = PortfolioReviewer()
        data = reviewer.review(holdings=[{"symbol": "A", "value": 100.0}]).to_dict()
        assert "summary" in data


class TestDailyReportGenerator:
    def test_generate(self) -> None:
        generator = DailyReportGenerator()
        report = generator.generate(
            market_summary=_FakeSummary(),
            holdings=[{"symbol": "A", "value": 100.0}],
            top_gainers=[{"symbol": "A"}],
            watchlist_signals=[
                {"symbol": "A", "signal": "BUY", "confidence": 90.0},
                {"symbol": "B", "signal": "SELL", "confidence": 70.0},
            ],
            risk_notes=["High volatility."],
        )
        assert report.date
        assert report.market is not None
        assert report.portfolio is not None
        assert report.watchlist_commentary
        assert report.top_signals
        assert report.risk_notes == ["High volatility."]

    def test_top_signals_ranking(self) -> None:
        generator = DailyReportGenerator()
        report = generator.generate(
            watchlist_signals=[
                {"symbol": "A", "signal": "BUY", "confidence": 50.0},
                {"symbol": "B", "signal": "BUY", "confidence": 95.0},
                {"symbol": "C", "signal": "HOLD", "confidence": 99.0},
            ]
        )
        assert report.top_signals[0]["symbol"] == "B"

    def test_generate_minimal(self) -> None:
        generator = DailyReportGenerator()
        report = generator.generate()
        assert report.market is None
        assert report.portfolio is None

    def test_to_dict(self) -> None:
        generator = DailyReportGenerator()
        data = generator.generate(watchlist_signals=[{"symbol": "A", "signal": "BUY"}]).to_dict()
        assert "watchlist_commentary" in data


class _FakeSummary:
    """Minimal stand-in for MarketSummary."""

    def __init__(
        self,
        index: float = 2000.0,
        change: float = 10.0,
        change_pct: float = 0.5,
        advances: int = 100,
        declines: int = 40,
        unchanged: int = 20,
        volume: int = 1_000_000,
        turnover: float = 5_000_000.0,
    ) -> None:
        self.index = index
        self.change = change
        self.change_pct = change_pct
        self.advances = advances
        self.declines = declines
        self.unchanged = unchanged
        self.volume = volume
        self.turnover = turnover
