"""Prediction engine for the ML subsystem.

Combines feature engineering, model loading, and prediction into a
single convenient interface that returns a structured
:class:`PredictionResult`.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from src.ml.dataset import DatasetBuilder
from src.ml.feature_engineering import FeatureConfig, FeatureEngineer
from src.ml.models import BaseModel, get_model
from src.ml.model_manager import ModelManager

logger = logging.getLogger("nepse.ml.prediction")

LABEL_NAMES: dict[int, str] = {0: "SELL", 1: "HOLD", 2: "BUY"}


@dataclass
class PredictionResult:
    """Result of a single prediction request.

    Attributes:
        signal: Predicted signal name (``BUY``/``HOLD``/``SELL``) for
            classification, or ``None`` for regression.
        probability: Confidence probability of the predicted class.
        predicted_return: Predicted forward return (regression only).
        model_name: Name of the model used.
        model_version: Version of the model used.
        feature_importances: Optional per-feature importance dict.
        raw_proba: Full class probability vector (classification).
        details: Extra metadata.
    """

    signal: str | None = None
    probability: float = 0.0
    predicted_return: float | None = None
    model_name: str = ""
    model_version: str | None = None
    feature_importances: dict[str, float] | None = None
    raw_proba: list[float] = field(default_factory=list)
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serialisable dictionary."""
        return {
            "signal": self.signal,
            "probability": round(self.probability, 4),
            "predicted_return": (
                round(self.predicted_return, 6)
                if self.predicted_return is not None
                else None
            ),
            "model_name": self.model_name,
            "model_version": self.model_version,
            "feature_importances": self.feature_importances,
            "raw_proba": [round(p, 4) for p in self.raw_proba],
            "details": self.details,
        }


class PredictionEngine:
    """Make predictions using stored or fresh models.

    Usage::

        engine = PredictionEngine()
        result = engine.predict(df, model_name="random_forest")
        print(result.signal, result.probability)
    """

    def __init__(
        self,
        manager: ModelManager | None = None,
        feature_config: FeatureConfig | None = None,
    ) -> None:
        """Initialise the engine.

        Args:
            manager: Optional model manager used for loading stored
                models.
            feature_config: Optional feature configuration.
        """
        self._manager = manager
        self._config = feature_config or FeatureConfig()
        self._engineer = FeatureEngineer(self._config)

    def predict(
        self,
        df: Any,
        model_name: str | None = None,
        horizon: int = 5,
        task: str = "classification",
        model: BaseModel | None = None,
    ) -> PredictionResult:
        """Predict the next signal or return for the latest row.

        Args:
            df: OHLCV DataFrame.  The most recent row is predicted.
            model_name: Model registry name to load (from manager or
                registry).
            horizon: Forward-return horizon used for features.
            task: Classification or regression.
            model: Optional pre-trained model.  Takes precedence over
                *model_name*.

        Returns:
            A :class:`PredictionResult`.

        Raises:
            ValueError: If no model can be determined.
        """
        features = self._engineer.generate(df)
        feature_cols = self._engineer.feature_columns(features)

        builder = DatasetBuilder(self._config, self._engineer)
        if task == "classification":
            dataset = builder.build_classification(
                df, horizon=horizon
            )
        else:
            dataset = builder.build_regression(df, horizon=horizon)

        if dataset.n_samples == 0:
            raise ValueError("No samples available for prediction.")

        trained = model
        version: str | None = None

        if trained is None and model_name is None:
            model_name = "random_forest"

        if trained is None:
            trained, version = self._resolve_model(model_name)

        if trained is None:
            raise ValueError(f"No model available for '{model_name}'.")

        if not self._is_fitted(trained):
            raise ValueError(
                "No trained model available — train one first or pass "
                "an already-fitted model via model=..."
            )

        # The feature rows were aligned to the dataset, so use the last
        # valid row for prediction.
        X_last = dataset.X[-1:]

        if task == "classification":
            proba = trained.predict_proba(X_last)
            y_pred = trained.predict(X_last)[0]

            if proba is not None and proba.shape[0] > 0:
                probs = proba[0]
                probability = float(np.max(probs))
                raw = probs.tolist()
            else:
                probability = 0.5
                raw = []

            label = LABEL_NAMES.get(int(y_pred), str(y_pred))
            return PredictionResult(
                signal=label,
                probability=probability,
                model_name=trained.name,
                model_version=version,
                feature_importances=trained.feature_importances(),
                raw_proba=raw,
                details={
                    "task": task,
                    "horizon": horizon,
                    "n_features": dataset.n_features,
                },
            )

        predicted = float(trained.predict(X_last)[0])
        return PredictionResult(
            predicted_return=predicted,
            signal="BUY" if predicted > 0 else "SELL",
            probability=0.5,
            model_name=trained.name,
            model_version=version,
            feature_importances=trained.feature_importances(),
            details={
                "task": task,
                "horizon": horizon,
                "n_features": dataset.n_features,
            },
        )

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    @staticmethod
    def _is_fitted(model: BaseModel) -> bool:
        """Return True when a model has been trained.

        Numpy models expose private state; sklearn wrappers expose
        ``_model`` or ``_fallback``.  A model is fitted when its backing
        estimator or fallback weights exist.
        """
        if hasattr(model, "_model") and model._model is not None:
            return True
        if hasattr(model, "_fallback") and model._fallback is not None:
            return True
        if hasattr(model, "_weights") and model._weights is not None:
            return True
        if hasattr(model, "_coef") and model._coef is not None:
            return True
        return False

    def _resolve_model(
        self, model_name: str | None
    ) -> tuple[BaseModel | None, str | None]:
        """Resolve a model from the manager or the registry."""
        if self._manager is not None and model_name is not None:
            stored = self._manager.load(model_name)
            if stored is not None:
                record = self._manager.versions.latest(model_name)
                return stored, record.version if record else None

        if model_name is not None:
            try:
                return get_model(model_name), None
            except KeyError:
                logger.warning("Unknown model name '%s'.", model_name)
                return None, None

        if self._manager is not None:
            models = self._manager.list_models()
            if models:
                first = models[0]["name"]
                stored = self._manager.load(first)
                record = self._manager.versions.latest(first)
                return stored, record.version if record else None

        return None, None
