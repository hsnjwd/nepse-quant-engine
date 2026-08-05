"""Event-driven institutional backtesting engine.

The engine (Part 9.1) drives bar-by-bar simulation across one or more
symbols, submits orders through an :class:`OrderManager`, executes
them with a :class:`FillEngine` against a :class:`PortfolioSimulator`,
and collects equity curves, fills, and closed trades.  It is
benchmark-aware, corporate-action-aware, and fully event-driven via
the :class:`EventBus`.

Usage::

    engine = BacktestEngine(
        config=BacktestConfig(initial_cash=1_000_000),
        event_bus=bus,
    )
    result = engine.run({"NABIL": df})
    print(result.equity_curve, result.trades)
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable

import pandas as pd

from src.backtesting.commission import TaxPolicy, build_commission_model
from src.backtesting.corporate_actions import CorporateActionEngine
from src.backtesting.events import BacktestEvent, EventBus
from src.backtesting.fills import FillEngine
from src.backtesting.models import (
    BacktestConfig,
    Bar,
    EventType,
    ExitReason,
    Fill,
    Order,
    OrderSide,
    OrderType,
    PositionSide,
    TimeInForce,
    Trade,
)
from src.backtesting.orders import OrderManager
from src.backtesting.portfolio import PortfolioSimulator, PortfolioSnapshot
from src.backtesting.slippage import build_slippage_model
from src.backtesting.statistics import (
    AdvancedPerformanceAnalyzer,
    AdvancedPerformanceReport,
)
from src.backtesting.timeline import BacktestTimeline

logger = logging.getLogger(__name__)


@dataclass
class BacktestResult:
    """The complete output of a backtest run.

    Attributes:
        equity_curve: Equity values per master bar (starting cash first).
        timestamps: Timestamps aligned with the equity curve.
        returns: Per-period fractional returns aligned with equity.
        portfolio_snapshots: Valuation snapshots per bar.
        fills: All executed fills.
        orders: All orders (including unfilled).
        trades: Closed round-trip trades.
        open_positions: Positions still open at the end.
        metrics: Advanced performance report.
        benchmark_curve: Optional benchmark equity curve.
        benchmark_returns: Optional benchmark returns.
        config: The configuration used.
        symbol_trades: Mapping of symbol -> closed trades.
    """

    equity_curve: list[float] = field(default_factory=list)
    timestamps: list[Any] = field(default_factory=list)
    returns: list[float] = field(default_factory=list)
    portfolio_snapshots: list[PortfolioSnapshot] = field(default_factory=list)
    fills: list[Fill] = field(default_factory=list)
    orders: list[Order] = field(default_factory=list)
    trades: list[Trade] = field(default_factory=list)
    open_positions: list[Any] = field(default_factory=list)
    metrics: AdvancedPerformanceReport = field(default_factory=AdvancedPerformanceReport)
    benchmark_curve: list[float] = field(default_factory=list)
    benchmark_returns: list[float] = field(default_factory=list)
    config: BacktestConfig = field(default_factory=BacktestConfig)
    symbol_trades: dict[str, list[Trade]] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serialisable summary dictionary."""
        return {
            "equity_curve": [round(v, 2) for v in self.equity_curve],
            "timestamps": [str(ts) for ts in self.timestamps],
            "returns": [round(v, 6) for v in self.returns],
            "fills": [f.to_dict() for f in self.fills],
            "orders": [o.to_dict() for o in self.orders],
            "trades": [t.to_dict() for t in self.trades],
            "open_positions": list(self.open_positions),
            "metrics": self.metrics.to_dict(),
            "benchmark_curve": [round(v, 2) for v in self.benchmark_curve],
            "total_trades": len(self.trades),
            "total_fills": len(self.fills),
        }


