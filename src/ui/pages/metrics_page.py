"""Metrics dashboard — cross-worker observability for the NEPSE Quant Engine (Sprint 12.1).

Renders the ``GET /metrics`` snapshot produced by the FastAPI backend
(``src/api/metrics.py``), including the Sprint 12.0 cross-worker
aggregate block:

- **System overview** — active worker count, total workers observed,
  metrics freshness / last update time, aggregate indicator-cache hit
  rate + hits/misses, and aggregate JSON-store lock counters (retries,
  stale recoveries, timeouts).
- **Worker breakdown** — a table of every observed worker (PID,
  hostname, active/inactive status, last-seen time, indicator-cache
  hits/misses/hit rate, JSON-store lock counters).
- **Visualizations** — Streamlit-native charts of aggregate vs local
  cache counters and worker liveness.

Design constraints (Sprint 12.1 Phase 2):

- Consumes the existing backend endpoint over HTTP (``API_BASE_URL``
  from ``src.config``) — no new frontend framework, no second UI, the
  Streamlit app is untouched.
- The data layer is split into pure, testable helpers (``fetch_metrics``
  and ``summarize``) so the integration is unit-testable without a
  running backend.
- The page **never crashes** when the API is unavailable, ``/metrics``
  errors, no workers are reported, metrics are stale, or a record is
  malformed — a clear user-facing status message is shown instead.
"""

from __future__ import annotations

import json
import time
from typing import Any

import streamlit as st
import pandas as pd

from src.config import API_BASE_URL, METRICS_FETCH_TTL_S, WORKER_METRICS_TTL_S
from src.ui.helpers import section_header, empty_state, fmt_number
from src.ui.components import kpi_card

# Fetch timeout — short so the page never blocks a rerun for long when
# the backend is down (best-effort observability).
_METRICS_TIMEOUT_S = 3.0

# ───────────────────────────────────────────────────────────────────
# Pure data layer (unit-testable without Streamlit / backend)
# ───────────────────────────────────────────────────────────────────


def fetch_metrics(base_url: str | None = None, timeout: float = _METRICS_TIMEOUT_S) -> dict[str, Any]:
    """Fetch and parse the backend ``/metrics`` snapshot.

    Args:
        base_url: Backend base URL (defaults to ``API_BASE_URL``).
        timeout: HTTP timeout in seconds.

    Returns:
        The parsed metrics payload dict.

    Raises:
        OSError / ValueError / json.JSONDecodeError: when the backend
        is unreachable, returns a non-JSON body, or the body is not a
        JSON object.  Callers must treat these as "metrics unavailable".
    """
    import urllib.request

    base = (base_url or API_BASE_URL).rstrip("/")
    url = f"{base}/metrics"
    with urllib.request.urlopen(url, timeout=timeout) as resp:  # noqa: S310 - local backend
        raw = resp.read().decode("utf-8")
    payload = json.loads(raw)
    if not isinstance(payload, dict):
        raise ValueError(f"/metrics returned a non-object payload: {type(payload).__name__}")
    return payload


# ───────────────────────────────────────────────────────────────────
# Cached fetch (Sprint 12.2, Phase 3)
# ───────────────────────────────────────────────────────────────────
# Before this sprint the page called ``fetch_metrics()`` directly in
# ``render()`` — one HTTP GET /metrics per Streamlit rerun (every widget
# interaction, navigation click, and auto-refresh tick).  The baseline
# audit (§17) measured the endpoint at ~9-10 ms in-process, so each
# request is cheap, but the *count* is unbounded and a backend outage
# blocks every rerun up to the 3 s timeout.
#
# The simplest reliable fix (per the sprint's Phase 3 mandate) is a
# short ``st.cache_data`` TTL over the fetch: successes are served from
# cache for ``METRICS_FETCH_TTL_S`` (default 10 s), and raised errors
# are **never** cached (st.cache_data does not store exceptions), so a
# failed fetch is retried on the next rerun instead of being pinned.
# A manual "Refresh now" button clears the cache for an immediate
# re-fetch.  WebSocket push was NOT introduced — see §17 for the
# measured justification (a 10 s operational snapshot is adequate and
# the project already has the retry path for outages).


@st.cache_data(ttl=METRICS_FETCH_TTL_S, show_spinner=False)
def _cached_fetch_metrics(
    base_url: str | None,
    timeout: float,
) -> dict[str, Any]:
    """Fetch /metrics with a short TTL (successful payloads only)."""
    return fetch_metrics(base_url=base_url, timeout=timeout)


