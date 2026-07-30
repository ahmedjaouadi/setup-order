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


def _matching_broker_stop(orders: list[Any], symbol: str) -> float | None:
    """Same match pattern as reconciliation._matching_stop_order: active SELL stop."""
    for order in orders:
        if order.symbol.upper() != symbol:
            continue
        if order.side != "SELL":
            continue
        if order.stop_price is not None:
            return float(order.stop_price)
    return None


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

        current_stop, degraded = await self._resolve_stop_guard_reference(
            normalized, position, stop_order
        )
        if degraded:
            decision = "rejected" if (current_stop is not None and new_stop < current_stop) else "allowed"
            self.event_store.record(
                EventLevel.WARNING,
                "stop_guard_degraded_mode",
                "Stop guard evaluated without live broker confirmation",
                symbol=normalized,
                data={
                    "new_stop": new_stop,
                    "local_reference_stop": current_stop,
                    "decision": decision,
                },
            )
        if current_stop is not None and new_stop < current_stop:
            return self._rejected(
                normalized,
                REASON_STOP_LOWERING_FORBIDDEN,
                "Stop lowering is forbidden (never_lower_stop)",
                data={
                    "current_stop": current_stop,
                    "requested_stop": new_stop,
                    **({"degraded_mode": True} if degraded else {}),
                },
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

    async def _resolve_stop_guard_reference(
        self,
        symbol: str,
        position: dict[str, Any] | None,
        stop_order: dict[str, Any] | None,
    ) -> tuple[float | None, bool]:
        """Resolves the never_lower_stop reference and whether it is degraded.

        Broker-connected and reachable: the live open_orders() stop wins when
        a matching SELL stop exists for the symbol (fresh truth); if none
        exists, that is a legitimate "no stop" answer, not a failure, so we
        fall back to the local reference unchanged. Broker unreachable
        (disconnected or open_orders() raised): the local MAXIMUM is used and
        the result is flagged degraded, so callers can enforce the asymmetric
        rule (allow rises, refuse falls).
        """
        if await self._broker_is_connected():
            try:
                broker_orders = await self.broker.open_orders()
            except Exception:
                return self._max_local_stop(position, stop_order), True
            broker_stop = _matching_broker_stop(broker_orders, symbol)
            if broker_stop is not None:
                return broker_stop, False
            return self._current_stop(position, stop_order), False
        return self._max_local_stop(position, stop_order), True

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

    @staticmethod
    def _max_local_stop(
        position: dict[str, Any] | None,
        stop_order: dict[str, Any] | None,
    ) -> float | None:
        values: list[float] = []
        for source, key in ((stop_order, "stop_price"), (position, "current_stop")):
            if not source:
                continue
            value = source.get(key)
            if value is None:
                continue
            try:
                values.append(float(value))
            except (TypeError, ValueError):
                continue
        return max(values) if values else None

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
