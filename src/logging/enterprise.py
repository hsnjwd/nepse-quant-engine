"""Enterprise logging — structured JSON, rotation, retention, and search.

Extends the base logging configuration with:

* Structured JSON formatter for machine-readable logs.
* Rotating file handlers with configurable retention.
* Named loggers for audit, trade, API, performance, and system events.
* Search utilities over the log directory.
"""

from __future__ import annotations

import gzip
import json
import logging
import re
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any

from src.logging.logger import LOG_LEVEL

LOG_DIR = Path("logs")
LOG_DIR.mkdir(exist_ok=True)

_MAX_BYTES = 10 * 1024 * 1024  # 10 MB
_BACKUP_COUNT = 5


class JsonFormatter(logging.Formatter):
    """Format log records as single-line JSON objects.

    Attributes:
        include_trace: Whether to include exception tracebacks.
    """

    def __init__(self, include_trace: bool = False) -> None:
        """Initialise the formatter.

        Args:
            include_trace: Include ``traceback`` field.
        """
        super().__init__()
        self._include_trace = include_trace

    def format(self, record: logging.LogRecord) -> str:
        """Format a record as JSON.

        Args:
            record: The log record.

        Returns:
            A single-line JSON string.
        """
        payload: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(
                record.created, tz=timezone.utc
            ).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        if record.exc_info and self._include_trace:
            payload["traceback"] = self.formatException(record.exc_info)
        for key, value in getattr(record, "extra_fields", {}).items():
            payload[key] = value
        return json.dumps(payload, default=str)


def _add_extra_fields(record: logging.LogRecord) -> None:
    """Attach empty extra_fields to every record (avoids KeyError)."""
    if not hasattr(record, "extra_fields"):
        record.extra_fields = {}


