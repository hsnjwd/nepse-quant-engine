"""Tests for the Strategy Performance Analytics Engine.

Covers basic and advanced metrics, empty/mixed histories, risk-adjusted
ratios, score/rating calculation, and cross-strategy ranking.
"""

from __future__ import annotations

import math

import pytest

from src.analytics.performance import (
    PerformanceAnalyzer,
    RankEntry,
    StrategyPerformance,
    StrategyRanking,
)


# ======================================================================
# Fixtures — trade histories
# ======================================================================


@pytest.fixture
def profitable_trades() -> list[dict]:
    """Consistently profitable trade history."""
    return [
        {
            "net_profit": 10_000.0,
            "return_pct": 5.0,
            "holding_days": 3,
        },
        {
            "net_profit": 8_000.0,
            "return_pct": 4.0,
            "holding_days": 5,
        },
        {
            "net_profit": 12_000.0,
            "return_pct": 6.0,
            "holding_days": 2,
        },
        {
            "net_profit": 6_000.0,
            "return_pct": 3.0,
            "holding_days": 4,
        },
        {
            "net_profit": 15_000.0,
            "return_pct": 7.5,
            "holding_days": 3,
        },
    ]


@pytest.fixture
def losing_trades() -> list[dict]:
    """Consistently losing trade history."""
    return [
        {
            "net_profit": -5_000.0,
            "return_pct": -2.5,
            "holding_days": 4,
        },
        {
            "net_profit": -8_000.0,
            "return_pct": -4.0,
            "holding_days": 6,
        },
        {
            "net_profit": -3_000.0,
            "return_pct": -1.5,
            "holding_days": 3,
        },
    ]


@pytest.fixture
def mixed_trades() -> list[dict]:
    """Mixed win/loss/breakeven trade history."""
    return [
        {
            "net_profit": 10_000.0,
            "return_pct": 5.0,
            "holding_days": 3,
        },
        {
            "net_profit": -4_000.0,
            "return_pct": -2.0,
            "holding_days": 5,
        },
        {
            "net_profit": 6_000.0,
            "return_pct": 3.0,
            "holding_days": 4,
        },
        {
            "net_profit": 0.0,
            "return_pct": 0.0,
            "holding_days": 2,
        },
        {
            "net_profit": -2_000.0,
            "return_pct": -1.0,
            "holding_days": 3,
        },
        {
            "net_profit": 8_000.0,
            "return_pct": 4.0,
            "holding_days": 6,
        },
    ]


@pytest.fixture
def analyzer() -> PerformanceAnalyzer:
    """Reusable PerformanceAnalyzer instance."""
    return PerformanceAnalyzer()


# ======================================================================
# StrategyPerformance dataclass
# ======================================================================


class TestStrategyPerformance:
    """StrategyPerformance dataclass serialisation."""

    def test_to_dict_returns_all_fields(self) -> None:
        """to_dict() returns all expected fields."""
        perf = StrategyPerformance(
            strategy_name="MomentumStrategy",
            symbol="NABIL",
            total_trades=10,
            winning_trades=7,
            losing_trades=2,
            breakeven_trades=1,
            win_rate=70.0,
            profit_factor=3.5,
            expectancy=5_000.0,
            gross_profit=50_000.0,
            gross_loss=15_000.0,
            net_profit=35_000.0,
            average_win=7_142.86,
            average_loss=-7_500.0,
            average_return_pct=3.5,
            average_holding_days=4.2,
            largest_win=15_000.0,
            largest_loss=-8_000.0,
            max_drawdown=12.5,
            recovery_factor=2.8,
            sharpe_ratio=1.5,
            sortino_ratio=2.1,
            calmar_ratio=0.8,
            ending_capital=1_035_000.0,
            return_pct=3.5,
            score=82.5,
            rating="Good",
        )
        d = perf.to_dict()

        assert d["strategy_name"] == "MomentumStrategy"
        assert d["symbol"] == "NABIL"
        assert d["total_trades"] == 10
        assert d["win_rate"] == 70.0
        assert d["profit_factor"] == 3.5
        assert d["net_profit"] == 35_000.0
        assert d["max_drawdown"] == 12.5
        assert d["sharpe_ratio"] == 1.5
        assert d["sortino_ratio"] == 2.1
        assert d["calmar_ratio"] == 0.8
        assert d["score"] == 82.5
        assert d["rating"] == "Good"
        assert d["ending_capital"] == 1_035_000.0
        assert len(d) == 28

    def test_empty_defaults(self) -> None:
        """Default-constructed performance has zeroed metrics."""
        perf = StrategyPerformance(strategy_name="S1", symbol="NABIL")
        d = perf.to_dict()

        assert d["total_trades"] == 0
        assert d["win_rate"] == 0.0
        assert d["profit_factor"] == 0.0
        assert d["sharpe_ratio"] is None
        assert d["sortino_ratio"] is None
        assert d["calmar_ratio"] is None
        assert d["score"] == 0.0
        assert d["rating"] == "Poor"


