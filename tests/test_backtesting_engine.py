"""Tests for the Sprint 9 institutional backtesting engine.

Covers models, events, orders, fills, slippage, commission, portfolio,
statistics, engine, corporate actions, scenarios, walk-forward,
multi-timeframe, reports, tournament, and replay bridge.
"""

from __future__ import annotations

from datetime import datetime

import numpy as np
import pandas as pd
import pytest

from src.backtesting.models import (
    BacktestConfig,
    Bar,
    EventType,
    Fill,
    Order,
    OrderSide,
    OrderStatus,
    OrderType,
    Position,
    PositionSide,
    TimeInForce,
    Trade,
)
from src.backtesting.events import BacktestEvent, EventBus
from src.backtesting.orders import OrderManager
from src.backtesting.fills import FillEngine
from src.backtesting.slippage import (
    FixedSlippage,
    PercentageSlippage,
    VolumeSlippage,
    VolatilitySlippage,
    SpreadSlippage,
    RandomizedSlippage,
    build_slippage_model,
)
from src.backtesting.commission import (
    FlatCommission,
    PercentageCommission,
    TieredCommission,
    BrokerCommission,
    TaxPolicy,
    build_commission_model,
)
from src.backtesting.portfolio import PortfolioSimulator
from src.backtesting.engine import BacktestEngine, BacktestResult
from src.backtesting.statistics import (
    AdvancedPerformanceAnalyzer,
    max_drawdown,
    sharpe_ratio,
    sortino_ratio,
    ulcer_index,
    omega_ratio,
    sqn,
    cagr,
    drawdown_series,
    rolling_sharpe,
    alpha_beta,
    holding_period_distribution,
    monthly_returns_table,
    exposure_time,
)
from src.backtesting.corporate_actions import (
    CorporateAction,
    CorporateActionEngine,
    adjust_price,
    apply_dividend,
)
from src.backtesting.scenarios import ScenarioLab, SCENARIOS
from src.backtesting.walk_forward import WalkForwardEngine
from src.backtesting.multi_timeframe import MultiTimeframeEngine, resample_frame
from src.backtesting.reports import InstitutionalReport
from src.backtesting.tournament import StrategyTournament, TournamentEntry
from src.backtesting.replay_bridge import ReplayBacktestBridge


# ═══════════════════════════════════════════════════════════════════
# Fixtures
# ═══════════════════════════════════════════════════════════════════


@pytest.fixture
def sample_df() -> pd.DataFrame:
    """A deterministic 120-bar OHLCV frame."""
    rng = np.random.default_rng(42)
    dates = pd.date_range(start="2024-01-01", periods=120, freq="B")
    close = 100.0 + np.cumsum(rng.normal(0.3, 1.0, 120))
    close = np.maximum(close, 20.0)
    open_ = close + rng.normal(0, 0.2, 120)
    high = np.maximum(open_, close) + np.abs(rng.normal(0, 0.5, 120))
    low = np.minimum(open_, close) - np.abs(rng.normal(0, 0.5, 120))
    volume = rng.integers(10_000, 200_000, 120)
    return pd.DataFrame({
        "Date": dates, "Open": open_, "High": high,
        "Low": low, "Close": close, "Volume": volume,
    })


def make_bar(close=100.0, symbol="NABIL", open_=100.0, high=105.0, low=95.0, volume=100_000.0, index=0):
    """Build a Bar quickly."""
    return Bar(symbol=symbol, timestamp=datetime(2024, 1, 1), open=open_,
               high=high, low=low, close=close, volume=volume, index=index)


# ═══════════════════════════════════════════════════════════════════
# Models
# ═══════════════════════════════════════════════════════════════════


class TestModels:
    def test_bar_creation(self):
        b = make_bar()
        assert b.symbol == "NABIL"
        assert b.close == 100.0

    def test_order_defaults(self):
        o = Order(order_id="O1", symbol="NABIL", side=OrderSide.BUY, quantity=100)
        assert o.status == OrderStatus.CREATED
        assert o.remaining_quantity == 100
        assert o.is_open

    def test_order_filled_props(self):
        o = Order(order_id="O1", symbol="NABIL", side=OrderSide.BUY, quantity=100)
        o.filled_quantity = 40
        assert o.remaining_quantity == 60
        assert o.is_open
        o.status = OrderStatus.FILLED
        assert not o.is_open
        assert o.is_filled

    def test_order_to_dict(self):
        o = Order(order_id="O1", symbol="NABIL", side=OrderSide.SELL, quantity=50)
        d = o.to_dict()
        assert d["side"] == "SELL"
        assert d["quantity"] == 50
        assert "order_id" in d

    def test_position_market_value_and_pnl(self):
        p = Position(symbol="NABIL", side=PositionSide.LONG, quantity=100, average_price=50.0)
        assert p.market_value(60) == 6000.0
        assert p.unrealized_pnl(60) == 1000.0
        assert p.unrealized_pnl(40) == -1000.0

    def test_position_short_pnl(self):
        p = Position(symbol="NABIL", side=PositionSide.SHORT, quantity=100, average_price=50.0)
        assert p.unrealized_pnl(40) == 1000.0
        assert p.unrealized_pnl(60) == -1000.0

    def test_fill_to_dict(self):
        f = Fill(fill_id="F1", order_id="O1", symbol="NABIL", side=OrderSide.BUY,
                 quantity=10, price=100.0, commission=5.0)
        d = f.to_dict()
        assert d["is_buy"] is True
        assert d["quantity"] == 10

    def test_trade_to_dict(self):
        t = Trade(trade_id="T1", symbol="NABIL", side=PositionSide.LONG, quantity=100,
                  entry_price=50.0, exit_price=60.0, gross_pnl=1000.0, net_pnl=990.0,
                  holding_bars=5)
        d = t.to_dict()
        assert d["gross_pnl"] == 1000.0
        assert d["holding_bars"] == 5

    def test_config_defaults(self):
        c = BacktestConfig()
        assert c.initial_cash == 1_000_000.0
        assert c.trades_per_year == 252

    def test_config_custom(self):
        c = BacktestConfig(initial_cash=500_000, slippage_model="volume",
                           commission_model="broker", allow_short=False)
        assert c.initial_cash == 500_000
        assert not c.allow_short


# ═══════════════════════════════════════════════════════════════════
# Event bus
# ═══════════════════════════════════════════════════════════════════