class RetentionRotatingHandler(RotatingFileHandler):
    """Rotating handler that gzips rotated files beyond a retention cap.

    Attributes:
        retention: Maximum number of rotated archives to keep.
    """

    def __init__(
        self,
        filename: str,
        max_bytes: int = _MAX_BYTES,
        backup_count: int = _BACKUP_COUNT,
        retention: int = _BACKUP_COUNT,
        encoding: str | None = "utf-8",
    ) -> None:
        """Initialise the handler.

        Args:
            filename: Log file path.
            max_bytes: Rotation threshold in bytes.
            backup_count: Number of rotation backups.
            retention: Number of archives to retain.
            encoding: File encoding.
        """
        super().__init__(
            filename,
            maxBytes=max_bytes,
            backupCount=backup_count,
            encoding=encoding,
        )
        self._retention = retention

    def doRollover(self) -> None:  # noqa: N802 - override
        """Perform rotation and prune old archives."""
        super().doRollover()
        base = Path(self.baseFilename)
        archives = sorted(
            base.parent.glob(f"{base.name}.*"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        # Compress rotated files that are not yet compressed.
        for archive in archives:
            if archive.suffix != ".gz":
                try:
                    with archive.open("rb") as f_in, gzip.open(
                        str(archive) + ".gz", "wb"
                    ) as f_out:
                        f_out.write(f_in.read())
                    archive.unlink()
                except OSError:
                    pass
        # Prune beyond retention.
        remaining = sorted(
            base.parent.glob(f"{base.name}.*.gz"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        for stale in remaining[self._retention :]:
            try:
                stale.unlink()
            except OSError:
                pass


def _level_from_env() -> int:
    """Resolve the log level from the LOG_LEVEL env/config value."""
    return getattr(logging, str(LOG_LEVEL).upper(), logging.INFO)


class EnterpriseLogger:
    """Factory for enterprise-grade named loggers.

    Usage::

        el = EnterpriseLogger()
        audit = el.get_logger("audit")
        audit.info("order placed", extra={"order_id": "X"})
    """

    def __init__(
        self,
        log_dir: str | Path = LOG_DIR,
        structured: bool = True,
        include_trace: bool = False,
    ) -> None:
        """Initialise the enterprise logger.

        Args:
            log_dir: Directory for log files.
            structured: Whether to use JSON formatting.
            include_trace: Include tracebacks in JSON records.
        """
        self._log_dir = Path(log_dir)
        self._log_dir.mkdir(parents=True, exist_ok=True)
        self._structured = structured
        self._include_trace = include_trace
        self._formatter = JsonFormatter(include_trace=include_trace)

        root = logging.getLogger("nepse")
        root.setLevel(_level_from_env())
        root.addFilter(_add_extra_fields)

    def get_logger(self, category: str) -> logging.Logger:
        """Return (and lazily configure) a named logger.

        Args:
            category: One of ``audit``, ``trade``, ``api``,
                ``performance``, ``system`` or any other name.

        Returns:
            A configured :class:`logging.Logger`.
        """
        name = f"nepse.{category}"
        logger = logging.getLogger(name)
        if not any(
            isinstance(h, RetentionRotatingHandler) for h in logger.handlers
        ):
            handler = RetentionRotatingHandler(
                str(self._log_dir / f"{category}.log")
            )
            handler.setFormatter(self._formatter)
            logger.addHandler(handler)
            logger.propagate = False
        return logger

    def _logger(self, category: str) -> logging.Logger:
        """Internal alias returning the named logger."""
        return self.get_logger(category)

    @property
    def audit(self) -> logging.Logger:
        """Return the audit logger."""
        return self.get_logger("audit")

    @property
    def trade(self) -> logging.Logger:
        """Return the trade logger."""
        return self.get_logger("trade")

    @property
    def api(self) -> logging.Logger:
        """Return the API logger."""
        return self.get_logger("api")

    @property
    def performance(self) -> logging.Logger:
        """Return the performance logger."""
        return self.get_logger("performance")

    @property
    def system(self) -> logging.Logger:
        """Return the system logger."""
        return self.get_logger("system")


_default = EnterpriseLogger()


def get_logger(category: str) -> logging.Logger:
    """Return an enterprise logger by category (global helper)."""
    return _default.get_logger(category)


def audit_logger() -> logging.Logger:
    """Return the global audit logger."""
    return _default.audit


def trade_logger() -> logging.Logger:
    """Return the global trade logger."""
    return _default.trade


def api_logger() -> logging.Logger:
    """Return the global API logger."""
    return _default.api


def performance_logger() -> logging.Logger:
    """Return the global performance logger."""
    return _default.performance


def system_logger() -> logging.Logger:
    """Return the global system logger."""
    return _default.system


# ---------------------------------------------------------------------------
# Search
# ---------------------------------------------------------------------------

_SEVERITY_ORDER = {
    "DEBUG": 0,
    "INFO": 1,
    "WARNING": 2,
    "ERROR": 3,
    "CRITICAL": 4,
}


def search_logs(
    query: str | None = None,
    level: str | None = None,
    category: str | None = None,
    limit: int = 100,
    log_dir: str | Path = LOG_DIR,
) -> list[dict[str, Any]]:
    """Search log files for matching records.

    Args:
        query: Plain-text substring to match (case-insensitive).
        level: Minimum severity (DEBUG/INFO/WARNING/ERROR/CRITICAL).
        category: Restrict to a category log file.
        limit: Maximum records to return.
        log_dir: Log directory.

    Returns:
        List of parsed log records (JSON when available).
    """
    patterns = [Path(log_dir) / f"{category}.log"] if category else list(
        Path(log_dir).glob("*.log")
    )
    matches: list[dict[str, Any]] = []
    min_severity = _SEVERITY_ORDER.get(str(level).upper(), 0)

    for path in patterns:
        if not path.exists():
            continue
        try:
            lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()
        except OSError:
            continue
        for line in lines[-2000:]:
            record = _parse_line(line)
            if record is None:
                continue
            if _SEVERITY_ORDER.get(record.get("level", ""), 0) < min_severity:
                continue
            if query and query.lower() not in line.lower():
                continue
            matches.append(record)
            if len(matches) >= limit:
                return matches
    return matches


def _parse_line(line: str) -> dict[str, Any] | None:
    """Parse a single log line, trying JSON first then plain text."""
    stripped = line.strip()
    if not stripped:
        return None
    try:
        return json.loads(stripped)
    except (ValueError, TypeError):
        pass
    # Plain text fallback — extract timestamp, level, and message.
    match = re.match(
        r"^([\d\-T:+,.\s]+)\s+\|\s+(\w+)\s+\|\s+([\w.]+)\s+\|\s+(.*)$",
        stripped,
    )
    if match:
        return {
            "timestamp": match.group(1).strip(),
            "level": match.group(2),
            "logger": match.group(3),
            "message": match.group(4),
        }
    return {"message": stripped}
