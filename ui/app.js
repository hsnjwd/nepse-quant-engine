/* ============================================================
   NEPSE Quant Engine — Web UI Application
   Single-Page Application with 6 views
   ============================================================ */

const API_BASE = 'http://127.0.0.1:8000';

// ============================================================
// API Client
// ============================================================
const api = {
  async request(endpoint, options = {}) {
    const url = `${API_BASE}${endpoint}`;
    try {
      const res = await fetch(url, {
        headers: { 'Accept': 'application/json', ...options.headers },
        ...options,
      });
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(body.detail || `HTTP ${res.status}: ${res.statusText}`);
      }
      return await res.json();
    } catch (err) {
      if (err.message.includes('Failed to fetch') || err.message.includes('NetworkError')) {
        throw new Error('Cannot connect to NEPSE Quant Engine API. Is the server running?');
      }
      throw err;
    }
  },

  // Health
  health() { return this.request('/'); },

  // Analysis
  analyze(symbol) { return this.request(`/analyze/${encodeURIComponent(symbol)}`); },

  // Backtest
  backtest(symbol, commission = 0, slippage = 0) {
    return this.request(`/backtest/${encodeURIComponent(symbol)}?commission=${commission}&slippage=${slippage}`);
  },

  // Market Scanner
  marketSummary() { return this.request('/market/'); },
  top10() { return this.request('/market/top10'); },
  buyList() { return this.request('/market/buylist'); },
  sellList() { return this.request('/market/selllist'); },
  strongBuy() { return this.request('/market/strongbuy'); },

  // Watchlist
  getWatchlist() { return this.request('/watchlist'); },
  addToWatchlist(symbol) { return this.request(`/watchlist/add/${encodeURIComponent(symbol)}`, { method: 'POST' }); },
  removeFromWatchlist(symbol) { return this.request(`/watchlist/remove/${encodeURIComponent(symbol)}`, { method: 'DELETE' }); },
  scanWatchlist() { return this.request('/watchlist/scan'); },

  // Portfolio
  portfolio() { return this.request('/portfolio/'); },
};

// ============================================================
// State
// ============================================================
const state = {
  currentView: 'dashboard',
  connectionOnline: false,
  healthCheckInterval: null,
};

// ============================================================
// Utilities
// ============================================================
function $(sel, ctx = document) { return ctx.querySelector(sel); }
function $$(sel, ctx = document) { return [...ctx.querySelectorAll(sel)]; }

function escapeHtml(str) {
  if (str == null) return '';
  return String(str).replace(/[&<>"']/g, function (m) {
    if (m === '&') return '&amp;';
    if (m === '<') return '&lt;';
    if (m === '>') return '&gt;';
    if (m === '"') return '&quot;';
    return '&#39;';
  });
}

function formatNumber(n, decimals = 2) {
  if (n == null || isNaN(n)) return '—';
  return Number(n).toFixed(decimals);
}

function signalClass(signal) {
  if (!signal) return 'hold';
  return signal.toLowerCase();
}

function signalEmoji(signal) {
  const map = { BUY: '🟢', SELL: '🔴', HOLD: '🟡' };
  return map[signal] || '⚪';
}

function showToast(message, type = 'info') {
  const container = $('#toast-container');
  if (!container) return;
  const el = document.createElement('div');
  el.className = `toast ${type}`;
  el.textContent = message;
  container.appendChild(el);
  setTimeout(() => { el.style.opacity = '0'; el.style.transform = 'translateX(100px)'; el.style.transition = '0.3s ease'; setTimeout(() => el.remove(), 300); }, 3000);
}

function showError(containerId, message) {
  const container = $(`#${containerId}`);
  if (!container) return;
  container.innerHTML = `<div class="error-state">${escapeHtml(message)}</div>`;
}

function showLoading(containerId) {
  const container = $(`#${containerId}`);
  if (!container) return;
  container.innerHTML = `<div class="loading-spinner"><div class="spinner"></div></div>`;
}

function showEmpty(containerId, message = 'No data available') {
  const container = $(`#${containerId}`);
  if (!container) return;
  container.innerHTML = `<div class="empty-state"><div class="empty-icon">📭</div><p>${escapeHtml(message)}</p></div>`;
}

