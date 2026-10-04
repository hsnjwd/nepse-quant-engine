# Development Roadmap

Future milestones, planned enhancements, and long-term vision for the NEPSE Quant Engine.

---

## v1.0.0-rc2 — Architecture Consolidation (Sprint 10, complete)

**Target: August 2026**

- [x] Fix Momentum Strategy MA filter bug (SMA_20/SMA_50, no silent fallback)
- [x] Consolidate confidence engine to a single implementation
- [x] Single CSV parsing path (`src/loaders/csv_loader.py`) with path resolver
- [x] Unify configuration (`src.config` as single source of truth)
- [x] Canonical version source (`src/version.py` reads `VERSION`)
- [x] Deprecate 10 legacy `src/test_*.py` smoke scripts
- [x] Exception-handling cleanup (logging, explicit exceptions)
- [x] Sprint 10 regression test suite

---

## v1.0.0 — Stable Release (Current: RC1)

**Target: July 2026**

- [x] Streamlit dashboard (24 pages)
- [x] Centralized DataService with tiered caching
- [x] Hybrid API/CSV/fallback provider chain
- [x] Portfolio database (SQLite) and analytics
- [x] Paper trading engine (market/limit/stop-loss/take-profit)
- [x] Market replay engine with cache injection
- [x] Alert center with notification drawer
- [x] Theme manager (Dark/Light/TradingView/Bloomberg)
- [x] Keyboard shortcuts and performance optimisation
- [x] Export center (CSV/Excel/JSON/HTML/PDF)
- [x] Docker deployment and CI/CD pipeline
- [x] Security audit and production hardening
- [x] 825+ passing tests

---

## v1.1.0 — Quality of Life

**Target: August 2026**

- [ ] Paper trading persistence (SQLite-backed)
- [ ] Alert persistence (SQLite or JSON file)
- [ ] Multi-user support (database per user or session isolation)
- [ ] Notification delivery channels (email, Telegram, desktop)
- [ ] User-defined custom strategies
- [ ] Strategy performance comparisons (A/B testing)
- [ ] API documentation with Swagger/OpenAPI annotations
- [ ] Internationalization (Nepali language support)
- [ ] Improved error messages with actionable guidance
- [ ] Keyboard shortcut customisation

---

## v1.2.0 — Advanced Analytics

**Target: September 2026**

- [ ] Machine learning signal enhancement
- [ ] Sentiment analysis (news/social media for NEPSE stocks)
- [ ] Correlation matrix and pair trading
- [ ] Portfolio rebalancing recommendations
- [ ] Risk parity allocation
- [ ] Option pricing model (if options become available on NEPSE)
- [ ] Backtest walk-forward optimisation UI
- [ ] Real-time portfolio tracking with push notifications
- [ ] Sector-wise heatmap with performance comparison
- [ ] Custom dashboard layouts (drag-and-drop widgets)

---

## v2.0.0 — Platform Expansion

**Target: Q4 2026**

- [ ] Multiple exchange support (BSE, NSE India, or other emerging markets)
- [ ] Broker API integration for real trading
- [ ] Mobile-responsive web interface
- [ ] Plugin system for community-developed strategies
- [ ] Strategy marketplace / sharing platform
- [ ] Real-time collaboration features
- [ ] Built-in backtest optimization with genetic algorithms
- [ ] Advanced order types (trailing stop, OCO, bracket orders)
- [ ] Tax reporting (capital gains, dividend summary for Nepal)
- [ ] Performance attribution and factor analysis

---

## Long-term Vision

- **Quant Research Platform**: Turn NEPSE Quant Engine into a full research environment comparable to QuantConnect or Backtrader, specialised for the Nepal market
- **Community**: Build a community of NEPSE quantitative traders contributing strategies, indicators, and data providers
- **Education**: Tutorial series, example notebooks, and strategy templates for new quantitative traders
- **Data Marketplace**: Crowdsourced NEPSE data collection and validation
- **Mobile App**: Companion mobile app for monitoring and alerts

---

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for how to get involved with any of these milestones.
