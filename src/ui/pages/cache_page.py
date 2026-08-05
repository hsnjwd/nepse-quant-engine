"""Performance Monitoring Dashboard — inspect DataService metrics, cache, providers, WebSocket, and health.

Extends the original cache debug page with Plotly charts for:
- API latency graph (per operation)
- Cache hit ratio over time
- Provider health status
- WebSocket connection stats
- Background refresh status
- Rate limiter stats
- Request metrics (total, P95, P99)
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import streamlit as st
import pandas as pd

from src.data import DataService as _DataService
from src.ui.helpers import section_header, divider
from src.ui.theme import theme

# Lazy import Plotly (optional — dashboard works without it)
try:
    import plotly.express as px
    import plotly.graph_objects as go
    HAS_PLOTLY = True
except ImportError:
    HAS_PLOTLY = False


def _svc() -> _DataService:
    return _DataService()


def render() -> None:
    """Render the Performance Monitoring Dashboard page."""
    section_header(
        "📊 Performance Dashboard",
        "DataService metrics, cache analytics, provider health, and WebSocket status",
    )

    service = _svc()
    metrics = service.get_metrics()

    # ── Tab layout ───────────────────────────────────────────────
    tabs = st.tabs([
        "📦 Cache",
        "⚡ Metrics",
        "🔌 Providers",
        "🌐 WebSocket",
        "🔄 Background",
        "⚙️ Rate Limiter",
        "🧠 Health",
    ])

    with tabs[0]:
        _render_cache_tab(service)

    with tabs[1]:
        _render_metrics_tab(metrics, service)

    with tabs[2]:
        _render_providers_tab(service)

    with tabs[3]:
        _render_websocket_tab(service)

    with tabs[4]:
        _render_background_tab(service)

    with tabs[5]:
        _render_rate_limiter_tab(service)

    with tabs[6]:
        _render_health_tab(service)


# ═══════════════════════════════════════════════════════════════════
# Tab renderers
# ═══════════════════════════════════════════════════════════════════


def _render_cache_tab(service: _DataService) -> None:
    """Cache configuration, entries, and controls."""
    cache = service._cache

    col1, col2, col3 = st.columns(3)
    with col1:
        st.metric("Memory TTL", f"{cache._memory_ttl}s")
    with col2:
        st.metric("Disk TTL", f"{cache._disk_ttl}s")
    with col3:
        provider_name = getattr(service._provider, "name", "unknown")
        st.metric("Active Provider", provider_name)

    # Provider chain
    provider = service._provider
    if provider:
        st.info(f"**Provider**: `{provider.name}`")
        if hasattr(provider, "_providers"):
            st.markdown("**Nested Providers:**")
            for i, p in enumerate(provider._providers):
                st.markdown(f"{i + 1}. `{p.name}` — {type(p).__name__}")
        if hasattr(provider, "last_provider") and provider.last_provider:
            st.success(f"Last successful: `{provider.last_provider}`")
    else:
        st.warning("No provider configured")

    # Memory cache
    divider()
    st.markdown("### 🧠 Memory Cache")
    mem_store: dict[str, Any] = {}
    try:
        mem = cache._memory
        with mem._lock:
            mem_store = dict(mem._store) if hasattr(mem, "_store") else {}
        if mem_store:
            st.metric("Entries", len(mem_store))
            rows = []
            now = time.time()
            for key, entry in sorted(mem_store.items()):
                age = now - getattr(entry, "timestamp", now)
                ttl = getattr(entry, "ttl", 0)
                remaining = max(0, ttl - age)
                val_type = type(getattr(entry, "data", None)).__name__
                rows.append({
                    "Key": key,
                    "Type": val_type,
                    "Age (s)": f"{age:.1f}",
                    "TTL (s)": ttl,
                    "Remaining (s)": f"{remaining:.1f}",
                    "Expired": "⚠️" if remaining <= 0 else "✅",
                })
            if rows:
                st.dataframe(rows, use_container_width=True, hide_index=True)
        else:
            st.caption("Empty")
    except Exception as exc:
        st.caption(f"Error: {exc}")

    # Disk cache
    divider()
    st.markdown("### 💾 Disk Cache")
    disk_files: list[Path] = []
    try:
        disk = cache._disk
        cache_dir = getattr(disk, "_cache_dir", None)
        if cache_dir:
            st.text(f"Directory: {cache_dir}")
            disk_files = list(cache_dir.glob("*.json"))
            if disk_files:
                st.metric("Files", len(disk_files))
                rows = []
                for f in sorted(disk_files):
                    rows.append({
                        "File": f.name,
                        "Size (bytes)": f.stat().st_size,
                        "Modified": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(f.stat().st_mtime)),
                    })
                st.dataframe(rows, use_container_width=True, hide_index=True)
            else:
                st.caption("Empty")
    except Exception as exc:
        st.caption(f"Error: {exc}")

    # Controls
    divider()
    col1, col2, col3 = st.columns(3)
    with col1:
        if st.button("🔄 Refresh Cache", use_container_width=True, type="primary"):
            with st.spinner("Refreshing..."):
                try:
                    service.refresh_cache()
                    st.success("Done")
                    st.rerun()
                except Exception as e:
                    st.error(str(e))
    with col2:
        if st.button("🗑️ Clear Cache", use_container_width=True):
            service.clear_cache()
            st.success("Cleared")
            st.rerun()
    with col3:
        st.caption(f"Updated: {time.strftime('%H:%M:%S')}")


def _render_metrics_tab(metrics: Any, service: _DataService) -> None:
    """Request metrics with KPI cards and latency charts."""
    st.markdown("### 📈 Request Metrics")

    col1, col2, col3, col4 = st.columns(4)
    with col1:
        st.metric("Total Requests", metrics.total_requests)
    with col2:
        st.metric("Cache Hits", metrics.cache_hits)
    with col3:
        st.metric("Cache Misses", metrics.cache_misses)
    with col4:
        hit_rate = metrics.cache_hit_rate
        st.metric(
            "Cache Hit Rate",
            f"{hit_rate:.1f}%",
            delta=f"{hit_rate - 80:.1f}%" if hit_rate else None,
        )

    col1, col2, col3, col4 = st.columns(4)
    with col1:
        st.metric("API Calls", metrics.api_calls)
    with col2:
        st.metric("CSV Fallbacks", metrics.csv_fallbacks)
    with col3:
        st.metric("Provider Failures", metrics.provider_failures)
    with col4:
        st.metric("Rate Limited", metrics.rate_limited_count)

    col1, col2, col3 = st.columns(3)
    with col1:
        st.metric("Avg Latency", f"{metrics.average_latency_ms:.1f} ms")
    with col2:
        st.metric("P95 Latency", f"{metrics.p95_latency_ms:.1f} ms")
    with col3:
        st.metric("P99 Latency", f"{metrics.p99_latency_ms:.1f} ms")

    # Per-operation latency chart
    if HAS_PLOTLY and metrics.per_operation:
        divider()
        st.markdown("### 🕐 Per-Operation Latency")
        op_data = []
        for op_name, op_metrics in metrics.per_operation.items():
            if op_metrics.calls > 0:
                op_data.append({
                    "Operation": op_name,
                    "Calls": op_metrics.calls,
                    "Avg (ms)": op_metrics.average_latency_ms,
                    "P95 (ms)": op_metrics.p95_latency_ms,
                    "P99 (ms)": op_metrics.p99_latency_ms,
                    "Cache Hits": op_metrics.cache_hits,
                })
        if op_data:
            df = pd.DataFrame(op_data)
            fig = px.bar(
                df,
                x="Operation",
                y=["Avg (ms)", "P95 (ms)"],
                barmode="group",
                title="Operation Latency (ms)",
            )
            st.plotly_chart(fig, use_container_width=True)

    # Failure rate
    if metrics.total_requests > 0:
        divider()
        st.markdown("### 📊 Failure Analysis")
        fail_rate = metrics.failure_rate
        fig = go.Figure()
        fig.add_trace(go.Indicator(
            mode="gauge+number+delta",
            value=fail_rate,
            title={"text": "Failure Rate (%)"},
            delta={"reference": 5.0},
            gauge={
                "axis": {"range": [0, 100]},
                "bar": {"color": "red" if fail_rate > 5 else "green"},
                "steps": [
                    {"range": [0, 5], "color": "lightgreen"},
                    {"range": [5, 20], "color": "orange"},
                    {"range": [20, 100], "color": "salmon"},
                ],
            },
        ))
        st.plotly_chart(fig, use_container_width=True)

    # Reset button
    if st.button("🔄 Reset Metrics"):
        service.reset_metrics()
        st.rerun()


def _render_providers_tab(service: _DataService) -> None:
    """Provider chain, health status, and latency."""
    st.markdown("### 🔌 Provider Chain")

    provider = service._provider
    if provider:
        st.info(f"**Active**: `{provider.name}`")

        if hasattr(provider, "_providers"):
            for i, p in enumerate(provider._providers):
                health = service.health_monitor.get_health(p.name)
                if health:
                    status = "✅" if health.enabled else "❌"
                    st.markdown(
                        f"{i + 1}. {status} `{p.name}` — {type(p).__name__} — "
                        f"Success: {health.success_rate:.0f}%, "
                        f"Avg Latency: {health.average_latency_ms:.0f} ms, "
                        f"Requests: {health.total_requests}"
                    )
                else:
                    st.markdown(f"{i + 1}. `{p.name}` — {type(p).__name__}")

        if hasattr(provider, "last_provider") and provider.last_provider:
            st.success(f"Last successful: `{provider.last_provider}`")

        # Chart provider comparison
        if HAS_PLOTLY:
            all_health = service.health_monitor.get_all_health()
            if all_health:
                divider()
                st.markdown("### 📊 Provider Comparison")
                health_data = []
                for h in all_health:
                    health_data.append({
                        "Provider": h.name,
                        "Success Rate (%)": h.success_rate,
                        "Avg Latency (ms)": h.average_latency_ms,
                        "Requests": h.total_requests,
                        "Enabled": "Yes" if h.enabled else "No",
                    })
                if health_data:
                    df = pd.DataFrame(health_data)
                    fig = px.bar(df, x="Provider", y="Success Rate (%)", color="Enabled",
                                 title="Provider Success Rate")
                    st.plotly_chart(fig, use_container_width=True)
    else:
        st.warning("No provider configured")


def _render_websocket_tab(service: _DataService) -> None:
    """WebSocket connection status and stats."""
    st.markdown("### 🌐 WebSocket Connection")

    ws_stats = service.get_websocket_stats()

    col1, col2, col3 = st.columns(3)
    with col1:
        status = "✅ Connected" if ws_stats.connected else "❌ Disconnected"
        st.metric("Status", status)
    with col2:
        st.metric("Reconnects", ws_stats.reconnect_count)
    with col3:
        st.metric("Messages Received", ws_stats.messages_received)

    col1, col2, col3 = st.columns(3)
    with col1:
        if ws_stats.connected:
            st.metric("Uptime", f"{ws_stats.uptime_seconds:.0f}s")
    with col2:
        st.metric("Subscribers", ws_stats.subscribers)
    with col3:
        st.text(f"URL: {ws_stats.url}")

    # Controls
    divider()
    col1, col2 = st.columns(2)
    with col1:
        if st.button("▶️ Start Live Feed", use_container_width=True, type="primary"):
            service.start_live_feed()
            st.success("WebSocket feed started")
            st.rerun()
    with col2:
        if st.button("⏹️ Stop Live Feed", use_container_width=True):
            service.stop_live_feed()
            st.info("WebSocket feed stopped, falling back to API polling")
            st.rerun()


def _render_background_tab(service: _DataService) -> None:
    """Background refresh status and controls."""
    st.markdown("### 🔄 Background Refresh")

    running = service.is_background_refresh_running()
    paused = service.is_background_refresh_paused()

    col1, col2, col3 = st.columns(3)
    with col1:
        status = "✅ Running" if running else "⏹️ Stopped"
        st.metric("Status", status)
    with col2:
        if running:
            st.metric("Paused", "Yes" if not paused else "No")
    with col3:
        st.metric("Interval", f"{service._background_interval}s")

    divider()
    col1, col2, col3, col4 = st.columns(4)
    with col1:
        if st.button("▶️ Start", use_container_width=True, type="primary"):
            service.start_background_refresh()
            st.rerun()
    with col2:
        if st.button("⏹️ Stop", use_container_width=True):
            service.stop_background_refresh()
            st.rerun()
    with col3:
        if running and paused:
            if st.button("▶️ Resume", use_container_width=True):
                service.resume_background_refresh()
                st.rerun()
        elif running:
            if st.button("⏸️ Pause", use_container_width=True):
                service.pause_background_refresh()
                st.rerun()
    with col4:
        interval = st.number_input(
            "Interval (s)",
            min_value=5,
            max_value=3600,
            value=service._background_interval,
            step=5,
        )
        if st.button("Set", use_container_width=True):
            service.set_refresh_interval(int(interval))
            st.success(f"Interval set to {interval}s")


def _render_rate_limiter_tab(service: _DataService) -> None:
    """Rate limiter statistics per provider."""
    st.markdown("### ⚡ Rate Limiter")

    all_stats = service.rate_limiter.get_all_stats()
    if all_stats:
        rows = []
        for stat in all_stats:
            rows.append({
                "Provider": stat.provider,
                "Tokens": f"{stat.tokens_remaining:.1f}/{stat.max_tokens:.0f}",
                "Cooldown": f"{stat.cooldown_remaining:.0f}s" if stat.cooldown_active else "No",
                "Failures": stat.consecutive_failures,
                "Throttled": stat.total_throttled,
                "429s": stat.total_429,
                "503s": stat.total_503,
            })
        if rows:
            st.dataframe(rows, use_container_width=True, hide_index=True)
    else:
        st.caption("No rate limiter data yet")

    if HAS_PLOTLY and all_stats:
        divider()
        st.markdown("### 📊 Rate Limit Events")
        stat_data = []
        for s in all_stats:
            stat_data.append({
                "Provider": s.provider,
                "429 Errors": s.total_429,
                "503 Errors": s.total_503,
                "Throttled": s.total_throttled,
            })
        if stat_data:
            df = pd.DataFrame(stat_data)
            fig = px.bar(df, x="Provider", y=["429 Errors", "503 Errors", "Throttled"],
                         barmode="group", title="Rate Limit Events by Provider")
            st.plotly_chart(fig, use_container_width=True)


def _render_health_tab(service: _DataService) -> None:
    """Provider health status and history."""
    st.markdown("### 🧠 Provider Health")

    all_health = service.health_monitor.get_all_health()
    if all_health:
        rows = []
        for h in all_health:
            rows.append({
                "Provider": h.name,
                "Enabled": "✅" if h.enabled else "❌",
                "Success Rate": f"{h.success_rate:.1f}%",
                "Requests": h.total_requests,
                "Failures": h.failed_requests,
                "Consecutive Failures": h.consecutive_failures,
                "Avg Latency": f"{h.average_latency_ms:.0f} ms",
                "Last Success": time.strftime(
                    "%H:%M:%S", time.localtime(h.last_success)
                ) if h.last_success > 0 else "—",
                "Last Failure": time.strftime(
                    "%H:%M:%S", time.localtime(h.last_failure)
                ) if h.last_failure > 0 else "—",
            })
        if rows:
            st.dataframe(rows, use_container_width=True, hide_index=True)

        divider()
        col1, col2 = st.columns(2)
        with col1:
            if st.button("🔄 Enable All Providers", use_container_width=True):
                for h in all_health:
                    service.health_monitor.enable(h.name)
                st.rerun()
        with col2:
            if st.button("🔄 Reset All Health", use_container_width=True):
                for h in all_health:
                    service.health_monitor.reset(h.name)
                st.rerun()
    else:
        st.caption("No health data yet — make some API calls first")
