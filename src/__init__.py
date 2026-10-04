"""NEPSE Quant Engine — algorithmic trading and market analysis platform.

Subpackages:
- engine: Analysis and signal generation
- indicators: Technical indicators (RSI, MACD, Bollinger, etc.)
- strategies: Trading strategies (momentum, breakout, adaptive)
- regime: Market regime detection
- backtest: Historical backtesting engine
- portfolio: Portfolio management, allocation, optimisation
- risk: Risk management and position sizing
- scanner: Market scanning and ranking
- signals: Signal scoring and aggregation
- decision: Entry/exit decision engine
- analytics: Performance analytics
- alerts: Alert generation and management
- recommendations: Trade recommendations
- data: Centralised DataService with cache/providers/WebSocket
- ml: Machine learning models
- ai: AI advisor
- backtesting: Institutional backtesting engine
- ui: Streamlit-based frontend
- api: FastAPI-based REST API
- bot: Telegram bot interface
"""

from __future__ import annotations

from src.version import __version__

__all__ = ["__version__"]