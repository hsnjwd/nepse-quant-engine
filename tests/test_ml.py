"""Tests for the ML subsystem (src/ml)."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from src.ml.cross_validation import (
    TimeSeriesSplitter,
    cross_validate,
)
from src.ml.dataset import DatasetBuilder, MLDataset
from src.ml.evaluation import (
    evaluate_classification,
    evaluate_regression,
)
from src.ml.feature_engineering import FeatureConfig, FeatureEngineer
from src.ml.models import (
    NumpyLinearRegression,
    NumpyLogisticRegression,
    available_models,
    default_model_name,
    get_model,
)
from src.ml.model_manager import ModelManager
from src.ml.prediction_engine import PredictionEngine
from src.ml.training import TrainingPipeline
from src.ml.versioning import ModelVersion, VersionStore, bump_version, validate_version


def make_ohlcv(n: int = 260, seed: int = 1) -> pd.DataFrame:
    """Build a deterministic OHLCV DataFrame for tests.

    The default feature set includes SMA_100/SMA_200 rolling windows,
    so frames must contain at least 200 rows for ``dropna`` not to
    discard everything.
    """
    rng = np.random.default_rng(seed)
    dates = pd.date_range("2025-01-01", periods=n, freq="D")
    close = 100 * np.cumprod(1 + rng.normal(0.0005, 0.01, n))
    open_ = close * (1 + rng.normal(0, 0.005, n))
    high = np.maximum(open_, close) * (1 + np.abs(rng.normal(0, 0.005, n)))
    low = np.minimum(open_, close) * (1 - np.abs(rng.normal(0, 0.005, n)))
    volume = rng.integers(10_000, 100_000, n)
    return pd.DataFrame(
        {
            "Date": dates,
            "Open": open_,
            "High": high,
            "Low": low,
            "Close": close,
            "Volume": volume,
        }
    )


# ═══════════════════════════════════════════════════════════════════
# Feature engineering
# ═══════════════════════════════════════════════════════════════════


class TestFeatureEngineer:
    def test_generates_default_features(self) -> None:
        df = make_ohlcv()
        result = FeatureEngineer().generate(df)
        assert "RSI" in result.columns
        assert "MACD" in result.columns
        assert "ATR" in result.columns
        assert "SMA_20" in result.columns
        assert "EMA_20" in result.columns

    def test_feature_columns_exclude_base_ohlcv(self) -> None:
        df = make_ohlcv()
        engineer = FeatureEngineer()
        result = engineer.generate(df)
        cols = engineer.feature_columns(result)
        assert "Close" not in cols
        assert "Date" not in cols
        assert "RSI" in cols

    def test_unknown_feature_raises(self) -> None:
        with pytest.raises(ValueError):
            FeatureConfig(features=["not_a_feature"])

    def test_min_rows_enforced(self) -> None:
        engineer = FeatureEngineer(FeatureConfig(min_rows=5000))
        with pytest.raises(ValueError):
            engineer.generate(make_ohlcv(n=60))

    def test_custom_feature_subset(self) -> None:
        config = FeatureConfig(features=["returns", "rsi"])
        result = FeatureEngineer(config).generate(make_ohlcv())
        assert "RSI" in result.columns
        assert "MACD" not in result.columns

    def test_requires_dataframe(self) -> None:
        with pytest.raises(TypeError):
            FeatureEngineer().generate([1, 2, 3])


# ═══════════════════════════════════════════════════════════════════
# Dataset building
# ═══════════════════════════════════════════════════════════════════


class TestDatasetBuilder:
    def test_build_classification_labels(self) -> None:
        df = make_ohlcv(n=260)
        dataset = DatasetBuilder().build_classification(df, horizon=5)
        assert dataset.task == "classification"
        assert dataset.n_samples > 0
        assert set(dataset.labels) == {0, 1, 2}
        assert dataset.n_features > 5

    def test_build_regression(self) -> None:
        df = make_ohlcv(n=260)
        dataset = DatasetBuilder().build_regression(df, horizon=1)
        assert dataset.task == "regression"
        assert dataset.n_samples > 0

    def test_split_time_ordered(self) -> None:
        df = make_ohlcv(n=260)
        dataset = DatasetBuilder().build_classification(df)
        train, test = DatasetBuilder.split(dataset, test_size=0.2)
        assert train.n_samples + test.n_samples == dataset.n_samples
        assert train.indices[0] < test.indices[0]

    def test_split_shuffle(self) -> None:
        dataset = MLDataset(
            X=np.arange(100).reshape(50, 2),
            y=np.arange(50),
            feature_names=["a", "b"],
        )
        train, test = DatasetBuilder.split(dataset, test_size=0.2, shuffle=True, seed=1)
        assert train.n_samples + test.n_samples == 50

    def test_to_dataframe(self) -> None:
        df = make_ohlcv(n=260)
        dataset = DatasetBuilder().build_classification(df)
        frame = dataset.to_dataframe()
        assert "target" in frame.columns
        assert len(frame) == dataset.n_samples


# ═══════════════════════════════════════════════════════════════════
# Models
# ═══════════════════════════════════════════════════════════════════


class TestModels:
    def test_numpy_logistic_fit_predict(self) -> None:
        rng = np.random.default_rng(0)
        X = rng.normal(size=(100, 4))
        y = (X[:, 0] + X[:, 1] > 0).astype(int)
        model = NumpyLogisticRegression(epochs=300)
        model.fit(X, y)
        preds = model.predict(X)
        assert preds.shape == (100,)
        proba = model.predict_proba(X)
        assert proba.shape == (100, 2)

    def test_numpy_logistic_single_class(self) -> None:
        model = NumpyLogisticRegression()
        model.fit(np.zeros((20, 3)), np.zeros(20, dtype=int))
        preds = model.predict(np.zeros((5, 3)))
        assert (preds == 0).all()

    def test_numpy_ridge_fit_predict(self) -> None:
        X = np.linspace(0, 1, 50).reshape(-1, 1)
        y = 2 * X[:, 0] + 0.5
        model = NumpyLinearRegression()
        model.fit(X, y)
        preds = model.predict(X)
        assert np.allclose(preds, y, atol=1e-2)

    def test_registry_has_models(self) -> None:
        names = available_models()
        assert "random_forest" in names
        assert "logistic_regression" in names
        assert "numpy_logistic" in names

    def test_get_model_unknown(self) -> None:
        with pytest.raises(KeyError):
            get_model("does_not_exist")

    def test_default_model_name_valid(self) -> None:
        assert default_model_name() in available_models()

    def test_sklearn_wrapper_falls_back(self) -> None:
        # Should not raise even without sklearn installed.
        model = get_model("random_forest")
        X = np.random.default_rng(1).normal(size=(60, 4))
        y = np.random.default_rng(2).integers(0, 3, 60)
        model.fit(X, y)
        assert model.predict(X).shape == (60,)

    def test_describe(self) -> None:
        model = get_model("numpy_logistic")
        desc = model.describe()
        assert desc["name"] == "numpy_logistic"
        assert desc["task"] == "classification"


# ═══════════════════════════════════════════════════════════════════
# Evaluation
# ═══════════════════════════════════════════════════════════════════


class TestEvaluation:
    def test_classification_perfect(self) -> None:
        y = np.array([0, 1, 2, 0, 1])
        metrics = evaluate_classification(y, y)
        assert metrics.accuracy == 1.0
        assert metrics.precision == 1.0
        assert metrics.recall == 1.0

    def test_classification_empty(self) -> None:
        metrics = evaluate_classification([], [])
        assert metrics.accuracy == 0.0

    def test_confusion_matrix_shape(self) -> None:
        y_true = [0, 1, 2, 0]
        y_pred = [0, 2, 2, 0]
        metrics = evaluate_classification(y_true, y_pred)
        assert metrics.confusion_matrix.shape == (3, 3)

    def test_regression_metrics(self) -> None:
        y_true = np.array([1.0, 2.0, 3.0])
        y_pred = np.array([1.1, 1.9, 3.2])
        metrics = evaluate_regression(y_true, y_pred)
        assert metrics.mae > 0
        assert metrics.rmse > 0
        assert metrics.r2 > 0.9

    def test_regression_metrics_dict(self) -> None:
        metrics = evaluate_regression([1.0, 2.0], [1.0, 2.0])
        assert "mae" in metrics.to_dict()


# ═══════════════════════════════════════════════════════════════════
# Cross-validation
# ═══════════════════════════════════════════════════════════════════


class TestCrossValidation:
    def test_splitter_counts(self) -> None:
        splitter = TimeSeriesSplitter(n_splits=5, train_size=50, test_size=10)
        folds = splitter.split(200)
        assert len(folds) == 5
        for train_idx, test_idx in folds:
            assert len(train_idx) == 50
            assert len(test_idx) == 10

    def test_splitter_expanding(self) -> None:
        splitter = TimeSeriesSplitter(n_splits=4, expanding=True, test_size=10)
        folds = splitter.split(120)
        assert len(folds) == 4

    def test_splitter_too_small(self) -> None:
        splitter = TimeSeriesSplitter(n_splits=5)
        with pytest.raises(ValueError):
            splitter.split(4)

    def test_cross_validate_classification(self) -> None:
        df = make_ohlcv(n=260)
        dataset = DatasetBuilder().build_classification(df)
        result = cross_validate(get_model("numpy_logistic"), dataset, n_splits=3)
        assert len(result.folds) == 3
        assert "accuracy" in result.mean_metrics

    def test_cross_validate_to_dict(self) -> None:
        df = make_ohlcv(n=260)
        dataset = DatasetBuilder().build_classification(df)
        result = cross_validate(get_model("numpy_logistic"), dataset, n_splits=3)
        data = result.to_dict()
        assert data["n_folds"] == 3


# ═══════════════════════════════════════════════════════════════════
# Versioning
# ═══════════════════════════════════════════════════════════════════


class TestVersioning:
    def test_validate_version(self) -> None:
        assert validate_version("1.2.3") == "1.2.3"
        with pytest.raises(ValueError):
            validate_version("abc")

    def test_bump_patch(self) -> None:
        assert bump_version("1.2.3") == "1.2.4"

    def test_bump_minor(self) -> None:
        assert bump_version("1.2.3", "minor") == "1.3.0"

    def test_bump_major(self) -> None:
        assert bump_version("1.2.3", "major") == "2.0.0"

    def test_store_roundtrip(self, tmp_path: Path) -> None:
        store = VersionStore(tmp_path)
        record = ModelVersion(model_name="test_model", version="1.0.0")
        store.save(record)
        versions = store.list_versions("test_model")
        assert len(versions) == 1
        assert versions[0].version == "1.0.0"

    def test_store_latest(self, tmp_path: Path) -> None:
        store = VersionStore(tmp_path)
        store.save(ModelVersion(model_name="m", version="1.0.0"))
        store.save(ModelVersion(model_name="m", version="1.0.1"))
        latest = store.latest("m")
        assert latest is not None
        assert latest.version == "1.0.1"

    def test_store_all_models(self, tmp_path: Path) -> None:
        store = VersionStore(tmp_path)
        store.save(ModelVersion(model_name="a", version="1.0.0"))
        store.save(ModelVersion(model_name="b", version="1.0.0"))
        assert sorted(store.all_models()) == ["a", "b"]


# ═══════════════════════════════════════════════════════════════════
# Model manager
# ═══════════════════════════════════════════════════════════════════


class TestModelManager:
    def test_save_load(self, tmp_path: Path) -> None:
        manager = ModelManager(tmp_path)
        model = get_model("numpy_logistic")
        model.fit(np.random.default_rng(0).normal(size=(40, 3)), np.arange(40) % 2)
        record = manager.save(model, name="numpy_logistic", metrics={"accuracy": 0.5})
        assert record.version == "1.0.0"

        loaded = manager.load("numpy_logistic")
        assert loaded is not None
        assert loaded.predict(np.zeros((2, 3))).shape == (2,)

    def test_load_missing(self, tmp_path: Path) -> None:
        manager = ModelManager(tmp_path)
        assert manager.load("missing") is None

    def test_list_models(self, tmp_path: Path) -> None:
        manager = ModelManager(tmp_path)
        assert manager.list_models() == []
        model = get_model("numpy_logistic")
        model.fit(np.eye(6), np.arange(6) % 2)
        manager.save(model, name="numpy_logistic")
        models = manager.list_models()
        assert len(models) == 1
        assert models[0]["name"] == "numpy_logistic"

    def test_version_bump_on_save(self, tmp_path: Path) -> None:
        manager = ModelManager(tmp_path)
        model = get_model("numpy_logistic")
        model.fit(np.eye(6), np.arange(6) % 2)
        r1 = manager.save(model, name="numpy_logistic")
        r2 = manager.save(model, name="numpy_logistic")
        assert r2.version == "1.0.1"

    def test_delete(self, tmp_path: Path) -> None:
        manager = ModelManager(tmp_path)
        model = get_model("numpy_logistic")
        model.fit(np.eye(6), np.arange(6) % 2)
        manager.save(model, name="numpy_logistic")
        assert manager.delete("numpy_logistic") is True
        assert manager.list_models() == []


# ═══════════════════════════════════════════════════════════════════
# Prediction engine
# ═══════════════════════════════════════════════════════════════════


class TestPredictionEngine:
    def test_predict_classification_with_trained_model(self) -> None:
        df = make_ohlcv(n=260)
        dataset = DatasetBuilder().build_classification(df)
        model = get_model("numpy_logistic")
        model.fit(dataset.X, dataset.y)
        result = PredictionEngine().predict(df, model=model)
        assert result.signal in ("BUY", "HOLD", "SELL")
        assert 0.0 <= result.probability <= 1.0

    def test_predict_requires_trained_model(self) -> None:
        df = make_ohlcv(n=260)
        with pytest.raises(ValueError):
            PredictionEngine().predict(df, model_name="numpy_logistic")

    def test_predict_regression(self) -> None:
        df = make_ohlcv(n=260)
        dataset = DatasetBuilder().build_regression(df)
        model = NumpyLinearRegression()
        model.fit(dataset.X, dataset.y)
        result = PredictionEngine().predict(df, model=model, task="regression")
        assert result.predicted_return is not None

    def test_predict_to_dict(self) -> None:
        df = make_ohlcv(n=260)
        dataset = DatasetBuilder().build_classification(df)
        model = get_model("numpy_logistic")
        model.fit(dataset.X, dataset.y)
        result = PredictionEngine().predict(df, model=model)
        data = result.to_dict()
        assert "signal" in data


# ═══════════════════════════════════════════════════════════════════
# Training pipeline
# ═══════════════════════════════════════════════════════════════════


class TestTrainingPipeline:
    def test_run_classification(self) -> None:
        df = make_ohlcv(n=280)
        result = TrainingPipeline().run(df, model_name="numpy_logistic")
        assert result.task == "classification"
        assert "accuracy" in result.metrics

    def test_run_regression(self) -> None:
        df = make_ohlcv(n=280)
        result = TrainingPipeline().run(
            df, model_name="numpy_ridge", task="regression"
        )
        assert result.task == "regression"
        assert "rmse" in result.metrics

    def test_invalid_task(self) -> None:
        with pytest.raises(ValueError):
            TrainingPipeline().run(make_ohlcv(), task="bogus")

    def test_to_dict(self) -> None:
        df = make_ohlcv(n=280)
        result = TrainingPipeline().run(df, model_name="numpy_logistic")
        data = result.to_dict()
        assert data["model_name"] == "numpy_logistic"
