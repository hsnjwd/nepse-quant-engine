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