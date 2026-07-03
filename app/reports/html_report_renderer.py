from __future__ import annotations

from html import escape

from app.reports.report_models import DailyReport


class HtmlReportRenderer:
    def render(self, report: DailyReport) -> str:
        opportunity_items = "".join(
            f"<li>{escape(str(item.get('symbol', '-')))}: {escape(str(item.get('score', '-')))}</li>"
            for item in report.top_opportunities[:10]
        ) or "<li>None</li>"
        action_items = "".join(
            f"<li>{escape(action)}</li>" for action in report.next_actions
        ) or "<li>None</li>"
        return (
            "<article>"
            f"<h1>Daily Report {escape(report.report_date)}</h1>"
            f"<p>Generated at: {escape(report.generated_at)}</p>"
            "<h2>Top Opportunities</h2>"
            f"<ul>{opportunity_items}</ul>"
            "<h2>Next Actions</h2>"
            f"<ul>{action_items}</ul>"
            "</article>"
        )
