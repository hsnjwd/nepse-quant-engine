"""Trade Recommendation Engine for the NEPSE Quant Engine.

Orchestrates the strategy execution, signal aggregation, position sizing,
and capital allocation pipeline into a single unified recommendation for
a given symbol.

This module deliberately avoids duplicating business logic already
implemented inside:

- :class:`~src.engine.analyzer.Analyzer`
- :class:`~src.strategies.manager.StrategyManager`
- :class:`~src.strategies.aggregator.StrategyAggregator`
- :class:`~src.risk.engine.RiskEngine`
- :class:`~src.portfolio.allocator.PortfolioAllocator`

It simply coordinates them and returns one unified recommendation.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from src.logging.logger import logger
from src.strategies.aggregator import StrategyAggregator
from src.strategies.manager import StrategyManager
from src.risk.engine import RiskEngine
from src.portfolio.allocator import PortfolioAllocator


# ---------------------------------------------------------------------------
# TradeRecommendation — output dataclass
# ---------------------------------------------------------------------------


@dataclass
class TradeRecommendation:
    """Unified trade recommendation for a single stock.

    Aggregates output from the strategy, risk, and portfolio subsystems
    into a single self-contained result.

    Attributes:
        symbol:
            Stock ticker symbol.
        signal:
            Consensus signal (``BUY``, ``SELL``, or ``HOLD``).
        confidence:
            Consensus confidence percentage (0–100).
        average_score:
            Mean score across all strategy signals.
        entry_price:
            Planned entry price per share, or ``None`` for non-BUY.
        stop_loss:
            Planned stop-loss price per share, or ``None`` for non-BUY.
        targets:
            List of target price levels from the consensus.
        shares:
            Number of shares to trade (0 for non-BUY).
        position_value:
            Total rupee value of the position (0 for non-BUY).
        capital_at_risk:
            Rupee amount at risk in this position (0 for non-BUY).
        portfolio_weight:
            Portfolio weight allocated to this position (0–1, 0 for
            non-BUY).
        strategy_votes:
            Vote counts keyed by signal label (``BUY``, ``SELL``,
            ``HOLD``).
        winning_strategies:
            Names of strategies whose vote matched the final signal.
        strategy_signals:
            Full list of raw per-strategy signal payloads for
            traceability.
        reasons:
            Human-readable explanation strings.
        metadata:
            Additional metadata (e.g., generator version, error info).
    """

    symbol: str
    signal: str
    confidence: float
    average_score: float

    entry_price: float | None = None
    stop_loss: float | None = None
    targets: list[float] = field(default_factory=list)

    shares: int = 0
    position_value: float = 0.0
    capital_at_risk: float = 0.0
    portfolio_weight: float = 0.0

    strategy_votes: dict[str, int] = field(default_factory=dict)
    winning_strategies: list[str] = field(default_factory=list)

    strategy_signals: list[dict[str, Any]] = field(default_factory=list)

    reasons: list[str] = field(default_factory=list)

    metadata: dict[str, Any] = field(default_factory=dict)

    # ------------------------------------------------------------------
    # Serialisation
    # ------------------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serialisable dictionary representation.

        Returns:
            Dictionary with the same fields as the dataclass, ready for
            API output or logging.
        """
        return {
            "symbol": self.symbol,
            "signal": self.signal,
            "confidence": self.confidence,
            "average_score": self.average_score,
            "entry_price": self.entry_price,
            "stop_loss": self.stop_loss,
            "targets": list(self.targets),
            "shares": self.shares,
            "position_value": self.position_value,
            "capital_at_risk": self.capital_at_risk,
            "portfolio_weight": self.portfolio_weight,
            "strategy_votes": dict(self.strategy_votes),
            "winning_strategies": list(self.winning_strategies),
            "strategy_signals": list(self.strategy_signals),
            "reasons": list(self.reasons),
            "metadata": dict(self.metadata),
        }


# ---------------------------------------------------------------------------
# RecommendationEngine
# ---------------------------------------------------------------------------


