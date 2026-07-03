from __future__ import annotations

from typing import Any

from app.observability.decision_explainer import DecisionExplainer
from app.observability.decision_trace_models import DecisionTrace
from app.observability.decision_trace_repository import DecisionTraceRepository


class DecisionTraceService:
    def __init__(self, repository: DecisionTraceRepository) -> None:
        self.repository = repository
        self.explainer = DecisionExplainer()

    def create(self, trace: DecisionTrace) -> str:
        return self.repository.add(trace.to_record())

    def get(self, trace_id: str) -> dict[str, Any] | None:
        trace = self.repository.get(trace_id)
        return self._with_explanation(trace) if trace else None

    def list(self, **filters: Any) -> list[dict[str, Any]]:
        return [self._with_explanation(row) for row in self.repository.list(**filters)]

    def list_for_entity(
        self,
        entity_type: str,
        entity_id: str,
        *,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        return [
            self._with_explanation(row)
            for row in self.repository.list_for_entity(
                entity_type,
                entity_id,
                limit=limit,
            )
        ]

    def _with_explanation(self, trace: dict[str, Any]) -> dict[str, Any]:
        return {
            **trace,
            "human_message": self.explainer.explain(trace),
        }
