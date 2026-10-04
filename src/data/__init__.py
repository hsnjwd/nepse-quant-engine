"""NEPSE Quant Engine — Centralized Data Service.

Everything flows through ``DataService``.  No module should call external
APIs, read CSV files, or access cache directly.

Usage::

    from src.data import DataService

    svc = DataService()
    summary = svc.get_market_summary()
    quote = svc.get_stock("NABIL")
    history = svc.get_history("NABIL")
"""

from __future__ import annotations

from src.data.service import DataService
from src.data.models import (
    MarketSummary,
    MarketStatus,
    StockQuote,
    StockHistory,
    TopMover,
    TopMovers,
    MarketScanResult,
    WatchlistEntry,
)
from src.data.exceptions import (
    DataServiceError,
    ProviderError,
    CacheError,
    DataUnavailable,
    ConfigurationError,
)
from src.data.cache import MemoryCache, DiskCache, TieredCache
from src.data.providers import (
    BaseProvider,
    CSVProvider,
    APIProvider,
    HybridProvider,
)
from src.data.websocket import LiveMarketStream, WebSocketConfig, WebSocketStats
from src.data.rate_limiter import RateLimiter, RateLimitStats
from src.data.health import (
    ProviderHealthMonitor,
    ProviderHealth,
    HealthCheckConfig,
)
from src.data.metrics import MetricsCollector, MetricsSnapshot
from src.data.quality import (
    DataQualityReport,
    QualityIssue,
    QualityMetricsCollector,
    quality_metrics,
    assess_history,
    validate_history_frame,
    assess_market_summary,
    assess_freshness,
    detect_disagreement,
    validate_corpus,
    VALID,
    INVALID,
    SUSPICIOUS,
    FRESH,
    STALE,
    UNKNOWN,
)

__all__ = [
    # Service
    "DataService",
    # Models
    "MarketSummary",
    "MarketStatus",
    "StockQuote",
    "StockHistory",
    "TopMover",
    "TopMovers",
    "MarketScanResult",
    "WatchlistEntry",
    # Exceptions
    "DataServiceError",
    "ProviderError",
    "CacheError",
    "DataUnavailable",
    "ConfigurationError",
    # Cache
    "MemoryCache",
    "DiskCache",
    "TieredCache",
    # Providers
    "BaseProvider",
    "CSVProvider",
    "APIProvider",
    "HybridProvider",
    # WebSocket
    "LiveMarketStream",
    "WebSocketConfig",
    "WebSocketStats",
    # Rate limiter
    "RateLimiter",
    "RateLimitStats",
    # Health
    "ProviderHealthMonitor",
    "ProviderHealth",
    "HealthCheckConfig",
    # Metrics
    "MetricsCollector",
    "MetricsSnapshot",
    # Data quality (Sprint 13.3)
    "DataQualityReport",
    "QualityIssue",
    "QualityMetricsCollector",
    "quality_metrics",
    "assess_history",
    "validate_history_frame",
    "assess_market_summary",
    "assess_freshness",
    "detect_disagreement",
    "validate_corpus",
    "VALID",
    "INVALID",
    "SUSPICIOUS",
    "FRESH",
    "STALE",
    "UNKNOWN",
]