class TestEventBus:
    def test_subscribe_publish(self):
        bus = EventBus()
        seen = []
        bus.subscribe(EventType.FILL, lambda t, p: seen.append(p))
        bus.publish(BacktestEvent(EventType.FILL, {"x": 1}))
        assert seen == [{"x": 1}]

    def test_subscribe_all(self):
        bus = EventBus()
        seen = []
        bus.subscribe(None, lambda t, p: seen.append((t, p)))
        bus.publish(BacktestEvent(EventType.BAR, {"a": 1}))
        bus.publish(BacktestEvent(EventType.FILL, {"b": 2}))
        assert len(seen) == 2

    def test_unsubscribe(self):
        bus = EventBus()
        cb = lambda t, p: None
        bus.subscribe(EventType.FILL, cb)
        bus.unsubscribe(EventType.FILL, cb)
        assert bus.subscriber_count(EventType.FILL) == 0

    def test_unsubscribe_missing_raises(self):
        bus = EventBus()
        with pytest.raises(ValueError):
            bus.unsubscribe(EventType.FILL, lambda t, p: None)

    def test_subscribe_non_callable_raises(self):
        bus = EventBus()
        with pytest.raises(TypeError):
            bus.subscribe(EventType.FILL, "not callable")

    def test_subscriber_errors_are_caught(self):
        bus = EventBus()
        def bad(t, p):
            raise RuntimeError("boom")
        seen = []
        bus.subscribe(EventType.FILL, bad)
        bus.subscribe(EventType.FILL, lambda t, p: seen.append(1))
        bus.publish(BacktestEvent(EventType.FILL, {}))
        assert seen == [1]

    def test_clear(self):
        bus = EventBus()
        bus.subscribe(EventType.FILL, lambda t, p: None)
        bus.clear()
        assert bus.subscriber_count() == 0

    def test_default_bus(self):
        from src.backtesting.events import default_event_bus
        assert isinstance(default_event_bus, EventBus)


# ═══════════════════════════════════════════════════════════════════
# Order manager & lifecycle
# ═══════════════════════════════════════════════════════════════════


class TestOrderManager:
    def test_create_market_order(self):
        m = OrderManager()
        o = m.market("NABIL", OrderSide.BUY, 100)
        assert o.order_type == OrderType.MARKET
        assert o.status == OrderStatus.CREATED

    def test_create_limit_order(self):
        m = OrderManager()
        o = m.limit("NABIL", OrderSide.SELL, 100, 150.0)
        assert o.order_type == OrderType.LIMIT
        assert o.limit_price == 150.0

    def test_create_stop_order(self):
        m = OrderManager()
        o = m.stop("NABIL", OrderSide.SELL, 100, 90.0)
        assert o.order_type == OrderType.STOP
        assert o.stop_price == 90.0

    def test_create_stop_limit(self):
        m = OrderManager()
        o = m.stop_limit("NABIL", OrderSide.SELL, 100, 90.0, 88.0)
        assert o.order_type == OrderType.STOP_LIMIT
        assert o.stop_price == 90.0
        assert o.limit_price == 88.0

    def test_create_trailing_stop(self):
        m = OrderManager()
        o = m.trailing_stop("NABIL", OrderSide.SELL, 100, 95.0, offset=3.0)
        assert o.order_type == OrderType.TRAILING_STOP
        assert o.trailing_offset == 3.0

    def test_trailing_stop_requires_offset(self):
        m = OrderManager()
        with pytest.raises(ValueError):
            m.trailing_stop("NABIL", OrderSide.SELL, 100, 95.0)

    def test_create_bracket(self):
        m = OrderManager()
        orders = m.bracket("NABIL", OrderSide.BUY, 100, 100.0, 115.0, 90.0)
        assert len(orders) == 3
        entry, tp, sl = orders
        assert entry.bracket_parent is None
        assert tp.bracket_parent == entry.order_id
        assert sl.bracket_parent == entry.order_id
        assert tp.oco_group == sl.oco_group

    def test_create_oco(self):
        m = OrderManager()
        a, b = m.oco("NABIL", OrderSide.SELL, 100, 115.0, 90.0)
        assert a.oco_group == b.oco_group
        assert a.order_id != b.order_id

    def test_create_ioc(self):
        m = OrderManager()
        o = m.ioc("NABIL", OrderSide.BUY, 100, 110.0)
        assert o.time_in_force == TimeInForce.IOC

    def test_create_fok(self):
        m = OrderManager()
        o = m.fok("NABIL", OrderSide.BUY, 100, 110.0)
        assert o.time_in_force == TimeInForce.FOK

    def test_create_gtd(self):
        m = OrderManager()
        o = m.gtd("NABIL", OrderSide.BUY, 100, OrderType.LIMIT, 5, limit_price=100.0)
        assert o.time_in_force == TimeInForce.GTD
        assert o.expires_at is not None

    def test_negative_quantity_raises(self):
        m = OrderManager()
        with pytest.raises(ValueError):
            m.market("NABIL", OrderSide.BUY, -1)

    def test_lifecycle_full(self):
        m = OrderManager()
        o = m.limit("NABIL", OrderSide.BUY, 100, 100.0)
        m.submit(o)
        assert o.status == OrderStatus.SUBMITTED
        m.accept(o)
        assert o.status == OrderStatus.ACCEPTED
        m.cancel(o)
        assert o.status == OrderStatus.CANCELLED

    def test_illegal_transition_raises(self):
        m = OrderManager()
        o = m.market("NABIL", OrderSide.BUY, 100)
        with pytest.raises(ValueError):
            m.accept(o)  # CREATED -> ACCEPTED is illegal

    def test_reject(self):
        m = OrderManager()
        o = m.market("NABIL", OrderSide.BUY, 100)
        m.submit(o)
        m.reject(o, "insufficient funds")
        assert o.status == OrderStatus.REJECTED
        assert o.note == "insufficient funds"

    def test_expire(self):
        m = OrderManager()
        o = m.market("NABIL", OrderSide.BUY, 100)
        m.submit(o)
        m.expire(o)
        assert o.status == OrderStatus.EXPIRED

    def test_working_orders(self):
        m = OrderManager()
        m.market("NABIL", OrderSide.BUY, 100)
        m.market("ADBL", OrderSide.BUY, 50)
        assert len(m.working_orders()) == 2
        assert len(m.working_orders("NABIL")) == 1

    def test_oco_cancel_siblings(self):
        m = OrderManager()
        a, b = m.oco("NABIL", OrderSide.SELL, 100, 115.0, 90.0)
        cancelled = m.cancel_oco_siblings(a)
        assert [c.order_id for c in cancelled] == [b.order_id]
        assert b.status == OrderStatus.CANCELLED

    def test_expire_gtd_orders(self):
        m = OrderManager()
        o = m.gtd("NABIL", OrderSide.BUY, 100, OrderType.LIMIT, 1, limit_price=100.0)
        o.expires_at = datetime(2020, 1, 1)  # force past expiry
        expired = m.expire_gtd_orders(datetime(2021, 1, 1))
        assert [e.order_id for e in expired] == [o.order_id]
        assert o.status == OrderStatus.EXPIRED

    def test_trailing_stop_ratchet(self):
        m = OrderManager()
        o = m.trailing_stop("NABIL", OrderSide.SELL, 100, 95.0, offset=3.0)
        m.update_trailing_stops(o, 110.0)
        assert o.stop_price == 107.0  # ratchets up with the market

    def test_orders_by_status(self):
        m = OrderManager()
        m.market("NABIL", OrderSide.BUY, 100)
        filled = m.orders_by_status(OrderStatus.FILLED)
        assert filled == []

    def test_clear(self):
        m = OrderManager()
        m.market("NABIL", OrderSide.BUY, 100)
        m.clear()
        assert m.all_orders() == []


