"""Tests for the position sizing and portfolio risk engine."""

from __future__ import annotations

import pytest

from src.risk.engine import RiskEngine, RiskPosition


# ======================================================================
# Constructor validation
# ======================================================================


class TestRiskEngineConstructor:
    """RiskEngine.__init__ validation."""

    def test_invalid_capital_zero(self) -> None:
        """Zero capital raises ValueError."""
        with pytest.raises(ValueError, match="Capital must be positive"):
            RiskEngine(capital=0)

    def test_invalid_capital_negative(self) -> None:
        """Negative capital raises ValueError."""
        with pytest.raises(ValueError, match="Capital must be positive"):
            RiskEngine(capital=-1000)

    def test_invalid_risk_per_trade_zero(self) -> None:
        """risk_per_trade_pct=0 raises ValueError."""
        with pytest.raises(
            ValueError, match="risk_per_trade_pct must be in"
        ):
            RiskEngine(capital=100_000, risk_per_trade_pct=0)

    def test_invalid_risk_per_trade_negative(self) -> None:
        """risk_per_trade_pct < 0 raises ValueError."""
        with pytest.raises(
            ValueError, match="risk_per_trade_pct must be in"
        ):
            RiskEngine(capital=100_000, risk_per_trade_pct=-0.01)

    def test_invalid_risk_per_trade_over_one(self) -> None:
        """risk_per_trade_pct > 1 raises ValueError."""
        with pytest.raises(
            ValueError, match="risk_per_trade_pct must be in"
        ):
            RiskEngine(capital=100_000, risk_per_trade_pct=1.5)

    def test_invalid_max_portfolio_risk_zero(self) -> None:
        """max_portfolio_risk_pct=0 raises ValueError."""
        with pytest.raises(
            ValueError, match="max_portfolio_risk_pct must be in"
        ):
            RiskEngine(capital=100_000, max_portfolio_risk_pct=0)

    def test_invalid_max_portfolio_risk_negative(self) -> None:
        """max_portfolio_risk_pct < 0 raises ValueError."""
        with pytest.raises(
            ValueError, match="max_portfolio_risk_pct must be in"
        ):
            RiskEngine(capital=100_000, max_portfolio_risk_pct=-0.05)

    def test_invalid_max_portfolio_risk_over_one(self) -> None:
        """max_portfolio_risk_pct > 1 raises ValueError."""
        with pytest.raises(
            ValueError, match="max_portfolio_risk_pct must be in"
        ):
            RiskEngine(capital=100_000, max_portfolio_risk_pct=1.2)

    def test_valid_construction_defaults(self) -> None:
        """Default parameters create a usable engine."""
        engine = RiskEngine(capital=100_000)
        assert engine is not None

    def test_valid_construction_custom(self) -> None:
        """Custom parameters create a usable engine."""
        engine = RiskEngine(
            capital=500_000,
            risk_per_trade_pct=0.03,
            max_portfolio_risk_pct=0.15,
        )
        assert engine is not None


# ======================================================================
# calculate_position — validation
# ======================================================================


