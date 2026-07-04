from __future__ import annotations

from typing import Any

EXPERIMENTAL_MODELS = {"moirai", "uni2ts", "moirai_uni2ts"}


class ForecastStackConsensus:
    """Combines model opinions for scoring and display, never for execution."""

    def evaluate(
        self,
        forecasts: list[dict[str, Any]],
        *,
        reliability: dict[str, dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        reliability = reliability or {}
        members = [self._member(item, reliability) for item in forecasts]
        usable = [item for item in members if item["status"] == "OK"]
        directions = [item["direction"] for item in usable if item["direction"] in {"UP", "DOWN"}]
        if not directions:
            consensus = "NO_CONSENSUS"
        elif all(direction == directions[0] for direction in directions):
            consensus = directions[0]
        else:
            consensus = "MIXED"

        by_name = {item["model_name"]: item for item in members}
        warnings: list[str] = []
        impact = 0.0
        timesfm = by_name.get("timesfm")
        chronos = by_name.get("chronos")
        if timesfm and chronos and timesfm["status"] == chronos["status"] == "OK":
            if timesfm["direction"] == chronos["direction"] and timesfm["direction"] in {
                "UP",
                "DOWN",
            }:
                impact += 6.0 * min(timesfm["score_factor"], chronos["score_factor"])
            elif timesfm["direction"] != chronos["direction"]:
                warnings.append("TIMESFM_CHRONOS_DIVERGENCE")
                warnings.append("MODEL_DIVERGENCE")
                impact -= 2.0

        lag_llama = by_name.get("lag_llama")
        if lag_llama and lag_llama["status"] == "OK":
            stop_probability = _number(lag_llama.get("prob_touch_stop_before_entry"))
            if stop_probability is not None and stop_probability >= 0.5:
                impact -= min(8.0, stop_probability * 10.0)
                warnings.append("HIGH_STOP_BEFORE_ENTRY_PROBABILITY")

        for item in usable:
            if item["model_name"] in EXPERIMENTAL_MODELS:
                continue
            if item["score_factor"] <= 0:
                if item["direction"] in {"UP", "DOWN"}:
                    warnings.append(f"{item['model_name'].upper()}_INSUFFICIENT_ACCURACY_HISTORY")
                continue
            expected_return = _number(item.get("expected_return_pct"))
            if expected_return is not None:
                impact += max(-2.0, min(2.0, expected_return)) * item["score_factor"]

        impact = round(max(-12.0, min(12.0, impact)), 2)
        execution_block_reasons = ["FORECAST_STACK_ADVISORY_ONLY"]
        if consensus == "MIXED":
            execution_block_reasons.append("MIXED_CONSENSUS")
        if any(
            item["reliability_status"]
            in {"ACCURACY_HISTORY_WARMUP", "INSUFFICIENT_ACCURACY_HISTORY"}
            for item in usable
        ):
            execution_block_reasons.append("INSUFFICIENT_ACCURACY_HISTORY")
        return {
            "consensus": consensus,
            "score_impact": impact,
            "warnings": sorted(set(warnings)),
            "members": members,
            "successful_model_count": len(usable),
            "model_count": len(members),
            "used_for_execution": False,
            "forecast_available_for_display": bool(usable),
            "forecast_used_for_score": bool(usable),
            "forecast_required_for_entry": False,
            "forecast_eligible_for_execution": False,
            "forecast_execution_policy": "ADVISORY_ONLY",
            "forecast_execution_block_reasons": sorted(set(execution_block_reasons)),
            "execution_impact": (
                "WARNING_ONLY" if consensus == "MIXED" else "ADVISORY_ONLY" if usable else "NONE"
            ),
            "decision_impact": "SCORING_ONLY" if usable else "NONE",
        }

    @staticmethod
    def _member(
        forecast: dict[str, Any],
        reliability: dict[str, dict[str, Any]],
    ) -> dict[str, Any]:
        name = _model_name(forecast.get("model_name", forecast.get("model")))
        status = str(forecast.get("status") or "UNKNOWN").upper()
        is_ok = status == "OK"
        scorecard = reliability.get(name, {})
        sample_size = int(scorecard.get("sample_size") or 0)
        grade = str(
            scorecard.get("reliability_grade")
            or ("WARMUP" if sample_size <= 0 else "INSUFFICIENT_DATA")
        )
        if is_ok:
            reliability_status = (
                "ACCURACY_HISTORY_WARMUP"
                if sample_size <= 0
                else (
                    "INSUFFICIENT_ACCURACY_HISTORY"
                    if sample_size < 30
                    else (
                        "OK_CALIBRATED" if grade in {"A", "B", "C", "D", "F"} else "OK_UNCALIBRATED"
                    )
                )
            )
        else:
            grade = "NOT_APPLICABLE"
            reliability_status = "NOT_APPLICABLE"
        direction = str(
            forecast.get("direction")
            or forecast.get("forecast_slope")
            or _direction_from_return(forecast.get("forecast_expected_return_pct"))
        ).upper()
        return {
            "model_name": name,
            "status": status,
            "direction": direction if is_ok else "",
            "direction_confidence": (
                _number(forecast.get("direction_confidence")) if is_ok else None
            ),
            "expected_return_pct": (
                _number(
                    forecast.get(
                        "expected_return_pct", forecast.get("forecast_expected_return_pct")
                    )
                )
                if is_ok
                else None
            ),
            "prob_touch_entry": _number(forecast.get("prob_touch_entry")) if is_ok else None,
            "prob_touch_stop_before_entry": (
                _number(forecast.get("prob_touch_stop_before_entry")) if is_ok else None
            ),
            "uncertainty_width_pct": (
                _number(forecast.get("uncertainty_width_pct")) if is_ok else None
            ),
            "reliability_grade": grade,
            "reliability_status": reliability_status,
            "sample_size": sample_size,
            "accuracy_samples": sample_size,
            "min_accuracy_samples_required": 30,
            "eligible_for_display": is_ok,
            "eligible_for_execution": False,
            "execution_block_reason": _member_execution_block_reason(status, reliability_status),
            "score_factor": (
                1.0 if is_ok and grade in {"A", "B"} else 0.35 if is_ok and grade == "C" else 0.0
            ),
            "experimental": name in EXPERIMENTAL_MODELS,
        }


def _model_name(value: Any) -> str:
    name = str(value or "unknown").lower().replace("-", "_")
    return "timesfm" if name.startswith("timesfm") else name


def _direction_from_return(value: Any) -> str:
    number = _number(value)
    if number is None or number == 0:
        return "FLAT"
    return "UP" if number > 0 else "DOWN"


def _number(value: Any) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _member_execution_block_reason(status: str, reliability_status: str) -> str:
    if status == "OK":
        return "FORECAST_STACK_ADVISORY_ONLY"
    return {
        "MISSING_DEPENDENCY": "DEPENDENCY_MISSING",
        "WORKER_UNREACHABLE": "WORKER_NOT_RUNNING",
        "LOAD_ERROR": "MODEL_LOAD_FAILED",
        "WORKER_ERROR": "FORECAST_FAILED",
        "INSUFFICIENT_DATA": "INSUFFICIENT_HISTORY_FOR_MODEL",
    }.get(status, status or reliability_status or "FORECAST_FAILED")
