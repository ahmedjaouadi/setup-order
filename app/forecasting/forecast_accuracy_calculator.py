from __future__ import annotations

import math
import statistics
from collections.abc import Iterable
from typing import Any

DEFAULT_GRADES = {
    "A": {"min_direction_accuracy": 0.62, "max_mape": 0.04},
    "B": {"min_direction_accuracy": 0.57, "max_mape": 0.06},
    "C": {"min_direction_accuracy": 0.52, "max_mape": 0.08},
    "D": {"min_direction_accuracy": 0.48, "max_mape": 0.12},
}


def outcome_metrics(
    *, forecast_start: float, forecast_end: float, actual_end: float
) -> dict[str, Any]:
    forecast_direction = _direction(forecast_end - forecast_start)
    actual_direction = _direction(actual_end - forecast_start)
    error = forecast_end - actual_end
    return {
        "forecast_direction": forecast_direction,
        "actual_direction": actual_direction,
        "direction_correct": forecast_direction == actual_direction,
        "absolute_error": abs(error),
        "squared_error": error * error,
        "percentage_error": abs(error) / abs(actual_end) if actual_end else None,
    }


def aggregate_metrics(rows: Iterable[dict[str, Any]]) -> dict[str, Any]:
    items = list(rows)
    percentage_errors = [
        float(row["percentage_error"]) for row in items if row.get("percentage_error") is not None
    ]
    absolute_errors = [
        float(row["absolute_error"]) for row in items if row.get("absolute_error") is not None
    ]
    squared_errors = [
        float(row["squared_error"]) for row in items if row.get("squared_error") is not None
    ]
    correct = [
        bool(row["direction_correct"]) for row in items if row.get("direction_correct") is not None
    ]
    entry_predictions: list[bool] = []
    entry_outcomes: list[bool] = []
    stop_before_entry = []
    calibration_errors = []
    for row in items:
        entry = _number(row.get("entry_price_reference"))
        start = _number(row.get("forecast_price_start"))
        end = _number(row.get("forecast_price_end"))
        touched = row.get("entry_touched_before_horizon")
        if entry is not None and start is not None and end is not None and touched is not None:
            entry_predictions.append(_crosses(start, end, entry))
            entry_outcomes.append(bool(touched))
        if row.get("stop_touched_before_entry") is not None:
            stop_before_entry.append(bool(row["stop_touched_before_entry"]))
        confidence = _number(row.get("direction_confidence"))
        if confidence is not None and row.get("direction_correct") is not None:
            calibration_errors.append(
                abs(max(0.0, min(1.0, confidence)) - float(bool(row["direction_correct"])))
            )
    return {
        "sample_size": len(items),
        "direction_accuracy": sum(correct) / len(correct) if correct else None,
        "mae": sum(absolute_errors) / len(absolute_errors) if absolute_errors else None,
        "rmse": math.sqrt(sum(squared_errors) / len(squared_errors)) if squared_errors else None,
        "mape": sum(percentage_errors) / len(percentage_errors) if percentage_errors else None,
        "median_absolute_error": statistics.median(absolute_errors) if absolute_errors else None,
        "entry_touch_accuracy": (
            sum(predicted == actual for predicted, actual in zip(entry_predictions, entry_outcomes))
            / len(entry_predictions)
            if entry_predictions
            else None
        ),
        "stop_before_entry_error_rate": (
            sum(stop_before_entry) / len(stop_before_entry) if stop_before_entry else None
        ),
        "calibration_score": (
            1.0 - sum(calibration_errors) / len(calibration_errors) if calibration_errors else None
        ),
    }


def reliability_grade(
    metrics: dict[str, Any],
    *,
    min_samples: int = 30,
    grades: dict[str, dict[str, float]] | None = None,
) -> str:
    if int(metrics.get("sample_size") or 0) < min_samples:
        return "INSUFFICIENT_DATA"
    accuracy = float(metrics.get("direction_accuracy") or 0.0)
    mape = float(metrics.get("mape") if metrics.get("mape") is not None else math.inf)
    for grade in ("A", "B", "C", "D"):
        rule = (grades or DEFAULT_GRADES).get(grade, DEFAULT_GRADES[grade])
        if accuracy >= float(rule["min_direction_accuracy"]) and mape <= float(rule["max_mape"]):
            return grade
    return "F"


def _direction(delta: float, *, epsilon: float = 1e-12) -> str:
    if delta > epsilon:
        return "UP"
    if delta < -epsilon:
        return "DOWN"
    return "FLAT"


def _crosses(start: float, end: float, level: float) -> bool:
    return min(start, end) <= level <= max(start, end)


def _number(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None
