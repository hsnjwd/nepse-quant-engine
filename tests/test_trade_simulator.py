"""Tests for historical trade execution simulation."""

import pandas as pd
import pytest

from src.backtest.trade_simulator import (
    EXIT_END_OF_DATA,
    EXIT_SELL_SIGNAL,
    EXIT_STOP_LOSS,
    EXIT_TARGET,
    ExecutionConfig,
    ExitReason,
    TradeExecutionEngine,
    TradeRecord,
    TradeResult,
    simulate_trade,
)


@pytest.fixture
def buy_signal() -> dict[str, float | str]:
    """Provide a baseline buy signal."""
    return {"signal": "BUY", "price": 100.0, "stop_loss": 95.0, "target1": 110.0}


def test_target_exit_includes_execution_costs(buy_signal: dict[str, float | str]) -> None:
    """A target hit includes slippage, commission, and one-day holding period."""
    data = pd.DataFrame([{"High": 100, "Low": 99, "Close": 100}, {"High": 111, "Low": 100, "Close": 110}])

    trade = simulate_trade(data, 0, buy_signal, commission=0.001, slippage=0.01)

    assert trade is not None
    assert trade["exit_reason"] == "TARGET"
    assert trade["result"] == "WIN"
    assert trade["entry_price"] == 101.0
    assert trade["exit_price"] == 108.9
    assert trade["shares"] == 1
    assert trade["gross_profit"] == 7.9
    assert trade["commission"] == pytest.approx(0.21)
    assert trade["net_profit"] == pytest.approx(7.69)
    assert trade["return_pct"] == pytest.approx(7.61)
    assert trade["holding_days"] == 1


def test_stop_loss_takes_priority_when_both_levels_hit(buy_signal: dict[str, float | str]) -> None:
    """The conservative stop-loss exit wins when one candle reaches both levels."""
    data = pd.DataFrame([{"High": 100, "Low": 99, "Close": 100}, {"High": 111, "Low": 94, "Close": 100}])

    trade = simulate_trade(data, 0, buy_signal)

    assert trade is not None
    assert trade["exit_reason"] == "STOP_LOSS"
    assert trade["result"] == "LOSS"
    assert trade["holding_days"] == 1


def test_sell_signal_exit_uses_candle_close(buy_signal: dict[str, float | str]) -> None:
    """A historical sell signal exits at that candle's close."""
    data = pd.DataFrame([{"High": 100, "Low": 99, "Close": 100, "Signal": "HOLD"}, {"High": 105, "Low": 98, "Close": 103, "Signal": "SELL"}])

    trade = simulate_trade(data, 0, buy_signal)

    assert trade is not None
    assert trade["exit_reason"] == "SELL_SIGNAL"
    assert trade["exit_price"] == 103.0
    assert trade["holding_days"] == 1


def test_end_of_data_exit_uses_actual_holding_period(buy_signal: dict[str, float | str]) -> None:
    """End-of-data exits use the final available candle and elapsed days."""
    data = pd.DataFrame([{"High": 100, "Low": 99, "Close": 100}, {"High": 105, "Low": 98, "Close": 102}, {"High": 106, "Low": 99, "Close": 104}])

    trade = simulate_trade(data, 0, buy_signal)

    assert trade is not None
    assert trade["exit_reason"] == "END_OF_DATA"
    assert trade["result"] == "TIME_EXIT"
    assert trade["exit_price"] == 104.0
    assert trade["holding_days"] == 2


def test_non_buy_signal_returns_none() -> None:
    """Non-buy signals retain the previous no-trade behavior."""
    data = pd.DataFrame([{"High": 100, "Low": 99, "Close": 100}])

    assert simulate_trade(data, 0, {"signal": "HOLD"}) is None
    assert simulate_trade(data, 0, {"signal": "SELL"}) is None


def test_negative_commission_raises_value_error(buy_signal: dict[str, float | str]) -> None:
    """Negative commission rates should raise a ValueError."""
    data = pd.DataFrame([{"High": 100, "Low": 99, "Close": 100}])

    with pytest.raises(ValueError, match="Commission and slippage must be non-negative"):
        simulate_trade(data, 0, buy_signal, commission=-0.01)


def test_negative_slippage_raises_value_error(buy_signal: dict[str, float | str]) -> None:
    """Negative slippage rates should raise a ValueError."""
    data = pd.DataFrame([{"High": 100, "Low": 99, "Close": 100}])

    with pytest.raises(ValueError, match="Commission and slippage must be non-negative"):
        simulate_trade(data, 0, buy_signal, slippage=-0.005)


def test_invalid_max_holding_days_raises_value_error() -> None:
    """Non-positive max_holding_days in ExecutionConfig raises ValueError."""
    with pytest.raises(ValueError, match="max_holding_days must be positive"):
        ExecutionConfig(max_holding_days=0)

    with pytest.raises(ValueError, match="max_holding_days must be positive"):
        ExecutionConfig(max_holding_days=-5)


def test_out_of_bounds_start_index_raises_value_error(buy_signal: dict[str, float | str]) -> None:
    """Start index outside valid DataFrame range raises ValueError."""
    data = pd.DataFrame([{"High": 100, "Low": 99, "Close": 100}])

    with pytest.raises(ValueError, match="out of bounds"):
        simulate_trade(data, 5, buy_signal)

    with pytest.raises(ValueError, match="out of bounds"):
        simulate_trade(data, -1, buy_signal)