def _num(value: Any, default: float = 0.0) -> float:
    """Best-effort numeric coercion for possibly-malformed metrics."""
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _int(value: Any, default: int = 0) -> int:
    """Best-effort integer coercion for possibly-malformed metrics."""
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def summarize(payload: dict[str, Any], now: float | None = None) -> dict[str, Any]:
    """Normalise a raw ``/metrics`` payload into dashboard-friendly rows.

    Tolerant of malformed/incomplete records: missing blocks default to
    zeros, non-dict records are skipped, and stale records (older than
    ``WORKER_METRICS_TTL_S``) are flagged ``active=False`` rather than
    raising.  Never raises.

    Returns:
        A dict with ``status`` (``"ok"`` | ``"stale"`` | ``"no_workers"``),
        ``fetched_at``, ``process``, ``aggregate``, ``workers`` (list of
        per-worker row dicts), and ``workers_total`` / ``workers_active``.
    """
    now = time.time() if now is None else now

    # A malformed response may carry non-dict blocks (e.g.
    # ``{"workers": "garbage"}`` or ``{"aggregate": 42}``) — guard with
    # ``isinstance`` before any attribute access so the helper truly
    # never raises, per the sprint's malformed-response requirement.
    workers_block = payload.get("workers")
    if not isinstance(workers_block, dict):
        workers_block = {}
    aggregate_block = payload.get("aggregate")
    if not isinstance(aggregate_block, dict):
        aggregate_block = {}
    process_block = payload.get("process")
    if not isinstance(process_block, dict):
        process_block = {}

    ttl = _num(workers_block.get("ttl_s", WORKER_METRICS_TTL_S), WORKER_METRICS_TTL_S)

    hits = _int(aggregate_block.get("hits"))
    misses = _int(aggregate_block.get("misses"))
    total = hits + misses
    aggregate = {
        "hits": hits,
        "misses": misses,
        "hit_rate": round(hits / total, 3) if total else 0.0,
        "lock_retries": _int(aggregate_block.get("lock_retries")),
        "stale_recoveries": _int(aggregate_block.get("stale_recoveries")),
        "timeouts": _int(aggregate_block.get("timeouts")),
        # Sprint 13.2: fleet HTTP request totals summed across active
        # workers (each worker counts only the requests it served, so
        # the sum is the true fleet total — never a double count).
        "requests": _int(aggregate_block.get("requests")),
        "request_errors": _int(aggregate_block.get("request_errors")),
    }

    workers: list[dict[str, Any]] = []
    for rec in workers_block.get("known", []) or []:
        if not isinstance(rec, dict):
            continue
        last_seen = _num(rec.get("last_seen"), 0.0)
        active = last_seen > 0 and (now - last_seen) <= ttl
        workers.append(
            {
                "pid": str(rec.get("pid", "?")),
                "hostname": str(rec.get("hostname", "?")),
                "active": active,
                "last_seen": last_seen,
                "last_seen_ago_s": round(max(0.0, now - last_seen), 1) if last_seen else None,
            }
        )

    if not workers:
        status = "no_workers"
    else:
        newest = max(w["last_seen"] for w in workers)
        status = "ok" if (now - newest) <= ttl else "stale"

    return {
        "status": status,
        # Newest worker report time — the most meaningful freshness
        # signal (client ``fetched_at`` would only measure fetch-to-
        # render time, which is always ~0 on a rerun).
        "newest_last_seen": max((w["last_seen"] for w in workers), default=0.0),
        "process": process_block,
        "aggregate": aggregate,
        "workers": workers,
        "workers_total": len(workers),
        "workers_active": sum(1 for w in workers if w["active"]),
        "ttl_s": ttl,
    }


def _age_label(seconds: float | None) -> str:
    if seconds is None:
        return "never"
    if seconds < 60:
        return f"{seconds:.0f}s ago"
    return f"{seconds / 60:.1f}m ago"


# ───────────────────────────────────────────────────────────────────
# Performance / gates / scanner normalization (Sprint 13.1)
# ───────────────────────────────────────────────────────────────────
# The backend /metrics payload gained three Sprint 13.1 blocks:
# ``api_requests`` (bounded per-endpoint HTTP latency + errors),
# ``performance_gates`` (configured thresholds + last CI verdict) and
# the pre-existing ``scanner_cache`` block.  These pure helpers
# normalise each block into dashboard-friendly rows, tolerating missing
# / malformed / zero / stale values exactly like ``summarize`` — the
# page must never crash on any backend shape.