// ============================================================
// Gauge Renderer
// ============================================================
function renderGauge(containerId, value, max, label, color = '#3b82f6') {
  const container = $(`#${containerId}`);
  if (!container) return;

  const radius = 54;
  const circumference = 2 * Math.PI * radius;
  const pct = Math.max(0, Math.min(100, (value / max) * 100));
  const offset = circumference - (pct / 100) * circumference;

  container.innerHTML = `
    <div class="gauge">
      <svg width="140" height="140" viewBox="0 0 140 140">
        <circle class="bg-circle" cx="70" cy="70" r="${radius}" />
        <circle class="value-circle" cx="70" cy="70" r="${radius}"
          stroke="${color}"
          stroke-dasharray="${circumference}"
          stroke-dashoffset="${offset}" />
      </svg>
      <div class="gauge-center">
        <div class="gauge-value">${formatNumber(value, 0)}</div>
        <div class="gauge-label">${escapeHtml(label)}</div>
      </div>
    </div>
  `;
}

// ============================================================
// Navigation
// ============================================================
function navigateTo(view) {
  state.currentView = view;

  // Update nav items
  $$('.nav-item').forEach(el => el.classList.remove('active'));
  const navItem = $(`.nav-item[data-view="${view}"]`);
  if (navItem) navItem.classList.add('active');

  // Update views
  $$('.page-view').forEach(el => el.classList.remove('active'));
  const pageView = $(`#view-${view}`);
  if (pageView) pageView.classList.add('active');

  // Update header title
  const titles = {
    dashboard: ['Dashboard', 'Market Overview & Summary'],
    analysis: ['Stock Analysis', 'Technical Analysis & Trade Plans'],
    backtest: ['Backtest Engine', 'Historical Trade Simulation'],
    scanner: ['Market Scanner', 'Stock Screening & Rankings'],
    watchlist: ['Watchlist', 'Track Your Favorite Stocks'],
    portfolio: ['Portfolio', 'Holdings Analysis & P&L'],
  };
  const [title, subtitle] = titles[view] || ['', ''];
  const h2 = $('.main-header .page-title h2');
  const p = $('.main-header .page-title p');
  if (h2) h2.textContent = title;
  if (p) p.textContent = subtitle;

  // Load view data
  switch (view) {
    case 'dashboard': loadDashboard(); break;
    case 'scanner': loadScanner('top10'); break;
    case 'watchlist': loadWatchlist(); break;
    case 'portfolio': loadPortfolio(); break;
  }
}

// ============================================================
// Health Check
// ============================================================
let healthOnline = false;

async function checkHealth() {
  const dot = $('.connection-dot');
  const text = $('.connection-status .status-text');
  try {
    await api.health();
    healthOnline = true;
    if (dot) { dot.className = 'connection-dot online'; }
    if (text) text.textContent = 'Connected';
    state.connectionOnline = true;
    $$('.requires-connection').forEach(el => el.disabled = false);
  } catch {
    healthOnline = false;
    if (dot) { dot.className = 'connection-dot offline'; }
    if (text) text.textContent = 'Disconnected';
    state.connectionOnline = false;
    $$('.requires-connection').forEach(el => el.disabled = true);
  }
}

// ============================================================
// Dashboard View
// ============================================================
async function loadDashboard() {
  showLoading('dashboard-content');
  try {
    const summary = await api.marketSummary();
    renderDashboard(summary);
  } catch (err) {
    showError('dashboard-content', err.message);
  }
}

