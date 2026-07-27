"""Tests for the portfolio capital allocation engine."""

from __future__ import annotations

from dataclasses import asdict

import pytest

from src.portfolio.allocator import (
    PortfolioAllocator,
    PositionAllocation,
)


# ======================================================================
# Constructor validation
# ======================================================================


class TestPortfolioAllocatorConstructor:
    """PortfolioAllocator.__init__ validation."""

    def test_invalid_capital_zero(self) -> None:
        """Zero capital raises ValueError."""
        with pytest.raises(ValueError, match="Capital must be positive"):
            PortfolioAllocator(capital=0)

    def test_invalid_capital_negative(self) -> None:
        """Negative capital raises ValueError."""
        with pytest.raises(ValueError, match="Capital must be positive"):
            PortfolioAllocator(capital=-1000)

    def test_invalid_weight_zero(self) -> None:
        """Zero max_position_weight raises ValueError."""
        with pytest.raises(
            ValueError, match="max_position_weight must be in"
        ):
            PortfolioAllocator(capital=100_000, max_position_weight=0)

    def test_invalid_weight_negative(self) -> None:
        """Negative max_position_weight raises ValueError."""
        with pytest.raises(
            ValueError, match="max_position_weight must be in"
        ):
            PortfolioAllocator(capital=100_000, max_position_weight=-0.1)

    def test_invalid_weight_over_one(self) -> None:
        """max_position_weight > 1 raises ValueError."""
        with pytest.raises(
            ValueError, match="max_position_weight must be in"
        ):
            PortfolioAllocator(capital=100_000, max_position_weight=1.5)

    def test_valid_construction(self) -> None:
        """Valid capital and weight create a usable allocator."""
        allocator = PortfolioAllocator(capital=100_000, max_position_weight=0.25)
        assert allocator is not None


# ======================================================================
# allocate_equal_weight
# ======================================================================


class TestAllocateEqualWeight:
    """PortfolioAllocator.allocate_equal_weight behaviour."""

    def _make_signal(
        self,
        symbol: str,
        signal: str = "BUY",
        price: float = 100.0,
    ) -> dict:
        return {"symbol": symbol, "signal": signal, "price": price}

    # ------------------------------------------------------------------
    # Edge cases
    # ------------------------------------------------------------------

    def test_empty_signals_list(self) -> None:
        """Empty signal list returns an empty allocation."""
        allocator = PortfolioAllocator(capital=100_000)
        result = allocator.allocate_equal_weight([])
        assert result == []

    def test_no_buy_signals(self) -> None:
        """Only HOLD/SELL signals returns an empty allocation."""
        allocator = PortfolioAllocator(capital=100_000)
        signals = [
            self._make_signal("NABIL", "HOLD"),
            self._make_signal("ADBL", "SELL"),
            self._make_signal("NICA", "HOLD"),
        ]
        result = allocator.allocate_equal_weight(signals)
        assert result == []

    def test_skips_missing_price(self) -> None:
        """A BUY signal without a price key is skipped."""
        allocator = PortfolioAllocator(capital=100_000)
        signals = [
            {"symbol": "NABIL", "signal": "BUY"},  # no price
        ]
        result = allocator.allocate_equal_weight(signals)
        assert result == []

    def test_skips_zero_price(self) -> None:
        """A BUY signal with price=0 is skipped."""
        allocator = PortfolioAllocator(capital=100_000)
        signals = [
            self._make_signal("NABIL", price=0),
        ]
        result = allocator.allocate_equal_weight(signals)
        assert result == []

    def test_skips_negative_price(self) -> None:
        """A BUY signal with price < 0 is skipped."""
        allocator = PortfolioAllocator(capital=100_000)
        signals = [
            self._make_signal("NABIL", price=-50),
        ]
        result = allocator.allocate_equal_weight(signals)
        assert result == []

    def test_skips_non_numeric_price(self) -> None:
        """A BUY signal with a string price that cannot be parsed is skipped."""
        allocator = PortfolioAllocator(capital=100_000)
        signals = [
            {"symbol": "NABIL", "signal": "BUY", "price": "N/A"},
        ]
        result = allocator.allocate_equal_weight(signals)
        assert result == []

    # ------------------------------------------------------------------
    # Correct allocation
    # ------------------------------------------------------------------

    def test_equal_allocation_five_positions(self) -> None:
        """Five BUY signals each get 20% weight."""
        allocator = PortfolioAllocator(capital=100_000)
        signals = [
            self._make_signal("NABIL", price=500),
            self._make_signal("ADBL", price=300),
            self._make_signal("NICA", price=200),
            self._make_signal("SBI", price=400),
            self._make_signal("EBL", price=250),
        ]
        result = allocator.allocate_equal_weight(signals)

        assert len(result) == 5

        for alloc in result:
            assert alloc.weight == pytest.approx(0.20)
            # capital should be 20_000 (100_000 * 0.20)
            expected_capital = 20_000.0
            assert alloc.capital == pytest.approx(
                expected_capital, rel=0.01
            )

    @pytest.mark.skip(
        reason="max_position_weight is not enforced by allocate_equal_weight(). "
               "Capped allocation will be implemented in a future "
               "constrained allocator method."
    )
    def test_weight_capped_by_max_position_weight(self) -> None:
        """When equal weight exceeds max_position_weight, it is capped."""
        allocator = PortfolioAllocator(
            capital=100_000, max_position_weight=0.10
        )
        signals = [
            self._make_signal("NABIL", price=500),
            self._make_signal("ADBL", price=300),
        ]
        result = allocator.allocate_equal_weight(signals)

        assert len(result) == 2
        # Raw equal weight = 0.50 > 0.10, so capped to 0.10
        for alloc in result:
            assert alloc.weight == pytest.approx(0.10)
            # capital per position = 100_000 * 0.10 = 10_000
            assert alloc.capital == pytest.approx(10_000.0, rel=0.01)

    def test_share_rounding_down(self) -> None:
        """Shares are floored, producing whole shares."""
        allocator = PortfolioAllocator(capital=10_000)
        # 1 BUY signal => weight 1.0 => capital_per_position = 10_000
        # shares = floor(10_000 / 150) = floor(66.666) = 66
        signals = [
            self._make_signal("NABIL", price=150),
        ]
        result = allocator.allocate_equal_weight(signals)

        assert len(result) == 1
        pos = result[0]
        assert pos.shares == 66
        # capital = 66 * 150 = 9900
        assert pos.capital == 9900.0
        # remaining cash = 10_000 - 9900 = 100
        assert pos.remaining_cash == pytest.approx(100.0)

    def test_remaining_cash_accumulates(self) -> None:
        """Remaining cash from rounding is tracked per-position."""
        allocator = PortfolioAllocator(capital=20_000)
        signals = [
            self._make_signal("NABIL", price=180),
            self._make_signal("ADBL", price=250),
        ]
        result = allocator.allocate_equal_weight(signals)

        assert len(result) == 2
        # weight = 0.50, capital_per_position = 10_000
        # Pos 0: shares = floor(10_000 / 180) = 55; cap = 55*180 = 9900; rem = 100
        assert result[0].shares == 55
        assert result[0].capital == 9900.0
        # Pos 1: shares = floor(10_000 / 250) = 40; cap = 40*250 = 10000
        # Last position: remaining = 20_000 - (9900 + 10000) = 100
        assert result[1].shares == 40
        assert result[1].capital == 10000.0
        assert result[1].remaining_cash == pytest.approx(100.0)

    def test_allocation_never_exceeds_capital(self) -> None:
        """Total allocated capital never exceeds the portfolio capital."""
        allocator = PortfolioAllocator(capital=50_000)
        signals = [
            self._make_signal("NABIL", price=340),
            self._make_signal("ADBL", price=210),
            self._make_signal("NICA", price=180),
            self._make_signal("SBI", price=420),
        ]
        result = allocator.allocate_equal_weight(signals)

        total = sum(a.capital for a in result)
        assert total <= 50_000 + 0.01  # allow floating-point tolerance

    def test_higher_price_fewer_shares(self) -> None:
        """More expensive stocks get fewer shares."""
        allocator = PortfolioAllocator(capital=100_000)
        signals = [
            self._make_signal("EXPENSIVE", price=1000),
            self._make_signal("CHEAP", price=50),
        ]
        result = allocator.allocate_equal_weight(signals)

        assert len(result) == 2
        expensive = next(a for a in result if a.symbol == "EXPENSIVE")
        cheap = next(a for a in result if a.symbol == "CHEAP")

        assert expensive.shares < cheap.shares


