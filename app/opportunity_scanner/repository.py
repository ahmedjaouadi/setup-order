from __future__ import annotations

from typing import Any, Protocol

from app.models import utc_now_iso


class OpportunitySignalStore(Protocol):
    def upsert_opportunity(self, opportunity: dict[str, Any]) -> str:
        ...


class OpportunitySignalRepository:
    """Persists context signals through the existing opportunities table."""

    def __init__(self, store: OpportunitySignalStore) -> None:
        self.store = store

    def upsert_signal(self, signal: dict[str, Any]) -> str:
        opportunity_id = signal.get("opportunity_id") or _opportunity_id(signal)
        payload = {
            "opportunity_status": signal.get("opportunity_status"),
            "opportunity_type": signal.get("opportunity_type"),
            "opportunity_types": signal.get("opportunity_types", []),
            "opportunity_score": signal.get("opportunity_score"),
            "reasons": signal.get("reasons", []),
            "warnings": signal.get("warnings", []),
            "recommended_next_action": signal.get("recommended_next_action"),
            "can_send_order": False,
            "executable": False,
            "source_snapshot": signal.get("source_snapshot", {}),
            "reason": "Opportunity Scanner signal; it cannot submit orders.",
        }
        return self.store.upsert_opportunity(
            {
                "opportunity_id": opportunity_id,
                "symbol": signal.get("symbol"),
                "opportunity_type": signal.get("opportunity_type"),
                "timeframe": signal.get("timeframe", "15m"),
                "status": _table_status(str(signal.get("opportunity_status") or "")),
                "score": signal.get("opportunity_score"),
                "detected_at": utc_now_iso(),
                "payload": payload,
            }
        )


def _opportunity_id(signal: dict[str, Any]) -> str:
    symbol = str(signal.get("symbol") or "UNKNOWN").upper()
    opportunity_type = str(signal.get("opportunity_type") or "WATCHLIST_ANOMALY").lower()
    return f"opp_{symbol}_{opportunity_type}_scanner"


def _table_status(opportunity_status: str) -> str:
    if opportunity_status == "OPPORTUNITY_DETECTED":
        return "DETECTED"
    if opportunity_status == "NO_OPPORTUNITY":
        return "REJECTED"
    return "WATCHLIST"
