# Screenshots & Demo Assets

This document lists the recommended screenshots and demo GIFs for the NEPSE Quant Engine v1.0.0-rc1 release.

---

## Screenshots to Capture

### 1. Dashboard (`screenshots/dashboard.png`)
- Full-page screenshot showing:
  - NEPSE index + daily change + volume + turnover
  - Market status (Open/Closed)
  - Top gainers/losers/turnover lists
  - Market breadth (advances/declines/unchanged)
  - Portfolio value and P&L summary
  - Recent watchlist signals
  - Recent alerts

### 2. Market Scanner (`screenshots/scanner.png`)
- Scanner page showing:
  - Filter panel (signal, score, confidence, RSI, volume, regime)
  - Results table (symbol, price, signal, score, confidence, RSI, MACD, regime)
  - Export and saved presets dropdown

### 3. Stock Analysis (`screenshots/stock_analysis.png`)
- Split view showing:
  - Search input with selected stock (e.g., NABIL)
  - Signal, confidence, recommendation badges
  - Candlestick chart with volume
  - RSI and MACD subplots

### 4. Advanced Charts (`screenshots/advanced_charts.png`)
- Real-time chart with:
  - Live candlestick updates
  - Volume bars
  - Moving averages (20/50/180)
  - RSI, MACD, Bollinger Bands indicators
  - "LIVE" status indicator

### 5. Watchlist (`screenshots/watchlist.png`)
- Table showing:
  - Symbol, price, signal, confidence, last update
  - Add/remove/refresh/scan buttons

### 6. Portfolio (`screenshots/portfolio.png`)
- Showing:
  - Summary cards (cash, invested, current value, profit, return %)
  - Holdings table
  - Allocation pie chart
  - Performance line chart

### 7. Portfolio Analytics (`screenshots/portfolio_analytics.png`)
- Comprehensive analytics with:
  - Daily/weekly/monthly/YTD returns
  - Sharpe/Sortino/Calmar ratios
  - Max drawdown, win rate, profit factor
  - Rolling returns/Sharpe/volatility charts

### 8. Paper Trading (`screenshots/paper_trading.png`)
- Showing:
  - Buy/sell form with order type selector
  - Open positions table
  - Pending orders
  - Trade history
  - Balance and P&L summary

### 9. Market Regime (`screenshots/market_regime.png`)
- Large regime indicator card showing:
  - Current regime name with colour
  - Confidence percentage
  - Reasons list
  - Metrics grid (ADX, ATR, RSI, MACD, trend, drawdown, volume)

### 10. Backtesting (`screenshots/backtesting.png`)
- Showing:
  - Strategy selector, stock selector, date range, parameters
  - Equity curve chart
  - Drawdown chart
  - Trades table
  - Performance metrics summary

### 11. Alerts Center (`screenshots/alerts_center.png`)
- Notification drawer showing:
  - Category filters
  - Alert list with priority colours
  - Unread badge on sidebar
  - Dismiss all button

### 12. Cache & Performance (`screenshots/cache_debug.png`)
- Developer dashboard with tabs:
  - Cache (memory/disk entries, TTL)
  - Metrics (requests, hit rate, latency graph)
  - Providers (health, latency comparison)
  - WebSocket (status, reconnects)
  - Background refresh (status, interval)
  - Rate Limiter (tokens, cooldowns)
  - Health (provider table)

### 13. System Status (`screenshots/system_status.png`)
- Health overview showing:
  - Cache status
  - Provider health
  - API latency
  - WebSocket status
  - Background refresh status
  - Memory/CPU usage
  - Request metrics

### 14. Settings (`screenshots/settings.png`)
- Settings page showing:
  - Theme selector (Dark/Light/TradingView/Bloomberg)
  - Refresh interval
  - Cache TTL
  - API provider priority
  - Notification preferences
  - Chart preferences

---

## Demo GIFs to Capture

### 1. Live Data Refresh (`demos/live_refresh.gif`)
- Show the dashboard auto-refreshing every 30 seconds:
  - Prices updating
  - Top gainers/losers rotating
  - Market status changing

### 2. Scanner Filters (`demos/scanner_filters.gif`)
- Apply different filters and watch the results table update:
  - Change signal filter (BUY → SELL → HOLD)
  - Adjust RSI range slider
  - Sort by different columns
  - Export results

### 3. Stock Search & Analysis (`demos/stock_search.gif`)
- Type a stock symbol (e.g., "NABIL") and watch:
  - Price data load
  - Indicators calculate
  - Charts render
  - Signal and recommendation update

### 4. Paper Trading (`demos/paper_trading.gif`)
- Place a market buy order and watch:
  - Position appear in open positions
  - Balance update
  - Trade recorded in history
  - P&L update as price changes

### 5. Market Replay (`demos/market_replay.gif`)
- Start replay mode and:
  - Candles updating in real-time
  - Play/pause/step controls in action
  - Speed slider changing playback rate

### 6. Theme Switching (`demos/theme_switch.gif`)
- Switch between Dark, Light, TradingView, and Bloomberg themes:
  - All pages updating colours instantly
  - Charts adapting colour scheme

### 7. Keyboard Shortcuts (`demos/keyboard_shortcuts.gif`)
- Press different keyboard shortcuts:
  - Ctrl+D → Dashboard
  - Ctrl+P → Portfolio
  - Ctrl+S → Scanner
  - Ctrl+B → Backtest
  - ? → Help overlay

---

## Asset Requirements

| Asset | Resolution | Format | Size Limit |
|-------|-----------|--------|------------|
| Screenshots | 1920×1080 or 2560×1440 | PNG | < 2 MB each |
| Demo GIFs | 1280×720 | GIF or WebP | < 10 MB each |
| Banner image | 1200×600 | PNG | < 500 KB |

---

## Tools for Capturing

- **Screenshots**: Use your browser's developer tools or screenshot tool (Windows Snipping Tool, macOS Cmd+Shift+4)
- **GIFs**: 
  - [ScreenToGif](https://www.screentogif.com/) (Windows, free)
  - [Kap](https://getkap.co/) (macOS, free)
  - [OBS Studio](https://obsproject.com/) (Cross-platform, free) + FFmpeg to convert to GIF/WebP
- **Annotations**: Add arrows, labels, and highlights using:
  - [CleanShot X](https://cleanshot.com/) (macOS)
  - [ShareX](https://getsharex.com/) (Windows, free)
  - [Figma](https://figma.com/) or [Photopea](https://www.photopea.com/) (free online editors)

---

## README Badges

Add these badges to `README.md` once screenshots are uploaded:

```markdown
![Dashboard](screenshots/dashboard.png)
![Scanner](screenshots/scanner.png)
![Stock Analysis](screenshots/stock_analysis.png)
![Portfolio](screenshots/portfolio.png)
```
