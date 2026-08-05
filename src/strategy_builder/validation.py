"""Strategy Builder — validation of rule trees.

Validates that rule trees reference known indicators, use valid
operators, and are structurally sound.
"""

from __future__ import annotations

import logging
from typing import Any

from src.strategy_builder.rules import (
    IndicatorRule,
    LogicOp,
    Operator,
    RuleGroup,
)

logger = logging.getLogger("nepse.strategy_builder.validation")

# Indicator columns produced by the analysis engine (src.indicators +
# engine.analyzer).
KNOWN_INDICATORS: set[str] = {
    "RSI",
    "MACD",
    "MACD_SIGNAL",
    "MACD_HIST",
    "SMA_20",
    "SMA_50",
    "SMA_200",
    "EMA_20",
    "ATR",
    "BB_MIDDLE",
    "BB_UPPER",
    "BB_LOWER",
    "VOLUME_MA",
    "RELATIVE_VOLUME",
    "VOLUME_SIGNAL",
    "VOLUME_SCORE",
    "Close",
    "Open",
    "High",
    "Low",
    "Volume",
}

_VALID_OPERATORS = {op.value for op in Operator}


class ValidationIssue:
    """A single validation finding.

    Attributes:
        path: Location of the issue within the rule tree.
        message: Human-readable description.
    """

    def __init__(self, path: str, message: str) -> None:
        """Initialise the issue."""
        self.path = path
        self.message = message

    def to_dict(self) -> dict[str, str]:
        """Return a JSON-serialisable dictionary."""
        return {"path": self.path, "message": self.message}

    def __repr__(self) -> str:
        """Return a developer-friendly representation."""
        return f"{self.path}: {self.message}"


def validate_tree(root: Any, extra_indicators: set[str] | None = None) -> list[ValidationIssue]:
    """Validate a rule tree.

    Args:
        root: The root rule (IndicatorRule or RuleGroup).
        extra_indicators: Additional indicator names that are allowed.

    Returns:
        List of :class:`ValidationIssue` findings (empty when valid).
    """
    known = KNOWN_INDICATORS | (extra_indicators or set())
    issues: list[ValidationIssue] = []

    def _walk(node: Any, path: str) -> None:
        if isinstance(node, RuleGroup):
            try:
                logic = LogicOp(node.logic)
            except ValueError:
                issues.append(
                    ValidationIssue(path, f"Invalid logic '{node.logic}'.")
                )
                return

            if logic == LogicOp.NOT and len(node.children) != 1:
                issues.append(
                    ValidationIssue(
                        path, "NOT groups must contain exactly one child."
                    )
                )
            if not node.children:
                issues.append(
                    ValidationIssue(path, f"{logic.value} group is empty.")
                )
            for i, child in enumerate(node.children):
                _walk(child, f"{path}[{i}]")
            return

        if isinstance(node, IndicatorRule):
            if node.indicator not in known:
                issues.append(
                    ValidationIssue(
                        path,
                        f"Unknown indicator '{node.indicator}'. "
                        f"Known: {sorted(known)}",
                    )
                )
            if str(node.operator) not in _VALID_OPERATORS:
                issues.append(
                    ValidationIssue(
                        path, f"Invalid operator '{node.operator}'."
                    )
                )
            return

        issues.append(
            ValidationIssue(path, f"Unknown rule type {type(node).__name__}.")
        )

    _walk(root, "root")
    logger.debug("Validated rule tree: %d issue(s).", len(issues))
    return issues


def is_valid(root: Any, extra_indicators: set[str] | None = None) -> bool:
    """Return True when a rule tree validates cleanly.

    Args:
        root: The rule tree root.
        extra_indicators: Additional allowed indicator names.

    Returns:
        Whether the tree has no validation issues.
    """
    return not validate_tree(root, extra_indicators)