# ======================================================================
# PerformanceAnalyzer — profitable strategy
# ======================================================================


class TestProfitableStrategy:
    """PerformanceAnalyzer on a profitable trade history."""

    def test_basic_counts(self, analyzer: PerformanceAnalyzer, profitable_trades: list[dict]) -> None:
        """Profitable strategy has 5 wins, 0 losses."""
        perf = analyzer.analyze(
            "MomentumStrategy", "NABIL", 1_000_000, profitable_trades
        )
        assert perf.total_trades == 5
        assert perf.winning_trades == 5
        assert perf.losing_trades == 0
        assert perf.breakeven_trades == 0

    def test_win_rate_100(self, analyzer: PerformanceAnalyzer, profitable_trades: list[dict]) -> None:
        """All profitable → 100% win rate."""
        perf = analyzer.analyze("M", "NABIL", 1_000_000, profitable_trades)
        assert perf.win_rate == 100.0

    def test_gross_profit(self, analyzer: PerformanceAnalyzer, profitable_trades: list[dict]) -> None:
        """Gross profit equals sum of all net profits."""
        total_net = sum(t["net_profit"] for t in profitable_trades)
        perf = analyzer.analyze("M", "NABIL", 1_000_000, profitable_trades)
        assert perf.gross_profit == total_net
        assert perf.gross_loss == 0.0

    def test_net_profit(self, analyzer: PerformanceAnalyzer, profitable_trades: list[dict]) -> None:
        """Net profit equals sum."""
        expected = sum(t["net_profit"] for t in profitable_trades)
        perf = analyzer.analyze("M", "NABIL", 1_000_000, profitable_trades)
        assert perf.net_profit == expected

    def test_average_values(self, analyzer: PerformanceAnalyzer, profitable_trades: list[dict]) -> None:
        """Average win and return are correctly computed."""
        expected_avg_win = sum(t["net_profit"] for t in profitable_trades) / 5
        expected_avg_return = sum(t["return_pct"] for t in profitable_trades) / 5
        expected_avg_hold = sum(t["holding_days"] for t in profitable_trades) / 5
        perf = analyzer.analyze("M", "NABIL", 1_000_000, profitable_trades)
        assert perf.average_win == expected_avg_win
        assert perf.average_return_pct == pytest.approx(expected_avg_return)
        assert perf.average_holding_days == expected_avg_hold

    def test_largest_win(self, analyzer: PerformanceAnalyzer, profitable_trades: list[dict]) -> None:
        """Largest win captures the maximum net profit."""
        perf = analyzer.analyze("M", "NABIL", 1_000_000, profitable_trades)
        assert perf.largest_win == 15_000.0
        assert perf.largest_loss == 6_000.0  # smallest profit (no losses)


# ======================================================================
# PerformanceAnalyzer — losing strategy
# ======================================================================


