"""Centralised configuration for the NEPSE Quant Engine.

All environment-driven settings are loaded here with defaults.
Components import from this module rather than reading ``os.environ``
directly.  See ``docs/ARCHITECTURE.md`` for the full configuration guide.
"""

from __future__ import annotations

import os

from dotenv import load_dotenv

load_dotenv()

# ==============================
# Telegram
# ==============================

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")

# ==============================
# API
# ==============================

API_BASE_URL = os.getenv(
    "API_BASE_URL",
    "http://127.0.0.1:8000"
)

# ==============================
# Data Service API URLs
# ==============================

DATA_SERVICE_API_URLS = {
    "nepse_scraper": os.getenv(
        "NEPSE_SCRAPER_URL",
        "https://nepseapi.surajrimal.dev/api/v1"
    ),
    "nepse_data_api": os.getenv(
        "NEPSE_DATA_API_URL",
        ""
    ),
    "nepse_client": os.getenv(
        "NEPSE_CLIENT_URL",
        "https://shubhamnpk.github.io/yonepse/data"
    ),
    "nepalstock_official": os.getenv(
        "NEPALSTOCK_OFFICIAL_URL",
        ""
    ),
    "github_datasets": os.getenv(
        "GITHUB_DATASETS_URL",
        "https://shubhamnpk.github.io/yonepse/data"
    ),
}

# ==============================
# Data
# ==============================

DATA_DIRECTORY = os.getenv(
    "DATA_DIRECTORY",
    "data/raw"
)

# ==============================
# Data Service Defaults
# ==============================

CACHE_MEMORY_TTL = int(
    os.getenv("CACHE_MEMORY_TTL", "60")
)

CACHE_DISK_TTL = int(
    os.getenv("CACHE_DISK_TTL", "600")
)

CACHE_DISK_DIR = os.getenv(
    "CACHE_DISK_DIR",
    ""
)

# ==============================
# WebSocket Settings
# ==============================

WEBSOCKET_ENABLED = os.getenv(
    "WEBSOCKET_ENABLED",
    "false"
).lower() in ("true", "1", "yes")

WEBSOCKET_URL = os.getenv(
    "WEBSOCKET_URL",
    "wss://nepseapi.surajrimal.dev/ws"
)

# ==============================
# Rate Limiter Settings
# ==============================

RATE_LIMIT = int(
    os.getenv("RATE_LIMIT", "10")
)

API_TIMEOUT = int(
    os.getenv("API_TIMEOUT", "15")
)

RETRY_COUNT = int(
    os.getenv("RETRY_COUNT", "3")
)

BACKOFF_BASE = float(
    os.getenv("BACKOFF_BASE", "1.0")
)

# ==============================
# Cache & Refresh Settings
# ==============================

CACHE_REFRESH_INTERVAL = int(
    os.getenv("CACHE_REFRESH_INTERVAL", "60")
)

HEALTH_CHECK_INTERVAL = int(
    os.getenv("HEALTH_CHECK_INTERVAL", "60")
)

ENABLE_PERFORMANCE_MONITORING = os.getenv(
    "ENABLE_PERFORMANCE_MONITORING",
    "false"
).lower() in ("true", "1", "yes")

# ==============================
# Indicator Settings
# ==============================

RSI_PERIOD = int(
    os.getenv("RSI_PERIOD", 14)
)

MACD_FAST = int(
    os.getenv("MACD_FAST", 12)
)

MACD_SLOW = int(
    os.getenv("MACD_SLOW", 26)
)

MACD_SIGNAL = int(
    os.getenv("MACD_SIGNAL", 9)
)

# ==============================
# Scanner Performance
# ==============================

SCANNER_WORKERS = int(
    os.getenv("SCANNER_WORKERS", "4")
)

SCANNER_CACHE_TTL = int(
    os.getenv("SCANNER_CACHE_TTL", "300")
)

