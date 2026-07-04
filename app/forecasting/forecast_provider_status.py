from __future__ import annotations

from typing import Any

from app.forecasting.forecast_stack_config import ForecastStackConfig
from app.forecasting.provider_statuses import (
    AVAILABLE,
    DISABLED_BY_CONFIG,
    EXTERNAL_WORKER_CONFIGURED,
    EXTERNAL_WORKER_OK,
    LOAD_ERROR,
    MISSING_DEPENDENCY,
    WORKER_ERROR,
    WORKER_NOT_CONFIGURED,
    WORKER_UNREACHABLE,
    is_available_status,
    status_from_reason,
)


class ForecastProviderStatusService:
    STATUSES = {
        AVAILABLE,
        "AVAILABLE",
        EXTERNAL_WORKER_CONFIGURED,
        EXTERNAL_WORKER_OK,
        MISSING_DEPENDENCY,
        DISABLED_BY_CONFIG,
        WORKER_NOT_CONFIGURED,
        WORKER_UNREACHABLE,
        WORKER_ERROR,
        LOAD_ERROR,
    }

    def __init__(self, settings: dict[str, Any], forecast_service: Any) -> None:
        self.config = ForecastStackConfig.from_settings(settings)
        self.forecast_service = forecast_service

    def list(self) -> dict[str, Any]:
        availability = self.forecast_service.models()
        discovered = [
            availability.get("timesfm", {}),
            *availability.get("external_models", []),
            *availability.get("baselines", []),
        ]
        by_name = {str(item.get("model")): item for item in discovered}
        items = []
        for name, provider in sorted(
            self.config.providers.items(), key=lambda item: item[1].priority
        ):
            runtime = by_name.get(name, {})
            runtime_status = str(runtime.get("status") or "")
            if runtime_status == "AVAILABLE":
                runtime_status = AVAILABLE
            reason = str(runtime.get("reason") or "Provider not registered")
            runtime_mode = str(
                runtime.get("runtime_mode")
                or runtime.get("execution_mode")
                or provider.options.get("runtime_mode")
                or "in_process"
            )
            installation_managed = provider.enabled or provider.auto_enable_when_ready
            if runtime_status not in self.STATUSES:
                runtime_status = (
                    AVAILABLE if runtime.get("available") else status_from_reason(reason)
                )
            status = runtime_status if installation_managed else DISABLED_BY_CONFIG
            installed = runtime_status != MISSING_DEPENDENCY
            available = is_available_status(status)
            persisted_name_fn = getattr(self.forecast_service, "_persisted_model_name", None)
            persisted_name = persisted_name_fn(name) if callable(persisted_name_fn) else name
            forecast_repository = getattr(self.forecast_service, "repository", None)
            latest = (
                forecast_repository.latest_for_model(persisted_name)
                if forecast_repository is not None
                and hasattr(forecast_repository, "latest_for_model")
                else None
            )
            latest_payload = _latest_payload(latest)
            accuracy_service = getattr(self.forecast_service, "accuracy_service", None)
            scorecards = accuracy_service.scorecards(name) if accuracy_service is not None else []
            best_scorecard = scorecards[0] if scorecards else {}
            sample_size = int(best_scorecard.get("sample_size") or 0)
            historical_grade = str(best_scorecard.get("reliability_grade") or "")
            worker_status = _worker_status(
                status=status,
                runtime_mode=runtime_mode,
                baseline=bool(runtime.get("baseline")),
            )
            dependency_status = _dependency_status(status, installed)
            forecast_status = _forecast_status(latest, available)
            historical_accuracy_status = _historical_accuracy_status(
                available=available,
                forecast_status=forecast_status,
                sample_size=sample_size,
                min_samples=self.config.min_accuracy_samples_for_boost,
                historical_grade=historical_grade,
            )
            forecast_summary = _forecast_summary(latest)
            eligible_for_runtime_forecast = provider.use_for_runtime_forecast and not bool(
                runtime.get("model_lab_only")
            )
            eligible_for_display = available and forecast_status in {"FORECAST_OK", "NOT_RUN"}
            eligible_for_execution = False
            execution_block_reason = _execution_block_reason(
                status=status,
                available=available,
                reason=reason,
                forecast_status=forecast_status,
                latest=latest,
                eligible_for_runtime_forecast=eligible_for_runtime_forecast,
            )
            last_error = _last_error(
                latest=latest,
                forecast_status=forecast_status,
                fallback_reason=execution_block_reason,
            )
            action_required = _action_required(status, reason)
            items.append(
                {
                    "model_name": name,
                    "role": provider.role,
                    "priority": provider.priority,
                    "status": status,
                    "installed": installed,
                    "available": available,
                    "configured": status != DISABLED_BY_CONFIG,
                    "enabled": provider.enabled or provider.auto_enable_when_ready,
                    "worker_status": worker_status,
                    "dependency_status": dependency_status,
                    "input_data_status": "INPUT_DATA_READY" if available else "NOT_APPLICABLE",
                    "forecast_status": forecast_status,
                    "current_run_status": forecast_status,
                    "historical_accuracy_status": historical_accuracy_status,
                    "reliability_status": historical_accuracy_status,
                    "accuracy_samples": sample_size,
                    "min_accuracy_samples_required": self.config.min_accuracy_samples_for_boost,
                    "eligible_for_display": eligible_for_display,
                    "eligible_for_execution": eligible_for_execution,
                    "execution_block_reason": execution_block_reason,
                    "action_required": action_required,
                    "unavailable_reason": (
                        None
                        if available
                        else (
                            reason
                            if status != DISABLED_BY_CONFIG
                            else "Disabled by forecast_stack configuration"
                        )
                    ),
                    "activation_mode": (
                        "AUTO_WHEN_READY" if provider.auto_enable_when_ready else "MANUAL"
                    ),
                    "runtime_mode": runtime_mode,
                    "use_for_scoring": provider.use_for_setup_score,
                    "use_for_execution": False,
                    "use_for_model_lab": provider.use_for_model_lab,
                    "last_run": latest.get("generated_at") if latest else None,
                    "last_error": last_error,
                    "direction": forecast_summary["direction"],
                    "confidence": forecast_summary["confidence"],
                    "confidence_display": forecast_summary["confidence_display"],
                    "direction_confidence": forecast_summary["direction_confidence"],
                    "expected_move_pct": forecast_summary["expected_move_pct"],
                    "uncertainty_width_pct": forecast_summary["uncertainty_width_pct"],
                    "forecast_horizon": forecast_summary["forecast_horizon"],
                    "q10_end_price": forecast_summary["q10_end_price"],
                    "q90_end_price": forecast_summary["q90_end_price"],
                    "reliability_grade": _gui_reliability_label(
                        historical_accuracy_status, historical_grade
                    ),
                    "historical_reliability_grade": historical_grade or None,
                    "sample_size": sample_size,
                    "historical_samples": sample_size,
                    "latest_forecast_payload": latest_payload or None,
                }
            )
        return {
            "enabled": self.config.enabled,
            "execution_mode": "scoring_only",
            "primary_model": self.config.primary_model,
            "providers": items,
            "model_groups": {
                "active": list(self.config.active_models),
                "comparison": list(self.config.comparison_models),
                "advanced": list(self.config.advanced_models),
                "experimental": list(self.config.experimental_models),
            },
            "horizons": {key: list(value) for key, value in self.config.horizons.items()},
            "safety": {
                "block_order_from_forecast": True,
                "use_forecast_for_scoring_only": True,
                "require_accuracy_history_before_score_boost": self.config.require_accuracy_history_before_score_boost,
                "min_accuracy_samples_for_boost": self.config.min_accuracy_samples_for_boost,
            },
        }


