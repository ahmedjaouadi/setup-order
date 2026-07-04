from __future__ import annotations

from app.reports.report_models import DailyReport


class MarkdownReportRenderer:
    def render(self, report: DailyReport) -> str:
        lines = [
            f"# Daily Report {report.report_date}",
            "",
            f"Generated at: {report.generated_at}",
            "",
            "## Top Opportunities",
            *_bullets(report.top_opportunities, "symbol", "score"),
            "",
            "## Blocked Setups",
            *_bullets(report.blocked_setups, "symbol", "status"),
            "",
            "## Forecast Summary",
            f"- Forecasts: {report.forecast_summary.get('count', 0)}",
            f"- Bullish: {report.forecast_summary.get('bullish', 0)}",
            "",
            "## Risk",
            f"- Status: {report.risk.get('risk_status', 'UNKNOWN')}",
            f"- Exposure: {report.risk.get('total_exposure_usd', 0)}",
            "",
            "## Next Actions",
            *[f"- {item}" for item in report.next_actions],
        ]
        return "\n".join(lines).strip() + "\n"


def _bullets(rows: list[dict], *keys: str) -> list[str]:
    if not rows:
        return ["- None"]
    return ["- " + " | ".join(str(row.get(key, "-")) for key in keys) for row in rows[:10]]