class BacktestEngine:
    """Event-driven, bar-by-bar, multi-symbol backtesting engine.

    The engine processes each master bar, lets a user strategy submit
    orders, executes eligible orders with the fill engine, marks the
    portfolio to market, and records equity.  Corporate actions are
    applied via :class:`CorporateActionEngine` before bars are
    processed.

    Attributes:
        config: Engine configuration.
        order_manager: Order management subsystem.
        fill_engine: Fill/slippage/commission subsystem.
        portfolio: Portfolio simulation subsystem.
        event_bus: Event bus (shared or internal).
    """

    def __init__(
        self,
        config: BacktestConfig | None = None,
        event_bus: EventBus | None = None,
        strategy: Callable[[int, dict[str, Bar], Any], list[Order]] | None = None,
        corporate_actions: list[dict[str, Any]] | None = None,
    ) -> None:
        """Initialise the engine.

        Args:
            config: Backtest configuration; defaults to ``BacktestConfig()``.
            event_bus: Optional event bus; a private one is created.
            strategy: Optional callable ``(bar_index, bars, context) ->
                list[Order]`` invoked each bar.
            corporate_actions: Optional list of corporate-action dicts.
        """
        self.config = config or BacktestConfig()
        self.event_bus = event_bus or EventBus()
        self.strategy = strategy
        self.corporate_actions = corporate_actions or []

        self.order_manager = OrderManager()
        self.fill_engine = FillEngine(
            slippage=build_slippage_model(self.config.slippage_model),
            commission=build_commission_model(self.config.commission_model),
            tax_policy=TaxPolicy(
                tax_rate=self.config.tax_rate,
                stamp_duty=self.config.stamp_duty,
                order_fee=self.config.order_fee,
            ),
        )
        self.portfolio = PortfolioSimulator(
            initial_cash=self.config.initial_cash,
            allow_short=self.config.allow_short,
            max_leverage=self.config.max_leverage,
            lot_size=self.config.lot_size,
        )
        self._ca_engine = CorporateActionEngine(self.corporate_actions)
        self._last_close: dict[str, float] = {}
        self._open_entry: dict[str, dict[str, Any]] = {}
        self._closed: list[Trade] = []
        self._symbol_trades: dict[str, list[Trade]] = {}

    # ═══════════════════════════════════════════════════════════════
    # Main run loop
    # ═══════════════════════════════════════════════════════════════

    def run(self, data: dict[str, pd.DataFrame]) -> BacktestResult:
        """Run a backtest over one or more symbol DataFrames.

        Args:
            data: Mapping of symbol -> OHLCV DataFrame (Date, Open,
                High, Low, Close, Volume columns).

        Returns:
            A :class:`BacktestResult` with equity, fills, trades, and metrics.
        """
        if not data:
            logger.warning("Backtest run with empty data.")
            return BacktestResult(config=self.config)

        # Reset per-run state.
        self._last_close = {}
        self._open_entry = {}
        self._closed = []
        self._symbol_trades = {}

        adjusted = self._ca_engine.apply_all(data)
        timeline = BacktestTimeline(adjusted)

        equity_curve: list[float] = [self.config.initial_cash]
        timestamps: list[Any] = [None]
        returns: list[float] = []
        snapshot_history: list[PortfolioSnapshot] = []
        all_fills: list[Fill] = []
        all_orders: list[Order] = []
        benchmark_curve: list[float] = []
        benchmark_returns: list[float] = []

        for snapshot in timeline.iter_steps():
            bar_index = snapshot.bar_index
            bars = snapshot.bars

            # Update last close prices.
            for sym, bar in bars.items():
                self._last_close[sym] = bar.close

            prices = {sym: bar.close for sym, bar in bars.items()}

            if self.strategy is not None:
                try:
                    new_orders = self.strategy(bar_index, bars, self._strategy_context())
                    if new_orders:
                        self._enqueue(new_orders)
                except Exception as exc:
                    logger.warning("Strategy error at bar %d: %s", bar_index, exc)

            # Trailing stop ratchet.
            for order in self.order_manager.working_orders():
                if order.order_type == OrderType.TRAILING_STOP and order.symbol in prices:
                    self.order_manager.update_trailing_stops(order, prices[order.symbol])

            # Fill working orders.
            for order in list(self.order_manager.working_orders()):
                bar = bars.get(order.symbol)
                if bar is None:
                    continue
                fill = self.fill_engine.try_fill(order, bar, bar.open)
                if fill is None:
                    continue
                try:
                    self.portfolio.apply_fill(fill, commission=fill.commission, price=fill.price)
                except Exception as exc:
                    logger.warning("Fill rejected for %s: %s", order.order_id, exc)
                    self.order_manager.reject(order, str(exc))
                    continue

                all_fills.append(fill)
                self.event_bus.publish(BacktestEvent(
                    EventType.FILL, {"fill": fill, "order": order.to_dict()},
                    bar_index, bar.timestamp,
                ))

                if order.oco_group:
                    for sibling in self.order_manager.cancel_oco_siblings(order):
                        self.event_bus.publish(BacktestEvent(
                            EventType.ORDER_CANCELLED,
                            {"order_id": sibling.order_id}, bar_index, bar.timestamp,
                        ))

                # IOC: cancel any unfilled remainder after the bar.
                if order.time_in_force == TimeInForce.IOC and order.is_open:
                    self.order_manager.cancel(order)
                    self.event_bus.publish(BacktestEvent(
                        EventType.ORDER_CANCELLED,
                        {"order_id": order.order_id}, bar_index, bar.timestamp,
                    ))

                # Position opened / closed / reversed tracking.
                self._reconcile_position(
                    symbol=order.symbol,
                    exit_price=fill.price,
                    exit_time=bar.timestamp,
                    exit_bar=bar_index,
                    exit_commission=fill.commission,
                    exit_slippage=fill.slippage_cost,
                )

            # Mark to market.
            snap = self.portfolio.mark_to_market(prices, bar_index, snapshot.timestamp)
            snapshot_history.append(snap)
            equity_curve.append(snap.equity)
            timestamps.append(snapshot.timestamp)
            if len(equity_curve) >= 2 and equity_curve[-2] != 0:
                returns.append(snap.equity / equity_curve[-2] - 1.0)
            else:
                returns.append(0.0)

            if self.config.benchmark_symbol and self.config.benchmark_symbol in prices:
                bp = prices[self.config.benchmark_symbol]
                benchmark_curve.append(bp)
                if len(benchmark_curve) >= 2 and benchmark_curve[-2] != 0:
                    benchmark_returns.append(bp / benchmark_curve[-2] - 1.0)

            self.event_bus.publish(BacktestEvent(
                EventType.EQUITY_UPDATED,
                {"equity": snap.equity, "bar_index": bar_index},
                bar_index, snapshot.timestamp,
            ))

        # Close remaining positions at last known prices.
        for symbol in list(self._open_entry.keys()):
            entry = self._open_entry.pop(symbol, None)
            if entry is None:
                continue
            px = self._last_close.get(symbol, entry["entry_price"])
            self._finalize_trade(
                symbol=symbol,
                exit_price=px,
                exit_time=timestamps[-1],
                exit_bar=len(timestamps) - 1,
                reason=ExitReason.END_OF_DATA.value,
                entry=entry,
            )
            # Force-closed positions are removed from the portfolio so
            # ``open_positions`` only reflects genuinely open positions.
            self.portfolio.purge_position(symbol)

        all_orders = self.order_manager.all_orders()

        if benchmark_returns and len(benchmark_returns) < len(returns):
            benchmark_returns = benchmark_returns + [0.0] * (len(returns) - len(benchmark_returns))

        analyzer = AdvancedPerformanceAnalyzer(
            periods_per_year=self.config.trades_per_year,
            risk_free_rate=self.config.risk_free_rate,
        )
        metrics = analyzer.analyze(
            equity=equity_curve,
            returns=returns,
            trades=self._closed,
            benchmark_returns=benchmark_returns if benchmark_returns else None,
            exposure=[s.exposure for s in snapshot_history],
        )

        final_prices = self._last_close
        open_positions = [
            pos.to_dict(final_prices.get(pos.symbol, pos.average_price))
            for pos in self.portfolio.positions()
        ]

        result = BacktestResult(
            equity_curve=equity_curve,
            timestamps=timestamps,
            returns=returns,
            portfolio_snapshots=snapshot_history,
            fills=all_fills,
            orders=all_orders,
            trades=list(self._closed),
            open_positions=open_positions,
            metrics=metrics,
            benchmark_curve=benchmark_curve,
            benchmark_returns=benchmark_returns,
            config=self.config,
            symbol_trades=dict(self._symbol_trades),
        )

        self.event_bus.publish(BacktestEvent(
            EventType.BACKTEST_COMPLETE, {"result": result.to_dict()},
        ))
        logger.info(
            "Backtest complete: %d fills, %d trades, final equity %.2f",
            len(all_fills), len(self._closed), equity_curve[-1],
        )
        return result

    # ═══════════════════════════════════════════════════════════════
    # Trade finalisation
    # ═══════════════════════════════════════════════════════════════

    def _finalize_trade(
        self,
        symbol: str,
        exit_price: float,
        exit_time: Any,
        exit_bar: int,
        reason: str,
        entry: dict[str, Any],
        exit_commission: float = 0.0,
        exit_slippage: float = 0.0,
    ) -> None:
        """Record a closed :class:`Trade` from an entry record.

        The trade is built purely from ``entry`` plus the exit
        arguments — it does NOT depend on a live portfolio position,
        because the position is removed from the portfolio the moment
        a fill fully closes it.

        Args:
            symbol: Ticker symbol.
            exit_price: Price used for the exit.
            exit_time: Exit timestamp.
            exit_bar: Exit bar index.
            reason: Exit reason string.
            entry: The recorded entry dict (``side``, ``entry_price``,
                ``entry_time``, ``entry_bar``, ``quantity``).
            exit_commission: Actual exit-side commission paid.
            exit_slippage: Actual exit-side slippage cost.
        """
        qty = int(entry.get("quantity", 0))
        if qty <= 0:
            logger.warning("Cannot finalize trade for %s: no entry quantity", symbol)
            return
        avg_entry = float(entry.get("entry_price", 0.0))
        side = entry.get("side", PositionSide.LONG)

        if side == PositionSide.LONG:
            gross = (exit_price - avg_entry) * qty
        else:
            gross = (avg_entry - exit_price) * qty

        # Use the actual exit costs; fall back to an estimate when the
        # exit did not flow through a fill (e.g. end-of-data close).
        if exit_commission <= 0:
            charges = self.fill_engine.charge(qty, exit_price)
            exit_commission = charges.total
        net = gross - exit_commission - exit_slippage

        entry_value = avg_entry * qty
        return_pct = net / entry_value if entry_value > 0 else 0.0

        trade = Trade(
            trade_id=uuid.uuid4().hex[:12],
            symbol=symbol,
            side=side,
            quantity=qty,
            entry_price=avg_entry,
            exit_price=exit_price,
            entry_time=entry.get("entry_time"),
            exit_time=exit_time,
            gross_pnl=gross,
            net_pnl=net,
            commission=exit_commission,
            slippage=exit_slippage,
            return_pct=return_pct,
            holding_bars=max(exit_bar - int(entry.get("entry_bar", 0)), 0),
            exit_reason=reason,
        )
        self._closed.append(trade)
        self._symbol_trades.setdefault(symbol, []).append(trade)

    def _reconcile_position(
        self,
        symbol: str,
        exit_price: float,
        exit_time: Any,
        exit_bar: int,
        exit_commission: float = 0.0,
        exit_slippage: float = 0.0,
    ) -> None:
        """Reconcile position tracking after a fill (open / close / reverse).

        Keeps ``_open_entry`` in sync with the portfolio so closed
        portions always produce a trade and reversals start fresh
        entries.

        Args:
            symbol: Ticker symbol.
            exit_price: Price used for any closing leg.
            exit_time: Exit timestamp.
            exit_bar: Exit bar index.
            exit_commission: Exit-side commission for the closing leg.
            exit_slippage: Exit-side slippage for the closing leg.
        """
        pos = self.portfolio.get_position(symbol)
        entry = self._open_entry.get(symbol)

        if pos is None:
            # Fully closed — build the trade from the recorded entry.
            if entry is not None:
                self._open_entry.pop(symbol, None)
                self._finalize_trade(
                    symbol=symbol,
                    exit_price=exit_price,
                    exit_time=exit_time,
                    exit_bar=exit_bar,
                    reason=ExitReason.SIGNAL.value,
                    entry=entry,
                    exit_commission=exit_commission,
                    exit_slippage=exit_slippage,
                )
            return

        if entry is None:
            # New position opened.
            self._open_entry[symbol] = {
                "side": pos.side,
                "entry_price": pos.average_price,
                "entry_time": exit_time,
                "entry_bar": exit_bar,
                "quantity": pos.quantity,
            }
            return

        # Reversal: the recorded entry side differs from the current
        # position side.  Close the old trade and start a new entry.
        if entry.get("side") != pos.side:
            self._open_entry.pop(symbol, None)
            self._finalize_trade(
                symbol=symbol,
                exit_price=exit_price,
                exit_time=exit_time,
                exit_bar=exit_bar,
                reason=ExitReason.SIGNAL.value,
                entry=entry,
                exit_commission=exit_commission,
                exit_slippage=exit_slippage,
            )
            self._open_entry[symbol] = {
                "side": pos.side,
                "entry_price": pos.average_price,
                "entry_time": exit_time,
                "entry_bar": exit_bar,
                "quantity": pos.quantity,
            }
            return

        # Position enlarged or reduced without closing — keep the
        # recorded entry in sync with the current position so a later
        # full close produces an accurate trade.
        if entry.get("side") == pos.side:
            entry["entry_price"] = pos.average_price
            entry["quantity"] = pos.quantity
            self._open_entry[symbol] = entry

    # ═══════════════════════════════════════════════════════════════
    # Internal helpers
    # ═══════════════════════════════════════════════════════════════

    def _enqueue(self, orders: list[Order]) -> None:
        """Submit and accept a list of new orders."""
        for order in orders:
            if order.status.value == "CREATED":
                self.order_manager.submit(order)
            if order.status.value == "SUBMITTED":
                self.order_manager.accept(order)
            self.event_bus.publish(BacktestEvent(
                EventType.ORDER_ACCEPTED, {"order": order.to_dict()},
            ))

    def _strategy_context(self) -> dict[str, Any]:
        """Return a mutable context dict handed to the strategy hook.

        The context exposes the :class:`OrderManager` so strategies can
        create orders via ``ctx["order_manager"]``, plus configuration,
        the live portfolio, and the last known close prices.
        """
        return {
            "config": self.config,
            "portfolio": self.portfolio,
            "last_close": self._last_close,
            "order_manager": self.order_manager,
        }