def summarize_api_requests(payload: dict[str, Any]) -> dict[str, Any]:
    """Normalise the ``api_requests`` block into per-endpoint rows.

    Returns a dict with ``available`` (bool), overall ``total_requests`` /
    ``error_requests`` / ``error_rate`` / ``p95_ms`` / ``p99_ms`` and a
    ``rows`` list of per-endpoint dicts (endpoint, requests, errors,
    error_rate, average_ms, p95_ms, p99_ms) sorted by request count.
    Missing or malformed blocks yield zeros / an empty list — never
    raise.
    """
    block = payload.get("api_requests")
    if not isinstance(block, dict):
        return {"available": False, "total_requests": 0, "error_requests": 0,
                "error_rate": 0.0, "p95_ms": 0.0, "p99_ms": 0.0, "rows": []}
    per = block.get("per_endpoint")
    rows: list[dict[str, Any]] = []
    if isinstance(per, dict):
        for endpoint, m in per.items():
            if not isinstance(m, dict):
                continue
            rows.append({
                "endpoint": str(endpoint),
                "requests": _int(m.get("requests")),
                "errors": _int(m.get("errors")),
                "error_rate": _num(m.get("error_rate")),
                "average_ms": _num(m.get("average_ms")),
                "p95_ms": _num(m.get("p95_ms")),
                "p99_ms": _num(m.get("p99_ms")),
            })
        rows.sort(key=lambda r: r["requests"], reverse=True)
    return {
        "available": True,
        "total_requests": _int(block.get("total_requests")),
        "error_requests": _int(block.get("error_requests")),
        "error_rate": _num(block.get("error_rate")),
        "p95_ms": _num(block.get("p95_ms")),
        "p99_ms": _num(block.get("p99_ms")),
        "rows": rows,
    }


def summarize_gates(payload: dict[str, Any]) -> dict[str, Any]:
    """Normalise the ``performance_gates`` block for the dashboard.

    The block carries the *configured* thresholds (single source of
    truth: ``benchmarks.ci_gate``) and the last recorded CI artifact
    (verdict + per-check booleans) when one exists.  Returns:

    - ``available`` — block present;
    - ``thresholds`` — dict of gate name -> configured maximum;
    - ``ci_artifact`` — dict (or ``None``) with ``verdict``,
      ``timestamp``, and ``checks`` (name -> bool or None);
    - ``gates`` — per-gate rows ``{name, maximum, actual, verdict}``
      where ``actual`` is the last CI-measured value and verdict is
      ``PASS`` / ``FAIL`` / ``unknown`` (no artifact).

    Malformed blocks degrade to ``available=False`` with empty rows.
    """
    block = payload.get("performance_gates")
    if not isinstance(block, dict):
        return {"available": False, "thresholds": {}, "ci_artifact": None, "gates": []}

    thresholds = block.get("thresholds")
    if not isinstance(thresholds, dict):
        thresholds = {}

    artifact = block.get("ci_artifact")
    artifact_info: dict[str, Any] | None = None
    checks: dict[str, Any] = {}
    if isinstance(artifact, dict):
        raw_checks = artifact.get("checks")
        if isinstance(raw_checks, dict):
            checks = raw_checks
        artifact_info = {
            "verdict": str(artifact.get("verdict") or "unknown"),
            "timestamp": artifact.get("timestamp"),
            "checks": checks,
        }

    # Map each known gate to its threshold key + last measured value.
    gate_keys = [
        ("Warm API ratio", "warm_api_ratio_max", "api_analyze_warm_cold_ratio"),
        ("Warm portfolio ratio", "warm_portfolio_ratio_max", "api_portfolio_warm_cold_ratio"),
        ("Analyze p99", "analyze_p99_max_ms", "api_analyze_warm_p99_ms"),
    ]
    gates: list[dict[str, Any]] = []
    for name, max_key, actual_key in gate_keys:
        maximum = _num(thresholds.get(max_key))
        actual = None
        if isinstance(artifact, dict):
            actual = artifact.get(actual_key)
        if actual is None:
            verdict = "unknown"
            actual_display = "—"
        else:
            actual_f = _num(actual)
            actual_display = round(actual_f, 3) if actual_f else 0.0
            verdict = "PASS" if (maximum and actual_f <= maximum) else "FAIL"
        gates.append({
            "name": name,
            "maximum": maximum,
            "actual": actual_display,
            "verdict": verdict,
        })

    return {
        "available": True,
        "thresholds": {k: _num(v) for k, v in thresholds.items()},
        "ci_artifact": artifact_info,
        "gates": gates,
    }


