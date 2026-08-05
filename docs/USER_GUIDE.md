# NEPSE Quant Engine — User Guide

## Overview

The NEPSE Quant Engine is a professional-grade quantitative trading platform for the Nepal Stock Exchange (NEPSE). It provides real-time market data, technical analysis, portfolio management, paper trading, backtesting, and strategy optimization through a responsive Streamlit web interface.

## Getting Started

### Launching the App

```bash
python -m streamlit run app.py
```

The app opens at [http://localhost:8501](http://localhost:8501).

### Navigation

The sidebar provides navigation to all pages:

| Section | Pages |
|---|---|
| **Main** | Dashboard |
| **Market** | Scanner, Analyze Stock, Advanced Charts, Market Regime |
| **Portfolio** | Portfolio, Portfolio Analytics, Paper Trading |
| **Strategies** | Backtest, Optimizer |
| **Monitoring** | Watchlist, Alerts (Notification Center), Reports |
| **System** | Settings, Cache & Performance, System Status |

### Auto-Refresh

Select an auto-refresh interval from the sidebar dropdown:
- Manual, 10 sec, 30 sec, 1 min, 5 min

## Features

### Dashboard
Live market snapshot showing NEPSE index, advances/declines, volume, turnover, top gainers/losers, market breadth, portfolio summary, recent trades, and alerts.

### Scanner
Scan all NEPSE stocks with:
- Filters: Signal, Regime, Score range, RSI range, Price range, Confidence
- Saved filter presets (save/load/delete)
- Pinned symbols (sorted to top)
- Favourite scans
- Export CSV, Import/Reset filters

### Analyze Stock
Deep dive into any symbol with:
- Live price, signal, confidence, RSI, MACD, ATR
- Pattern detection, volume analysis, support/resistance
- Charts: Candlestick, Volume, RSI, MACD, Bollinger Bands

### Advanced Charts
Professional-grade Plotly charts with:
- Candlestick, Volume, RSI, MACD, Moving Averages, Bollinger Bands
- Live WebSocket updates (green LIVE badge when connected)
- Disconnected fallback to polling
- Support/resistance levels, zoom, crosshair
- Indicator summary table
- CSV download

### Portfolio
- Holdings table with live pricing
- Allocation pie chart
- P&L by holding
- Persistent SQLite database

### Portfolio Analytics
Advanced metrics: Sharpe, Sortino, Calmar, Max Drawdown, CAGR, Alpha, Beta, Win Rate, Profit Factor, Exposure, Information Ratio, Treynor Ratio. Rolling returns/Sharpe/volatility charts.

### Paper Trading
Simulate trades with virtual capital (₹1,000,000 default):
- Market orders, Limit orders, Stop-Loss, Take-Profit
- Open positions with live P&L
- Trade history
- Balance management

### Market Regime
Detects current market regime from NEPSE index history:
- PANIC, OVERHEATED, BULL, BEAR, RECOVERY, DISTRIBUTION, ACCUMULATION, SIDEWAYS, LOW_VOLATILITY, HIGH_VOLATILITY

### Backtest
Test strategies on historical data:
- 5 strategies: Momentum, Mean Reversion, Breakout, MA Crossover, Volatility
- Multi-stock selection
- Date range, capital, commission, slippage
- Best performer ranking
- Equity curve, drawdown, trades export
- Run all stocks for comparison

### Watchlist
Monitor selected symbols with live prices, signals, confidence scores, and automatic scanning.

### Notification Center (Alerts)
Unified notification system with:
- 8 categories: Portfolio, Scanner, Price, Market, WebSocket, System, Background, Trade
- Priority levels: Info, Success, Warning, Critical
- Read/unread, dismiss, dismiss all, clear all
- Search and filter by category
- Persistent history
- Sidebar unread badge

### Reports
Generate and export:
- Portfolio Report (holdings, P&L)
- Backtest Report (metrics, trades)
- Market Report (scan results, top movers)
- Export formats: CSV, JSON, HTML, Excel, PDF

### Settings
Configure:
- **Appearance**: Dark/Light theme
- **Trading**: Commission, risk %, default capital, slippage
- **Data**: Cache TTL, API provider priority, clear cache
- **Display**: Auto-refresh rate, currency format
- **Notifications**: Enable/disable alert types, priority level
- **Charts**: Default type, color scheme, period, indicators visibility

### Cache & Performance
Monitor DataService performance:
- Cache hit rate gauge
- Request metrics (total, hits, misses, API calls)
- Provider health (latency, success rate)
- WebSocket status
- Background refresh controls
- Memory/disk cache inspection

### System Status
Application health dashboard:
- **Overview**: Total requests, cache hit rate, WebSocket status, background refresh, API failures, latency (avg/P95/P99)
- **Cache**: Memory/disk keys, clear/refresh, key inspector
- **Providers**: Health snapshot, enable/disable, reset
- **WebSocket**: Status, reconnects, messages, uptime, start/stop
- **Background**: Start/pause/resume/stop, interval control
- **Metrics**: Request metrics with cache hit rate gauge chart
- **Config**: All user settings, reload/reset

## Keyboard Shortcuts

| Shortcut | Action |
|---|---|
| `Ctrl+K` | Focus symbol search |
| `Ctrl+P` | Navigate to Portfolio |
| `Ctrl+S` | Navigate to Scanner |
| `Ctrl+B` | Navigate to Backtest |
| `Ctrl+D` | Navigate to Dashboard |
| `Ctrl+H` | Navigate to System Status |
| `?` | Toggle keyboard shortcuts help |

## Export Formats

| Format | Portfolio | Backtest | Market Scan | Journal |
|---|---|---|---|---|
| CSV | ✅ | ✅ | ✅ | ✅ |
| Excel | ✅ | — | — | — |
| JSON | ✅ | ✅ | ✅ | ✅ |
| HTML | ✅ | ✅ | ✅ | ✅ |
| PDF | ✅ | ✅ | ✅ | — |

## Troubleshooting

### No live data
- Check that the market is open (Open/Closed indicator in sidebar)
- Verify internet connection
- Check System Status page for provider health
- Clear cache from Settings > Data

### Charts not loading
- Ensure Plotly is installed: `pip install plotly`
- Try a different symbol
- Reduce the date range

### PDF export not working
- Install ReportLab: `pip install reportlab`
- Or WeasyPrint: `pip install weasyprint`
- Falls back to HTML export
