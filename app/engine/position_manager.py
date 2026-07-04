from __future__ import annotations

from app.models import EventLevel, PositionRecord, utc_now_iso
from app.storage.event_store import EventStore
from app.storage.repositories import TradingRepository


class PositionManager:
    def __init__(
        self,
        repository: TradingRepository,
        event_store: EventStore,
    ) -> None:
        self.repository = repository
        self.event_store = event_store

    def open_or_update_position(
        self,
        setup_id: str,
        symbol: str,
        quantity: int,
        average_price: float,
        current_price: float,
        stop_loss: float | None,
    ) -> PositionRecord:
        risk_remaining = 0.0
        if stop_loss is not None and quantity > 0:
            risk_remaining = max(average_price - stop_loss, 0) * quantity
        position = PositionRecord(
            symbol=symbol.upper(),
            setup_id=setup_id,
            quantity=quantity,
            average_price=average_price,
            current_price=current_price,
            unrealized_pnl=round((current_price - average_price) * quantity, 2),
            current_stop=stop_loss,
            risk_remaining=round(risk_remaining, 2),
            status="OPEN" if quantity else "CLOSED",
            updated_at=utc_now_iso(),
        )
        self.repository.upsert_position(position)
        return position

    def raise_stop(
        self,
        symbol: str,
        new_stop: float,
        allow_lower: bool = False,
    ) -> bool:
        position = self.repository.get_position(symbol)
        if not position:
            self.event_store.record(
                EventLevel.WARNING,
                "stop_move_rejected",
                "No position found for stop move",
                symbol=symbol.upper(),
            )
            return False
        current_stop = position.get("current_stop")
        if current_stop is not None and new_stop < float(current_stop) and not allow_lower:
            self.event_store.record(
                EventLevel.RISK,
                "stop_move_rejected",
                "Stop lowering is forbidden",
                setup_id=position.get("setup_id"),
                symbol=symbol.upper(),
                data={"current_stop": current_stop, "requested_stop": new_stop},
            )
            return False
        updated = PositionRecord(
            symbol=position["symbol"],
            setup_id=position["setup_id"],
            quantity=int(position["quantity"]),
            average_price=float(position["average_price"]),
            current_price=float(position["current_price"]),
            unrealized_pnl=float(position["unrealized_pnl"]),
            current_stop=new_stop,
            risk_remaining=round(
                max(float(position["average_price"]) - new_stop, 0) * int(position["quantity"]),
                2,
            ),
            status=position["status"],
            updated_at=utc_now_iso(),
        )
        self.repository.upsert_position(updated)
        self.event_store.record(
            EventLevel.RISK,
            "stop_moved",
            "Stop moved",
            setup_id=position.get("setup_id"),
            symbol=symbol.upper(),
            data={"new_stop": new_stop},
        )
        return True
