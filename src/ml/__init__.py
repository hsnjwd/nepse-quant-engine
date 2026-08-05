"""Machine-learning subsystem for the NEPSE Quant Engine.

Provides feature engineering, dataset construction, model wrappers
with graceful fallbacks, training, evaluation, cross-validation,
versioning, persistence, and prediction.

The subsystem degrades gracefully when scikit-learn / XGBoost /
LightGBM are not installed by falling back to pure-numpy baseline
models.
"""

from __future__ import annotations

from src.ml.dataset import DatasetBuilder, MLDataset
from src.ml.evaluation import (
    ClassificationMetrics,
    RegressionMetrics,
    evaluate_classification,
    evaluate_regression,
)
from src.ml.feature_engineering import FeatureConfig, FeatureEngineer
from src.ml.models import (
    BaseModel,
    MODEL_REGISTRY,
    available_models,
    default_model_name,
    get_model,
)
from src.ml.model_manager import ModelManager
from src.ml.prediction_engine import PredictionEngine, PredictionResult
from src.ml.training import TrainingPipeline, TrainingResult
from src.ml.versioning import ModelVersion, VersionStore

__all__ = [
    "DatasetBuilder",
    "MLDataset",
    "ClassificationMetrics",
    "RegressionMetrics",
    "evaluate_classification",
    "evaluate_regression",
    "FeatureConfig",
    "FeatureEngineer",
    "BaseModel",
    "MODEL_REGISTRY",
    "available_models",
    "default_model_name",
    "get_model",
    "ModelManager",
    "PredictionEngine",
    "PredictionResult",
    "TrainingPipeline",
    "TrainingResult",
    "ModelVersion",
    "VersionStore",
]
