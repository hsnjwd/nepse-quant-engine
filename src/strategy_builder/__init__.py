"""Strategy Builder subsystem.

Lets users construct trading strategies from visual rules — indicator
comparisons combined with AND / OR / NOT logic and nested groups —
then validate, serialize (versioned JSON), and instantly backtest them.

Public API::

    from src.strategy_builder.builder import RuleBasedStrategy
    from src.strategy_builder.rules import IndicatorRule, and_rule, or_rule
    from src.strategy_builder.serialization import save, load
"""

from __future__ import annotations

from src.strategy_builder.backtest import InstantBacktestResult, InstantBacktester
from src.strategy_builder.builder import (
    RuleBasedStrategy,
    enrich_indicators,
    evaluate_rules,
    evaluate_series,
)
from src.strategy_builder.rules import (
    IndicatorRule,
    LogicOp,
    Operator,
    RuleGroup,
    and_rule,
    not_rule,
    or_rule,
)
from src.strategy_builder.serialization import (
    SCHEMA_VERSION,
    SerializedStrategy,
    from_dict,
    from_json,
    load,
    save,
    to_json,
)
from src.strategy_builder.validation import (
    KNOWN_INDICATORS,
    ValidationIssue,
    is_valid,
    validate_tree,
)

__all__ = [
    "InstantBacktestResult",
    "InstantBacktester",
    "RuleBasedStrategy",
    "enrich_indicators",
    "evaluate_rules",
    "evaluate_series",
    "IndicatorRule",
    "LogicOp",
    "Operator",
    "RuleGroup",
    "and_rule",
    "not_rule",
    "or_rule",
    "SCHEMA_VERSION",
    "SerializedStrategy",
    "from_dict",
    "from_json",
    "load",
    "save",
    "to_json",
    "KNOWN_INDICATORS",
    "ValidationIssue",
    "is_valid",
    "validate_tree",
]