class TestCalculatePositionValidation:
    """RiskEngine.calculate_position input validation."""

    def test_entry_price_zero(self) -> None:
        """entry_price=0 raises ValueError."""
        engine = RiskEngine(capital=100_000)
        with pytest.raises(ValueError, match="entry_price must be positive"):
            engine.calculate_position("NABIL", entry_price=0, stop_loss=95)

    def test_entry_price_negative(self) -> None:
        """entry_price < 0 raises ValueError."""
        engine = RiskEngine(capital=100_000)
        with pytest.raises(ValueError, match="entry_price must be positive"):
            engine.calculate_position("NABIL", entry_price=-50, stop_loss=95)

    def test_stop_loss_zero(self) -> None:
        """stop_loss=0 raises ValueError."""
        engine = RiskEngine(capital=100_000)
        with pytest.raises(ValueError, match="stop_loss must be positive"):
            engine.calculate_position("NABIL", entry_price=100, stop_loss=0)

    def test_stop_loss_negative(self) -> None:
        """stop_loss < 0 raises ValueError."""
        engine = RiskEngine(capital=100_000)
        with pytest.raises(ValueError, match="stop_loss must be positive"):
            engine.calculate_position("NABIL", entry_price=100, stop_loss=-10)

    def test_stop_loss_equals_entry(self) -> None:
        """stop_loss == entry_price raises ValueError."""
        engine = RiskEngine(capital=100_000)
        with pytest.raises(
            ValueError, match="must be less than entry_price"
        ):
            engine.calculate_position("NABIL", entry_price=100, stop_loss=100)

    def test_stop_loss_above_entry(self) -> None:
        """stop_loss > entry_price raises ValueError."""
        engine = RiskEngine(capital=100_000)
        with pytest.raises(
            ValueError, match="must be less than entry_price"
        ):
            engine.calculate_position("NABIL", entry_price=100, stop_loss=110)


# ======================================================================
# calculate_position — correct values
# ======================================================================


class TestCalculatePositionValues:
    """RiskEngine.calculate_position arithmetic."""

    def test_shares_calculation(self) -> None:
        """Shares are correctly calculated as floor(capital_at_risk / risk_per_share)."""
        engine = RiskEngine(capital=100_000, risk_per_trade_pct=0.02)
        # capital_at_risk = 100_000 * 0.02 = 2_000
        # risk_per_share = 100 - 90 = 10
        # shares = floor(2_000 / 10) = 200
        result = engine.calculate_position("NABIL", entry_price=100, stop_loss=90)
        assert result.shares == 200

    def test_shares_rounds_down(self) -> None:
        """Shares are always rounded down (floor)."""
        engine = RiskEngine(capital=100_000, risk_per_trade_pct=0.02)
        # capital_at_risk = 2_000
        # risk_per_share = 150 - 130 = 20
        # shares = floor(2_000 / 20) = 100
        result = engine.calculate_position("NABIL", entry_price=150, stop_loss=130)
        assert result.shares == 100

    def test_position_value(self) -> None:
        """Position value is shares x entry_price."""
        engine = RiskEngine(capital=100_000, risk_per_trade_pct=0.02)
        result = engine.calculate_position("NABIL", entry_price=500, stop_loss=480)
        # capital_at_risk = 2_000
        # risk_per_share = 20
        # shares = floor(2_000 / 20) = 100
        # position_value = 100 * 500 = 50_000
        assert result.position_value == 50_000.0

    def test_capital_at_risk(self) -> None:
        """Capital at risk equals capital x risk_per_trade_pct."""
        engine = RiskEngine(capital=200_000, risk_per_trade_pct=0.03)
        result = engine.calculate_position("NABIL", entry_price=100, stop_loss=95)
        # capital_at_risk = 200_000 * 0.03 = 6_000
        assert result.capital_at_risk == 6_000.0

    def test_risk_per_share(self) -> None:
        """Risk per share is entry_price - stop_loss."""
        engine = RiskEngine(capital=100_000)
        result = engine.calculate_position("NABIL", entry_price=950, stop_loss=920)
        assert result.risk_per_share == 30.0

    def test_risk_pct(self) -> None:
        """Risk percentage reflects risk_per_trade_pct."""
        engine = RiskEngine(capital=100_000, risk_per_trade_pct=0.05)
        result = engine.calculate_position("NABIL", entry_price=100, stop_loss=90)
        assert result.risk_pct == 5.0

    def test_custom_risk_per_trade(self) -> None:
        """Custom risk_per_trade_pct changes capital at risk."""
        engine = RiskEngine(capital=100_000, risk_per_trade_pct=0.01)
        # capital_at_risk = 1_000
        result = engine.calculate_position("NABIL", entry_price=100, stop_loss=95)
        assert result.capital_at_risk == 1_000.0
        # risk_per_share = 5
        # shares = floor(1_000 / 5) = 200
        assert result.shares == 200

    def test_symbol_preserved(self) -> None:
        """Symbol is preserved in the output."""
        engine = RiskEngine(capital=100_000)
        result = engine.calculate_position("SBI", entry_price=400, stop_loss=380)
        assert result.symbol == "SBI"


