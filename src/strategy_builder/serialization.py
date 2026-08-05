"""Strategy Builder — serialization of rule-based strategies.

Provides versioned JSON serialization and deserialization for rule
trees, with migration hooks for future format changes.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.strategy_builder.rules import IndicatorRule, RuleGroup

logger = logging.getLogger("nepse.strategy_builder.serialization")

SCHEMA_VERSION = 1


@dataclass
class SerializedStrategy:
    """Versioned strategy document.

    Attributes:
        name: Strategy display name.
        description: Optional strategy description.
        version: Semantic version of the strategy definition.
        schema_version: Serialization schema version.
        entry_rules: Rule tree evaluated to generate BUY signals.
        exit_rules: Optional rule tree evaluated for SELL signals.
        parameters: Free-form parameter dict.
        created_at: ISO timestamp.
        tags: Optional tags.
    """

    name: str
    entry_rules: Any
    exit_rules: Any = None
    description: str = ""
    version: str = "1.0.0"
    schema_version: int = SCHEMA_VERSION
    parameters: dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    tags: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serialisable dictionary."""
        return {
            "schema_version": self.schema_version,
            "name": self.name,
            "description": self.description,
            "version": self.version,
            "created_at": self.created_at,
            "tags": self.tags,
            "parameters": self.parameters,
            "entry_rules": self.entry_rules.to_dict(),
            "exit_rules": (
                self.exit_rules.to_dict() if self.exit_rules is not None else None
            ),
        }


def to_json(strategy: SerializedStrategy, indent: int = 2) -> str:
    """Serialize a strategy document to JSON.

    Args:
        strategy: The strategy document.
        indent: JSON indentation.

    Returns:
        JSON string.
    """
    return json.dumps(strategy.to_dict(), indent=indent)


def from_dict(data: dict[str, Any]) -> SerializedStrategy:
    """Deserialize a strategy document from a dictionary.

    Args:
        data: Strategy dictionary.

    Returns:
        A :class:`SerializedStrategy`.

    Raises:
        ValueError: For unsupported schema versions.
    """
    schema_version = int(data.get("schema_version", 1))
    if schema_version > SCHEMA_VERSION:
        raise ValueError(
            f"Strategy schema version {schema_version} is newer than "
            f"supported version {SCHEMA_VERSION}."
        )

    entry = data.get("entry_rules")
    if entry is None:
        raise ValueError("Strategy is missing entry_rules.")
    entry_rules = _parse_node(entry)

    exit_data = data.get("exit_rules")
    exit_rules = _parse_node(exit_data) if exit_data is not None else None

    return SerializedStrategy(
        name=data.get("name", "Untitled Strategy"),
        description=data.get("description", ""),
        version=data.get("version", "1.0.0"),
        schema_version=schema_version,
        parameters=data.get("parameters", {}),
        created_at=data.get("created_at", ""),
        tags=data.get("tags", []),
        entry_rules=entry_rules,
        exit_rules=exit_rules,
    )


def from_json(payload: str) -> SerializedStrategy:
    """Deserialize a strategy document from a JSON string.

    Args:
        payload: JSON string.

    Returns:
        A :class:`SerializedStrategy`.
    """
    return from_dict(json.loads(payload))


def save(strategy: SerializedStrategy, path: str | Path) -> None:
    """Persist a strategy to a JSON file.

    Args:
        strategy: The strategy document.
        path: Destination file path.
    """
    Path(path).write_text(to_json(strategy), encoding="utf-8")
    logger.info("Saved strategy '%s' to %s.", strategy.name, path)


def load(path: str | Path) -> SerializedStrategy:
    """Load a strategy from a JSON file.

    Args:
        path: Source file path.

    Returns:
        A :class:`SerializedStrategy`.
    """
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    strategy = from_dict(data)
    logger.info("Loaded strategy '%s' from %s.", strategy.name, path)
    return strategy


def _parse_node(node: dict[str, Any]) -> Any:
    """Recursively parse a rule node from a dictionary."""
    node_type = node.get("type")
    if node_type == "indicator":
        return IndicatorRule.from_dict(node)
    if node_type == "group":
        return RuleGroup.from_dict(node)
    raise ValueError(f"Unknown node type '{node_type}'.")
