"""Shared API schemas, pagination helpers, and model serializers.

Provides stable JSON shapes for mobile-ready endpoints: paginated
responses, success envelopes, and converters for DataService models.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass
class Page:
    """A paginated slice of results.

    Attributes:
        items: The page items.
        page: 1-based page number.
        page_size: Items per page.
        total: Total items available.
    """

    items: list[Any] = field(default_factory=list)
    page: int = 1
    page_size: int = 20
    total: int = 0

    @property
    def total_pages(self) -> int:
        """Return the total number of pages."""
        if self.page_size <= 0:
            return 0
        return (self.total + self.page_size - 1) // self.page_size

    def to_dict(self) -> dict[str, Any]:
        """Return a stable JSON dictionary."""
        return {
            "items": self.items,
            "page": self.page,
            "page_size": self.page_size,
            "total": self.total,
            "total_pages": self.total_pages,
            "has_next": self.page < self.total_pages,
            "has_prev": self.page > 1,
        }


def paginate(
    items: list[Any],
    page: int = 1,
    page_size: int = 20,
) -> Page:
    """Slice a list into a :class:`Page`.

    Args:
        items: Full item list.
        page: 1-based page number (clamped).
        page_size: Items per page (clamped to 1–100).

    Returns:
        A :class:`Page`.
    """
    page = max(1, int(page))
    page_size = max(1, min(100, int(page_size)))

    start = (page - 1) * page_size
    end = start + page_size
    return Page(
        items=list(items[start:end]),
        page=page,
        page_size=page_size,
        total=len(items),
    )


def ok(data: Any = None, **extra: Any) -> dict[str, Any]:
    """Wrap a payload in a success envelope.

    Args:
        data: Payload.
        **extra: Extra top-level fields.

    Returns:
        ``{"success": True, "data": ..., **extra}``.
    """
    result: dict[str, Any] = {"success": True, "data": data}
    result.update(extra)
    return result


# ---------------------------------------------------------------------------
# DataService model serializers
# ---------------------------------------------------------------------------


def _iso(value: Any) -> str | None:
    """Convert a datetime to ISO string, handling plain strings."""
    if isinstance(value, datetime):
        return value.isoformat()
    if value is None:
        return None
    return str(value)


def quote_to_dict(quote: Any) -> dict[str, Any]:
    """Serialize a StockQuote dataclass to a dictionary.

    Args:
        quote: StockQuote instance or dict-like.

    Returns:
        JSON-serialisable dict.
    """
    if isinstance(quote, dict):
        return dict(quote)
    return {
        "symbol": getattr(quote, "symbol", ""),
        "company_name": getattr(quote, "company_name", ""),
        "ltp": getattr(quote, "ltp", 0.0),
        "change": getattr(quote, "change", 0.0),
        "change_pct": getattr(quote, "change_pct", 0.0),
        "open_price": getattr(quote, "open_price", 0.0),
        "high": getattr(quote, "high", 0.0),
        "low": getattr(quote, "low", 0.0),
        "close": getattr(quote, "close", 0.0),
        "volume": getattr(quote, "volume", 0),
        "turnover": getattr(quote, "turnover", 0.0),
        "previous_close": getattr(quote, "previous_close", 0.0),
        "timestamp": _iso(getattr(quote, "timestamp", None)),
    }


def mover_to_dict(mover: Any) -> dict[str, Any]:
    """Serialize a TopMover dataclass to a dictionary.

    Args:
        mover: TopMover instance or dict-like.

    Returns:
        JSON-serialisable dict.
    """
    if isinstance(mover, dict):
        return dict(mover)
    return {
        "symbol": getattr(mover, "symbol", ""),
        "ltp": getattr(mover, "ltp", 0.0),
        "change_pct": getattr(mover, "change_pct", 0.0),
        "turnover": getattr(mover, "turnover", 0.0),
        "volume": getattr(mover, "volume", 0),
    }


def summary_to_dict(summary: Any) -> dict[str, Any]:
    """Serialize a MarketSummary dataclass to a dictionary.

    Args:
        summary: MarketSummary instance or dict-like.

    Returns:
        JSON-serialisable dict.
    """
    if isinstance(summary, dict):
        return dict(summary)
    return {
        "index": getattr(summary, "index", 0.0),
        "change": getattr(summary, "change", 0.0),
        "change_pct": getattr(summary, "change_pct", 0.0),
        "volume": getattr(summary, "volume", 0),
        "turnover": getattr(summary, "turnover", 0.0),
        "advances": getattr(summary, "advances", 0),
        "declines": getattr(summary, "declines", 0),
        "unchanged": getattr(summary, "unchanged", 0),
        "status": getattr(summary, "status", "Unknown"),
        "timestamp": _iso(getattr(summary, "timestamp", None)),
    }
