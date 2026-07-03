from __future__ import annotations

from typing import Any

from app.models import utc_now_iso
from app.opportunities.opportunity_to_scenario_mapper import OpportunityToScenarioMapper
from app.storage.event_store import EventStore
from app.storage.repositories import TradingRepository


class ScenarioGenerator:
    def __init__(
        self,
        repository: TradingRepository,
        event_store: EventStore,
        settings: dict[str, Any] | None = None,
        mapper: OpportunityToScenarioMapper | None = None,
    ) -> None:
        self.repository = repository
        self.event_store = event_store
        self.mapper = mapper or OpportunityToScenarioMapper(settings)

    def generate_draft(self, opportunity_id: str) -> dict[str, Any]:
        opportunity = self.repository.get_opportunity(opportunity_id)
        if opportunity is None:
            raise KeyError(opportunity_id)
        scenario = self.mapper.map(opportunity)
        persisted = {
            **scenario,
            "source_opportunity_id": opportunity_id,
            "created_at": scenario.get("created_at") or utc_now_iso(),
        }
        self.repository.add_scenario_draft(persisted)
        self.event_store.record_runtime(
            "scenario_generated",
            aggregate_type="opportunity",
            aggregate_id=opportunity_id,
            symbol=str(opportunity.get("symbol") or ""),
            payload=persisted,
        )
        self.event_store.record_decision_trace(
            decision_type="SCENARIO_GENERATED",
            final_decision="DRAFT",
            symbol=str(opportunity.get("symbol") or ""),
            scenario_id=str(scenario.get("scenario_id") or ""),
            opportunity_id=opportunity_id,
            trace={
                "entity_type": "OPPORTUNITY",
                "entity_id": opportunity_id,
                "decision": "DRAFT",
                "reason_codes": ["SCENARIO_NOT_ARMED"],
                "outputs": {
                    "scenario_id": scenario.get("scenario_id"),
                    "armed": False,
                    "ambiguities": scenario.get("ambiguities", []),
                },
                "human_message": "Scenario draft generated from opportunity. It is not armed.",
            },
        )
        return {
            "ok": True,
            "source_opportunity_id": opportunity_id,
            "scenario": persisted,
        }
