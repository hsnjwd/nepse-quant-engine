"""Institutional backtesting & execution engine for the NEPSE Quant Engine.

Sprint 9 — a professional, event-driven backtesting platform inspired
by QuantConnect / Zipline / Backtrader / vectorbt, fully compatible
with the existing architecture and DataService.

Modules:
    models:          Core dataclasses (Order, Fill, Position, Trade, Bar).
    events:          Publish/subscribe event bus.
    timeline:        Bar-by-bar synchronised timeline across symbols.
    orders:          Advanced order types + lifecycle state machine.
    fills:           Fill decision & execution with slippage/commission.
    slippage:        Pluggable slippage models.
    commission:      Pluggable commission models + tax policy.
    portfolio:       Multi-position, leverage, short-selling simulation.
    statistics:      Advanced performance analytics.
    engine:          Event-driven :class:`BacktestEngine`.
    corporate_actions: Splits, dividends, rights, delisting.
    scenarios:       Scenario laboratory for stress testing.
    walk_forward:    Rolling/expanding walk-forward optimisation.
    multi_timeframe: Daily/weekly/monthly synchronisation.
    reports:         Institutional HTML/PDF/Excel/JSON reports.
    tournament:      Strategy tournament and ranking.
"""

from src.backtesting.models import (
    BacktestConfig,
    Bar,
    EventType,
    ExitReason,
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
from src.backtesting.engine import BacktestEngine, BacktestResult
from src.backtesting.events import BacktestEvent, EventBus
from src.backtesting.orders import OrderManager
from src.backtesting.portfolio import PortfolioSimulator, PortfolioSnapshot
from src.backtesting.statistics import (
    AdvancedPerformanceAnalyzer,
    AdvancedPerformanceReport,
    max_drawdown,
    sharpe_ratio,
    sortino_ratio,
    ulcer_index,
    omega_ratio,
    sqn,
    cagr,
)
from src.backtesting.scenarios import ScenarioLab, ScenarioResult
from src.backtesting.walk_forward import WalkForwardEngine, WalkForwardFold, WalkForwardSummary
from src.backtesting.tournament import StrategyTournament, TournamentEntry
from src.backtesting.reports import InstitutionalReport, generate_report

__all__ = [
    "BacktestConfig",
    "Bar",
    "EventType",
    "ExitReason",
    "Fill",
    "Order",
    "OrderSide",
    "OrderStatus",
    "OrderType",
    "Position",
    "PositionSide",
    "TimeInForce",
    "Trade",
    "BacktestEngine",
    "BacktestResult",
    "BacktestEvent",
    "EventBus",
    "OrderManager",
    "PortfolioSimulator",
    "PortfolioSnapshot",
    "AdvancedPerformanceAnalyzer",
    "AdvancedPerformanceReport",
    "max_drawdown",
    "sharpe_ratio",
    "sortino_ratio",
    "ulcer_index",
    "omega_ratio",
    "sqn",
    "cagr",
    "ScenarioLab",
    "ScenarioResult",
    "WalkForwardEngine",
    "WalkForwardFold",
    "WalkForwardSummary",
    "StrategyTournament",
    "TournamentEntry",
    "InstitutionalReport",
    "generate_report",
]
