from __future__ import annotations

from datetime import datetime, timezone
import json
from typing import Any

from app.models import utc_now_iso
from app.reports.html_report_renderer import HtmlReportRenderer
from app.reports.markdown_report_renderer import MarkdownReportRenderer
from app.reports.report_models import DailyReport
from app.storage.repositories import TradingRepository
from app.utils.id_generator import new_id


class DailyReportService:
    def __init__(self, repository: TradingRepository) -> None:
        self.repository = repository
        self.markdown = MarkdownReportRenderer()
        self.html = HtmlReportRenderer()

    def generate(self, *, report_date: str | None = None) -> dict[str, Any]:
        report_date = report_date or datetime.now(timezone.utc).date().isoformat()
        top_opportunities = self.repository.list_opportunities(limit=10)
        setups = self.repository.list_setups()
        armed_setups = [
            setup
            for setup in setups
            if setup.get("enabled") and setup.get("status") not in {"DISABLED", "DRAFT"}
        ]
        blocked_setups = [
            setup
            for setup in setups
            if setup.get("status")
            in {"MANUAL_REVIEW_REQUIRED", "ERROR_REQUIRES_MANUAL_REVIEW", "ERROR"}
        ]
        forecasts = self._recent_forecasts()
        report = DailyReport(
            report_id=new_id("daily"),
            report_date=report_date,
            generated_at=utc_now_iso(),
            top_opportunities=top_opportunities,
            armed_setups=armed_setups,
            blocked_setups=blocked_setups,
            forecast_summary={
                "count": len(forecasts),
                "bullish": len(
                    [
                        item
                        for item in forecasts
                        if item.get("forecast_status") in {"BULLISH", "NEUTRAL_BULLISH"}
                    ]
                ),
                "items": forecasts,
            },
            backtests=self.repository.list_backtest_runs(limit=10),
            trades=[],
            risk=self.repository.latest_portfolio_snapshot() or {},
            errors=self.repository.list_events(level="ERROR", limit=25),
            next_actions=self._next_actions(top_opportunities, blocked_setups),
        )
        payload = {
            "report_id": report.report_id,
            "report_date": report.report_date,
            "report": {
                "generated_at": report.generated_at,
                "top_opportunities": report.top_opportunities,
                "armed_setups": report.armed_setups,
                "blocked_setups": report.blocked_setups,
                "forecast_summary": report.forecast_summary,
                "backtests": report.backtests,
                "trades": report.trades,
                "risk": report.risk,
                "errors": report.errors,
                "next_actions": report.next_actions,
            },
            "markdown": self.markdown.render(report),
            "html": self.html.render(report),
            "created_at": report.generated_at,
        }
        self.repository.add_daily_report(payload)
        return payload

    def latest(self) -> dict[str, Any] | None:
        return self.repository.latest_daily_report()

    def get(self, report_date: str) -> dict[str, Any] | None:
        return self.repository.get_daily_report(report_date)

    def _recent_forecasts(self) -> list[dict[str, Any]]:
        rows = self.repository.database.execute(
            """
            SELECT forecast_payload_json
            FROM forecast_metrics
            ORDER BY generated_at DESC, id DESC
            LIMIT 50
            """
        ).fetchall()
        forecasts = []
        for row in rows:
            try:
                forecasts.append(json.loads(row["forecast_payload_json"] or "{}"))
            except json.JSONDecodeError:
                continue
        return forecasts

    @staticmethod
    def _next_actions(
        top_opportunities: list[dict[str, Any]],
        blocked_setups: list[dict[str, Any]],
    ) -> list[str]:
        actions = []
        if top_opportunities:
            actions.append("Review top shortlisted opportunities and generate scenario drafts.")
        if blocked_setups:
            actions.append("Resolve blocked setups before arming or executing anything.")
        if not actions:
            actions.append("No urgent action detected.")
        return actions
