"""FastAPI application entry point for NEPSE Quant Engine."""

from __future__ import annotations

from fastapi import FastAPI

from src.api.analyze import router as analyze_router
from src.api.backtest import router as backtest_router
from src.api.health import router as health_router
from src.api.portfolio import router as portfolio_router
from src.api.scanner import router as scanner_router
from src.api.watchlist import router as watchlist_router

app = FastAPI(
    title="NEPSE Quant Engine API",
    description="Quantitative trading engine API for NEPSE stock market analysis and backtesting.",
    version="0.5.0",
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