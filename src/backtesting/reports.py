"""Institutional reports for the backtesting engine.

Part 9.11 — generates professional reports in HTML, PDF, Excel, and
JSON formats, including executive summary, equity curve, drawdown,
monthly returns, trade distribution, sector allocation, heatmaps, AI
commentary, risk metrics, and optimisation results.
"""

from __future__ import annotations

import io
import json
import logging
from datetime import datetime
from typing import Any

import pandas as pd

from src.backtesting.engine import BacktestResult
from src.backtesting.statistics import AdvancedPerformanceReport

logger = logging.getLogger(__name__)


class InstitutionalReport:
    """Builds professional reports from a :class:`BacktestResult`.

    Usage::

        report = InstitutionalReport(result, title="Momentum Backtest")
        html = report.to_html()
        pdf = report.to_pdf()
        xlsx = report.to_excel()
        j = report.to_json()
    """

    def __init__(
        self,
        result: BacktestResult,
        title: str = "Backtest Report",
        ai_commentary: str | None = None,
        additional_sections: list[dict[str, Any]] | None = None,
    ) -> None:
        """Initialise the report.

        Args:
            result: A completed :class:`BacktestResult`.
            title: Report title.
            ai_commentary: Optional natural-language commentary.
            additional_sections: Optional extra sections rendered as
                ``{"title", "headers", "rows"}``.
        """
        self.result = result
        self.title = title
        self.ai_commentary = ai_commentary
        self.additional_sections = additional_sections or []
        self.generated_at = datetime.now()

    # ═══════════════════════════════════════════════════════════════
    # Section builders
    # ═══════════════════════════════════════════════════════════════

    def executive_summary(self) -> list[tuple[str, Any]]:
        """Return headline metric rows as ``(label, value)`` pairs."""
        m = self.result.metrics
        return [
            ("Total Return", f"{m.total_return * 100:.2f}%"),
            ("Annualized Return", f"{m.annualized_return * 100:.2f}%"),
            ("Sharpe", f"{m.sharpe:.3f}"),
            ("Sortino", f"{m.sortino:.3f}"),
            ("Calmar", f"{m.calmar:.3f}"),
            ("Max Drawdown", f"{m.max_drawdown:.2f}%"),
            ("Ulcer Index", f"{m.ulcer_index:.3f}"),
            ("Omega", f"{m.omega:.3f}" if m.omega != float("inf") else "inf"),
            ("SQN", f"{m.sqn:.3f}"),
            ("Win Rate", f"{m.win_rate:.2f}%"),
            ("Profit Factor", f"{m.profit_factor:.3f}"),
            ("Trades", str(m.trade_count)),
            ("Exposure Time", f"{m.exposure_time * 100:.1f}%"),
            ("Alpha", f"{m.alpha:.4f}"),
            ("Beta", f"{m.beta:.3f}"),
            ("Volatility", f"{m.volatility * 100:.2f}%"),
        ]

    def risk_metrics(self) -> list[tuple[str, Any]]:
        """Return risk-focused metric rows."""
        m = self.result.metrics
        return [
            ("Max Drawdown", f"{m.max_drawdown:.2f}%"),
            ("Recovery Factor", f"{m.recovery_factor:.3f}"),
            ("MAR Ratio", f"{m.mar_ratio:.3f}"),
            ("Ulcer Index", f"{m.ulcer_index:.3f}"),
            ("Gain/Loss Ratio", f"{m.gain_loss_ratio:.3f}"),
            ("Avg Trade Duration (bars)", f"{m.avg_trade_duration:.2f}"),
            ("Avg Trade Return", f"{m.avg_trade_return * 100:.3f}%"),
            ("Expectancy", f"{m.expectancy * 100:.4f}%"),
        ]

    def trade_rows(self) -> list[list[str]]:
        """Return trade table rows."""
        return [
            [
                t.symbol,
                t.side.value,
                str(t.quantity),
                f"{t.entry_price:.2f}",
                f"{t.exit_price:.2f}",
                f"{t.net_pnl:.2f}",
                f"{t.return_pct * 100:.2f}%",
                str(t.holding_bars),
                t.exit_reason,
            ]
            for t in self.result.trades
        ]

    def sections(self) -> list[dict[str, Any]]:
        """Return the full ordered section list for PDF/HTML renderers."""
        trade_headers = [
            "Symbol", "Side", "Qty", "Entry", "Exit",
            "Net P&L", "Return %", "Bars", "Exit Reason",
        ]

        monthly = self.monthly_returns()
        monthly_headers = ["Month"] + [str(c) for c in monthly.columns] if not monthly.empty else ["Month"]
        monthly_rows = (
            [[str(idx)] + [f"{v:.2%}" if pd.notna(v) else "" for v in row]
             for idx, row in monthly.iterrows()]
            if not monthly.empty
            else []
        )

        sections: list[dict[str, Any]] = [
            {
                "title": "Executive Summary",
                "headers": ["Metric", "Value"],
                "rows": [[k, str(v)] for k, v in self.executive_summary()],
            },
            {
                "title": "Risk Metrics",
                "headers": ["Metric", "Value"],
                "rows": [[k, str(v)] for k, v in self.risk_metrics()],
            },
        ]

        if self.ai_commentary:
            sections.append({
                "title": "AI Commentary",
                "content": self.ai_commentary,
            })

        if not monthly.empty:
            sections.append({
                "title": "Monthly Returns",
                "headers": monthly_headers,
                "rows": monthly_rows,
            })

        sections.append({
            "title": f"Trades ({len(self.result.trades)})",
            "headers": trade_headers,
            "rows": self.trade_rows(),
        })

        for extra in self.additional_sections:
            sections.append(extra)

        return sections

    def monthly_returns(self) -> pd.DataFrame:
        """Return the monthly returns pivot table (may be empty)."""
        try:
            from src.backtesting.statistics import monthly_returns_table
            return monthly_returns_table(
                self.result.equity_curve,
                self.result.timestamps,
            )
        except Exception as exc:
            logger.warning("Monthly returns table failed: %s", exc)
            return pd.DataFrame()

    # ═══════════════════════════════════════════════════════════════
    # Format writers
    # ═══════════════════════════════════════════════════════════════

    def to_json(self) -> bytes:
        """Serialise the full result as pretty JSON."""
        payload = {
            "title": self.title,
            "generated_at": self.generated_at.isoformat(),
            "metrics": self.result.metrics.to_dict(),
            "trades": [t.to_dict() for t in self.result.trades],
            "orders": [o.to_dict() for o in self.result.orders],
            "fills": [f.to_dict() for f in self.result.fills],
            "equity_curve": [round(v, 2) for v in self.result.equity_curve],
            "ai_commentary": self.ai_commentary,
        }
        return json.dumps(payload, indent=2, default=str).encode("utf-8")

    def to_html(self) -> str:
        """Generate a styled HTML report."""
        rows = "".join(
            f"<tr><td>{k}</td><td>{v}</td></tr>" for k, v in self.executive_summary()
        )
        risk_rows = "".join(
            f"<tr><td>{k}</td><td>{v}</td></tr>" for k, v in self.risk_metrics()
        )
        trade_rows = "".join(
            "<tr>" + "".join(f"<td>{c}</td>" for c in row) + "</tr>"
            for row in self.trade_rows()
        )
        commentary = (
            f"<h2>AI Commentary</h2><p>{self.ai_commentary}</p>"
            if self.ai_commentary else ""
        )
        return f"""<!DOCTYPE html>
<html><head><meta charset='utf-8'>
<title>{self.title}</title>
<style>
body {{ font-family: 'Segoe UI', Arial, sans-serif; margin: 24px; background: #0E1117; color: #FAFAFA; }}
h1 {{ color: #1F77B4; border-bottom: 2px solid #1F77B4; padding-bottom: 8px; }}
h2 {{ color: #FAFAFA; margin-top: 24px; }}
table {{ border-collapse: collapse; width: 100%; margin: 12px 0; }}
th {{ background: #1F77B4; color: white; padding: 8px 12px; text-align: left; }}
td {{ padding: 6px 12px; border-bottom: 1px solid #2D3138; }}
tr:nth-child(even) {{ background: #1A1D24; }}
.header {{ color: #888; font-size: 0.85rem; margin-bottom: 20px; }}
.metric-grid {{ display: grid; grid-template-columns: repeat(auto-fill, minmax(220px, 1fr)); gap: 12px; }}
.metric {{ background: #1A1D24; border-radius: 8px; padding: 12px; }}
.metric .label {{ color: #888; font-size: 0.8rem; }}
.metric .value {{ font-size: 1.1rem; font-weight: 600; color: #1F77B4; }}
</style></head><body>
<h1>{self.title}</h1>
<p class='header'>Generated: {self.generated_at.strftime('%Y-%m-%d %H:%M:%S')} — NEPSE Quant Engine</p>
<h2>Executive Summary</h2>
<div class='metric-grid'>
{''.join(f"<div class='metric'><div class='label'>{k}</div><div class='value'>{v}</div></div>" for k, v in self.executive_summary())}
</div>
<h2>Risk Metrics</h2>
<table><thead><tr><th>Metric</th><th>Value</th></tr></thead><tbody>{risk_rows}</tbody></table>
{commentary}
<h2>Trades ({len(self.result.trades)})</h2>
<table><thead><tr>
<th>Symbol</th><th>Side</th><th>Qty</th><th>Entry</th><th>Exit</th>
<th>Net P&L</th><th>Return %</th><th>Bars</th><th>Exit Reason</th>
</tr></thead><tbody>{trade_rows}</tbody></table>
</body></html>"""

    def to_excel(self) -> bytes:
        """Generate an Excel workbook with metrics, trades, and equity.

        Requires ``openpyxl``.  When it is not installed the report
        degrades gracefully to CSV bytes (still non-empty and
        spreadsheet-openable) so callers never crash.
        """
        output = io.BytesIO()
        try:
            with pd.ExcelWriter(output, engine="openpyxl") as writer:
                metrics_df = pd.DataFrame(
                    self.executive_summary(), columns=["Metric", "Value"]
                )
                metrics_df.to_excel(writer, sheet_name="Summary", index=False)

                risk_df = pd.DataFrame(self.risk_metrics(), columns=["Metric", "Value"])
                risk_df.to_excel(writer, sheet_name="Risk", index=False)

                trades_df = pd.DataFrame(
                    self.trade_rows(),
                    columns=["Symbol", "Side", "Qty", "Entry", "Exit",
                             "Net P&L", "Return %", "Bars", "Exit Reason"],
                )
                trades_df.to_excel(writer, sheet_name="Trades", index=False)

                pd.DataFrame(
                    {
                        "equity": self.result.equity_curve,
                        "timestamp": [str(ts) for ts in self.result.timestamps],
                    }
                ).to_excel(writer, sheet_name="Equity", index=False)

                monthly = self.monthly_returns()
                if not monthly.empty:
                    monthly.to_excel(writer, sheet_name="Monthly Returns")
        except ImportError as exc:
            logger.warning("openpyxl not installed; exporting CSV instead: %s", exc)
            return self.to_csv()
        return output.getvalue()

    def to_csv(self) -> bytes:
        """Serialize the report as CSV bytes (fallback when openpyxl is missing)."""
        import csv

        buf = io.StringIO()
        writer = csv.writer(buf)
        writer.writerow([self.title, self.generated_at.isoformat()])
        writer.writerow([])
        writer.writerow(["Metric", "Value"])
        writer.writerows(self.executive_summary())
        writer.writerow([])
        writer.writerow(["Metric", "Value"])
        writer.writerows(self.risk_metrics())
        writer.writerow([])
        writer.writerow(["Symbol", "Side", "Qty", "Entry", "Exit",
                         "Net P&L", "Return %", "Bars", "Exit Reason"])
        writer.writerows(self.trade_rows())
        return buf.getvalue().encode("utf-8")

    def to_pdf(self, filename: str | None = None) -> bytes:
        """Generate a PDF report via the ExportCenter ReportLab pipeline.

        Args:
            filename: Optional output path.

        Returns:
            PDF bytes.
        """
        from src.data.export import ExportCenter

        sections = [
            {
                "title": s["title"],
                "headers": s.get("headers"),
                "rows": s.get("rows"),
                "content": s.get("content"),
            }
            for s in self.sections()
            if s.get("headers") or s.get("content")
        ]
        return ExportCenter.to_pdf(self.title, sections, filename)


def generate_report(
    result: BacktestResult,
    title: str = "Backtest Report",
    ai_commentary: str | None = None,
) -> InstitutionalReport:
    """Convenience factory for building an :class:`InstitutionalReport`.

    Args:
        result: Completed backtest result.
        title: Report title.
        ai_commentary: Optional commentary.

    Returns:
        A ready-to-serialise report.
    """
    return InstitutionalReport(result, title=title, ai_commentary=ai_commentary)
