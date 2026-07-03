from __future__ import annotations

from typing import Any

from app.models import utc_now_iso
from app.opportunities.opportunity_expiration_policy import OpportunityExpirationPolicy
from app.storage.event_store import EventStore
from app.storage.repositories import TradingRepository


class OpportunityLifecycleService:
    def __init__(
        self,
        repository: TradingRepository,
        event_store: EventStore,
        expiration_policy: OpportunityExpirationPolicy,
    ) -> None:
        self.repository = repository
        self.event_store = event_store
        self.expiration_policy = expiration_policy

    def mark_reviewed(self, opportunity_id: str) -> dict[str, Any]:
        opportunity = self.repository.get_opportunity(opportunity_id)
        if opportunity is None:
            raise KeyError(opportunity_id)
        payload = opportunity.get("payload") if isinstance(opportunity.get("payload"), dict) else {}
        payload["reviewed_at"] = utc_now_iso()
        opportunity["payload"] = payload
        opportunity["status"] = "REVIEWED"
        self.repository.upsert_opportunity(opportunity)
        self._runtime("opportunity_reviewed", opportunity_id, opportunity)
        return {"ok": True, "opportunity_id": opportunity_id, "status": "REVIEWED"}

    def expire(self, opportunity_id: str, *, reason: str = "manual") -> dict[str, Any]:
        opportunity = self.repository.get_opportunity(opportunity_id)
        if opportunity is None:
            raise KeyError(opportunity_id)
        payload = opportunity.get("payload") if isinstance(opportunity.get("payload"), dict) else {}
        payload["expired_at"] = utc_now_iso()
        payload["expiration_reason"] = reason
        opportunity["payload"] = payload
        opportunity["status"] = "EXPIRED"
        self.repository.upsert_opportunity(opportunity)
        self._runtime("opportunity_expired", opportunity_id, opportunity)
        return {"ok": True, "opportunity_id": opportunity_id, "status": "EXPIRED"}

    def expire_stale(self) -> dict[str, int]:
        count = 0
        for opportunity in self.repository.list_opportunities(limit=500):
            if self.expiration_policy.is_expired(opportunity):
                self.expire(str(opportunity["opportunity_id"]), reason="policy")
                count += 1
        return {"expired": count}

    def _runtime(
        self,
        event_type: str,
        opportunity_id: str,
        opportunity: dict,
    ) -> None:
        self.event_store.record_runtime(
            event_type,
            aggregate_type="opportunity",
            aggregate_id=opportunity_id,
            symbol=str(opportunity.get("symbol") or ""),
            payload={
                "status": opportunity.get("status"),
                "payload": opportunity.get("payload", {}),
            },
        )
