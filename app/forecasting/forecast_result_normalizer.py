from __future__ import annotations

from dataclasses import asdict, is_dataclass
from typing import Any

from app.forecasting.base_forecaster import NormalizedForecastResult
from app.models import utc_now_iso


def normalize_forecast_result(
    value: Any, *, model_name: str, symbol: str, timeframe: str, horizon_bars: int
) -> NormalizedForecastResult:
    if isinstance(value, NormalizedForecastResult):
        return value
    raw = asdict(value) if is_dataclass(value) else value if isinstance(value, dict) else {}
    point = _numbers(raw.get("point_forecast") or raw.get("q50_path"))
    quantiles = raw.get("quantiles") if isinstance(raw.get("quantiles"), dict) else None
    if quantiles is None and point:
        quantiles = {"0.50": point}
    direction = str(raw.get("direction") or _direction(point)).upper()
    return NormalizedForecastResult(
        model_name=str(model_name).lower().replace("-", "_"),
        symbol=str(symbol).upper(), timeframe=str(timeframe), horizon_bars=int(horizon_bars),
        generated_at=str(raw.get("generated_at") or utc_now_iso()),
        status=str(raw.get("status") or ("OK" if point else "LOAD_ERROR")),
        point_forecast=point or None, quantiles=quantiles,
        prediction_intervals=raw.get("prediction_intervals"), direction=direction,
        direction_confidence=_number(raw.get("direction_confidence")),
        expected_return_pct=_number(raw.get("expected_return_pct")),
        prob_touch_entry=_number(raw.get("prob_touch_entry")),
        prob_touch_stop_before_entry=_number(raw.get("prob_touch_stop_before_entry")),
        warnings=list(raw.get("warnings") or []), raw_output_ref=raw.get("raw_output_ref"),
    )


def _numbers(value: Any) -> list[float]:
    return [number for item in value for number in [_number(item)] if number is not None] if isinstance(value, list) else []


def _number(value: Any) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _direction(point: list[float]) -> str:
    if len(point) < 2 or point[-1] == point[0]:
        return "FLAT"
    return "UP" if point[-1] > point[0] else "DOWN"