def summarize_scanner(payload: dict[str, Any]) -> dict[str, Any]:
    """Normalise the ``scanner_cache`` block into displayable counters.

    Returns ``available`` plus dataframe/analysis hits/misses/entries
    and a combined ``cache_hit_rate``.  Missing/malformed -> zeros.
    """
    block = payload.get("scanner_cache")
    if not isinstance(block, dict):
        return {"available": False, "df_hits": 0, "df_misses": 0, "df_entries": 0,
                "analysis_hits": 0, "analysis_misses": 0, "analysis_entries": 0,
                "cache_hit_rate": 0.0}
    hits = _int(block.get("dataframe_hits")) + _int(block.get("analysis_hits"))
    misses = _int(block.get("dataframe_misses")) + _int(block.get("analysis_misses"))
    total = hits + misses
    return {
        "available": True,
        "df_hits": _int(block.get("dataframe_hits")),
        "df_misses": _int(block.get("dataframe_misses")),
        "df_entries": _int(block.get("dataframe_entries")),
        "analysis_hits": _int(block.get("analysis_hits")),
        "analysis_misses": _int(block.get("analysis_misses")),
        "analysis_entries": _int(block.get("analysis_entries")),
        "cache_hit_rate": round(hits / total, 3) if total else 0.0,
    }


# ───────────────────────────────────────────────────────────────────
# Streamlit page
# ───────────────────────────────────────────────────────────────────