# ═══════════════════════════════════════════════════════════════════
# Slippage models
# ═══════════════════════════════════════════════════════════════════


class TestSlippage:
    def test_fixed(self):
        m = FixedSlippage(0.05)
        bar = make_bar()
        o = Order(order_id="O", symbol="NABIL", side=OrderSide.BUY, quantity=10)
        assert m.compute(o, bar, 100.0) == 0.05
        assert m.adjust(o, bar, 100.0) == 100.05

    def test_fixed_sell_adjust(self):
        m = FixedSlippage(0.05)
        bar = make_bar()
        o = Order(order_id="O", symbol="NABIL", side=OrderSide.SELL, quantity=10)
        assert m.adjust(o, bar, 100.0) == 99.95

    def test_percentage(self):
        m = PercentageSlippage(0.001)
        bar = make_bar()
        o = Order(order_id="O", symbol="NABIL", side=OrderSide.BUY, quantity=10)
        assert m.compute(o, bar, 100.0) == pytest.approx(0.1)

    def test_volume_slippage_scales_with_participation(self):
        m = VolumeSlippage(base_rate=0.0005, volume_factor=1.0)
        bar = make_bar(volume=100_000.0)
        o = Order(order_id="O", symbol="NABIL", side=OrderSide.BUY, quantity=10_000)
        small = m.compute(o, bar, 100.0, {"quantity": 1_000})
        big = m.compute(o, bar, 100.0, {"quantity": 90_000})
        assert big > small

    def test_volatility_slippage(self):
        m = VolatilitySlippage(0.05)
        bar = make_bar(high=110.0, low=90.0)
        o = Order(order_id="O", symbol="NABIL", side=OrderSide.BUY, quantity=10)
        assert m.compute(o, bar, 100.0) > 0

    def test_spread(self):
        m = SpreadSlippage(0.002)
        bar = make_bar()
        o = Order(order_id="O", symbol="NABIL", side=OrderSide.BUY, quantity=10)
        assert m.compute(o, bar, 100.0) == pytest.approx(0.1)

    def test_random_deterministic_with_seed(self):
        m1 = RandomizedSlippage(seed=7)
        m2 = RandomizedSlippage(seed=7)
        bar = make_bar()
        o = Order(order_id="O", symbol="NABIL", side=OrderSide.BUY, quantity=10)
        assert m1.compute(o, bar, 100.0) == m2.compute(o, bar, 100.0)

    def test_build_slippage_model(self):
        m = build_slippage_model("percentage")
        assert isinstance(m, PercentageSlippage)
        m2 = build_slippage_model("fixed", amount=0.1)
        assert isinstance(m2, FixedSlippage)
        assert m2.amount == 0.1

    def test_build_slippage_unknown_raises(self):
        with pytest.raises(ValueError):
            build_slippage_model("nope")


# ═══════════════════════════════════════════════════════════════════
# Commission engine
# ═══════════════════════════════════════════════════════════════════


class TestCommission:
    def test_flat(self):
        m = FlatCommission(10.0)
        c, e = m.compute(100, 50.0)
        assert c == 10.0
        assert e == 0.0

    def test_percentage(self):
        m = PercentageCommission(0.001, minimum=0.0)
        c, e = m.compute(100, 50.0)
        assert c == pytest.approx(5.0)

    def test_percentage_minimum(self):
        m = PercentageCommission(0.001, minimum=10.0)
        c, _ = m.compute(10, 1.0)  # value 10 -> 0.01, floored to 10
        assert c == 10.0

    def test_tiered(self):
        m = TieredCommission([(0, 0.001), (1000, 0.0005)])
        c1, _ = m.compute(10, 50.0)  # value 500 -> tier 1
        c2, _ = m.compute(100, 50.0)  # value 5000 -> tier 2
        assert c1 == pytest.approx(0.5)
        assert c2 == pytest.approx(2.5)

    def test_broker(self):
        m = BrokerCommission(0.004, 0.0001, 0.00015)
        c, e = m.compute(100, 1000.0)
        assert c == pytest.approx(400.0)
        assert e == pytest.approx(25.0)

    def test_tax_policy(self):
        t = TaxPolicy(tax_rate=0.01, stamp_duty=0.001, order_fee=5.0)
        base = PercentageCommission(0.001).charges(100, 100.0)
        result = t.apply(base, 100, 100.0)
        assert result.commission == pytest.approx(15.0)  # 10 + 5 fee
        assert result.tax == pytest.approx(100.0)
        assert result.stamp_duty == pytest.approx(10.0)
        assert result.total == pytest.approx(125.0)  # 15 + 100 + 10

    def test_commission_result_dict(self):
        r = PercentageCommission(0.001).charges(100, 100.0)
        d = r.to_dict()
        assert "commission" in d and "total" in d

    def test_build_commission_model(self):
        m = build_commission_model("broker")
        assert isinstance(m, BrokerCommission)
        with pytest.raises(ValueError):
            build_commission_model("nope")


# ═══════════════════════════════════════════════════════════════════
# Fill engine
# ═══════════════════════════════════════════════════════════════════


