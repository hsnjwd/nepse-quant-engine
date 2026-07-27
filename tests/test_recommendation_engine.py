"""Tests for the Trade Recommendation Engine.

Covers the full orchestration pipeline: strategy execution, signal
aggregation, position sizing, and capital allocation.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from src.strategies.manager import StrategyManager
from src.recommendations.engine import (
    RecommendationEngine,
    TradeRecommendation,
)


# ======================================================================
# Fixtures — mock strategy signals
# ======================================================================


@pytest.fixture
def buy_signals() -> list[dict]:
    """Three BUY, one SELL, one HOLD → majority BUY."""
    return [
        {
            "strategy_name": "MomentumStrategy",
            "strategy_version": "1.0.0",
            "signal": "BUY",
            "price": 100.0,
            "stop_loss": 90.0,
            "targets": [110.0, 120.0],
            "score": 85.0,
            "details": {"trend": "uptrend"},
        },
        {
            "strategy_name": "BreakoutStrategy",
            "strategy_version": "1.0.0",
            "signal": "BUY",
            "price": 100.0,
            "stop_loss": 90.0,
            "targets": [108.0, 118.0],
            "score": 78.0,
            "details": {"pattern": "bullish"},
        },
        {
            "strategy_name": "TrendStrategy",
            "strategy_version": "1.0.0",
            "signal": "BUY",
            "price": 100.0,
            "stop_loss": 90.0,
            "targets": [112.0, 125.0],
            "score": 82.0,
            "details": {"momentum": "positive"},
        },
        {
            "strategy_name": "MeanReversion",
            "strategy_version": "1.0.0",
            "signal": "SELL",
            "price": 102.0,
            "stop_loss": None,
            "targets": [],
            "score": 65.0,
            "details": {},
        },
        {
            "strategy_name": "VolatilityStrategy",
            "strategy_version": "1.0.0",
            "signal": "HOLD",
            "price": None,
            "stop_loss": None,
            "targets": [],
            "score": 50.0,
            "details": {},
        },
    ]


@pytest.fixture
def sell_signals() -> list[dict]:
    """One BUY, three SELL, one HOLD → majority SELL."""
    return [
        {
            "strategy_name": "MomentumStrategy",
            "strategy_version": "1.0.0",
            "signal": "BUY",
            "price": 100.0,
            "stop_loss": 95.0,
            "targets": [105.0],
            "score": 70.0,
            "details": {},
        },
        {
            "strategy_name": "BreakoutStrategy",
            "strategy_version": "1.0.0",
            "signal": "SELL",
            "price": 98.0,
            "stop_loss": None,
            "targets": [],
            "score": 72.0,
            "details": {},
        },
        {
            "strategy_name": "TrendStrategy",
            "strategy_version": "1.0.0",
            "signal": "SELL",
            "price": 99.0,
            "stop_loss": None,
            "targets": [],
            "score": 68.0,
            "details": {},
        },
        {
            "strategy_name": "MeanReversion",
            "strategy_version": "1.0.0",
            "signal": "SELL",
            "price": 97.0,
            "stop_loss": None,
            "targets": [],
            "score": 75.0,
            "details": {},
        },
        {
            "strategy_name": "VolatilityStrategy",
            "strategy_version": "1.0.0",
            "signal": "HOLD",
            "price": None,
            "stop_loss": None,
            "targets": [],
            "score": 50.0,
            "details": {},
        },
    ]


@pytest.fixture
def hold_signals() -> list[dict]:
    """One BUY, one SELL, three HOLD → majority HOLD."""
    return [
        {
            "strategy_name": "MomentumStrategy",
            "strategy_version": "1.0.0",
            "signal": "HOLD",
            "price": None,
            "stop_loss": None,
            "targets": [],
            "score": 50.0,
            "details": {},
        },
        {
            "strategy_name": "BreakoutStrategy",
            "strategy_version": "1.0.0",
            "signal": "HOLD",
            "price": None,
            "stop_loss": None,
            "targets": [],
            "score": 55.0,
            "details": {},
        },
        {
            "strategy_name": "TrendStrategy",
            "strategy_version": "1.0.0",
            "signal": "BUY",
            "price": 100.0,
            "stop_loss": 90.0,
            "targets": [110.0],
            "score": 70.0,
            "details": {},
        },
        {
            "strategy_name": "MeanReversion",
            "strategy_version": "1.0.0",
            "signal": "SELL",
            "price": 105.0,
            "stop_loss": None,
            "targets": [],
            "score": 65.0,
            "details": {},
        },
        {
            "strategy_name": "VolatilityStrategy",
            "strategy_version": "1.0.0",
            "signal": "HOLD",
            "price": None,
            "stop_loss": None,
            "targets": [],
            "score": 45.0,
            "details": {},
        },
    ]


# ======================================================================
# TradeRecommendation dataclass
# ======================================================================


class TestTradeRecommendation:
    """TradeRecommendation dataclass serialisation."""

    def test_to_dict_returns_all_fields(self) -> None:
        """to_dict() returns all expected fields."""
        rec = TradeRecommendation(
            symbol="NABIL",
            signal="BUY",
            confidence=80.0,
            average_score=75.5,
            entry_price=100.0,
            stop_loss=90.0,
            targets=[110.0, 120.0],
            shares=200,
            position_value=20_000.0,
            capital_at_risk=2_000.0,
            portfolio_weight=0.5,
            strategy_votes={"BUY": 3, "SELL": 1, "HOLD": 1},
            winning_strategies=["MomentumStrategy", "BreakoutStrategy"],
            strategy_signals=[{"dummy": "signal"}],
            reasons=["3 strategies voted BUY", "Average score: 81.67"],
            metadata={"generated_by": "RecommendationEngine"},
        )
        d = rec.to_dict()

        assert d["symbol"] == "NABIL"
        assert d["signal"] == "BUY"
        assert d["confidence"] == 80.0
        assert d["average_score"] == 75.5
        assert d["entry_price"] == 100.0
        assert d["stop_loss"] == 90.0
        assert d["targets"] == [110.0, 120.0]
        assert d["shares"] == 200
        assert d["position_value"] == 20_000.0
        assert d["capital_at_risk"] == 2_000.0
        assert d["portfolio_weight"] == 0.5
        assert d["strategy_votes"] == {"BUY": 3, "SELL": 1, "HOLD": 1}
        assert "MomentumStrategy" in d["winning_strategies"]
        assert len(d["strategy_signals"]) == 1
        assert len(d["reasons"]) == 2
        assert d["metadata"]["generated_by"] == "RecommendationEngine"

    def test_to_dict_hold_defaults(self) -> None:
        """HOLD recommendation has zero shares and None prices."""
        rec = TradeRecommendation(
            symbol="NABIL",
            signal="HOLD",
            confidence=0.0,
            average_score=0.0,
        )
        d = rec.to_dict()

        assert d["signal"] == "HOLD"
        assert d["entry_price"] is None
        assert d["stop_loss"] is None
        assert d["targets"] == []
        assert d["shares"] == 0
        assert d["position_value"] == 0.0
        assert d["capital_at_risk"] == 0.0
        assert d["portfolio_weight"] == 0.0


# ======================================================================
# RecommendationEngine — BUY
# ======================================================================


class TestRecommendationEngineBuy:
    """RecommendationEngine produces full BUY recommendations."""

    def test_buy_recommendation_full_pipeline(self, buy_signals: list[dict]) -> None:
        """BUY consensus produces a fully populated recommendation."""
        with patch.object(StrategyManager, "evaluate_all", return_value=buy_signals):
            engine = RecommendationEngine(capital=100_000)
            result = engine.recommend("NABIL", df=None)

        assert result.symbol == "NABIL"
        assert result.signal == "BUY"
        assert result.confidence > 0
        assert result.average_score > 0

        # Risk engine values
        assert result.entry_price is not None
        assert result.stop_loss is not None
        assert result.shares > 0
        assert result.position_value > 0
        assert result.capital_at_risk > 0

        # Allocation
        assert result.portfolio_weight > 0

        # Aggregate metadata
        assert result.strategy_votes["BUY"] == 3
        assert result.strategy_votes["SELL"] == 1
        assert result.strategy_votes["HOLD"] == 1
        assert len(result.winning_strategies) == 3
        assert len(result.strategy_signals) == 5
        assert len(result.reasons) > 0
        assert result.metadata["generated_by"] == "RecommendationEngine"

    def test_buy_recommendation_exact_values(self, buy_signals: list[dict]) -> None:
        """BUY recommendation produces expected numeric values."""
        with patch.object(StrategyManager, "evaluate_all", return_value=buy_signals):
            engine = RecommendationEngine(
                capital=100_000,
                risk_per_trade_pct=0.02,
            )
            result = engine.recommend("NABIL", df=None)

        # All three BUY signals have price=100, stop_loss=90
        # consensus.price = 100.0, consensus.stop_loss = 90.0
        assert result.entry_price == 100.0
        assert result.stop_loss == 90.0

        # RiskEngine: capital_at_risk = 100_000 * 0.02 = 2_000
        # risk_per_share = 100 - 90 = 10
        # shares = floor(2_000 / 10) = 200
        # position_value = 200 * 100 = 20_000
        assert result.capital_at_risk == 2_000.0
        assert result.shares == 200
        assert result.position_value == 20_000.0

        # Allocator: 1 BUY signal → weight = 1.0
        assert result.portfolio_weight == 1.0

    def test_buy_targets_averaged(self, buy_signals: list[dict]) -> None:
        """Targets are element-wise averaged from BUY signals."""
        with patch.object(StrategyManager, "evaluate_all", return_value=buy_signals):
            engine = RecommendationEngine(capital=100_000)
            result = engine.recommend("NABIL", df=None)

        # Targets [110, 120], [108, 118], [112, 125]
        # avg idx0: (110 + 108 + 112) / 3 = 110.0
        # avg idx1: (120 + 118 + 125) / 3 = 121.0
        assert len(result.targets) == 2
        assert result.targets[0] == 110.0
        assert result.targets[1] == 121.0


# ======================================================================
# RecommendationEngine — SELL / HOLD
# ======================================================================


class TestRecommendationEngineNonBuy:
    """Non-BUY recommendations have zero share/risk/allocation values."""

    def test_sell_recommendation_no_allocation(
        self, sell_signals: list[dict]
    ) -> None:
        """SELL consensus returns HOLD signal with zero risk values."""
        with patch.object(StrategyManager, "evaluate_all", return_value=sell_signals):
            engine = RecommendationEngine(capital=100_000)
            result = engine.recommend("NABIL", df=None)

        # Aggregator resolves SELL > BUY/HOLD → SELL
        assert result.signal == "SELL"
        assert result.confidence > 0
        assert result.average_score > 0

        # No allocation
        assert result.entry_price is None
        assert result.stop_loss is None
        assert result.targets == []
        assert result.shares == 0
        assert result.position_value == 0.0
        assert result.capital_at_risk == 0.0
        assert result.portfolio_weight == 0.0

        # Metadata still present
        assert result.strategy_votes["SELL"] == 3
        assert result.metadata["generated_by"] == "RecommendationEngine"

    def test_hold_recommendation_no_allocation(
        self, hold_signals: list[dict]
    ) -> None:
        """HOLD consensus returns HOLD with zero risk values."""
        with patch.object(StrategyManager, "evaluate_all", return_value=hold_signals):
            engine = RecommendationEngine(capital=100_000)
            result = engine.recommend("NABIL", df=None)

        assert result.signal == "HOLD"
        assert result.shares == 0
        assert result.position_value == 0.0
        assert result.capital_at_risk == 0.0
        assert result.portfolio_weight == 0.0
        assert result.entry_price is None

    def test_empty_signals_list(self) -> None:
        """No strategies registered returns HOLD with zero confidence."""
        with patch.object(StrategyManager, "evaluate_all", return_value=[]):
            engine = RecommendationEngine(capital=100_000)
            result = engine.recommend("NABIL", df=None)

        assert result.signal == "HOLD"
        assert result.confidence == 0.0
        assert result.average_score == 0.0
        assert result.shares == 0


# ======================================================================
# RecommendationEngine — missing price / stop loss
# ======================================================================


class TestRecommendationEngineMissingData:
    """BUY consensus with missing price/stop_loss returns HOLD."""

    def test_missing_entry_price(self) -> None:
        """BUY majority consensus without price returns HOLD."""
        # Two BUY signals (both price=None) + one SELL => BUY wins vote
        # but consensus.price is None, triggering the missing-price branch.
        signals = [
            {
                "strategy_name": "S1",
                "strategy_version": "1.0.0",
                "signal": "BUY",
                "price": None,
                "stop_loss": 90.0,
                "targets": [110.0],
                "score": 80.0,
                "details": {},
            },
            {
                "strategy_name": "S2",
                "strategy_version": "1.0.0",
                "signal": "BUY",
                "price": None,
                "stop_loss": 92.0,
                "targets": [112.0],
                "score": 75.0,
                "details": {},
            },
            {
                "strategy_name": "S3",
                "strategy_version": "1.0.0",
                "signal": "SELL",
                "price": 105.0,
                "stop_loss": None,
                "targets": [],
                "score": 60.0,
                "details": {},
            },
        ]
        with patch.object(StrategyManager, "evaluate_all", return_value=signals):
            engine = RecommendationEngine(capital=100_000)
            result = engine.recommend("NABIL", df=None)

        # BUY=2, SELL=1 => BUY wins vote, but both BUY prices are None
        assert result.signal == "HOLD"
        assert result.entry_price is None
        assert result.shares == 0

    def test_missing_stop_loss(self) -> None:
        """BUY majority consensus without stop_loss returns HOLD."""
        # Two BUY signals (both stop_loss=None) + one SELL => BUY wins
        # vote but consensus.stop_loss is None.
        signals = [
            {
                "strategy_name": "S1",
                "strategy_version": "1.0.0",
                "signal": "BUY",
                "price": 100.0,
                "stop_loss": None,
                "targets": [110.0],
                "score": 80.0,
                "details": {},
            },
            {
                "strategy_name": "S2",
                "strategy_version": "1.0.0",
                "signal": "BUY",
                "price": 102.0,
                "stop_loss": None,
                "targets": [112.0],
                "score": 75.0,
                "details": {},
            },
            {
                "strategy_name": "S3",
                "strategy_version": "1.0.0",
                "signal": "SELL",
                "price": 105.0,
                "stop_loss": None,
                "targets": [],
                "score": 60.0,
                "details": {},
            },
        ]
        with patch.object(StrategyManager, "evaluate_all", return_value=signals):
            engine = RecommendationEngine(capital=100_000)
            result = engine.recommend("NABIL", df=None)

        # BUY=2, SELL=1 => BUY wins vote, but all stop_loss are None
        assert result.signal == "HOLD"
        assert result.stop_loss is None
        assert result.shares == 0

    def test_buy_consensus_with_price_no_stop(self) -> None:
        """BUY majority has avg price but all stop_loss=None → HOLD."""
        signals = [
            {
                "strategy_name": "S1",
                "strategy_version": "1.0.0",
                "signal": "BUY",
                "price": 100.0,
                "stop_loss": None,
                "targets": [110.0],
                "score": 80.0,
                "details": {},
            },
            {
                "strategy_name": "S2",
                "strategy_version": "1.0.0",
                "signal": "BUY",
                "price": 102.0,
                "stop_loss": None,
                "targets": [112.0],
                "score": 75.0,
                "details": {},
            },
            {
                "strategy_name": "S3",
                "strategy_version": "1.0.0",
                "signal": "SELL",
                "price": 105.0,
                "stop_loss": None,
                "targets": [],
                "score": 60.0,
                "details": {},
            },
        ]
        with patch.object(StrategyManager, "evaluate_all", return_value=signals):
            engine = RecommendationEngine(capital=100_000)
            result = engine.recommend("NABIL", df=None)

        assert result.signal == "HOLD"
        assert result.entry_price is not None  # consensus has price
        assert result.stop_loss is None  # but no stop loss
        assert result.metadata["generated_by"] == "RecommendationEngine"


# ======================================================================
# RecommendationEngine — error handling
# ======================================================================


class TestRecommendationEngineErrors:
    """RecommendationEngine error handling."""

    def test_invalid_symbol_empty_string(self) -> None:
        """Empty symbol raises ValueError."""
        engine = RecommendationEngine(capital=100_000)
        with pytest.raises(ValueError, match="Symbol must be a non-empty string"):
            engine.recommend("", df=None)

    def test_invalid_symbol_none(self) -> None:
        """None symbol raises ValueError."""
        engine = RecommendationEngine(capital=100_000)
        with pytest.raises(ValueError, match="Symbol must be a non-empty string"):
            engine.recommend(None, df=None)  # type: ignore

    def test_strategy_exception_caught(self) -> None:
        """Unexpected strategy exception returns HOLD with error metadata."""
        with patch.object(
            StrategyManager,
            "evaluate_all",
            side_effect=RuntimeError("Market data unavailable"),
        ):
            engine = RecommendationEngine(capital=100_000)
            result = engine.recommend("NABIL", df=None)

        assert result.signal == "HOLD"
        assert result.confidence == 0.0
        assert result.shares == 0
        assert "error" in result.metadata
        assert "Market data unavailable" in result.metadata["error"]

    def test_risk_engine_rejection_returns_hold(self, buy_signals: list[dict]) -> None:
        """If RiskEngine rejects the trade, engine returns HOLD."""
        # BUY consensus with price=stop_loss (invalid for RiskEngine)
        bad_signals = [
            {
                **buy_signals[0],
                "price": 100.0,
                "stop_loss": 100.0,  # equal — RiskEngine rejects
            }
        ]
        with patch.object(StrategyManager, "evaluate_all", return_value=bad_signals):
            engine = RecommendationEngine(capital=100_000)
            # Consensus is BUY (1 BUY), but RiskEngine raises ValueError
            # The except clause catches it and returns HOLD
            result = engine.recommend("NABIL", df=None)

        assert result.signal == "HOLD"
        assert result.shares == 0
        assert "error" in result.metadata


# ======================================================================
# recommend_to_dict helper
# ======================================================================


class TestRecommendToDict:
    """RecommendationEngine.recommend_to_dict convenience method."""

    def test_recommend_to_dict_returns_dict(self, buy_signals: list[dict]) -> None:
        """recommend_to_dict returns a dictionary."""
        with patch.object(StrategyManager, "evaluate_all", return_value=buy_signals):
            engine = RecommendationEngine(capital=100_000)
            result = engine.recommend_to_dict("NABIL", df=None)

        assert isinstance(result, dict)
        assert result["symbol"] == "NABIL"
        assert result["signal"] == "BUY"
        assert result["shares"] > 0

    def test_recommend_to_dict_hold(self) -> None:
        """recommend_to_dict with empty signals returns dict with HOLD."""
        with patch.object(StrategyManager, "evaluate_all", return_value=[]):
            engine = RecommendationEngine(capital=100_000)
            result = engine.recommend_to_dict("NABIL", df=None)

        assert isinstance(result, dict)
        assert result["signal"] == "HOLD"
        assert result["shares"] == 0


# ======================================================================
# Integration — components wired correctly
# ======================================================================


class TestRecommendationEngineIntegration:
    """Verify the sub-components are correctly wired together."""

    def test_strategy_manager_created(self) -> None:
        """Engine creates a StrategyManager internally."""
        engine = RecommendationEngine(capital=100_000)
        assert hasattr(engine, "_manager")
        assert isinstance(engine._manager, StrategyManager)

    def test_risk_engine_created(self) -> None:
        """Engine creates a RiskEngine with the correct capital."""
        engine = RecommendationEngine(capital=500_000, risk_per_trade_pct=0.03)
        assert hasattr(engine, "_risk_engine")
        assert engine._risk_engine._capital == 500_000
        assert engine._risk_engine._risk_per_trade == 0.03

    def test_allocator_created(self) -> None:
        """Engine creates a PortfolioAllocator with the correct capital."""
        engine = RecommendationEngine(
            capital=100_000,
            max_position_weight=0.25,
        )
        assert hasattr(engine, "_allocator")
        assert engine._allocator._capital == 100_000
        assert engine._allocator._max_weight == 0.25

    def test_repeated_calls_produce_consistent_results(
        self, buy_signals: list[dict]
    ) -> None:
        """Calling recommend() multiple times produces consistent results."""
        with patch.object(StrategyManager, "evaluate_all", return_value=buy_signals):
            engine = RecommendationEngine(capital=100_000)

            result1 = engine.recommend("NABIL", df=None)
            result2 = engine.recommend("NABIL", df=None)

            assert result1.signal == result2.signal == "BUY"
            assert result1.shares == result2.shares
            assert result1.portfolio_weight == result2.portfolio_weight

    def test_different_symbols_isolated(self) -> None:
        """Different symbols produce separate recommendations."""
        # Same signals for simplicity — just verify symbol is preserved
        signals = [
            {
                "strategy_name": "S1",
                "strategy_version": "1.0.0",
                "signal": "BUY",
                "price": 100.0,
                "stop_loss": 90.0,
                "targets": [110.0],
                "score": 80.0,
                "details": {},
            }
        ]
        with patch.object(StrategyManager, "evaluate_all", return_value=signals):
            engine = RecommendationEngine(capital=100_000)
            r1 = engine.recommend("NABIL", df=None)
            r2 = engine.recommend("ADBL", df=None)

            assert r1.symbol == "NABIL"
            assert r2.symbol == "ADBL"
            assert r1.signal == "BUY"
            assert r2.signal == "BUY"