function renderDashboard(summary) {
  const container = $('#dashboard-content');
  if (!container) return;

  const total = summary.total || 0;
  const buys = summary.buy || 0;
  const holds = summary.hold || 0;
  const sells = summary.sell || 0;
  const skipped = summary.skipped || 0;
  const top10 = summary.top10 || [];

  let top10Rows = '';
  if (top10.length === 0) {
    top10Rows = '<tr><td colspan="6" style="text-align:center;color:var(--text-muted)">No ranked stocks available</td></tr>';
  } else {
    top10.forEach((stock, i) => {
      top10Rows += `
        <tr>
          <td><strong>#${i + 1}</strong></td>
          <td><strong>${escapeHtml(stock.symbol)}</strong></td>
          <td><span class="signal-badge ${signalClass(stock.signal)}">${escapeHtml(stock.signal)}</span></td>
          <td>${formatNumber(stock.score, 1)}</td>
          <td>${formatNumber(stock.confidence, 0)}%</td>
          <td>${formatNumber(stock.best_rr, 2)}</td>
        </tr>
      `;
    });
  }

  container.innerHTML = `
    <div class="stats-grid market-summary-cards">
      <div class="stat-card total">
        <div class="stat-label">Total Stocks</div>
        <div class="stat-value">${total}</div>
        <div class="stat-sub">${skipped > 0 ? `${skipped} skipped` : 'All analyzed'}</div>
      </div>
      <div class="stat-card buy">
        <div class="stat-label">BUY Signals</div>
        <div class="stat-value" style="color:var(--accent-green)">${buys}</div>
        <div class="stat-sub">${total > 0 ? ((buys / total) * 100).toFixed(1) : 0}% of market</div>
      </div>
      <div class="stat-card hold">
        <div class="stat-label">HOLD Signals</div>
        <div class="stat-value" style="color:var(--accent-yellow)">${holds}</div>
        <div class="stat-sub">${total > 0 ? ((holds / total) * 100).toFixed(1) : 0}% of market</div>
      </div>
      <div class="stat-card sell">
        <div class="stat-label">SELL Signals</div>
        <div class="stat-value" style="color:var(--accent-red)">${sells}</div>
        <div class="stat-sub">${total > 0 ? ((sells / total) * 100).toFixed(1) : 0}% of market</div>
      </div>
    </div>

    <div class="card">
      <div class="card-header">
        <h3>🏆 Top 10 Stocks</h3>
        <span class="card-action" onclick="navigateTo('scanner')">View All →</span>
      </div>
      <div class="table-container">
        <table>
          <thead>
            <tr>
              <th>Rank</th>
              <th>Symbol</th>
              <th>Signal</th>
              <th>Score</th>
              <th>Confidence</th>
              <th>R:R</th>
            </tr>
          </thead>
          <tbody>${top10Rows}</tbody>
        </table>
      </div>
    </div>
  `;
}

// ============================================================
// Analysis View
// ============================================================
async function runAnalysis() {
  const symbol = $('#analysis-symbol').value.trim().toUpperCase();
  if (!symbol) { showToast('Please enter a stock symbol', 'error'); return; }

  const container = $('#analysis-result');
  showLoading('analysis-result');

  try {
    const data = await api.analyze(symbol);
    renderAnalysis(data);
  } catch (err) {
    showError('analysis-result', err.message);
  }
}

