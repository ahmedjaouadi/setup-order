from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True, slots=True)
class ForecastProviderConfig:
    name: str
    enabled: bool
    priority: int
    role: str
    use_for_setup_score: bool = False
    use_for_execution: bool = False
    use_for_model_lab: bool = False
    use_for_runtime_forecast: bool = True
    auto_enable_when_ready: bool = False
    options: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ForecastStackConfig:
    enabled: bool
    execution_mode: str
    primary_model: str
    providers: dict[str, ForecastProviderConfig]
    active_models: tuple[str, ...] = ()
    comparison_models: tuple[str, ...] = ()
    advanced_models: tuple[str, ...] = ()
    experimental_models: tuple[str, ...] = ()
    horizons: dict[str, tuple[int, ...]] = field(default_factory=dict)
    require_accuracy_history_before_score_boost: bool = True
    min_accuracy_samples_for_boost: int = 30

    @classmethod
    def from_settings(cls, settings: dict[str, Any]) -> "ForecastStackConfig":
        raw = settings.get("forecast_stack") if isinstance(settings.get("forecast_stack"), dict) else {}
        legacy = settings.get("forecasting") if isinstance(settings.get("forecasting"), dict) else {}
        providers_raw = raw.get("providers") if isinstance(raw.get("providers"), dict) else {}
        if not providers_raw:
            providers_raw = {
                name: {"enabled": name in {"timesfm", "naive_baseline", "atr_baseline"}}
                for name in legacy.get("default_models", ["timesfm", "naive_baseline", "atr_baseline"])
            }
        providers: dict[str, ForecastProviderConfig] = {}
        for index, (name, value) in enumerate(providers_raw.items()):
            options = value if isinstance(value, dict) else {}
            providers[name] = ForecastProviderConfig(
                name=name, enabled=bool(options.get("enabled", False)),
                priority=int(options.get("priority", index)), role=str(options.get("role") or "comparison"),
                use_for_setup_score=bool(options.get("use_for_setup_score", False)),
                use_for_execution=False,
                use_for_model_lab=bool(options.get("use_for_model_lab", False)),
                use_for_runtime_forecast=bool(options.get("use_for_runtime_forecast", True)),
                auto_enable_when_ready=bool(options.get("auto_enable_when_ready", False)),
                options=dict(options),
            )
        safety = raw.get("safety") if isinstance(raw.get("safety"), dict) else {}
        horizons_raw = raw.get("horizons") if isinstance(raw.get("horizons"), dict) else {}
        return cls(
            enabled=bool(raw.get("enabled", legacy.get("enabled", True))),
            execution_mode="scoring_only",
            primary_model=str(raw.get("primary_model") or legacy.get("provider") or "timesfm"),
            providers=providers,
            active_models=_names(raw.get("active_models"), ("timesfm", "naive_baseline", "atr_baseline")),
            comparison_models=_names(raw.get("comparison_models"), ("chronos", "lag_llama")),
            advanced_models=_names(raw.get("advanced_models"), ("neuralforecast", "autogluon")),
            experimental_models=_names(raw.get("experimental_models"), ("moirai_uni2ts",)),
            horizons={
                str(timeframe): tuple(int(value) for value in values)
                for timeframe, values in horizons_raw.items()
                if isinstance(values, list | tuple)
            } or {"15m": (4, 8, 16), "1h": (4, 8, 24), "1d": (3, 5, 10)},
            require_accuracy_history_before_score_boost=bool(
                safety.get("require_accuracy_history_before_score_boost", True)
            ),
            min_accuracy_samples_for_boost=int(safety.get("min_accuracy_samples_for_boost", 30)),
        )


def _names(value: Any, default: tuple[str, ...]) -> tuple[str, ...]:
    if not isinstance(value, list | tuple):
        return default
    return tuple(str(item) for item in value if str(item).strip())
