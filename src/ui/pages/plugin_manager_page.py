"""Plugin Manager page — discover, load, inspect, enable/disable, install/uninstall."""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import streamlit as st

from src.ui.helpers import section_header, divider, empty_state
from src.ui.components import kpi_card
from src.ui.notifications import notification_manager as _notif


def render() -> None:
    """Render the Plugin Manager page."""
    section_header(
        "🔌 Plugin Manager",
        "Discover, enable/disable, install and uninstall platform plugins",
    )

    try:
        from src.plugins import load_plugins, plugin_metadata, registry, reload_plugins
    except ImportError as exc:
        st.error(f"Plugin module unavailable: {exc}")
        return

    col1, col2, col3 = st.columns(3)
    with col1:
        if st.button("🔍 Discover Plugins", type="primary", key="pl_scan"):
            count = len(load_plugins())
            st.session_state["pl_scanned"] = True
            st.success(f"Discovered and registered {count} plugin(s).")
            _notif.notify_system(f"Discovered {count} plugins")
    with col2:
        if st.button("🔄 Reload Plugins", key="pl_reload"):
            count = reload_plugins()
            st.session_state["pl_scanned"] = True
            st.success(f"Reloaded {count} plugin(s).")
            _notif.notify_system(f"Reloaded {count} plugins")
    with col3:
        if st.button("🗑️ Clear Registry", key="pl_clear"):
            registry().clear()
            st.session_state.pop("pl_scanned", None)
            st.info("Plugin registry cleared.")
            _notif.notify_system("Plugin registry cleared")

    if st.session_state.get("pl_scanned") or registry().count() > 0:
        metadata = plugin_metadata()
        kpi_card("Registered Plugins", str(len(metadata)))

        if metadata:
            st.markdown("#### Plugin Catalog")
            df = pd.DataFrame(metadata)
            st.dataframe(df, use_container_width=True, hide_index=True)

            divider()

            by_type = df.groupby("type").size().to_dict() if not df.empty else {}
            if by_type:
                st.markdown("#### By Category")
                cols = st.columns(min(len(by_type), 4))
                for i, (category, count) in enumerate(sorted(by_type.items())):
                    with cols[i % 4]:
                        kpi_card(category.title(), str(count))

            st.markdown("#### Plugin Detail")
            selected = st.selectbox(
                "Plugin",
                [m["name"] for m in metadata],
                key="pl_select",
            )
            if selected:
                _render_plugin_controls(selected)
    else:
        empty_state(
            "🔌",
            "No plugins loaded",
            "Click **Discover Plugins** to scan the built-in and on-disk plugin folders.",
        )

    divider()

    # ── Install from folder (Part 4) ──────────────────────────────
    st.markdown("##### 📂 Install from Folder")
    col1, col2 = st.columns([3, 1])
    with col1:
        plugin_dir = st.text_input(
            "Plugin directory (absolute or relative)",
            value="plugins",
            key="pl_install_dir",
        )
    with col2:
        if st.button("⬆️ Install", key="pl_install_btn", use_container_width=True):
            _install_from_folder(plugin_dir)

    # ── Uninstall (Part 4) ────────────────────────────────────────
    st.markdown("##### 🗑️ Uninstall Plugin")
    if registry().count() > 0:
        col1, col2 = st.columns([3, 1])
        with col1:
            uninstall_name = st.selectbox(
                "Plugin to uninstall",
                [m["name"] for m in registry().metadata()],
                key="pl_uninstall_select",
            )
        with col2:
            if st.button("🗑️ Uninstall", key="pl_uninstall_btn", use_container_width=True):
                try:
                    registry().unregister(uninstall_name)
                    st.success(f"Uninstalled '{uninstall_name}' (removed from registry).")
                    _notif.notify_system(f"Uninstalled plugin {uninstall_name}")
                    st.rerun()
                except KeyError as exc:
                    st.warning(str(exc))


# ── Per-plugin controls ───────────────────────────────────────────


def _render_plugin_controls(selected: str) -> None:
    """Render per-plugin controls: metadata, settings, deps, enable/disable (Part 4)."""
    from src.plugins import registry

    try:
        plugin = registry().get(selected)
    except KeyError:
        st.warning("Plugin not found.")
        return

    # Metadata + settings view
    st.json(plugin.metadata())

    # Type detection
    if hasattr(plugin, "compute"):
        st.caption("Type: IndicatorPlugin — implements compute()")
    elif hasattr(plugin, "render"):
        st.caption("Type: ReportPlugin — implements render()")
    elif hasattr(plugin, "check"):
        st.caption("Type: AlertPlugin — implements check()")
    elif hasattr(plugin, "send"):
        st.caption("Type: NotificationPlugin — implements send()")
    elif hasattr(plugin, "build"):
        st.caption("Type: StrategyPlugin — implements build()")

    # Dependency viewer (Part 4)
    st.markdown("##### 🧩 Dependencies")
    try:
        module = type(plugin).__module__
        deps: list[str] = []
        meta = plugin.metadata()
        if isinstance(meta, dict) and meta.get("dependencies"):
            deps = list(meta["dependencies"])
        else:
            deps = [module]
        st.caption(" · ".join(deps) if deps else "None declared")
    except Exception:
        st.caption("None declared")

    # Enable / Disable (Part 4)
    is_enabled = registry().is_enabled(selected)
    c1, c2 = st.columns(2)
    with c1:
        if not is_enabled and st.button("✅ Enable", key=f"pl_enable_{selected}", use_container_width=True):
            if registry().enable(selected):
                st.success(f"'{selected}' enabled.")
                _notif.notify_system(f"Enabled plugin {selected}", priority="success")
                st.rerun()
    with c2:
        if is_enabled and st.button("🚫 Disable", key=f"pl_disable_{selected}", use_container_width=True):
            if registry().disable(selected):
                st.success(f"'{selected}' disabled.")
                _notif.notify_system(f"Disabled plugin {selected}")
                st.rerun()

    st.caption("Status: " + ("🟢 Enabled" if is_enabled else "🔴 Disabled"))


def _install_from_folder(plugin_dir: str) -> None:
    """Install plugin classes from a directory into the registry (Part 4)."""
    from src.plugins import registry
    from src.plugins.discovery import discover_in_directory

    try:
        path = Path(plugin_dir)
        classes = discover_in_directory(path)
        if not classes:
            st.warning(f"No plugin classes found in {path}.")
            return
        reg = registry()
        added = 0
        for cls in classes:
            try:
                instance = cls()
                instance.initialize()
                if not reg.has(instance.name):
                    reg.register(instance)
                    added += 1
            except Exception as exc:
                st.warning(f"Could not install {cls.__name__}: {exc}")
        st.success(f"Installed {added} plugin(s) from {path}.")
        st.session_state["pl_scanned"] = True
        _notif.notify_system(f"Installed {added} plugins from {plugin_dir}")
    except Exception as exc:
        st.error(f"Install failed: {exc}")
