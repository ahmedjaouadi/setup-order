from __future__ import annotations

from app.broker.tws_connector import BrokerConnector
from app.models import EventLevel, PositionRecord, SetupRole, SetupStatus
from app.storage.event_store import EventStore
from app.storage.repositories import TradingRepository


class ReconciliationEngine:
    def __init__(
        self,
        repository: TradingRepository,
        event_store: EventStore,
        broker: BrokerConnector,
    ) -> None:
        self.repository = repository
        self.event_store = event_store
        self.broker = broker

    async def run(self) -> dict[str, int]:
        broker_positions = await self.broker.positions()
        broker_orders = await self.broker.open_orders()
        result = {
            "broker_positions": len(broker_positions),
            "broker_open_orders": len(broker_orders),
            "local_positions": len(self.repository.list_positions()),
            "local_orders": len(self.repository.list_orders()),
            "adopted_positions": 0,
            "manual_review_required": 0,
        }
        positions_by_symbol = {
            position.symbol.upper(): position
            for position in broker_positions
            if position.quantity != 0
        }
        for setup in self.repository.list_setups():
            config = setup.get("config", {})
            role = config.get("setup_role")
            if role is None and config.get("setup_type") == "position_management":
                role = SetupRole.MANAGEMENT_ONLY.value
            if role != SetupRole.MANAGEMENT_ONLY.value:
                continue
            source = config.get("position_source", {})
            if source.get("mode") != "adopt_existing_ibkr_position":
                continue
            symbol = str(setup["symbol"]).upper()
            broker_position = positions_by_symbol.get(symbol)
            if broker_position is None:
                if source.get("block_if_position_not_found", True):
                    result["manual_review_required"] += 1
                    self.repository.update_setup_status(
                        setup["setup_id"],
                        SetupStatus.MANUAL_REVIEW_REQUIRED.value,
                        "Existing IBKR position not found",
                    )
                    self.event_store.record(
                        EventLevel.SYNC,
                        "adoption_blocked_position_not_found",
                        "Existing IBKR position not found",
                        setup_id=setup["setup_id"],
                        symbol=symbol,
                    )
                continue
            protective_stop = _protective_stop(config)
            if protective_stop is None:
                result["manual_review_required"] += 1
                self.repository.update_setup_status(
                    setup["setup_id"],
                    SetupStatus.ERROR_REQUIRES_MANUAL_REVIEW.value,
                    "Protective stop is missing",
                )
                self.event_store.record(
                    EventLevel.CRITICAL,
                    "adoption_blocked_missing_stop",
                    "Protective stop is missing",
                    setup_id=setup["setup_id"],
                    symbol=symbol,
                )
                continue
            if broker_position.current_price < protective_stop:
                result["manual_review_required"] += 1
                self.repository.update_setup_status(
                    setup["setup_id"],
                    SetupStatus.MANUAL_REVIEW_REQUIRED.value,
                    "Market price is below protective stop",
                )
                self.event_store.record(
                    EventLevel.RISK,
                    "adoption_blocked_price_below_stop",
                    "Market price is below protective stop",
                    setup_id=setup["setup_id"],
                    symbol=symbol,
                    data={
                        "current_price": broker_position.current_price,
                        "protective_stop": protective_stop,
                    },
                )
                continue
            stop_order = _matching_stop_order(broker_orders, symbol)
            safety = config.get("safety", {})
            if stop_order is None and safety.get("pause_if_stop_is_missing", True):
                result["manual_review_required"] += 1
                self.repository.update_setup_status(
                    setup["setup_id"],
                    SetupStatus.MANUAL_REVIEW_REQUIRED.value,
                    "Broker stop order not found",
                )
                self.event_store.record(
                    EventLevel.RISK,
                    "adoption_blocked_stop_not_found",
                    "Broker stop order not found",
                    setup_id=setup["setup_id"],
                    symbol=symbol,
                )
                continue
            current_stop = stop_order.stop_price if stop_order else protective_stop
            risk_remaining = max(
                broker_position.current_price - float(current_stop),
                0.0,
            ) * abs(broker_position.quantity)
            self.repository.upsert_position(
                PositionRecord(
                    symbol=symbol,
                    setup_id=setup["setup_id"],
                    quantity=broker_position.quantity,
                    average_price=broker_position.average_price,
                    current_price=broker_position.current_price,
                    unrealized_pnl=round(
                        (broker_position.current_price - broker_position.average_price)
                        * broker_position.quantity,
                        2,
                    ),
                    current_stop=float(current_stop),
                    risk_remaining=round(risk_remaining, 2),
                    status="OPEN",
                )
            )
            self.repository.update_setup_status(
                setup["setup_id"],
                SetupStatus.IN_POSITION.value,
                "Existing IBKR position adopted",
            )
            result["adopted_positions"] += 1
            self.event_store.record(
                EventLevel.SYNC,
                "existing_position_adopted",
                "Existing IBKR position adopted",
                setup_id=setup["setup_id"],
                symbol=symbol,
                data={
                    "quantity": broker_position.quantity,
                    "average_price": broker_position.average_price,
                    "current_stop": current_stop,
                },
            )
        self.event_store.record(
            EventLevel.SYNC,
            "reconciliation_completed",
            "Reconciliation completed",
            data=result,
        )
        return result


def _protective_stop(config: dict) -> float | None:
    risk = config.get("risk", {})
    value = risk.get("protective_stop", risk.get("initial_stop_loss"))
    return float(value) if value is not None else None


def _matching_stop_order(orders: list, symbol: str):
    for order in orders:
        if order.symbol.upper() != symbol:
            continue
        if order.side != "SELL":
            continue
        if order.stop_price is not None:
            return order
    return None
