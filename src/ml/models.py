"""Model wrappers for the NEPSE Quant Engine ML subsystem.

Every model wrapper implements the same :class:`BaseModel` interface and
is registered in ``MODEL_REGISTRY``.  Wrappers gracefully fall back to
a pure-numpy baseline implementation when the underlying library
(scikit-learn, XGBoost, LightGBM) is unavailable.

All wrapper classes are defined at module scope so that fitted models
can be serialized with :mod:`pickle`.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from typing import Any, Callable

import numpy as np

from src.ml.utils import (
    LIGHTGBM_AVAILABLE,
    SKLEARN_AVAILABLE,
    XGBOOST_AVAILABLE,
)

logger = logging.getLogger("nepse.ml.models")


class BaseModel(ABC):
    """Abstract interface implemented by every ML model wrapper."""

    name: str = "base"
    task: str = "classification"  # or "regression"
    library: str = "numpy"

    @abstractmethod
    def fit(self, X: np.ndarray, y: np.ndarray) -> "BaseModel":
        """Fit the model to data.

        Args:
            X: Feature matrix.
            y: Target vector.

        Returns:
            Self.
        """
        raise NotImplementedError

    @abstractmethod
    def predict(self, X: np.ndarray) -> np.ndarray:
        """Predict class labels (classification) or values (regression)."""
        raise NotImplementedError

    def predict_proba(self, X: np.ndarray) -> np.ndarray | None:
        """Return class probabilities, or None for regression models.

        Args:
            X: Feature matrix.

        Returns:
            Probability matrix ``(n_samples, n_classes)`` or ``None``.
        """
        return None

    def feature_importances(self) -> dict[str, float] | None:
        """Return per-feature importance weights, if available.

        Returns:
            Mapping of feature name to importance, or ``None``.
        """
        return None

    def describe(self) -> dict[str, Any]:
        """Return a JSON-serialisable description of the model."""
        return {
            "name": self.name,
            "task": self.task,
            "library": self.library,
        }

    def __repr__(self) -> str:
        """Return a developer-friendly representation."""
        return (
            f"{self.__class__.__name__}(name={self.name!r}, "
            f"task={self.task!r})"
        )


class NumpyLogisticRegression(BaseModel):
    """Pure-numpy logistic regression (softmax) fallback.

    Uses batch gradient descent on standardized features.  Supports both
    binary and multiclass classification.
    """

    name = "numpy_logistic"
    task = "classification"
    library = "numpy"

    def __init__(
        self,
        learning_rate: float = 0.1,
        epochs: int = 500,
        l2: float = 1e-3,
        seed: int | None = 42,
    ) -> None:
        """Initialise the fallback classifier.

        Args:
            learning_rate: Gradient-descent step size.
            epochs: Number of training passes.
            l2: L2 regularisation weight.
            seed: Random seed for weight initialisation.
        """
        self._lr = learning_rate
        self._epochs = epochs
        self._l2 = l2
        self._seed = seed
        self._weights: np.ndarray | None = None
        self._classes: np.ndarray | None = None
        self._mean: np.ndarray | None = None
        self._std: np.ndarray | None = None

    # ------------------------------------------------------------------
    # BaseModel interface
    # ------------------------------------------------------------------

    def fit(self, X: np.ndarray, y: np.ndarray) -> "NumpyLogisticRegression":
        """Train the softmax classifier.

        Args:
            X: Feature matrix.
            y: Integer class labels.

        Returns:
            Self.
        """
        X = np.asarray(X, dtype=float)
        y = np.asarray(y, dtype=int)

        classes = np.unique(y)
        if classes.size < 2:
            # Single-class degenerate case — store it and predict it.
            self._classes = classes
            self._weights = None
            return self

        self._classes = classes
        n_classes = classes.size

        mean = X.mean(axis=0)
        std = X.std(axis=0)
        std[std == 0] = 1.0
        self._mean, self._std = mean, std
        Xs = (X - mean) / std
        Xs = np.hstack([np.ones((Xs.shape[0], 1)), Xs])

        rng = np.random.default_rng(self._seed)
        W = rng.normal(0, 0.1, size=(Xs.shape[1], n_classes))
        y_onehot = np.zeros((len(y), n_classes))
        y_onehot[np.arange(len(y)), np.searchsorted(classes, y)] = 1.0

        for _ in range(self._epochs):
            scores = Xs @ W
            exp = np.exp(scores - scores.max(axis=1, keepdims=True))
            proba = exp / exp.sum(axis=1, keepdims=True)
            grad = Xs.T @ (proba - y_onehot) / len(y)
            grad[1:, :] += self._l2 * W[1:, :]
            W -= self._lr * grad

        self._weights = W
        return self

    def predict(self, X: np.ndarray) -> np.ndarray:
        """Predict class labels."""
        if self._classes is None:
            raise RuntimeError("Model not fitted.")
        if self._weights is None:
            return np.full(len(np.asarray(X)), self._classes[0])
        proba = self.predict_proba(X)
        if proba is None:
            raise RuntimeError("Model not fitted.")
        return self._classes[np.argmax(proba, axis=1)]

    def predict_proba(self, X: np.ndarray) -> np.ndarray | None:
        """Return softmax class probabilities."""
        if self._classes is None:
            raise RuntimeError("Model not fitted.")
        if self._weights is None:
            return np.full((len(np.asarray(X)), 1), 1.0)
        X = np.asarray(X, dtype=float)
        Xs = (X - self._mean) / self._std
        Xs = np.hstack([np.ones((Xs.shape[0], 1)), Xs])
        scores = Xs @ self._weights
        exp = np.exp(scores - scores.max(axis=1, keepdims=True))
        return exp / exp.sum(axis=1, keepdims=True)


class NumpyLinearRegression(BaseModel):
    """Pure-numpy ridge linear regression fallback."""

    name = "numpy_ridge"
    task = "regression"
    library = "numpy"

    def __init__(self, l2: float = 1e-3) -> None:
        """Initialise the ridge regressor.

        Args:
            l2: Regularisation strength.
        """
        self._l2 = l2
        self._coef: np.ndarray | None = None
        self._mean: np.ndarray | None = None
        self._std: np.ndarray | None = None

    def fit(self, X: np.ndarray, y: np.ndarray) -> "NumpyLinearRegression":
        """Fit ridge regression via the normal equations.

        Args:
            X: Feature matrix.
            y: Continuous target vector.

        Returns:
            Self.
        """
        X = np.asarray(X, dtype=float)
        y = np.asarray(y, dtype=float)

        mean = X.mean(axis=0)
        std = X.std(axis=0)
        std[std == 0] = 1.0
        self._mean, self._std = mean, std
        Xs = (X - mean) / std
        Xs = np.hstack([np.ones((Xs.shape[0], 1)), Xs])

        n_features = Xs.shape[1]
        reg = self._l2 * np.eye(n_features)
        reg[0, 0] = 0.0
        self._coef = np.linalg.solve(Xs.T @ Xs + reg, Xs.T @ y)
        return self

    def predict(self, X: np.ndarray) -> np.ndarray:
        """Predict continuous values."""
        if self._coef is None or self._mean is None or self._std is None:
            raise RuntimeError("Model not fitted.")
        X = np.asarray(X, dtype=float)
        Xs = (X - self._mean) / self._std
        Xs = np.hstack([np.ones((Xs.shape[0], 1)), Xs])
        return Xs @ self._coef


# ---------------------------------------------------------------------------
# Scikit-learn backed classifier — one module-level class, subclassed by
# each model so instances remain picklable.
# ---------------------------------------------------------------------------


class SklearnClassifier(BaseModel):
    """Classification wrapper around a scikit-learn style estimator.

    Subclasses override :meth:`_create_estimator` to return a fresh
    unfitted estimator.  When scikit-learn is unavailable the wrapper
    transparently uses :class:`NumpyLogisticRegression`.
    """

    name = "sklearn_classifier"
    task = "classification"
    library = "sklearn"

    def __init__(self, **params: Any) -> None:
        """Initialise the wrapper.

        Args:
            **params: Hyperparameters passed to the estimator factory.
        """
        self._params = dict(params)
        self._model: Any = None
        self._fallback: NumpyLogisticRegression | None = None

    @staticmethod
    def _create_estimator(**params: Any) -> Any:
        """Return a fresh unfitted estimator.

        Raises:
            ImportError: If the backing library is unavailable.
        """
        raise NotImplementedError

    def _fit_sklearn(self, X: np.ndarray, y: np.ndarray) -> None:
        """Fit the sklearn estimator, tolerating unknown kwargs."""
        try:
            estimator = self._create_estimator(**self._params)
        except TypeError:
            # Duplicate/unknown kwargs — retry with defaults.
            estimator = self._create_estimator()
        estimator.fit(X, y)
        self._model = estimator

    def fit(self, X: np.ndarray, y: np.ndarray) -> "BaseModel":
        """Fit the model, falling back to numpy when needed."""
        X = np.asarray(X, dtype=float)
        y = np.asarray(y, dtype=int)
        if SKLEARN_AVAILABLE:
            try:
                self._fit_sklearn(X, y)
                return self
            except Exception as exc:
                logger.warning(
                    "Sklearn model '%s' failed to fit (%s); "
                    "falling back to numpy baseline.",
                    self.name,
                    exc,
                )
        self._fallback = NumpyLogisticRegression(
            seed=self._params.get("random_state", 42)
        )
        self._fallback.fit(X, y)
        return self

    def predict(self, X: np.ndarray) -> np.ndarray:
        """Predict class labels."""
        if self._model is not None:
            return np.asarray(self._model.predict(X))
        if self._fallback is not None:
            return self._fallback.predict(X)
        raise RuntimeError("Model not fitted.")

    def predict_proba(self, X: np.ndarray) -> np.ndarray | None:
        """Return class probabilities when available."""
        if self._model is not None and hasattr(self._model, "predict_proba"):
            return np.asarray(self._model.predict_proba(X))
        if self._fallback is not None:
            return self._fallback.predict_proba(X)
        return None

    def feature_importances(self) -> dict[str, float] | None:
        """Return per-feature importance when the estimator exposes it."""
        model = self._model
        if model is None:
            return None
        if hasattr(model, "feature_importances_"):
            return {
                f"f{i}": float(v)
                for i, v in enumerate(np.asarray(model.feature_importances_).ravel())
            }
        if hasattr(model, "coef_"):
            coef = np.abs(np.asarray(model.coef_)).mean(axis=0)
            return {f"f{i}": float(v) for i, v in enumerate(coef)}
        return None

    def describe(self) -> dict[str, Any]:
        """Return a JSON-serialisable description."""
        return {
            "name": self.name,
            "task": self.task,
            "library": "sklearn" if self._model is not None else "numpy",
        }


class RandomForestModel(SklearnClassifier):
    """Random Forest classifier."""

    name = "random_forest"

    @staticmethod
    def _create_estimator(**params: Any) -> Any:
        from sklearn.ensemble import RandomForestClassifier

        return RandomForestClassifier(
            n_estimators=100, random_state=42, **params
        )


class GradientBoostingModel(SklearnClassifier):
    """Gradient Boosting classifier."""

    name = "gradient_boosting"

    @staticmethod
    def _create_estimator(**params: Any) -> Any:
        from sklearn.ensemble import GradientBoostingClassifier

        return GradientBoostingClassifier(random_state=42, **params)


class LogisticRegressionModel(SklearnClassifier):
    """Logistic Regression classifier."""

    name = "logistic_regression"

    @staticmethod
    def _create_estimator(**params: Any) -> Any:
        from sklearn.linear_model import LogisticRegression

        return LogisticRegression(max_iter=1000, random_state=42, **params)


class XGBoostModel(SklearnClassifier):
    """XGBoost classifier, gracefully degrading to gradient boosting."""

    name = "xgboost"
    library = "xgboost"

    @staticmethod
    def _create_estimator(**params: Any) -> Any:
        if not XGBOOST_AVAILABLE:
            raise ImportError("xgboost is not installed.")
        from xgboost import XGBClassifier

        return XGBClassifier(eval_metric="logloss", random_state=42, **params)


class LightGBMModel(SklearnClassifier):
    """LightGBM classifier, gracefully degrading to gradient boosting."""

    name = "lightgbm"
    library = "lightgbm"

    @staticmethod
    def _create_estimator(**params: Any) -> Any:
        if not LIGHTGBM_AVAILABLE:
            raise ImportError("lightgbm is not installed.")
        from lightgbm import LGBMClassifier

        return LGBMClassifier(verbose=-1, random_state=42, **params)


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

MODEL_REGISTRY: dict[str, type[BaseModel]] = {
    "random_forest": RandomForestModel,
    "gradient_boosting": GradientBoostingModel,
    "logistic_regression": LogisticRegressionModel,
    "xgboost": XGBoostModel,
    "lightgbm": LightGBMModel,
    "numpy_logistic": NumpyLogisticRegression,
    "numpy_ridge": NumpyLinearRegression,
}


def get_model(name: str) -> BaseModel:
    """Return a model instance by registry name.

    Args:
        name: Registry key (e.g. ``"random_forest"``).

    Returns:
        A fresh model wrapper instance.

    Raises:
        KeyError: If the name is unknown.
    """
    try:
        cls = MODEL_REGISTRY[name]
    except KeyError as exc:
        raise KeyError(
            f"Unknown model '{name}'. Available: {sorted(MODEL_REGISTRY)}"
        ) from exc
    return cls()


def available_models() -> list[str]:
    """Return all registry model names."""
    return sorted(MODEL_REGISTRY)


def default_model_name() -> str:
    """Return the recommended default model name.

    Prefers the best available gradient-boosting implementation,
    otherwise falls back to the numpy baseline.
    """
    if LIGHTGBM_AVAILABLE:
        return "lightgbm"
    if XGBOOST_AVAILABLE:
        return "xgboost"
    if SKLEARN_AVAILABLE:
        return "random_forest"
    return "numpy_logistic"