# ======================================================================
# portfolio_risk
# ======================================================================


class TestPortfolioRisk:
    """RiskEngine.portfolio_risk behaviour."""

    def test_empty_list(self) -> None:
        """Empty position list returns zero summary within limit."""
        engine = RiskEngine(capital=100_000)
        result = engine.portfolio_risk([])

        assert result["positions"] == 0
        assert result["total_capital_at_risk"] == 0.0
        assert result["portfolio_risk_pct"] == 0.0
        assert result["within_limit"] is True

    def test_single_position_within_limit(self) -> None:
        """Single position within max portfolio risk."""
        engine = RiskEngine(capital=100_000, risk_per_trade_pct=0.02)
        pos = engine.calculate_position("NABIL", 100, 95)
        # capital_at_risk = 2_000
        # max_portfolio_risk = 10% of 100_000 = 10_000
        result = engine.portfolio_risk([pos])

        assert result["positions"] == 1
        assert result["total_capital_at_risk"] == 2_000.0
        assert result["portfolio_risk_pct"] == 2.0
        assert result["within_limit"] is True
        assert result["remaining_risk_capacity"] == 8_000.0

    def test_multiple_positions_exceeds_limit(self) -> None:
        """Multiple positions can exceed max portfolio risk."""
        engine = RiskEngine(
            capital=100_000,
            risk_per_trade_pct=0.06,
            max_portfolio_risk_pct=0.10,
        )
        # Each position: capital_at_risk = 6_000
        pos_a = engine.calculate_position("NABIL", 100, 94)
        pos_b = engine.calculate_position("ADBL", 200, 194)
        # total = 12_000 > 10_000 max
        result = engine.portfolio_risk([pos_a, pos_b])

        assert result["total_capital_at_risk"] == 12_000.0
        assert result["portfolio_risk_pct"] == 12.0
        assert result["within_limit"] is False
        # remaining = 10_000 - 12_000 = -2_000
        assert result["remaining_risk_capacity"] == -2_000.0

    def test_remaining_capacity_positive(self) -> None:
        """Remaining capacity is positive when under limit."""
        engine = RiskEngine(capital=100_000, risk_per_trade_pct=0.02)
        pos = engine.calculate_position("NABIL", 100, 95)
        result = engine.portfolio_risk([pos])

        assert result["remaining_risk_capacity"] > 0


# ======================================================================
# calculate_multiple
# ======================================================================


class TestCalculateMultiple:
    """RiskEngine.calculate_multiple behaviour."""

    def test_multiple_valid_positions(self) -> None:
        """All valid inputs produce RiskPosition instances."""
        engine = RiskEngine(capital=100_000)
        inputs = [
            {"symbol": "NABIL", "entry_price": 500, "stop_loss": 480},
            {"symbol": "ADBL", "entry_price": 300, "stop_loss": 285},
            {"symbol": "NICA", "entry_price": 200, "stop_loss": 190},
        ]
        results = engine.calculate_multiple(inputs)

        assert len(results) == 3
        assert all(isinstance(p, RiskPosition) for p in results)
        assert results[0].symbol == "NABIL"
        assert results[1].symbol == "ADBL"
        assert results[2].symbol == "NICA"

    def test_skips_invalid_inputs(self) -> None:
        """Invalid entries are skipped, valid ones still returned."""
        engine = RiskEngine(capital=100_000)
        inputs = [
            {"symbol": "NABIL", "entry_price": 500, "stop_loss": 480},
            {"symbol": "BAD", "entry_price": 100, "stop_loss": 100},  # equal
            {"symbol": "MISSING", "entry_price": 100},  # missing stop_loss
            {"symbol": "GOOD", "entry_price": 200, "stop_loss": 190},
        ]
        results = engine.calculate_multiple(inputs)

        assert len(results) == 2
        symbols = [r.symbol for r in results]
        assert "NABIL" in symbols
        assert "GOOD" in symbols
        assert "BAD" not in symbols

    def test_empty_input_list(self) -> None:
        """Empty input list returns empty list."""
        engine = RiskEngine(capital=100_000)
        results = engine.calculate_multiple([])
        assert results == []

    def test_all_invalid_returns_empty(self) -> None:
        """All invalid inputs returns empty list."""
        engine = RiskEngine(capital=100_000)
        inputs = [
            {"symbol": "A", "entry_price": 100, "stop_loss": 100},
            {"symbol": "B", "entry_price": -50, "stop_loss": 10},
        ]
        results = engine.calculate_multiple(inputs)
        assert results == []


