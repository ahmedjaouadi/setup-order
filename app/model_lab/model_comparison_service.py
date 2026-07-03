from __future__ import annotations

from typing import Any

from app.model_lab.forecast_stack_benchmark import ForecastStackBenchmarkService


class ModelComparisonService:
    def __init__(self, repository: Any) -> None:
        self.benchmark = ForecastStackBenchmarkService(repository)

    def compare(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self.benchmark.compare(payload)

    def walk_forward(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self.benchmark.compare({**payload, "validation": "walk_forward"})
