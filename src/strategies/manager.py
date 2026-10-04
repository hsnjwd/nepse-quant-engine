"""Multi-strategy execution manager for the NEPSE Quant Engine.

Executes every registered strategy against a shared market DataFrame while
isolating failures so that one broken strategy cannot stop the execution of
the remaining strategies.
"""

from __future__ import annotations

from typing import Any

from src.logging.logger import logger
from src.strategies.aggregator import StrategyAggregator
from src.strategies.base import BaseStrategy
from src.strategies.registry import registry


class StrategyManager:
    """Execute registered trading strategies."""

    def __init__(self) -> None:
        """Initialize the strategy manager."""
        self._registry = registry()
        self._aggregator = StrategyAggregator()

    def evaluate_all(self, df: Any) -> list[dict[str, Any]]:
        """Execute all registered strategies.

        Args:
            df:
                Historical OHLCV market data.

        Returns:
            List of standardized signal payloads.
        """
        signals: list[dict[str, Any]] = []

        success_count = 0
        failure_count = 0

        for strategy in self._registry:
            try:
                payload = strategy.generate_signal(df)

                if not isinstance(payload, dict):
                    raise TypeError(
                        f"{strategy.name} returned "
                        f"{type(payload).__name__}, expected dict."
                    )

                signals.append(payload)
                success_count += 1

                logger.debug(
                    "Strategy '%s' -> %s (score=%s)",
                    strategy.name,
                    payload.get("signal"),
                    payload.get("score"),
                )

            except Exception as exc:
                failure_count += 1

                logger.exception(
                    "Strategy '%s' failed: %s",
                    strategy.name,
                    exc,
                )

                signals.append(
                    self._build_error_signal(
                        strategy=strategy,
                        error=str(exc),
                    )
                )

        logger.info(
            "StrategyManager executed %d strategies (%d succeeded, %d failed).",
            len(signals),
            success_count,
            failure_count,
        )

        return signals

    def evaluate_single(
        self,
        strategy_name: str,
        df: Any,
    ) -> dict[str, Any]:
        """Execute one registered strategy.

        Args:
            strategy_name:
                Registered strategy name.

            df:
                Historical OHLCV data.

        Returns:
            Standardized signal payload.
        """
        try:
            strategy = self._registry.get(strategy_name)

        except KeyError as exc:
            logger.error(str(exc))

            return {
                "strategy_name": strategy_name,
                "strategy_version": None,
                "signal": "HOLD",
                "price": None,
                "stop_loss": None,
                "targets": [],
                "score": 0.0,
                "details": {},
                "error": str(exc),
            }

        try:
            payload = strategy.generate_signal(df)

            if not isinstance(payload, dict):
                raise TypeError(
                    f"{strategy.name} returned "
                    f"{type(payload).__name__}, expected dict."
                )

            return payload

        except Exception as exc:
            logger.exception(
                "Strategy '%s' failed: %s",
                strategy.name,
                exc,
            )

            return self._build_error_signal(
                strategy,
                str(exc),
            )

    def evaluate_and_aggregate(self, df: Any) -> dict[str, Any]:
        """Execute all strategies and aggregate results into a consensus.

        Combines :meth:`evaluate_all` and the :class:`StrategyAggregator`
        into a single call for convenience.  The raw per-strategy signals
        are included alongside the aggregated consensus.

        Args:
            df:
                Historical OHLCV market data.

        Returns:
            A dictionary with two keys:

            - **signals**:  List of raw signal payloads from each strategy.
            - **aggregate**: Consensus dictionary from the aggregator.
        """
        signals: list[dict[str, Any]] = self.evaluate_all(df)
        consensus = self._aggregator.aggregate(signals)

        logger.info(
            "Consensus generated: signal=%s confidence=%.2f%% strategies=%d",
            consensus.signal,
            consensus.confidence,
            len(signals),
        )

        return {
            "signals": signals,
            "aggregate": consensus.to_dict(),
        }

    def available_strategies(self) -> list[str]:
        """Return registered strategy names."""
        return self._registry.list_strategies()

    def strategy_metadata(self) -> list[dict[str, str]]:
        """Return metadata for all registered strategies."""
        return self._registry.strategy_info()

    @staticmethod
    def _build_error_signal(
        strategy: BaseStrategy,
        error: str,
    ) -> dict[str, Any]:
        """Create a fallback signal for a failed strategy.

        Args:
            strategy:
                Strategy that failed.

            error:
                Exception message.

        Returns:
            Standardized HOLD signal.
        """
        return {
            "strategy_name": strategy.name,
            "strategy_version": strategy.version,
            "signal": "HOLD",
            "price": None,
            "stop_loss": None,
            "targets": [],
            "score": 0.0,
            "details": {},
            "error": error,
        }