# LRU bound per scanner-cache tier (DataFrames + analysis outputs).
# Sprint 12.3 measured finding: the default 200 was *smaller than the
# shipped real corpus* (280 usable files) and the 500-symbol scaling
# target, so a "warm" scan of >200 symbols silently re-parsed and
# re-analysed the evicted symbols every scan (warm-500 speedup collapsed
# from ~50-90x to 1.6x; 1500 CSV reads across 5 warm reps).  Raised to
# 600 so the real corpus and the 500-symbol benchmark corpus fit with
# headroom.  Memory: ~24 KB per cached 500-row DataFrame tier entry, so
# 600 entries is ~15 MB — acceptable for the scanner workload.
SCANNER_CACHE_MAX_ENTRIES = int(
    os.getenv("SCANNER_CACHE_MAX_ENTRIES", "600")
)

SCANNER_LOG_PROGRESS_EVERY = int(
    os.getenv("SCANNER_LOG_PROGRESS_EVERY", "25")
)

# ==============================
# Historical Data Cache
# ==============================

HISTORY_CACHE_TTL = int(
    os.getenv("HISTORY_CACHE_TTL", "300")
)

# ==============================
# Data Quality (Sprint 13.3)
# ==============================

# A history whose latest record is older than this many calendar days is
# classified STALE (and the analysis is flagged in ``data_quality``).
DATA_STALE_AFTER_DAYS = int(
    os.getenv("DATA_STALE_AFTER_DAYS", "7")
)

# A gap between consecutive trading records larger than this many
# business days is flagged as a continuity warning (Sprint 13.3).
DATA_MAX_GAP_DAYS = int(
    os.getenv("DATA_MAX_GAP_DAYS", "14")
)

# When True, stale data also *suppresses* the generated signal (forced to
# HOLD with ``signal_suppressed=True``), not just flagged.  Default OFF
# keeps every existing caller's behaviour byte-identical; live operators
# that want stale prices to never produce a fresh BUY/SELL can enable
# this.  Invalid data (OHLC integrity violations) always suppresses
# regardless of this flag.
ENFORCE_SIGNAL_FRESHNESS = os.getenv(
    "ENFORCE_SIGNAL_FRESHNESS",
    "false"
).lower() in ("true", "1", "yes")

# ==============================
# Indicator Cache (Sprint 11.4)
# ==============================

INDICATOR_CACHE_ENABLED = os.getenv(
    "INDICATOR_CACHE_ENABLED",
    "true"
).lower() in ("true", "1", "yes")

INDICATOR_CACHE_MAX_ENTRIES = int(
    os.getenv("INDICATOR_CACHE_MAX_ENTRIES", "200")
)

# ==============================
# API Alert Batching (Sprint 11.9)
# ==============================
# When enabled, multi-symbol API analysis paths (watchlist scan,
# portfolio) process alert state through ``process_alert_batch`` — one
# history read + one atomic write per batch — instead of the per-symbol
# ``process_alerts`` engine (one read + one write per symbol).  The
# single-symbol ``/api/analyze`` endpoint is unchanged (a one-symbol
# request is not a batch).  Default OFF: the default behaviour is
# exactly the legacy per-symbol alert path, so responses are
# byte-identical unless the operator opts in.  One documented opt-in
# divergence: in batch mode a holding whose analysis raises is isolated
# and skipped in the portfolio response, whereas the legacy path
# propagates the exception (HTTP 500).  See
# ``docs/PERFORMANCE_BASELINE.md`` §14.
ENABLE_ANALYZE_ALERT_BATCH = os.getenv(
    "ENABLE_ANALYZE_ALERT_BATCH",
    "false"
).lower() in ("true", "1", "yes")

# ==============================
# Cross-Worker Metrics (Sprint 12.0)
# ==============================
# Best-effort aggregation of worker-local counters across uvicorn
# ``--workers=N`` processes.  Each worker self-reports its record into
# a shared JSON store (written transactionally via ``update_json``);
# ``/metrics`` reads the store and exposes active-worker + aggregate
# statistics.  Observability only — a metrics failure never breaks the
# API (see ``src/utils/worker_metrics.py``).
WORKER_METRICS_FILE = os.getenv(
    "WORKER_METRICS_FILE",
    "data/state/worker_metrics.json",
)

# Worker liveness window: a record whose ``last_seen`` is older than
# this is no longer counted as an active worker (stale records are
# tolerated, never deleted, and never error).
WORKER_METRICS_TTL_S = float(
    os.getenv("WORKER_METRICS_TTL_S", "60")
)

