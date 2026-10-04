"""Keyboard shortcuts for NEPSE Quant Engine.

Provides JavaScript injection for global keyboard shortcuts
(Ctrl+K/R/P/S/B/D/H/?) that work across all pages.
"""

from __future__ import annotations

import streamlit as st

from src.ui.theme import theme

SHORTCUTS = {
    "Ctrl+K": "Global search (focuses symbol search)",
    "Ctrl+R": "Refresh current page",
    "Ctrl+P": "Navigate to Portfolio",
    "Ctrl+S": "Navigate to Scanner",
    "Ctrl+B": "Navigate to Backtest",
    "Ctrl+D": "Navigate to Dashboard",
    "Ctrl+H": "Navigate to System Health",
    "?": "Show keyboard shortcuts help",
}


def inject_keyboard_shortcuts() -> None:
    """Inject JavaScript for global keyboard shortcuts into the Streamlit app.

    Call this once in app.py after page config and before the main content.
    """
    js_code = """
    <script>
    document.addEventListener('keydown', function(e) {
        // Don't trigger shortcuts when typing in inputs
        if (e.target.tagName === 'INPUT' || e.target.tagName === 'TEXTAREA') {
            if (e.key === 'Escape') {
                e.target.blur();
            }
            return;
        }

        var key = '';
        if (e.ctrlKey || e.metaKey) {
            key = 'Ctrl+' + e.key.toUpperCase();
        } else {
            key = e.key;
        }

        var page = '';
        switch (key) {
            case 'Ctrl+K':
                // Focus the symbol search input
                var searchInput = document.querySelector('input[placeholder*="ymbol"], input[placeholder*="earch"]');
                if (searchInput) { searchInput.focus(); searchInput.select(); }
                e.preventDefault();
                break;
            case 'Ctrl+R':
                // Allow browser to handle native refresh
                break;
            case 'Ctrl+P':
                page = 'portfolio';
                e.preventDefault();
                break;
            case 'Ctrl+S':
                page = 'scanner';
                e.preventDefault();
                break;
            case 'Ctrl+B':
                page = 'backtest';
                e.preventDefault();
                break;
            case 'Ctrl+D':
                page = 'dashboard';
                e.preventDefault();
                break;
            case 'Ctrl+H':
                page = 'system_status';
                e.preventDefault();
                break;
            case '?':
                var helpDiv = document.getElementById('shortcuts-help');
                if (helpDiv) {
                    helpDiv.style.display = helpDiv.style.display === 'none' ? 'block' : 'none';
                }
                e.preventDefault();
                break;
        }

        if (page) {
            // Streamlit page navigation via URL hash
            // Find the nav button by label text matching and click it
            var buttons = document.querySelectorAll('button[data-testid="baseButton-secondary"], button[data-testid="baseButton-primary"]');
            for (var i = 0; i < buttons.length; i++) {
                if (buttons[i].innerText.toLowerCase().indexOf(page.replace('_', ' ')) >= 0) {
                    buttons[i].click();
                    break;
                }
            }
        }
    });
    </script>
    """
    st.markdown(js_code, unsafe_allow_html=True)


def render_shortcuts_help() -> None:
    """Render the keyboard shortcuts help overlay when '?' is pressed."""
    st.markdown(
        f"""
        <div id="shortcuts-help" style="display: none; position: fixed; top: 50%; left: 50%;
             transform: translate(-50%, -50%); background: {theme.card_bg};
             border: 1px solid {theme.border}; border-radius: 12px;
             padding: 24px; z-index: 9999; min-width: 400px;
             box-shadow: 0 8px 32px rgba(0,0,0,0.5);">
            <h3 style="color: {theme.text}; margin-bottom: 16px;">⌨️ Keyboard Shortcuts</h3>
            <table style="width: 100%; color: {theme.text};">
                <tr><td style="padding: 6px 12px;"><kbd style="background: {theme.input_bg};
                    padding: 2px 8px; border-radius: 4px; font-size: 0.85rem;">Ctrl+K</kbd></td>
                    <td style="padding: 6px 12px; color: {theme.text_secondary};">Global search</td></tr>
                <tr><td style="padding: 6px 12px;"><kbd style="background: {theme.input_bg};
                    padding: 2px 8px; border-radius: 4px; font-size: 0.85rem;">Ctrl+R</kbd></td>
                    <td style="padding: 6px 12px; color: {theme.text_secondary};">Refresh page</td></tr>
                <tr><td style="padding: 6px 12px;"><kbd style="background: {theme.input_bg};
                    padding: 2px 8px; border-radius: 4px; font-size: 0.85rem;">Ctrl+P</kbd></td>
                    <td style="padding: 6px 12px; color: {theme.text_secondary};">Portfolio</td></tr>
                <tr><td style="padding: 6px 12px;"><kbd style="background: {theme.input_bg};
                    padding: 2px 8px; border-radius: 4px; font-size: 0.85rem;">Ctrl+S</kbd></td>
                    <td style="padding: 6px 12px; color: {theme.text_secondary};">Scanner</td></tr>
                <tr><td style="padding: 6px 12px;"><kbd style="background: {theme.input_bg};
                    padding: 2px 8px; border-radius: 4px; font-size: 0.85rem;">Ctrl+B</kbd></td>
                    <td style="padding: 6px 12px; color: {theme.text_secondary};">Backtest</td></tr>
                <tr><td style="padding: 6px 12px;"><kbd style="background: {theme.input_bg};
                    padding: 2px 8px; border-radius: 4px; font-size: 0.85rem;">Ctrl+D</kbd></td>
                    <td style="padding: 6px 12px; color: {theme.text_secondary};">Dashboard</td></tr>
                <tr><td style="padding: 6px 12px;"><kbd style="background: {theme.input_bg};
                    padding: 2px 8px; border-radius: 4px; font-size: 0.85rem;">Ctrl+H</kbd></td>
                    <td style="padding: 6px 12px; color: {theme.text_secondary};">System Health</td></tr>
                <tr><td style="padding: 6px 12px;"><kbd style="background: {theme.input_bg};
                    padding: 2px 8px; border-radius: 4px; font-size: 0.85rem;">?</kbd></td>
                    <td style="padding: 6px 12px; color: {theme.text_secondary};">Toggle help</td></tr>
            </table>
            <p style="color: {theme.text_muted}; font-size: 0.8rem; margin-top: 12px;">
                Press <kbd style="background: {theme.input_bg}; padding: 2px 8px; border-radius: 4px;">?</kbd>
                to toggle this help overlay.
            </p>
        </div>
        """,
        unsafe_allow_html=True,
    )