class TestFillEngine:
    def _order(self, side=OrderSide.BUY, otype=OrderType.MARKET, qty=100, **kw):
        return Order(order_id="O", symbol="NABIL", side=side, order_type=otype,
                     quantity=qty, **kw)

    def test_market_fill(self):
        fe = FillEngine()
        o = self._order()
        bar = make_bar(close=100.0, open_=100.0)
        fill = fe.try_fill(o, bar, bar.open)
        assert fill is not None
        assert fill.quantity == 100
        assert o.status == OrderStatus.FILLED

    def test_limit_buy_fills_when_price_below(self):
        fe = FillEngine(slippage=FixedSlippage(0))
        o = self._order(otype=OrderType.LIMIT, limit_price=105.0)
        bar = make_bar(open_=100.0)
        fill = fe.try_fill(o, bar, bar.open)
        assert fill is not None
        assert fill.price <= 105.0

    def test_limit_buy_no_fill_when_price_above(self):
        fe = FillEngine()
        o = self._order(otype=OrderType.LIMIT, limit_price=90.0)
        bar = make_bar(open_=100.0, low=95.0)
        assert fe.try_fill(o, bar, bar.open) is None

    def test_stop_sell_fills_below(self):
        fe = FillEngine(slippage=FixedSlippage(0))
        o = self._order(side=OrderSide.SELL, otype=OrderType.STOP, stop_price=95.0)
        bar = make_bar(open_=90.0)
        fill = fe.try_fill(o, bar, bar.open)
        assert fill is not None

    def test_stop_buy_fills_above(self):
        fe = FillEngine(slippage=FixedSlippage(0))
        o = self._order(side=OrderSide.BUY, otype=OrderType.STOP, stop_price=105.0)
        bar = make_bar(open_=110.0)
        fill = fe.try_fill(o, bar, bar.open)
        assert fill is not None

    def test_partial_fill(self):
        fe = FillEngine()
        o = self._order(qty=100)
        bar = make_bar()
        fill = fe.try_fill(o, bar, bar.open, available_quantity=40)
        assert fill is not None
        assert fill.quantity == 40
        assert o.status == OrderStatus.PARTIALLY_FILLED
        assert o.remaining_quantity == 60

    def test_fok_requires_full(self):
        fe = FillEngine()
        o = self._order(qty=100, time_in_force=TimeInForce.FOK)
        bar = make_bar()
        assert fe.try_fill(o, bar, bar.open, available_quantity=40) is None

    def test_fill_charges_commission(self):
        fe = FillEngine(commission=PercentageCommission(0.001))
        o = self._order()
        bar = make_bar(open_=100.0)
        fill = fe.try_fill(o, bar, bar.open)
        assert fill.commission > 0

    def test_slippage_included_in_price(self):
        fe = FillEngine(slippage=FixedSlippage(1.0))
        o = self._order()
        bar = make_bar(open_=100.0)
        fill = fe.try_fill(o, bar, bar.open)
        assert fill.price == 101.0  # buy pays more

    def test_average_fill_price(self):
        fe = FillEngine(slippage=FixedSlippage(0))
        o = self._order(qty=100)
        bar = make_bar(open_=100.0)
        fe.try_fill(o, bar, bar.open, available_quantity=50)
        bar2 = make_bar(open_=120.0)
        fe.try_fill(o, bar2, bar2.open, available_quantity=50)
        assert o.average_fill_price == pytest.approx(110.0)
        assert o.status == OrderStatus.FILLED

    def test_zero_quantity_no_fill(self):
        fe = FillEngine()
        o = self._order(qty=0)
        bar = make_bar()
        assert fe.try_fill(o, bar, bar.open) is None


# ═══════════════════════════════════════════════════════════════════
# Portfolio simulator
# ═══════════════════════════════════════════════════════════════════


class TestPortfolio:
    def _fill(self, side=OrderSide.BUY, qty=100, price=100.0, symbol="NABIL"):
        return Fill(fill_id="F", order_id="O", symbol=symbol, side=side,
                    quantity=qty, price=price, commission=0.0)

    def test_open_long(self):
        p = PortfolioSimulator(initial_cash=10_000)
        p.apply_fill(self._fill(), commission=10.0)
        assert p.cash == pytest.approx(10_000 - 10_000 - 10.0)
        pos = p.get_position("NABIL")
        assert pos is not None
        assert pos.quantity == 100
        assert pos.average_price == 100.0

    def test_close_long_cash(self):
        p = PortfolioSimulator(initial_cash=10_000)
        p.apply_fill(self._fill(qty=100, price=10.0), commission=5.0)
        p.apply_fill(self._fill(side=OrderSide.SELL, qty=100, price=12.0), commission=5.0)
        # buy 100@10 = -1000, sell 100@12 = +1200, commissions 10
        assert p.cash == pytest.approx(10_000 + 200 - 10.0)
        assert p.realized_pnl == pytest.approx(200.0)

    def test_short_selling(self):
        p = PortfolioSimulator(initial_cash=10_000, allow_short=True)
        p.apply_fill(self._fill(side=OrderSide.SELL, qty=100, price=100.0), commission=0.0)
        pos = p.get_position("NABIL")
        assert pos.side == PositionSide.SHORT
        assert pos.quantity == 100
        # Short sale brings in cash
        assert p.cash == pytest.approx(10_000 + 10_000)

    def test_short_disabled(self):
        p = PortfolioSimulator(initial_cash=10_000, allow_short=False)
        with pytest.raises(ValueError):
            p.apply_fill(self._fill(side=OrderSide.SELL, qty=100, price=100.0), commission=0.0)

    def test_close_short(self):
        p = PortfolioSimulator(initial_cash=10_000, allow_short=True)
        p.apply_fill(self._fill(side=OrderSide.SELL, qty=100, price=100.0), commission=0.0)
        p.apply_fill(self._fill(side=OrderSide.BUY, qty=100, price=90.0), commission=0.0)
        assert p.realized_pnl == pytest.approx(1000.0)
        assert p.get_position("NABIL") is None

    def test_reversal_long_to_short(self):
        p = PortfolioSimulator(initial_cash=20_000, allow_short=True)
        p.apply_fill(self._fill(qty=100, price=10.0), commission=0.0)
        # Sell 150: close 100 long, open 50 short
        p.apply_fill(self._fill(side=OrderSide.SELL, qty=150, price=12.0), commission=0.0)
        pos = p.get_position("NABIL")
        assert pos.side == PositionSide.SHORT
        assert pos.quantity == 50
        # cash: -1000 (buy) + 1200 (close proceeds) + 600 (new short) = +800
        assert p.cash == pytest.approx(20_000 + 800)
        assert p.realized_pnl == pytest.approx(200.0)

    def test_reversal_short_to_long(self):
        p = PortfolioSimulator(initial_cash=20_000, allow_short=True)
        p.apply_fill(self._fill(side=OrderSide.SELL, qty=100, price=10.0), commission=0.0)
        p.apply_fill(self._fill(side=OrderSide.BUY, qty=150, price=12.0), commission=0.0)
        pos = p.get_position("NABIL")
        assert pos.side == PositionSide.LONG
        assert pos.quantity == 50

    def test_average_up(self):
        p = PortfolioSimulator(initial_cash=20_000)
        p.apply_fill(self._fill(qty=100, price=10.0), commission=0.0)
        p.apply_fill(self._fill(qty=100, price=20.0), commission=0.0)
        pos = p.get_position("NABIL")
        assert pos.quantity == 200
        assert pos.average_price == pytest.approx(15.0)

    def test_mark_to_market(self):
        p = PortfolioSimulator(initial_cash=10_000)
        p.apply_fill(self._fill(qty=100, price=10.0), commission=0.0)
        snap = p.mark_to_market({"NABIL": 12.0}, bar_index=0)
        assert snap.positions_value == 1200.0
        # cash = 10000 - 1000 (buy 100@10) = 9000; equity = 9000 + 1200
        assert snap.equity == pytest.approx(10_200.0)
        assert snap.unrealized_pnl == 200.0

    def test_equity_curve(self):
        p = PortfolioSimulator(initial_cash=10_000)
        p.mark_to_market({}, bar_index=0)
        p.mark_to_market({}, bar_index=1)
        assert len(p.equity_curve()) == 2

    def test_sector_allocation(self):
        p = PortfolioSimulator(initial_cash=100_000)
        p.apply_fill(self._fill(qty=10, price=100.0, symbol="NABIL"), commission=0.0)
        p.apply_fill(self._fill(qty=10, price=50.0, symbol="ADBL"), commission=0.0)
        alloc = p.sector_allocation({"NABIL": 100, "ADBL": 50}, {"NABIL": "Bank", "ADBL": "Bank"})
        assert alloc == {"Bank": 1500.0}

    def test_concentration_single(self):
        p = PortfolioSimulator(initial_cash=100_000)
        p.apply_fill(self._fill(qty=10, price=100.0), commission=0.0)
        assert p.concentration({"NABIL": 100.0}) == pytest.approx(1.0)

    def test_concentration_diversified(self):
        p = PortfolioSimulator(initial_cash=100_000)
        p.apply_fill(self._fill(qty=10, price=100.0, symbol="NABIL"), commission=0.0)
        p.apply_fill(self._fill(qty=10, price=100.0, symbol="ADBL"), commission=0.0)
        assert p.concentration({"NABIL": 100.0, "ADBL": 100.0}) == pytest.approx(0.5)

    def test_correlation_exposure(self):
        p = PortfolioSimulator(initial_cash=100_000)
        p.apply_fill(self._fill(qty=10, price=100.0, symbol="NABIL"), commission=0.0)
        p.apply_fill(self._fill(qty=10, price=100.0, symbol="ADBL"), commission=0.0)
        corr = p.correlation_exposure({"NABIL": 100.0, "ADBL": 100.0}, {("NABIL", "ADBL"): 0.8})
        assert corr == pytest.approx(0.8)

    def test_correlation_single_position(self):
        p = PortfolioSimulator(initial_cash=100_000)
        p.apply_fill(self._fill(qty=10, price=100.0), commission=0.0)
        assert p.correlation_exposure({"NABIL": 100.0}) == 0.0

    def test_purge_position(self):
        p = PortfolioSimulator(initial_cash=10_000)
        p.apply_fill(self._fill(), commission=0.0)
        p.purge_position("NABIL")
        assert p.get_position("NABIL") is None

    def test_snapshots_to_dict(self):
        p = PortfolioSimulator(initial_cash=10_000)
        p.mark_to_market({}, bar_index=0)
        d = p.snapshots()[0].to_dict()
        assert "equity" in d and "leverage" in d