def render() -> None:
    """Render the Metrics dashboard page (never crashes on API failure)."""
    section_header(
        "📊 Metrics",
        "Cross-worker observability — aggregate + worker-local counters from /metrics",
    )

    try:
        payload = _cached_fetch_metrics(
            API_BASE_URL.rstrip("/") if API_BASE_URL else None,
            _METRICS_TIMEOUT_S,
        )
    except Exception as exc:  # noqa: BLE001 - any failure = graceful status
        empty_state(
            "🔌",
            "Metrics unavailable",
            f"The backend /metrics endpoint could not be reached at {API_BASE_URL}/metrics "
            f"({type(exc).__name__}). Start the FastAPI server to see live metrics.",
        )
        if st.button("🔄 Retry"):
            # Errors are never cached, but clear explicitly so the
            # retry cannot serve a previously-cached payload.
            _cached_fetch_metrics.clear()
            st.rerun()
        return

    # Manual refresh (Sprint 12.2): the fetch is cached for
    # METRICS_FETCH_TTL_S; this button clears the cache so the next
    # rerun re-fetches immediately instead of waiting out the TTL.
    # Only rendered on the success path — the failure path already
    # offers "Retry", so a down backend never shows two near-identical
    # controls.
    if st.button("🔄 Refresh now"):
        _cached_fetch_metrics.clear()
        st.rerun()

    try:
        summary = summarize(payload)
    except Exception as exc:  # noqa: BLE001 - malformed payload must not crash the page
        empty_state(
            "⚠️",
            "Malformed metrics payload",
            f"The /metrics response could not be interpreted ({type(exc).__name__}).",
        )
        return

    # ── Status banner ──────────────────────────────────────────────
    status = summary["status"]
    if status == "no_workers":
        st.info("No workers have reported metrics yet — worker self-reports appear on the "
                "first /metrics call from each process (throttled to one per 5 s).")
    elif status == "stale":
        st.warning("Worker metrics are stale — no active worker has reported within "
                   f"the {summary['ttl_s']:.0f}s liveness window.")
    else:
        st.success(f"Metrics fresh — {summary['workers_active']} of {summary['workers_total']} "
                   "workers active")

    # ── System overview ────────────────────────────────────────────
    agg = summary["aggregate"]
    process = summary["process"]
    col1, col2, col3, col4 = st.columns(4)
    with col1:
        kpi_card("👷 Active Workers", str(summary["workers_active"]))
    with col2:
        kpi_card("🧾 Total Observed", str(summary["workers_total"]))
    with col3:
        rate = agg["hit_rate"]
        kpi_card("🎯 Aggregate Hit Rate", f"{rate * 100:.1f}%",
                 help_text="Aggregate indicator-cache hit rate across active workers")
    newest = summary["newest_last_seen"]
    # Clamp against clock skew (a future worker timestamp would print a
    # negative age).
    freshness = "never" if not newest else _age_label(max(0.0, time.time() - newest))
    with col4:
        kpi_card("🕓 Last Update", freshness,
                 help_text="Age of the newest worker metrics report")

    col1, col2, col3, col4 = st.columns(4)
    with col1:
        kpi_card("💾 Aggregate Hits", fmt_number(agg["hits"]))
    with col2:
        kpi_card("🔍 Aggregate Misses", fmt_number(agg["misses"]))
    with col3:
        kpi_card("🔒 Lock Retries", fmt_number(agg["lock_retries"]),
                 help_text="Cross-process JSON-store lock retries (contention)")
    with col4:
        kpi_card("♻️ Stale Recoveries", fmt_number(agg["stale_recoveries"]),
                 help_text="Stale lock files broken and recovered")

    col1, col2, col3, col4 = st.columns(4)
    with col1:
        kpi_card("🧾 Fleet Requests", fmt_number(agg["requests"]),
                 help_text="HTTP requests served across all active workers (each request counted once)")
    with col2:
        kpi_card("❌ Fleet Errors", fmt_number(agg["request_errors"]),
                 help_text="HTTP error responses across all active workers")
    with col3:
        kpi_card("📶 Fleet Hit Rate", f"{agg['hit_rate'] * 100:.1f}%",
                 help_text="Aggregate indicator-cache hit rate across active workers")
    # Serving worker's p99 latency — computed once so both this KPI and
    # the API Request Latency section below share the same normalised
    # value (never re-derived).
    req = summarize_api_requests(payload)
    with col4:
        kpi_card("⏱️ Local p99", f"{req['p99_ms']:.0f} ms" if req["p99_ms"] else "—",
                 help_text="Serving worker's p99 request latency (bounded tracker)")

    if process:
        col1, col2 = st.columns(2)
        with col1:
            kpi_card("🖥️ Serving PID", str(process.get("pid", "?")))
        with col2:
            kpi_card("🏷️ Hostname", str(process.get("hostname", "?")))

    # ── Performance gates (Sprint 13.1) ────────────────────────────
    # Rendered from the API-provided ``performance_gates`` block — the
    # thresholds are read from benchmarks.ci_gate by the backend (single
    # source of truth) and the verdicts come from the last recorded CI
    # artifact, so Streamlit never re-derives gate arithmetic.
    gates = summarize_gates(payload)
    if gates["available"] and gates["gates"]:
        st.markdown("---")
        st.markdown("##### 🚦 Performance Gates (CI enforcement)")
        gate_rows = []
        for g in gates["gates"]:
            if g["verdict"] == "PASS":
                badge = "🟢 PASS"
                color = "#00C853"
            elif g["verdict"] == "FAIL":
                badge = "🔴 FAIL"
                color = "#FF5252"
            else:
                badge = "⚪ Not run"
                color = "#6B7280"
            gate_rows.append({
                "Gate": g["name"],
                "Maximum": g["maximum"] if g["maximum"] else "—",
                "Last CI actual": g["actual"],
                "Status": badge,
            })
        gate_df = pd.DataFrame(gate_rows)

        def _color_gate(val: str) -> str:
            if "PASS" in val:
                return "color: #00C853; font-weight: 600"
            if "FAIL" in val:
                return "color: #FF5252; font-weight: 600"
            return "color: #6B7280"

        st.dataframe(
            gate_df.style.applymap(_color_gate, subset=["Status"]),
            use_container_width=True,
            hide_index=True,
        )
        ci = gates.get("ci_artifact")
        if ci and ci.get("timestamp"):
            st.caption(
                f"Last CI verdict: {ci.get('verdict')} @ {ci.get('timestamp')} — "
                "thresholds enforced by the benchmark CI job."
            )
        else:
            st.caption("No CI benchmark artifact recorded yet — thresholds shown are the enforced values.")

    # ── API request latency (Sprint 13.1) ──────────────────────────
    # ``req`` was already normalised above for the fleet-row p99 KPI.
    if req["available"]:
        st.markdown("---")
        st.markdown("##### 📈 API Request Latency (this worker)")
        col1, col2, col3, col4 = st.columns(4)
        with col1:
            kpi_card("🧾 Requests", fmt_number(req["total_requests"]))
        with col2:
            kpi_card("❌ Errors", fmt_number(req["error_requests"]))
        with col3:
            kpi_card("⏱️ p95", f"{req['p95_ms']:.0f} ms" if req["p95_ms"] else "—")
        with col4:
            kpi_card("⏱️ p99", f"{req['p99_ms']:.0f} ms" if req["p99_ms"] else "—")
        if req["rows"]:
            st.dataframe(
                pd.DataFrame(
                    {
                        "Endpoint": [r["endpoint"] for r in req["rows"]],
                        "Requests": [r["requests"] for r in req["rows"]],
                        "Errors": [r["errors"] for r in req["rows"]],
                        "Err %": [f"{r['error_rate'] * 100:.1f}%" for r in req["rows"]],
                        "Avg ms": [f"{r['average_ms']:.1f}" for r in req["rows"]],
                        "p95 ms": [f"{r['p95_ms']:.1f}" for r in req["rows"]],
                        "p99 ms": [f"{r['p99_ms']:.1f}" for r in req["rows"]],
                    }
                ),
                use_container_width=True,
                hide_index=True,
            )

    # ── Scanner cache (Sprint 13.1) ────────────────────────────────
    scan = summarize_scanner(payload)
    if scan["available"]:
        st.markdown("---")
        st.markdown("##### 🔍 Scanner Cache")
        col1, col2, col3, col4 = st.columns(4)
        with col1:
            kpi_card("📄 DF Hits / Misses", f"{fmt_number(scan['df_hits'])} / {fmt_number(scan['df_misses'])}")
        with col2:
            kpi_card("🧠 Analysis Hits / Misses", f"{fmt_number(scan['analysis_hits'])} / {fmt_number(scan['analysis_misses'])}")
        with col3:
            kpi_card("💾 Entries", fmt_number(scan["df_entries"] + scan["analysis_entries"]))
        with col4:
            kpi_card("🎯 Hit Rate", f"{scan['cache_hit_rate'] * 100:.1f}%")

    # ── Worker breakdown table ─────────────────────────────────────
    st.markdown("---")
    st.markdown("##### 👷 Worker Breakdown")
    if summary["workers"]:
        rows = []
        for w in summary["workers"]:
            rows.append(
                {
                    "PID": w["pid"],
                    "Hostname": w["hostname"],
                    "Status": "🟢 Active" if w["active"] else "⚪ Stale",
                    "Last Seen": _age_label(w["last_seen_ago_s"]),
                }
            )
        df = pd.DataFrame(rows)

        def _color_status(val: str) -> str:
            return "color: #00C853" if "Active" in val else "color: #6B7280"

        st.dataframe(
            df.style.applymap(_color_status, subset=["Status"]),
            use_container_width=True,
            hide_index=True,
        )
    else:
        st.caption("No worker records reported yet.")

    # ── Aggregate vs local cache counters ──────────────────────────
    local_ic = payload.get("indicator_cache") or {}
    if isinstance(local_ic, dict) and (agg["hits"] + agg["misses"] + _int(local_ic.get("hits")) + _int(local_ic.get("misses")) > 0):
        st.markdown("---")
        st.markdown("##### 📊 Cache Counters (aggregate vs serving worker)")
        chart = pd.DataFrame(
            {
                "Hits": [agg["hits"], _int(local_ic.get("hits"))],
                "Misses": [agg["misses"], _int(local_ic.get("misses"))],
            },
            index=["Aggregate", "Serving worker"],
        )
        st.bar_chart(chart)
        local_rate = _num(local_ic.get("hit_rate"), 0.0)
        col1, col2 = st.columns(2)
        with col1:
            kpi_card("🖥️ Local Hit Rate", f"{local_rate * 100:.1f}%",
                     help_text="Serving worker's own indicator-cache hit rate")
        with col2:
            kpi_card("🧾 Local Entries", fmt_number(_int(local_ic.get("entries"))))

    # ── Lock counters per worker (if available in local block) ─────
    lock = payload.get("json_store") or {}
    if isinstance(lock, dict):
        st.markdown("---")
        st.markdown("##### 🔒 JSON-Store Lock Counters (serving worker)")
        col1, col2, col3 = st.columns(3)
        with col1:
            kpi_card("Retries", fmt_number(_int(lock.get("retries"))))
        with col2:
            kpi_card("Stale Recoveries", fmt_number(_int(lock.get("stale_recoveries"))))
        with col3:
            kpi_card("Timeouts", fmt_number(_int(lock.get("timeouts"))))

    st.caption("Best-effort observability — worker records self-report on a 5 s throttle "
               "and expire after the liveness window. No secrets or filesystem paths are exposed.")
