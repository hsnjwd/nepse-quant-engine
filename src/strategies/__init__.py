"""Quantitative trading strategies package for NEPSE Quant Engine."""

from __future__ import annotations

from src.strategies.base import BaseStrategy
from src.strategies.breakout import BreakoutStrategy
from src.strategies.momentum import MomentumStrategy
from src.strategies.registry import (
    StrategyRegistry,
    get_strategy,
    list_strategies,
    register_strategy,
)

__all__ = [
    "BaseStrategy",
    "MomentumStrategy",
    "BreakoutStrategy",
    "StrategyRegistry",
    "get_strategy",
    "register_strategy",
    "list_strategies",
]
