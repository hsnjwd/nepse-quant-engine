"""Multi-strategy signal aggregator for the NEPSE Quant Engine.

Receives the list of signal payloads produced by :class:`StrategyManager`
and combines them into a single final recommendation via democratic voting,
score averaging, and consensus confidence calculation.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from statistics import fmean
from typing import Any

from src.logging.logger import logger

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

BUY = "BUY"
SELL = "SELL"
HOLD = "HOLD"

_VALID_SIGNALS = frozenset({BUY, SELL, HOLD})


# ---------------------------------------------------------------------------
# AggregatedSignal — output dataclass
# ---------------------------------------------------------------------------


@dataclass
class AggregatedSignal:
    """Normalised consensus output produced by :class:`StrategyAggregator`.

    Attributes:
        signal:
            Final consensus signal (``BUY``, ``SELL``, or ``HOLD``).
        confidence:
            Consensus confidence percentage (0–100), derived from vote share.
        average_score:
            Mean score across all strategy signals.
        votes:
            Raw vote counts keyed by signal label.
        buy_count:
            Number of strategies that voted ``BUY``.
        sell_count:
            Number of strategies that voted ``SELL``.
        hold_count:
            Number of strategies that voted ``HOLD``.
        total_strategies:
            Total number of strategy signals processed.
        winning_strategies:
            Names of strategies whose vote matches the final signal.
        price:
            Mean reference price across ``BUY`` signals, or ``None``.
        stop_loss:
            Mean stop-loss across ``BUY`` signals, or ``None``.
        targets:
            Element-wise mean of each target index across ``BUY`` signals.
        strategy_signals:
            The full list of input signal dictionaries for traceability.
        reasons:
            Human-readable explanation strings describing the consensus.
    """

    signal: str = HOLD
    confidence: float = 0.0
    average_score: float = 0.0
    votes: dict[str, int] = field(default_factory=lambda: {BUY: 0, SELL: 0, HOLD: 0})
    buy_count: int = 0
    sell_count: int = 0
    hold_count: int = 0
    total_strategies: int = 0
    winning_strategies: list[str] = field(default_factory=list)
    price: float | None = None
    stop_loss: float | None = None
    targets: list[float] = field(default_factory=list)
    strategy_signals: list[dict[str, Any]] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)

    # ------------------------------------------------------------------
    # Serialisation
    # ------------------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serialisable dictionary representation.

        Returns:
            Dictionary with the same keys as the dataclass fields,
            ready for API output or logging.
        """
        return {
            "signal": self.signal,
            "confidence": self.confidence,
            "average_score": self.average_score,
            "votes": dict(self.votes),
            "buy_count": self.buy_count,
            "sell_count": self.sell_count,
            "hold_count": self.hold_count,
            "total_strategies": self.total_strategies,
            "winning_strategies": list(self.winning_strategies),
            "price": self.price,
            "stop_loss": self.stop_loss,
            "targets": list(self.targets),
            "strategy_signals": list(self.strategy_signals),
            "reasons": list(self.reasons),
        }


# ---------------------------------------------------------------------------
# StrategyAggregator
# ---------------------------------------------------------------------------


