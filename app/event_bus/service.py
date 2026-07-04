from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable
from typing import Any

from app.storage.event_store import EventStore
from app.storage.repositories import TradingRepository

EventHandler = Callable[[dict[str, Any]], None]


class EventBus:
    def __init__(
        self,
        repository: TradingRepository,
        event_store: EventStore,
    ) -> None:
        self.repository = repository
        self.event_store = event_store
        self._handlers: dict[str, list[EventHandler]] = defaultdict(list)

    def subscribe(self, event_type: str, handler: EventHandler) -> None:
        self._handlers[event_type].append(handler)

    def publish(
        self,
        event_type: str,
        *,
        aggregate_type: str | None = None,
        aggregate_id: str | None = None,
        symbol: str | None = None,
        payload: dict[str, Any] | None = None,
        correlation_id: str | None = None,
        causation_id: str | None = None,
    ) -> dict[str, Any]:
        event_id = self.event_store.record_runtime(
            event_type,
            aggregate_type=aggregate_type,
            aggregate_id=aggregate_id,
            symbol=symbol,
            payload=payload or {},
            correlation_id=correlation_id,
            causation_id=causation_id,
        )
        event = {
            "event_id": event_id,
            "event_type": event_type,
            "aggregate_type": aggregate_type,
            "aggregate_id": aggregate_id,
            "symbol": symbol.upper() if symbol else None,
            "payload": payload or {},
            "correlation_id": correlation_id,
            "causation_id": causation_id,
        }
        for handler in list(self._handlers.get(event_type, [])):
            handler(event)
        return event

    def list_events(
        self,
        *,
        event_type: str | None = None,
        symbol: str | None = None,
        aggregate_id: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        return self.repository.list_runtime_events(
            event_type=event_type,
            symbol=symbol,
            aggregate_id=aggregate_id,
            limit=limit,
        )
