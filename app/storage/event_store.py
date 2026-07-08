from __future__ import annotations

import logging
from typing import Any

from app.models import EventLevel, EventRecord, to_jsonable, utc_now_iso
from app.storage.repositories import TradingRepository
from app.utils.id_generator import new_id

logger = logging.getLogger(__name__)


class EventStore:
    def __init__(self, repository: TradingRepository) -> None:
        self.repository = repository
        self.failed_writes = 0

    def record(
        self,
        level: EventLevel,
        event_type: str,
        message: str,
        setup_id: str | None = None,
        symbol: str | None = None,
        data: dict | None = None,
    ) -> None:
        """Record a telemetry event; never raises.

        Events are an observability sink: no caller wants a failed event write
        to abort the flow being observed. A raising write here froze the engine
        heartbeat when "database is locked" propagated out of the stock-poll
        timeout handler (2026-07-08 incident). Failures are logged to app.log
        (which does not share the SQLite lock) and counted in failed_writes.
        Safety decisions must never depend on an event row existing; they are
        enforced through bot_state (see _persist_engine_safety_block).
        """
        try:
            self.repository.add_event(
                EventRecord(
                    timestamp=utc_now_iso(),
                    level=level.value,
                    event_type=event_type,
                    setup_id=setup_id,
                    symbol=symbol,
                    message=message,
                    data=to_jsonable(data or {}),
                )
            )
        except Exception:
            self.failed_writes += 1
            logger.exception(
                "Event write failed (%s total); event dropped: %s %s",
                self.failed_writes,
                event_type,
                message,
            )

    def record_runtime(
        self,
        event_type: str,
        *,
        aggregate_type: str | None = None,
        aggregate_id: str | None = None,
        symbol: str | None = None,
        payload: dict[str, Any] | None = None,
        correlation_id: str | None = None,
        causation_id: str | None = None,
    ) -> str:
        event_id = new_id("evt")
        self.repository.add_runtime_event(
            {
                "event_id": event_id,
                "event_type": event_type,
                "aggregate_type": aggregate_type,
                "aggregate_id": aggregate_id,
                "symbol": symbol.upper() if symbol else None,
                "payload": to_jsonable(payload or {}),
                "correlation_id": correlation_id,
                "causation_id": causation_id,
                "created_at": utc_now_iso(),
            }
        )
        return event_id

    def record_decision_trace(
        self,
        *,
        decision_type: str,
        final_decision: str,
        trace: dict[str, Any],
        symbol: str | None = None,
        setup_id: str | None = None,
        scenario_id: str | None = None,
        opportunity_id: str | None = None,
        correlation_id: str | None = None,
    ) -> str:
        trace_id = str(trace.get("trace_id") or new_id("trace"))
        created_at = utc_now_iso()
        trace_payload = {
            "trace_id": trace_id,
            **to_jsonable(trace),
            "decision_type": decision_type,
            "final_decision": final_decision,
            "created_at": created_at,
        }
        self.repository.add_decision_trace(
            {
                "trace_id": trace_id,
                "symbol": symbol.upper() if symbol else None,
                "setup_id": setup_id,
                "scenario_id": scenario_id,
                "opportunity_id": opportunity_id,
                "decision_type": decision_type,
                "final_decision": final_decision,
                "trace": trace_payload,
                "created_at": created_at,
            }
        )
        self.record_runtime(
            "decision_trace_created",
            aggregate_type="decision_trace",
            aggregate_id=trace_id,
            symbol=symbol,
            payload={
                "decision_type": decision_type,
                "final_decision": final_decision,
                "setup_id": setup_id,
                "scenario_id": scenario_id,
                "opportunity_id": opportunity_id,
            },
            correlation_id=correlation_id,
        )
        return trace_id