# ======================================================================
# RiskPosition dataclass
# ======================================================================


class TestRiskPosition:
    """RiskPosition dataclass behaviour."""

    def test_to_dict_returns_all_fields(self) -> None:
        """to_dict() returns all eight fields."""
        pos = RiskPosition(
            symbol="NABIL",
            entry_price=500.0,
            stop_loss=480.0,
            risk_per_share=20.0,
            capital_at_risk=2_000.0,
            shares=100,
            position_value=50_000.0,
            risk_pct=2.0,
        )
        d = pos.to_dict()

        assert d["symbol"] == "NABIL"
        assert d["entry_price"] == 500.0
        assert d["stop_loss"] == 480.0
        assert d["risk_per_share"] == 20.0
        assert d["capital_at_risk"] == 2_000.0
        assert d["shares"] == 100
        assert d["position_value"] == 50_000.0
        assert d["risk_pct"] == 2.0
        assert len(d) == 8


# ======================================================================
# Edge cases
# ======================================================================


class TestEdgeCases:
    """Edge cases and boundary conditions."""

    def test_large_risk_distance_more_shares(self) -> None:
        """Wider stop means more shares for the same capital at risk."""
        engine = RiskEngine(capital=100_000, risk_per_trade_pct=0.02)
        # risk_per_share = 1
        wide = engine.calculate_position("WIDE", entry_price=101, stop_loss=100)
        # risk_per_share = 10
        tight = engine.calculate_position("TIGHT", entry_price=110, stop_loss=100)

        assert wide.shares > tight.shares

    def test_very_tight_stop_maximises_shares(self) -> None:
        """Very tight stop-loss results in maximum possible shares."""
        engine = RiskEngine(capital=100_000, risk_per_trade_pct=0.02)
        # risk_per_share = 100.50 - 100.00 = 0.50 (exact in binary)
        # shares = floor(2_000 / 0.50) = 4_000
        result = engine.calculate_position(
            "TIGHT", entry_price=100.50, stop_loss=100.00
        )
        assert result.shares == 4_000
        # Verify tight stop logic still holds: tighter stop = more shares
        wide = engine.calculate_position("WIDE", entry_price=110.00, stop_loss=100.00)
        assert result.shares > wide.shares

    def test_decimal_exact_division(self) -> None:
        """Decimal-based exact division handles tricky float ratios."""
        engine = RiskEngine(capital=100_000, risk_per_trade_pct=0.02)
        # This ratio (2000 / 0.01) fails in IEEE 754 binary float
        # but is exact in Decimal.
        result = engine.calculate_position(
            "EXACT", entry_price=100.01, stop_loss=100.00
        )
        assert result.shares == 200_000

    def test_high_capital_low_risk(self) -> None:
        """High capital with low risk_per_trade_pct still works."""
        engine = RiskEngine(capital=10_000_000, risk_per_trade_pct=0.005)
        # capital_at_risk = 50_000
        result = engine.calculate_position("NABIL", entry_price=500, stop_loss=490)
        # risk_per_share = 10
        # shares = floor(50_000 / 10) = 5_000
        assert result.shares == 5_000
        assert result.capital_at_risk == 50_000.0
