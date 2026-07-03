from __future__ import annotations

from typing import Any

from app.forecasting.forecast_repository import ForecastRepository
from app.storage.repositories import TradingRepository


class OpportunityExplainer:
    def __init__(
        self,
        repository: TradingRepository,
        forecast_repository: ForecastRepository | None = None,
    ) -> None:
        self.repository = repository
        self.forecast_repository = forecast_repository

    def explain(self, opportunity_id: str) -> dict[str, Any]:
        opportunity = self.repository.get_opportunity(opportunity_id)
        if opportunity is None:
            raise KeyError(opportunity_id)
        payload = opportunity.get("payload") if isinstance(opportunity.get("payload"), dict) else {}
        score = payload.get("score") if isinstance(payload.get("score"), dict) else {}
        selection = payload.get("selection") if isinstance(payload.get("selection"), dict) else {}
        liquidity = payload.get("liquidity_filter") if isinstance(payload.get("liquidity_filter"), dict) else {}
        symbol = str(opportunity.get("symbol") or "").upper()
        forecast = None
        if self.forecast_repository is not None and symbol:
            row = self.forecast_repository.latest_forecast(symbol)
            forecast = row.get("forecast") if row else None
        traces = self.repository.list_decision_traces(
            opportunity_id=opportunity_id,
            limit=20,
        )
        return {
            "opportunity_id": opportunity_id,
            "symbol": symbol,
            "status": opportunity.get("status"),
            "score": opportunity.get("score"),
            "features": {
                "selection_inputs": selection.get("inputs", {}),
                "market_snapshot": payload.get("market_snapshot", {}),
                "liquidity_filter": liquidity,
            },
            "scores": {
                "overall": score.get("overall_score", opportunity.get("score")),
                "components": score.get("components", {}),
                "explanations": score.get("explanations", []),
            },
            "forecast": forecast,
            "blocking_reasons": liquidity.get("issues", []),
            "decision_traces": traces,
            "human_summary": self._human_summary(opportunity, liquidity, forecast),
        }

    @staticmethod
    def _human_summary(
        opportunity: dict[str, Any],
        liquidity: dict[str, Any],
        forecast: dict[str, Any] | None,
    ) -> str:
        symbol = opportunity.get("symbol") or "This symbol"
        if liquidity.get("blocked"):
            return f"{symbol} is blocked by data or liquidity filters."
        if forecast:
            return f"{symbol} is shortlisted with forecast status {forecast.get('forecast_status', 'UNKNOWN')}."
        return f"{symbol} is shortlisted from scanner/setup evidence; no cached forecast is attached."
