"""API Explorer page — interactive REST API browser and tester.

Lists every endpoint exposed by the FastAPI backend, shows its
parameters, builds example requests, and lets the user fire live
requests directly from the UI (base URL configurable).

The page never imports ``requests`` directly — all HTTP goes through
:mod:`src.api.explorer` (audit-approved).
"""

from __future__ import annotations

import json
from typing import Any

import pandas as pd
import streamlit as st

from src.ui.helpers import section_header, divider
from src.ui.components import kpi_card
from src.ui.theme import theme


# ── Session helpers ───────────────────────────────────────────────


def _init() -> None:
    """Initialise session state used by this page."""
    st.session_state.setdefault("api_base_url", "http://localhost:8000")
    st.session_state.setdefault("api_endpoints", None)
    st.session_state.setdefault("api_last_result", None)


def _load_endpoints() -> list[dict[str, Any]]:
    """Return introspected endpoint metadata (cached in session)."""
    cached = st.session_state.get("api_endpoints")
    if cached is not None:
        return cached
    from src.api.explorer import list_endpoints

    endpoints = [e.to_dict() for e in list_endpoints()]
    st.session_state["api_endpoints"] = endpoints
    return endpoints


# ── Request body presets for common POST endpoints ────────────────

_BODY_PRESETS: dict[str, dict[str, Any]] = {
    "/signals/explain": {
        "symbol": "NABIL",
        "analysis": {"rsi": 55.0, "macd": 1.2, "macd_signal": 0.8, "price": 500.0},
        "regime": "BULL",
    },
    "/models/train": {"symbol": "NABIL", "model_name": "random_forest", "task": "classification", "horizon": 5},
    "/models/predict": {"symbol": "NABIL", "model_name": "random_forest", "task": "classification", "horizon": 5},
    "/strategies/build": {
        "name": "RSI Mean Reversion",
        "description": "Buy when RSI crosses above 30.",
        "entry_rules": {"type": "indicator", "indicator": "RSI", "operator": ">", "value": 30.0},
    },
    "/optimizer/mpt": {
        "returns": {"NABIL": [0.01, 0.02, -0.01], "NRIC": [0.005, 0.01, 0.015]},
        "objective": "sharpe",
        "n_points": 30,
    },
    "/optimizer/risk-parity": {
        "returns": {"NABIL": [0.01, 0.02, -0.01], "NRIC": [0.005, 0.01, 0.015]},
    },
    "/optimizer/kelly": {"win_rate": 0.55, "avg_win": 100.0, "avg_loss": 80.0},
    "/optimizer/genetic": {
        "param_space": {
            "rsi_period": {"min": 5, "max": 30, "step": 1},
            "stop_loss_pct": {"min": 2.0, "max": 10.0, "step": 1.0},
        },
        "fitness_fn": "module:function",
        "population_size": 20,
        "generations": 10,
    },
    "/risk/var": {"returns": [0.01, -0.02, 0.005, -0.015], "confidence": 0.95, "method": "historical"},
    "/risk/monte-carlo": {"returns": [0.01, -0.02, 0.005], "simulations": 5000, "horizon": 252},
    "/risk/stress-test": {"returns": [0.01, -0.02, 0.005], "portfolio_value": 1_000_000.0},
    "/replay/start": {"symbol": "NABIL", "days": 120},
}


def _body_preset(path: str) -> dict[str, Any]:
    """Return a sensible JSON body preset for an endpoint path."""
    for pattern, preset in _BODY_PRESETS.items():
        if path.startswith(pattern) or pattern in path:
            return json.loads(json.dumps(preset))
    return {"symbol": "NABIL"}


# ── Example request builders ──────────────────────────────────────


def _example_url(endpoint: dict[str, Any]) -> str:
    """Build an example URL string for an endpoint."""
    base = st.session_state.get("api_base_url", "http://localhost:8000")
    url = f"{base.rstrip('/')}{endpoint['path']}"
    for name in endpoint.get("path_params", []):
        url = url.replace(f"{{{name}}}", "NABIL" if name == "symbol" else "123")
    query = endpoint.get("query_params", {})
    if query:
        parts = []
        for name, default in query.items():
            value = 120 if name in ("days", "page_size", "simulations") else (1 if name == "page" else (0.0 if name in ("min_confidence", "commission", "slippage") else (0.95 if name == "confidence_level" else (default if default is not None else ""))))
            if value != "":
                parts.append(f"{name}={value}")
        if parts:
            url += "?" + "&".join(parts)
    return url


# ── Page renderer ─────────────────────────────────────────────────