function renderAnalysis(data) {
  const container = $('#analysis-result');
  if (!container) return;

  const signal = data.signal || 'HOLD';
  const sigClass = signalClass(signal);
  const score = data.score || 0;
  const confidence = data.confidence || 0;

  // Determine gauge color
  let gaugeColor = '#eab308'; // HOLD yellow
  if (signal === 'BUY') gaugeColor = '#22c55e';
  else if (signal === 'SELL') gaugeColor = '#ef4444';

  const gaugeId = 'analysis-gauge';
  container.innerHTML = `
    <div class="content-grid">
      <div>
        <div class="signal-display ${sigClass}">
          <span style="font-size:32px">${signalEmoji(signal)}</span>
          <div>
            <div class="signal-text">${escapeHtml(signal)}</div>
            <div class="signal-meta">${escapeHtml(data.symbol)} · ${escapeHtml(data.trend || '—')} · ${escapeHtml(data.trade_style || '—')}</div>
          </div>
        </div>

        <div class="content-grid three" style="margin-bottom:16px">
          <div class="metric-item">
            <div class="metric-value">${formatNumber(data.price)}</div>
            <div class="metric-label">Price (NPR)</div>
          </div>
          <div class="metric-item">
            <div class="metric-value">${formatNumber(confidence, 0)}%</div>
            <div class="metric-label">Confidence</div>
          </div>
          <div class="metric-item">
            <div class="metric-value">${formatNumber(score, 1)}</div>
            <div class="metric-label">Score /10</div>
          </div>
        </div>

        <div class="card" style="margin-bottom:12px">
          <div class="card-header"><h3>📋 Trade Plan</h3></div>
          <table>
            <tbody>
              <tr><td style="width:140px;color:var(--text-muted)">Entry Zone</td><td>${escapeHtml(data.entry_zone || '—')}</td></tr>
              <tr><td style="color:var(--text-muted)">Stop Loss</td><td style="color:var(--accent-red)">${formatNumber(data.stop_loss)}</td></tr>
              <tr><td style="color:var(--text-muted)">Target 1</td><td style="color:var(--accent-green)">${formatNumber(data.target1)}</td></tr>
              <tr><td style="color:var(--text-muted)">Target 2</td><td style="color:var(--accent-green)">${formatNumber(data.target2)}</td></tr>
              <tr><td style="color:var(--text-muted)">Target 3</td><td style="color:var(--accent-green)">${formatNumber(data.target3)}</td></tr>
              <tr><td style="color:var(--text-muted)">Risk Level</td><td>${escapeHtml(data.risk || '—')}</td></tr>
              <tr><td style="color:var(--text-muted)">Holding Period</td><td>${escapeHtml(data.holding_period || '—')}</td></tr>
            </tbody>
          </table>
        </div>
      </div>

      <div>
        <div class="card" style="margin-bottom:12px">
          <div class="card-header"><h3>🎯 Score Gauge</h3></div>
          <div id="${gaugeId}"></div>
        </div>

        <div class="card" style="margin-bottom:12px">
          <div class="card-header"><h3>📊 Technical Indicators</h3></div>
          <table>
            <tbody>
              <tr><td style="width:140px;color:var(--text-muted)">RSI</td><td>${formatNumber(data.rsi, 1)}</td></tr>
              <tr><td style="color:var(--text-muted)">MACD</td><td>${formatNumber(data.macd, 4)}</td></tr>
              <tr><td style="color:var(--text-muted)">ATR</td><td>${formatNumber(data.atr, 2)}</td></tr>
              <tr><td style="color:var(--text-muted)">Trend</td><td>${escapeHtml(data.trend || '—')}</td></tr>
              <tr><td style="color:var(--text-muted)">Volume</td><td>${escapeHtml(data.volume_signal || '—')} (${formatNumber(data.relative_volume, 2)}x)</td></tr>
              <tr><td style="color:var(--text-muted)">Pattern</td><td>${escapeHtml(data.pattern || '—')} (${escapeHtml(data.pattern_type || '—')})</td></tr>
            </tbody>
          </table>
        </div>

        <div class="card">
          <div class="card-header"><h3>⚡ Risk / Reward</h3></div>
          <table>
            <tbody>
              <tr><td style="width:140px;color:var(--text-muted)">Best R:R</td><td>${formatNumber(data.best_rr, 2)} <span style="color:var(--text-muted);font-size:12px">(${escapeHtml(data.rr_grade || '—')})</span></td></tr>
              <tr><td style="color:var(--text-muted)">T1 R:R</td><td>${formatNumber(data.rr_target1, 2)}</td></tr>
              <tr><td style="color:var(--text-muted)">T2 R:R</td><td>${formatNumber(data.rr_target2, 2)}</td></tr>
              <tr><td style="color:var(--text-muted)">T3 R:R</td><td>${formatNumber(data.rr_target3, 2)}</td></tr>
            </tbody>
          </table>
        </div>

        ${data.alerts && data.alerts.length ? `
        <div class="card" style="margin-top:12px">
          <div class="card-header"><h3>🔔 Alerts</h3></div>
          ${data.alerts.map(a => `<div class="alert-item"><span class="alert-icon">${a.priority >= 4 ? '🔴' : a.priority >= 3 ? '🟡' : '🔵'}</span>${escapeHtml(a.message)}</div>`).join('')}
        </div>` : ''}
      </div>
    </div>
  `;

  // Render gauge
  renderGauge(gaugeId, score, 10, 'Score /10', gaugeColor);
}

// Enter key handler for analysis
$('#analysis-symbol')?.addEventListener('keydown', (e) => {
  if (e.key === 'Enter') runAnalysis();
});

// ============================================================
// Backtest View
// ============================================================
async function runBacktest() {
  const symbol = $('#backtest-symbol').value.trim().toUpperCase();
  const commission = parseFloat($('#backtest-commission').value) || 0;
  const slippage = parseFloat($('#backtest-slippage').value) || 0;

  if (!symbol) { showToast('Please enter a stock symbol', 'error'); return; }

  const container = $('#backtest-result');
  showLoading('backtest-result');

  try {
    const data = await api.backtest(symbol, commission, slippage);
    renderBacktest(data);
  } catch (err) {
    showError('backtest-result', err.message);
  }
}

