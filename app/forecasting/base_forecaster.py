from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass(frozen=True, slots=True)
class ForecastModelCapabilities:
    model_name: str
    supports_point_forecast: bool = True
    supports_quantiles: bool = False
    supports_probabilistic_paths: bool = False
    supports_covariates: bool = False
    supports_zero_shot: bool = False
    requires_training: bool = False
    requires_local_model_path: bool = False
    installed: bool = False
    available: bool = False
    unavailable_reason: str | None = None


@dataclass(frozen=True, slots=True)
class NormalizedForecastResult:
    model_name: str
    symbol: str
    timeframe: str
    horizon_bars: int
    generated_at: str
    status: str
    point_forecast: list[float] | None = None
    quantiles: dict[str, list[float]] | None = None
    prediction_intervals: dict[str, Any] | None = None
    direction: str = "FLAT"
    direction_confidence: float | None = None
    expected_return_pct: float | None = None
    prob_touch_entry: float | None = None
    prob_touch_stop_before_entry: float | None = None
    warnings: list[str] = field(default_factory=list)
    raw_output_ref: str | None = None


class BaseForecaster(Protocol):
    name: str

    def capabilities(self) -> ForecastModelCapabilities: ...

    def forecast(self, request: dict[str, Any]) -> NormalizedForecastResult: ...
