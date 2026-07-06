from __future__ import annotations

import logging
from typing import Any

from app.engine.position_manager import PositionManager
from app.engine.trade_guards import TradeGuardsService
from app.models import ConnectionStatus, EventLevel
from app.storage.event_store import EventStore
from app.storage.repositories import TradingRepository

logger = logging.getLogger(__name__)

REASON_STOP_LOWERING_FORBIDDEN = "STOP_LOWERING_FORBIDDEN"
REASON_NO_STOP_TARGET = "NO_STOP_TARGET"
REASON_BROKER_REJECTED = "BROKER_REJECTED"


class StopModificationService:
    """Moves a protective stop for a symbol, broker first, local state second.

    The broker order is the source of truth: when a working stop order exists
    at the broker, it is modified there before any local record changes. Local
    records (order stop_price, position current_stop) are only updated once the
    broker accepted the change, so the UI never claims a protection level that
    TWS does not actually hold.
    """

    def __init__(
        self,
        repository: TradingRepository,
        event_store: EventStore,
        broker: Any,
        position_manager: PositionManager,
        trade_guards: TradeGuardsService | None = None,
    ) -> None:
        self.repository = repository
        self.event_store = event_store
        self.broker = broker
        self.position_manager = position_manager
        self.trade_guards = trade_guards

    async def modify_stop(self, symbol: str, new_stop: float) -> dict[str, Any]:
        normalized = symbol.upper()
        if self.trade_guards is not None:
            verdict = self.trade_guards.evaluate_stop_modification(normalized)
            if verdict is not None:
                return self._rejected(
                    normalized,
                    verdict.reason_code,
                    verdict.message,
                    data={"trade_guards": verdict.as_payload()},
                )
        position = self.repository.get_position(normalized)
        stop_order = self.repository.active_stop_order_for_symbol(normalized)
        if position is None and stop_order is None:
            return self._rejected(
                normalized,
                REASON_NO_STOP_TARGET,
                "No position or active stop order found for this symbol",
            )

        current_stop = self._current_stop(position, stop_order)
        if current_stop is not None and new_stop < current_stop:
            return self._rejected(
                normalized,
                REASON_STOP_LOWERING_FORBIDDEN,
                "Stop lowering is forbidden (never_lower_stop)",
                data={"current_stop": current_stop, "requested_stop": new_stop},
            )

        broker_updated = False
        broker_order_id = (stop_order or {}).get("broker_order_id")
        if broker_order_id and await self._broker_is_connected():
            result = await self.broker.modify_stop_order(str(broker_order_id), new_stop)
            if not result.accepted:
                return self._rejected(
                    normalized,
                    REASON_BROKER_REJECTED,
                    result.reason or "Broker rejected the stop modification",
                    data={"broker_order_id": broker_order_id},
                )
            broker_updated = True

        if stop_order is not None:
            self.repository.update_order_stop_price(str(stop_order["id"]), new_stop)
        if position is not None:
            self.position_manager.raise_stop(normalized, new_stop)

        self.event_store.record(
            EventLevel.RISK,
            "stop_modified",
            "Protective stop modified",
            setup_id=(position or stop_order or {}).get("setup_id"),
            symbol=normalized,
            data={
                "new_stop": new_stop,
                "previous_stop": current_stop,
                "broker_updated": broker_updated,
                "stop_order_id": (stop_order or {}).get("id"),
                "broker_order_id": broker_order_id,
            },
        )
        return {
            "ok": True,
            "symbol": normalized,
            "new_stop": new_stop,
            "previous_stop": current_stop,
            "broker_updated": broker_updated,
            "stop_order_id": (stop_order or {}).get("id"),
        }

    async def _broker_is_connected(self) -> bool:
        status_reader = getattr(self.broker, "status", None)
        if not callable(status_reader):
            return False
        try:
            return await status_reader() == ConnectionStatus.CONNECTED
        except Exception:
            return False

    @staticmethod
    def _current_stop(
        position: dict[str, Any] | None,
        stop_order: dict[str, Any] | None,
    ) -> float | None:
        for source in (stop_order, position):
            if not source:
                continue
            value = source.get("stop_price") if source is stop_order else source.get("current_stop")
            if value is not None:
                try:
                    return float(value)
                except (TypeError, ValueError):
                    continue
        return None

    def _rejected(
        self,
        symbol: str,
        reason_code: str,
        reason: str,
        *,
        data: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        self.event_store.record(
            EventLevel.RISK,
            "stop_modification_rejected",
            reason,
            symbol=symbol,
            data={"reason_code": reason_code, **(data or {})},
        )
        return {"ok": False, "symbol": symbol, "reason_code": reason_code, "reason": reason}