function renderBacktest(data) {
  const container = $('#backtest-result');
  if (!container) return;

  const metrics = data.metrics || {};
  const report = data.report || {};
  const trades = data.trades || [];

  // Metrics
  const isGood = metrics.win_rate >= 50;

  let tradeRows = '';
  if (trades.length === 0) {
    tradeRows = '<tr><td colspan="7" style="text-align:center;color:var(--text-muted)">No trades executed during backtest period</td></tr>';
  } else {
    trades.forEach((t, i) => {
      const isWin = (t.return_pct || 0) > 0;
      tradeRows += `
        <tr>
          <td>#${i + 1}</td>
          <td>${escapeHtml(t.date || '—')}</td>
          <td style="color:${isWin ? 'var(--accent-green)' : 'var(--accent-red)'}">${formatNumber(t.entry_price)}</td>
          <td style="color:${isWin ? 'var(--accent-green)' : 'var(--accent-red)'}">${formatNumber(t.exit_price)}</td>
          <td>${t.shares || 1}</td>
          <td style="color:${isWin ? 'var(--accent-green)' : 'var(--accent-red)'}">${t.return_pct >= 0 ? '+' : ''}${formatNumber(t.return_pct, 1)}%</td>
          <td><span class="signal-badge ${t.exit_reason === 'TARGET' ? 'buy' : t.exit_reason === 'STOP_LOSS' ? 'sell' : 'hold'}">${escapeHtml(t.exit_reason)}</span></td>
        </tr>
      `;
    });
  }

  container.innerHTML = `
    <div class="stats-grid backtest-metrics">
      <div class="stat-card total">
        <div class="stat-label">Total Trades</div>
        <div class="stat-value">${metrics.total_trades || 0}</div>
      </div>
      <div class="stat-card buy">
        <div class="stat-label">Win Rate</div>
        <div class="stat-value" style="color:${isGood ? 'var(--accent-green)' : 'var(--accent-red)'}">${formatNumber(metrics.win_rate, 1)}%</div>
        <div class="stat-sub">${metrics.winning_trades || 0}W / ${metrics.losing_trades || 0}L</div>
      </div>
      <div class="stat-card ${metrics.profit_factor >= 1 ? 'buy' : 'sell'}">
        <div class="stat-label">Profit Factor</div>
        <div class="stat-value">${formatNumber(metrics.profit_factor, 2)}</div>
      </div>
      <div class="stat-card">
        <div class="stat-label">Expectancy</div>
        <div class="stat-value" style="color:${(metrics.expectancy || 0) >= 0 ? 'var(--accent-green)' : 'var(--accent-red)'}">${metrics.expectancy >= 0 ? '+' : ''}${formatNumber(metrics.expectancy, 2)}%</div>
      </div>
    </div>

    <div class="content-grid" style="margin-bottom:20px">
      <div class="card">
        <div class="card-header"><h3>📊 Performance Details</h3></div>
        <div class="metrics-grid">
          <div class="metric-item">
            <div class="metric-value" style="color:var(--accent-green)">${formatNumber(metrics.average_win, 2)}%</div>
            <div class="metric-label">Avg Win</div>
          </div>
          <div class="metric-item">
            <div class="metric-value" style="color:var(--accent-red)">${formatNumber(metrics.average_loss, 2)}%</div>
            <div class="metric-label">Avg Loss</div>
          </div>
          <div class="metric-item">
            <div class="metric-value">${metrics.winning_trades || 0}</div>
            <div class="metric-label">Wins</div>
          </div>
          <div class="metric-item">
            <div class="metric-value">${metrics.losing_trades || 0}</div>
            <div class="metric-label">Losses</div>
          </div>
          <div class="metric-item">
            <div class="metric-value">${metrics.breakeven_trades || 0}</div>
            <div class="metric-label">Breakeven</div>
          </div>
        </div>
      </div>

      <div class="card">
        <div class="card-header"><h3>📄 Summary Report</h3></div>
        <div class="report-text">${escapeHtml(report.summary || 'No report generated')}</div>
      </div>
    </div>

    <div class="card">
      <div class="card-header">
        <h3>📝 Trade History (${trades.length})</h3>
      </div>
      <div class="table-container">
        <table>
          <thead>
            <tr>
              <th>#</th>
              <th>Date</th>
              <th>Entry</th>
              <th>Exit</th>
              <th>Shares</th>
              <th>Return</th>
              <th>Exit Reason</th>
            </tr>
          </thead>
          <tbody>${tradeRows}</tbody>
        </table>
      </div>
    </div>
  `;
}

