from __future__ import annotations

from app.models import EventLevel, EventRecord, utc_now_iso
from app.storage.repositories import TradingRepository


class EventStore:
    def __init__(self, repository: TradingRepository) -> None:
        self.repository = repository

    def record(
        self,
        level: EventLevel,
        event_type: str,
        message: str,
        setup_id: str | None = None,
        symbol: str | None = None,
        data: dict | None = None,
    ) -> None:
        self.repository.add_event(
            EventRecord(
                timestamp=utc_now_iso(),
                level=level.value,
                event_type=event_type,
                setup_id=setup_id,
                symbol=symbol,
                message=message,
                data=data or {},
            )
        )

