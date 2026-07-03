from __future__ import annotations

from typing import Any

from app.forecasting.forecast_service import ForecastService


class ForecastOperationalService:
    """Operational wrapper that keeps forecasts explicit and non-executing."""

    def __init__(self, forecast_service: ForecastService) -> None:
        self.forecast_service = forecast_service

    async def run_for_setup(
        self,
        setup: dict[str, Any],
        *,
        force_refresh: bool = False,
        cached_only: bool = False,
        model_name: str | None = None,
    ) -> dict[str, Any]:
        symbol = str(setup.get("symbol") or "").upper()
        setup_id = str(setup.get("setup_id") or "")
        config = setup.get("config") if isinstance(setup.get("config"), dict) else {}
        timeframe = _nested(config, "timeframes", "signal") or config.get("timeframe")
        result = await self.forecast_service.forecast(
            symbol,
            timeframe=timeframe,
            setup_id=setup_id or None,
            force_refresh=force_refresh,
            cached_only=cached_only,
            model_name=model_name,
        )
        result["used_for_decision"] = False
        result["decision_impact"] = "SCORING_ONLY" if result.get("status") == "OK" else "NONE"
        result["execution_allowed"] = False
        return result


def _nested(payload: dict[str, Any], *path: str) -> Any:
    current: Any = payload
    for key in path:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current