// Enter key for backtest
$('#backtest-symbol')?.addEventListener('keydown', (e) => {
  if (e.key === 'Enter') runBacktest();
});

// ============================================================
// Scanner View
// ============================================================
let scannerTab = 'top10';

function switchScannerTab(tab) {
  scannerTab = tab;
  $$('#scanner-tabs .tab').forEach(el => el.classList.remove('active'));
  const tabEl = $(`#scanner-tabs .tab[data-tab="${tab}"]`);
  if (tabEl) tabEl.classList.add('active');
  loadScanner(tab);
}

async function loadScanner(tab) {
  const container = $('#scanner-content');
  showLoading('scanner-content');
  try {
    let data;
    switch (tab) {
      case 'top10': data = await api.top10(); break;
      case 'buylist': data = await api.buyList(); break;
      case 'selllist': data = await api.sellList(); break;
      case 'strongbuy': data = await api.strongBuy(); break;
      default: data = await api.top10();
    }
    renderScannerList(container, data, tab);
  } catch (err) {
    showError('scanner-content', err.message);
  }
}

function renderScannerList(container, data, tab) {
  if (!data || (Array.isArray(data) && data.length === 0)) {
    showEmpty('scanner-content', 'No stocks found for this category');
    return;
  }

  const stocks = Array.isArray(data) ? data : [];
  let rows = '';
  stocks.forEach((stock, i) => {
    const hasPositionSizing = stock.position_size;
    rows += `
      <tr onclick="quickAnalyze('${escapeHtml(stock.symbol)}')" style="cursor:pointer">
        <td><strong>#${i + 1}</strong></td>
        <td><strong>${escapeHtml(stock.symbol)}</strong></td>
        <td><span class="signal-badge ${signalClass(stock.signal)}">${escapeHtml(stock.signal)}</span></td>
        <td>${formatNumber(stock.score, 1)}</td>
        <td>${formatNumber(stock.confidence, 0)}%</td>
        <td>${formatNumber(stock.best_rr, 2)}</td>
        <td>${escapeHtml(stock.trend || '—')}</td>
      </tr>
    `;
  });

  container.innerHTML = `
    <div class="card">
      <div class="card-header">
        <h3>${tab === 'top10' ? '🏆 Top 10' : tab === 'buylist' ? '🟢 Buy List' : tab === 'selllist' ? '🔴 Sell List' : '💎 Strong Buy'} <span style="color:var(--text-muted);font-weight:400;font-size:13px">(${stocks.length} stocks)</span></h3>
        <span style="font-size:12px;color:var(--text-muted)">Click a row to analyze</span>
      </div>
      <div class="table-container">
        <table>
          <thead>
            <tr>
              <th>Rank</th>
              <th>Symbol</th>
              <th>Signal</th>
              <th>Score</th>
              <th>Confidence</th>
              <th>R:R</th>
              <th>Trend</th>
            </tr>
          </thead>
          <tbody>${rows}</tbody>
        </table>
      </div>
    </div>
  `;
}

function quickAnalyze(symbol) {
  $('#analysis-symbol').value = symbol;
  navigateTo('analysis');
  runAnalysis();
}

// ============================================================
// Watchlist View
// ============================================================
async function loadWatchlist() {
  const container = $('#watchlist-content');
  showLoading('watchlist-content');
  try {
    const data = await api.getWatchlist();
    renderWatchlist(data);
  } catch (err) {
    showError('watchlist-content', err.message);
  }
}

