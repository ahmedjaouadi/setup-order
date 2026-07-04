from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(frozen=True, slots=True)
class ForecastConfig:
    enabled: bool = True
    provider: str = "timesfm"
    model: str = "timesfm_2_5_200m"
    model_repo: str = "google/timesfm-2.5-200m-pytorch"
    python_executable: str = ""
    worker_timeout_seconds: int = 180
    backend: str = "torch"
    device: str = "auto"
    timeframe: str = "15m"
    target: str = "log_return"
    context_bars: int = 256
    min_context_bars: int = 96
    horizon_bars: int = 4
    recalc_on_closed_bar_only: bool = True
    stale_after_minutes: int = 20
    use_for_decision: bool = False
    display_in_gui: bool = True
    persist_results: bool = True
    default_models: tuple[str, ...] = (
        "timesfm",
        "chronos",
        "lag_llama",
        "moirai",
        "moirai_uni2ts",
        "uni2ts",
        "neuralforecast",
        "autogluon",
        "naive_baseline",
        "atr_baseline",
    )
    provider_options: dict[str, dict[str, Any]] = field(default_factory=dict)
    score_thresholds: dict[str, int] = field(
        default_factory=lambda: {
            "bullish": 75,
            "neutral_bullish": 60,
            "neutral": 40,
        }
    )

    @classmethod
    def from_settings(cls, settings: dict[str, Any]) -> ForecastConfig:
        raw = settings.get("forecasting", {})
        if not isinstance(raw, dict):
            raw = {}
        thresholds = raw.get("score_thresholds", {})
        if not isinstance(thresholds, dict):
            thresholds = {}
        defaults = cls()
        raw_models = raw.get("default_models", defaults.default_models)
        if isinstance(raw_models, str):
            default_models = (raw_models,)
        elif isinstance(raw_models, list | tuple):
            default_models = tuple(str(item) for item in raw_models if str(item).strip())
        else:
            default_models = defaults.default_models
        raw_provider_options = raw.get("providers", {})
        provider_options = (
            {
                str(key).strip().lower().replace("-", "_"): value
                for key, value in raw_provider_options.items()
                if isinstance(value, dict)
            }
            if isinstance(raw_provider_options, dict)
            else {}
        )
        return cls(
            enabled=bool(raw.get("enabled", defaults.enabled)),
            provider=str(raw.get("provider", defaults.provider)),
            model=str(raw.get("model", defaults.model)),
            model_repo=str(raw.get("model_repo", defaults.model_repo)),
            python_executable=str(raw.get("python_executable", defaults.python_executable)),
            worker_timeout_seconds=int(
                raw.get("worker_timeout_seconds", defaults.worker_timeout_seconds)
                or defaults.worker_timeout_seconds
            ),
            backend=str(raw.get("backend", defaults.backend)),
            device=str(raw.get("device", defaults.device)),
            timeframe=str(raw.get("timeframe", defaults.timeframe)),
            target=str(raw.get("target", defaults.target)),
            context_bars=int(
                raw.get("context_bars", defaults.context_bars) or defaults.context_bars
            ),
            min_context_bars=int(
                raw.get("min_context_bars", defaults.min_context_bars) or defaults.min_context_bars
            ),
            horizon_bars=int(
                raw.get("horizon_bars", defaults.horizon_bars) or defaults.horizon_bars
            ),
            recalc_on_closed_bar_only=bool(
                raw.get("recalc_on_closed_bar_only", defaults.recalc_on_closed_bar_only)
            ),
            stale_after_minutes=int(
                raw.get("stale_after_minutes", defaults.stale_after_minutes)
                or defaults.stale_after_minutes
            ),
            use_for_decision=False,
            display_in_gui=bool(raw.get("display_in_gui", defaults.display_in_gui)),
            persist_results=bool(raw.get("persist_results", defaults.persist_results)),
            default_models=default_models or defaults.default_models,
            provider_options=provider_options,
            score_thresholds={
                "bullish": int(thresholds.get("bullish", defaults.score_thresholds["bullish"])),
                "neutral_bullish": int(
                    thresholds.get(
                        "neutral_bullish",
                        defaults.score_thresholds["neutral_bullish"],
                    )
                ),
                "neutral": int(thresholds.get("neutral", defaults.score_thresholds["neutral"])),
            },
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class ForecastReferences:
    setup_id: str | None = None
    support_level_reference: float | None = None
    entry_trigger_reference: float | None = None
    stop_level_reference: float | None = None


@dataclass(frozen=True, slots=True)
class TimesFMForecastOutput:
    q10_path: list[float]
    q50_path: list[float]
    q90_path: list[float]
    prob_touch_entry: float | None = None
    prob_touch_stop_before_entry: float | None = None
    prediction_intervals: dict[str, Any] | None = None
    warnings: list[str] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class ForecastResult:
    symbol: str
    timeframe: str
    model: str
    target: str
    context_bars: int
    horizon_bars: int
    generated_at: str
    current_price: float | None = None
    reference_price: float | None = None
    forecast_last_price: float | None = None
    forecast_expected_return_pct: float | None = None
    median_end_price: float | None = None
    median_return_pct: float | None = None
    q10_end_price: float | None = None
    q50_end_price: float | None = None
    q90_end_price: float | None = None
    direction_basis: str = "q50_last_vs_reference_price"
    forecast_path: list[float] = field(default_factory=list)
    q10_path: list[float] = field(default_factory=list)
    q50_path: list[float] = field(default_factory=list)
    q90_path: list[float] = field(default_factory=list)
    support_level_reference: float | None = None
    entry_trigger_reference: float | None = None
    stop_level_reference: float | None = None
    median_above_entry_trigger: bool | None = None
    q10_above_support: bool | None = None
    q10_above_stop: bool | None = None
    forecast_slope: str = "FLAT"
    direction: str = "FLAT"
    direction_confidence: float | None = None
    prob_touch_entry: float | None = None
    prob_touch_stop_before_entry: float | None = None
    uncertainty_width_pct: float | None = None
    prediction_intervals: dict[str, Any] | None = None
    warnings: list[str] = field(default_factory=list)
    forecast_status: str = "DISABLED"
    confidence: str = "LOW"
    metric_score: int = 0
    used_for_decision: bool = False
    decision_impact: str = "NONE"
    status: str = "DISABLED"
    error: str | None = None
    input_start_time: str | None = None
    input_end_time: str | None = None
    setup_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["used_for_decision"] = False
        payload["decision_impact"] = "NONE"
        return payload
