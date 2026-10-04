"""Adaptive Strategy Engine for the NEPSE Quant Engine.

Automatically selects, enables, disables, or weights trading strategies
based on the detected market regime.

Integrates with:

- :class:`~src.strategies.manager.StrategyManager`
- :class:`~src.strategies.aggregator.StrategyAggregator`
- :class:`~src.recommendations.engine.RecommendationEngine`
- :class:`~src.portfolio.optimizer.PortfolioOptimizer`
- :class:`~src.risk.engine.RiskEngine`
- :class:`~src.regime.detector.MarketRegimeDetector`

without modifying any of their public APIs.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from src.logging.logger import logger

# ======================================================================
# Constants — default strategy-to-regime mapping
# ======================================================================

_REGIME_LABELS: list[str] = [
    "BULL",
    "BEAR",
    "SIDEWAYS",
    "HIGH_VOLATILITY",
    "LOW_VOLATILITY",
    "ACCUMULATION",
    "DISTRIBUTION",
    "RECOVERY",
    "PANIC",
    "OVERHEATED",
]

_DEFAULT_STRATEGY_MAPPING: dict[str, dict[str, list[str]]] = {
    "BULL": {
        "prefer": ["Momentum", "Breakout"],
        "reduce": ["MeanReversion"],
        "disable": [],
    },
    "BEAR": {
        "prefer": ["Defensive", "MeanReversion"],
        "reduce": [],
        "disable": ["Breakout"],
    },
    "SIDEWAYS": {
        "prefer": ["MeanReversion"],
        "reduce": ["Momentum"],
        "disable": [],
    },
    "HIGH_VOLATILITY": {
        "prefer": ["Defensive", "VolatilityAware"],
        "reduce": ["Breakout"],
        "disable": [],
    },
    "LOW_VOLATILITY": {
        "prefer": ["Momentum", "Breakout"],
        "reduce": [],
        "disable": [],
    },
    "ACCUMULATION": {
        "prefer": ["Momentum", "Volume"],
        "reduce": [],
        "disable": [],
    },
    "DISTRIBUTION": {
        "prefer": ["Exit", "MeanReversion"],
        "reduce": ["Momentum"],
        "disable": [],
    },
    "RECOVERY": {
        "prefer": ["Breakout", "Momentum"],
        "reduce": [],
        "disable": [],
    },
    "PANIC": {
        "prefer": ["Defensive"],
        "reduce": [],
        "disable": ["Momentum", "Breakout"],
    },
    "OVERHEATED": {
        "prefer": ["Defensive", "MeanReversion"],
        "reduce": ["Momentum"],
        "disable": [],
    },
}

# Base weights used when no specific strategy mapping exists
_BASE_WEIGHT = 1.0
_PREFERRED_MULTIPLIER = 2.0
_REDUCED_MULTIPLIER = 0.5

_CONFIDENCE_MIN = 0.0
_CONFIDENCE_MAX = 100.0


# ======================================================================
# AdaptiveDecision — output dataclass
# ======================================================================


@dataclass
class AdaptiveDecision:
    """Result of an adaptive strategy selection decision.

    Attributes:
        market_regime:
            The detected market regime (e.g. ``"BULL"``, ``"BEAR"``).
        selected_strategies:
            List of strategy names that are recommended for the current
            regime.
        disabled_strategies:
            List of strategy names that should be deactivated.
        strategy_weights:
            Dictionary mapping strategy name to its normalised weight
            (summing to 1.0).
        confidence:
            Overall confidence of the adaptive decision (0–100).
        reasons:
            Human-readable explanation strings.
        metadata:
            Additional metadata (source, mapping_version, etc.).
    """

    market_regime: str = "UNKNOWN"
    selected_strategies: list[str] = field(default_factory=list)
    disabled_strategies: list[str] = field(default_factory=list)
    strategy_weights: dict[str, float] = field(default_factory=dict)
    confidence: float = 0.0
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
            "market_regime": self.market_regime,
            "selected_strategies": list(self.selected_strategies),
            "disabled_strategies": list(self.disabled_strategies),
            "strategy_weights": {
                k: round(v, 4) for k, v in self.strategy_weights.items()
            },
            "confidence": round(self.confidence, 2),
            "reasons": list(self.reasons),
            "metadata": dict(self.metadata),
        }

    def __repr__(self) -> str:
        return (
            f"{self.__class__.__name__}("
            f"regime={self.market_regime!r}, "
            f"selected={len(self.selected_strategies)}, "
            f"confidence={self.confidence:.1f})"
        )


# ======================================================================
# AdaptiveStrategyEngine
# ======================================================================


class AdaptiveStrategyEngine:
    """Select and weight trading strategies based on market regime.

    The engine uses a configurable strategy-to-regime mapping to decide
    which strategies to prefer, reduce, or disable for each detected
    market regime.

    Usage::

        from src.adaptive.engine import AdaptiveStrategyEngine
        from src.regime.detector import MarketRegimeDetector

        detector = MarketRegimeDetector()
        adaptive = AdaptiveStrategyEngine()

        regime = detector.detect(df)
        decision = adaptive.select_strategies(regime.regime)
        print(decision.to_dict())
    """

    # ------------------------------------------------------------------
    # Constructor
    # ------------------------------------------------------------------

    def __init__(
        self,
        strategy_mapping: dict[str, dict[str, list[str]]] | None = None,
    ) -> None:
        """Initialise the adaptive strategy engine.

        Args:
            strategy_mapping:
                Optional custom mapping from regime names to strategy
                preferences.  Each entry must have ``prefer``,
                ``reduce``, and ``disable`` keys, each mapping to a
                list of strategy name substrings.  If omitted, the
                built-in default mapping is used.
        """
        self._mapping: dict[str, dict[str, list[str]]] = (
            strategy_mapping if strategy_mapping is not None
            else _DEFAULT_STRATEGY_MAPPING
        )

        logger.debug(
            "AdaptiveStrategyEngine initialised with %d regime mappings.",
            len(self._mapping),
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def select_strategies(
        self,
        regime: str | None,
        all_strategies: list[str] | None = None,
    ) -> AdaptiveDecision:
        """Select and weight strategies for a given market regime.

        Args:
            regime:
                Detected market regime label (e.g. ``"BULL"``,
                ``"BEAR"``).  ``None`` is treated as ``"UNKNOWN"``.
            all_strategies:
                Optional list of all available strategy names.  When
                omitted, the engine extracts strategy references from
                the mapping and assumes those are all that exist.

        Returns:
            An :class:`AdaptiveDecision` with selected strategies,
            disabled strategies, normalised weights, confidence, and
            reasons.
        """
        logger.info(
            "Adaptive strategy selection for regime: %s.",
            regime,
        )

        # Guard against None regime — treat as unknown
        if regime is None:
            regime = "UNKNOWN"

        regime_upper = regime.strip().upper()

        # Get the mapping for this regime
        mapping = self._get_mapping(regime_upper)

        # Extract preferred / reduced / disabled strategy name parts
        preferred_parts: list[str] = mapping.get("prefer", [])
        reduced_parts: list[str] = mapping.get("reduce", [])
        disabled_parts: list[str] = mapping.get("disable", [])

        # Determine the universe of strategy names
        if isinstance(all_strategies, list):
            strategy_names = list(all_strategies)
        else:
            strategy_names = self._extract_strategy_names(mapping)

        # Match registry names against mapping parts
        preferred = self._match_strategies(strategy_names, preferred_parts)
        reduced = self._match_strategies(strategy_names, reduced_parts)
        disabled = self._match_strategies(strategy_names, disabled_parts)

        # Compute which strategies are selected (not disabled)
        selected = [s for s in strategy_names if s not in disabled]

        # Calculate normalised weights
        weights = self._calculate_weights(
            strategy_names=strategy_names,
            preferred=preferred,
            reduced=reduced,
            disabled=disabled,
        )

        # Calculate confidence
        confidence = self._calculate_confidence(
            regime=regime_upper,
            selected_count=len(selected),
            total_count=len(strategy_names),
        )

        # Build reasons
        reasons = self._build_reasons(
            regime=regime_upper,
            selected=selected,
            disabled=disabled,
            preferred=preferred,
            reduced=reduced,
            weights=weights,
            confidence=confidence,
        )

        logger.info(
            "Adaptive decision: regime=%s, selected=%d, disabled=%d, "
            "confidence=%.1f%%.",
            regime_upper,
            len(selected),
            len(disabled),
            confidence,
        )

        return AdaptiveDecision(
            market_regime=regime_upper,
            selected_strategies=selected,
            disabled_strategies=disabled,
            strategy_weights=weights,
            confidence=confidence,
            reasons=reasons,
            metadata={
                "generated_by": "AdaptiveStrategyEngine",
            },
        )

    def select_with_signals(
        self,
        regime: str | None,
        strategies: list[dict[str, Any]],
    ) -> AdaptiveDecision:
        """Select and weight strategies using actual signal payloads.

        Each element in *strategies* should be a signal dictionary as
        produced by :class:`~src.strategies.manager.StrategyManager`.
        The engine extracts the ``strategy_name`` from each valid
        (non-errored) signal and uses that as the list of all
        available strategies.  Errored signals (those containing an
        ``error`` key) are excluded from strategy extraction.

        Args:
            regime:
                Detected market regime label.  ``None`` is treated as
                ``"UNKNOWN"``.
            strategies:
                List of strategy signal dictionaries.  Each must
                contain a ``strategy_name`` key.

        Returns:
            An :class:`AdaptiveDecision` with selected strategies,
            weights, confidence, and reasons computed from the actual
            signal data.
        """
        # Filter out errored signals (matching StrategyAggregator pattern)
        if not strategies:
            return self.select_strategies(regime=regime, all_strategies=[])

        valid_signals = [
            s for s in strategies if not s.get("error")
        ]
        strategy_names = [
            s.get("strategy_name", f"unknown_{i}")
            for i, s in enumerate(valid_signals)
        ]
        return self.select_strategies(
            regime=regime,
            all_strategies=strategy_names,
        )

    def calculate_strategy_weights(
        self,
        regime: str,
        strategy_names: list[str] | None = None,
    ) -> dict[str, float]:
        """Calculate normalised strategy weights for a regime.

        This is a convenience method that returns only the weight
        dictionary without the full decision context.

        Args:
            regime:
                Detected market regime label.
            strategy_names:
                Optional list of strategy names.  If omitted, names are
                extracted from the mapping.

        Returns:
            Dictionary mapping strategy name to normalised weight
            (summing to 1.0).
        """
        decision = self.select_strategies(
            regime=regime,
            all_strategies=strategy_names,
        )
        return dict(decision.strategy_weights)

    # ------------------------------------------------------------------
    # Internal — mapping
    # ------------------------------------------------------------------

    def _get_mapping(
        self,
        regime: str,
    ) -> dict[str, list[str]]:
        """Retrieve the strategy mapping for a regime.

        Falls back to the default mapping (BULL) if the regime is
        unknown.

        Args:
            regime:
                Upper-case regime label.

        Returns:
            Dictionary with ``prefer``, ``reduce``, and ``disable``
            keys.
        """
        if regime in self._mapping:
            return self._mapping[regime]

        logger.warning(
            "Unknown regime '%s' — falling back to equal-weight default.",
            regime,
        )

        return {
            "prefer": [],
            "reduce": [],
            "disable": [],
        }

    @staticmethod
    def _extract_strategy_names(
        mapping: dict[str, list[str]],
    ) -> list[str]:
        """Extract all unique strategy name parts from a mapping.

        Args:
            mapping:
                Regime mapping dictionary with ``prefer``, ``reduce``,
                and ``disable`` keys.

        Returns:
            Sorted list of unique strategy name parts.
        """
        names: set[str] = set()
        for key in ("prefer", "reduce", "disable"):
            names.update(mapping.get(key, []))
        return sorted(names)

    # ------------------------------------------------------------------
    # Internal — matching
    # ------------------------------------------------------------------

    @staticmethod
    def _match_strategies(
        strategy_names: list[str],
        name_parts: list[str],
    ) -> list[str]:
        """Match full strategy names against name part substrings.

        A strategy name matches a part if the part is a case-insensitive
        substring of the strategy name.  For example, ``"Momentum"``
        would match ``"MomentumStrategy"``.

        Args:
            strategy_names:
                Full registered strategy names.
            name_parts:
                Substring parts to match against.

        Returns:
            List of matching strategy names.
        """
        matched: list[str] = []
        for part in name_parts:
            part_lower = part.strip().lower()
            for name in strategy_names:
                if part_lower in name.strip().lower():
                    if name not in matched:
                        matched.append(name)
        return matched

    # ------------------------------------------------------------------
    # Internal — weights
    # ------------------------------------------------------------------

    @staticmethod
    def _calculate_weights(
        strategy_names: list[str],
        preferred: list[str],
        reduced: list[str],
        disabled: list[str],
    ) -> dict[str, float]:
        """Calculate normalised strategy weights.

        Base weight is 1.0.  Preferred strategies get a multiplier of
        2.0.  Reduced strategies get a multiplier of 0.5.  Disabled
        strategies get weight 0.0.  Weights are then normalised to sum
        to 1.0.

        Args:
            strategy_names:
                All strategy names.
            preferred:
                Strategies to receive extra weight.
            reduced:
                Strategies to receive reduced weight.
            disabled:
                Strategies to receive zero weight.

        Returns:
            Dictionary of normalised weights summing to 1.0.
        """
        if not strategy_names:
            return {}

        raw: dict[str, float] = {}
        for name in strategy_names:
            if name in disabled:
                raw[name] = 0.0
            elif name in preferred:
                raw[name] = _BASE_WEIGHT * _PREFERRED_MULTIPLIER
            elif name in reduced:
                raw[name] = _BASE_WEIGHT * _REDUCED_MULTIPLIER
            else:
                raw[name] = _BASE_WEIGHT

        total = sum(raw.values())
        if total <= 0:
            # All disabled or zero — fall back to equal weight
            n = len(strategy_names)
            return {name: 1.0 / n for name in strategy_names} if n else {}

        return {name: v / total for name, v in raw.items()}

    # ------------------------------------------------------------------
    # Internal — confidence
    # ------------------------------------------------------------------

    @staticmethod
    def _calculate_confidence(
        regime: str,
        selected_count: int,
        total_count: int,
    ) -> float:
        """Calculate the confidence of the adaptive decision.

        Factors:

        - Known regimes get a base confidence of 70.
        - Unknown regimes start at 30.
        - More selected strategies (relative to total) increases
          confidence.
        - The score is clamped to [0, 100].

        Args:
            regime:
                The detected regime label.
            selected_count:
                Number of strategies selected (not disabled).
            total_count:
                Total number of strategies available.

        Returns:
            Confidence score between 0 and 100.
        """
        if total_count == 0:
            return 0.0

        base = 70.0 if regime in _REGIME_LABELS else 30.0

        ratio = selected_count / total_count
        coverage_bonus = ratio * 20.0

        raw = base + coverage_bonus
        return max(_CONFIDENCE_MIN, min(_CONFIDENCE_MAX, raw))

    # ------------------------------------------------------------------
    # Internal — reasons
    # ------------------------------------------------------------------

    @staticmethod
    def _build_reasons(
        regime: str,
        selected: list[str],
        disabled: list[str],
        preferred: list[str],
        reduced: list[str],
        weights: dict[str, float],
        confidence: float,
    ) -> list[str]:
        """Build human-readable reasons for the adaptive decision.

        Args:
            regime:
                Detected market regime.
            selected:
                Strategies that were selected.
            disabled:
                Strategies that were disabled.
            preferred:
                Strategies that received a weight bonus.
            reduced:
                Strategies that received a weight reduction.
            weights:
                Final normalised weight dictionary.
            confidence:
                Decision confidence percentage.

        Returns:
            List of human-readable explanation strings.
        """
        reasons: list[str] = []

        if regime in _REGIME_LABELS:
            reasons.append(f"{regime} market detected.")
        else:
            reasons.append(f"Unknown regime '{regime}' — applying equal-weight fallback.")

        if preferred:
            for s in preferred:
                w = weights.get(s, 0)
                reasons.append(
                    f"{s} preferred — weight {w:.0%}."
                )
        else:
            reasons.append("No strategies specifically preferred.")

        if reduced:
            for s in reduced:
                w = weights.get(s, 0)
                reasons.append(
                    f"{s} reduced — weight {w:.0%}."
                )

        if disabled:
            for s in disabled:
                reasons.append(f"{s} disabled for this regime.")
        else:
            reasons.append("All strategies enabled.")

        reasons.append(
            f"Decision confidence: {confidence:.1f}%."
        )

        return reasons

    # ------------------------------------------------------------------
    # Serialisation helper
    # ------------------------------------------------------------------

    def decision_to_dict(
        self,
        decision: AdaptiveDecision,
    ) -> dict[str, Any]:
        """Convert an ``AdaptiveDecision`` to a dictionary.

        This is a convenience wrapper around
        :meth:`AdaptiveDecision.to_dict` that also logs the conversion.

        Args:
            decision:
                The adaptive decision to serialise.

        Returns:
            JSON-serialisable dictionary representation.
        """
        logger.debug(
            "Serialising AdaptiveDecision for regime %s.",
            decision.market_regime,
        )
        return decision.to_dict()
