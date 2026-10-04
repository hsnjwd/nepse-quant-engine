"""Portfolio data models.

All portfolio entities are defined as immutable dataclasses.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Any


@dataclass
class PortfolioHolding:
    """A single holding in a portfolio."""

    id: int = 0
    symbol: str = ""
    quantity: int = 0
    average_price: float = 0.0
    invested: float = 0.0
    current_price: float = 0.0
    current_value: float = 0.0
    unrealized_pnl: float = 0.0
    unrealized_pnl_pct: float = 0.0
    realized_pnl: float = 0.0
    weight_pct: float = 0.0
    sector: str = ""
    updated_at: datetime = field(default_factory=datetime.now)

    @property
    def is_profitable(self) -> bool:
        return self.unrealized_pnl > 0


@dataclass
class PortfolioTransaction:
    """A buy or sell transaction."""

    id: int = 0
    symbol: str = ""
    transaction_type: str = ""  # BUY | SELL
    quantity: int = 0
    price: float = 0.0
    total: float = 0.0
    commission: float = 0.0
    pnl: float = 0.0
    notes: str = ""
    timestamp: datetime = field(default_factory=datetime.now)


@dataclass
class PortfolioSummary:
    """Full portfolio summary snapshot."""

    cash: float = 0.0
    invested: float = 0.0
    current_value: float = 0.0
    total_pnl: float = 0.0
    total_pnl_pct: float = 0.0
    unrealized_pnl: float = 0.0
    realized_pnl: float = 0.0
    total_commission: float = 0.0
    holdings_count: int = 0
    transaction_count: int = 0
    holdings: list[PortfolioHolding] = field(default_factory=list)
    timestamp: datetime = field(default_factory=datetime.now)


@dataclass
class PortfolioAnalytics:
    """Advanced portfolio analytics."""

    daily_return: float = 0.0
    weekly_return: float = 0.0
    monthly_return: float = 0.0
    ytd_return: float = 0.0
    sharpe_ratio: float = 0.0
    sortino_ratio: float = 0.0
    calmar_ratio: float = 0.0
    max_drawdown_pct: float = 0.0
    win_rate: float = 0.0
    profit_factor: float = 0.0
    avg_win: float = 0.0
    avg_loss: float = 0.0
    total_trades: int = 0
    exposure_pct: float = 0.0
    sector_allocation: dict[str, float] = field(default_factory=dict)
    timestamp: datetime = field(default_factory=datetime.now)