def _worker_status(*, status: str, runtime_mode: str, baseline: bool) -> str:
    if baseline:
        return "BUILTIN_READY"
    if is_available_status(status):
        return "WORKER_READY" if "worker" in runtime_mode else "WORKER_READY"
    if status == MISSING_DEPENDENCY:
        return "DEPENDENCY_MISSING"
    if status == WORKER_NOT_CONFIGURED:
        return "WORKER_NOT_CONFIGURED"
    if status == WORKER_UNREACHABLE:
        return "WORKER_NOT_RUNNING"
    if status in {WORKER_ERROR, LOAD_ERROR}:
        return "WORKER_ERROR"
    if status == DISABLED_BY_CONFIG:
        return "DISABLED_BY_CONFIG"
    return "WORKER_NOT_CONFIGURED"


def _dependency_status(status: str, installed: bool) -> str:
    if status == MISSING_DEPENDENCY or not installed:
        return "DEPENDENCY_MISSING"
    if status == WORKER_NOT_CONFIGURED:
        return "DEPENDENCY_MISSING"
    if status == LOAD_ERROR:
        return "DEPENDENCY_VERSION_ERROR"
    if status == DISABLED_BY_CONFIG:
        return "NOT_APPLICABLE"
    return "DEPENDENCIES_OK"


def _forecast_status(latest: dict[str, Any] | None, available: bool) -> str:
    if not available:
        return "NOT_RUN"
    if not latest:
        return "NOT_RUN"
    latest_status = str(latest.get("status") or "").upper()
    if latest_status == "OK":
        return "FORECAST_OK"
    if latest_status in {"TIMEOUT", "FORECAST_TIMEOUT"}:
        return "FORECAST_TIMEOUT"
    if latest_status in {"EMPTY", "FORECAST_EMPTY_OUTPUT"}:
        return "FORECAST_EMPTY_OUTPUT"
    return "FORECAST_ERROR"


