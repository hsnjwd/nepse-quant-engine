"""Exception hierarchy for the NEPSE Quant Engine data service.

All exceptions inherit from ``DataServiceError`` so callers can catch a single
base type when they do not care about the specific failure mode.
"""

from __future__ import annotations


class DataServiceError(Exception):
    """Base exception for all data-service errors."""


class ProviderError(DataServiceError):
    """A provider failed to return data (API timeout, bad response, etc.)."""


class ProviderTimeout(ProviderError):
    """A provider timed out while fetching data (Sprint 13.2).

    Distinguishable from a generic :class:`ProviderError` so callers and
    observability can tell an upstream *timeout* apart from a refused
    connection, HTTP error, or malformed payload.  Inherits from
    ``ProviderError`` so existing ``except ProviderError`` handlers keep
    working unchanged.
    """


class InvalidDataError(ProviderError):
    """A provider returned data that fails the canonical quality contract.

    Raised by the centralized validator (``src.data.quality``) when a
    provider's payload violates OHLC integrity, contains NaN/inf,
    negative volume, conflicting duplicates, or otherwise cannot be
    trusted (Sprint 13.3).  Inherits from ``ProviderError`` so the
    ``HybridProvider`` fallback chain treats it like any other provider
    failure — an invalid provider result falls through to the next
    source instead of being silently used.
    """


class CacheError(DataServiceError):
    """The cache layer encountered an error (read/write/eviction failure)."""


class DataUnavailable(DataServiceError):
    """No provider could supply the requested data and no cached copy exists."""


class ConfigurationError(DataServiceError):
    """The data service or a provider is misconfigured."""