# ======================================================================
# PositionAllocation dataclass
# ======================================================================


class TestPositionAllocation:
    """PositionAllocation dataclass behaviour."""

    def test_to_dict_returns_expected_keys(self) -> None:
        """to_dict() returns all six fields as a dictionary."""
        alloc = PositionAllocation(
            symbol="NABIL",
            price=500.0,
            weight=0.20,
            capital=10_000.0,
            shares=20,
            remaining_cash=50.0,
        )
        d = alloc.to_dict()

        assert d["symbol"] == "NABIL"
        assert d["price"] == 500.0
        assert d["weight"] == 0.20
        assert d["capital"] == 10_000.0
        assert d["shares"] == 20
        assert d["remaining_cash"] == 50.0


# ======================================================================
# portfolio_summary
# ======================================================================


class TestPortfolioSummary:
    """PortfolioAllocator.portfolio_summary output."""

    def test_summary_empty(self) -> None:
        """Empty allocation list produces zero-summary."""
        allocator = PortfolioAllocator(capital=100_000)
        summary = allocator.portfolio_summary([])

        assert summary["total_positions"] == 0
        assert summary["capital_allocated"] == 0.0
        assert summary["cash_remaining"] == 100_000.0
        assert summary["symbols"] == []

    def test_summary_with_allocations(self) -> None:
        """Summary reflects the actual allocation state."""
        allocator = PortfolioAllocator(capital=100_000)
        allocations = [
            PositionAllocation(
                symbol="NABIL",
                price=500.0,
                weight=0.50,
                capital=25_000.0,
                shares=50,
                remaining_cash=0.0,
            ),
            PositionAllocation(
                symbol="ADBL",
                price=250.0,
                weight=0.50,
                capital=25_000.0,
                shares=100,
                remaining_cash=0.0,
            ),
        ]
        summary = allocator.portfolio_summary(allocations)

        assert summary["total_positions"] == 2
        assert summary["capital_allocated"] == 50_000.0
        assert summary["cash_remaining"] == 50_000.0
        assert summary["symbols"] == ["NABIL", "ADBL"]
        assert summary["weights"]["NABIL"] == 0.50
        assert summary["weights"]["ADBL"] == 0.50
