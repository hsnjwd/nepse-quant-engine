"""Exception hierarchy for the NEPSE Quant Engine data service.

All exceptions inherit from ``DataServiceError`` so callers can catch a single
base type when they do not care about the specific failure mode.
"""

from __future__ import annotations


class DataServiceError(Exception):
    """Base exception for all data-service errors."""


class ProviderError(DataServiceError):
    """A provider failed to return data (API timeout, bad response, etc.)."""


class CacheError(DataServiceError):
    """The cache layer encountered an error (read/write/eviction failure)."""


class DataUnavailable(DataServiceError):
    """No provider could supply the requested data and no cached copy exists."""


class ConfigurationError(DataServiceError):
    """The data service or a provider is misconfigured."""