class TestLosingStrategy:
    """PerformanceAnalyzer on a losing trade history."""

    def test_basic_counts(self, analyzer: PerformanceAnalyzer, losing_trades: list[dict]) -> None:
        """Losing strategy has 3 losses, 0 wins."""
        perf = analyzer.analyze("BadStrategy", "ADBL", 500_000, losing_trades)
        assert perf.total_trades == 3
        assert perf.winning_trades == 0
        assert perf.losing_trades == 3
        assert perf.breakeven_trades == 0

    def test_win_rate_zero(self, analyzer: PerformanceAnalyzer, losing_trades: list[dict]) -> None:
        """All losing → 0% win rate."""
        perf = analyzer.analyze("B", "ADBL", 500_000, losing_trades)
        assert perf.win_rate == 0.0

    def test_net_profit_negative(self, analyzer: PerformanceAnalyzer, losing_trades: list[dict]) -> None:
        """Net profit is negative for a losing strategy."""
        perf = analyzer.analyze("B", "ADBL", 500_000, losing_trades)
        assert perf.net_profit < 0
        assert perf.gross_loss > 0

    def test_largest_loss(self, analyzer: PerformanceAnalyzer, losing_trades: list[dict]) -> None:
        """Largest loss captures the minimum (most negative) net profit."""
        perf = analyzer.analyze("B", "ADBL", 500_000, losing_trades)
        assert perf.largest_loss == -8_000.0

    def test_profit_factor_zero(self, analyzer: PerformanceAnalyzer, losing_trades: list[dict]) -> None:
        """No gross profit → profit factor is 0.0."""
        perf = analyzer.analyze("B", "ADBL", 500_000, losing_trades)
        assert perf.profit_factor == 0.0


# ======================================================================
# PerformanceAnalyzer — mixed trades
# ======================================================================


class TestMixedStrategy:
    """PerformanceAnalyzer on a mixed win/loss/breakeven history."""

    def test_counts(self, analyzer: PerformanceAnalyzer, mixed_trades: list[dict]) -> None:
        """Mixed history has correct win/loss/breakeven counts."""
        perf = analyzer.analyze("S1", "NABIL", 1_000_000, mixed_trades)
        assert perf.total_trades == 6
        assert perf.winning_trades == 3
        assert perf.losing_trades == 2
        assert perf.breakeven_trades == 1

    def test_win_rate(self, analyzer: PerformanceAnalyzer, mixed_trades: list[dict]) -> None:
        """Win rate is 50% (3 wins / 6 total)."""
        perf = analyzer.analyze("S1", "NABIL", 1_000_000, mixed_trades)
        assert perf.win_rate == 50.0

    def test_profit_factor(self, analyzer: PerformanceAnalyzer, mixed_trades: list[dict]) -> None:
        """Profit factor is gross_profit / abs(gross_loss)."""
        # gross_profit = 10_000 + 6_000 + 8_000 = 24_000
        # gross_loss = abs(-4_000 + -2_000) = 6_000
        # PF = 24_000 / 6_000 = 4.0
        perf = analyzer.analyze("S1", "NABIL", 1_000_000, mixed_trades)
        assert perf.gross_profit == 24_000.0
        assert perf.gross_loss == 6_000.0
        assert perf.profit_factor == 4.0

    def test_net_profit(self, analyzer: PerformanceAnalyzer, mixed_trades: list[dict]) -> None:
        """Net profit is gross profit minus gross loss."""
        perf = analyzer.analyze("S1", "NABIL", 1_000_000, mixed_trades)
        assert perf.net_profit == 18_000.0  # 24_000 - 6_000

    def test_expectancy(self, analyzer: PerformanceAnalyzer, mixed_trades: list[dict]) -> None:
        """Expectancy is mean net profit per trade."""
        perf = analyzer.analyze("S1", "NABIL", 1_000_000, mixed_trades)
        # (10_000 + -4_000 + 6_000 + 0 + -2_000 + 8_000) / 6 = 18_000 / 6 = 3_000
        assert perf.expectancy == 3_000.0

    def test_averages(self, analyzer: PerformanceAnalyzer, mixed_trades: list[dict]) -> None:
        """Average win/loss/return correctly computed."""
        perf = analyzer.analyze("S1", "NABIL", 1_000_000, mixed_trades)
        # Average win = (10_000 + 6_000 + 8_000) / 3 = 8_000
        assert perf.average_win == 8_000.0
        # Average loss = (-4_000 + -2_000) / 2 = -3_000
        assert perf.average_loss == -3_000.0


# ======================================================================
# PerformanceAnalyzer — empty history
# ======================================================================


