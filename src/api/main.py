"""FastAPI application entry point for NEPSE Quant Engine."""

from __future__ import annotations

import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from src.version import __version__

from src.api.analyze import router as analyze_router
from src.api.backtest import router as backtest_router
from src.api.health import router as health_router
from src.api.models import router as models_router
from src.api.optimizer import router as optimizer_router
from src.api.portfolio import router as portfolio_router
from src.api.regime import router as regime_router
from src.api.replay import router as replay_router
from src.api.risk import router as risk_router
from src.api.scanner import router as scanner_router
from src.api.signals import router as signals_router
from src.api.simulation import router as simulation_router
from src.api.stocks import router as stocks_router
from src.api.strategies import router as strategies_router
from src.api.watchlist import router as watchlist_router

app = FastAPI(
    title="NEPSE Quant Engine API",
    description="Quantitative trading engine API for NEPSE stock market analysis and backtesting.",
    version=__version__,
)

# ── CORS middleware (configurable via CORS_ORIGINS env var) ─────
_CORS_ORIGINS = os.getenv(
    "CORS_ORIGINS",
    "http://localhost:8501,http://127.0.0.1:8501",
).split(",")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in _CORS_ORIGINS if o.strip()],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Health Check
app.include_router(health_router)

# Stock Analysis
app.include_router(
    analyze_router,
    prefix="/analyze",
    tags=["Analyze"],
)

# Watchlist Management
app.include_router(
    watchlist_router,
)

# Market Scanner
app.include_router(
    scanner_router,
    prefix="/market",
    tags=["Market Scanner"],
)

# Regime Detection
app.include_router(
    regime_router,
    tags=["Regime Detection"],
)

# Monte Carlo Simulation
app.include_router(
    simulation_router,
    tags=["Monte Carlo Simulation"],
)

# Backtest Engine
app.include_router(
    backtest_router,
    prefix="/backtest",
    tags=["Backtest"],
)

# Portfolio Management
app.include_router(
    portfolio_router,
    prefix="/portfolio",
    tags=["Portfolio"],
)

# Stocks & Quotes
app.include_router(stocks_router)

# Market Signals
app.include_router(signals_router)

# Strategy Marketplace
app.include_router(strategies_router)

# ML Models
app.include_router(models_router)

# Optimizer
app.include_router(optimizer_router)

# Risk Lab
app.include_router(risk_router)

# Market Replay
app.include_router(replay_router)
