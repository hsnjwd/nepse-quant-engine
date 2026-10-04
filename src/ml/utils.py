"""Shared utilities for the machine-learning subsystem.

Provides safe optional-library detection, reproducible seeding, and
small helpers reused across the ML pipeline.
"""

from __future__ import annotations

import importlib.util
import logging
import random
from typing import Any

import numpy as np

logger = logging.getLogger("nepse.ml")

# ---------------------------------------------------------------------------
# Optional dependency detection
# ---------------------------------------------------------------------------


def is_available(module: str) -> bool:
    """Return True if *module* can be imported.

    Args:
        module: Fully-qualified module name.

    Returns:
        Whether the module is importable.
    """
    return importlib.util.find_spec(module) is not None


SKLEARN_AVAILABLE = is_available("sklearn")
XGBOOST_AVAILABLE = is_available("xgboost")
LIGHTGBM_AVAILABLE = is_available("lightgbm")


def require_sklearn() -> None:
    """Raise ImportError if scikit-learn is unavailable.

    Raises:
        ImportError: When scikit-learn is not installed.
    """
    if not SKLEARN_AVAILABLE:
        raise ImportError(
            "scikit-learn is required for this model. "
            "Install it with: pip install scikit-learn"
        )


# ---------------------------------------------------------------------------
# Reproducibility
# ---------------------------------------------------------------------------


def set_seed(seed: int | None = None) -> None:
    """Seed numpy and the Python random module.

    Args:
        seed: Optional integer seed.  ``None`` seeds non-reproducibly.
    """
    if seed is not None:
        np.random.seed(seed)
        random.seed(seed)
        logger.debug("ML seed set to %s.", seed)


# ---------------------------------------------------------------------------
# Data helpers
# ---------------------------------------------------------------------------


def safe_float(value: Any, default: float = 0.0) -> float:
    """Convert *value* to float, returning *default* on failure."""
    try:
        if value is None:
            return default
        result = float(value)
        if result != result:  # NaN
            return default
        return result
    except (TypeError, ValueError):
        return default


def drop_non_numeric(df: Any) -> Any:
    """Return a copy of *df* containing only numeric columns."""
    import pandas as pd

    return df.select_dtypes(include=[np.number]).copy()


def require_dataframe(df: Any) -> Any:
    """Validate that *df* is a non-empty pandas DataFrame."""
    import pandas as pd

    if not isinstance(df, pd.DataFrame):
        raise TypeError(
            f"Expected pandas DataFrame, got {type(df).__name__}."
        )
    if df.empty:
        raise ValueError("DataFrame is empty.")
    return df
