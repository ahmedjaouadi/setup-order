from __future__ import annotations

from typing import Any


class ForecastToScoreMapper:
    """Maps forecast signals into one bounded scoring component."""

    def component_score(
        self,
        signal: dict[str, Any],
        *,
        setup_direction: str = "long",
    ) -> dict[str, Any]:
        status = str(signal.get("status") or "")
        if status in {"NO_CACHED_FORECAST", "DISABLED", "INVALID_INPUT"}:
            return {
                "score": 50.0,
                "status": status or "NO_CACHED_FORECAST",
                "used": False,
                "warning": signal.get("reason") or "Forecast not available.",
            }

        score = _bounded(signal.get("alignment_score", signal.get("metric_score", 50.0)))
        direction = str(signal.get("direction") or "UNKNOWN").upper()
        setup_direction = str(setup_direction or "long").lower()
        if setup_direction == "long" and direction == "DOWN":
            score = min(score, 35.0)
        elif setup_direction == "short" and direction == "UP":
            score = min(score, 35.0)

        return {
            "score": score,
            "status": status or "OK",
            "used": True,
            "warning": "; ".join(signal.get("warnings") or []),
            "direction": direction,
            "forecast_status": signal.get("forecast_status"),
            "decision_impact": "SCORING_ONLY",
        }


def _bounded(value: Any) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 50.0
    return max(0.0, min(100.0, number))
