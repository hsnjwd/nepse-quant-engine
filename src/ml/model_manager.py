"""Model manager for the ML subsystem.

Handles persistence of trained models to disk (pickle) together with
their :class:`ModelVersion` metadata, automatic loading, and model
retrieval by name.
"""

from __future__ import annotations

import logging
import pickle
from pathlib import Path
from typing import Any

from src.ml.models import BaseModel, available_models, get_model
from src.ml.versioning import ModelVersion, VersionStore

logger = logging.getLogger("nepse.ml.manager")


class ModelManager:
    """Save, load, and list trained models.

    Usage::

        manager = ModelManager(Path("models"))
        manager.save(model, name="random_forest", metrics=metrics)
        loaded = manager.load("random_forest")
    """

    def __init__(self, directory: str | Path = "models") -> None:
        """Initialise the manager.

        Args:
            directory: Root directory for model artifacts and version
                metadata.
        """
        self._dir = Path(directory)
        self._dir.mkdir(parents=True, exist_ok=True)
        self._versions = VersionStore(self._dir / "versions")

    @property
    def directory(self) -> Path:
        """Return the model root directory."""
        return self._dir

    @property
    def versions(self) -> VersionStore:
        """Return the underlying version store."""
        return self._versions

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def save(
        self,
        model: BaseModel,
        name: str | None = None,
        metrics: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
        version: str | None = None,
        tags: list[str] | None = None,
    ) -> ModelVersion:
        """Persist a trained model and its metadata.

        Args:
            model: The trained model wrapper.
            name: Model name.  Defaults to ``model.name``.
            metrics: Optional evaluation metrics.
            params: Optional hyperparameters.
            version: Explicit version; defaults to a patch bump of the
                latest stored version.
            tags: Optional free-form tags.

        Returns:
            The recorded :class:`ModelVersion`.
        """
        model_name = (name or model.name).lower()
        if model_name not in available_models() and not isinstance(
            model, BaseModel
        ):
            logger.warning(
                "Model name '%s' is not in the registry.", model_name
            )

        latest = self._versions.latest(model_name)
        if version is None:
            version = "1.0.0" if latest is None else _bump(latest.version)

        record = ModelVersion(
            model_name=model_name,
            version=version,
            task=model.task,
            metrics=metrics or {},
            params=params or {},
            tags=tags or [],
        )

        model_dir = self._dir / model_name
        model_dir.mkdir(parents=True, exist_ok=True)
        artifact = model_dir / f"{record.version_id}.pkl"
        with artifact.open("wb") as f:
            pickle.dump(model, f)
        record.path = str(artifact)

        self._versions.save(record)
        logger.info(
            "Saved model '%s' version %s (%s).",
            model_name,
            version,
            artifact,
        )
        return record

    def load(self, name: str, version: str | None = None) -> BaseModel | None:
        """Load a trained model by name and optional version.

        Args:
            name: Model registry name.
            version: Specific version string; defaults to the latest.

        Returns:
            The deserialised model, or ``None`` if unavailable.
        """
        model_name = name.lower()
        if version is None:
            record = self._versions.latest(model_name)
        else:
            records = [
                r
                for r in self._versions.list_versions(model_name)
                if r.version == version
            ]
            record = records[0] if records else None

        if record is None or not record.path:
            logger.info("No stored model for '%s' version %s.", name, version)
            return None

        path = Path(record.path)
        if not path.exists():
            logger.warning("Model artifact missing: %s", path)
            return None

        try:
            with path.open("rb") as f:
                model = pickle.load(f)
            logger.info("Loaded model '%s' version %s.", name, record.version)
            return model
        except Exception as exc:
            logger.error("Failed to load model '%s': %s", name, exc)
            return None

    # ------------------------------------------------------------------
    # Active-version markers (additive, for UI promote/rollback)
    # ------------------------------------------------------------------

    def set_active(self, name: str, version: str) -> bool:
        """Mark a stored version of a model as the active (promoted) one.

        Args:
            name: Model registry name.
            version: Version string that must exist in the store.

        Returns:
            True when the version exists and was marked active.
        """
        model_name = name.lower()
        records = [
            r for r in self._versions.list_versions(model_name)
            if r.version == version
        ]
        if not records:
            return False
        model_dir = self._dir / model_name
        model_dir.mkdir(parents=True, exist_ok=True)
        (model_dir / "ACTIVE").write_text(version, encoding="utf-8")
        logger.info("Promoted model '%s' to active version %s.", model_name, version)
        return True

    def active_version(self, name: str) -> str | None:
        """Return the active (promoted) version of a model, if any.

        Args:
            name: Model registry name.

        Returns:
            The active version string, or ``None`` when unset.
        """
        marker = self._dir / name.lower() / "ACTIVE"
        if marker.exists():
            try:
                version = marker.read_text(encoding="utf-8").strip()
                if any(
                    r.version == version
                    for r in self._versions.list_versions(name.lower())
                ):
                    return version
            except OSError:
                pass
        return None

    def list_models(self) -> list[dict[str, Any]]:
        """Return metadata for every stored model.

        Returns:
            List of dictionaries summarising each stored model.
        """
        result: list[dict[str, Any]] = []
        for model_name in self._versions.all_models():
            latest = self._versions.latest(model_name)
            if latest is not None:
                result.append(
                    {
                        "name": model_name,
                        "version": latest.version,
                        "created_at": latest.created_at,
                        "metrics": latest.metrics,
                        "tags": latest.tags,
                    }
                )
        return sorted(result, key=lambda item: item["name"])

    def delete(self, name: str) -> bool:
        """Delete every stored version of a model.

        Args:
            name: Model registry name.

        Returns:
            True when at least one artifact was removed.
        """
        model_name = name.lower()
        removed = False
        model_dir = self._dir / model_name
        for path in self._versions.list_versions(model_name):
            artifact = Path(path.path)
            if artifact.exists():
                artifact.unlink()
            self._versions.delete(path.version_id)
            removed = True
        if model_dir.exists() and not any(model_dir.iterdir()):
            model_dir.rmdir()
        return removed


def _bump(version: str) -> str:
    """Bump the patch component of a version."""
    from src.ml.versioning import bump_version

    return bump_version(version, part="patch")
