from fastapi import FastAPI

from src.api.health import router as health_router
from src.api.analyze import router as analyze_router
from src.api.watchlist import router as watchlist_router
from src.api.scanner import router as scanner_router
from src.api.backtest import router as backtest_router
from src.api.portfolio import router as portfolio_router

app = FastAPI(
    title="NEPSE Quant Engine API"
)

# Health
app.include_router(health_router)

# Analyze
app.include_router(
    analyze_router,
    prefix="/analyze",
    tags=["Analyze"]
)

# Watchlist
app.include_router(
    watchlist_router,
    tags=["Watchlist"]
)

# Market Scanner
app.include_router(
    scanner_router,
    prefix="/market",
    tags=["Market Scanner"]
)

# Backtest
app.include_router(
    backtest_router,
    prefix="/backtest",
    tags=["Backtest"]
)

# Portfolio
app.include_router(
    portfolio_router,
    prefix="/portfolio",
    tags=["Portfolio"]
)