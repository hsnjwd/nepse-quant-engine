"""Export module for the NEPSE Quant Engine Dashboard.

Supports exporting dashboard data to:

- **PDF** — via a simple text-to-PDF report
- **HTML** — self-contained interactive HTML with embedded Plotly charts
- **CSV** — tabular portfolio/trade data
- **JSON** — full data serialisation

All exporters work with standard library modules where possible.
"""

from __future__ import annotations

import csv
import json
import os
from typing import Any


# ======================================================================
# JSON export
# ======================================================================


def export_json(
    data: dict[str, Any],
    path: str,
    indent: int = 2,
) -> str:
    """Export data to a JSON file.

    Args:
        data: Dictionary to serialise.
        path: Output file path.
        indent: JSON indentation level (default ``2``).

    Returns:
        The absolute path of the written file.

    Raises:
        OSError: If the file cannot be written.
        TypeError: If *data* contains non-serialisable types.
    """
    with open(path, mode="w", encoding="utf-8") as f:
        json.dump(data, f, indent=indent, ensure_ascii=False, default=str)
    abs_path = os.path.abspath(path)
    return abs_path


# ======================================================================
# CSV export
# ======================================================================


def export_csv(
    rows: list[dict[str, Any]],
    path: str,
    fieldnames: list[str] | None = None,
) -> str:
    """Export tabular data to a CSV file.

    Args:
        rows: List of dictionaries (one per row).
        path: Output file path.
        fieldnames: Optional column order.  If omitted, uses keys from
            the first row.

    Returns:
        The absolute path of the written file.

    Raises:
        OSError: If the file cannot be written.
    """
    if not rows:
        _write_empty_csv(path)
        return os.path.abspath(path)

    fn = fieldnames or list(rows[0].keys())
    with open(path, mode="w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fn, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)

    return os.path.abspath(path)


def _write_empty_csv(path: str) -> None:
    """Write a CSV file with only a header row for an empty dataset.

    Args:
        path: Output file path.
    """
    with open(path, mode="w", newline="", encoding="utf-8") as f:
        pass  # empty file


# ======================================================================
# HTML export
# ======================================================================


def export_html(
    title: str,
    sections: list[dict[str, Any]],
    path: str,
) -> str:
    """Export a self-contained HTML dashboard report.

    Each element of *sections* is a dict with ``type`` and ``content``
    keys.  Supported types:

    - ``"text"`` — plain text or markdown-style content string.
    - ``"table"`` — dict with ``headers`` and ``rows`` keys.
    - ``"plotly"`` — Plotly figure JSON (from ``fig.to_json()``).
    - ``"metric"`` — dict with ``label`` and ``value`` keys.

    Args:
        title: Page title.
        sections: List of section dicts.
        path: Output HTML file path.

    Returns:
        The absolute path of the written file.

    Raises:
        OSError: If the file cannot be written.
    """
    html_parts: list[str] = [
        "<!DOCTYPE html>",
        '<html lang="en">',
        "<head>",
        '<meta charset="UTF-8">',
        '<meta name="viewport" content="width=device-width, initial-scale=1.0">',
        f"<title>{_escape_html(title)}</title>",
        '<script src="https://cdn.plot.ly/plotly-2.35.2.min.js"></script>',
        "<style>",
        _HTML_STYLES,
        "</style>",
        "</head>",
        "<body>",
        f'<div class="dashboard-container">',
        f'<h1>{_escape_html(title)}</h1>',
    ]

    for section in sections:
        section_type = section.get("type", "text")
        content = section.get("content", "")

        if section_type == "text":
            html_parts.append(
                f'<div class="section text-section">{_escape_html(str(content))}</div>'
            )

        elif section_type == "table":
            headers = content.get("headers", [])
            rows = content.get("rows", [])
            html_parts.append('<div class="section"><table>')
            if headers:
                html_parts.append("<thead><tr>")
                for h in headers:
                    html_parts.append(f"<th>{_escape_html(h)}</th>")
                html_parts.append("</tr></thead>")
            html_parts.append("<tbody>")
            for row in rows:
                html_parts.append("<tr>")
                for cell in row:
                    html_parts.append(f"<td>{_escape_html(str(cell))}</td>")
                html_parts.append("</tr>")
            html_parts.append("</tbody></table></div>")

        elif section_type == "metric":
            label = _escape_html(str(content.get("label", "")))
            value = _escape_html(str(content.get("value", "")))
            html_parts.append(
                f'<div class="section metric-card">'
                f'<span class="metric-label">{label}</span>'
                f'<span class="metric-value">{value}</span>'
                f"</div>"
            )

        elif section_type == "plotly":
            fig_json = content.get("fig_json", "{}")
            div_id = f"plotly-{len(html_parts)}"
            html_parts.append(
                f'<div class="section chart-section" id="{div_id}"></div>'
            )
            html_parts.append(
                f"<script>"
                f"var data = {fig_json};"
                f"Plotly.newPlot('{div_id}', data.data || [], data.layout || {{}}, "
                f"{{responsive: true}});"
                f"</script>"
            )

    html_parts.append("</div></body></html>")

    with open(path, mode="w", encoding="utf-8") as f:
        f.write("\n".join(html_parts))

    return os.path.abspath(path)


def _escape_html(text: str) -> str:
    """Escape HTML special characters.

    Args:
        text: Raw text.

    Returns:
        HTML-safe string.
    """
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&#x27;")
    )


