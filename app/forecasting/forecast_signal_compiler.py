from __future__ import annotations

from typing import Any

from app.forecasting.forecast_repository import ForecastRepository


class ForecastSignalCompiler:
    """Builds a decision-safe signal from the latest persisted forecast."""

    def __init__(self, repository: ForecastRepository) -> None:
        self.repository = repository

    def latest_signal(
        self,
        symbol: str,
        *,
        timeframe: str | None = None,
        model_name: str | None = None,
    ) -> dict[str, Any]:
        normalized_symbol = str(symbol or "").strip().upper()
        if not normalized_symbol:
            return _empty_signal("", "INVALID_INPUT", "symbol is required")

        row = self.repository.latest_forecast(
            normalized_symbol,
            timeframe=timeframe,
            model_name=model_name,
        )
        if not row:
            return _empty_signal(
                normalized_symbol,
                "NO_CACHED_FORECAST",
                "No cached forecast is available.",
            )

        forecast = row.get("forecast") if isinstance(row.get("forecast"), dict) else {}
        metric_score = _bounded_score(forecast.get("metric_score"))
        expected_return = _number(forecast.get("forecast_expected_return_pct"))
        slope = str(forecast.get("forecast_slope") or "FLAT").upper()
        direction = _direction(expected_return, slope)
        warnings: list[str] = []
        if forecast.get("status") != "OK":
            warnings.append(str(forecast.get("error") or forecast.get("status")))
        if forecast.get("forecast_status") in {"BEARISH", "WEAK"}:
            warnings.append("Forecast is weak or bearish.")

        return {
            "symbol": normalized_symbol,
            "status": forecast.get("status") or row.get("status") or "OK",
            "model": forecast.get("model") or row.get("model_name"),
            "timeframe": forecast.get("timeframe") or row.get("timeframe"),
            "generated_at": forecast.get("generated_at") or row.get("generated_at"),
            "forecast_status": forecast.get("forecast_status"),
            "direction": direction,
            "expected_return_pct": expected_return,
            "alignment_score": metric_score,
            "metric_score": metric_score,
            "confidence": forecast.get("confidence", "LOW"),
            "q10_above_support": forecast.get("q10_above_support"),
            "q10_above_stop": forecast.get("q10_above_stop"),
            "median_above_entry_trigger": forecast.get("median_above_entry_trigger"),
            "used_for_decision": False,
            "decision_impact": "SCORING_ONLY",
            "warnings": warnings,
            "forecast": forecast,
        }


def _empty_signal(symbol: str, status: str, reason: str) -> dict[str, Any]:
    return {
        "symbol": symbol,
        "status": status,
        "reason": reason,
        "forecast_status": status,
        "direction": "UNKNOWN",
        "alignment_score": 50.0 if status == "NO_CACHED_FORECAST" else 0.0,
        "metric_score": 50.0 if status == "NO_CACHED_FORECAST" else 0.0,
        "used_for_decision": False,
        "decision_impact": "NONE",
        "warnings": [reason],
        "forecast": {},
    }


def _direction(expected_return: float | None, slope: str) -> str:
    if expected_return is not None:
        if expected_return > 0:
            return "UP"
        if expected_return < 0:
            return "DOWN"
    return slope if slope in {"UP", "DOWN", "FLAT"} else "UNKNOWN"


def _bounded_score(value: Any) -> float:
    number = _number(value)
    if number is None:
        return 50.0
    return max(0.0, min(100.0, float(number)))


def _number(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
