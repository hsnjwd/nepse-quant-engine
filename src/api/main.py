"""FastAPI application entry point for NEPSE Quant Engine."""

from __future__ import annotations

import os
import socket
import time
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware

from src.logging.logger import logger
from src.version import __version__


@asynccontextmanager
async def _worker_lifespan(app: FastAPI):
    """Log worker process start/stop with identity (Sprint 13.2).

    Under ``uvicorn --workers=N`` each worker is a separate OS process;
    FastAPI's lifespan runs once per worker, so this emits one
    ``worker start pid=...`` / ``worker stop pid=...`` line per worker.
    That makes multi-worker deployments diagnosable from logs alone
    (which pid served which requests, when workers came and went) — the
    Phase 14 observability requirement.  The ``pid``/``hostname`` are
    process identity only — never secrets or filesystem paths.
    """
    logger.info(
        "worker start pid=%s hostname=%s app=%s",
        os.getpid(),
        socket.gethostname(),
        type(app).__name__,
    )
    try:
        yield
    finally:
        logger.info("worker stop pid=%s", os.getpid())

from src.api.analyze import router as analyze_router
from src.api.backtest import router as backtest_router
from src.api.health import router as health_router
from src.api.metrics import router as metrics_router
from src.api.timing import request_tracker
from src.api.models import router as models_router
from src.api.optimizer import router as optimizer_router
from src.api.portfolio import router as portfolio_router
from src.api.regime import router as regime_router
from src.api.replay import router as replay_router
from src.api.risk import router as risk_router
from src.api.scanner import (
    market_status_response as _market_status_response,
    router as scanner_router,
)
from src.api.signals import router as signals_router
from src.api.simulation import router as simulation_router
from src.api.stocks import router as stocks_router
from src.api.strategies import router as strategies_router
from src.api.watchlist import router as watchlist_router

app = FastAPI(
    title="NEPSE Quant Engine API",
    description="Quantitative trading engine API for NEPSE stock market analysis and backtesting.",
    version=__version__,
    lifespan=_worker_lifespan,
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

# Request timing middleware (structured latency logging + bounded
# request tracker).  The tracker records every request into a bounded,
# thread-safe per-endpoint window (Sprint 13.1 Phase 2) so /metrics can
# report endpoint-level latency percentiles and error counts.  It is
# cheap (one deque append + two counters) and can never raise into the
# request path.
@app.middleware("http")
async def request_timing(request: Request, call_next):
    """Log every request's latency as a structured timing record."""
    start = time.perf_counter()
    status = 500
    try:
        response = await call_next(request)
        status = response.status_code
    except Exception:
        logger.exception(
            "timing name=api_request method=%s path=%s status=500",
            request.method,
            request.url.path,
        )
        raise
    finally:
        duration_ms = (time.perf_counter() - start) * 1000.0
        # Bounded per-endpoint recording (Sprint 13.1 Phase 2): feeds
        # /metrics endpoint-level latency/error snapshots.  Cheap and
        # never raises.
        request_tracker.record(request.url.path, status, duration_ms)
    # Success path only (an exception re-raised above).  Same structured
    # timing line as before the tracker was added.
    logger.debug(
        "timing name=api_request method=%s path=%s status=%d duration_ms=%.2f",
        request.method,
        request.url.path,
        status,
        duration_ms,
    )
    return response


# Health Check
app.include_router(health_router)

# Performance Metrics
app.include_router(metrics_router)

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

# Live market status — top-level alias for ``/market/status`` so the
# live NEPSE index/change/turnover/volume snapshot is also reachable at
# ``/market-status`` (identical semantics; both delegate to
# ``market_status_response``).
@app.get("/market-status", tags=["Market Scanner"])
def market_status_alias() -> dict[str, Any]:
    """Live NEPSE market status (alias of ``/market/status``)."""
    return _market_status_response()

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