def _historical_accuracy_status(
    *,
    available: bool,
    forecast_status: str,
    sample_size: int,
    min_samples: int,
    historical_grade: str,
) -> str:
    if sample_size <= 0:
        if not available and forecast_status == "NOT_RUN":
            return "NOT_APPLICABLE"
        return "ACCURACY_HISTORY_WARMUP"
    if sample_size < min_samples:
        return "INSUFFICIENT_ACCURACY_HISTORY"
    if historical_grade in {"A", "B", "C", "D", "F"}:
        return "OK_CALIBRATED"
    return "OK_UNCALIBRATED"


def _gui_reliability_label(reliability_status: str, historical_grade: str) -> str:
    if reliability_status == "ACCURACY_HISTORY_WARMUP":
        return "WARMUP"
    if reliability_status == "INSUFFICIENT_ACCURACY_HISTORY":
        return "WARMUP"
    if reliability_status == "OK_CALIBRATED":
        return historical_grade or "CALIBRATED"
    if reliability_status == "OK_UNCALIBRATED":
        return "UNCALIBRATED"
    return reliability_status


def _action_required(status: str, reason: str) -> str:
    if status == WORKER_NOT_CONFIGURED:
        return "Installer/configurer le provider ou desactiver explicitement ce modele."
    if status == MISSING_DEPENDENCY:
        return "Installer la dependance optionnelle ou laisser le modele en diagnostic."
    if status == WORKER_UNREACHABLE:
        return "Verifier le chemin du worker et relancer le diagnostic."
    if status == DISABLED_BY_CONFIG:
        return "Activer le provider dans forecast_stack.providers si necessaire."
    if status in {WORKER_ERROR, LOAD_ERROR}:
        return reason or "Consulter la derniere erreur du provider."
    return ""


def _latest_payload(latest: dict[str, Any] | None) -> dict[str, Any]:
    payload = latest.get("forecast") if latest else None
    return payload if isinstance(payload, dict) else {}


