"""Tests for the broker layer (src/brokers)."""

from __future__ import annotations

import pytest

from src.brokers import (
    AccountBalance,
    MockBroker,
    Order,
    OrderSide,
    OrderStatus,
    OrderType,
    PaperBroker,
    Position,
    get_adapter,
)
from src.brokers.future_nepse import FutureNepseBroker


class TestOrderModels:
    def test_order_defaults(self) -> None:
        order = Order(symbol="NABIL", side=OrderSide.BUY, quantity=100)
        assert order.status == OrderStatus.PENDING
        assert order.order_type == OrderType.MARKET
        assert order.order_id == ""

    def test_order_to_dict(self) -> None:
        order = Order(symbol="NABIL", side=OrderSide.BUY, quantity=100)
        data = order.to_dict()
        assert data["symbol"] == "NABIL"
        assert data["side"] == "BUY"
        assert data["quantity"] == 100

    def test_enum_values(self) -> None:
        assert OrderSide.BUY.value == "BUY"
        assert OrderSide.SELL.value == "SELL"
        assert OrderType.LIMIT.value == "LIMIT"
        assert OrderStatus.EXECUTED.value == "EXECUTED"

    def test_position_market_value(self) -> None:
        position = Position(
            symbol="NABIL", quantity=10, average_price=100.0, current_price=110.0
        )
        assert position.market_value() == 1100.0
        assert position.unrealized_pnl() == 100.0

    def test_position_to_dict(self) -> None:
        position = Position(symbol="A", quantity=2, average_price=10, current_price=12)
        data = position.to_dict()
        assert data["market_value"] == 24.0
        assert data["unrealized_pnl"] == 4.0

    def test_account_balance(self) -> None:
        balance = AccountBalance(cash=1000, equity=1500, buying_power=1000)
        assert balance.to_dict()["equity"] == 1500.0


class TestPaperBroker:
    def test_requires_connection(self) -> None:
        broker = PaperBroker()
        with pytest.raises(RuntimeError):
            broker.place_order(Order(symbol="A", side=OrderSide.BUY, quantity=10))

    def test_connect_disconnect(self) -> None:
        broker = PaperBroker()
        broker.connect()
        assert broker.connected
        broker.disconnect()
        assert not broker.connected

    def test_buy(self) -> None:
        broker = PaperBroker(cash=1_000_000, price_feed=lambda s: 500.0)
        broker.connect()
        order = broker.place_order(
            Order(symbol="NABIL", side=OrderSide.BUY, quantity=100)
        )
        assert order.status == OrderStatus.EXECUTED
        assert order.filled_price == 500.0
        assert order.order_id
        balance = broker.get_balance()
        assert balance.cash == pytest.approx(950_000.0)
        positions = broker.get_positions()
        assert len(positions) == 1
        assert positions[0].quantity == 100
        assert positions[0].average_price == 500.0

    def test_average_price(self) -> None:
        prices = {"A": 100.0}
        broker = PaperBroker(cash=1_000_000, price_feed=lambda s: prices[s])
        broker.connect()
        broker.place_order(Order(symbol="A", side=OrderSide.BUY, quantity=10))
        prices["A"] = 200.0
        broker.place_order(Order(symbol="A", side=OrderSide.BUY, quantity=10))
        positions = broker.get_positions()
        assert positions[0].average_price == pytest.approx(150.0)

    def test_sell(self) -> None:
        broker = PaperBroker(cash=1_000_000, price_feed=lambda s: 100.0)
        broker.connect()
        broker.place_order(Order(symbol="A", side=OrderSide.BUY, quantity=100))
        order = broker.place_order(Order(symbol="A", side=OrderSide.SELL, quantity=40))
        assert order.status == OrderStatus.EXECUTED
        positions = broker.get_positions()
        assert positions[0].quantity == 60

    def test_sell_all_closes_position(self) -> None:
        broker = PaperBroker(cash=1_000_000, price_feed=lambda s: 100.0)
        broker.connect()
        broker.place_order(Order(symbol="A", side=OrderSide.BUY, quantity=10))
        broker.place_order(Order(symbol="A", side=OrderSide.SELL, quantity=10))
        assert broker.get_positions() == []

    def test_insufficient_cash_rejected(self) -> None:
        broker = PaperBroker(cash=1000, price_feed=lambda s: 500.0)
        broker.connect()
        order = broker.place_order(Order(symbol="A", side=OrderSide.BUY, quantity=10))
        assert order.status == OrderStatus.REJECTED
        assert broker.get_balance().cash == pytest.approx(1000.0)

    def test_insufficient_shares_rejected(self) -> None:
        broker = PaperBroker(price_feed=lambda s: 100.0)
        broker.connect()
        order = broker.place_order(Order(symbol="A", side=OrderSide.SELL, quantity=10))
        assert order.status == OrderStatus.REJECTED

    def test_invalid_quantity_rejected(self) -> None:
        broker = PaperBroker()
        broker.connect()
        order = broker.place_order(Order(symbol="A", side=OrderSide.BUY, quantity=0))
        assert order.status == OrderStatus.REJECTED

    def test_commission(self) -> None:
        broker = PaperBroker(
            cash=1_000_000, price_feed=lambda s: 100.0, commission_rate=0.01
        )
        broker.connect()
        broker.place_order(Order(symbol="A", side=OrderSide.BUY, quantity=10))
        # 10 * 100 = 1000, plus 1% commission = 1010 deducted.
        assert broker.get_balance().cash == pytest.approx(998_990.0)

    def test_get_order(self) -> None:
        broker = PaperBroker()
        broker.connect()
        order = broker.place_order(Order(symbol="A", side=OrderSide.BUY, quantity=10))
        fetched = broker.get_order(order.order_id)
        assert fetched is order

    def test_cancel_executed_fails(self) -> None:
        broker = PaperBroker()
        broker.connect()
        order = broker.place_order(Order(symbol="A", side=OrderSide.BUY, quantity=10))
        assert broker.cancel_order(order.order_id) is False

    def test_price_feed_fallback(self) -> None:
        def broken(_symbol: str) -> float:
            raise ValueError("boom")

        broker = PaperBroker(price_feed=broken)
        broker.connect()
        order = broker.place_order(Order(symbol="A", side=OrderSide.BUY, quantity=10))
        assert order.status == OrderStatus.EXECUTED
        assert order.filled_price == 100.0

    def test_describe(self) -> None:
        broker = PaperBroker()
        assert broker.describe()["name"] == "paper"


