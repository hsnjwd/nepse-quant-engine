"""Dataset construction for supervised trading models.

Builds labelled datasets from OHLCV data for two task types:

* **Classification** — BUY / HOLD / SELL labels derived from forward
  returns against configurable thresholds.
* **Regression** — next-day (or *horizon*-day) forward returns.

Datasets expose a consistent ``(X, y)`` interface together with feature
names and a time-ordered train/test split helper.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd

from src.ml.feature_engineering import FeatureConfig, FeatureEngineer
from src.ml.utils import require_dataframe

logger = logging.getLogger("nepse.ml.dataset")

# Classification labels
BUY = 2
HOLD = 1
SELL = 0


@dataclass
class MLDataset:
    """A labelled dataset ready for model training.

    Attributes:
        X: Feature matrix (rows x columns).
        y: Target vector.
        feature_names: Column names of the feature matrix.
        indices: Original DataFrame index positions for each row.
        task: Either ``"classification"`` or ``"regression"``.
        target_name: Description of the target variable.
        labels: Optional mapping of integer labels to names.
    """

    X: np.ndarray
    y: np.ndarray
    feature_names: list[str]
    indices: list[Any] = field(default_factory=list)
    task: str = "classification"
    target_name: str = "forward_return"
    labels: dict[int, str] = field(default_factory=dict)

    @property
    def n_features(self) -> int:
        """Return the number of features."""
        return self.X.shape[1] if self.X.ndim == 2 else 0

    @property
    def n_samples(self) -> int:
        """Return the number of rows."""
        return len(self.y)

    def to_dataframe(self) -> pd.DataFrame:
        """Return the dataset as a single pandas DataFrame.

        Returns:
            DataFrame with feature columns plus the ``y`` target.
        """
        data = {name: self.X[:, i] for i, name in enumerate(self.feature_names)}
        data["target"] = self.y
        return pd.DataFrame(data)


class DatasetBuilder:
    """Build labelled datasets from engineered features.

    Usage::

        builder = DatasetBuilder()
        dataset = builder.build_classification(
            df, horizon=5, buy_threshold=0.01, sell_threshold=-0.01
        )
    """

    def __init__(
        self,
        feature_config: FeatureConfig | None = None,
        engineer: FeatureEngineer | None = None,
    ) -> None:
        """Initialise the builder.

        Args:
            feature_config: Optional feature configuration.
            engineer: Optional feature engineer.  Defaults to a new
                engineer using *feature_config*.
        """
        self._config = feature_config or FeatureConfig()
        self._engineer = engineer or FeatureEngineer(self._config)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def build_classification(
        self,
        df: Any,
        horizon: int = 5,
        buy_threshold: float = 0.01,
        sell_threshold: float = -0.01,
    ) -> MLDataset:
        """Build a BUY/HOLD/SELL classification dataset.

        Args:
            df: OHLCV DataFrame.
            horizon: Forward return window in days.
            buy_threshold: Forward return above which label = BUY.
            sell_threshold: Forward return below which label = SELL.

        Returns:
            A :class:`MLDataset` with labels ``{0: SELL, 1: HOLD,
            2: BUY}``.
        """
        features = self._engineer.generate(df)
        forward = features["Close"].shift(-horizon) / features["Close"] - 1

        labels = pd.Series(np.full(len(features), HOLD, dtype=int), index=features.index)
        labels[forward > buy_threshold] = BUY
        labels[forward < sell_threshold] = SELL

        return self._to_dataset(
            features=features,
            labels=labels,
            task="classification",
            target_name=f"forward_return_{horizon}d",
            labels_map={0: "SELL", 1: "HOLD", 2: "BUY"},
        )

    def build_regression(
        self,
        df: Any,
        horizon: int = 1,
    ) -> MLDataset:
        """Build a forward-return regression dataset.

        Args:
            df: OHLCV DataFrame.
            horizon: Forward return window in days.

        Returns:
            A :class:`MLDataset` for regression.
        """
        features = self._engineer.generate(df)
        target = features["Close"].shift(-horizon) / features["Close"] - 1

        return self._to_dataset(
            features=features,
            labels=target,
            task="regression",
            target_name=f"forward_return_{horizon}d",
        )

    # ------------------------------------------------------------------
    # Splitting
    # ------------------------------------------------------------------

    @staticmethod
    def split(
        dataset: MLDataset,
        test_size: float = 0.2,
        shuffle: bool = False,
        seed: int | None = None,
    ) -> tuple[MLDataset, MLDataset]:
        """Split a dataset into train and test subsets.

        The split is time-ordered by default (no shuffle), which is
        appropriate for financial time series.

        Args:
            dataset: The dataset to split.
            test_size: Fraction of rows for the test set (0–1).
            shuffle: Whether to shuffle before splitting.  Defaults to
                False to preserve temporal order.
            seed: Optional seed when shuffling.

        Returns:
            A ``(train, test)`` tuple of :class:`MLDataset` objects.
        """
        if not 0 < test_size < 1:
            raise ValueError("test_size must be between 0 and 1.")

        n = dataset.n_samples
        n_test = max(1, int(n * test_size))
        n_train = n - n_test

        order = np.arange(n)
        if shuffle:
            rng = np.random.default_rng(seed)
            rng.shuffle(order)

        train_idx = order[:n_train]
        test_idx = order[n_train:]

        def _slice(indices: np.ndarray) -> MLDataset:
            source_indices = dataset.indices or list(range(dataset.n_samples))
            return MLDataset(
                X=dataset.X[indices],
                y=dataset.y[indices],
                feature_names=list(dataset.feature_names),
                indices=[source_indices[i] for i in indices],
                task=dataset.task,
                target_name=dataset.target_name,
                labels=dict(dataset.labels),
            )

        return _slice(train_idx), _slice(test_idx)

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _to_dataset(
        self,
        features: pd.DataFrame,
        labels: Any,
        task: str,
        target_name: str,
        labels_map: dict[int, str] | None = None,
    ) -> MLDataset:
        """Convert an engineered frame and labels into an MLDataset."""
        feature_cols = self._engineer.feature_columns(features)

        # Accept both pandas Series and numpy arrays as labels.
        if not isinstance(labels, pd.Series):
            labels = pd.Series(labels, index=features.index)
        valid = ~labels.isna()
        X = features.loc[valid, feature_cols].to_numpy(dtype=float)
        y = labels[valid].to_numpy(dtype=float)
        indices = list(features.index[valid])

        logger.info(
            "Built %s dataset: %d samples, %d features (%s).",
            task,
            len(y),
            X.shape[1],
            target_name,
        )
        return MLDataset(
            X=X,
            y=y,
            feature_names=feature_cols,
            indices=indices,
            task=task,
            target_name=target_name,
            labels=labels_map or {},
        )
