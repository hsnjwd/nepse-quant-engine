"""Strategies API endpoints — marketplace listing and rule strategies."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, status

from src.api.schemas import ok
from src.logging.logger import logger

router = APIRouter(prefix="/strategies", tags=["Strategies"])


@router.get("/", status_code=status.HTTP_200_OK)
def list_strategies() -> dict[str, Any]:
    """List all marketplace strategies with metadata.

    Returns:
        A dict with discovered strategy metadata.
    """
    try:
        from src.strategies.marketplace import marketplace_info

        return ok(marketplace_info())
    except Exception as exc:
        logger.error("Failed to list strategies: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to list strategies: {exc}",
        ) from exc


@router.get("/registered", status_code=status.HTTP_200_OK)
def list_registered() -> dict[str, Any]:
    """List strategies registered with the global registry."""
    from src.strategies.registry import strategy_info

    return ok(strategy_info())


@router.post("/build", status_code=status.HTTP_200_OK)
def build_strategy(payload: dict[str, Any]) -> dict[str, Any]:
    """Build a rule-based strategy from a serialized definition.

    Args:
        payload: Serialized strategy dict (name, entry_rules, etc.).

    Returns:
        The built strategy's rule dictionary and validation status.
    """
    try:
        from src.strategy_builder.serialization import from_dict

        strategy = from_dict(payload)
        built = _build_rule_strategy(strategy)
        return ok(
            {
                "name": strategy.name,
                "rules": strategy.to_dict(),
                "validation": [i.to_dict() for i in built.validate()],
            }
        )
    except Exception as exc:
        logger.error("Failed to build strategy: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc


def _build_rule_strategy(strategy: Any) -> Any:
    """Instantiate a RuleBasedStrategy from a SerializedStrategy."""
    from src.strategy_builder.builder import RuleBasedStrategy

    return RuleBasedStrategy(
        name=strategy.name,
        entry_rules=strategy.entry_rules,
        exit_rules=strategy.exit_rules,
        description=strategy.description,
        parameters=strategy.parameters,
    )