class TestMockBroker:
    def test_default_price_deterministic(self) -> None:
        broker = MockBroker()
        assert broker._default_price("NABIL") == broker._default_price("NABIL")

    def test_buy_sell(self) -> None:
        broker = MockBroker(cash=1_000_000, price_feed=lambda s: 100.0)
        broker.connect()
        buy = broker.place_order(Order(symbol="A", side=OrderSide.BUY, quantity=10))
        assert buy.status == OrderStatus.EXECUTED
        sell = broker.place_order(Order(symbol="A", side=OrderSide.SELL, quantity=4))
        assert sell.status == OrderStatus.EXECUTED

    def test_balance_equity(self) -> None:
        broker = MockBroker(cash=1_000_000, price_feed=lambda s: 100.0)
        broker.connect()
        broker.place_order(Order(symbol="A", side=OrderSide.BUY, quantity=10))
        balance = broker.get_balance()
        assert balance.equity == pytest.approx(1_000_000.0)

    def test_requires_connection(self) -> None:
        broker = MockBroker()
        with pytest.raises(RuntimeError):
            broker.place_order(Order(symbol="A", side=OrderSide.BUY, quantity=1))

    def test_describe(self) -> None:
        broker = MockBroker()
        assert broker.describe()["name"] == "mock"


class TestFutureNepseBroker:
    def test_connect_not_implemented(self) -> None:
        broker = FutureNepseBroker()
        with pytest.raises(NotImplementedError):
            broker.connect()

    def test_place_order_not_implemented(self) -> None:
        broker = FutureNepseBroker()
        with pytest.raises(NotImplementedError):
            broker.place_order(
                Order(symbol="A", side=OrderSide.BUY, quantity=1)
            )

    def test_describe(self) -> None:
        broker = FutureNepseBroker(base_url="https://example.com")
        data = broker.describe()
        assert data["status"] == "placeholder"
        assert data["base_url"] == "https://example.com"


class TestAdapters:
    def test_get_adapter_ibkr(self) -> None:
        adapter = get_adapter("ibkr", account="acct1")
        assert adapter.name == "ibkr"
        assert "account" in adapter.required_env

    def test_get_adapter_binance(self) -> None:
        adapter = get_adapter("binance", api_key="k", api_secret="s")
        assert adapter.name == "binance"

    def test_get_adapter_alpaca(self) -> None:
        adapter = get_adapter("alpaca", api_key="k", api_secret="s")
        assert adapter.name == "alpaca"

    def test_get_adapter_unknown(self) -> None:
        with pytest.raises(ValueError):
            get_adapter("nope")

    def test_adapter_describe(self) -> None:
        adapter = get_adapter("alpaca", api_key="k", api_secret="s")
        data = adapter.describe()
        assert data["status"].startswith("stub")
