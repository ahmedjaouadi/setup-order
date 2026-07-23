from __future__ import annotations

from collections.abc import Callable
from typing import Any, Protocol

from app.broker.tws_connector import BrokerConnector, SimulatedBrokerConnector
from app.engine.post_fill_progression import PostFillProgression
from app.engine.transaction_costs import simulated_fill_price, transaction_cost_settings
from app.models import OrderRecord, OrderStatus, PositionRecord
from app.storage.event_store import EventStore
from app.storage.repositories import TradingRepository


class StopOrderPlacer(Protocol):
    async def place_stop_order(
        self,
        setup: dict[str, Any],
        quantity: int,
        stop_loss: float,
        parent_id: str | None = None,
    ) -> OrderRecord: ...


class FillExecutor:
    def __init__(
        self,
        repository: TradingRepository,
        event_store: EventStore,
        broker_provider: Callable[[], BrokerConnector],
        stop_order_placer: StopOrderPlacer,
        settings: dict[str, Any] | None = None,
    ) -> None:
        self.repository = repository
        self.event_store = event_store
        self.broker_provider = broker_provider
        self.stop_order_placer = stop_order_placer
        self.settings = settings if isinstance(settings, dict) else {}
        self.progression = PostFillProgression(repository, event_store)

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

        # Paper fills must never be perfect (docs/skills.md 24bis.2):
        # apply the simulated slippage when a cost model is configured.
        if transaction_cost_settings(self.settings):
            fill_price = simulated_fill_price(
                trigger_price=fill_price,
                spread=None,
                settings=self.settings,
            )

        broker_order_id = order.get("broker_order_id")
        if not broker_order_id:
            return None

        broker_position = await broker.simulate_fill(broker_order_id, fill_price)
        if not broker_position:
            return None

        position = self.progression.record_fill(
            order_id=order_id,
            setup_id=order["setup_id"],
            quantity=broker_position.quantity,
            fill_price=fill_price,
            symbol=broker_position.symbol,
        )
        if position is None:
            return None

        setup = self.repository.get_setup(order["setup_id"])
        if not setup:
            return None

        protection_verified = self.progression.has_active_protection(setup["setup_id"])
        if not protection_verified:
            stop_order = await self.stop_order_placer.place_stop_order(
                setup,
                quantity=broker_position.quantity,
                stop_loss=position.current_stop,
                parent_id=order_id,
            )
            if stop_order.status in {OrderStatus.REJECTED.value, OrderStatus.ERROR.value}:
                return position
            protection_verified = True

        self.progression.mark_in_position(
            setup["setup_id"],
            protection_verified=protection_verified,
        )
        return position