# ═══════════════════════════════════════════════════════════════════
# Statistics
# ═══════════════════════════════════════════════════════════════════


class TestStatistics:
    def test_max_drawdown(self):
        equity = [100, 90, 80, 95, 100, 70, 120]
        assert max_drawdown(equity) == pytest.approx(30.0)

    def test_max_drawdown_flat(self):
        assert max_drawdown([100, 100, 100]) == 0.0

    def test_drawdown_series(self):
        dd = drawdown_series([100, 90, 100])
        assert dd[0] == 0.0
        assert dd[1] == 10.0
        assert dd[2] == 0.0

    def test_sharpe(self):
        returns = [0.01, 0.02, -0.005, 0.015, 0.005]
        s = sharpe_ratio(returns, 252)
        assert isinstance(s, float)

    def test_sharpe_single_return(self):
        assert sharpe_ratio([0.01], 252) == 0.0

    def test_sortino(self):
        returns = [0.01, 0.02, -0.01, 0.0, -0.005]
        s = sortino_ratio(returns, 252)
        assert isinstance(s, float)

    def test_ulcer_index(self):
        ui = ulcer_index([100, 95, 90, 95, 100])
        assert ui > 0

    def test_omega(self):
        returns = [0.01, 0.02, -0.01, -0.005, 0.03]
        o = omega_ratio(returns)
        assert o > 0

    def test_omega_no_losses(self):
        assert omega_ratio([0.01, 0.02]) == float("inf")

    def test_sqn(self):
        s = sqn([0.01, 0.02, -0.01, 0.005])
        assert isinstance(s, float)

    def test_cagr(self):
        c = cagr(100, 200, 252, 252)  # 1 year double
        assert c == pytest.approx(1.0)

    def test_rolling_sharpe(self):
        returns = [0.01] * 10 + [-0.01] * 10
        rs = rolling_sharpe(returns, 5, 252)
        assert rs[0] is None
        assert rs[4] is not None
        assert rs[9] is not None

    def test_alpha_beta(self):
        returns = [0.01, 0.02, -0.01, 0.015]
        bench = [0.005, 0.01, -0.005, 0.008]
        alpha, beta = alpha_beta(returns, bench, 252)
        assert beta > 0

    def test_holding_distribution(self):
        trades = [
            Trade(trade_id=str(i), symbol="NABIL", side=PositionSide.LONG, quantity=1,
                  entry_price=1.0, exit_price=1.0, holding_bars=i)
            for i in range(1, 10)
        ]
        dist = holding_period_distribution(trades, bins=3)
        assert len(dist) == 3
        assert sum(dist) == 9

    def test_monthly_returns(self):
        equity = [100.0] + [100 + i for i in range(1, 65)]
        timestamps = pd.date_range("2024-01-01", periods=65, freq="B")
        table = monthly_returns_table(equity, timestamps)
        assert isinstance(table, pd.DataFrame)

    def test_exposure_time(self):
        assert exposure_time([1, 1, 1], [0.5, 0.0, 1.0], threshold=0.1) == pytest.approx(2 / 3)

    def test_analyzer_full(self):
        equity = [1000.0]
        returns = []
        for i in range(1, 60):
            equity.append(equity[-1] * (1 + (0.001 if i % 2 else -0.0005)))
            returns.append(0.001 if i % 2 else -0.0005)
        trades = [
            Trade(trade_id="t", symbol="NABIL", side=PositionSide.LONG, quantity=100,
                  entry_price=10.0, exit_price=11.0, net_pnl=100.0, gross_pnl=100.0,
                  return_pct=0.10, holding_bars=5)
            for _ in range(5)
        ]
        a = AdvancedPerformanceAnalyzer()
        report = a.analyze(equity, returns, trades=trades)
        assert report.trade_count == 5
        assert report.win_rate == 100.0
        assert report.sharpe is not None
        d = report.to_dict()
        assert "sharpe" in d

    def test_analyzer_empty(self):
        a = AdvancedPerformanceAnalyzer()
        report = a.analyze([1000.0], [])
        assert report.trade_count == 0