function renderWatchlist(data) {
  const container = $('#watchlist-content');
  if (!container) return;

  const symbols = data && typeof data === 'object' ? Object.keys(data) : [];
  let chips = '';
  if (symbols.length === 0) {
    chips = '<div style="color:var(--text-muted);padding:8px 0">Your watchlist is empty. Add stocks above.</div>';
  } else {
    symbols.forEach(sym => {
      chips += `
        <div class="watchlist-chip">
          <span onclick="quickAnalyze('${escapeHtml(sym)}')" style="cursor:pointer">${escapeHtml(sym)}</span>
          <span class="remove-chip" onclick="removeFromWatchlist('${escapeHtml(sym)}')">×</span>
        </div>
      `;
    });
  }

  container.innerHTML = `
    <div style="margin-bottom:20px">
      <div class="card" style="margin-bottom:16px">
        <div class="card-header"><h3>✏️ Manage Watchlist</h3></div>
        <div class="input-group">
          <input type="text" id="watchlist-add-input" placeholder="Enter symbol (e.g., NABIL)" style="flex:1">
          <button class="btn btn-primary requires-connection" onclick="addToWatchlist()">Add</button>
        </div>
      </div>

      <div class="card" style="margin-bottom:16px">
        <div class="card-header">
          <h3>📋 Your Symbols (${symbols.length})</h3>
          <button class="btn btn-outline btn-sm requires-connection" onclick="scanWatchlistView()">🔄 Scan All</button>
        </div>
        <div class="watchlist-items">${chips}</div>
      </div>
    </div>

    <div id="watchlist-scan-results"></div>
  `;

  // Enter key handler
  $('#watchlist-add-input')?.addEventListener('keydown', (e) => {
    if (e.key === 'Enter') addToWatchlist();
  });
}

async function addToWatchlist() {
  const input = $('#watchlist-add-input');
  const symbol = input?.value.trim().toUpperCase();
  if (!symbol) { showToast('Please enter a symbol', 'error'); return; }
  try {
    await api.addToWatchlist(symbol);
    showToast(`${symbol} added to watchlist`, 'success');
    input.value = '';
    loadWatchlist();
  } catch (err) {
    showToast(err.message, 'error');
  }
}

async function removeFromWatchlist(symbol) {
  try {
    await api.removeFromWatchlist(symbol);
    showToast(`${symbol} removed from watchlist`, 'info');
    loadWatchlist();
  } catch (err) {
    showToast(err.message, 'error');
  }
}

async function scanWatchlistView() {
  const container = $('#watchlist-scan-results');
  showLoading('watchlist-scan-results');
  try {
    const data = await api.scanWatchlist();
    renderWatchlistScan(data);
  } catch (err) {
    showError('watchlist-scan-results', err.message);
  }
}

function renderWatchlistScan(data) {
  const container = $('#watchlist-scan-results');
  if (!container) return;

  const results = data.results || [];
  const alerts = data.alerts || [];

  let rows = '';
  if (results.length === 0) {
    rows = '<tr><td colspan="5" style="text-align:center;color:var(--text-muted)">No results from scan</td></tr>';
  } else {
    results.forEach(r => {
      if (r.error) {
        rows += `<tr><td><strong>${escapeHtml(r.symbol)}</strong></td><td colspan="4" style="color:var(--accent-red)">⚠ ${escapeHtml(r.error)}</td></tr>`;
      } else {
        rows += `
          <tr>
            <td><strong>${escapeHtml(r.symbol)}</strong></td>
            <td><span class="signal-badge ${signalClass(r.signal)}">${escapeHtml(r.signal)}</span></td>
            <td>${formatNumber(r.score, 1)}</td>
            <td>${formatNumber(r.confidence, 0)}%</td>
            <td>${escapeHtml(r.trend || '—')}</td>
          </tr>
        `;
      }
    });
  }

  let alertsHtml = '';
  if (alerts.length > 0) {
    alertsHtml = `
      <div class="card" style="margin-bottom:16px">
        <div class="card-header"><h3>🔔 New Alerts (${alerts.length})</h3></div>
        ${alerts.flatMap(a => a.alerts.map(alert => `
          <div class="alert-item">
            <span class="alert-icon">${alert.priority >= 4 ? '🔴' : alert.priority >= 3 ? '🟡' : '🔵'}</span>
            <strong>${escapeHtml(a.symbol)}:</strong> ${escapeHtml(alert.message)}
          </div>
        `)).join('')}
      </div>
    `;
  }

  container.innerHTML = `
    ${alertsHtml}
    <div class="card">
      <div class="card-header">
        <h3>📊 Scan Results (${results.length} stocks)</h3>
      </div>
      <div class="table-container">
        <table>
          <thead>
            <tr>
              <th>Symbol</th>
              <th>Signal</th>
              <th>Score</th>
              <th>Confidence</th>
              <th>Trend</th>
            </tr>
          </thead>
          <tbody>${rows}</tbody>
        </table>
      </div>
    </div>
  `;
}

