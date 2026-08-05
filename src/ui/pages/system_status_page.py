"""System Status page — application health monitoring dashboard.

Shows:
- Cache status (memory/disk keys, TTL, hit rate)
- Provider health (latency, uptime, failures, success rate)
- API latency (average, P95, P99)
- WebSocket status (connected, reconnects, messages)
- Background refresh status
- Request metrics (total, cache hits/misses, failures)
- Thread count
- Last refresh time
"""

from __future__ import annotations

import time
from typing import Any

import streamlit as st
import pandas as pd

from src.ui.components import kpi_card
from src.ui.helpers import (
    section_header,
    divider,
    fmt_number,
    fmt_pct,
)
from src.ui.theme import theme
from src.data import DataService as _DataService
from src.config.user_settings import UserSettings
from src.data.cache import MemoryCache, DiskCache


def _svc() -> _DataService:
    return _DataService()


def render() -> None:
    """Render the System Status / Application Health page."""
    section_header("🩺 System Status", "Application health monitoring and diagnostics")

    service = _svc()
    settings = UserSettings()

    tabs = st.tabs([
        "📊 Overview",
        "📦 Cache",
        "🔌 Providers",
        "📡 WebSocket",
        "🔄 Background",
        "📈 Metrics",
        "⚙️ Config",
    ])

    # ── Tab 1: Overview ──────────────────────────────────────────
    with tabs[0]:
        section_header("System Overview")
        col1, col2, col3, col4 = st.columns(4)
        with col1:
            kpi_card("📊 Total Requests", str(service.get_metrics().total_requests))
        with col2:
            kpi_card("💾 Cache Hit Rate", fmt_pct(service.get_metrics().cache_hit_rate / 100.0))
        with col3:
            ws_stats = service.get_websocket_stats()
            kpi_card("📡 WebSocket", "Connected" if ws_stats.connected else "Disconnected",
                     delta="🟢" if ws_stats.connected else "⚪")
        with col4:
            bg = service.is_background_refresh_running()
            kpi_card("🔄 Background", "Running" if bg else "Stopped",
                     delta="🟢" if bg else "⚪")

        col1, col2, col3, col4 = st.columns(4)
        with col1:
            metrics = service.get_metrics()
            kpi_card("📉 API Failures", str(metrics.provider_failures))
        with col2:
            kpi_card("⏱️ Avg Latency", f"{metrics.average_latency_ms:.1f}ms")
        with col3:
            kpi_card("📡 P95 Latency", f"{metrics.p95_latency_ms:.1f}ms")
        with col4:
            kpi_card("📡 P99 Latency", f"{metrics.p99_latency_ms:.1f}ms")

    # ── Tab 2: Cache ─────────────────────────────────────────────
    with tabs[1]:
        section_header("Cache Status")
        col1, col2 = st.columns(2)

        # Memory cache info — access the real cache via DataService
        real_cache = service._cache
        mem = real_cache._memory if hasattr(real_cache, '_memory') else None
        disk = real_cache._disk if hasattr(real_cache, '_disk') else None
        if mem is None:
            mem = MemoryCache()
        if disk is None:
            from src.data.cache import DiskCache as DC
            disk = DC()
        with col1:
            st.markdown("##### 🧠 Memory Cache")
            mem_size = mem.size if mem else 0
            st.metric("Total Keys", mem_size)

        with col2:
            st.markdown("##### 💾 Disk Cache")
            st.metric("Disk Cache", "Active" if disk else "N/A")

        # Cache operations
        col1, col2 = st.columns(2)
        with col1:
            if st.button("🗑️ Clear Memory Cache", use_container_width=True):
                if mem:
                    mem.clear()
                service.clear_cache()
                st.success("Cache cleared")
        with col2:
            if st.button("🔄 Refresh All Cache", use_container_width=True):
                with st.spinner("Refreshing cache..."):
                    service.refresh_cache()
                st.success("Cache refreshed")

        # Key-value inspector
        divider()
        st.markdown("##### 🔍 Cache Inspector")
        col1, col2 = st.columns([3, 1])
        with col1:
            cache_key = st.text_input("Cache key to inspect", placeholder="e.g. market_summary", key="cache_key_input")
        with col2:
            inspect_btn = st.button("🔍 Inspect", use_container_width=True)

        if inspect_btn and cache_key and mem:
            val = mem.get(cache_key)
            if val is None:
                st.info(f"Key '{cache_key}' not found in memory cache")
            else:
                val_str = str(val)[:500]
                st.code(val_str, language="text")

    # ── Tab 3: Providers ─────────────────────────────────────────
    with tabs[2]:
        section_header("Provider Health")
        health_data = service.health_monitor.get_all_health()
        if health_data:
            rows = []
            for h in health_data:
                rows.append({
                    "Provider": h.name,
                    "Status": "🟢 Healthy" if h.enabled else "🔴 Disabled",
                    "Total": h.total_requests,
                    "Success": h.successful_requests,
                    "Failures": h.failed_requests,
                    "Success Rate": fmt_pct(h.success_rate / 100.0),
                    "Avg Latency": f"{h.average_latency_ms:.1f}ms",
                    "Last Success": time.strftime("%H:%M:%S", time.localtime(h.last_success)) if h.last_success > 0 else "—",
                    "Last Failure": time.strftime("%H:%M:%S", time.localtime(h.last_failure)) if h.last_failure > 0 else "—",
                })
            df = pd.DataFrame(rows)

            def color_status(val: str) -> str:
                if "Healthy" in val:
                    return "color: #00C853"
                if "Disabled" in val:
                    return "color: #FF5252"
                return ""

            st.dataframe(
                df.style.applymap(color_status, subset=["Status"]),
                use_container_width=True,
                hide_index=True,
            )
        else:
            st.info("No provider health data available.")

        col1, col2 = st.columns(2)
        with col1:
            if st.button("🔄 Reset Provider Health", use_container_width=True):
                for h in health_data:
                    service.health_monitor.reset(h.name)
                st.success("Provider health reset")
        with col2:
            if st.button("🔁 Re-enable All", use_container_width=True):
                for h in health_data:
                    service.health_monitor.enable(h.name)
                st.success("All providers enabled")

    # ── Tab 4: WebSocket ─────────────────────────────────────────
    with tabs[3]:
        section_header("WebSocket Status")
        ws = service.get_websocket_stats()
        col1, col2, col3 = st.columns(3)
        with col1:
            kpi_card("📡 Status", "Connected" if ws.connected else "Disconnected")
        with col2:
            kpi_card("🔄 Reconnects", str(ws.reconnect_count))
        with col3:
            kpi_card("📨 Messages", str(ws.messages_received))

        if ws.connected:
            st.metric("Uptime", f"{ws.uptime_seconds:.0f}s")
            st.metric("Subscribers", ws.subscribers)
            st.metric("URL", ws.url)

        col1, col2 = st.columns(2)
        with col1:
            if st.button("▶️ Start Live Feed", use_container_width=True):
                try:
                    service.start_live_feed()
                    st.success("Live feed started")
                except Exception as e:
                    st.error(f"Failed: {e}")
        with col2:
            if st.button("⏹️ Stop Live Feed", use_container_width=True):
                service.stop_live_feed()
                st.success("Live feed stopped")

    # ── Tab 5: Background Refresh ────────────────────────────────
    with tabs[4]:
        section_header("Background Refresh")
        running = service.is_background_refresh_running()
        paused = service.is_background_refresh_paused()

        col1, col2, col3 = st.columns(3)
        with col1:
            kpi_card("🔄 Status", "Running" if running else "Stopped",
                     delta="🟢" if running else "⚪")
        with col2:
            kpi_card("⏸️ Paused", "Yes" if not paused else "No")
        with col3:
            kpi_card("⏱️ Interval", f"{service._background_interval}s")

        col1, col2, col3, col4 = st.columns(4)
        with col1:
            if st.button("▶️ Start", use_container_width=True):
                service.start_background_refresh()
                st.rerun()
        with col2:
            if st.button("⏸️ Pause", use_container_width=True):
                service.pause_background_refresh()
                st.rerun()
        with col3:
            if st.button("▶️ Resume", use_container_width=True):
                service.resume_background_refresh()
                st.rerun()
        with col4:
            if st.button("⏹️ Stop", use_container_width=True):
                service.stop_background_refresh()
                st.rerun()

        interval = st.slider("Refresh Interval (s)", 10, 600, int(service._background_interval), key="sys_bg_interval")
        if st.button("Set Interval", use_container_width=True):
            service.set_refresh_interval(interval)
            st.success(f"Interval set to {interval}s")

    # ── Tab 6: Metrics ───────────────────────────────────────────
    with tabs[5]:
        section_header("Request Metrics")
        m = service.get_metrics()
        col1, col2, col3 = st.columns(3)
        with col1:
            kpi_card("📊 Total", str(m.total_requests))
            kpi_card("💾 Cache Hits", str(m.cache_hits))
            kpi_card("🔍 Cache Misses", str(m.cache_misses))
        with col2:
            kpi_card("📡 API Calls", str(m.api_calls))
            kpi_card("📉 CSV Fallbacks", str(m.csv_fallbacks))
            kpi_card("❌ Provider Failures", str(m.provider_failures))
        with col3:
            kpi_card("⏱️ Avg Latency", f"{m.average_latency_ms:.1f}ms")
            kpi_card("📡 P95", f"{m.p95_latency_ms:.1f}ms")
            kpi_card("📡 P99", f"{m.p99_latency_ms:.1f}ms")

        # Cache hit rate chart
        if m.total_requests > 0:
            divider()
            st.markdown("##### Cache Hit Rate")
            try:
                import plotly.graph_objects as go
                fig = go.Figure(go.Indicator(
                    mode="gauge+number",
                    value=m.cache_hit_rate,
                    number={"suffix": "%", "font": {"color": theme.text}},
                    gauge={
                        "axis": {"range": [0, 100], "tickcolor": theme.text_secondary},
                        "bar": {"color": theme.primary},
                        "steps": [
                            {"range": [0, 50], "color": "#FF525233"},
                            {"range": [50, 80], "color": "#FFC10733"},
                            {"range": [80, 100], "color": "#00C85333"},
                        ],
                        "threshold": {
                            "line": {"color": theme.success, "width": 4},
                            "thickness": 0.75,
                            "value": 80,
                        },
                    },
                ))
                fig.update_layout(
                    paper_bgcolor="rgba(0,0,0,0)",
                    font=dict(color=theme.text),
                    height=250,
                )
                st.plotly_chart(fig, use_container_width=True)
            except ImportError:
                kpi_card("💾 Cache Hit Rate", fmt_pct(m.cache_hit_rate / 100.0))

        if st.button("🔄 Reset Metrics", use_container_width=True):
            service.reset_metrics()
            st.success("Metrics reset")

    # ── Tab 7: Config ────────────────────────────────────────────
    with tabs[6]:
        section_header("Application Configuration")
        all_settings = settings.get_all()
        rows = []
        for key, value in all_settings.items():
            if not key.startswith("_"):
                rows.append({"Setting": key, "Value": str(value)[:100]})
        st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)

        col1, col2 = st.columns(2)
        with col1:
            if st.button("🔄 Reload Config", use_container_width=True):
                settings._load()
                st.success("Configuration reloaded")
        with col2:
            if st.button("🔄 Reset to Defaults", use_container_width=True):
                settings.reset()
                st.success("Settings reset to defaults")