def _present(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    return True


def _pick_value(latest: dict[str, Any] | None, *keys: str) -> Any:
    if not latest:
        return None
    payload = _latest_payload(latest)
    for key in keys:
        value = payload.get(key)
        if _present(value):
            return value
        value = latest.get(key)
        if _present(value):
            return value
    return None


def _number(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _direction_from_prices(start: float | None, end: float | None) -> str | None:
    if start is None or end is None:
        return None
    delta = end - start
    if abs(delta) < 1e-9:
        return "FLAT"
    return "UP" if delta > 0 else "DOWN"


def _direction(latest: dict[str, Any] | None) -> str | None:
    explicit = str(_pick_value(latest, "direction") or "").strip().upper()
    if explicit in {"UP", "DOWN", "FLAT"}:
        return explicit
    slope = str(_pick_value(latest, "forecast_slope") or "").strip().upper()
    if slope in {"UP", "DOWN", "FLAT"}:
        return slope
    expected_move_pct = _number(
        _pick_value(
            latest, "forecast_expected_return_pct", "expected_return_pct", "median_return_pct"
        )
    )
    if expected_move_pct is not None:
        return _direction_from_prices(0.0, expected_move_pct)
    return _direction_from_prices(
        _number(_pick_value(latest, "current_price", "reference_price")),
        _number(_pick_value(latest, "forecast_last_price", "median_end_price", "q50_end_price")),
    )


def _format_percent(raw: float | None) -> str | None:
    if raw is None:
        return None
    value = raw * 100 if 0 <= raw <= 1 else raw
    return f"{value:.0f}%"


def _confidence_fields(
    latest: dict[str, Any] | None,
) -> tuple[str | None, str | None, float | None]:
    label = str(_pick_value(latest, "confidence") or "").strip().upper() or None
    numeric = _number(_pick_value(latest, "direction_confidence"))
    numeric_display = _format_percent(numeric)
    if label and numeric_display:
        return label, f"{label} ({numeric_display})", numeric
    if label:
        return label, label, numeric
    if numeric_display:
        return numeric_display, numeric_display, numeric
    return None, None, None


def _expected_move_pct(latest: dict[str, Any] | None) -> float | None:
    explicit = _number(
        _pick_value(
            latest, "forecast_expected_return_pct", "expected_return_pct", "median_return_pct"
        )
    )
    if explicit is not None:
        return explicit
    current_price = _number(_pick_value(latest, "current_price", "reference_price"))
    last_price = _number(
        _pick_value(latest, "forecast_last_price", "median_end_price", "q50_end_price")
    )
    if current_price is None or last_price is None or current_price <= 0:
        return None
    return round(((last_price - current_price) / current_price) * 100, 4)


def _uncertainty_width_pct(latest: dict[str, Any] | None) -> float | None:
    explicit = _number(_pick_value(latest, "uncertainty_width_pct"))
    if explicit is not None:
        return explicit
    current_price = _number(_pick_value(latest, "current_price", "reference_price"))
    q10_end_price = _number(_pick_value(latest, "q10_end_price"))
    q90_end_price = _number(_pick_value(latest, "q90_end_price"))
    if (
        current_price is None
        or current_price <= 0
        or q10_end_price is None
        or q90_end_price is None
    ):
        return None
    return round(abs(q90_end_price - q10_end_price) / current_price * 100, 4)


def _forecast_horizon(latest: dict[str, Any] | None) -> str | None:
    horizon = _pick_value(latest, "horizon_bars")
    timeframe = _pick_value(latest, "timeframe")
    if not _present(horizon) and not _present(timeframe):
        return None
    left = str(horizon).strip() if _present(horizon) else "?"
    right = str(timeframe).strip() if _present(timeframe) else "?"
    return f"{left} x {right}"


def _forecast_summary(latest: dict[str, Any] | None) -> dict[str, Any]:
    confidence, confidence_display, direction_confidence = _confidence_fields(latest)
    return {
        "direction": _direction(latest),
        "confidence": confidence,
        "confidence_display": confidence_display,
        "direction_confidence": direction_confidence,
        "expected_move_pct": _expected_move_pct(latest),
        "uncertainty_width_pct": _uncertainty_width_pct(latest),
        "forecast_horizon": _forecast_horizon(latest),
        "q10_end_price": _number(_pick_value(latest, "q10_end_price")),
        "q90_end_price": _number(_pick_value(latest, "q90_end_price")),
    }


def _execution_block_reason(
    *,
    status: str,
    available: bool,
    reason: str,
    forecast_status: str,
    latest: dict[str, Any] | None,
    eligible_for_runtime_forecast: bool,
) -> str:
    if not eligible_for_runtime_forecast:
        return "BENCHMARK_FRAMEWORK_ONLY"
    if not available:
        return _provider_block_reason(status, reason)
    if forecast_status == "FORECAST_OK":
        return "FORECAST_STACK_ADVISORY_ONLY"
    if forecast_status == "NOT_RUN":
        return "NOT_SELECTED_FOR_CURRENT_RUN"
    if forecast_status == "FORECAST_TIMEOUT":
        return "WORKER_TIMEOUT"
    if forecast_status == "FORECAST_EMPTY_OUTPUT":
        return "FORECAST_FAILED"
    return _forecast_failure_reason(latest)


def _provider_block_reason(status: str, reason: str) -> str:
    if status == MISSING_DEPENDENCY:
        return "DEPENDENCY_MISSING"
    if status == WORKER_NOT_CONFIGURED:
        return "WORKER_NOT_CONFIGURED"
    if status == WORKER_UNREACHABLE:
        return "WORKER_NOT_RUNNING"
    if status == DISABLED_BY_CONFIG:
        normalized = str(reason or "").lower()
        if "offline model lab" in normalized:
            return "BENCHMARK_FRAMEWORK_ONLY"
        return "DISABLED_BY_CONFIG"
    if status == LOAD_ERROR:
        return "MODEL_LOAD_FAILED"
    if status == WORKER_ERROR:
        return "FORECAST_FAILED"
    return "FORECAST_FAILED"


def _forecast_failure_reason(latest: dict[str, Any] | None) -> str:
    status = str(latest.get("status") or "").strip().upper() if latest else ""
    error_text = str(_pick_value(latest, "error") or "").lower()
    if status in {"TIMEOUT", "FORECAST_TIMEOUT"} or "timed out" in error_text:
        return "WORKER_TIMEOUT"
    if "not implemented" in error_text:
        return "FORECAST_NOT_IMPLEMENTED"
    if "unsupported" in error_text:
        return "UNSUPPORTED_INPUT_FORMAT"
    if status == "INSUFFICIENT_DATA" or ("minimum required" in error_text and "bars" in error_text):
        return "INSUFFICIENT_HISTORY_FOR_MODEL"
    if status == LOAD_ERROR or ("load" in error_text and "fail" in error_text):
        return "MODEL_LOAD_FAILED"
    return "FORECAST_FAILED"


def _last_error(
    *,
    latest: dict[str, Any] | None,
    forecast_status: str,
    fallback_reason: str,
) -> str | None:
    if forecast_status in {"FORECAST_OK", "NOT_RUN"}:
        return None
    explicit = _pick_value(latest, "error")
    if _present(explicit):
        return str(explicit)
    status = str(latest.get("status") or "").strip() if latest else ""
    if status:
        return f"Latest forecast run ended with status '{status}' ({fallback_reason}) but did not persist an error message."
    return f"Latest forecast run failed ({fallback_reason}) but did not persist an error message."
