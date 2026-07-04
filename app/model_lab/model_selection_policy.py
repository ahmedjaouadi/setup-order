from __future__ import annotations

from typing import Any

EXPERIMENTAL_MODELS = {"moirai", "uni2ts", "moirai_uni2ts"}


class ForecastModelSelectionPolicy:
    """Applies the V2.3 baseline and safety gates to one comparison slice."""

    def select(
        self,
        results: list[dict[str, Any]],
        *,
        min_samples: int,
        experimental_scorecards: dict[str, bool] | None = None,
    ) -> dict[str, Any]:
        experimental_scorecards = experimental_scorecards or {}
        by_name = {str(item["model_name"]): item for item in results}
        naive = by_name.get("naive_baseline")
        atr = by_name.get("atr_baseline")
        eligible: list[dict[str, Any]] = []
        evaluations: dict[str, dict[str, Any]] = {}
        for item in results:
            model = str(item["model_name"])
            metrics = item.get("metrics") if isinstance(item.get("metrics"), dict) else {}
            trading = (
                item.get("trading_metrics") if isinstance(item.get("trading_metrics"), dict) else {}
            )
            enough_samples = int(metrics.get("sample_size") or 0) >= min_samples
            beats_naive = model == "naive_baseline" or _beats(metrics, naive)
            beats_atr = model in {"naive_baseline", "atr_baseline"} or _beats_any(metrics, atr)
            pnl_safe = float(trading.get("forecast_filter_pnl_delta") or 0.0) >= 0.0
            experimental_valid = model not in EXPERIMENTAL_MODELS or bool(
                experimental_scorecards.get(model)
            )
            selected_eligible = (
                model not in {"naive_baseline", "atr_baseline"}
                and enough_samples
                and beats_naive
                and beats_atr
                and pnl_safe
                and experimental_valid
            )
            evaluations[model] = {
                "enough_samples": enough_samples,
                "beats_naive_baseline": beats_naive,
                "beats_atr_baseline": beats_atr,
                "pnl_not_degraded": pnl_safe,
                "experimental_scorecard_valid": experimental_valid,
                "eligible": selected_eligible,
            }
            if selected_eligible:
                eligible.append(item)
        eligible.sort(key=_ranking_key)
        selected = str(eligible[0]["model_name"]) if eligible else None
        return {
            "selected_model": selected,
            "fallback_model": "naive_baseline" if naive else None,
            "evaluations": evaluations,
            "selection_scope": "symbol_timeframe_horizon",
        }


def _beats(metrics: dict[str, Any], baseline: dict[str, Any] | None) -> bool:
    if baseline is None:
        return False
    baseline_metrics = baseline.get("metrics") if isinstance(baseline.get("metrics"), dict) else {}
    mae = _number(metrics.get("mae"))
    baseline_mae = _number(baseline_metrics.get("mae"))
    return mae is not None and baseline_mae is not None and mae < baseline_mae


def _beats_any(metrics: dict[str, Any], baseline: dict[str, Any] | None) -> bool:
    if baseline is None:
        return False
    baseline_metrics = baseline.get("metrics") if isinstance(baseline.get("metrics"), dict) else {}
    lower_is_better = ("mae", "rmse", "mape", "calibration_error")
    higher_is_better = ("direction_accuracy", "quantile_coverage")
    for key in lower_is_better:
        current, other = _number(metrics.get(key)), _number(baseline_metrics.get(key))
        if current is not None and other is not None and current < other:
            return True
    for key in higher_is_better:
        current, other = _number(metrics.get(key)), _number(baseline_metrics.get(key))
        if current is not None and other is not None and current > other:
            return True
    return False


def _ranking_key(item: dict[str, Any]) -> tuple[float, float]:
    metrics = item.get("metrics") if isinstance(item.get("metrics"), dict) else {}
    mae = _number(metrics.get("mae"))
    accuracy = _number(metrics.get("direction_accuracy"))
    return (mae if mae is not None else float("inf"), -(accuracy if accuracy is not None else 0.0))


def _number(value: Any) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None
