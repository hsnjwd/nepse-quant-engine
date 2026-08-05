"""Export center — generate reports in CSV, Excel, JSON, and HTML formats.

Provides utilities for exporting portfolio data, trade history, signals,
backtests, charts, and performance metrics.
"""

from __future__ import annotations

import csv
import io
import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd

logger = logging.getLogger(__name__)


class ExportFormat:
    CSV = "csv"
    EXCEL = "xlsx"
    JSON = "json"
    HTML = "html"


class ExportCenter:
    """Generates downloadable reports in multiple formats."""

    @staticmethod
    def portfolio_to_csv(
        holdings: list[dict[str, Any]],
        transactions: list[dict[str, Any]],
    ) -> bytes:
        """Export portfolio holdings and transactions to CSV."""
        output = io.StringIO()
        writer = csv.writer(output)

        writer.writerow(["Portfolio Report", datetime.now().isoformat()])
        writer.writerow([])
        writer.writerow(["Holdings"])
        if holdings:
            headers = list(holdings[0].keys())
            writer.writerow(headers)
            for h in holdings:
                writer.writerow([h.get(hk, "") for hk in headers])
        writer.writerow([])
        writer.writerow(["Transactions"])
        if transactions:
            headers = list(transactions[0].keys())
            writer.writerow(headers)
            for t in transactions:
                writer.writerow([t.get(tk, "") for tk in headers])

        return output.getvalue().encode("utf-8")

    @staticmethod
    def portfolio_to_excel(
        holdings: list[dict[str, Any]],
        transactions: list[dict[str, Any]],
    ) -> bytes:
        """Export portfolio to Excel (.xlsx).

        Requires ``openpyxl``.  When it is not installed this degrades
        gracefully to CSV bytes so callers never crash.
        """
        output = io.BytesIO()
        try:
            with pd.ExcelWriter(output, engine="openpyxl") as writer:
                if holdings:
                    df_h = pd.DataFrame(holdings)
                    df_h.to_excel(writer, sheet_name="Holdings", index=False)
                if transactions:
                    df_t = pd.DataFrame(transactions)
                    df_t.to_excel(writer, sheet_name="Transactions", index=False)
        except ImportError as exc:
            logger.warning("openpyxl not installed; exporting CSV instead: %s", exc)
            return ExportCenter.portfolio_to_csv(holdings, transactions)
        return output.getvalue()

    @staticmethod
    def backtest_to_csv(trades: list[dict[str, Any]], metrics: dict[str, Any]) -> bytes:
        """Export backtest results to CSV."""
        output = io.StringIO()
        writer = csv.writer(output)

        writer.writerow(["Backtest Report", datetime.now().isoformat()])
        writer.writerow([])
        writer.writerow(["Metrics"])
        for key, value in metrics.items():
            writer.writerow([key, value])
        writer.writerow([])
        writer.writerow(["Trades"])
        if trades:
            headers = list(trades[0].keys())
            writer.writerow(headers)
            for t in trades:
                writer.writerow([t.get(tk, "") for tk in headers])

        return output.getvalue().encode("utf-8")

    @staticmethod
    def signals_to_csv(signals: list[dict[str, Any]]) -> bytes:
        """Export scan/signal results to CSV."""
        output = io.StringIO()
        if not signals:
            return output.getvalue().encode("utf-8")
        writer = csv.DictWriter(output, fieldnames=list(signals[0].keys()))
        writer.writeheader()
        for s in signals:
            writer.writerow(s)
        return output.getvalue().encode("utf-8")

    @staticmethod
    def to_json(data: Any) -> bytes:
        """Export any data to formatted JSON."""
        return json.dumps(data, default=str, indent=2).encode("utf-8")

    @staticmethod
    def to_pdf(
        title: str,
        sections: list[dict[str, Any]],
        filename: str | None = None,
    ) -> bytes:
        """Generate a professional PDF report using ReportLab.

        Args:
            title: Report title (appears on cover page).
            sections: List of dicts with ``title``, ``headers``, ``rows``, or ``content``.
            filename: Optional path to save the PDF (returns bytes anyway).

        Returns:
            PDF file contents as bytes.

        Requires ``reportlab``.  Falls back to HTML-to-PDF via weasyprint if available.
        """
        try:
            return ExportCenter._to_pdf_reportlab(title, sections, filename)
        except ImportError:
            logger.warning("reportlab not installed; falling back to weasyprint")
            try:
                return ExportCenter._to_pdf_weasyprint(title, sections)
            except (ImportError, Exception) as exc:
                logger.error("PDF generation failed: %s. Install: pip install reportlab", exc)
                # Return empty PDF placeholder
                return b"%PDF-1.4\n%PDF generation unavailable"

    @staticmethod
    def _to_pdf_reportlab(
        title: str,
        sections: list[dict[str, Any]],
        filename: str | None = None,
    ) -> bytes:
        """Generate a professional PDF report using ReportLab.

        Delegates to smaller helpers:
        - ``_build_styles`` — creates all ``ParagraphStyle`` instances
        - ``_build_cover_page`` — creates the title/date cover
        - ``_build_section_table`` — renders a single section table
        """
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.units import mm
        from reportlab.lib.colors import HexColor
        from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, PageBreak

        buf = io.BytesIO()
        doc = SimpleDocTemplate(
            buf, pagesize=A4,
            leftMargin=20 * mm, rightMargin=20 * mm,
            topMargin=20 * mm, bottomMargin=20 * mm,
        )

        styles = ExportCenter._build_reportlab_styles()
        elements: list[Any] = []

        # ── Cover page ──────────────────────────────────────────
        elements.extend(ExportCenter._build_cover_page(title, styles["normal"]))
        elements.append(PageBreak())

        # ── Content sections ─────────────────────────────────────
        for section in sections:
            elements.append(Paragraph(section["title"], styles["heading"]))
            elements.append(Spacer(1, 4 * mm))

            if "content" in section:
                elements.append(Paragraph(str(section["content"]), styles["normal"]))
            elif "headers" in section and section["headers"]:
                table = ExportCenter._build_section_table(
                    section["headers"],
                    section.get("rows", []),
                    doc.width,
                )
                if table:
                    elements.append(table)

            elements.append(Spacer(1, 6 * mm))

        # ── Footer and build ─────────────────────────────────────
        def _add_page_number(canvas: Any, _doc: Any) -> None:
            canvas.saveState()
            canvas.setFont("Helvetica", 8)
            canvas.setFillColor(HexColor("#999999"))
            canvas.drawCentredString(
                _doc.width / 2.0, 10 * mm,
                f"Page {_doc.page}",
            )
            canvas.restoreState()

        doc.build(elements, onFirstPage=_add_page_number, onLaterPages=_add_page_number)

        pdf_bytes = buf.getvalue()

        if filename:
            try:
                with open(filename, "wb") as f:
                    f.write(pdf_bytes)
            except Exception as exc:
                logger.warning("[ExportCenter] Could not write PDF to %s: %s", filename, exc)

        return pdf_bytes

    @staticmethod
    def _build_reportlab_styles() -> dict[str, Any]:
        """Return a dict of ``ParagraphStyle`` objects for PDF reports."""
        from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
        from reportlab.lib.colors import HexColor
        from reportlab.lib.enums import TA_CENTER

        styles = getSampleStyleSheet()
        return {
            "title": ParagraphStyle(
                "ReportTitle", parent=styles["Title"],
                fontSize=24, textColor=HexColor("#1F77B4"),
                spaceAfter=12, alignment=TA_CENTER,
            ),
            "heading": ParagraphStyle(
                "ReportHeading", parent=styles["Heading2"],
                fontSize=16, textColor=HexColor("#1F77B4"),
                spaceBefore=20, spaceAfter=8,
            ),
            "normal": ParagraphStyle(
                "ReportNormal", parent=styles["Normal"],
                fontSize=10, textColor=HexColor("#333333"),
                spaceAfter=4,
            ),
        }

    @staticmethod
    def _build_cover_page(title: str, normal_style: Any) -> list[Any]:
        """Return reportlab flowables for a cover page (title + date + app name)."""
        from reportlab.lib.units import inch
        from reportlab.lib.styles import ParagraphStyle
        from reportlab.lib.colors import HexColor
        from reportlab.platypus import Paragraph, Spacer
        from reportlab.lib.enums import TA_CENTER

        return [
            Spacer(1, 2 * inch),
            Paragraph(title, normal_style),
            Spacer(1, 0.5 * inch),
            Paragraph(
                f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
                ParagraphStyle("Date", parent=normal_style, alignment=TA_CENTER, textColor=HexColor("#666666")),
            ),
            Spacer(1, 0.3 * inch),
            Paragraph(
                "NEPSE Quant Engine",
                ParagraphStyle("AppName", parent=normal_style, alignment=TA_CENTER, textColor=HexColor("#999999")),
            ),
        ]

    @staticmethod
    def _build_section_table(
        headers: list[str],
        rows: list[list[str]],
        doc_width: float,
    ) -> Any:
        """Build a styled ReportLab table for a report section.

        Args:
            headers: Column header strings.
            rows: List of row cell-value lists.
            doc_width: Page content width (for column sizing).

        Returns:
            A ReportLab ``Table`` instance, or ``None`` if headers are empty.
        """
        from reportlab.lib.colors import HexColor, white
        from reportlab.platypus import Table, TableStyle

        if not headers:
            return None

        table_data: list[list[str]] = [headers]
        for row in rows:
            table_data.append([str(cell) for cell in row])

        col_width = doc_width / len(headers)
        table = Table(table_data, colWidths=[col_width] * len(headers))
        table.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), HexColor("#1F77B4")),
            ("TEXTCOLOR", (0, 0), (-1, 0), white),
            ("FONTSIZE", (0, 0), (-1, 0), 10),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("FONTSIZE", (0, 1), (-1, -1), 9),
            ("ALIGN", (0, 0), (-1, -1), "CENTER"),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("GRID", (0, 0), (-1, -1), 0.5, HexColor("#CCCCCC")),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [HexColor("#F5F5F5"), white]),
            ("TOPPADDING", (0, 0), (-1, -1), 6),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
        ]))
        return table

    @staticmethod
    def _to_pdf_weasyprint(
        title: str,
        sections: list[dict[str, Any]],
    ) -> bytes:
        """Generate PDF by rendering HTML through weasyprint."""
        from weasyprint import HTML
        html_str = ExportCenter.to_html(title, sections)
        return HTML(string=html_str).write_pdf()

    @staticmethod
    def to_html(
        title: str,
        sections: list[dict[str, Any]],
    ) -> str:
        """Generate a styled HTML report from named sections.

        Each section has ``{"title": str, "headers": list[str], "rows": list[list[str]]}``.
        """
        html_parts = [
            "<!DOCTYPE html><html><head><meta charset='utf-8'>",
            f"<title>{title}</title>",
            "<style>",
            "body { font-family: 'Segoe UI', Arial, sans-serif; margin: 20px; background: #0E1117; color: #FAFAFA; }",
            "h1 { color: #1F77B4; border-bottom: 2px solid #1F77B4; padding-bottom: 8px; }",
            "h2 { color: #FAFAFA; margin-top: 24px; }",
            "table { border-collapse: collapse; width: 100%; margin: 12px 0; }",
            "th { background: #1F77B4; color: white; padding: 8px 12px; text-align: left; }",
            "td { padding: 6px 12px; border-bottom: 1px solid #2D3138; }",
            "tr:nth-child(even) { background: #1A1D24; }",
            ".header { color: #888; font-size: 0.85rem; margin-bottom: 20px; }",
            "</style></head><body>",
            f"<h1>{title}</h1>",
            f"<p class='header'>Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}</p>",
        ]

        for section in sections:
            html_parts.append(f"<h2>{section['title']}</h2>")
            if "headers" in section and section["headers"]:
                html_parts.append("<table><thead><tr>")
                for h in section["headers"]:
                    html_parts.append(f"<th>{h}</th>")
                html_parts.append("</tr></thead><tbody>")
                for row in section.get("rows", []):
                    html_parts.append("<tr>")
                    for cell in row:
                        html_parts.append(f"<td>{cell}</td>")
                    html_parts.append("</tr>")
                html_parts.append("</tbody></table>")
            elif "content" in section:
                html_parts.append(f"<p>{section['content']}</p>")

        html_parts.append("</body></html>")
        return "\n".join(html_parts)