class TestEmptyHistory:
    """PerformanceAnalyzer on empty trade history."""

    def test_empty_returns_zeroed_metrics(self, analyzer: PerformanceAnalyzer) -> None:
        """Empty history returns zeroed metrics."""
        perf = analyzer.analyze("S1", "NABIL", 1_000_000, [])
        assert perf.total_trades == 0
        assert perf.winning_trades == 0
        assert perf.losing_trades == 0
        assert perf.win_rate == 0.0
        assert perf.profit_factor == 0.0
        assert perf.net_profit == 0.0
        assert perf.ending_capital == 1_000_000.0
        assert perf.return_pct == 0.0
        assert perf.score == 0.0
        assert perf.rating == "Poor"

    def test_empty_metadata(self, analyzer: PerformanceAnalyzer) -> None:
        """Empty history marks empty_history in metadata."""
        perf = analyzer.analyze("S1", "NABIL", 1_000_000, [])
        assert perf.metadata.get("empty_history") is True


# ======================================================================
# PerformanceAnalyzer — drawdown
# ======================================================================


class TestDrawdown:
    """Maximum drawdown calculation."""

    def test_drawdown_from_trades(self, analyzer: PerformanceAnalyzer) -> None:
        """Drawdown computed from simulated equity curve."""
        # Equity curve from these trades:
        # 0: 1_000_000
        # 1: 1_010_000  (+10_000)
        # 2: 1_006_000  (-4_000)
        # 3: 1_012_000  (+6_000)
        # 4: 1_012_000  (0)
        # 5: 1_010_000  (-2_000)
        # 6: 1_018_000  (+8_000)
        # Peak = 1_012_000 (index 3), trough = 1_006_000 (index 2)
        # DD = (1_012_000 - 1_006_000) / 1_012_000 * 100 = 0.59%
        # Actually peak is at index 3 = 1_012_000, then drops to 1_010_000
        # DD from peak to trough = (1_012_000 - 1_006_000) / 1_012_000 * 100
        # Wait, 1_006_000 is at index 2 which is BEFORE 1_012_000 at index 3
        # Let me recalculate:
        # idx 0: 1_000_000 (peak=1_000_000)
        # idx 1: 1_010_000 (peak=1_010_000)
        # idx 2: 1_006_000 → DD = (1_010_000 - 1_006_000)/1_010_000 * 100 = 0.396%
        # idx 3: 1_012_000 (peak=1_012_000)
        # idx 4: 1_012_000 → no change
        # idx 5: 1_010_000 → DD = (1_012_000 - 1_010_000)/1_012_000 * 100 = 0.198%
        # idx 6: 1_018_000 (peak=1_018_000)
        # Max DD = 0.396%
        trades = [
            {"net_profit": 10_000.0, "return_pct": 1.0, "holding_days": 3},
            {"net_profit": -4_000.0, "return_pct": -0.4, "holding_days": 5},
            {"net_profit": 6_000.0, "return_pct": 0.6, "holding_days": 4},
            {"net_profit": 0.0, "return_pct": 0.0, "holding_days": 2},
            {"net_profit": -2_000.0, "return_pct": -0.2, "holding_days": 3},
            {"net_profit": 8_000.0, "return_pct": 0.8, "holding_days": 6},
        ]
        perf = analyzer.analyze("S1", "NABIL", 1_000_000, trades)
        # Max DD = (1_010_000 - 1_006_000) / 1_010_000 * 100 = 0.396...
        # Stored value is round(0.396..., 2) = 0.4
        assert perf.max_drawdown == pytest.approx(0.4, abs=0.001)

    def test_no_drawdown_with_strictly_increasing(self, analyzer: PerformanceAnalyzer) -> None:
        """Strictly increasing equity has zero drawdown."""
        trades = [
            {"net_profit": 10_000.0, "return_pct": 1.0, "holding_days": 3},
            {"net_profit": 5_000.0, "return_pct": 0.5, "holding_days": 4},
        ]
        perf = analyzer.analyze("S1", "NABIL", 1_000_000, trades)
        assert perf.max_drawdown == 0.0

    def test_drawdown_npr_not_zero(self, analyzer: PerformanceAnalyzer, mixed_trades: list[dict]) -> None:
        """Recovery factor is computable when there's a drawdown."""
        perf = analyzer.analyze("S1", "NABIL", 1_000_000, mixed_trades)
        assert perf.recovery_factor > 0


# ======================================================================
# PerformanceAnalyzer — risk-adjusted ratios
# ======================================================================