# ═══════════════════════════════════════════════════════════════════
# Corporate actions
# ═══════════════════════════════════════════════════════════════════


class TestCorporateActions:
    def _df(self):
        dates = pd.date_range("2024-01-01", periods=10, freq="B")
        return pd.DataFrame({
            "Date": dates, "Open": range(100, 110), "High": range(105, 115),
            "Low": range(95, 105), "Close": range(100, 110), "Volume": [1000] * 10,
        }).astype({"Open": float, "High": float, "Low": float, "Close": float})

    def test_adjust_price_split(self):
        df = self._df()
        adj = adjust_price(df, 2.0)
        assert adj["Close"].iloc[0] == 50.0
        assert adj["Volume"].iloc[0] == 2000.0

    def test_adjust_price_zero_ratio(self):
        df = self._df()
        adj = adjust_price(df, 0.0)
        assert adj["Close"].iloc[0] == 100.0

    def test_dividend(self):
        df = self._df()
        adj = apply_dividend(df, 5.0, "2024-01-05")
        # Bars on/before ex-date are reduced.
        assert adj["Close"].iloc[0] == pytest.approx(95.0)
        assert adj["Close"].iloc[-1] == pytest.approx(109.0)  # after ex-date unchanged

    def test_engine_split(self):
        df = self._df()
        engine = CorporateActionEngine([{"kind": "split", "symbol": "NABIL", "ratio": 2.0}])
        result = engine.apply_all({"NABIL": df})
        assert result["NABIL"]["Close"].iloc[0] == 50.0

    def test_engine_reverse_split(self):
        df = self._df()
        engine = CorporateActionEngine([{"kind": "reverse_split", "symbol": "NABIL", "ratio": 2.0}])
        result = engine.apply_all({"NABIL": df})
        assert result["NABIL"]["Close"].iloc[0] == 200.0

    def test_engine_bonus(self):
        df = self._df()
        engine = CorporateActionEngine([{"kind": "bonus", "symbol": "NABIL", "ratio": 1.0}])
        result = engine.apply_all({"NABIL": df})
        assert result["NABIL"]["Close"].iloc[0] == pytest.approx(50.0)

    def test_engine_delisting(self):
        df = self._df()
        engine = CorporateActionEngine([{"kind": "delisting", "symbol": "NABIL", "date": "2024-01-05"}])
        result = engine.apply_all({"NABIL": df})
        # Bars on/before the delisting date are kept (01-01 .. 01-05 = 5).
        assert len(result["NABIL"]) == 5

    def test_engine_delisting_no_date(self):
        df = self._df()
        engine = CorporateActionEngine([{"kind": "delisting", "symbol": "NABIL"}])
        result = engine.apply_all({"NABIL": df})
        assert "NABIL" not in result

    def test_engine_symbol_change(self):
        df = self._df()
        engine = CorporateActionEngine([{"kind": "symbol_change", "symbol": "OLD", "to_symbol": "NEW"}])
        result = engine.apply_all({"OLD": df})
        assert "NEW" in result
        assert "OLD" not in result

    def test_invalid_kind_raises(self):
        with pytest.raises(ValueError):
            CorporateAction(kind="nope", symbol="NABIL")

    def test_from_dict(self):
        a = CorporateAction.from_dict({"kind": "split", "symbol": "NABIL", "ratio": 2.0})
        assert a.kind == "split"
        assert a.ratio == 2.0

    def test_actions_for_and_len(self):
        engine = CorporateActionEngine([{"kind": "split", "symbol": "A"}, {"kind": "split", "symbol": "B"}])
        assert len(engine) == 2
        assert len(engine.actions_for("A")) == 1


# ═══════════════════════════════════════════════════════════════════
# Backtest engine end-to-end
# ═══════════════════════════════════════════════════════════════════


class TestBacktestEngine:
    def test_run_produces_equity_curve(self, sample_df):
        engine = BacktestEngine()
        result = engine.run({"NABIL": sample_df})
        assert len(result.equity_curve) == len(sample_df) + 1
        assert result.equity_curve[0] == pytest.approx(1_000_000.0)

    def test_run_with_strategy_produces_trades(self, sample_df):
        from src.backtesting.orders import OrderManager

        def strategy(bar_index, bars, ctx):
            manager: OrderManager = ctx["order_manager"]
            if bar_index == 10:
                return [manager.market("NABIL", OrderSide.BUY, 100)]
            if bar_index == 50:
                return [manager.market("NABIL", OrderSide.SELL, 100)]
            return []

        engine = BacktestEngine(strategy=strategy)
        result = engine.run({"NABIL": sample_df})
        assert len(result.fills) >= 2
        assert len(result.trades) == 1
        t = result.trades[0]
        assert t.quantity == 100

    def test_run_tracks_cash_and_equity(self, sample_df):
        def strategy(bar_index, bars, ctx):
            manager = ctx["order_manager"]
            if bar_index == 5:
                return [manager.market("NABIL", OrderSide.BUY, 100)]
            return []

        engine = BacktestEngine(config=BacktestConfig(initial_cash=100_000), strategy=strategy)
        result = engine.run({"NABIL": sample_df})
        # Position closed at end of data -> trade recorded
        assert len(result.trades) == 1
        assert result.metrics.trade_count == 1

    def test_empty_data(self):
        engine = BacktestEngine()
        result = engine.run({})
        assert isinstance(result, BacktestResult)
        assert result.equity_curve == []

    def test_result_to_dict(self, sample_df):
        engine = BacktestEngine()
        result = engine.run({"NABIL": sample_df})
        d = result.to_dict()
        assert "equity_curve" in d
        assert "metrics" in d
        assert d["total_trades"] == 0

    def test_event_emission(self, sample_df):
        from src.backtesting.events import EventBus
        bus = EventBus()
        events = []
        bus.subscribe(None, lambda t, p: events.append(t))
        engine = BacktestEngine(event_bus=bus)
        engine.run({"NABIL": sample_df})
        assert EventType.EQUITY_UPDATED in events
        assert EventType.BACKTEST_COMPLETE in events

    def test_benchmark(self, sample_df):
        bench = sample_df.copy()
        bench.columns = [c if c != "Close" else "Close" for c in bench.columns]
        config = BacktestConfig(benchmark_symbol="NEPSE")
        engine = BacktestEngine(config=config)
        result = engine.run({"NABIL": sample_df, "NEPSE": bench})
        assert len(result.benchmark_curve) > 0

    def test_short_strategy(self, sample_df):
        def strategy(bar_index, bars, ctx):
            manager = ctx["order_manager"]
            if bar_index == 10:
                return [manager.market("NABIL", OrderSide.SELL, 100)]
            if bar_index == 40:
                return [manager.market("NABIL", OrderSide.BUY, 100)]
            return []

        engine = BacktestEngine(strategy=strategy)
        result = engine.run({"NABIL": sample_df})
        assert len(result.trades) == 1

    def test_trailing_stop_strategy(self, sample_df):
        # Buy first so the trailing stop has something to exit.
        def strategy2(bar_index, bars, ctx):
            manager = ctx["order_manager"]
            if bar_index == 2:
                return [manager.market("NABIL", OrderSide.BUY, 100)]
            if bar_index == 5:
                bar = bars["NABIL"]
                return [manager.trailing_stop("NABIL", OrderSide.SELL, 100, bar.close * 0.9, pct=0.05)]
            return []

        engine = BacktestEngine(strategy=strategy2)
        result = engine.run({"NABIL": sample_df})
        assert len(result.trades) >= 1


