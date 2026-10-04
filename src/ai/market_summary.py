"""Daily market summary generator for the AI Strategy Advisor.

Transforms market summary data (index, breadth, top movers, regime)
into natural-language daily commentary.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger("nepse.ai.market_summary")


@dataclass
class MarketCommentary:
    """Natural-language daily market summary.

    Attributes:
        headline: One-line market headline.
        paragraphs: Ordered list of summary paragraphs.
        sentiment: Bullish / Bearish / Neutral.
        key_levels: Optional key index levels.
        generated_at: ISO timestamp of generation.
    """

    headline: str = ""
    paragraphs: list[str] = field(default_factory=list)
    sentiment: str = "Neutral"
    key_levels: dict[str, float] = field(default_factory=dict)
    generated_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serialisable dictionary."""
        return {
            "headline": self.headline,
            "paragraphs": self.paragraphs,
            "sentiment": self.sentiment,
            "key_levels": self.key_levels,
            "generated_at": self.generated_at,
        }


class MarketSummaryGenerator:
    """Generate daily market commentary from market data.

    Usage::

        generator = MarketSummaryGenerator()
        commentary = generator.generate(summary, top_gainers, top_losers, regime)
    """

    def generate(
        self,
        summary: Any,
        top_gainers: list[Any] | None = None,
        top_losers: list[Any] | None = None,
        regime: str | None = None,
    ) -> MarketCommentary:
        """Compose the daily market summary.

        Args:
            summary: Market summary object with ``index``, ``change``,
                ``change_pct``, ``volume``, ``turnover``, ``advances``,
                ``declines``, ``unchanged`` attributes.
            top_gainers: Optional list of top mover objects/dicts.
            top_losers: Optional list of top mover objects/dicts.
            regime: Optional market regime label.

        Returns:
            A :class:`MarketCommentary`.
        """
        from datetime import datetime, timezone

        index = self._attr(summary, "index", 0.0)
        change = self._attr(summary, "change", 0.0)
        change_pct = self._attr(summary, "change_pct", 0.0)
        advances = self._attr(summary, "advances", 0)
        declines = self._attr(summary, "declines", 0)
        unchanged = self._attr(summary, "unchanged", 0)

        sentiment = self._classify_sentiment(change_pct, advances, declines)
        headline = self._build_headline(index, change, change_pct, sentiment)

        paragraphs = [
            f"The NEPSE index closed at {index:,.2f}, "
            f"{'up' if change >= 0 else 'down'} {abs(change):,.2f} "
            f"({change_pct:+.2f}%) on the day."
        ]

        if advances or declines:
            paragraphs.append(
                f"Market breadth shows {advances} advancing stocks, "
                f"{declines} declining and {unchanged} unchanged."
            )

        if top_gainers:
            names = [self._mover_name(g) for g in top_gainers[:3]]
            if names:
                paragraphs.append(f"Top gainers: {', '.join(names)}.")

        if top_losers:
            names = [self._mover_name(g) for g in top_losers[:3]]
            if names:
                paragraphs.append(f"Top losers: {', '.join(names)}.")

        if regime:
            paragraphs.append(
                f"The market is currently in a {regime.upper()} regime."
            )

        levels: dict[str, float] = {}
        if index:
            levels["index"] = round(index, 2)
            if change_pct:
                levels["support_approx"] = round(index * (1 - 0.02), 2)
                levels["resistance_approx"] = round(index * (1 + 0.02), 2)

        commentary = MarketCommentary(
            headline=headline,
            paragraphs=paragraphs,
            sentiment=sentiment,
            key_levels=levels,
            generated_at=datetime.now(timezone.utc).isoformat(),
        )
        logger.info("Generated market commentary (sentiment=%s).", sentiment)
        return commentary

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    @staticmethod
    def _attr(obj: Any, name: str, default: Any) -> Any:
        """Read an attribute from an object or dict-like payload."""
        if obj is None:
            return default
        if isinstance(obj, dict):
            return obj.get(name, default)
        return getattr(obj, name, default)

    @staticmethod
    def _classify_sentiment(
        change_pct: float,
        advances: Any,
        declines: Any,
    ) -> str:
        """Classify market sentiment from change and breadth."""
        if change_pct > 0.3 and advances > declines:
            return "Bullish"
        if change_pct < -0.3 and declines > advances:
            return "Bearish"
        return "Neutral"

    @staticmethod
    def _build_headline(
        index: float,
        change: float,
        change_pct: float,
        sentiment: str,
    ) -> str:
        """Compose the one-line headline."""
        direction = "gains" if change >= 0 else "losses"
        return (
            f"NEPSE index {direction} to {index:,.2f} "
            f"({change_pct:+.2f}%) — {sentiment} tone."
        )

    @staticmethod
    def _mover_name(mover: Any) -> str:
        """Extract a symbol name from a mover object/dict."""
        if isinstance(mover, dict):
            return str(mover.get("symbol", mover.get("ticker", "?")))
        return str(getattr(mover, "symbol", getattr(mover, "ticker", "?")))
