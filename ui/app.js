/* ============================================================
   NEPSE Quant Engine — Web UI Application
   Single-Page Application with 6 views, regime detection,
   backtest charts, and portfolio heatmaps
   ============================================================ */

const API_BASE = 'http://127.0.0.1:8000';

// Global Chart.js instances for cleanup
let equityChartInstance = null;
let returnsChartInstance = null;
let riskChartInstance = null;
let pathsChartInstance = null;
let distributionChartInstance = null;
let varChartInstance = null;

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

  // Regime Detection
  regime(symbol) {
    const q = symbol ? `?symbol=${encodeURIComponent(symbol)}` : '';
    return this.request(`/regime/${q}`);
  },

  // Monte Carlo Simulation
  simulate(symbol, simulations = 1000, method = 'bootstrap', confidence = 0.95) {
    return this.request(`/simulation/${encodeURIComponent(symbol)}?simulations=${simulations}&method=${method}&confidence_level=${confidence}`);
  },
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

function formatPct(n, decimals = 2) {
  if (n == null || isNaN(n)) return '—';
  const v = Number(n);
  return (v >= 0 ? '+' : '') + v.toFixed(decimals) + '%';
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
    <div class="gauge-container">
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
    </div>
  `;
}

// ============================================================
// Regime helpers
// ============================================================

const REGIME_META = {
  BULL:           { icon: '🚀', color: '#22c55e', bg: 'rgba(34,197,94,0.12)', label: 'Bull Market' },
  BEAR:           { icon: '🐻', color: '#ef4444', bg: 'rgba(239,68,68,0.12)', label: 'Bear Market' },
  SIDEWAYS:       { icon: '➡️', color: '#eab308', bg: 'rgba(234,179,8,0.12)', label: 'Sideways' },
  HIGH_VOLATILITY:{ icon: '⚡', color: '#f97316', bg: 'rgba(249,115,22,0.12)', label: 'High Volatility' },
  LOW_VOLATILITY: { icon: '🌊', color: '#06b6d4', bg: 'rgba(6,182,212,0.12)', label: 'Low Volatility' },
  ACCUMULATION:   { icon: '📥', color: '#a855f7', bg: 'rgba(168,85,247,0.12)', label: 'Accumulation' },
  DISTRIBUTION:   { icon: '📤', color: '#ec4899', bg: 'rgba(236,72,153,0.12)', label: 'Distribution' },
  RECOVERY:       { icon: '🔄', color: '#22d3ee', bg: 'rgba(34,211,238,0.12)', label: 'Recovery' },
  PANIC:          { icon: '🔴', color: '#dc2626', bg: 'rgba(220,38,38,0.2)', label: 'Panic Selling' },
  OVERHEATED:     { icon: '🔥', color: '#f97316', bg: 'rgba(249,115,22,0.15)', label: 'Overheated' },
};

function getRegimeMeta(regime) {
  return REGIME_META[regime] || { icon: '❓', color: '#5a6378', bg: 'rgba(90,99,120,0.12)', label: 'Unknown' };
}

function applyRegimeStyles(el, regime) {
  if (!el) return;
  // Remove all regime classes
  Object.keys(REGIME_META).forEach(r => el.classList.remove(`regime-${r}`));
  el.classList.remove('regime-UNKNOWN');
  if (regime && REGIME_META[regime]) {
    el.classList.add(`regime-${regime}`);
  } else {
    el.classList.add('regime-UNKNOWN');
  }
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
    simulator: ['Monte Carlo Simulator', 'VaR/CVaR Analysis & Portfolio Paths'],
    portfolio: ['Portfolio', 'Holdings Analysis & P&L'],
    upload: ['Upload OHLCV', 'Import Individual Stock Price History'],
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
    case 'upload': renderUpload(); break;
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
// Regime Detection (loaded on every dashboard visit)
// ============================================================

let lastRegimeData = null;

async function loadRegime() {
  const pill = $('#regime-pill');
  const hero = $('#regime-hero');
  if (!pill || !hero) return;

  try {
    const data = await api.regime();
    lastRegimeData = data;
    renderRegimePill(data, pill);
    renderRegimeHero(data, hero);
  } catch (err) {
    pill.classList.add('hidden');
    hero.classList.add('hidden');
  }
}

function renderRegimePill(data, pill) {
  const meta = getRegimeMeta(data.regime);
  pill.classList.remove('hidden');
  applyRegimeStyles(pill, data.regime);

  const dot = pill.querySelector('.regime-dot');
  const label = pill.querySelector('.regime-label');
  if (dot) dot.style.background = meta.color;
  if (label) label.textContent = `${meta.icon} ${meta.label} (${formatNumber(data.confidence, 0)}%)`;
}

function renderRegimeHero(data, hero) {
  const meta = getRegimeMeta(data.regime);
  hero.classList.remove('hidden');
  applyRegimeStyles(hero, data.regime);

  hero.querySelector('.regime-hero-icon').textContent = meta.icon;
  hero.querySelector('.regime-hero-label').textContent = meta.label;

  hero.querySelector('.regime-confidence').textContent = formatNumber(data.confidence, 1) + '%';
  hero.querySelector('.regime-trend').textContent = formatNumber(data.trend_strength, 1);
  hero.querySelector('.regime-volatility').textContent = formatNumber(data.volatility * 100, 2) + '%';
  hero.querySelector('.regime-symbol').textContent = data.symbol || '—';

  // Reasons
  const reasonsEl = hero.querySelector('.regime-hero-reasons');
  if (data.reasons && data.reasons.length > 0) {
    reasonsEl.innerHTML = data.reasons.map(r =>
      `<div class="regime-hero-reason">${escapeHtml(r)}</div>`
    ).join('');
  } else {
    reasonsEl.innerHTML = '<div class="regime-hero-reason" style="color:var(--text-muted)">No specific reasons available</div>';
  }
}

// ============================================================
// Dashboard View
// ============================================================
async function loadDashboard() {
  // Load regime detection
  loadRegime();

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

  const local = uploadedAnalysis(symbol);
  if (local) { renderAnalysis(local); return; }

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
  let gaugeColor = '#eab308';
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
// Backtest View — with charts
// ============================================================
async function runBacktest() {
  const symbol = $('#backtest-symbol').value.trim().toUpperCase();
  const commission = parseFloat($('#backtest-commission').value) || 0;
  const slippage = parseFloat($('#backtest-slippage').value) || 0;

  if (!symbol) { showToast('Please enter a stock symbol', 'error'); return; }

  const chartsEl = $('#backtest-charts');
  const resultEl = $('#backtest-result');
  showLoading('backtest-result');
  if (chartsEl) chartsEl.classList.add('hidden');

  // Destroy old chart instances
  destroyCharts();

  const local = uploadedBacktest(symbol, commission);
  if (local) { renderBacktest(local); return; }

  try {
    const data = await api.backtest(symbol, commission, slippage);
    renderBacktest(data);
  } catch (err) {
    showError('backtest-result', err.message);
  }
}

function destroyCharts() {
  if (equityChartInstance) { equityChartInstance.destroy(); equityChartInstance = null; }
  if (returnsChartInstance) { returnsChartInstance.destroy(); returnsChartInstance = null; }
  if (riskChartInstance) { riskChartInstance.destroy(); riskChartInstance = null; }
  if (pathsChartInstance) { pathsChartInstance.destroy(); pathsChartInstance = null; }
  if (distributionChartInstance) { distributionChartInstance.destroy(); distributionChartInstance = null; }
  if (varChartInstance) { varChartInstance.destroy(); varChartInstance = null; }
}

function renderBacktest(data) {
  const container = $('#backtest-result');
  if (!container) return;

  const metrics = data.metrics || {};
  const report = data.report || {};
  const trades = data.trades || [];

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

  // Render charts if we have trades
  if (trades.length > 0) {
    renderBacktestCharts(trades, metrics);
  }
}

function renderBacktestCharts(trades, metrics) {
  const chartsEl = $('#backtest-charts');
  if (!chartsEl) return;
  chartsEl.classList.remove('hidden');

  // Build equity curve data from cumulative returns
  let cumulative = 1.0;
  const labels = [];
  const equityData = [];
  const returnData = [];

  trades.forEach((t, i) => {
    labels.push(t.date ? t.date.substring(0, 10) : `Trade ${i + 1}`);
    cumulative *= (1 + (t.return_pct || 0) / 100);
    equityData.push(+(cumulative * 100 - 100).toFixed(2));
    returnData.push(+(t.return_pct || 0));
  });

  const chartOptions = {
    responsive: true,
    maintainAspectRatio: false,
    plugins: {
      legend: {
        labels: { color: '#8892a8', font: { family: 'Inter', size: 11 } },
      },
      tooltip: {
        backgroundColor: '#1a1f2e',
        titleColor: '#e8edf5',
        bodyColor: '#8892a8',
        borderColor: '#2a3548',
        borderWidth: 1,
        cornerRadius: 6,
        padding: 10,
      },
    },
    scales: {
      x: {
        ticks: { color: '#5a6378', font: { size: 10 } },
        grid: { color: 'rgba(30,41,59,0.5)' },
      },
      y: {
        ticks: { color: '#5a6378', font: { size: 10 } },
        grid: { color: 'rgba(30,41,59,0.5)' },
      },
    },
  };

  // Equity curve chart
  const equityCtx = document.getElementById('equity-curve-chart');
  if (equityCtx) {
    if (equityChartInstance) equityChartInstance.destroy();
    const isFinalPositive = equityData[equityData.length - 1] >= 0;
    const gradient = equityCtx.getContext('2d').createLinearGradient(0, 0, 0, 250);
    gradient.addColorStop(0, isFinalPositive ? 'rgba(34,197,94,0.2)' : 'rgba(239,68,68,0.2)');
    gradient.addColorStop(1, isFinalPositive ? 'rgba(34,197,94,0)' : 'rgba(239,68,68,0)');

    equityChartInstance = new Chart(equityCtx, {
      type: 'line',
      data: {
        labels,
        datasets: [{
          label: 'Cumulative Return %',
          data: equityData,
          borderColor: isFinalPositive ? '#22c55e' : '#ef4444',
          backgroundColor: gradient,
          fill: true,
          tension: 0.3,
          pointRadius: 2,
          pointHoverRadius: 5,
          borderWidth: 2,
        }],
      },
      options: {
        ...chartOptions,
        plugins: {
          ...chartOptions.plugins,
          legend: { display: false },
        },
      },
    });
  }

  // Individual returns bar chart
  const retCtx = document.getElementById('returns-chart');
  if (retCtx) {
    if (returnsChartInstance) returnsChartInstance.destroy();
    returnsChartInstance = new Chart(retCtx, {
      type: 'bar',
      data: {
        labels,
        datasets: [{
          label: 'Trade Return %',
          data: returnData,
          backgroundColor: returnData.map(v => v >= 0 ? 'rgba(34,197,94,0.7)' : 'rgba(239,68,68,0.7)'),
          borderColor: returnData.map(v => v >= 0 ? '#22c55e' : '#ef4444'),
          borderWidth: 1,
          borderRadius: 3,
        }],
      },
      options: {
        ...chartOptions,
        plugins: {
          ...chartOptions.plugins,
          legend: { display: false },
        },
        scales: {
          ...chartOptions.scales,
          y: {
            ...chartOptions.scales.y,
            beginAtZero: true,
          },
        },
      },
    });
  }
}

// Enter key for backtest
$('#backtest-symbol')?.addEventListener('keydown', (e) => {
  if (e.key === 'Enter') runBacktest();
});

// ============================================================
// Monte Carlo Simulator View
// ============================================================

async function runSimulation() {
  const symbol = $('#sim-symbol').value.trim().toUpperCase();
  if (!symbol) { showToast('Please enter a stock symbol', 'error'); return; }

  const simulations = parseInt($('#sim-count').value) || 1000;
  const method = $('#sim-method').value;
  const confidence = parseFloat($('#sim-confidence').value) || 0.95;

  const chartsEl = $('#simulation-charts');
  const resultEl = $('#simulation-result');
  showLoading('simulation-result');
  if (chartsEl) chartsEl.classList.add('hidden');

  // Destroy old chart instances
  destroyCharts();

  const local = uploadedSimulation(symbol, simulations);
  if (local) { renderSimulation(local); return; }

  try {
    showToast(`Running ${simulations.toLocaleString()} simulations via ${method}...`, 'info');
    const data = await api.simulate(symbol, simulations, method, confidence);
    renderSimulation(data);
  } catch (err) {
    showError('simulation-result', err.message);
  }
}

function renderSimulation(data) {
  const container = $('#simulation-result');
  if (!container) return;

  const summary = data.summary || {};
  const curves = data.equity_curves || [];
  const bestCurve = data.best_equity || [];
  const worstCurve = data.worst_equity || [];
  const avgCurve = data.average_equity || [];

  // Clear any old content first
  container.innerHTML = '';

  // Show charts section
  const chartsEl = $('#simulation-charts');
  if (chartsEl) chartsEl.classList.remove('hidden');

  // Render metrics
  renderSimMetrics(summary, data);

  // Render percentile table
  renderPercentileTable(summary);

  // Render charts
  renderPathChart(curves, bestCurve, worstCurve, avgCurve, data.symbol);
  renderDistributionChart(summary);
  renderVarChart(summary);
}

function renderSimMetrics(summary, data) {
  const container = $('#simulation-metrics');
  if (!container) return;

  const probProfit = summary.probability_of_profit || 0;
  const probLoss = summary.probability_of_loss || 0;
  const probRuin = summary.probability_of_ruin || 0;
  const var95 = summary.value_at_risk_95 || 0;
  const cvar95 = summary.conditional_var_95 || 0;
  const meanRet = summary.mean_return || 0;
  const medianRet = summary.median_return || 0;
  const bestRet = summary.best_return || 0;
  const worstRet = summary.worst_return || 0;
  const meanDD = summary.mean_drawdown || 0;
  const maxDD = summary.max_drawdown || 0;
  const ci = summary.confidence_interval || {};

  const formatMoney = v => (v >= 0 ? '+' : '') + 'NPR ' + formatNumber(Math.abs(v));

  container.innerHTML = `
    <div class="simulator-metric ${probProfit >= 70 ? 'good' : probProfit >= 40 ? 'warn' : 'bad'}">
      <div class="metric-value">${formatNumber(probProfit, 1)}%</div>
      <div class="metric-label">Prob. of Profit</div>
      <div class="metric-sub">${formatNumber(probLoss, 1)}% loss probability</div>
    </div>
    <div class="simulator-metric ${probRuin < 5 ? 'good' : probRuin < 20 ? 'warn' : 'bad'}">
      <div class="metric-value">${formatNumber(probRuin, 1)}%</div>
      <div class="metric-label">Prob. of Ruin</div>
      <div class="metric-sub">>50% drawdown risk</div>
    </div>
    <div class="simulator-metric ${var95 >= 0 ? 'good' : 'bad'}">
      <div class="metric-value">${formatMoney(var95)}</div>
      <div class="metric-label">VaR (95%)</div>
      <div class="metric-sub">5th percentile ending equity</div>
    </div>
    <div class="simulator-metric ${cvar95 >= 0 ? 'good' : 'bad'}">
      <div class="metric-value">${formatMoney(cvar95)}</div>
      <div class="metric-label">CVaR (95%)</div>
      <div class="metric-sub">Expected shortfall</div>
    </div>
    <div class="simulator-metric info">
      <div class="metric-value">${formatNumber(data.simulations_run || 0, 0)}</div>
      <div class="metric-label">Simulations</div>
      <div class="metric-sub">${data.method} · ${data.total_trades || 0} trades</div>
    </div>
    <div class="simulator-metric info">
      <div class="metric-value">${data.symbol}</div>
      <div class="metric-label">Stock</div>
      <div class="metric-sub">${data.method} method</div>
    </div>
    <div class="simulator-metric neutral">
      <div class="metric-value">${formatMoney(meanRet)}</div>
      <div class="metric-label">Mean Return</div>
      <div class="metric-sub">Median: ${formatMoney(medianRet)}</div>
    </div>
    <div class="simulator-metric neutral">
      <div class="metric-value">${formatMoney(bestRet)}</div>
      <div class="metric-label">Best / Worst</div>
      <div class="metric-sub">${formatMoney(worstRet)} worst</div>
    </div>
    <div class="simulator-metric ${meanDD < 20 ? 'good' : meanDD < 40 ? 'warn' : 'bad'}">
      <div class="metric-value">${formatNumber(meanDD, 1)}%</div>
      <div class="metric-label">Mean Drawdown</div>
      <div class="metric-sub">Max: ${formatNumber(maxDD, 1)}%</div>
    </div>
    <div class="simulator-metric info">
      <div class="metric-value">${formatMoney(ci.lower || 0)}</div>
      <div class="metric-label">CI Lower (${formatNumber((data.confidence_level || 0.95) * 100, 0)}%)</div>
      <div class="metric-sub">Upper: ${formatMoney(ci.upper || 0)}</div>
    </div>
  `;
}

function renderPercentileTable(summary) {
  const container = $('#percentile-table');
  if (!container) return;

  const pcts = summary.percentiles || {};
  const labels = ['p5', 'p10', 'p25', 'p50', 'p75', 'p90', 'p95', 'p99'];
  const displayLabels = ['5th', '10th', '25th', '50th', '75th', '90th', '95th', '99th'];

  const values = labels.map(l => pcts[l] || 0);
  const minVal = Math.min(...values);
  const maxVal = Math.max(...values);
  const range = maxVal - minVal || 1;

  container.innerHTML = `
    <table>
      <thead>
        <tr>
          <th>Percentile</th>
          <th style="width:50%">Distribution</th>
          <th>Value (NPR)</th>
          <th>Outlook</th>
        </tr>
      </thead>
      <tbody>
        ${labels.map((key, i) => {
          const val = pcts[key] || 0;
          const isPos = val >= 0;
          const barWidth = Math.max(((val - minVal) / range) * 100, 2);
          return `<tr>
            <td><strong>${displayLabels[i]}</strong></td>
            <td>
              <div style="height:8px;background:var(--bg-input);border-radius:4px;overflow:hidden">
                <div style="height:100%;width:${barWidth}%;background:${isPos ? '#22c55e' : '#ef4444'};border-radius:4px;transition:width 0.5s ease"></div>
              </div>
            </td>
            <td style="font-family:'JetBrains Mono',monospace;font-weight:600;color:${isPos ? 'var(--accent-green)' : 'var(--accent-red)'}">NPR ${formatNumber(val)}</td>
            <td><span class="signal-badge ${isPos ? 'buy' : (val < 0 ? 'sell' : 'hold')}">${isPos ? 'Profit' : (val < 0 ? 'Loss' : 'Break-even')}</span></td>
          </tr>`;
        }).join('')}
      </tbody>
    </table>
  `;
}

function renderPathChart(curves, bestCurve, worstCurve, avgCurve, symbol) {
  const ctx = document.getElementById('paths-chart');
  if (!ctx) return;

  if (pathsChartInstance) pathsChartInstance.destroy();

  const datasets = [];
  const colors = [
    'rgba(59,130,246,0.08)', 'rgba(139,92,246,0.08)', 'rgba(34,197,94,0.08)',
    'rgba(249,115,22,0.08)', 'rgba(236,72,153,0.08)', 'rgba(6,182,212,0.08)',
    'rgba(234,179,8,0.08)', 'rgba(168,85,247,0.08)',
  ];

  // Add sampled simulation paths (faded)
  curves.forEach((curve, i) => {
    if (curve.length < 2) return;
    // Create a time axis (trade number)
    const data = curve.map((v, j) => ({ x: j, y: +v.toFixed(2) }));
    datasets.push({
      label: `Path ${i + 1}`,
      data,
      borderColor: colors[i % colors.length],
      borderWidth: 0.5,
      pointRadius: 0,
      tension: 0.1,
      showLine: true,
    });
  });

  // Add best curve
  if (bestCurve.length >= 2) {
    const bestData = bestCurve.map((v, j) => ({ x: j, y: +v.toFixed(2) }));
    datasets.push({
      label: 'Best Path',
      data: bestData,
      borderColor: '#22c55e',
      borderWidth: 2.5,
      pointRadius: 0,
      tension: 0.2,
      showLine: true,
    });
  }

  // Add worst curve
  if (worstCurve.length >= 2) {
    const worstData = worstCurve.map((v, j) => ({ x: j, y: +v.toFixed(2) }));
    datasets.push({
      label: 'Worst Path',
      data: worstData,
      borderColor: '#ef4444',
      borderWidth: 2.5,
      pointRadius: 0,
      tension: 0.2,
      showLine: true,
    });
  }

  // Add average curve
  if (avgCurve.length >= 2) {
    const avgData = avgCurve.map((v, j) => ({ x: j, y: +v.toFixed(2) }));
    datasets.push({
      label: 'Average Path',
      data: avgData,
      borderColor: '#eab308',
      borderWidth: 2.5,
      pointRadius: 0,
      tension: 0.2,
      borderDash: [6, 3],
      showLine: true,
    });
  }

  pathsChartInstance = new Chart(ctx, {
    type: 'scatter',
    data: { datasets },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      animation: { duration: 800 },
      plugins: {
        legend: {
          labels: { color: '#8892a8', font: { family: 'Inter', size: 10 } },
        },
        tooltip: {
          backgroundColor: '#1a1f2e',
          titleColor: '#e8edf5',
          bodyColor: '#8892a8',
          borderColor: '#2a3548',
          borderWidth: 1,
          cornerRadius: 6,
          padding: 10,
          mode: 'index',
        },
      },
      scales: {
        x: {
          title: { display: true, text: 'Trade Number', color: '#5a6378', font: { size: 10 } },
          ticks: { color: '#5a6378', font: { size: 9 } },
          grid: { color: 'rgba(30,41,59,0.3)' },
        },
        y: {
          title: { display: true, text: 'Portfolio Value (NPR)', color: '#5a6378', font: { size: 10 } },
          ticks: { color: '#5a6378', font: { size: 9 } },
          grid: { color: 'rgba(30,41,59,0.3)' },
        },
      },
      elements: {
        point: { radius: 0 },
      },
    },
  });
}

function renderDistributionChart(summary) {
  const ctx = document.getElementById('distribution-chart');
  if (!ctx) return;

  if (distributionChartInstance) distributionChartInstance.destroy();

  // Generate histogram bins from percentile data
  const pcts = summary.percentiles || {};
  const binLabels = ['≤p5', 'p5-p10', 'p10-p25', 'p25-p50', 'p50-p75', 'p75-p90', 'p90-p95', 'p95-p99', '≥p99'];
  const binProbabilities = [5, 5, 15, 25, 25, 15, 5, 4, 1];

  // Approximate bin centers from percentiles
  const bins = [
    pcts.p5 || 0,
    (pcts.p5 + pcts.p10) / 2 || 0,
    (pcts.p10 + pcts.p25) / 2 || 0,
    (pcts.p25 + pcts.p50) / 2 || 0,
    (pcts.p50 + pcts.p75) / 2 || 0,
    (pcts.p75 + pcts.p90) / 2 || 0,
    (pcts.p90 + pcts.p95) / 2 || 0,
    (pcts.p95 + pcts.p99) / 2 || 0,
    pcts.p99 || 0,
  ];

  distributionChartInstance = new Chart(ctx, {
    type: 'bar',
    data: {
      labels: binLabels,
      datasets: [{
        label: 'Probability %',
        data: binProbabilities,
        backgroundColor: bins.map(v => v >= 0 ? 'rgba(34,197,94,0.7)' : 'rgba(239,68,68,0.7)'),
        borderColor: bins.map(v => v >= 0 ? '#22c55e' : '#ef4444'),
        borderWidth: 1,
        borderRadius: 3,
      }],
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      plugins: {
        legend: { display: false },
        tooltip: {
          backgroundColor: '#1a1f2e',
          titleColor: '#e8edf5',
          bodyColor: '#8892a8',
          borderColor: '#2a3548',
          borderWidth: 1,
          cornerRadius: 6,
          padding: 10,
          callbacks: {
            afterLabel: function(context) {
              const val = bins[context.dataIndex];
              return `Approx. equity: NPR ${formatNumber(val)}`;
            },
          },
        },
      },
      scales: {
        x: {
          ticks: { color: '#5a6378', font: { size: 9 }, maxRotation: 45 },
          grid: { display: false },
        },
        y: {
          title: { display: true, text: 'Probability (%)', color: '#5a6378', font: { size: 10 } },
          ticks: { color: '#5a6378', font: { size: 9 } },
          grid: { color: 'rgba(30,41,59,0.3)' },
          beginAtZero: true,
        },
      },
    },
  });
}

function renderVarChart(summary) {
  const ctx = document.getElementById('var-chart');
  if (!ctx) return;

  if (varChartInstance) varChartInstance.destroy();

  const var95 = summary.value_at_risk_95 || 0;
  const cvar95 = summary.conditional_var_95 || 0;
  const meanRet = summary.mean_return || 0;
  const probProfit = summary.probability_of_profit || 0;

  // Build a simple waterfall-style visualization of risk metrics
  const labels = ['VaR (95%)', 'CVaR (95%)', 'Mean Return', 'Best Case'];
  const values = [var95, cvar95, meanRet, summary.best_return || 0];

  varChartInstance = new Chart(ctx, {
    type: 'bar',
    data: {
      labels,
      datasets: [{
        label: 'Ending Equity (NPR)',
        data: values,
        backgroundColor: values.map(v => {
          if (v >= 0) return 'rgba(34,197,94,0.7)';
          return 'rgba(239,68,68,0.7)';
        }),
        borderColor: values.map(v => {
          if (v >= 0) return '#22c55e';
          return '#ef4444';
        }),
        borderWidth: 2,
        borderRadius: 4,
      }],
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      plugins: {
        legend: { display: false },
        tooltip: {
          backgroundColor: '#1a1f2e',
          titleColor: '#e8edf5',
          bodyColor: '#8892a8',
          borderColor: '#2a3548',
          borderWidth: 1,
          cornerRadius: 6,
          padding: 10,
          callbacks: {
            label: function(context) {
              const val = context.parsed.y;
              return `NPR ${formatNumber(val)}`;
            },
          },
        },
      },
      scales: {
        x: {
          ticks: { color: '#5a6378', font: { size: 10 } },
          grid: { display: false },
        },
        y: {
          title: { display: true, text: 'Equity (NPR)', color: '#5a6378', font: { size: 10 } },
          ticks: { color: '#5a6378', font: { size: 9 } },
          grid: { color: 'rgba(30,41,59,0.3)' },
        },
      },
    },
  });
}

// Enter key for simulator
$('#sim-symbol')?.addEventListener('keydown', (e) => {
  if (e.key === 'Enter') runSimulation();
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
// Upload OHLCV — client-side price history store
// ============================================================
const UPLOAD_KEY = 'nq_uploaded_history_v1';
// Load persisted uploads at module init so uploaded data survives page
// reloads even if the Upload tab is never visited first.
let uploadedHistory = loadUploadStore();
let uploadChartInstance = null;

function loadUploadStore() {
  try {
    return JSON.parse(localStorage.getItem(UPLOAD_KEY) || '{}') || {};
  } catch (e) {
    return {};
  }
}

function saveUploadStore() {
  try {
    localStorage.setItem(UPLOAD_KEY, JSON.stringify(uploadedHistory));
  } catch (e) {
    showToast('Could not persist uploads (storage full?)', 'error');
  }
}

function uploadedRecords(symbol) {
  const key = String(symbol || '').trim().toUpperCase();
  const entry = uploadedHistory[key];
  return entry ? entry.records : null;
}

function uploadSymbol(symbol, records, source) {
  const key = String(symbol || '').trim().toUpperCase();
  if (!key) return { ok: false, message: 'Symbol is required.' };
  if (!records || records.length === 0) return { ok: false, message: 'No valid rows to upload.' };
  uploadedHistory[key] = {
    records,
    count: records.length,
    source: source || 'CSV',
    uploadedAt: new Date().toISOString(),
  };
  saveUploadStore();
  return { ok: true, key, count: records.length };
}

function removeUpload(symbol) {
  const key = String(symbol || '').trim().toUpperCase();
  if (uploadedHistory[key]) {
    delete uploadedHistory[key];
    saveUploadStore();
    const wrap = $('#upload-preview');
    if (wrap) wrap.classList.add('hidden');
    const list = $('#upload-list');
    if (list) list.innerHTML = renderUploadList();
    showToast(`${key} removed`, 'info');
  }
}

// --- CSV parsing (mirrors src/loaders/csv_loader.py) ---
function splitCSVLine(line) {
  const out = [];
  let cur = '';
  let inQ = false;
  for (let i = 0; i < line.length; i++) {
    const c = line[i];
    if (c === '"') {
      if (inQ && line[i + 1] === '"') { cur += '"'; i++; }
      else inQ = !inQ;
    } else if (c === ',' && !inQ) {
      out.push(cur); cur = '';
    } else cur += c;
  }
  out.push(cur);
  return out;
}

function parseNum(v) {
  if (v === null || v === undefined) return null;
  const s = String(v).replace(/[,%]/g, '').trim();
  if (s === '') return null;
  const n = Number(s);
  return isNaN(n) ? null : n;
}

function normalizeDate(v) {
  const s = String(v).trim();
  if (/^\d{4}-\d{2}-\d{2}/.test(s)) return s.slice(0, 10);
  const d = new Date(s);
  if (isNaN(d.getTime())) return null;
  return d.toISOString().slice(0, 10);
}

function parseOHLCV(text) {
  const lines = String(text || '').split(/\r?\n/).filter(l => l.trim() !== '');
  if (lines.length < 2) {
    return { records: [], errors: ['CSV must contain a header row and at least one data row.'] };
  }
  const header = lines[0].split(',').map(h => h.trim().toLowerCase());
  const colMap = {};
  header.forEach((name, i) => {
    if (/^(date|datetime|timestamp|day)$/.test(name)) colMap.date = i;
    else if (/^open$/.test(name)) colMap.open = i;
    else if (/^high$/.test(name)) colMap.high = i;
    else if (/^low$/.test(name)) colMap.low = i;
    else if (/^(close|closing|adj\s*close|last\s*traded\s*price|ltp)$/.test(name)) colMap.close = i;
    else if (/^(volume|vol|no\.?\s*of\s*shares\s*traded)$/.test(name)) colMap.volume = i;
  });
  const missing = ['date', 'close'].filter(k => !(k in colMap));
  if (missing.length) {
    return { records: [], errors: [`Missing required column(s): ${missing.join(', ')}. Need Date and Close (Open/High/Low/Volume optional).`] };
  }
  const records = [];
  const errors = [];
  for (let i = 1; i < lines.length; i++) {
    const cells = splitCSVLine(lines[i]);
    const raw = {};
    for (const [k, idx] of Object.entries(colMap)) {
      if (idx !== undefined && idx < cells.length) raw[k] = cells[idx].trim();
    }
    if (raw.date === undefined || raw.date === '') {
      errors.push(`Row ${i + 1}: missing date — skipped.`);
      continue;
    }
    const date = normalizeDate(raw.date);
    const close = parseNum(raw.close);
    if (!date) { errors.push(`Row ${i + 1}: invalid date "${raw.date}" — skipped.`); continue; }
    if (close === null) { errors.push(`Row ${i + 1}: invalid Close value "${raw.close}" — skipped.`); continue; }
    const open = raw.open !== undefined ? parseNum(raw.open) : close;
    const high = raw.high !== undefined ? parseNum(raw.high) : Math.max(open || close, close);
    const low = raw.low !== undefined ? parseNum(raw.low) : Math.min(open || close, close);
    const volume = raw.volume !== undefined ? Math.max(0, parseNum(raw.volume) || 0) : 0;
    records.push({ date, open: open ?? close, high: high ?? close, low: low ?? close, close, volume });
  }
  records.sort((a, b) => (a.date < b.date ? -1 : a.date > b.date ? 1 : 0));
  return { records, errors };
}

// --- Indicator math (client-side) ---
function sma(values, period) {
  const out = [];
  let sum = 0;
  for (let i = 0; i < values.length; i++) {
    sum += values[i];
    if (i >= period) sum -= values[i - period];
    out.push(i >= period - 1 ? sum / period : null);
  }
  return out;
}

function ema(values, period) {
  const out = [];
  const k = 2 / (period + 1);
  let prev = null;
  for (let i = 0; i < values.length; i++) {
    if (i < period - 1) { out.push(null); continue; }
    if (prev === null) {
      let s = 0;
      for (let j = 0; j < period; j++) s += values[i - period + 1 + j];
      prev = s / period;
    } else {
      prev = values[i] * k + prev * (1 - k);
    }
    out.push(prev);
  }
  return out;
}

function rsi(closes, period = 14) {
  if (!closes || closes.length <= period) return null;
  let gain = 0;
  let loss = 0;
  for (let i = 1; i <= period; i++) {
    const ch = closes[i] - closes[i - 1];
    if (ch >= 0) gain += ch; else loss -= ch;
  }
  let avgGain = gain / period;
  let avgLoss = loss / period;
  for (let i = period + 1; i < closes.length; i++) {
    const ch = closes[i] - closes[i - 1];
    avgGain = (avgGain * (period - 1) + Math.max(ch, 0)) / period;
    avgLoss = (avgLoss * (period - 1) + Math.max(-ch, 0)) / period;
  }
  if (avgLoss === 0) return 100;
  return 100 - 100 / (1 + avgGain / avgLoss);
}

function macd(closes, fast = 12, slow = 26, signalPeriod = 9) {
  const ef = ema(closes, fast);
  const es = ema(closes, slow);
  const line = closes.map((_, i) => (ef[i] !== null && es[i] !== null ? ef[i] - es[i] : null));
  const valid = line.filter(v => v !== null);
  const signal = ema(valid, signalPeriod);
  const lastLine = line[line.length - 1];
  const lastSignal = signal.length ? signal[signal.length - 1] : null;
  return {
    line: lastLine,
    signal: lastSignal,
    hist: lastLine !== null && lastSignal !== null ? lastLine - lastSignal : null,
  };
}

function atr(records, period = 14) {
  if (!records || records.length <= period) return null;
  const trs = [];
  for (let i = 0; i < records.length; i++) {
    const r = records[i];
    const pc = i > 0 ? records[i - 1].close : null;
    const tr = pc === null ? r.high - r.low : Math.max(r.high - r.low, Math.abs(r.high - pc), Math.abs(r.low - pc));
    trs.push(tr);
  }
  let sum = 0;
  for (let i = 1; i <= period; i++) sum += trs[i];
  let prev = sum / period;
  for (let i = period + 1; i < trs.length; i++) prev = (prev * (period - 1) + trs[i]) / period;
  return prev;
}

// --- Payload builders from uploaded data ---
function uploadedAnalysis(symbol) {
  const recs = uploadedRecords(symbol);
  if (!recs || recs.length < 20) return null;
  const closes = recs.map(r => r.close);
  const last = closes[closes.length - 1];
  const rsiVal = rsi(closes);
  const macdVal = macd(closes);
  const atrVal = atr(recs);
  const sma20 = sma(closes, 20).pop();
  const ema20 = ema(closes, 20).pop();
  const sma50 = closes.length >= 50 ? sma(closes, 50).pop() : null;
  const volAvg = recs.slice(-20).reduce((s, r) => s + r.volume, 0) / Math.min(20, recs.length);
  const volLast = recs[recs.length - 1].volume;
  const ret5 = last / closes[Math.max(0, closes.length - 6)] - 1;
  const ret20 = last / closes[Math.max(0, closes.length - 21)] - 1;

  let score = 5.0;
  const reasons = [];
  if (rsiVal !== null && rsiVal < 30) { score += 1.5; reasons.push('RSI oversold — rebound potential'); }
  else if (rsiVal !== null && rsiVal > 70) { score -= 1.5; reasons.push('RSI overbought — pullback risk'); }
  else if (rsiVal !== null) reasons.push(`RSI ${rsiVal.toFixed(1)} neutral`);
  if (last > sma20) { score += 1.2; reasons.push('Price above 20-day SMA'); }
  if (sma50 !== null && last > sma50) { score += 1.0; reasons.push('Price above 50-day SMA'); }
  if (macdVal.hist !== null && macdVal.hist > 0) { score += 1.0; reasons.push('MACD bullish crossover'); }
  if (volLast > volAvg * 1.2) { score += 0.5; reasons.push('Above-average volume'); }
  if (ret5 > 0.03) reasons.push('Momentum over 5 sessions');
  if (ret20 > 0.05) { score += 0.5; reasons.push('Uptrend over 20 sessions'); }

  const signal = score >= 7 ? 'BUY' : score <= 3.5 ? 'SELL' : 'HOLD';
  const confidence = Math.min(95, Math.round(50 + Math.abs(score - 5) * 9));
  const stopPct = atrVal && atrVal > 0 ? Math.min(0.15, (atrVal * 2) / last) : 0.06;
  const stop = last * (1 - stopPct);
  const risk = last - stop;
  const t1 = last + risk * 1.5;
  const t2 = last + risk * 2.5;
  const t3 = last + risk * 4;
  const bestRR = risk > 0 ? (t2 - last) / risk : 1.5;

  return {
    symbol: String(symbol).toUpperCase(),
    signal,
    confidence,
    score: Math.min(10, +score.toFixed(1)),
    price: last,
    trend: last > sma20 ? 'Uptrend' : last < sma20 ? 'Downtrend' : 'Sideways',
    trade_style: 'Swing',
    entry_zone: `${(last * 0.99).toFixed(2)} – ${(last * 1.01).toFixed(2)}`,
    stop_loss: +stop.toFixed(2),
    target1: +t1.toFixed(2),
    target2: +t2.toFixed(2),
    target3: +t3.toFixed(2),
    risk: atrVal !== null && atrVal / last > 0.05 ? 'High' : atrVal !== null && atrVal / last > 0.03 ? 'Medium' : 'Low',
    holding_period: '1–3 months',
    rsi: rsiVal !== null ? +rsiVal.toFixed(1) : null,
    macd: macdVal.line !== null ? +macdVal.line.toFixed(4) : null,
    atr: atrVal !== null ? +atrVal.toFixed(2) : null,
    volume_signal: volLast > volAvg ? 'Rising' : 'Falling',
    relative_volume: +(volLast / (volAvg || 1)).toFixed(2),
    pattern: 'From uploaded OHLCV',
    pattern_type: 'User data',
    best_rr: +bestRR.toFixed(2),
    rr_grade: bestRR >= 2.5 ? 'Excellent' : bestRR >= 1.5 ? 'Good' : 'Poor',
    rr_target1: +(risk > 0 ? (t1 - last) / risk : 1.5).toFixed(2),
    rr_target2: +(risk > 0 ? (t2 - last) / risk : 2.5).toFixed(2),
    rr_target3: +(risk > 0 ? (t3 - last) / risk : 4).toFixed(2),
    alerts: reasons.slice(0, 4).map((message, i) => ({
      priority: signal === 'BUY' ? 3 : signal === 'SELL' ? 2 : 1,
      message,
    })),
  };
}

function uploadedBacktest(symbol, commissionPct = 0.1) {
  const recs = uploadedRecords(symbol);
  if (!recs || recs.length < 50) return null;
  const closes = recs.map(r => r.close);
  const s20 = sma(closes, 20);
  const s50 = sma(closes, 50);
  const trades = [];
  let inTrade = null;
  for (let i = 50; i < recs.length; i++) {
    const c20 = s20[i];
    const c50 = s50[i];
    const p20 = s20[i - 1];
    const p50 = s50[i - 1];
    if (c20 === null || c50 === null) continue;
    if (!inTrade && p20 !== null && p50 !== null && p20 <= p50 && c20 > c50) {
      inTrade = { entry: recs[i].close, date: recs[i].date };
    } else if (inTrade) {
      const pnl = ((recs[i].close - inTrade.entry) / inTrade.entry) * 100 - commissionPct;
      const exit = recs[i].close <= inTrade.entry * 0.92
        ? 'STOP_LOSS'
        : pnl >= 15
          ? 'TARGET'
          : p20 !== null && p50 !== null && c20 < c50
            ? 'SIGNAL'
            : null;
      if (exit) {
        trades.push({ date: recs[i].date, entry_price: +inTrade.entry.toFixed(2), exit_price: +recs[i].close.toFixed(2), shares: 10, return_pct: +pnl.toFixed(2), exit_reason: exit });
        inTrade = null;
      }
    }
  }
  if (inTrade) {
    const lastRec = recs[recs.length - 1];
    const pnl = ((lastRec.close - inTrade.entry) / inTrade.entry) * 100 - commissionPct;
    trades.push({ date: lastRec.date, entry_price: +inTrade.entry.toFixed(2), exit_price: +lastRec.close.toFixed(2), shares: 10, return_pct: +pnl.toFixed(2), exit_reason: 'EOD' });
  }
  const wins = trades.filter(t => t.return_pct > 0);
  const losses = trades.filter(t => t.return_pct <= 0);
  const total = trades.length;
  const grossWin = wins.reduce((s, t) => s + t.return_pct, 0);
  const grossLoss = Math.abs(losses.reduce((s, t) => s + t.return_pct, 0));
  const winRate = total ? (wins.length / total) * 100 : 0;
  const metrics = {
    total_trades: total,
    win_rate: +winRate.toFixed(1),
    winning_trades: wins.length,
    losing_trades: losses.length,
    breakeven_trades: total - wins.length - losses.length,
    profit_factor: grossLoss > 0 ? +(grossWin / grossLoss).toFixed(2) : grossWin > 0 ? 99 : 0,
    expectancy: total ? +((grossWin - grossLoss) / total).toFixed(2) : 0,
    average_win: wins.length ? +(grossWin / wins.length).toFixed(2) : 0,
    average_loss: losses.length ? +(-grossLoss / losses.length).toFixed(2) : 0,
  };
  return {
    symbol: String(symbol).toUpperCase(),
    metrics,
    report: {
      summary: `SMA 20/50 crossover backtest on uploaded ${String(symbol).toUpperCase()} data (${recs.length} bars). ${total} trades, win rate ${winRate.toFixed(1)}%, profit factor ${metrics.profit_factor}, expectancy ${metrics.expectancy}% per trade. Commission ${commissionPct}% per round trip.`,
    },
    trades,
  };
}

function uploadedSimulation(symbol, simulations = 1000) {
  const recs = uploadedRecords(symbol);
  if (!recs || recs.length < 30) return null;
  const rets = [];
  for (let i = 1; i < recs.length; i++) rets.push(recs[i].close / recs[i - 1].close - 1);
  const n = Math.min(30, rets.length);
  const start = 100000;
  const endings = [];
  const curves = [];
  for (let s = 0; s < simulations; s++) {
    let eq = start;
    const path = [start];
    for (let i = 0; i < n; i++) {
      eq *= 1 + rets[Math.floor(Math.random() * rets.length)];
      path.push(+eq.toFixed(2));
    }
    endings.push(eq);
    if (s < 8) curves.push(path);
  }
  endings.sort((a, b) => a - b);
  const pct = p => endings[Math.min(endings.length - 1, Math.max(0, Math.round(p * (endings.length - 1))))];
  const p5 = pct(0.05), p10 = pct(0.10), p25 = pct(0.25), p50 = pct(0.50);
  const p75 = pct(0.75), p90 = pct(0.90), p95 = pct(0.95), p99 = pct(0.99);
  const mean = endings.reduce((s, v) => s + v, 0) / endings.length;
  const belowStart = endings.filter(v => v < start).length;
  const ruin = endings.filter(v => v < start * 0.5).length;
  const tail = endings.filter(v => v <= p5);
  const cvar = tail.length ? tail.reduce((s, v) => s + v, 0) / tail.length : p5;
  let maxDD = 0;
  curves.forEach(path => {
    let peak = start;
    path.forEach(v => {
      if (v > peak) peak = v;
      const dd = (peak - v) / peak;
      if (dd > maxDD) maxDD = dd;
    });
  });
  const avgPath = [];
  for (let i = 0; i <= n; i++) {
    let s = 0;
    let c = 0;
    curves.forEach(p => { if (p[i] !== undefined) { s += p[i]; c++; } });
    avgPath.push(c ? +(s / c).toFixed(2) : null);
  }
  return {
    symbol: String(symbol).toUpperCase(),
    method: 'bootstrap',
    simulations_run: simulations,
    total_trades: recs.length,
    confidence_level: 0.95,
    summary: {
      probability_of_profit: +((1 - belowStart / simulations) * 100).toFixed(1),
      probability_of_loss: +((belowStart / simulations) * 100).toFixed(1),
      probability_of_ruin: +((ruin / simulations) * 100).toFixed(1),
      value_at_risk_95: +p5.toFixed(2),
      conditional_var_95: +cvar.toFixed(2),
      mean_return: +(mean - start).toFixed(2),
      median_return: +(p50 - start).toFixed(2),
      best_return: +(endings[endings.length - 1] - start).toFixed(2),
      worst_return: +(endings[0] - start).toFixed(2),
      mean_drawdown: +(maxDD * 100).toFixed(1),
      max_drawdown: +(maxDD * 100).toFixed(1),
      confidence_interval: { lower: +p5.toFixed(2), upper: +p95.toFixed(2) },
      percentiles: { p5: +p5.toFixed(2), p10: +p10.toFixed(2), p25: +p25.toFixed(2), p50: +p50.toFixed(2), p75: +p75.toFixed(2), p90: +p90.toFixed(2), p95: +p95.toFixed(2), p99: +p99.toFixed(2) },
    },
    equity_curves: curves,
    best_equity: curves.length ? curves.reduce((a, b) => (b[b.length - 1] > a[a.length - 1] ? b : a)) : [],
    worst_equity: curves.length ? curves.reduce((a, b) => (b[b.length - 1] < a[a.length - 1] ? b : a)) : [],
    average_equity: avgPath,
  };
}

// --- Upload view ---
function renderUpload() {
  uploadedHistory = loadUploadStore();
  const list = $('#upload-list');
  if (list) list.innerHTML = renderUploadList();
  const wrap = $('#upload-preview');
  if (wrap && !wrap.classList.contains('hidden')) wrap.classList.add('hidden');
}

function renderUploadList() {
  const keys = Object.keys(uploadedHistory).sort();
  if (keys.length === 0) {
    return '<div class="empty-state"><div class="empty-icon">📤</div><p>No uploaded datasets yet. Upload a CSV above or load a sample.</p></div>';
  }
  return keys.map(k => {
    const e = uploadedHistory[k];
    const first = e.records[0].date;
    const last = e.records[e.records.length - 1].date;
    return `
      <div class="upload-row">
        <strong>${escapeHtml(k)}</strong>
        <span class="upload-meta">${e.count} bars · ${escapeHtml(first)} → ${escapeHtml(last)} · ${escapeHtml(e.source || 'CSV')}</span>
        <span class="upload-actions">
          <button class="btn btn-outline btn-sm" onclick="previewUpload('${escapeHtml(k)}')">👁 Preview</button>
          <button class="btn btn-outline btn-sm" onclick="runAnalysisUpload('${escapeHtml(k)}')">📊 Analyze</button>
          <button class="btn btn-outline btn-sm" onclick="runBacktestUpload('${escapeHtml(k)}')">⏪ Backtest</button>
          <button class="btn btn-outline btn-sm" onclick="runSimulationUpload('${escapeHtml(k)}')">🎲 Simulate</button>
          <button class="btn btn-danger btn-sm" onclick="removeUpload('${escapeHtml(k)}')">✕</button>
        </span>
      </div>`;
  }).join('');
}

function handleUploadFile() {
  const input = $('#upload-file');
  const symbolInput = $('#upload-symbol');
  const msg = $('#upload-message');
  const symbol = symbolInput ? symbolInput.value.trim().toUpperCase() : '';
  if (!input || !input.files || input.files.length === 0) {
    if (msg) msg.innerHTML = '<div class="error-state">Choose a CSV file first.</div>';
    return;
  }
  const file = input.files[0];
  const reader = new FileReader();
  reader.onload = e => processParsed(parseOHLCV(String(e.target.result || '')), symbol, msg);
  reader.onerror = () => { if (msg) msg.innerHTML = '<div class="error-state">Could not read the file.</div>'; };
  reader.readAsText(file);
}

function processParsed(result, symbol, msg) {
  if (result.errors.length && result.records.length === 0) {
    if (msg) msg.innerHTML = `<div class="error-state">${escapeHtml(result.errors.join(' '))}</div>`;
    return;
  }
  if (!symbol) {
    if (msg) msg.innerHTML = '<div class="error-state">Enter a stock symbol before uploading.</div>';
    return;
  }
  const store = uploadSymbol(symbol, result.records, 'CSV');
  if (!store.ok) {
    if (msg) msg.innerHTML = `<div class="error-state">${escapeHtml(store.message)}</div>`;
    return;
  }
  if (msg) {
    const warn = result.errors.length
      ? `<div class="warning-state">${escapeHtml(result.errors.slice(0, 5).join(' '))}</div>`
      : '';
    msg.innerHTML = `<div class="success-state">✅ ${escapeHtml(store.key)} uploaded — ${store.count} bars.</div>${warn}`;
  }
  renderUpload();
  previewUpload(store.key);
}

function loadSampleOHLCV() {
  const symbolInput = $('#upload-symbol');
  const symbol = (symbolInput && symbolInput.value.trim()) || 'NABIL';
  const key = symbol.toUpperCase();
  const recs = [];
  let price = 400 + Math.random() * 100;
  const d = new Date('2024-01-02');
  const skipWeekend = dd => {
    while (dd.getDay() === 0 || dd.getDay() === 6) dd.setDate(dd.getDate() + 1);
    return dd;
  };
  for (let i = 0; i < 260; i++) {
    const chg = (Math.random() - 0.47) * 0.03;
    const open = price;
    const close = Math.max(1, open * (1 + chg));
    const high = Math.max(open, close) * (1 + Math.random() * 0.015);
    const low = Math.min(open, close) * (1 - Math.random() * 0.015);
    price = close;
    skipWeekend(d);
    recs.push({
      date: d.toISOString().slice(0, 10),
      open: +open.toFixed(2),
      high: +high.toFixed(2),
      low: +low.toFixed(2),
      close: +close.toFixed(2),
      volume: Math.round(50000 + Math.random() * 450000),
    });
    d.setDate(d.getDate() + 1);
  }
  const msg = $('#upload-message');
  const store = uploadSymbol(key, recs, 'Sample');
  if (msg) msg.innerHTML = `<div class="success-state">✅ Sample ${escapeHtml(key)} generated — ${store.count} bars.</div>`;
  renderUpload();
  previewUpload(key);
}

function previewUpload(key) {
  const recs = uploadedRecords(key);
  if (!recs) return;
  const wrap = $('#upload-preview');
  if (wrap) wrap.classList.remove('hidden');
  const table = $('#upload-table');
  if (table) {
    const rows = recs.slice(-20).reverse().map(r =>
      `<tr><td>${escapeHtml(r.date)}</td><td>${formatNumber(r.open)}</td><td>${formatNumber(r.high)}</td><td>${formatNumber(r.low)}</td><td>${formatNumber(r.close)}</td><td>${r.volume.toLocaleString()}</td></tr>`
    ).join('');
    table.innerHTML = `<table><thead><tr><th>Date</th><th>Open</th><th>High</th><th>Low</th><th>Close</th><th>Volume</th></tr></thead><tbody>${rows}</tbody></table>`;
  }
  const ctx = document.getElementById('upload-chart');
  if (ctx && typeof Chart !== 'undefined') {
    if (uploadChartInstance) uploadChartInstance.destroy();
    uploadChartInstance = new Chart(ctx, {
      type: 'line',
      data: {
        labels: recs.map(r => r.date),
        datasets: [{
          label: `${key} Close`,
          data: recs.map(r => r.close),
          borderColor: '#3b82f6',
          backgroundColor: 'rgba(59,130,246,0.1)',
          fill: true,
          tension: 0.25,
          pointRadius: 0,
          borderWidth: 2,
        }],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        plugins: {
          legend: { labels: { color: '#8892a8', font: { size: 11 } } },
          tooltip: { backgroundColor: '#1a1f2e', titleColor: '#e8edf5', bodyColor: '#8892a8', borderColor: '#2a3548', borderWidth: 1, cornerRadius: 6, padding: 10 },
        },
        scales: {
          x: { ticks: { color: '#5a6378', font: { size: 9 }, maxTicksLimit: 8 }, grid: { color: 'rgba(30,41,59,0.3)' } },
          y: { ticks: { color: '#5a6378', font: { size: 9 } }, grid: { color: 'rgba(30,41,59,0.3)' } },
        },
      },
    });
  }
}

function runAnalysisUpload(key) {
  navigateTo('analysis');
  const input = $('#analysis-symbol');
  if (input) input.value = key;
  runAnalysis();
}

function runBacktestUpload(key) {
  navigateTo('backtest');
  const input = $('#backtest-symbol');
  if (input) input.value = key;
  runBacktest();
}

function runSimulationUpload(key) {
  navigateTo('simulator');
  const input = $('#sim-symbol');
  if (input) input.value = key;
  runSimulation();
}

// ============================================================
// Portfolio View — with heatmap
// ============================================================
async function loadPortfolio() {
  const container = $('#portfolio-content');
  showLoading('portfolio-content');

  // Hide heatmap until data loads
  const heatmap = $('#portfolio-heatmap');
  if (heatmap) heatmap.classList.add('hidden');

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

  // Build sector heatmap from holdings
  const sectorMap = {};
  holdings.forEach(h => {
    const sector = h.sector || 'Others';
    if (!sectorMap[sector]) {
      sectorMap[sector] = { totalInvested: 0, totalValue: 0, count: 0, pnl: 0 };
    }
    sectorMap[sector].totalInvested += (h.average_price || 0) * (h.quantity || 0);
    sectorMap[sector].totalValue += (h.ltp || 0) * (h.quantity || 0);
    sectorMap[sector].count += 1;
    sectorMap[sector].pnl += (h.pnl || 0);
  });

  // Render heatmap if we have data
  renderPortfolioHeatmap(sectorMap, data.portfolio_cost || 0);

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

function renderPortfolioHeatmap(sectorMap, totalInvested) {
  const heatmapEl = $('#portfolio-heatmap');
  if (!heatmapEl) return;

  const sectors = Object.keys(sectorMap);
  if (sectors.length === 0) {
    heatmapEl.classList.add('hidden');
    return;
  }

  heatmapEl.classList.remove('hidden');

  // Sector allocation heatmap (horizontal bars)
  const sectorContainer = $('#sector-heatmap');
  if (!sectorContainer) return;

  // Sort sectors by invested amount descending
  const sortedSectors = sectors.sort((a, b) => sectorMap[b].totalInvested - sectorMap[a].totalInvested);
  const maxInvested = Math.max(...sortedSectors.map(s => sectorMap[s].totalInvested));

  // Color palette for sectors
  const sectorColors = [
    'rgba(59,130,246,0.85)',   // blue
    'rgba(139,92,246,0.85)',   // purple
    'rgba(34,197,94,0.85)',    // green
    'rgba(249,115,22,0.85)',   // orange
    'rgba(236,72,153,0.85)',   // pink
    'rgba(6,182,212,0.85)',    // cyan
    'rgba(234,179,8,0.85)',    // yellow
    'rgba(239,68,68,0.85)',    // red
    'rgba(168,85,247,0.85)',   // violet
    'rgba(20,184,166,0.85)',   // teal
  ];

  let barsHtml = '';
  sortedSectors.forEach((sector, i) => {
    const data = sectorMap[sector];
    const pct = maxInvested > 0 ? (data.totalInvested / maxInvested) * 100 : 0;
    const allocPct = totalInvested > 0 ? (data.totalInvested / totalInvested) * 100 : 0;
    const color = sectorColors[i % sectorColors.length];
    const isPnlPositive = data.pnl >= 0;

    barsHtml += `
      <div class="heatmap-row">
        <div class="heatmap-label">${escapeHtml(sector)}</div>
        <div class="heatmap-bar-wrapper">
          <div class="heatmap-bar" style="width:${pct}%;background:${color}">
            ${pct > 20 ? `NPR ${formatNumber(data.totalInvested)}` : ''}
          </div>
        </div>
        <div class="heatmap-value" style="color:${isPnlPositive ? 'var(--accent-green)' : 'var(--accent-red)'}">
          ${formatNumber(allocPct, 1)}%
          <span style="font-size:10px;color:var(--text-muted);font-weight:400">(${data.count})</span>
        </div>
      </div>
    `;
  });

  sectorContainer.innerHTML = barsHtml;

  // Risk contribution pie chart
  const riskCtx = document.getElementById('risk-contribution-chart');
  if (riskCtx) {
    if (riskChartInstance) riskChartInstance.destroy();

    const riskColors = sortedSectors.map((_, i) => sectorColors[i % sectorColors.length]);
    const riskValues = sortedSectors.map(s => sectorMap[s].totalInvested);

    riskChartInstance = new Chart(riskCtx, {
      type: 'doughnut',
      data: {
        labels: sortedSectors,
        datasets: [{
          data: riskValues,
          backgroundColor: riskColors.map(c => c.replace('0.85', '0.7')),
          borderColor: riskColors,
          borderWidth: 2,
        }],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        plugins: {
          legend: {
            position: 'right',
            labels: {
              color: '#8892a8',
              font: { family: 'Inter', size: 11 },
              padding: 12,
              usePointStyle: true,
              pointStyle: 'circle',
            },
          },
          tooltip: {
            backgroundColor: '#1a1f2e',
            titleColor: '#e8edf5',
            bodyColor: '#8892a8',
            borderColor: '#2a3548',
            borderWidth: 1,
            cornerRadius: 6,
            padding: 10,
            callbacks: {
              label: function(context) {
                const total = context.dataset.data.reduce((a, b) => a + b, 0);
                const pct = total > 0 ? ((context.parsed / total) * 100).toFixed(1) : 0;
                return ` ${context.label}: NPR ${formatNumber(context.parsed)} (${pct}%)`;
              },
            },
          },
        },
      },
    });
  }
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
