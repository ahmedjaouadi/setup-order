from __future__ import annotations

from typing import Any


class ModelDriftDetector:
    def detect(
        self,
        current: dict[str, Any],
        reference: dict[str, Any],
        *,
        accuracy_drop_threshold: float = 0.08,
        error_growth_threshold: float = 0.25,
    ) -> dict[str, Any]:
        current_accuracy = _number(current.get("direction_accuracy"))
        reference_accuracy = _number(reference.get("direction_accuracy"))
        current_mae = _number(current.get("mae"))
        reference_mae = _number(reference.get("mae"))
        accuracy_drop = (
            (reference_accuracy - current_accuracy)
            if current_accuracy is not None and reference_accuracy is not None
            else None
        )
        error_growth = (
            ((current_mae - reference_mae) / reference_mae)
            if current_mae is not None and reference_mae not in (None, 0)
            else None
        )
        reasons = []
        if accuracy_drop is not None and accuracy_drop >= accuracy_drop_threshold:
            reasons.append("DIRECTION_ACCURACY_DROP")
        if error_growth is not None and error_growth >= error_growth_threshold:
            reasons.append("MAE_GROWTH")
        return {
            "drift_detected": bool(reasons),
            "reasons": reasons,
            "accuracy_drop": accuracy_drop,
            "mae_growth_pct": error_growth,
        }


def _number(value: Any) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None
