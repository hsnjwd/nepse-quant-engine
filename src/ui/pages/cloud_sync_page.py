"""Cloud Sync page — synchronize app data through a pluggable provider."""

from __future__ import annotations

import pandas as pd
import streamlit as st

from src.ui.helpers import section_header, divider
from src.ui.components import kpi_card


def render() -> None:
    """Render the Cloud Sync page."""
    section_header(
        "☁️ Cloud Sync",
        "Synchronize watchlist, settings, and portfolio data",
    )

    try:
        from src.sync import default_sync_manager
        from src.sync.base import SyncItem, SyncKind
        from src.sync.local import LocalSyncProvider
        from src.sync.manager import SyncManager
    except ImportError as exc:
        st.error(f"Sync module unavailable: {exc}")
        return

    provider_dir = st.text_input("Sync directory", value="sync_store", key="cs_dir")
    manager = SyncManager(LocalSyncProvider(provider_dir))

    col1, col2 = st.columns(2)
    with col1:
        if st.button("⬆️ Sync Now", type="primary", key="cs_push"):
            result = manager.sync_now()
            st.session_state["cs_result"] = result
    with col2:
        if st.button("⬇️ Pull & Restore", key="cs_pull"):
            items = manager.provider.pull()
            st.session_state["cs_pulled"] = items
            restored = manager.restore(items)
            st.session_state["cs_restored"] = restored

    result = st.session_state.get("cs_result")
    if result:
        cols = st.columns(3)
        with cols[0]:
            kpi_card("Status", result.status.value.upper())
        with cols[1]:
            kpi_card("Synced", str(result.synced_items))
        with cols[2]:
            kpi_card("Failed", str(result.failed_items))
        st.caption(result.message)

    restored = st.session_state.get("cs_restored")
    if restored:
        st.markdown("#### Restored")
        st.json(restored)

    pulled = st.session_state.get("cs_pulled")
    if pulled:
        st.markdown("#### Pulled Items")
        rows = [
            {
                "kind": item.kind.value if hasattr(item.kind, "value") else item.kind,
                "key": item.key,
                "updated_at": item.updated_at,
            }
            for item in pulled
        ]
        st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)

    divider()
    st.caption(
        "Providers are pluggable — implement SyncProvider to connect "
        "any cloud backend (no provider is hardcoded)."
    )