class StrategyAggregator:
    """Combine multiple strategy signals into a single consensus.

    The aggregator uses simple majority voting to determine the final signal,
    averages scores and prices, and computes confidence based on the vote
    share of the winning decision.

    Usage::

        from src.strategies.aggregator import StrategyAggregator
        from src.strategies.manager import StrategyManager

        manager = StrategyManager()
        signals = manager.evaluate_all(df)

        aggregator = StrategyAggregator()
        consensus = aggregator.aggregate(signals)
        print(consensus.to_dict())
    """

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def aggregate(
        self,
        signals: list[dict[str, Any]],
    ) -> AggregatedSignal:
        """Aggregate a list of strategy signals into a single consensus.

        Args:
            signals:
                Signal dictionaries from
                :meth:`StrategyManager.evaluate_all`.  Each dict must
                contain at least the keys ``signal`` and ``strategy_name``.
                Signals that carry an ``error`` key are skipped.

        Returns:
            An :class:`AggregatedSignal` instance describing the consensus.
        """
        if not signals:
            logger.debug("aggregate() received an empty signal list; returning HOLD.")
            return AggregatedSignal()

        total = len(signals)
        logger.debug("Aggregating %d strategy signals.", total)

        # 1. Count votes (skip errored signals for vote tallying but keep
        #    them in strategy_signals for traceability).
        valid_signals: list[dict[str, Any]] = [
            s for s in signals if not self._has_error(s)
        ]
        valid_total = len(valid_signals)
        errored_signals: list[dict[str, Any]] = [
            s for s in signals if self._has_error(s)
        ]

        if not valid_signals:
            logger.info(
                "All %d signals errored; returning HOLD consensus.",
                total,
            )
            return AggregatedSignal(
                total_strategies=total,
                strategy_signals=list(signals),
                reasons=["All strategy signals contained errors."],
            )

        vote_counter: Counter[str] = Counter()
        for s in valid_signals:
            sig = self._get_signal(s)
            vote_counter[sig] += 1

        buy_votes = vote_counter.get(BUY, 0)
        sell_votes = vote_counter.get(SELL, 0)
        hold_votes = vote_counter.get(HOLD, 0)

        votes: dict[str, int] = {
            BUY: buy_votes,
            SELL: sell_votes,
            HOLD: hold_votes,
        }

        # 2. Determine final signal.
        final_signal = self._resolve_signal(buy_votes, sell_votes, hold_votes)

        # 3. Average score across all valid signals.
        scores = [self._get_score(s) for s in valid_signals]
        avg_score = round(fmean(scores), 2) if scores else 0.0

        # 4. Confidence = (max vote / total strategies) * 100.
        max_votes = max(buy_votes, sell_votes, hold_votes)
        confidence = (
            round((max_votes / valid_total) * 100, 2)
            if valid_total
            else 0.0
        )

        # 5. Price — mean of BUY signal prices.
        buy_signals = [s for s in valid_signals if self._get_signal(s) == BUY]
        buy_prices = [self._get_price(s) for s in buy_signals if self._get_price(s) is not None]
        avg_price = round(fmean(buy_prices), 2) if buy_prices else None  # type: ignore[arg-type]

        # 6. Stop loss — mean of BUY stop losses.
        buy_stops = [
            self._get_stop_loss(s)
            for s in buy_signals
            if self._get_stop_loss(s) is not None
        ]
        avg_stop = round(fmean(buy_stops), 2) if buy_stops else None  # type: ignore[arg-type]

        # 7. Targets — element-wise mean per index across BUY signals.
        avg_targets = self._average_targets(buy_signals)

        # 8. Winning strategies (those that voted for final_signal).
        winning_names = sorted(
            s.get("strategy_name", "unknown")
            for s in valid_signals
            if self._get_signal(s) == final_signal
        )

        # 9. Human-readable reasons.
        reasons = self._build_reasons(
            final_signal=final_signal,
            buy_votes=buy_votes,
            sell_votes=sell_votes,
            hold_votes=hold_votes,
            total=valid_total,
            avg_score=avg_score,
            confidence=confidence,
        )

        logger.info(
            "Aggregation complete: %s (%d/%d votes, confidence=%.1f%%)",
            final_signal,
            max_votes,
            valid_total,
            confidence,
        )

        return AggregatedSignal(
            signal=final_signal,
            confidence=confidence,
            average_score=avg_score,
            votes=votes,
            buy_count=buy_votes,
            sell_count=sell_votes,
            hold_count=hold_votes,
            total_strategies=total,
            winning_strategies=winning_names,
            price=avg_price,
            stop_loss=avg_stop,
            targets=avg_targets,
            strategy_signals=list(signals),
            reasons=reasons,
        )

    def aggregate_to_dict(
        self,
        signals: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Convenience method that returns the aggregation as a dictionary.

        Equivalent to ``aggregate(signals).to_dict()``.

        Args:
            signals:
                Signal dictionaries from :class:`StrategyManager`.

        Returns:
            JSON-serialisable consensus dictionary.
        """
        return self.aggregate(signals).to_dict()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _has_error(signal: dict[str, Any]) -> bool:
        """Return ``True`` if the signal payload contains an error."""
        return bool(signal.get("error"))

    @staticmethod
    def _get_signal(signal: dict[str, Any]) -> str:
        """Return the signal value, defaulting to ``HOLD``."""
        sig = signal.get("signal", HOLD)
        return sig if sig in _VALID_SIGNALS else HOLD

    @staticmethod
    def _get_score(signal: dict[str, Any]) -> float:
        """Return the numeric score, defaulting to ``0.0``."""
        val = signal.get("score")
        if val is None:
            return 0.0
        try:
            return float(val)
        except (TypeError, ValueError):
            return 0.0

    @staticmethod
    def _get_price(signal: dict[str, Any]) -> float | None:
        """Return the reference price, or ``None``."""
        val = signal.get("price")
        if val is None:
            return None
        try:
            return float(val)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _get_stop_loss(signal: dict[str, Any]) -> float | None:
        """Return the stop-loss value, or ``None``."""
        val = signal.get("stop_loss")
        if val is None:
            return None
        try:
            return float(val)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _get_targets(signal: dict[str, Any]) -> list[float]:
        """Return the targets list, falling back to the legacy ``target1`` key."""
        targets = signal.get("targets")
        if isinstance(targets, list) and targets:
            result: list[float] = []
            for t in targets:
                try:
                    result.append(float(t))
                except (TypeError, ValueError):
                    pass
            return result

        # Fallback: legacy single-target field.
        t1 = signal.get("target1")
        if t1 is not None:
            try:
                return [float(t1)]
            except (TypeError, ValueError):
                pass

        return []

    @staticmethod
    def _resolve_signal(
        buy_votes: int,
        sell_votes: int,
        hold_votes: int,
    ) -> str:
        """Resolve the final signal from vote counts using majority rule.

        Returns:
            ``BUY`` if buy votes outnumber both sell and hold.
            ``SELL`` if sell votes outnumber both buy and hold.
            ``HOLD`` otherwise.
        """
        if buy_votes > sell_votes and buy_votes > hold_votes:
            return BUY
        if sell_votes > buy_votes and sell_votes > hold_votes:
            return SELL
        return HOLD

    @staticmethod
    def _average_targets(buy_signals: list[dict[str, Any]]) -> list[float]:
        """Compute element-wise mean of target lists across BUY signals.

        For each target index (0, 1, …), the values from every BUY signal
        that has that index are averaged.  Missing indices are silently
        skipped so that a signal with 2 targets does not break the average
        of a signal with 3 targets.

        Args:
            buy_signals:
                Signal dicts whose ``signal`` field is ``BUY``.

        Returns:
            A list of mean target prices, one per index that had at least
            one contributing value.
        """
        target_lists: list[list[float]] = [
            StrategyAggregator._get_targets(s) for s in buy_signals
        ]

        if not target_lists:
            return []

        # Determine the maximum target index present across all signals.
        max_len = max((len(tl) for tl in target_lists), default=0)
        if max_len == 0:
            return []

        averages: list[float] = []
        for idx in range(max_len):
            values_at_idx = [
                tl[idx] for tl in target_lists if idx < len(tl)
            ]
            if values_at_idx:
                avg = round(fmean(values_at_idx), 2)
                averages.append(avg)

        return averages

    @staticmethod
    def _build_reasons(
        final_signal: str,
        buy_votes: int,
        sell_votes: int,
        hold_votes: int,
        total: int,
        avg_score: float,
        confidence: float,
    ) -> list[str]:
        """Build a list of human-readable explanation strings.

        Args:
            final_signal:
                The resolved consensus signal.
            buy_votes:
                Number of ``BUY`` votes.
            sell_votes:
                Number of ``SELL`` votes.
            hold_votes:
                Number of ``HOLD`` votes.
            total:
                Total number of strategies (including errored ones).
            avg_score:
                Mean score across all valid strategies.
            confidence:
                Consensus confidence percentage.

        Returns:
            A list of descriptive sentences explaining the result.
        """
        reasons: list[str] = []

        reasons.append(
            f"{buy_votes} strategy{'s' if buy_votes != 1 else ''} voted BUY, "
            f"{sell_votes} voted SELL, "
            f"{hold_votes} voted HOLD."
        )
        reasons.append(f"Final consensus: {final_signal}.")
        reasons.append(f"Average score: {avg_score}.")
        reasons.append(f"Consensus confidence: {confidence}%.")

        # Optional nuance: unanimous / near-unanimous.
        winning = max(buy_votes, sell_votes, hold_votes)
        if winning == total:
            reasons.append("Unanimous decision — all strategies agree.")
        elif winning >= total * 0.75:
            reasons.append("Strong majority supports this signal.")

        return reasons
