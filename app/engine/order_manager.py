from __future__ import annotations

from app.broker.order_mapper import order_record_to_broker_request
from app.broker.tws_connector import BrokerConnector, SimulatedBrokerConnector
from app.models import (
    EventLevel,
    OrderRecord,
    OrderSide,
    OrderStatus,
    OrderType,
    PositionRecord,
    RiskDecision,
    SetupRole,
    SetupStatus,
    utc_now_iso,
)
from app.storage.event_store import EventStore
from app.storage.repositories import TradingRepository
from app.utils.id_generator import new_id


class DuplicateOrderError(RuntimeError):
    pass


class ManagementOnlyEntryError(RuntimeError):
    pass


class BrokerModeMismatchError(RuntimeError):
    pass


class OrderManager:
    def __init__(
        self,
        repository: TradingRepository,
        event_store: EventStore,
        broker: BrokerConnector,
        default_entry_order_type: str = "STP_LMT",
        default_stop_order_type: str = "STP",
        default_entry_limit_offset: float = 0.05,
    ) -> None:
        self.repository = repository
        self.event_store = event_store
        self.broker = broker
        self.default_entry_order_type = default_entry_order_type
        self.default_stop_order_type = default_stop_order_type
        self.default_entry_limit_offset = default_entry_limit_offset

    async def place_entry_order(
        self,
        setup: dict,
        risk_decision: RiskDecision,
    ) -> OrderRecord:
        self._ensure_broker_matches_setup_mode(setup)
        setup_role = str(
            setup.get("config", {}).get(
                "setup_role",
                SetupRole.ENTRY_AND_MANAGEMENT.value,
            )
        )
        if setup_role == SetupRole.MANAGEMENT_ONLY.value:
            raise ManagementOnlyEntryError(
                "MANAGEMENT_ONLY setup cannot place an entry order"
            )
        if self.repository.active_orders_for_setup(setup["setup_id"]):
            raise DuplicateOrderError("An active order already exists for this setup")
        entry = setup["config"].get("entry", {})
        order_type = str(entry.get("order_type", self.default_entry_order_type))
        limit_offset = float(entry.get("limit_offset", self.default_entry_limit_offset))
        trigger_price = (
            risk_decision.trigger_price
            if risk_decision.trigger_price is not None
            else risk_decision.entry_price
        )
        limit_price = None
        if order_type == OrderType.STP_LMT.value:
            limit_price = round(
                risk_decision.entry_price
                if risk_decision.trigger_price is not None
                else trigger_price + limit_offset,
                2,
            )
        order = OrderRecord(
            id=new_id("ord"),
            setup_id=setup["setup_id"],
            symbol=setup["symbol"],
            side=OrderSide.BUY.value,
            order_type=order_type,
            quantity=risk_decision.quantity,
            status=OrderStatus.CREATED.value,
            trigger_price=trigger_price,
            limit_price=limit_price,
            stop_price=None,
        )
        broker_result = await self.broker.submit_order(
            order_record_to_broker_request(order)
        )
        order.status = broker_result.status
        order.broker_order_id = broker_result.broker_order_id
        order.broker_perm_id = broker_result.broker_perm_id
        order.updated_at = utc_now_iso()
        self.repository.upsert_order(order)
        if not broker_result.accepted or order.status in {
            OrderStatus.REJECTED.value,
            OrderStatus.ERROR.value,
        }:
            reason = broker_result.reason or "Entry order rejected by broker"
            self.repository.update_setup_status(
                setup["setup_id"],
                SetupStatus.ERROR_REQUIRES_MANUAL_REVIEW.value,
                reason,
            )
            self.event_store.record(
                EventLevel.ERROR,
                "entry_order_rejected",
                reason,
                setup_id=setup["setup_id"],
                symbol=setup["symbol"],
                data={
                    "order_id": order.id,
                    "broker_order_id": order.broker_order_id,
                    "status": order.status,
                    "quantity": order.quantity,
                    "trigger_price": order.trigger_price,
                    "limit_price": order.limit_price,
                    "worst_case_entry_price": risk_decision.entry_price,
                },
            )
            return order
        self.repository.update_setup_status(
            setup["setup_id"],
            SetupStatus.ENTRY_ORDER_PLACED.value,
            "Entry order submitted",
        )
        self.event_store.record(
            EventLevel.ORDER,
            "entry_order_submitted",
            broker_result.reason or "Entry order submitted",
            setup_id=setup["setup_id"],
            symbol=setup["symbol"],
            data={
                "order_id": order.id,
                "broker_order_id": order.broker_order_id,
                "quantity": order.quantity,
                "trigger_price": order.trigger_price,
                "limit_price": order.limit_price,
                "worst_case_entry_price": risk_decision.entry_price,
            },
        )
        return order

    async def place_stop_order(
        self,
        setup: dict,
        quantity: int,
        stop_loss: float,
        parent_id: str | None = None,
    ) -> OrderRecord:
        self._ensure_broker_matches_setup_mode(setup)
        order = OrderRecord(
            id=new_id("stp"),
            setup_id=setup["setup_id"],
            symbol=setup["symbol"],
            side=OrderSide.SELL.value,
            order_type=self.default_stop_order_type,
            quantity=quantity,
            status=OrderStatus.CREATED.value,
            stop_price=stop_loss,
            parent_id=parent_id,
        )
        broker_result = await self.broker.submit_order(
            order_record_to_broker_request(order)
        )
        order.status = broker_result.status
        order.broker_order_id = broker_result.broker_order_id
        order.broker_perm_id = broker_result.broker_perm_id
        order.updated_at = utc_now_iso()
        self.repository.upsert_order(order)
        if not broker_result.accepted or order.status in {
            OrderStatus.REJECTED.value,
            OrderStatus.ERROR.value,
        }:
            reason = broker_result.reason or "Protective stop rejected by broker"
            self.repository.update_setup_status(
                setup["setup_id"],
                SetupStatus.ERROR_REQUIRES_MANUAL_REVIEW.value,
                reason,
            )
            self.event_store.record(
                EventLevel.CRITICAL,
                "protective_stop_rejected",
                reason,
                setup_id=setup["setup_id"],
                symbol=setup["symbol"],
                data={
                    "order_id": order.id,
                    "broker_order_id": order.broker_order_id,
                    "status": order.status,
                    "quantity": order.quantity,
                    "stop_loss": stop_loss,
                },
            )
            return order
        self.repository.update_setup_status(
            setup["setup_id"],
            SetupStatus.STOP_ORDER_PLACED.value,
            "Protective stop submitted",
        )
        self.event_store.record(
            EventLevel.ORDER,
            "protective_stop_submitted",
            "Protective stop submitted",
            setup_id=setup["setup_id"],
            symbol=setup["symbol"],
            data={"order_id": order.id, "stop_loss": stop_loss},
        )
        return order

    async def cancel_order(self, order_id: str) -> bool:
        order = self.repository.get_order(order_id)
        if not order or not order.get("broker_order_id"):
            return False
        result = await self.broker.cancel_order(order["broker_order_id"])
        if result.accepted:
            self.repository.update_order_status(order_id, OrderStatus.CANCELLED.value)
            self.event_store.record(
                EventLevel.ORDER,
                "order_cancelled",
                "Order cancelled",
                setup_id=order["setup_id"],
                symbol=order["symbol"],
                data={"order_id": order_id},
            )
        return result.accepted

    async def simulate_fill_order(
        self,
        order_id: str,
        fill_price: float,
    ) -> PositionRecord | None:
        order = self.repository.get_order(order_id)
        if not order or order["status"] != OrderStatus.SUBMITTED.value:
            return None
        if not isinstance(self.broker, SimulatedBrokerConnector):
            return None
        broker_order_id = order.get("broker_order_id")
        if not broker_order_id:
            return None
        broker_position = await self.broker.simulate_fill(broker_order_id, fill_price)
        if not broker_position:
            return None
        self.repository.update_order_status(order_id, OrderStatus.FILLED.value)
        setup = self.repository.get_setup(order["setup_id"])
        if not setup:
            return None
        stop_loss = float(setup["config"]["risk"]["initial_stop_loss"])
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
        await self.place_stop_order(
            setup,
            quantity=broker_position.quantity,
            stop_loss=stop_loss,
            parent_id=order_id,
        )
        self.repository.update_setup_status(
            setup["setup_id"],
            SetupStatus.IN_POSITION.value,
            "Position protected and open",
        )
        self.event_store.record(
            EventLevel.TRADE,
            "entry_filled",
            "Entry filled in simulation",
            setup_id=setup["setup_id"],
            symbol=setup["symbol"],
            data={"fill_price": fill_price, "quantity": broker_position.quantity},
        )
        return position

    def _ensure_broker_matches_setup_mode(self, setup: dict) -> None:
        setup_mode = str(setup.get("config", {}).get("mode", "simulation"))
        broker_mode = str(getattr(self.broker, "account_mode", "unknown"))
        if setup_mode != broker_mode:
            raise BrokerModeMismatchError(
                f"Setup mode '{setup_mode}' cannot use broker account '{broker_mode}'."
            )