# ═══════════════════════════════════════════════════════════════════
# Scenarios
# ═══════════════════════════════════════════════════════════════════


class TestScenarios:
    def test_all_scenarios_available(self):
        assert "bull" in SCENARIOS
        assert "flash_crash" in SCENARIOS
        assert len(SCENARIOS) == 8

    def test_transform_unknown_raises(self, sample_df):
        lab = ScenarioLab()
        with pytest.raises(ValueError):
            lab.transform({"NABIL": sample_df}, "nope")

    def test_transform_bull(self, sample_df):
        lab = ScenarioLab()
        out = lab.transform({"NABIL": sample_df}, "bull", seed=1)
        assert "NABIL" in out
        assert len(out["NABIL"]) == len(sample_df)

    def test_run_produces_result(self, sample_df):
        lab = ScenarioLab()
        r = lab.run({"NABIL": sample_df}, "sideways", seed=1)
        assert r.scenario == "sideways"
        assert isinstance(r.final_equity, float)

    def test_run_all(self, sample_df):
        lab = ScenarioLab()
        results = lab.run_all({"NABIL": sample_df}, seed=1)
        assert len(results) == 8

    def test_comparison_table(self, sample_df):
        lab = ScenarioLab()
        lab.run_all({"NABIL": sample_df}, seed=1)
        table = lab.comparison_table()
        assert len(table) == 8
        assert "scenario" in table[0]

    def test_flash_crash_drops_prices(self, sample_df):
        lab = ScenarioLab()
        out = lab.transform({"NABIL": sample_df}, "flash_crash", seed=1)
        assert out["NABIL"]["Close"].min() < sample_df["Close"].min()

    def test_liquidity_crisis_reduces_volume(self, sample_df):
        lab = ScenarioLab()
        out = lab.transform({"NABIL": sample_df}, "liquidity_crisis", seed=1)
        assert out["NABIL"]["Volume"].sum() < sample_df["Volume"].sum()

    def test_scenario_result_to_dict(self):
        from src.backtesting.scenarios import ScenarioResult
        r = ScenarioResult(scenario="bull", total_return=0.1)
        d = r.to_dict()
        assert d["scenario"] == "bull"


# ═══════════════════════════════════════════════════════════════════
# Walk forward
# ═══════════════════════════════════════════════════════════════════


class TestWalkForward:
    def _df(self, n=300):
        rng = np.random.default_rng(3)
        dates = pd.date_range("2024-01-01", periods=n, freq="B")
        close = 100.0 + np.cumsum(rng.normal(0.3, 1.0, n))
        close = np.maximum(close, 20.0)
        return pd.DataFrame({
            "Date": dates, "Open": close, "High": close + 1,
            "Low": close - 1, "Close": close, "Volume": [1000] * n,
        })

    def test_split_windows(self):
        wf = WalkForwardEngine(train_size=100, test_size=20)
        windows = wf.split_windows(300)
        assert len(windows) == 10  # (300-100-20)/20 + 1

    def test_split_expanding(self):
        wf = WalkForwardEngine(train_size=100, test_size=20, expanding=True)
        windows = wf.split_windows(300)
        assert windows[0][0] == 0
        assert windows[1][0] == 0  # expanding keeps train start at 0

    def test_split_insufficient_data(self):
        wf = WalkForwardEngine(train_size=100, test_size=20)
        assert wf.split_windows(50) == []

    def test_invalid_sizes(self):
        with pytest.raises(ValueError):
            WalkForwardEngine(train_size=0, test_size=20)

    def test_run(self):
        wf = WalkForwardEngine(train_size=100, test_size=20)
        df = self._df()
        build = lambda params: (lambda bi, bars, ctx: [])
        folds, summary = wf.run(df, build)
        assert len(folds) == 10
        assert summary.folds == 10

    def test_parameter_grid(self):
        wf = WalkForwardEngine(train_size=100, test_size=20)
        df = self._df()

        def build(params):
            return lambda bi, bars, ctx: []

        folds, summary = wf.run(df, build, parameter_grid=[{"a": 1}, {"a": 2}])
        assert len(folds) > 0
        assert "parameters" in summary.to_dict()

    def test_save_load_parameters(self, tmp_path):
        wf = WalkForwardEngine(train_size=100, test_size=20)
        wf._parameters = {"lookback": 10}
        path = tmp_path / "params.json"
        wf.save_parameters(path)
        loaded = WalkForwardEngine.load_parameters(path)
        assert loaded == {"lookback": 10}

    def test_load_missing(self, tmp_path):
        assert WalkForwardEngine.load_parameters(tmp_path / "missing.json") == {}

    def test_summary_empty(self):
        wf = WalkForwardEngine(train_size=100, test_size=20)
        s = wf._summary()
        assert s.folds == 0


# ═══════════════════════════════════════════════════════════════════
# Multi-timeframe
# ═══════════════════════════════════════════════════════════════════


