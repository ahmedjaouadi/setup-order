from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any

from app.forecasting.forecast_accuracy_calculator import (
    aggregate_metrics,
    outcome_metrics,
    reliability_grade,
)
from app.forecasting.forecast_accuracy_repository import ForecastAccuracyRepository
from app.models import utc_now_iso
from app.utils.id_generator import new_id


class ForecastAccuracyService:
    """Evaluates forecasts after their horizon; it has no broker dependency."""

    def __init__(self, repository: ForecastAccuracyRepository, settings: dict[str, Any] | None = None) -> None:
        self.repository = repository
        raw = (settings or {}).get("forecast_accuracy", {})
        self.min_samples = int(raw.get("min_required_samples", 30))
        self.grades = raw.get("grades") if isinstance(raw.get("grades"), dict) else None

    def register(self, forecast_id: int, forecast: dict[str, Any]) -> str | None:
        if forecast.get("status") != "OK":
            return None
        start = _number(forecast.get("current_price"))
        end = _number(forecast.get("forecast_last_price"))
        if start is None or end is None:
            return None
        generated = _parse_datetime(forecast.get("generated_at"))
        timeframe = str(forecast.get("timeframe") or "15m")
        horizon = int(forecast.get("horizon_bars") or 1)
        due = generated + _bar_duration(timeframe) * horizon
        delta = end - start
        outcome = {
            "outcome_id": new_id("fco"),
            "forecast_id": forecast_id,
            "model_name": _model_name(forecast.get("model")),
            "model_version": forecast.get("model_version") or forecast.get("model"),
            "symbol": str(forecast.get("symbol") or "").upper(),
            "timeframe": timeframe,
            "horizon_bars": horizon,
            "generated_at": generated.isoformat(),
            "evaluation_due_at": due.isoformat(),
            "forecast_target_time": due.isoformat(),
            "forecast_price_start": start,
            "forecast_price_end": end,
            "forecast_direction": "UP" if delta > 0 else "DOWN" if delta < 0 else "FLAT",
            "predicted_return_pct": ((end - start) / start * 100) if start else None,
            "direction_confidence": _confidence(forecast.get("direction_confidence", forecast.get("confidence"))),
            "entry_price_reference": _number(forecast.get("entry_trigger_reference")),
            "stop_price_reference": _number(forecast.get("stop_level_reference")),
            "status": "PENDING",
            "events": ["FORECAST_CREATED"],
        }
        return self.repository.create_outcome(outcome)

    def evaluate_due(
        self,
        prices: dict[str, float] | None = None,
        *,
        evaluated_at: str | None = None,
    ) -> dict[str, Any]:
        now = _parse_datetime(evaluated_at)
        observations = {
            str(key).upper(): _observation(value)
            for key, value in (prices or {}).items()
        }
        evaluated: list[dict[str, Any]] = []
        skipped: list[str] = []
        for row in self.repository.due_outcomes(now.isoformat()):
            observation = observations.get(str(row["symbol"]).upper())
            if observation is None or observation["price"] is None:
                skipped.append(row["outcome_id"])
                continue
            actual = float(observation["price"])
            metrics = outcome_metrics(
                forecast_start=float(row["forecast_price_start"]),
                forecast_end=float(row["forecast_price_end"]),
                actual_end=actual,
            )
            touches = _touch_metrics(row, observation)
            actual_return = (
                (actual - float(row["forecast_price_start"]))
                / float(row["forecast_price_start"])
                * 100
                if row.get("forecast_price_start") else None
            )
            result = {
                **row,
                **metrics,
                **touches,
                "actual_price_end": actual,
                "actual_return_pct": actual_return,
                "signed_error": float(row["forecast_price_end"]) - actual,
                "quality_bucket": _quality_bucket(metrics),
                "evaluated_at": now.isoformat(),
                "status": "EVALUATED",
                "events": [*row.get("payload", {}).get("events", ["FORECAST_CREATED"]), "FORECAST_OUTCOME_READY", "FORECAST_EVALUATED"],
            }
            self.repository.update_outcome(row["outcome_id"], result)
            evaluated.append(result)
        return {"evaluated": evaluated, "evaluated_count": len(evaluated), "skipped_without_price": skipped}

    def rebuild_scorecards(self) -> list[dict[str, Any]]:
        evaluated = self.repository.outcomes(status="EVALUATED", limit=100000)
        groups: dict[tuple[str, str, str, int], list[dict[str, Any]]] = defaultdict(list)
        for row in evaluated:
            key = (row["model_name"], row["symbol"], row["timeframe"], int(row["horizon_bars"]))
            groups[key].append(row)
        now = utc_now_iso()
        scorecards: list[dict[str, Any]] = []
        for (model, symbol, timeframe, horizon), rows in groups.items():
            metrics = aggregate_metrics(rows)
            scorecards.append({
                "scorecard_id": new_id("fsc"), "model_name": model, "symbol": symbol,
                "timeframe": timeframe, "horizon_bars": horizon, **metrics,
                "enough_data": metrics["sample_size"] >= self.min_samples,
                "min_required_samples": self.min_samples,
                "reliability_grade": reliability_grade(metrics, min_samples=self.min_samples, grades=self.grades),
                "updated_at": now,
                "computed_at": now,
                "events": ["FORECAST_SCORECARD_UPDATED"],
            })
        self.repository.replace_scorecards(scorecards)
        return scorecards

    def scorecards(self, model_name: str, *, symbol: str | None = None, timeframe: str | None = None) -> list[dict[str, Any]]:
        return self.repository.scorecards(model_name, symbol=symbol, timeframe=timeframe)

    def outcomes(
        self,
        model_name: str,
        *,
        symbol: str | None = None,
        timeframe: str | None = None,
        status: str | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        return self.repository.outcomes(
            model_name=model_name,
            symbol=symbol,
            timeframe=timeframe,
            status=status,
            limit=limit,
        )


def _parse_datetime(value: Any = None) -> datetime:
    if not value:
        return datetime.now(timezone.utc)
    text = str(value).replace("Z", "+00:00")
    parsed = datetime.fromisoformat(text)
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _bar_duration(timeframe: str) -> timedelta:
    value = timeframe.strip().lower()
    if value.endswith("m") and value[:-1].isdigit():
        return timedelta(minutes=int(value[:-1]))
    if value.endswith("h") and value[:-1].isdigit():
        return timedelta(hours=int(value[:-1]))
    if value.endswith("d") and value[:-1].isdigit():
        return timedelta(days=int(value[:-1]))
    return timedelta(minutes=15)


def _number(value: Any) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _model_name(value: Any) -> str:
    name = str(value or "unknown").strip().lower().replace("-", "_")
    return "timesfm" if name.startswith("timesfm") else name


def _confidence(value: Any) -> float | None:
    number = _number(value)
    if number is not None:
        return max(0.0, min(1.0, number / 100 if number > 1 else number))
    return {"LOW": 0.45, "MEDIUM": 0.65, "HIGH": 0.85}.get(str(value or "").upper())


def _observation(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        path = [_number(item) for item in value.get("path", [])] if isinstance(value.get("path"), list) else []
        clean_path = [item for item in path if item is not None]
        price = _number(value.get("price", value.get("close")))
        if price is None and clean_path:
            price = clean_path[-1]
        return {
            "price": price,
            "high": _number(value.get("high")),
            "low": _number(value.get("low")),
            "path": clean_path,
        }
    return {"price": _number(value), "high": None, "low": None, "path": []}


def _touch_metrics(row: dict[str, Any], observation: dict[str, Any]) -> dict[str, Any]:
    start = _number(row.get("forecast_price_start"))
    entry = _number(row.get("entry_price_reference"))
    stop = _number(row.get("stop_price_reference"))
    path = list(observation.get("path") or [])
    if not path:
        extrema = [observation.get("low"), observation.get("high"), observation.get("price")]
        path = [float(value) for value in extrema if value is not None]

    def touched(level: float | None) -> bool | None:
        if level is None or not path:
            return None
        return min(path) <= level <= max(path)

    entry_touched = touched(entry)
    stop_touched = touched(stop)
    stop_before_entry: bool | None = None
    if entry is not None and stop is not None and path:
        entry_index = next((index for index, price in enumerate(path) if _level_reached(start, entry, price)), None)
        stop_index = next((index for index, price in enumerate(path) if _level_reached(start, stop, price)), None)
        if stop_index is not None:
            stop_before_entry = entry_index is None or stop_index < entry_index
        elif entry_index is not None:
            stop_before_entry = False
    return {
        "entry_touched_before_horizon": entry_touched,
        "stop_touched_before_horizon": stop_touched,
        "stop_touched_before_entry": stop_before_entry,
    }


def _level_reached(start: float | None, level: float, price: float) -> bool:
    if start is None:
        return price == level
    return price >= level if level >= start else price <= level


def _quality_bucket(metrics: dict[str, Any]) -> str:
    percentage_error = _number(metrics.get("percentage_error"))
    if metrics.get("direction_correct") and percentage_error is not None and percentage_error <= 0.03:
        return "EXCELLENT"
    if metrics.get("direction_correct") and percentage_error is not None and percentage_error <= 0.08:
        return "GOOD"
    if metrics.get("direction_correct"):
        return "DIRECTION_ONLY"
    return "POOR"
