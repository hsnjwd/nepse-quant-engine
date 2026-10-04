"""Tests for quantitative trading strategies framework."""

import pandas as pd
import pytest

from src.strategies import (
    BaseStrategy,
    BreakoutStrategy,
    MomentumStrategy,
    StrategyRegistry,
    get_strategy,
    list_strategies,
    register_strategy,
)


@pytest.fixture
def sample_ohlcv_data() -> pd.DataFrame:
    """Provide a sample OHLCV DataFrame for strategy testing."""
    dates = pd.date_range("2024-01-01", periods=30)
    data = []
    price = 100.0
    for i, dt in enumerate(dates):
        price += 2.0 if i > 15 else 0.5
        data.append(
            {
                "Date": dt,
                "Open": price - 1,
                "High": price + 2,
                "Low": price - 1,
                "Close": price,
                "Volume": 1000 + i * 50,
            }
        )
    return pd.DataFrame(data)


def test_momentum_strategy_signal_generation(sample_ohlcv_data: pd.DataFrame) -> None:
    """MomentumStrategy generates valid signal payload."""
    strategy = MomentumStrategy()
    assert strategy.name == "MomentumStrategy"

    payload = strategy.generate_signal(sample_ohlcv_data)

    assert isinstance(payload, dict)
    assert payload["strategy_name"] == "MomentumStrategy"
    assert payload["signal"] in ("BUY", "SELL", "HOLD")
    assert "price" in payload
    assert "score" in payload
    assert "details" in payload


def test_breakout_strategy_signal_generation(sample_ohlcv_data: pd.DataFrame) -> None:
    """BreakoutStrategy generates valid breakout signal payload."""
    strategy = BreakoutStrategy(lookback_period=10)
    assert strategy.name == "BreakoutStrategy"

    payload = strategy.generate_signal(sample_ohlcv_data)

    assert isinstance(payload, dict)
    assert payload["strategy_name"] == "BreakoutStrategy"
    assert payload["signal"] in ("BUY", "SELL", "HOLD")
    assert "details" in payload


def test_strategy_registry_operations() -> None:
    """StrategyRegistry handles registration, retrieval, and errors cleanly."""
    registry = StrategyRegistry()
    strategy = MomentumStrategy()
    registry.register(strategy)

    assert "MomentumStrategy" in registry.list_strategies()
    retrieved = registry.get("MomentumStrategy")
    assert retrieved is strategy

    with pytest.raises(KeyError, match="not found in registry"):
        registry.get("NonExistentStrategy")

    with pytest.raises(TypeError, match="Expected BaseStrategy instance"):
        registry.register("invalid_strategy")  # type: ignore


def test_global_registry_convenience_functions() -> None:
    """Global registry helpers get_strategy and list_strategies work as expected."""
    available = list_strategies()
    assert "MomentumStrategy" in available
    assert "BreakoutStrategy" in available

    mom = get_strategy("MomentumStrategy")
    assert isinstance(mom, MomentumStrategy)