def render() -> None:
    """Render the API Explorer page."""
    section_header(
        "🧪 API Explorer",
        "Browse and test every REST endpoint exposed by the engine API",
    )

    _init()

    try:
        from src.api.explorer import send_request
    except ImportError as exc:
        st.error(f"API explorer module unavailable: {exc}")
        return

    # ── Base URL configuration ────────────────────────────────────
    col1, col2, col3 = st.columns([2, 1, 1])
    with col1:
        base_url = st.text_input(
            "Base URL",
            value=st.session_state.get("api_base_url", "http://localhost:8000"),
            key="api_base_url_input",
            help="FastAPI backend URL (default http://localhost:8000).",
        )
        st.session_state["api_base_url"] = base_url.rstrip("/")
    with col2:
        test_health = st.button("🩺 Test Connection", use_container_width=True, key="api_health")
    with col3:
        refresh_list = st.button("🔄 Refresh Endpoints", use_container_width=True, key="api_refresh")

    if refresh_list:
        st.session_state["api_endpoints"] = None
        # Drop the persisted selection so a stale label can't break the
        # selectbox when the endpoint set changes.
        st.session_state.pop("api_endpoint_select", None)

    # Health probe
    if test_health:
        with st.spinner("Testing connection..."):
            result = send_request(base_url, "GET", "/")
        if result.ok:
            if result.status_code == 200:
                st.success(f"✅ Connected — status {result.status_code} in {result.elapsed_ms:.0f} ms")
            else:
                st.warning(f"⚠️ Responded with status {result.status_code} in {result.elapsed_ms:.0f} ms")
        else:
            st.error(f"❌ Cannot reach API: {result.error}")
            st.caption(
                "Start the backend with: `python -m uvicorn src.api.main:app --port 8000`"
            )

    divider()

    # ── Endpoint browser ──────────────────────────────────────────
    endpoints = _load_endpoints()
    if not endpoints:
        st.info("No endpoints found. The FastAPI app may not be importable — "
                "check `src/api/main.py`.")
        return

    kpi_card("Total Endpoints", str(len(endpoints)))

    st.markdown("#### 📋 Endpoint Catalog")
    df = pd.DataFrame(
        [
            {
                "Method": e["method"],
                "Path": e["path"],
                "Tags": ", ".join(e["tags"]) or "—",
                "Summary": e["summary"][:60],
            }
            for e in endpoints
        ]
    )
    st.dataframe(df, use_container_width=True, hide_index=True)

    divider()

    # ── Selected endpoint detail + live tester ────────────────────
    st.markdown("#### 🔬 Live Tester")
    labels = [f"{e['method']} {e['path']}" for e in endpoints]
    selected = st.selectbox("Endpoint", labels, index=0, key="api_endpoint_select")
    endpoint = endpoints[labels.index(selected)]

    method = endpoint["method"]
    method_colors = {"GET": theme.success, "POST": theme.primary, "PUT": theme.warning, "DELETE": theme.danger}
    color = method_colors.get(method, theme.text_secondary)
    st.markdown(
        f"<span style='background:{color}22; color:{color}; border:1px solid {color}44; "
        f"border-radius:6px; padding:2px 10px; font-weight:600;'>{method}</span> "
        f"<code>{endpoint['path']}</code>",
        unsafe_allow_html=True,
    )
    if endpoint["summary"]:
        st.caption(endpoint["summary"])

    path_values: dict[str, Any] = {}
    if endpoint["path_params"]:
        st.markdown("##### Path Parameters")
        for name in endpoint["path_params"]:
            path_values[name] = st.text_input(
                name,
                value="NABIL" if name == "symbol" else "1",
                key=f"api_path_{name}_{endpoint['path']}",
            )

    query_values: dict[str, Any] = {}
    if endpoint["query_params"]:
        st.markdown("##### Query Parameters (optional)")
        cols = st.columns(3)
        for i, (name, default) in enumerate(endpoint["query_params"].items()):
            with cols[i % 3]:
                default_text = "" if default is None else str(default)
                query_values[name] = st.text_input(
                    name,
                    value=default_text,
                    key=f"api_query_{name}_{endpoint['path']}",
                )

    body: dict[str, Any] | None = None
    if method in ("POST", "PUT", "PATCH"):
        st.markdown("##### JSON Body")
        preset = _body_preset(endpoint["path"])
        body_text = st.text_area(
            "Request body (JSON)",
            value=json.dumps(preset, indent=2),
            height=220,
            key=f"api_body_{endpoint['path']}",
        )
        try:
            body = json.loads(body_text)
        except json.JSONDecodeError:
            body = None

    # Example request
    st.markdown("##### Example Request")
    example = f"{method} {_example_url(endpoint)}"
    if body is not None:
        example += f"\nBody: {json.dumps(body, indent=2)}"
    st.code(example, language="bash")

    # Send
    col1, col2 = st.columns([1, 3])
    with col1:
        send_btn = st.button("🚀 Send Request", type="primary", use_container_width=True, key="api_send")
    with col2:
        st.caption(
            "Requests are sent to the running backend. Connection failures are "
            "reported inline — never crashes the page."
        )

    if send_btn:
        if body is None and method in ("POST", "PUT", "PATCH"):
            st.error("Request body is not valid JSON.")
        else:
            with st.spinner(f"{method} {endpoint['path']}..."):
                result = send_request(
                    base_url,
                    method,
                    endpoint["path"],
                    path_values=path_values or None,
                    query_params={k: v for k, v in query_values.items() if v != ""} or None,
                    body=body,
                )
            st.session_state["api_last_result"] = result

    last = st.session_state.get("api_last_result")
    if last:
        st.markdown("##### Response")
        if last.ok:
            is_2xx = 200 <= last.status_code < 300
            if is_2xx:
                st.success(f"Status {last.status_code} — {last.elapsed_ms:.0f} ms")
            else:
                st.warning(f"Status {last.status_code} — {last.elapsed_ms:.0f} ms")
        else:
            st.error(f"Request failed: {last.error}")

        if last.data is not None:
            st.json(last.data)
        elif last.text:
            st.code(last.text[:4000], language="json")

    divider()
    st.caption(
        "💡 Tip: start the backend (`python -m uvicorn src.api.main:app --port 8000`) "
        "to test endpoints live. The Streamlit app and API are independent processes."
    )