def test_zero_costs_trade_execution(buy_signal: dict[str, float | str]) -> None:
    """Execution with zero commission and zero slippage returns exact prices."""
    data = pd.DataFrame([{"High": 100, "Low": 99, "Close": 100}, {"High": 112, "Low": 99, "Close": 110}])

    trade = simulate_trade(data, 0, buy_signal, commission=0.0, slippage=0.0)

    assert trade is not None
    assert trade["entry_price"] == 100.0
    assert trade["exit_price"] == 110.0
    assert trade["shares"] == 1
    assert trade["gross_profit"] == 10.0
    assert trade["commission"] == 0.0
    assert trade["net_profit"] == 10.0
    assert trade["return_pct"] == 10.0


def test_commission_only_cost_calculation(buy_signal: dict[str, float | str]) -> None:
    """Commission without slippage subtracts commission from profit accurately."""
    data = pd.DataFrame([{"High": 100, "Low": 99, "Close": 100}, {"High": 112, "Low": 99, "Close": 110}])

    trade = simulate_trade(data, 0, buy_signal, commission=0.01, slippage=0.0)

    assert trade is not None
    assert trade["entry_price"] == 100.0
    assert trade["exit_price"] == 110.0
    assert trade["gross_profit"] == 10.0
    # (100 + 110) * 0.01 = 2.1
    assert trade["commission"] == 2.1
    assert trade["net_profit"] == 7.9
    assert trade["return_pct"] == 7.9


def test_slippage_only_cost_calculation(buy_signal: dict[str, float | str]) -> None:
    """Slippage without commission adjusts entry and exit prices adversely."""
    data = pd.DataFrame([{"High": 100, "Low": 99, "Close": 100}, {"High": 115, "Low": 99, "Close": 110}])

    trade = simulate_trade(data, 0, buy_signal, commission=0.0, slippage=0.02)

    assert trade is not None
    # entry = 100 * 1.02 = 102.0
    assert trade["entry_price"] == 102.0
    # exit target = 110 * 0.98 = 107.8
    assert trade["exit_price"] == 107.8
    assert trade["gross_profit"] == 5.8
    assert trade["commission"] == 0.0
    assert trade["net_profit"] == 5.8
    assert trade["return_pct"] == round(5.8 / 102.0 * 100, 2)


def test_lowercase_dataframe_columns(buy_signal: dict[str, float | str]) -> None:
    """DataFrames with lowercase column names ('high', 'low', 'close') execute correctly."""
    data = pd.DataFrame([{"high": 100, "low": 99, "close": 100}, {"high": 112, "low": 98, "close": 110}])

    trade = simulate_trade(data, 0, buy_signal)

    assert trade is not None
    assert trade["exit_reason"] == EXIT_TARGET
    assert trade["holding_days"] == 1


def test_custom_max_holding_days(buy_signal: dict[str, float | str]) -> None:
    """Custom max_holding_days limits execution horizon."""
    candles = [{"High": 100, "Low": 99, "Close": 100}] + [
        {"High": 101, "Low": 99, "Close": 100} for _ in range(10)
    ]
    data = pd.DataFrame(candles)

    trade = simulate_trade(data, 0, buy_signal, max_holding_days=3)

    assert trade is not None
    assert trade["exit_reason"] == EXIT_END_OF_DATA
    assert trade["holding_days"] == 3


def test_trade_execution_engine_direct_class_usage(buy_signal: dict[str, float | str]) -> None:
    """TradeExecutionEngine can be instantiated and executed directly."""
    data = pd.DataFrame([{"High": 100, "Low": 99, "Close": 100}, {"High": 105, "Low": 93, "Close": 98}])

    config = ExecutionConfig(commission=0.002, slippage=0.005, max_holding_days=5)
    engine = TradeExecutionEngine(config=config)
    record = engine.execute(data, 0, buy_signal)

    assert isinstance(record, TradeRecord)
    assert record.exit_reason == ExitReason.STOP_LOSS.value
    assert record.result == TradeResult.LOSS.value
    assert record.holding_days == 1
    assert record.shares == 1

    as_dict = record.to_dict()
    assert isinstance(as_dict, dict)
    assert as_dict["exit_reason"] == "STOP_LOSS"
    assert as_dict["shares"] == 1


def test_build_trade_record_with_shares() -> None:
    """build_trade_record accurately scales profits and commission by shares count."""
    engine = TradeExecutionEngine(config=ExecutionConfig(commission=0.01))
    record = engine.build_trade_record(
        entry_price=100.0,
        exit_price=110.0,
        shares=50,
        holding_days=3,
        exit_reason=ExitReason.TARGET.value,
    )

    assert record.shares == 50
    assert record.gross_profit == 500.0  # (110 - 100) * 50
    assert record.commission == 105.0    # (5000 + 5500) * 0.01
    assert record.net_profit == 395.0     # 500 - 105
    assert record.return_pct == 7.9       # 395 / 5000 * 100


def test_exit_reason_and_result_enums() -> None:
    """ExitReason and TradeResult enums match expected string constants."""
    assert ExitReason.TARGET == "TARGET"
    assert ExitReason.STOP_LOSS == "STOP_LOSS"
    assert ExitReason.SELL_SIGNAL == "SELL_SIGNAL"
    assert ExitReason.END_OF_DATA == "END_OF_DATA"

    assert TradeResult.WIN == "WIN"
    assert TradeResult.LOSS == "LOSS"
    assert TradeResult.SELL == "SELL"
    assert TradeResult.TIME_EXIT == "TIME_EXIT"


def test_missing_required_price_column_raises_key_error(buy_signal: dict[str, float | str]) -> None:
    """Missing required price columns in DataFrame candle raises KeyError."""
    data = pd.DataFrame([{"Volume": 100}, {"Volume": 200}])

    with pytest.raises(KeyError, match="Candle is missing required price column"):
        simulate_trade(data, 0, buy_signal)