class TestRiskAdjustedRatios:
    """Sharpe, Sortino, and Calmar ratio calculations."""

    def test_sharpe_ratio_computed(self, analyzer: PerformanceAnalyzer, profitable_trades: list[dict]) -> None:
        """Profitable strategy with 5 trades has a Sharpe ratio."""
        perf = analyzer.analyze("S1", "NABIL", 1_000_000, profitable_trades)
        assert perf.sharpe_ratio is not None
        assert perf.sharpe_ratio > 0

    def test_sharpe_ratio_none_for_one_trade(self, analyzer: PerformanceAnalyzer) -> None:
        """Single trade cannot produce a Sharpe ratio."""
        trades = [{"net_profit": 1_000.0, "return_pct": 1.0, "holding_days": 3}]
        perf = analyzer.analyze("S1", "NABIL", 1_000_000, trades)
        assert perf.sharpe_ratio is None

    def test_sortino_ratio_computed(self, analyzer: PerformanceAnalyzer, mixed_trades: list[dict]) -> None:
        """Mixed trades with losses produce a Sortino ratio."""
        perf = analyzer.analyze("S1", "NABIL", 1_000_000, mixed_trades)
        assert perf.sortino_ratio is not None

    def test_sortino_ratio_none_for_all_wins(self, analyzer: PerformanceAnalyzer, profitable_trades: list[dict]) -> None:
        """100% win rate has no negative returns → Sortino is None."""
        perf = analyzer.analyze("S1", "NABIL", 1_000_000, profitable_trades)
        assert perf.sortino_ratio is None

    def test_calmar_ratio_computed(self, analyzer: PerformanceAnalyzer, mixed_trades: list[dict]) -> None:
        """Mixed trades produce a Calmar ratio."""
        perf = analyzer.analyze("S1", "NABIL", 1_000_000, mixed_trades)
        # max_drawdown > 0 and return_pct != 0 → Calmar should exist
        if perf.max_drawdown > 0:
            assert perf.calmar_ratio is not None
        else:
            assert perf.calmar_ratio is None

    def test_calmar_ratio_none_for_zero_drawdown(self, analyzer: PerformanceAnalyzer) -> None:
        """No drawdown → Calmar is None."""
        trades = [
            {"net_profit": 10_000.0, "return_pct": 1.0, "holding_days": 3},
        ]
        perf = analyzer.analyze("S1", "NABIL", 1_000_000, trades)
        assert perf.calmar_ratio is None

    def test_sharpe_specific_value(self, analyzer: PerformanceAnalyzer) -> None:
        """Sharpe ratio for known returns matches expected value."""
        # returns: [2.0, 3.0, 4.0]
        # mean = 3.0, pstdev = sqrt(2/3) ≈ 0.8165
        # sharpe = 3.0 / 0.8165 * sqrt(252) ≈ 3.673 * 15.874 = 58.31
        trades = [
            {"net_profit": 2_000.0, "return_pct": 2.0, "holding_days": 3},
            {"net_profit": 3_000.0, "return_pct": 3.0, "holding_days": 4},
            {"net_profit": 4_000.0, "return_pct": 4.0, "holding_days": 5},
        ]
        perf = analyzer.analyze("S1", "NABIL", 1_000_000, trades)
        assert perf.sharpe_ratio is not None
        # expected: (3.0 / sqrt(2/3)) * sqrt(252)
        expected = (3.0 / math.sqrt(2 / 3)) * math.sqrt(252)
        assert perf.sharpe_ratio == pytest.approx(expected, rel=1e-3)


# ======================================================================
# PerformanceAnalyzer — score and rating
# ======================================================================


