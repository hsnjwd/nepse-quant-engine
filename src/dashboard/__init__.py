"""Dashboard & Visualisation Module for the NEPSE Quant Engine.

Provides a standalone analytics interface with:

- Terminal-friendly reports
- Interactive Plotly dashboards
- Export to PDF, HTML, CSV, JSON
- Centralised metrics calculations
- Chart generators for every engine component
"""

from __future__ import annotations

from src.dashboard.dashboard import Dashboard

__all__ = [
    "Dashboard",
]
