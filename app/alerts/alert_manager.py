from __future__ import annotations

from app.models import EventLevel
from app.storage.event_store import EventStore


class AlertManager:
    def __init__(self, event_store: EventStore) -> None:
        self.event_store = event_store

    def notify(
        self,
        event_type: str,
        message: str,
        level: EventLevel = EventLevel.INFO,
        setup_id: str | None = None,
        symbol: str | None = None,
    ) -> None:
        self.event_store.record(
            level=level,
            event_type=event_type,
            message=message,
            setup_id=setup_id,
            symbol=symbol,
        )