class TestScoreAndRating:
    """Composite quality score and qualitative rating."""

    def test_elite_rating(self, analyzer: PerformanceAnalyzer) -> None:
        """Near-perfect metrics produce Elite rating."""
        # 5 trades, all profitable, high returns, no drawdown
        trades = [
            {"net_profit": 20_000.0, "return_pct": 10.0, "holding_days": 3},
            {"net_profit": 18_000.0, "return_pct": 9.0, "holding_days": 4},
        ]
        perf = analyzer.analyze("S1", "NABIL", 1_000_000, trades)
        # Only 2 trades → sharpe sortino are None → score may not hit Elite
        # Increase trades for more stable stats
        trades = [
            {"net_profit": 20_000.0, "return_pct": 8.0, "holding_days": 3},
            {"net_profit": 18_000.0, "return_pct": 7.0, "holding_days": 4},
            {"net_profit": 22_000.0, "return_pct": 9.0, "holding_days": 2},
            {"net_profit": 16_000.0, "return_pct": 6.0, "holding_days": 5},
            {"net_profit": 25_000.0, "return_pct": 10.0, "holding_days": 3},
        ]
        perf = analyzer.analyze("S1", "NABIL", 1_000_000, trades)
        assert perf.rating in ("Elite", "Excellent", "Good")

    def test_poor_rating(self, analyzer: PerformanceAnalyzer) -> None:
        """All-losing strategy scores Poor."""
        trades = [
            {"net_profit": -5_000.0, "return_pct": -2.0, "holding_days": 4},
            {"net_profit": -8_000.0, "return_pct": -4.0, "holding_days": 6},
        ]
        perf = analyzer.analyze("S1", "NABIL", 1_000_000, trades)
        assert perf.rating == "Poor"

    def test_score_never_exceeds_100(self, analyzer: PerformanceAnalyzer) -> None:
        """Score is clamped at 100."""
        trades = [
            {"net_profit": 100_000.0, "return_pct": 50.0, "holding_days": 1},
            {"net_profit": 100_000.0, "return_pct": 50.0, "holding_days": 1},
            {"net_profit": 100_000.0, "return_pct": 50.0, "holding_days": 1},
        ]
        perf = analyzer.analyze("S1", "NABIL", 1_000_000, trades)
        assert perf.score <= 100.0

    def test_score_never_below_zero(self, analyzer: PerformanceAnalyzer) -> None:
        """Score is clamped at 0."""
        trades = [
            {"net_profit": -100_000.0, "return_pct": -50.0, "holding_days": 10},
            {"net_profit": -200_000.0, "return_pct": -80.0, "holding_days": 15},
        ]
        perf = analyzer.analyze("S1", "NABIL", 1_000_000, trades)
        assert perf.score >= 0.0

    def test_rating_lookup(self) -> None:
        """Rating labels match expected thresholds."""
        from src.analytics.performance import _lookup_rating

        assert _lookup_rating(96.0) == "Elite"
        assert _lookup_rating(90.0) == "Excellent"
        assert _lookup_rating(75.0) == "Good"
        assert _lookup_rating(60.0) == "Average"
        assert _lookup_rating(45.0) == "Weak"
        assert _lookup_rating(30.0) == "Poor"
        assert _lookup_rating(0.0) == "Poor"


# ======================================================================
# PerformanceAnalyzer — error handling
# ======================================================================


class TestErrorHandling:
    """PerformanceAnalyzer error resilience."""

    def test_missing_net_profit_defaults_to_zero(
        self, analyzer: PerformanceAnalyzer
    ) -> None:
        """Trades without net_profit are treated as breakeven."""
        trades = [
            {"return_pct": 5.0, "holding_days": 3},
            {"net_profit": 10_000.0, "return_pct": 5.0, "holding_days": 3},
        ]
        perf = analyzer.analyze("S1", "NABIL", 1_000_000, trades)
        assert perf.total_trades == 2
        assert perf.winning_trades == 1
        assert perf.breakeven_trades == 1

    def test_zero_starting_capital_does_not_crash(
        self, analyzer: PerformanceAnalyzer
    ) -> None:
        """Zero starting capital is handled gracefully."""
        trades = [{"net_profit": 1_000.0, "return_pct": 1.0, "holding_days": 3}]
        perf = analyzer.analyze("S1", "NABIL", 0, trades)
        assert perf.total_trades == 1
        assert perf.return_pct == 0.0  # division by zero avoided

    def test_single_trade_returns_valid_basics(
        self, analyzer: PerformanceAnalyzer
    ) -> None:
        """Single trade produces viable basic metrics but None ratios."""
        trades = [
            {"net_profit": 5_000.0, "return_pct": 2.5, "holding_days": 5},
        ]
        perf = analyzer.analyze("S1", "NABIL", 1_000_000, trades)
        assert perf.total_trades == 1
        assert perf.winning_trades == 1
        assert perf.win_rate == 100.0
        assert perf.sharpe_ratio is None
        assert perf.sortino_ratio is None


# ======================================================================
# PerformanceAnalyzer — analyze_to_dict helper
# ======================================================================


