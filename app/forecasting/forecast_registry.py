from __future__ import annotations

from typing import Any

from app.forecasting.adapters import ForecastAdapterRegistry
from app.forecasting.forecast_models import ForecastConfig


class ForecastRegistry:
    """Public provider registry used by runtime and Model Lab."""

    def __init__(self, timesfm_engine: Any | None = None) -> None:
        self._registry = ForecastAdapterRegistry(timesfm_engine=timesfm_engine)

    def get(self, model_name: str) -> Any | None:
        return self._registry.get(_name(model_name))

    def names(self) -> list[str]:
        return sorted({"timesfm", "naive_baseline", "atr_baseline", *self._registry.names()})

    def capabilities(self, model_name: str, config: ForecastConfig):
        return self._registry.capabilities(_name(model_name), config)

    def forecast(self, model_name: str, request: dict[str, Any], config: ForecastConfig):
        return self._registry.forecast_normalized(_name(model_name), request, config)


def _name(value: str) -> str:
    return str(value).strip().lower().replace("-", "_")
