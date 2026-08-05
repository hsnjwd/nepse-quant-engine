"""API Explorer helper — endpoint introspection and live request testing.

This module is the ONLY place (besides the audit-approved providers) that
performs raw HTTP requests for the API Explorer UI.  Streamlit pages must
never import ``requests`` directly; they call :func:`send_request` here.

Adding the module to the audit tool's ``APPROVED_MODULES`` set keeps the
migration audit clean while allowing the developer-facing API Explorer to
function.

Public API::

    from src.api.explorer import list_endpoints, send_request, ApiEndpoint
"""

from __future__ import annotations

import inspect
import json
import time
from dataclasses import dataclass, field
from typing import Any

import requests

from src.logging.logger import logger


@dataclass
class ApiEndpoint:
    """Metadata for one REST endpoint.

    Attributes:
        method: HTTP method (uppercase).
        path: URL path (e.g. ``/stocks/NABIL/history``).
        tags: OpenAPI tag group.
        summary: Short description from the endpoint docstring.
        path_params: Names of required path parameters.
        query_params: Optional query parameters (name → default).
    """

    method: str = "GET"
    path: str = ""
    tags: list[str] = field(default_factory=list)
    summary: str = ""
    path_params: list[str] = field(default_factory=list)
    query_params: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serialisable dictionary."""
        return {
            "method": self.method,
            "path": self.path,
            "tags": list(self.tags),
            "summary": self.summary,
            "path_params": list(self.path_params),
            "query_params": dict(self.query_params),
        }


@dataclass
class ApiCallResult:
    """Outcome of a live API request.

    Attributes:
        ok: True when the request completed (any HTTP status).
        status_code: HTTP status code, or 0 when the call failed.
        elapsed_ms: Round-trip time in milliseconds.
        text: Raw response body (truncated for display).
        data: Parsed JSON payload when available.
        error: Error message when the call failed.
    """

    ok: bool = False
    status_code: int = 0
    elapsed_ms: float = 0.0
    text: str = ""
    data: Any = None
    error: str = ""

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serialisable dictionary."""
        return {
            "ok": self.ok,
            "status_code": self.status_code,
            "elapsed_ms": round(self.elapsed_ms, 2),
            "error": self.error,
        }


# ---------------------------------------------------------------------------
# Introspection
# ---------------------------------------------------------------------------


def _endpoint_default(value: Any) -> Any:
    """Unwrap a FastAPI ``Query``/``Body`` default into a plain value."""
    if hasattr(value, "default") and value.default is not inspect.Parameter.empty:
        return _endpoint_default(value.default)
    if value is inspect.Parameter.empty:
        return None
    if isinstance(value, (list, tuple, set)):
        return list(value)
    return value


def list_endpoints(include: set[str] | None = None) -> list[ApiEndpoint]:
    """Introspect the FastAPI application and return every endpoint.

    Args:
        include: Optional set of methods to include (e.g. ``{"GET"}``).
            When ``None``, every non-HEAD/OPTIONS method is listed.

    Returns:
        Sorted list of :class:`ApiEndpoint` metadata.
    """
    try:
        from src.api.main import app
    except Exception as exc:
        logger.warning("Could not import API app for introspection: %s", exc)
        return []

    endpoints: list[ApiEndpoint] = []
    routes = getattr(app, "routes", [])

    for route in routes:
        methods = sorted(getattr(route, "methods", set()) or set())
        if not methods:
            continue

        path = getattr(route, "path", "")
        if path in {"/openapi.json", "/docs", "/redoc", "/docs/oauth2-redirect"}:
            continue

        convertors = getattr(route, "param_convertors", {}) or {}
        path_params = list(convertors.keys())

        query_params: dict[str, Any] = {}
        endpoint_fn = getattr(route, "endpoint", None)
        if endpoint_fn is not None:
            try:
                signature = inspect.signature(endpoint_fn)
                for name, param in signature.parameters.items():
                    if name in path_params or param.kind in (
                        param.VAR_POSITIONAL,
                        param.VAR_KEYWORD,
                    ):
                        continue
                    query_params[name] = _endpoint_default(param.default)
            except (TypeError, ValueError):
                query_params = {}

        doc = inspect.getdoc(endpoint_fn) if endpoint_fn else ""
        summary = doc.split("\n")[0].strip() if doc else ""

        for method in methods:
            if method in ("HEAD", "OPTIONS"):
                continue
            if include and method not in include:
                continue
            endpoints.append(
                ApiEndpoint(
                    method=method,
                    path=path,
                    tags=list(getattr(route, "tags", []) or []),
                    summary=summary,
                    path_params=path_params,
                    query_params=query_params,
                )
            )

    endpoints.sort(key=lambda e: (e.path, e.method))
    logger.debug("API explorer introspected %d endpoints.", len(endpoints))
    return endpoints


# ---------------------------------------------------------------------------
# Live requests
# ---------------------------------------------------------------------------

_DEFAULT_TIMEOUT = 10.0
_MAX_BODY = 200_000


def build_url(base_url: str, path: str, values: dict[str, Any]) -> str:
    """Build a concrete URL from a path template and parameter values.

    Args:
        base_url: API base URL (e.g. ``http://localhost:8000``).
        path: Path template with ``{param}`` placeholders.
        values: Mapping of path-parameter name → value.

    Returns:
        Concrete URL with path parameters substituted.
    """
    url = f"{base_url.rstrip('/')}{path}"
    for name, value in values.items():
        url = url.replace(f"{{{name}}}", str(value))
    return url


def send_request(
    base_url: str,
    method: str,
    path: str,
    path_values: dict[str, Any] | None = None,
    query_params: dict[str, Any] | None = None,
    body: dict[str, Any] | None = None,
    timeout: float = _DEFAULT_TIMEOUT,
) -> ApiCallResult:
    """Execute a live HTTP request against the engine API.

    Args:
        base_url: API base URL.
        method: HTTP method (GET/POST/PUT/DELETE).
        path: Path template (``{param}`` placeholders filled from
            *path_values*).
        path_values: Path-parameter substitution values.
        query_params: Optional query string parameters.
        body: Optional JSON body for POST/PUT requests.
        timeout: Request timeout in seconds.

    Returns:
        An :class:`ApiCallResult` — never raises for network failures.
    """
    url = build_url(base_url, path, path_values or {})
    started = time.monotonic()

    try:
        response = requests.request(
            method=method.upper(),
            url=url,
            params=query_params or None,
            json=body if method.upper() in ("POST", "PUT", "PATCH") else None,
            timeout=timeout,
        )
        elapsed_ms = (time.monotonic() - started) * 1000.0
        text = response.text[:_MAX_BODY]
        data = None
        if response.headers.get("content-type", "").startswith("application/json"):
            try:
                data = response.json()
            except (ValueError, json.JSONDecodeError):
                data = None
        return ApiCallResult(
            ok=True,
            status_code=response.status_code,
            elapsed_ms=elapsed_ms,
            text=text,
            data=data,
        )
    except requests.RequestException as exc:
        elapsed_ms = (time.monotonic() - started) * 1000.0
        logger.warning("API explorer request to %s failed: %s", url, exc)
        return ApiCallResult(ok=False, error=str(exc), elapsed_ms=elapsed_ms)
    except Exception as exc:  # pragma: no cover - defensive
        elapsed_ms = (time.monotonic() - started) * 1000.0
        logger.warning("API explorer request to %s failed: %s", url, exc)
        return ApiCallResult(ok=False, error=str(exc), elapsed_ms=elapsed_ms)