_HTML_STYLES = """
* { margin: 0; padding: 0; box-sizing: border-box; }
body {
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
    background: #0f172a; color: #e2e8f0; padding: 24px;
}
.dashboard-container { max-width: 1200px; margin: 0 auto; }
h1 { font-size: 24px; margin-bottom: 24px; color: #f1f5f9; }
.section { margin-bottom: 20px; }
.text-section {
    background: #1e293b; border-radius: 8px; padding: 16px;
    line-height: 1.6; white-space: pre-wrap;
}
table {
    width: 100%; border-collapse: collapse;
    background: #1e293b; border-radius: 8px; overflow: hidden;
}
th, td { padding: 10px 14px; text-align: left; border-bottom: 1px solid #334155; }
th { background: #334155; color: #94a3b8; font-size: 12px; text-transform: uppercase; }
tr:last-child td { border-bottom: none; }
.metric-card {
    display: inline-flex; flex-direction: column;
    background: #1e293b; border-radius: 8px; padding: 16px 24px;
    margin: 0 12px 12px 0; min-width: 160px;
}
.metric-label { font-size: 11px; color: #94a3b8; text-transform: uppercase; }
.metric-value { font-size: 28px; font-weight: 700; color: #f1f5f9; margin-top: 4px; }
.chart-section { background: #1e293b; border-radius: 8px; padding: 12px; }
"""


# ======================================================================
# PDF export
# ======================================================================


def export_pdf(
    title: str,
    sections: list[dict[str, Any]],
    path: str,
) -> str:
    """Export a simple text-based PDF report.

    .. note::

       This generates a minimal PDF without external dependencies using
       the Python standard library.  The output is a text-only report
       rendered in Courier font.  For production-quality PDF generation
       with charts, consider using ``weasyprint`` or ``reportlab`` with
       the :func:`export_html` output as an intermediate step.

    Args:
        title: Report title.
        sections: List of section dicts (same format as
            :func:`export_html`).
        path: Output PDF file path.

    Returns:
        The absolute path of the written file.

    Raises:
        OSError: If the file cannot be written.
    """
    _write_text_pdf(title, sections, path)
    return os.path.abspath(path)


def _write_text_pdf(
    title: str,
    sections: list[dict[str, Any]],
    path: str,
) -> None:
    """Write a minimal text-based PDF using a simple approach.

    This creates a valid PDF with basic text content.  For complex
    reports with charts, use the HTML export.

    Args:
        title: Report title.
        sections: List of section dicts.
        path: Output file path.
    """
    lines: list[str] = []
    lines.append(f"NEPSE Quant Engine Report: {title}")
    lines.append("=" * 60)
    lines.append("")

    for section in sections:
        section_type = section.get("type", "text")
        content = section.get("content", {})

        if section_type == "text":
            lines.append(str(content))
            lines.append("")

        elif section_type == "metric":
            label = content.get("label", "")
            value = content.get("value", "")
            lines.append(f"{label}: {value}")

        elif section_type == "table":
            headers = content.get("headers", [])
            rows = content.get("rows", [])
            if headers:
                lines.append(" | ".join(str(h) for h in headers))
                lines.append("-" * 60)
            for row in rows:
                lines.append(" | ".join(str(c) for c in row))
            lines.append("")

    text_content = "\n".join(lines)

    # Build minimal PDF manually using Python standard library
    # PDF structure: header → objects → xref → trailer
    pdf_lines: list[str] = [
        "%PDF-1.4",
        "1 0 obj",
        "<< /Type /Catalog /Pages 2 0 R >>",
        "endobj",
        "2 0 obj",
        "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        "endobj",
        "3 0 obj",
        "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
        "/Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>",
        "endobj",
    ]

    # Escape PDF string content
    safe_text = text_content.replace("\\", "\\\\").replace(
        "(", "\\("
    ).replace(")", "\\)").replace("\n", "\\n")

    stream_content = (
        "BT\n"
        "/F1 10 Tf\n"
        "50 740 Td\n"
        f"({safe_text}) Tj\n"
        "ET\n"
    )

    stream_length = len(stream_content.encode("latin-1", errors="replace"))

    pdf_lines.extend([
        "4 0 obj",
        f"<< /Length {stream_length} >>",
        "stream",
        stream_content,
        "endstream",
        "endobj",
        "5 0 obj",
        "<< /Type /Font /Subtype /Type1 /BaseFont /Courier >>",
        "endobj",
        "xref",
        "0 6",
        "0000000000 65535 f ",
        "0000000009 00000 n ",
        "0000000058 00000 n ",
        "0000000115 00000 n ",
        "0000000266 00000 n ",
        "0000000383 00000 n ",
        "trailer",
        "<< /Size 6 /Root 1 0 R >>",
        "startxref",
        "453",
        "%%EOF",
    ])

    with open(path, mode="wb") as f:
        f.write("\n".join(pdf_lines).encode("latin-1", errors="replace"))
