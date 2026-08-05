"""Strategy Builder — visual rule-based strategy construction.

Defines a declarative rule language (comparisons, AND/OR/NOT, nested
rules) that can be evaluated against an OHLCV DataFrame to produce
trading signals.  Strategies can be serialized to JSON, validated,
and backtested instantly.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

logger = logging.getLogger("nepse.strategy_builder.rules")


class Operator(str, Enum):
    """Comparison operators supported by rules."""

    GT = ">"
    LT = "<"
    GTE = ">="
    LTE = "<="
    EQ = "=="
    NE = "!="

    @classmethod
    def evaluate(cls, op: str, left: float, right: float) -> bool:
        """Evaluate a binary comparison.

        Args:
            op: One of the operator values.
            left: Left operand.
            right: Right operand.

        Returns:
            Boolean result of the comparison.
        """
        if op == cls.GT:
            return left > right
        if op == cls.LT:
            return left < right
        if op == cls.GTE:
            return left >= right
        if op == cls.LTE:
            return left <= right
        if op == cls.EQ:
            return left == right
        if op == cls.NE:
            return left != right
        raise ValueError(f"Unknown operator: {op}")


class LogicOp(str, Enum):
    """Logical combinators for nested rules."""

    AND = "AND"
    OR = "OR"
    NOT = "NOT"


@dataclass
class IndicatorRule:
    """A single comparison rule: ``indicator OP value``.

    Attributes:
        indicator: Indicator column name (e.g. ``"RSI"``).
        operator: Comparison operator.
        value: Numeric threshold.
    """

    indicator: str
    operator: Operator | str
    value: float = 0.0

    def evaluate(self, row: Any, getter: Any = None) -> bool:
        """Evaluate against a DataFrame row or a value getter.

        Args:
            row: pandas row (supports ``row["COL"]``).
            getter: Optional callable ``getter(column) -> float`` that
                takes precedence over *row*.

        Returns:
            Boolean evaluation result.
        """
        if getter is not None:
            actual = float(getter(self.indicator))
        else:
            try:
                actual = float(row[self.indicator])
            except (KeyError, TypeError, ValueError):
                return False
        return Operator.evaluate(self.operator, actual, self.value)

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serialisable dictionary."""
        return {
            "type": "indicator",
            "indicator": self.indicator,
            "operator": str(self.operator),
            "value": self.value,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "IndicatorRule":
        """Reconstruct from a dictionary."""
        return cls(
            indicator=data["indicator"],
            operator=data["operator"],
            value=float(data.get("value", 0.0)),
        )


@dataclass
class RuleGroup:
    """A logical group of nested rules.

    Attributes:
        logic: AND / OR / NOT.
        children: List of rules (IndicatorRule or RuleGroup).
    """

    logic: LogicOp | str = LogicOp.AND
    children: list[Any] = field(default_factory=list)

    def evaluate(self, row: Any, getter: Any = None) -> bool:
        """Evaluate the group recursively.

        Args:
            row: pandas row.
            getter: Optional column value getter.

        Returns:
            Boolean evaluation result.
        """
        logic = LogicOp(self.logic)
        if logic == LogicOp.NOT:
            if not self.children:
                return False
            return not self.children[0].evaluate(row, getter)
        if logic == LogicOp.AND:
            return all(child.evaluate(row, getter) for child in self.children)
        return any(child.evaluate(row, getter) for child in self.children)

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serialisable dictionary."""
        return {
            "type": "group",
            "logic": str(self.logic),
            "children": [child.to_dict() for child in self.children],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "RuleGroup":
        """Reconstruct from a dictionary."""
        children = []
        for child in data.get("children", []):
            children.append(
                IndicatorRule.from_dict(child)
                if child.get("type") == "indicator"
                else RuleGroup.from_dict(child)
            )
        return cls(
            logic=data.get("logic", "AND"),
            children=children,
        )


def and_rule(*children: Any) -> RuleGroup:
    """Build an AND group from rules."""
    return RuleGroup(logic=LogicOp.AND, children=list(children))


def or_rule(*children: Any) -> RuleGroup:
    """Build an OR group from rules."""
    return RuleGroup(logic=LogicOp.OR, children=list(children))


def not_rule(child: Any) -> RuleGroup:
    """Build a NOT group from a rule."""
    return RuleGroup(logic=LogicOp.NOT, children=[child])