class TestMultiTimeframe:
    def _df(self, n=100):
        dates = pd.date_range("2024-01-01", periods=n, freq="B")
        return pd.DataFrame({
            "Date": dates, "Open": range(100, 100 + n), "High": range(105, 105 + n),
            "Low": range(95, 95 + n), "Close": range(100, 100 + n), "Volume": [1000] * n,
        }).astype({"Open": float, "High": float, "Low": float, "Close": float})

    def test_resample_weekly(self):
        df = self._df()
        weekly = resample_frame(df, "W")
        assert len(weekly) < len(df)

    def test_resample_monthly(self):
        df = self._df()
        monthly = resample_frame(df, "ME")
        assert len(monthly) < len(df)

    def test_build_frames(self):
        df = self._df()
        mtf = MultiTimeframeEngine(df)
        frames = mtf.frames()
        assert set(frames.keys()) == {"1D", "1W", "1M"}

    def test_iter_aligned(self):
        df = self._df()
        mtf = MultiTimeframeEngine(df)
        aligned = list(mtf.iter_aligned())
        assert len(aligned) == len(df)
        assert aligned[0].daily is not None

    def test_len(self):
        df = self._df()
        mtf = MultiTimeframeEngine(df)
        assert len(mtf) == len(df)

    def test_latest_weekly(self):
        df = self._df()
        mtf = MultiTimeframeEngine(df)
        weekly = mtf.latest_weekly(df["Date"].iloc[-1])
        assert weekly is not None
        assert "Close" in weekly

    def test_resample_any_rule(self):
        df = self._df()
        mtf = MultiTimeframeEngine(df)
        bi = mtf.resample("2W")
        assert len(bi) < len(df)


# ═══════════════════════════════════════════════════════════════════
# Reports
# ═══════════════════════════════════════════════════════════════════


class TestReports:
    def _result(self):
        engine = BacktestEngine()
        rng = np.random.default_rng(9)
        dates = pd.date_range("2024-01-01", periods=60, freq="B")
        close = 100.0 + np.cumsum(rng.normal(0.3, 1.0, 60))
        df = pd.DataFrame({"Date": dates, "Open": close, "High": close + 1,
                           "Low": close - 1, "Close": close, "Volume": [1000] * 60})
        return engine.run({"NABIL": df})

    def test_executive_summary(self):
        r = InstitutionalReport(self._result())
        summary = r.executive_summary()
        assert len(summary) > 10

    def test_risk_metrics(self):
        r = InstitutionalReport(self._result())
        assert len(r.risk_metrics()) > 5

    def test_to_json(self):
        r = InstitutionalReport(self._result(), ai_commentary="Test commentary")
        data = r.to_json()
        assert isinstance(data, bytes)
        assert b"Test commentary" in data

    def test_to_html(self):
        r = InstitutionalReport(self._result())
        html = r.to_html()
        assert "<html>" in html
        assert "Executive Summary" in html

    def test_to_excel(self):
        r = InstitutionalReport(self._result())
        data = r.to_excel()
        assert isinstance(data, bytes)
        assert len(data) > 0

    def test_to_pdf(self):
        r = InstitutionalReport(self._result())
        pdf = r.to_pdf()
        assert isinstance(pdf, bytes)
        assert b"%PDF" in pdf

    def test_generate_report(self):
        from src.backtesting.reports import generate_report
        r = generate_report(self._result(), title="Custom")
        assert r.title == "Custom"

    def test_sections_include_ai(self):
        r = InstitutionalReport(self._result(), ai_commentary="AI says buy")
        titles = [s["title"] for s in r.sections()]
        assert "AI Commentary" in titles


# ═══════════════════════════════════════════════════════════════════
# Tournament
# ═══════════════════════════════════════════════════════════════════


class TestTournament:
    def _df(self):
        rng = np.random.default_rng(4)
        dates = pd.date_range("2024-01-01", periods=80, freq="B")
        close = 100.0 + np.cumsum(rng.normal(0.3, 1.0, 80))
        return pd.DataFrame({"Date": dates, "Open": close, "High": close + 1,
                             "Low": close - 1, "Close": close, "Volume": [1000] * 80})

    def _buy_hold(self, symbol="NABIL"):
        def strategy(bar_index, bars, ctx):
            from src.backtesting.orders import OrderManager
            manager: OrderManager = ctx["order_manager"]
            if bar_index == 2:
                return [manager.market(symbol, OrderSide.BUY, 50)]
            if bar_index == 60:
                return [manager.market(symbol, OrderSide.SELL, 50)]
            return []
        return strategy

    def test_run_ranks(self):
        df = self._df()
        tournament = StrategyTournament()
        strategies = {"A": self._buy_hold(), "B": self._buy_hold()}
        entries = tournament.run({"NABIL": df}, strategies)
        assert len(entries) == 2
        ranked = tournament.rank(entries)
        assert ranked[0].rank == 1

    def test_entry_to_dict(self):
        e = TournamentEntry(name="X", total_return=0.1)
        d = e.to_dict()
        assert d["name"] == "X"

    def test_ai_score(self):
        s = StrategyTournament._ai_score(1.0, 3.0, 0.0, 100.0)
        assert s == pytest.approx(100.0)

    def test_rank_by_return(self):
        a = TournamentEntry(name="a", total_return=0.2)
        b = TournamentEntry(name="b", total_return=0.5)
        tournament = StrategyTournament()
        ranked = tournament.rank([a, b], key="total_return")
        assert ranked[0].name == "b"

    def test_failed_strategy_flagged(self):
        df = self._df()

        def bad(bar_index, bars, ctx):
            raise RuntimeError("boom")

        tournament = StrategyTournament()
        entries = tournament.run({"NABIL": df}, {"Bad": bad})
        assert entries[0].metadata.get("error")

    def test_ranking_table(self):
        df = self._df()
        tournament = StrategyTournament()
        tournament.run({"NABIL": df}, {"A": self._buy_hold()})
        table = tournament.ranking_table()
        assert len(table) == 1


# ═══════════════════════════════════════════════════════════════════
# Replay bridge
# ═══════════════════════════════════════════════════════════════════


class TestReplayBridge:
    def test_load(self, sample_df):
        bridge = ReplayBacktestBridge()
        assert bridge.load(sample_df, "NABIL") is True
        assert bridge.state.symbol == "NABIL"

    def test_step(self, sample_df):
        bridge = ReplayBacktestBridge()
        bridge.load(sample_df, "NABIL")
        frame = bridge.step()
        assert frame is not None
        assert bridge.state.bar_index == 1

    def test_current_decision(self, sample_df):
        def strategy(bar_index, bars, ctx):
            return []

        bridge = ReplayBacktestBridge(strategy=strategy)
        bridge.load(sample_df, "NABIL")
        decision = bridge.current_decision(sample_df)
        assert decision == []

    def test_run_full_backtest(self, sample_df):
        bridge = ReplayBacktestBridge()
        bridge.load(sample_df, "NABIL")
        result = bridge.run_full_backtest(sample_df)
        assert result is not None
        assert bridge.state.backtest_complete
        assert bridge.result is result

    def test_reset(self, sample_df):
        bridge = ReplayBacktestBridge()
        bridge.load(sample_df, "NABIL")
        bridge.reset()
        assert bridge.state.symbol == ""

    def test_inspect_orders(self, sample_df):
        bridge = ReplayBacktestBridge()
        bridge.load(sample_df, "NABIL")
        assert bridge.inspect_orders() == []