# ==============================
# Metrics Page (Sprint 12.2)
# ==============================
# TTL for the Streamlit Metrics page's /metrics fetch cache.  A short
# TTL (default 10 s) bounds how often the page re-fetches the backend
# snapshot while keeping it fresh enough for an operational dashboard.
# The page also exposes a manual "Refresh now" button that clears the
# cache immediately.  Errors are never cached (st.cache_data does not
# store raised exceptions), so a failed fetch is retried on the next
# rerun instead of serving a stale snapshot.
METRICS_FETCH_TTL_S = float(
    os.getenv("METRICS_FETCH_TTL_S", "10")
)

# ==============================
# Second Provider (Sprint 13.6)
# ==============================
# A genuinely independent market-data source (independent sourcing
# pipeline, not a wrapper/cache of the primary source).  Research found
# Aabishkar2/nepse-data and omitnomis/ShareSansarScraper as independent
# NEPSE archives; live reliability is NOT verified in this environment,
# so the adapter is DISABLED by default and only engaged when an
# operator configures a base URL they have validated.  When empty, the
# provider chain contains only the default sources (no behavioural
# change).
SECOND_PROVIDER_URL = os.getenv(
    "SECOND_PROVIDER_URL",
    "",
)

# Provider name registered in the hybrid chain + health monitor when the
# second provider is enabled.
SECOND_PROVIDER_NAME = os.getenv(
    "SECOND_PROVIDER_NAME",
    "github_csv",
)

# ==============================
# Trading Calendar (Sprint 13.4)
# ==============================

# Path of the versioned NEPSE trading calendar JSON (weekend rule +
# operator-maintained holiday list + optional corpus-derived observed
# sessions).  See ``src/data/calendar.py`` for the provenance policy:
# no holiday is hard-coded without documentation.
NEPSE_CALENDAR_FILE = os.getenv(
    "NEPSE_CALENDAR_FILE",
    "data/state/nepse_calendar.json",
)

# ==============================
# Cross-Provider Reconciliation (Sprint 13.4)
# ==============================

# Relative tolerance (%) for price fields (open/high/low/close): two
# provider values agree when their relative difference is within this
# bound.  Volume is inherently noisier and gets its own, wider bound.
RECONCILE_PRICE_TOLERANCE_PCT = float(
    os.getenv("RECONCILE_PRICE_TOLERANCE_PCT", "1.0")
)

RECONCILE_VOLUME_TOLERANCE_PCT = float(
    os.getenv("RECONCILE_VOLUME_TOLERANCE_PCT", "10.0")
)

# Relative difference (%) above which a field disagreement is
# *material* (unresolved -> quarantine / signal suppression).  Between
# the tolerance and this threshold the disagreement is MINOR (preferred
# source is used, warning/provenance attached).  Prices and volume have
# separate material thresholds.
RECONCILE_MATERIAL_PRICE_PCT = float(
    os.getenv("RECONCILE_MATERIAL_PRICE_PCT", "5.0")
)

RECONCILE_MATERIAL_VOLUME_PCT = float(
    os.getenv("RECONCILE_MATERIAL_VOLUME_PCT", "50.0")
)

# Preferred provider name used to resolve MINOR disagreements.  The
# reconciliation layer never silently averages conflicting prices; a
# MINOR conflict resolves to the documented preferred source and a
# warning is attached.  Empty string => first provider in order wins.
RECONCILE_PREFERRED_SOURCE = os.getenv(
    "RECONCILE_PREFERRED_SOURCE",
    "api",
)

# Day-over-day price move (percent) above which ``classify_price_jump``
# flags a change as a *large-but-potentially-legitimate* corporate-action
# move (dividend / right / bonus / split) instead of suspecting bad data.
# Structurally-impossible OHLC is rejected regardless of this threshold.
RECONCILE_CORPORATE_ACTION_MOVE_PCT = float(
    os.getenv("RECONCILE_CORPORATE_ACTION_MOVE_PCT", "20.0")
)

# ==============================
# Benchmark Settings
# ==============================

BENCHMARK_RESULTS_DIR = os.getenv(
    "BENCHMARK_RESULTS_DIR",
    "benchmarks/results"
)

BENCHMARK_SYMBOLS = int(
    os.getenv("BENCHMARK_SYMBOLS", "50")
)

BENCHMARK_ROWS = int(
    os.getenv("BENCHMARK_ROWS", "500")
)