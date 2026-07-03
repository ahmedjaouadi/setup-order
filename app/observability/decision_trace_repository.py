from __future__ import annotations

from typing import Any

from app.storage.repositories import TradingRepository


class DecisionTraceRepository:
    def __init__(self, repository: TradingRepository) -> None:
        self.repository = repository

    def add(self, trace: dict[str, Any]) -> str:
        self.repository.add_decision_trace(trace)
        return str(trace["trace_id"])

    def get(self, trace_id: str) -> dict[str, Any] | None:
        return self.repository.get_decision_trace(trace_id)

    def list(
        self,
        *,
        setup_id: str | None = None,
        symbol: str | None = None,
        scenario_id: str | None = None,
        opportunity_id: str | None = None,
        decision_type: str | None = None,
        final_decision: str | None = None,
        date: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        return self.repository.list_decision_traces(
            setup_id=setup_id,
            symbol=symbol,
            scenario_id=scenario_id,
            opportunity_id=opportunity_id,
            decision_type=decision_type,
            final_decision=final_decision,
            date=date,
            limit=limit,
        )

    def list_for_entity(
        self,
        entity_type: str,
        entity_id: str,
        *,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        normalized = str(entity_type or "").strip().upper()
        if normalized == "SETUP":
            return self.list(setup_id=entity_id, limit=limit)
        if normalized == "SCENARIO":
            return self.list(scenario_id=entity_id, limit=limit)
        if normalized == "OPPORTUNITY":
            return self.list(opportunity_id=entity_id, limit=limit)
        return [
            row
            for row in self.list(limit=limit)
            if (row.get("trace") or {}).get("entity_id") == entity_id
            and (row.get("trace") or {}).get("entity_type") == normalized
        ]
