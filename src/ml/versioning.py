"""Model versioning for the ML subsystem.

Tracks model metadata, training history, and versioned storage of
trained models so that models can be saved, loaded, and compared
across experiments.
"""

from __future__ import annotations

import json
import logging
import re
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger("nepse.ml.versioning")

_VERSION_RE = re.compile(r"^\d+\.\d+\.\d+$")


def _now_iso() -> str:
    """Return the current UTC timestamp as an ISO string."""
    return datetime.now(timezone.utc).isoformat()


@dataclass
class ModelVersion:
    """Metadata record for one trained model version.

    Attributes:
        model_name: Model registry name (e.g. ``"random_forest"``).
        version: Semantic version string.
        task: Classification or regression.
        metrics: Evaluation metrics recorded at training time.
        params: Model hyperparameters.
        created_at: ISO timestamp of creation.
        version_id: Unique identifier for the version.
        path: Optional file path where the model is stored.
        tags: Optional free-form tags.
    """

    model_name: str
    version: str = "1.0.0"
    task: str = "classification"
    metrics: dict[str, Any] = field(default_factory=dict)
    params: dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=_now_iso)
    version_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    path: str = ""
    tags: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serialisable dictionary."""
        return asdict(self)


def validate_version(version: str) -> str:
    """Validate a semantic-version string.

    Args:
        version: Version string like ``"1.2.3"``.

    Returns:
        The validated version string.

    Raises:
        ValueError: If the version is not in ``major.minor.patch`` form.
    """
    version = version.strip()
    if not _VERSION_RE.match(version):
        raise ValueError(
            f"Invalid version '{version}'. Use semantic versioning "
            "like '1.2.3'."
        )
    return version


def bump_version(version: str, part: str = "patch") -> str:
    """Increment a semantic version.

    Args:
        version: Existing version string.
        part: Which component to bump — ``"major"``, ``"minor"`` or
            ``"patch"``.

    Returns:
        The incremented version string.
    """
    major, minor, patch = (int(x) for x in validate_version(version).split("."))
    if part == "major":
        return f"{major + 1}.0.0"
    if part == "minor":
        return f"{major}.{minor + 1}.0"
    return f"{major}.{minor}.{patch + 1}"


class VersionStore:
    """Persist and retrieve :class:`ModelVersion` metadata records.

    Records are stored as JSON files under a directory keyed by model
    name.
    """

    def __init__(self, directory: str | Path) -> None:
        """Initialise the store.

        Args:
            directory: Directory for version metadata files.
        """
        self._dir = Path(directory)
        self._dir.mkdir(parents=True, exist_ok=True)

    @property
    def directory(self) -> Path:
        """Return the metadata directory."""
        return self._dir

    def _model_dir(self, model_name: str) -> Path:
        path = self._dir / model_name.lower()
        path.mkdir(parents=True, exist_ok=True)
        return path

    def save(self, record: ModelVersion) -> Path:
        """Persist a version record.

        Args:
            record: The version record.

        Returns:
            The file path written.
        """
        validate_version(record.version)
        model_dir = self._model_dir(record.model_name)
        path = model_dir / f"{record.version_id}.json"
        path.write_text(
            json.dumps(record.to_dict(), indent=2),
            encoding="utf-8",
        )
        logger.info(
            "Saved version %s of model '%s' to %s.",
            record.version,
            record.model_name,
            path,
        )
        return path

    def list_versions(self, model_name: str) -> list[ModelVersion]:
        """Return all stored versions for a model, newest first.

        Args:
            model_name: Model registry name.

        Returns:
            Chronologically sorted (newest first) version records.
        """
        model_dir = self._dir / model_name.lower()
        if not model_dir.exists():
            return []
        records: list[ModelVersion] = []
        for path in sorted(model_dir.glob("*.json")):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                records.append(ModelVersion(**data))
            except Exception as exc:  # pragma: no cover - defensive
                logger.warning("Could not read version file %s: %s", path, exc)
        records.sort(key=lambda r: r.created_at, reverse=True)
        return records

    def latest(self, model_name: str) -> ModelVersion | None:
        """Return the most recent version of a model.

        Args:
            model_name: Model registry name.

        Returns:
            The newest :class:`ModelVersion` or ``None``.
        """
        versions = self.list_versions(model_name)
        return versions[0] if versions else None

    def all_models(self) -> list[str]:
        """Return all model names that have stored versions."""
        return sorted(
            p.name for p in self._dir.iterdir() if p.is_dir()
        )

    def delete(self, version_id: str) -> bool:
        """Delete a version record by ID.

        Args:
            version_id: The version identifier.

        Returns:
            True when a record was removed.
        """
        for path in self._dir.rglob("*.json"):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                if data.get("version_id") == version_id:
                    path.unlink()
                    return True
            except Exception:  # pragma: no cover - defensive
                continue
        return False
