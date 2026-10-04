"""Strategy Builder — rule evaluation engine and strategy objects.

Evaluates rule trees against OHLCV DataFrames (with indicator columns
enriched) and produces standard signal payloads compatible with the
existing strategy framework.
"""

from __future__ import annotations

import logging
from typing import Any

from src.logging.logger import logger
from src.strategy_builder.rules import IndicatorRule, RuleGroup
from src.strategy_builder.validation import ValidationIssue, is_valid

logger = logger.getChild("builder") if logger.name else logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Indicator enrichment
# ---------------------------------------------------------------------------


def enrich_indicators(df: Any) -> Any:
    """Add indicator columns to a DataFrame.

    Uses the existing analysis engine when available and falls back to
    the lightweight indicator modules.

    Args:
        df: OHLCV DataFrame.

    Returns:
        DataFrame enriched with indicator columns.
    """
    try:
        from src.indicators.moving_average import add_moving_averages
        from src.indicators.momentum import add_momentum_indicators
        from src.indicators.volatility import add_volatility_indicators
        from src.indicators.volume import add_volume_indicators

        result = df.copy()
        result = add_moving_averages(result)
        result = add_momentum_indicators(result)
        result = add_volume_indicators(result)
        result = add_volatility_indicators(result)
        return result
    except Exception as exc:
        logger.warning("Indicator enrichment unavailable: %s", exc)
        return df.copy()


# ---------------------------------------------------------------------------
# Evaluation
# ---------------------------------------------------------------------------


def evaluate_rules(root: Any, df: Any, index: int) -> bool:
    """Evaluate a rule tree against a specific row of a DataFrame.

    Args:
        root: The rule tree root.
        df: Indicator-enriched DataFrame.
        index: Row index to evaluate.

    Returns:
        Boolean result of the rule tree.
    """
    if index < 0 or index >= len(df):
        return False
    row = df.iloc[index]
    return bool(root.evaluate(row))


def evaluate_series(root: Any, df: Any) -> list[bool]:
    """Evaluate a rule tree against every row of a DataFrame.

    Args:
        root: The rule tree root.
        df: Indicator-enriched DataFrame.

    Returns:
        List of boolean results, one per row.
    """
    return [bool(root.evaluate(df.iloc[i])) for i in range(len(df))]


# ---------------------------------------------------------------------------
# RuleBasedStrategy
# ---------------------------------------------------------------------------


class RuleBasedStrategy:
    """A strategy defined declaratively by entry/exit rule trees.

    Attributes:
        name: Strategy name.
        entry_rules: Rule tree that must hold for a BUY signal.
        exit_rules: Optional rule tree that triggers a SELL signal.
        description: Optional description.
        parameters: Optional parameter dict.
    """

    def __init__(
        self,
        name: str,
        entry_rules: Any,
        exit_rules: Any = None,
        description: str = "",
        parameters: dict[str, Any] | None = None,
    ) -> None:
        """Initialise the strategy.

        Args:
            name: Strategy display name.
            entry_rules: Entry rule tree.
            exit_rules: Optional exit rule tree.
            description: Optional description.
            parameters: Optional parameters.
        """
        self._name = name.strip()
        self._entry_rules = entry_rules
        self._exit_rules = exit_rules
        self._description = description.strip()
        self._parameters = parameters or {}

    @property
    def name(self) -> str:
        """Return the strategy name."""
        return self._name

    @property
    def description(self) -> str:
        """Return the strategy description."""
        return self._description

    @property
    def parameters(self) -> dict[str, Any]:
        """Return the strategy parameters."""
        return self._parameters

    @property
    def entry_rules(self) -> Any:
        """Return the entry rule tree."""
        return self._entry_rules

    @property
    def exit_rules(self) -> Any:
        """Return the exit rule tree, or None."""
        return self._exit_rules

    def validate(self) -> list[ValidationIssue]:
        """Validate the rule trees.

        Returns:
            List of validation issues (empty when valid).
        """
        from src.strategy_builder.validation import validate_tree

        issues = validate_tree(self._entry_rules)
        if self._exit_rules is not None:
            for issue in validate_tree(self._exit_rules):
                if issue.to_dict() not in [i.to_dict() for i in issues]:
                    issues.append(issue)
        return issues

    def generate_signal(self, df: Any) -> dict[str, Any]:
        """Generate a signal from the latest row of a DataFrame.

        Args:
            df: OHLCV DataFrame (optionally indicator-enriched).

        Returns:
            Standardized signal payload dict.
        """
        enriched = enrich_indicators(df)
        if len(enriched) == 0:
            return self._signal("HOLD", 0.0, 0.0, {"error": "empty data"})

        price = _last_close(enriched)
        entry = evaluate_rules(self._entry_rules, enriched, len(enriched) - 1)

        exit_hit = False
        if self._exit_rules is not None:
            exit_hit = evaluate_rules(
                self._exit_rules, enriched, len(enriched) - 1
            )

        if entry and not exit_hit:
            return self._signal("BUY", price, 0.8)
        if exit_hit:
            return self._signal("SELL", price, 0.8)
        return self._signal("HOLD", price, 0.3)

    # ------------------------------------------------------------------

    def _signal(
        self,
        signal: str,
        price: float,
        score: float,
        details: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Build a standard signal payload."""
        return {
            "signal": signal,
            "price": round(price, 2),
            "stop_loss": None,
            "target1": None,
            "targets": [],
            "score": round(score, 2),
            "strategy_name": self._name,
            "strategy_version": "1.0.0",
            "details": details or {},
        }

    def to_rule_dict(self) -> dict[str, Any]:
        """Return the rule tree as a dictionary."""
        from src.strategy_builder.serialization import SerializedStrategy

        return SerializedStrategy(
            name=self._name,
            description=self._description,
            parameters=self._parameters,
            entry_rules=self._entry_rules,
            exit_rules=self._exit_rules,
        ).to_dict()


def _last_close(df: Any) -> float:
    """Return the latest close price."""
    try:
        return float(df["Close"].iloc[-1])
    except (KeyError, IndexError, TypeError, ValueError):
        return 0.0