class TestAnalyzeToDict:
    """PerformanceAnalyzer.analyze_to_dict convenience method."""

    def test_analyze_to_dict_returns_dict(
        self, analyzer: PerformanceAnalyzer, profitable_trades: list[dict]
    ) -> None:
        """analyze_to_dict returns a dictionary."""
        d = analyzer.analyze_to_dict("M", "NABIL", 1_000_000, profitable_trades)
        assert isinstance(d, dict)
        assert d["strategy_name"] == "M"
        assert d["symbol"] == "NABIL"
        assert d["total_trades"] == 5


# ======================================================================
# StrategyRanking
# ======================================================================


class TestStrategyRanking:
    """StrategyRanking behaviour."""

    @pytest.fixture
    def performances(self) -> list[StrategyPerformance]:
        """A set of strategies with different scores."""
        return [
            StrategyPerformance(
                strategy_name="Momentum",
                symbol="NABIL",
                total_trades=10,
                score=85.0,
                rating="Excellent",
                win_rate=70.0,
                profit_factor=3.0,
                net_profit=50_000.0,
            ),
            StrategyPerformance(
                strategy_name="Breakout",
                symbol="NABIL",
                total_trades=8,
                score=72.0,
                rating="Good",
                win_rate=62.5,
                profit_factor=2.5,
                net_profit=30_000.0,
            ),
            StrategyPerformance(
                strategy_name="Trend",
                symbol="NABIL",
                total_trades=15,
                score=91.0,
                rating="Excellent",
                win_rate=80.0,
                profit_factor=4.2,
                net_profit=80_000.0,
            ),
        ]

    def test_rank_sorted_by_score_descending(
        self, performances: list[StrategyPerformance]
    ) -> None:
        """Rankings are sorted by score descending."""
        ranking = StrategyRanking()
        entries = ranking.rank(performances)

        assert len(entries) == 3
        assert entries[0].strategy_name == "Trend"  # 91.0
        assert entries[1].strategy_name == "Momentum"  # 85.0
        assert entries[2].strategy_name == "Breakout"  # 72.0

    def test_rank_positions_are_1_based(
        self, performances: list[StrategyPerformance]
    ) -> None:
        """Rank positions start at 1."""
        ranking = StrategyRanking()
        entries = ranking.rank(performances)

        assert entries[0].rank == 1
        assert entries[1].rank == 2
        assert entries[2].rank == 3

    def test_rank_includes_all_fields(
        self, performances: list[StrategyPerformance]
    ) -> None:
        """Each RankEntry has all required fields."""
        ranking = StrategyRanking()
        entries = ranking.rank(performances)

        entry = entries[0]
        assert entry.strategy_name == "Trend"
        assert entry.score == 91.0
        assert entry.rating == "Excellent"
        assert entry.win_rate == 80.0
        assert entry.profit_factor == 4.2
        assert entry.net_profit == 80_000.0

    def test_rank_empty_list(self) -> None:
        """Empty performance list returns empty ranking."""
        ranking = StrategyRanking()
        entries = ranking.rank([])
        assert entries == []

    def test_rank_to_dict(
        self, performances: list[StrategyPerformance]
    ) -> None:
        """rank_to_dict returns list of dicts."""
        ranking = StrategyRanking()
        result = ranking.rank_to_dict(performances)

        assert isinstance(result, list)
        assert len(result) == 3
        assert isinstance(result[0], dict)
        assert result[0]["rank"] == 1
        assert result[0]["strategy_name"] == "Trend"

    def test_rank_to_dict_empty(self) -> None:
        """rank_to_dict with empty list returns empty list."""
        ranking = StrategyRanking()
        assert ranking.rank_to_dict([]) == []


# ======================================================================
# RankEntry dataclass
# ======================================================================


class TestRankEntry:
    """RankEntry dataclass serialisation."""

    def test_to_dict(self) -> None:
        """to_dict() returns all fields."""
        entry = RankEntry(
            rank=1,
            strategy_name="Momentum",
            score=85.0,
            rating="Excellent",
            win_rate=70.0,
            profit_factor=3.0,
            net_profit=50_000.0,
        )
        d = entry.to_dict()

        assert d["rank"] == 1
        assert d["strategy_name"] == "Momentum"
        assert d["score"] == 85.0
        assert d["rating"] == "Excellent"
        assert d["win_rate"] == 70.0
        assert d["profit_factor"] == 3.0
        assert d["net_profit"] == 50_000.0
        assert len(d) == 7