class RecommendationEngine:
    """Orchestrate the full recommendation pipeline.

    Coordinates the strategy framework, risk engine, and portfolio
    allocator to produce a single unified :class:`TradeRecommendation`
    for a given symbol.

    Pipeline::

        Strategies  →  Aggregator  →  Risk Engine  →  Allocator
            │              │               │              │
            └── signals ───┘               │              │
                             └── consensus ┘              │
                                           └── risk pos. ─┘
                                                      └── allocation
                                                                │
                                                    TradeRecommendation

    Args:
        capital:
            Total portfolio capital in NPR (must be > 0).
        risk_per_trade_pct:
            Fraction of capital to risk on a single trade, as a decimal
            (e.g. ``0.02`` = 2%).  Must be in ``(0, 1]``.
        max_position_weight:
            Maximum allowed portfolio weight for any single position
            (e.g. ``0.20`` = 20%).  Must be in ``(0, 1]``.
    """

    def __init__(
        self,
        capital: float,
        risk_per_trade_pct: float = 0.02,
        max_position_weight: float = 0.20,
    ) -> None:
        self._manager = StrategyManager()
        self._risk_engine = RiskEngine(
            capital=capital,
            risk_per_trade_pct=risk_per_trade_pct,
        )
        self._allocator = PortfolioAllocator(
            capital=capital,
            max_position_weight=max_position_weight,
        )

        logger.debug(
            "RecommendationEngine(capital=%.2f, risk_per_trade=%.4f, "
            "max_weight=%.4f)",
            capital,
            risk_per_trade_pct,
            max_position_weight,
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def recommend(
        self,
        symbol: str,
        df: Any,
    ) -> TradeRecommendation:
        """Generate a complete trade recommendation for a single symbol.

        The full pipeline:

        1. **Execute** — all registered strategies on *df*.
        2. **Aggregate** — combine signals via democratic voting.
        3. **Check** — if the consensus is not ``BUY``, return a
           ``HOLD`` recommendation with zero share / risk / allocation
           values.
        4. **Size** — if ``BUY``, calculate position size via the
           :class:`~src.risk.engine.RiskEngine`.
        5. **Allocate** — distribute capital via the
           :class:`~src.portfolio.allocator.PortfolioAllocator`.
        6. **Merge** — combine results from every stage into a single
           :class:`TradeRecommendation`.

        Args:
            symbol:
                Stock ticker symbol to recommend on.
            df:
                Historical OHLCV market data for the symbol.  Passed
                directly to :meth:`StrategyManager.evaluate_all`.

        Returns:
            A :class:`TradeRecommendation` with the full pipeline
            output.

        Raises:
            ValueError: If *symbol* is empty or not a string.
        """
        if not symbol or not isinstance(symbol, str):
            raise ValueError(
                f"Symbol must be a non-empty string, got {symbol!r}."
            )

        try:
            return self._recommend_impl(symbol, df)
        except Exception as exc:
            logger.exception(
                "Recommendation failed for '%s': %s",
                symbol,
                exc,
            )
            return TradeRecommendation(
                symbol=symbol,
                signal="HOLD",
                confidence=0.0,
                average_score=0.0,
                reasons=[f"Recommendation engine error: {exc}"],
                metadata={
                    "generated_by": "RecommendationEngine",
                    "error": str(exc),
                },
            )

    def recommend_to_dict(
        self,
        symbol: str,
        df: Any,
    ) -> dict[str, Any]:
        """Generate a recommendation and return it as a dictionary.

        Convenience wrapper around :meth:`recommend` that calls
        ``to_dict()`` on the result.

        Args:
            symbol:
                Stock ticker symbol.
            df:
                Historical OHLCV market data.

        Returns:
            JSON-serialisable recommendation dictionary.
        """
        return self.recommend(symbol, df).to_dict()

    # ------------------------------------------------------------------
    # Internal implementation
    # ------------------------------------------------------------------

    def _recommend_impl(
        self,
        symbol: str,
        df: Any,
    ) -> TradeRecommendation:
        """Core recommendation logic (separated for error-handling)."""
        # Step 1 — Execute all registered strategies.
        raw_signals: list[dict[str, Any]] = self._manager.evaluate_all(df)

        logger.debug(
            "Received %d raw signals for '%s'.",
            len(raw_signals),
            symbol,
        )

        # Step 2 — Aggregate signals via democratic voting.
        aggregator = StrategyAggregator()
        consensus = aggregator.aggregate(raw_signals)

        base_metadata: dict[str, Any] = {
            "generated_by": "RecommendationEngine",
        }

        # Step 3 — Non-BUY consensus → return HOLD with zero values.
        if consensus.signal != "BUY":
            logger.info(
                "Consensus for '%s' is %s — skipping allocation.",
                symbol,
                consensus.signal,
            )
            return TradeRecommendation(
                symbol=symbol,
                signal=consensus.signal,
                confidence=consensus.confidence,
                average_score=consensus.average_score,
                entry_price=None,
                stop_loss=None,
                targets=[],
                shares=0,
                position_value=0.0,
                capital_at_risk=0.0,
                portfolio_weight=0.0,
                strategy_votes=dict(consensus.votes),
                winning_strategies=list(consensus.winning_strategies),
                strategy_signals=list(consensus.strategy_signals),
                reasons=list(consensus.reasons),
                metadata=base_metadata,
            )

        # Step 3a — Validate that price and stop_loss are available.
        if consensus.price is None:
            logger.info(
                "BUY consensus for '%s' has no entry price — returning HOLD.",
                symbol,
            )
            return TradeRecommendation(
                symbol=symbol,
                signal="HOLD",
                confidence=consensus.confidence,
                average_score=consensus.average_score,
                entry_price=None,
                stop_loss=None,
                targets=list(consensus.targets),
                strategy_votes=dict(consensus.votes),
                winning_strategies=list(consensus.winning_strategies),
                strategy_signals=list(consensus.strategy_signals),
                reasons=[
                    *list(consensus.reasons),
                    "No entry price available for BUY signal.",
                ],
                metadata=base_metadata,
            )

        if consensus.stop_loss is None:
            logger.info(
                "BUY consensus for '%s' has no stop loss — returning HOLD.",
                symbol,
            )
            return TradeRecommendation(
                symbol=symbol,
                signal="HOLD",
                confidence=consensus.confidence,
                average_score=consensus.average_score,
                entry_price=consensus.price,
                stop_loss=None,
                targets=list(consensus.targets),
                strategy_votes=dict(consensus.votes),
                winning_strategies=list(consensus.winning_strategies),
                strategy_signals=list(consensus.strategy_signals),
                reasons=[
                    *list(consensus.reasons),
                    "No stop-loss price available for BUY signal.",
                ],
                metadata=base_metadata,
            )

        # Step 4 — Size the position via fixed-fraction risk management.
        risk_pos = self._risk_engine.calculate_position(
            symbol=symbol,
            entry_price=consensus.price,
            stop_loss=consensus.stop_loss,
        )

        # Step 5 — Allocate capital via equal-weight distribution.
        allocator_signal: dict[str, Any] = {
            "symbol": symbol,
            "signal": "BUY",
            "price": consensus.price,
        }
        allocations = self._allocator.allocate_equal_weight(
            [allocator_signal]
        )
        portfolio_weight: float = (
            allocations[0].weight if allocations else 0.0
        )

        logger.info(
            "Recommendation for '%s': %s, shares=%d, "
            "value=%.2f, weight=%.4f",
            symbol,
            consensus.signal,
            risk_pos.shares,
            risk_pos.position_value,
            portfolio_weight,
        )

        # Step 6 — Merge every stage into one recommendation.
        return TradeRecommendation(
            symbol=symbol,
            signal=consensus.signal,
            confidence=consensus.confidence,
            average_score=consensus.average_score,
            entry_price=consensus.price,
            stop_loss=consensus.stop_loss,
            targets=list(consensus.targets),
            shares=risk_pos.shares,
            position_value=risk_pos.position_value,
            capital_at_risk=risk_pos.capital_at_risk,
            portfolio_weight=portfolio_weight,
            strategy_votes=dict(consensus.votes),
            winning_strategies=list(consensus.winning_strategies),
            strategy_signals=list(consensus.strategy_signals),
            reasons=list(consensus.reasons),
            metadata=base_metadata,
        )
