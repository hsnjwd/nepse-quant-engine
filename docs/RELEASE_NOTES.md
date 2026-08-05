# NEPSE Quant Engine — v1.0.0-rc1 Release Notes

**Release Date:** 2026-07-30  
**Version:** 1.0.0-rc1  
**Status:** Release Candidate

---

## Overview

NEPSE Quant Engine is an open-source quantitative trading and market analysis platform for the Nepal Stock Exchange (NEPSE). This release candidate marks the first production-ready version of the platform, featuring a professional Streamlit dashboard, centralised data service, persistent portfolio management, paper trading engine, and comprehensive backtesting infrastructure.

---

## What's New

### 🖥️ Professional Streamlit Dashboard (24 Pages)

| Page | Description |
|------|-------------|
| **Dashboard** | Live market snapshot: NEPSE index, top gainers/losers/turnover, market breadth, portfolio summary, signals, alerts |
| **Market Scanner** | Full-featured scanner with saved presets, filters (signal, score, confidence, RSI, volume, regime), sort, export |
| **Stock Analysis** | Deep technical analysis with candlestick, volume, RSI, MACD, Bollinger Bands, ATR, OBV charts; support/resistance |
| **Advanced Charts** | Real-time WebSocket live charts with LIVE indicator, auto-fallback to polling |
| **Watchlist** | Live price and signal updates every 30s; add, remove, scan |
| **Portfolio** | SQLite-backed persistent holdings, transactions, P&L, allocation charts |
| **Portfolio Analytics** | Sharpe/Sortino/Calmar ratios, rolling returns, volatility, Alpha/Beta, Information/Treynor |
| **Paper Trading** | Market/limit/stop-loss/take-profit orders, pending queue, trade history |
| **Market Regime** | Detected regime (PANIC → OVERHEATED → BULL → BEAR → RECOVERY → DISTRIBUTION → ACCUMULATION → SIDEWAYS → LOW_VOL → HIGH_VOL) with confidence and metrics |
| **Backtesting** | Strategy/stock/date selection, equity curve, drawdown, trades, performance metrics |
| **Optimizer** | Parameter optimisation with heatmap and ranking |
| **Alerts Center** | Price/volume/RSI/MACD/breakout/regime/portfolio alerts; notification drawer with categories |
| **Reports** | CSV, Excel, JSON, HTML, PDF export for portfolio, backtests, scan, watchlist |
| **Cache & Performance** | Memory/disk cache inspection, metrics, provider health, WebSocket status, background refresh |
| **System Status** | Health monitoring: cache, providers, API latency, WebSocket, memory, CPU, request metrics |
| **Settings** | Theme, refresh interval, cache TTL, API provider priority, notifications, chart preferences |

### 📊 DataService — Centralised Data Layer

- **Singleton architecture**: One shared instance across all pages
- **HybridProvider chain**: API → CSV → cache → graceful empty (never crashes)
- **TieredCache**: In-memory (fast) + disk (persistent) with configurable TTL
- **WebSocket live feed**: Auto-reconnect, heartbeat, subscriber pattern, polling fallback
- **Rate limiter**: Token-bucket per provider, exponential backoff with jitter
- **Health monitor**: Auto-disable failing providers, re-enable after recovery
- **Request metrics**: Cache hit rate, P50/P95/P99 latency, per-operation breakdown
- **Background refresh**: Thread-safe, pause/resume, dynamic interval

### 💼 Portfolio & Trading

- **Portfolio Database**: SQLite-backed, persistent holdings, transactions, realised/unrealised P&L
- **Paper Trading Engine**: Market/limit/stop-loss/take-profit orders, partial fills, commission
- **Trade Journal**: Automatic logging, notes, lessons, tags, strategy, emotion tracking
- **Market Replay**: Play/pause/step/fast-forward/rewind historical sessions

### 🔒 Security & Deployment

- Security audit: removed traceback leaks, added CORS middleware, validated error handling
- Docker: multi-stage build, docker-compose, health checks, non-root user
- CI/CD: GitHub Actions test → build → release pipeline
- Backup/restore: Linux shell scripts and Windows batch, 30-day retention
- Launcher scripts: Linux (bash) and Windows (batch), modes: web/api/bot/all

---

## Quick Start

### Docker (recommended)

```bash
git clone https://github.com/hsnjwd/nepse-quant-engine.git
cd nepse-quant-engine
cp .env.example .env
docker compose up -d web
# Open http://localhost:8501
```

### Native Python

```bash
pip install -r requirements.txt
python -m streamlit run app.py
# Open http://localhost:8501
```

### API Backend

```bash
python -m uvicorn src.api.main:app --host 127.0.0.1 --port 8000
```

---

## Requirements

- Python 3.12+
- pip
- Optional: Docker 24+ (for containerised deployment)

---

## Test Suite

```bash
python -m pytest tests/ -q --tb=short
```

**Target:** 825+ tests passing, 22 skipped (external API-dependent tests)

---

## Key Improvements Since v0.6.5

1. **Code Quality**: Removed dead code, unused imports, `hasattr()` workarounds; improved typing, docstrings, logging
2. **Performance**: LRU-cached Plotly figures, `@st.cache_resource` for DataService, lazy imports, background preloading
3. **Stability**: Fixed DataService init order, singleton lifecycle, provider contracts, thread safety
4. **Security**: Removed `st.exception()` traceback leak, added CORS middleware with configurable origins
5. **Deployment**: Docker, docker-compose, CI/CD, backup/restore, launcher scripts, production config

---

## Changelog

See [CHANGELOG.md](CHANGELOG.md) for the complete release history.

---

## Known Issues

See [KNOWN_ISSUES.md](KNOWN_ISSUES.md).

---

## Roadmap

See [ROADMAP.md](ROADMAP.md) for planned features.
