from __future__ import annotations

from typing import Any, Callable, Protocol

from app.broker.tws_connector import BrokerConnector, SimulatedBrokerConnector
from app.models import EventLevel, OrderRecord, OrderStatus, PositionRecord, SetupStatus
from app.storage.event_store import EventStore
from app.storage.repositories import TradingRepository


class StopOrderPlacer(Protocol):
    async def place_stop_order(
        self,
        setup: dict[str, Any],
        quantity: int,
        stop_loss: float,
        parent_id: str | None = None,
    ) -> OrderRecord:
        ...


class FillExecutor:
    def __init__(
        self,
        repository: TradingRepository,
        event_store: EventStore,
        broker_provider: Callable[[], BrokerConnector],
        stop_order_placer: StopOrderPlacer,
    ) -> None:
        self.repository = repository
        self.event_store = event_store
        self.broker_provider = broker_provider
        self.stop_order_placer = stop_order_placer

    async def simulate_fill_order(
        self,
        order_id: str,
        fill_price: float,
    ) -> PositionRecord | None:
        order = self.repository.get_order(order_id)
        if not order or order["status"] != OrderStatus.SUBMITTED.value:
            return None

        broker = self.broker_provider()
        if not isinstance(broker, SimulatedBrokerConnector):
            return None

        broker_order_id = order.get("broker_order_id")
        if not broker_order_id:
            return None

        broker_position = await broker.simulate_fill(broker_order_id, fill_price)
        if not broker_position:
            return None

        self.repository.update_order_status(order_id, OrderStatus.FILLED.value)
        setup = self.repository.get_setup(order["setup_id"])
        if not setup:
            return None

        config = setup.get("config", {}) if isinstance(setup.get("config"), dict) else {}
        trailing = config.get("trailing_stop_loss", {}) if isinstance(config, dict) else {}
        stop_raw = trailing.get("initial_stop") if isinstance(trailing, dict) else None
        if stop_raw is None:
            self.event_store.record(
                EventLevel.CRITICAL,
                "entry_fill_missing_trailing_stop",
                "Filled entry has no trailing_stop_loss.initial_stop",
                setup_id=setup["setup_id"],
                symbol=setup["symbol"],
            )
            self.repository.update_setup_status(
                setup["setup_id"],
                SetupStatus.ERROR_REQUIRES_MANUAL_REVIEW.value,
                "Filled entry missing trailing stop",
            )
            return None
        stop_loss = float(stop_raw)
        position = PositionRecord(
            symbol=broker_position.symbol,
            setup_id=setup["setup_id"],
            quantity=broker_position.quantity,
            average_price=fill_price,
            current_price=fill_price,
            unrealized_pnl=0.0,
            current_stop=stop_loss,
            risk_remaining=round(max(fill_price - stop_loss, 0) * broker_position.quantity, 2),
            status="OPEN",
        )
        self.repository.upsert_position(position)
        self.repository.update_setup_status(
            setup["setup_id"],
            SetupStatus.ENTRY_FILLED.value,
            "Entry order filled",
        )
        self.event_store.record(
            EventLevel.TRADE,
            "entry_filled",
            "Entry filled by the internal test broker",
            setup_id=setup["setup_id"],
            symbol=setup["symbol"],
            data={"fill_price": fill_price, "quantity": broker_position.quantity},
        )

        protection = self.repository.protection_snapshot_for_setup(setup["setup_id"])
        if not protection.get("has_active_stop_order"):
            stop_order = await self.stop_order_placer.place_stop_order(
                setup,
                quantity=broker_position.quantity,
                stop_loss=stop_loss,
                parent_id=order_id,
            )
            if stop_order.status in {OrderStatus.REJECTED.value, OrderStatus.ERROR.value}:
                return position

        self.repository.update_setup_status(
            setup["setup_id"],
            SetupStatus.IN_POSITION.value,
            "Position protected and open",
        )
        return position
