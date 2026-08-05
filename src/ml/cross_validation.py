"""Cross-validation for time-series model evaluation.

Provides time-ordered (non-shuffled) K-fold and expanding-window
splits suitable for financial data, plus a helper to run an entire
evaluation pipeline.
"""

from __future__ import annotations

import copy
import logging
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from src.ml.dataset import MLDataset
from src.ml.evaluation import (
    ClassificationMetrics,
    RegressionMetrics,
    evaluate_classification,
    evaluate_regression,
)

logger = logging.getLogger("nepse.ml.cv")


@dataclass
class CVRound:
    """Results of one cross-validation fold.

    Attributes:
        fold: Zero-based fold index.
        train_indices: Indices used for training.
        test_indices: Indices used for evaluation.
        metrics: Evaluation metrics dict for this fold.
    """

    fold: int
    train_indices: list[int] = field(default_factory=list)
    test_indices: list[int] = field(default_factory=list)
    metrics: dict[str, Any] = field(default_factory=dict)


@dataclass
class CVResult:
    """Aggregate cross-validation outcome.

    Attributes:
        folds: Per-fold :class:`CVRound` entries.
        mean_metrics: Mean metrics across folds.
        best_fold: Fold index with the highest primary metric.
    """

    folds: list[CVRound] = field(default_factory=list)
    mean_metrics: dict[str, Any] = field(default_factory=dict)
    best_fold: int = 0

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serialisable dictionary."""
        return {
            "n_folds": len(self.folds),
            "mean_metrics": self.mean_metrics,
            "best_fold": self.best_fold,
            "folds": [
                {
                    "fold": f.fold,
                    "metrics": f.metrics,
                    "train_indices": f.train_indices[:5],
                    "test_indices": f.test_indices[:5],
                }
                for f in self.folds
            ],
        }


class TimeSeriesSplitter:
    """Generate time-ordered train/test index splits.

    Two modes are supported:

    * **rolling** — fixed-size training window that advances.
    * **expanding** — training window grows each fold.

    Usage::

        splitter = TimeSeriesSplitter(n_splits=5, train_size=100)
        for train_idx, test_idx in splitter.split(n_samples=500):
            ...
    """

    def __init__(
        self,
        n_splits: int = 5,
        train_size: int | None = None,
        test_size: int | None = None,
        gap: int = 0,
        expanding: bool = False,
    ) -> None:
        """Initialise the splitter.

        Args:
            n_splits: Number of folds.
            train_size: Fixed training window size (rolling mode).
                Defaults to ``0.6 * n_samples`` when None.
            test_size: Test window size per fold.  Defaults to
                ``n_samples // (n_splits + 1)``.
            gap: Number of samples skipped between train and test.
            expanding: Whether the training window expands each fold.
        """
        if n_splits < 2:
            raise ValueError("n_splits must be at least 2.")
        self._n_splits = n_splits
        self._train_size = train_size
        self._test_size = test_size
        self._gap = gap
        self._expanding = expanding

    def split(self, n_samples: int) -> list[tuple[np.ndarray, np.ndarray]]:
        """Generate the fold index pairs.

        Args:
            n_samples: Total number of samples.

        Returns:
            List of ``(train_indices, test_indices)`` numpy arrays.
        """
        if n_samples < self._n_splits * 2:
            raise ValueError(
                f"n_samples={n_samples} is too small for {self._n_splits} folds."
            )

        test_size = self._test_size or max(1, n_samples // (self._n_splits + 1))
        train_size = self._train_size or int(n_samples * 0.6)

        folds: list[tuple[np.ndarray, np.ndarray]] = []
        for i in range(self._n_splits):
            test_start = (
                n_samples - (self._n_splits - i) * test_size
            )
            test_end = test_start + test_size
            if self._expanding:
                train_start = 0
            else:
                train_start = max(0, test_start - train_size)
            train_end = max(train_start, test_start - self._gap)

            train_idx = np.arange(train_start, train_end)
            test_idx = np.arange(test_start, test_end)
            if len(train_idx) == 0 or len(test_idx) == 0:
                continue
            folds.append((train_idx, test_idx))

        if not folds:
            raise ValueError("No valid folds could be generated.")
        return folds


def cross_validate(
    model: Any,
    dataset: MLDataset,
    n_splits: int = 5,
    train_size: int | None = None,
    expanding: bool = False,
    primary_metric: str = "accuracy",
) -> CVResult:
    """Run time-series cross-validation for a model.

    Args:
        model: An object with ``fit``/``predict`` (or ``predict`` only
            for evaluation of pre-trained models).
        dataset: The labelled :class:`MLDataset`.
        n_splits: Number of folds.
        train_size: Fixed training window size.
        expanding: Expanding-window mode.
        primary_metric: Metric used to pick ``best_fold``.

    Returns:
        An aggregated :class:`CVResult`.
    """
    splitter = TimeSeriesSplitter(
        n_splits=n_splits,
        train_size=train_size,
        expanding=expanding,
    )
    splits = splitter.split(dataset.n_samples)

    rounds: list[CVRound] = []
    metric_names: set[str] = set()

    for fold, (train_idx, test_idx) in enumerate(splits):
        fold_model = copy.deepcopy(model) if hasattr(model, "fit") else model
        if hasattr(fold_model, "fit"):
            fold_model.fit(dataset.X[train_idx], dataset.y[train_idx])
        y_pred = fold_model.predict(dataset.X[test_idx])
        y_true = dataset.y[test_idx]

        if dataset.task == "classification":
            metrics = evaluate_classification(y_true, y_pred).to_dict()
        else:
            metrics = evaluate_regression(y_true, y_pred).to_dict()

        metric_names.update(metrics.keys())
        rounds.append(
            CVRound(
                fold=fold,
                train_indices=train_idx.tolist(),
                test_indices=test_idx.tolist(),
                metrics=metrics,
            )
        )

    mean_metrics: dict[str, float] = {}
    for name in metric_names:
        values = [r.metrics.get(name, 0.0) for r in rounds]
        # Only aggregate scalar metrics.  Non-scalar entries (e.g. the
        # confusion matrix list) can have different shapes per fold and
        # cannot be averaged into a numpy array.
        numeric = [v for v in values if isinstance(v, (int, float))]
        if numeric:
            mean_metrics[name] = float(np.mean(numeric))
    best_fold = int(
        max(rounds, key=lambda r: r.metrics.get(primary_metric, -1.0)).fold
    )

    return CVResult(
        folds=rounds,
        mean_metrics=mean_metrics,
        best_fold=best_fold,
    )
