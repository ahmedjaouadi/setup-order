from __future__ import annotations

from typing import Any


def forecast_confidence(
    result: dict[str, Any], scorecard: dict[str, Any] | None = None
) -> dict[str, Any]:
    scorecard = scorecard or {}
    samples = int(scorecard.get("sample_size") or 0)
    grade = str(scorecard.get("reliability_grade") or "INSUFFICIENT_DATA")
    raw = _number(result.get("direction_confidence"))
    factor = {"A": 1.0, "B": 0.85, "C": 0.6, "D": 0.3, "F": 0.0}.get(grade, 0.0)
    calibrated = None if raw is None else round(max(0.0, min(1.0, raw)) * factor, 4)
    return {
        "raw_confidence": raw,
        "calibrated_confidence": calibrated,
        "reliability_grade": grade,
        "sample_size": samples,
        "eligible_for_strong_boost": grade in {"A", "B"} and samples >= 30,
    }


def _number(value: Any) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None