// ============================================================
// Portfolio View
// ============================================================
async function loadPortfolio() {
  const container = $('#portfolio-content');
  showLoading('portfolio-content');
  try {
    const data = await api.portfolio();
    renderPortfolio(data);
  } catch (err) {
    showError('portfolio-content', err.message);
  }
}

function renderPortfolio(data) {
  const container = $('#portfolio-content');
  if (!container) return;

  const pnl = data.portfolio_pnl || 0;
  const pnlPct = data.portfolio_return_pct || 0;
  const isPositive = pnl >= 0;

  const holdings = data.holdings || [];
  let rows = '';
  if (holdings.length === 0) {
    rows = '<tr><td colspan="8" style="text-align:center;color:var(--text-muted)">No holdings in portfolio</td></tr>';
  } else {
    holdings.forEach(h => {
      const hPnl = h.pnl || 0;
      const hPos = hPnl >= 0;
      rows += `
        <tr>
          <td><strong>${escapeHtml(h.symbol)}</strong></td>
          <td>${h.quantity || 0}</td>
          <td>${formatNumber(h.average_price)}</td>
          <td>${formatNumber(h.ltp)}</td>
          <td style="color:${hPos ? 'var(--accent-green)' : 'var(--accent-red)'}">${hPos ? '+' : ''}${formatNumber(hPnl)}</td>
          <td style="color:${hPos ? 'var(--accent-green)' : 'var(--accent-red)'}">${hPos ? '+' : ''}${formatNumber(h.pnl_pct, 1)}%</td>
          <td><span class="signal-badge ${signalClass(h.decision)}">${escapeHtml(h.decision || '—')}</span></td>
          <td style="max-width:160px;overflow:hidden;text-overflow:ellipsis;color:var(--text-muted);font-size:12px">${h.advisor ? escapeHtml(h.advisor.recommendation || '—') : '—'}</td>
        </tr>
      `;
    });
  }

  container.innerHTML = `
    <div class="stats-grid portfolio-summary">
      <div class="stat-card total">
        <div class="stat-label">Total Investment</div>
        <div class="stat-value">NPR ${formatNumber(data.portfolio_cost)}</div>
      </div>
      <div class="stat-card total">
        <div class="stat-label">Current Value</div>
        <div class="stat-value">NPR ${formatNumber(data.portfolio_value)}</div>
      </div>
      <div class="stat-card ${isPositive ? 'pnl-positive' : 'pnl-negative'}">
        <div class="stat-label">Total P&L</div>
        <div class="stat-value">${isPositive ? '+' : ''}NPR ${formatNumber(pnl)}</div>
      </div>
      <div class="stat-card ${isPositive ? 'pnl-positive' : 'pnl-negative'}">
        <div class="stat-label">Return %</div>
        <div class="stat-value">${isPositive ? '+' : ''}${formatNumber(pnlPct, 1)}%</div>
        <div class="stat-sub">${isPositive ? '📈 Profit' : '📉 Loss'}</div>
      </div>
    </div>

    <div class="card">
      <div class="card-header">
        <h3>📋 Holdings (${holdings.length})</h3>
        <span style="font-size:12px;color:var(--text-muted)">Click symbol to analyze</span>
      </div>
      <div class="table-container">
        <table>
          <thead>
            <tr>
              <th>Symbol</th>
              <th>Qty</th>
              <th>Avg Cost</th>
              <th>LTP</th>
              <th>P&L</th>
              <th>P&L %</th>
              <th>Decision</th>
              <th>Advice</th>
            </tr>
          </thead>
          <tbody>${rows}</tbody>
        </table>
      </div>
    </div>
  `;
}

// ============================================================
// Initialization
// ============================================================
function init() {
  // Navigation setup
  $$('.nav-item').forEach(el => {
    el.addEventListener('click', () => navigateTo(el.dataset.view));
  });

  // Health check
  checkHealth();
  setInterval(checkHealth, 15000);

  // Load initial view
  navigateTo('dashboard');
}

document.addEventListener('DOMContentLoaded', init);
