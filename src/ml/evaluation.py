"""Model evaluation metrics for the ML subsystem.

Provides classification and regression metrics that work regardless of
whether scikit-learn is installed.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

import numpy as np

logger = logging.getLogger("nepse.ml.evaluation")


@dataclass
class ClassificationMetrics:
    """Aggregate classification metrics.

    Attributes:
        accuracy: Fraction of correct predictions.
        precision: Macro-averaged precision.
        recall: Macro-averaged recall.
        f1: Macro-averaged F1 score.
        confusion_matrix: ``n_classes x n_classes`` matrix.
        classes: Class labels.
        support: Per-class sample counts.
    """

    accuracy: float = 0.0
    precision: float = 0.0
    recall: float = 0.0
    f1: float = 0.0
    confusion_matrix: np.ndarray = field(default_factory=lambda: np.zeros((0, 0)))
    classes: list[int] = field(default_factory=list)
    support: list[int] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serialisable dictionary."""
        return {
            "accuracy": round(self.accuracy, 4),
            "precision": round(self.precision, 4),
            "recall": round(self.recall, 4),
            "f1": round(self.f1, 4),
            "classes": self.classes,
            "support": self.support,
            "confusion_matrix": self.confusion_matrix.tolist(),
        }


@dataclass
class RegressionMetrics:
    """Aggregate regression metrics.

    Attributes:
        mae: Mean absolute error.
        mse: Mean squared error.
        rmse: Root mean squared error.
        r2: Coefficient of determination.
        directional_accuracy: Fraction of correct sign predictions.
    """

    mae: float = 0.0
    mse: float = 0.0
    rmse: float = 0.0
    r2: float = 0.0
    directional_accuracy: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serialisable dictionary."""
        return {
            "mae": round(self.mae, 6),
            "mse": round(self.mse, 6),
            "rmse": round(self.rmse, 6),
            "r2": round(self.r2, 4),
            "directional_accuracy": round(self.directional_accuracy, 4),
        }


def evaluate_classification(
    y_true: Any,
    y_pred: Any,
    classes: list[int] | None = None,
) -> ClassificationMetrics:
    """Evaluate classification predictions.

    Args:
        y_true: Ground-truth labels.
        y_pred: Predicted labels.
        classes: Optional explicit class list.

    Returns:
        A :class:`ClassificationMetrics` instance.
    """
    y_true = np.asarray(y_true, dtype=int)
    y_pred = np.asarray(y_pred, dtype=int)

    if len(y_true) == 0:
        return ClassificationMetrics()

    if classes is None:
        classes = sorted(set(y_true.tolist()) | set(y_pred.tolist()))
    class_to_idx = {c: i for i, c in enumerate(classes)}

    n = max(len(y_true), 1)
    accuracy = float(np.mean(y_true == y_pred))

    matrix = np.zeros((len(classes), len(classes)), dtype=int)
    for t, p in zip(y_true, y_pred):
        if t in class_to_idx and p in class_to_idx:
            matrix[class_to_idx[t], class_to_idx[p]] += 1

    precision_list: list[float] = []
    recall_list: list[float] = []
    support: list[int] = []
    for c in classes:
        i = class_to_idx[c]
        tp = matrix[i, i]
        fp = int(matrix[:, i].sum()) - tp
        fn = int(matrix[i, :].sum()) - tp
        support.append(int(matrix[i, :].sum()))
        precision_list.append(tp / (tp + fp) if (tp + fp) else 0.0)
        recall_list.append(tp / (tp + fn) if (tp + fn) else 0.0)

    precision = float(np.mean(precision_list)) if precision_list else 0.0
    recall = float(np.mean(recall_list)) if recall_list else 0.0
    f1 = (
        (2 * precision * recall / (precision + recall))
        if (precision + recall) > 0
        else 0.0
    )

    return ClassificationMetrics(
        accuracy=accuracy,
        precision=precision,
        recall=recall,
        f1=f1,
        confusion_matrix=matrix,
        classes=classes,
        support=support,
    )


def evaluate_regression(
    y_true: Any,
    y_pred: Any,
) -> RegressionMetrics:
    """Evaluate regression predictions.

    Args:
        y_true: Ground-truth continuous values.
        y_pred: Predicted continuous values.

    Returns:
        A :class:`RegressionMetrics` instance.
    """
    y_true = np.asarray(y_true, dtype=float)
    y_pred = np.asarray(y_pred, dtype=float)

    n = max(len(y_true), 1)
    errors = y_true - y_pred
    mae = float(np.mean(np.abs(errors)))
    mse = float(np.mean(errors**2))
    rmse = float(np.sqrt(mse))
    ss_res = float(np.sum(errors**2))
    ss_tot = float(np.sum((y_true - np.mean(y_true)) ** 2))
    r2 = 1 - ss_res / ss_tot if ss_tot > 0 else 0.0

    directional = float(np.mean(np.sign(y_true) == np.sign(y_pred)))
    return RegressionMetrics(
        mae=mae,
        mse=mse,
        rmse=rmse,
        r2=r2,
        directional_accuracy=directional,
    )
