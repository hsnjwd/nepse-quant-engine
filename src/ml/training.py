"""Training pipeline for the ML subsystem.

Orchestrates feature engineering → dataset construction → model
fitting → evaluation → persistence, returning a structured
:class:`TrainingResult`.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from src.ml.dataset import DatasetBuilder, MLDataset
from src.ml.evaluation import (
    ClassificationMetrics,
    RegressionMetrics,
    evaluate_classification,
    evaluate_regression,
)
from src.ml.feature_engineering import FeatureConfig, FeatureEngineer
from src.ml.models import BaseModel, get_model
from src.ml.versioning import ModelVersion

logger = logging.getLogger("nepse.ml.training")


@dataclass
class TrainingResult:
    """Outcome of a single training run.

    Attributes:
        model: The trained model wrapper.
        metrics: Evaluation metrics (dict of :class:`ClassificationMetrics`
            or :class:`RegressionMetrics` ``to_dict()`` output).
        train_metrics: Metrics on the training split.
        test_metrics: Metrics on the test split.
        version: Optional persisted :class:`ModelVersion`.
        task: Classification or regression.
        elapsed_seconds: Wall-clock training time.
        dataset: The final dataset used (train subset).
        params: Hyperparameters used.
    """

    model: BaseModel
    metrics: dict[str, Any] = field(default_factory=dict)
    train_metrics: dict[str, Any] = field(default_factory=dict)
    test_metrics: dict[str, Any] = field(default_factory=dict)
    version: ModelVersion | None = None
    task: str = "classification"
    elapsed_seconds: float = 0.0
    dataset: MLDataset | None = None
    params: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serialisable summary."""
        return {
            "model_name": self.model.name,
            "task": self.task,
            "metrics": self.metrics,
            "elapsed_seconds": round(self.elapsed_seconds, 3),
            "version": self.version.to_dict() if self.version else None,
            "params": self.params,
        }


class TrainingPipeline:
    """High-level training orchestrator.

    Usage::

        pipeline = TrainingPipeline()
        result = pipeline.run(df, task="classification", horizon=5)
    """

    def __init__(
        self,
        feature_config: FeatureConfig | None = None,
        dataset_builder: DatasetBuilder | None = None,
    ) -> None:
        """Initialise the pipeline.

        Args:
            feature_config: Optional feature configuration.
            dataset_builder: Optional dataset builder.  Defaults to a
                fresh builder using *feature_config*.
        """
        self._config = feature_config or FeatureConfig()
        self._builder = dataset_builder or DatasetBuilder(self._config)
        self._engineer = FeatureEngineer(self._config)

    @property
    def feature_config(self) -> FeatureConfig:
        """Return the active feature configuration."""
        return self._config

    def run(
        self,
        df: Any,
        model_name: str | None = None,
        task: str = "classification",
        horizon: int = 5,
        test_size: float = 0.2,
        params: dict[str, Any] | None = None,
        seed: int | None = 42,
    ) -> TrainingResult:
        """Train and evaluate a model on the provided data.

        Args:
            df: OHLCV DataFrame.
            model_name: Model registry name; defaults to
                :func:`~src.ml.models.default_model_name`.
            task: ``"classification"`` or ``"regression"``.
            horizon: Forward-return horizon for labels.
            test_size: Fraction of data reserved for testing.
            params: Optional hyperparameters passed to the model.
            seed: Random seed for reproducibility.

        Returns:
            A :class:`TrainingResult`.

        Raises:
            ValueError: For invalid task or insufficient data.
        """
        if task not in ("classification", "regression"):
            raise ValueError(f"Unknown task '{task}'.")

        if model_name is None:
            from src.ml.models import default_model_name

            model_name = default_model_name()

        start = time.monotonic()

        if task == "classification":
            dataset = self._builder.build_classification(
                df, horizon=horizon
            )
        else:
            dataset = self._builder.build_regression(df, horizon=horizon)

        if dataset.n_samples < 10:
            raise ValueError(
                f"Only {dataset.n_samples} samples available; need at least 10."
            )

        train_ds, test_ds = DatasetBuilder.split(dataset, test_size=test_size)

        model = get_model(model_name)
        if params:
            model = _with_params(model, params)
        model.fit(train_ds.X, train_ds.y)

        y_pred_train = model.predict(train_ds.X)
        y_pred_test = model.predict(test_ds.X)

        if task == "classification":
            train_metrics = evaluate_classification(
                train_ds.y, y_pred_train
            ).to_dict()
            test_metrics = evaluate_classification(
                test_ds.y, y_pred_test
            ).to_dict()
        else:
            train_metrics = evaluate_regression(
                train_ds.y, y_pred_train
            ).to_dict()
            test_metrics = evaluate_regression(
                test_ds.y, y_pred_test
            ).to_dict()

        elapsed = time.monotonic() - start

        logger.info(
            "Training complete: model=%s task=%s test_accuracy=%.3f "
            "elapsed=%.2fs",
            model_name,
            task,
            test_metrics.get("accuracy", test_metrics.get("r2", 0.0)),
            elapsed,
        )

        return TrainingResult(
            model=model,
            metrics=test_metrics,
            train_metrics=train_metrics,
            test_metrics=test_metrics,
            task=task,
            elapsed_seconds=elapsed,
            dataset=train_ds,
            params=params or {},
        )

    def run_with_persistence(
        self,
        df: Any,
        manager: Any,
        model_name: str | None = None,
        task: str = "classification",
        horizon: int = 5,
        tags: list[str] | None = None,
        **kwargs: Any,
    ) -> TrainingResult:
        """Train, evaluate, and persist the model via a ModelManager.

        Args:
            df: OHLCV DataFrame.
            manager: A :class:`~src.ml.model_manager.ModelManager`.
            model_name: Model registry name.
            task: Classification or regression.
            horizon: Forward-return horizon.
            tags: Optional tags for the stored version.
            **kwargs: Extra arguments for :meth:`run`.

        Returns:
            A :class:`TrainingResult` with ``version`` populated.
        """
        result = self.run(
            df,
            model_name=model_name,
            task=task,
            horizon=horizon,
            **kwargs,
        )
        version = manager.save(
            model=result.model,
            metrics=result.metrics,
            params={**result.params, "task": task, "horizon": horizon},
            tags=tags,
        )
        result.version = version
        return result


def _with_params(model: BaseModel, params: dict[str, Any]) -> BaseModel:
    """Recreate a model with explicit hyperparameters where possible."""
    try:
        from src.ml.models import MODEL_REGISTRY

        cls = MODEL_REGISTRY[model.name]
        instance = cls(**params)
        if hasattr(instance, "name"):
            instance.name = model.name
        return instance
    except Exception:
        return model
