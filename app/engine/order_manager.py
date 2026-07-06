from __future__ import annotations

from app.broker.order_mapper import order_record_to_broker_request
from app.broker.tws_connector import BrokerConnector
from app.engine.fill_executor import FillExecutor
from app.models import (
    EventLevel,
    OrderRecord,
    OrderSide,
    OrderStatus,
    OrderType,
    PositionRecord,
    RiskDecision,
    SetupStatus,
    utc_now_iso,
)
from app.setups.setup_roles import setup_is_management_only, setup_role_from_config
from app.storage.event_store import EventStore
from app.storage.repositories import TradingRepository
from app.utils.id_generator import new_id


class DuplicateOrderError(RuntimeError):
    pass


class UnprotectedActiveOrderError(RuntimeError):
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
        settings: dict | None = None,
    ) -> None:
        self.repository = repository
        self.event_store = event_store
        self.broker = broker
        self.default_entry_order_type = default_entry_order_type
        self.default_stop_order_type = default_stop_order_type
        self.default_entry_limit_offset = default_entry_limit_offset
        self.fill_executor = FillExecutor(
            repository=repository,
            event_store=event_store,
            broker_provider=lambda: self.broker,
            stop_order_placer=self,
            settings=settings,
        )

    async def place_entry_order(
        self,
        setup: dict,
        risk_decision: RiskDecision,
    ) -> OrderRecord:
        self._ensure_broker_matches_setup_mode(setup)
        setup_role = setup_role_from_config(setup.get("config", {}))
        if setup_is_management_only(setup_role):
            raise ManagementOnlyEntryError("MANAGEMENT_ONLY setup cannot place an entry order")
        protection = self.repository.protection_snapshot_for_setup(setup["setup_id"])
        if protection.get("position_open") and not protection.get("has_active_stop_order"):
            raise UnprotectedActiveOrderError(
                "An open position exists without an active protective stop order"
            )
        if protection.get("active_entry_order_id"):
            if not protection.get("has_active_stop_order"):
                raise UnprotectedActiveOrderError(
                    "An active entry order exists without an attached protective stop order"
                )
            raise DuplicateOrderError("An active protected order already exists for this setup")
        trailing_stop = _trailing_initial_stop(setup)
        if trailing_stop is None or trailing_stop <= 0:
            raise ValueError(
                "Trailing stop-loss initial stop must be ready before submitting an entry order"
            )
        if not _trailing_stop_order_ready(setup):
            raise ValueError("BLOCKED_TRAILING_STOP_NOT_READY")
        risk_decision.stop_loss = trailing_stop
        entry = setup["config"].get("entry", {})
        order_type = str(entry.get("order_type", self.default_entry_order_type))
        limit_offset = float(entry.get("limit_offset", self.default_entry_limit_offset))
        trigger_price, limit_price, stop_price = self._entry_order_prices(
            order_type,
            risk_decision,
            limit_offset,
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
            stop_price=stop_price,
            oca_group=f"bracket:{setup['setup_id']}",
        )
        broker_result = await self.broker.submit_order(
            order_record_to_broker_request(order, transmit=False)
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
        if not order.broker_order_id:
            await self._cancel_parent_for_failed_protection(
                setup,
                order,
                reason="Parent order accepted without broker order ID",
                protection_status="STOP_PARENT_ID_MISSING",
            )
            return order
        try:
            stop_order = await self.place_stop_order(
                setup,
                quantity=risk_decision.quantity,
                stop_loss=risk_decision.stop_loss,
                parent_id=order.id,
                broker_parent_id=order.broker_order_id,
                transmit=True,
                update_setup_status=False,
            )
        except Exception as exc:
            await self._cancel_parent_for_failed_protection(
                setup,
                order,
                reason=f"Protective stop submission raised an exception: {exc}",
                protection_status="STOP_SUBMISSION_EXCEPTION",
            )
            return order
        if stop_order.status in {
            OrderStatus.REJECTED.value,
            OrderStatus.ERROR.value,
        }:
            await self._cancel_parent_for_failed_protection(setup, order, stop_order=stop_order)
            return order
        self.repository.update_setup_status(
            setup["setup_id"],
            SetupStatus.ENTRY_ORDER_PLACED.value,
            "Bracket order submitted",
        )
        self.event_store.record(
            EventLevel.ORDER,
            "entry_order_submitted",
            broker_result.reason or "Bracket order submitted",
            setup_id=setup["setup_id"],
            symbol=setup["symbol"],
            data={
                "order_id": order.id,
                "broker_order_id": order.broker_order_id,
                "quantity": order.quantity,
                "trigger_price": order.trigger_price,
                "limit_price": order.limit_price,
                "worst_case_entry_price": risk_decision.entry_price,
                "stop_order_id": stop_order.id,
                "stop_broker_order_id": stop_order.broker_order_id,
                "stop_loss": risk_decision.stop_loss,
                "protection_status": "BRACKET_ORDER_SUBMITTED",
            },
        )
        return order

    @staticmethod
    def _entry_order_prices(
        order_type: str,
        risk_decision: RiskDecision,
        limit_offset: float,
    ) -> tuple[float | None, float | None, float | None]:
        """(trigger_price, limit_price, stop_price) of a BUY entry order.

        The TWS connector reads limit_price for LMT, stop_price for STP and
        trigger_price + limit_price for STP_LMT; the record must carry the
        fields the broker actually consumes for its order type.
        """
        trigger_price = (
            risk_decision.trigger_price
            if risk_decision.trigger_price is not None
            else risk_decision.entry_price
        )
        if order_type == OrderType.LMT.value:
            return None, round(risk_decision.entry_price, 2), None
        if order_type == OrderType.STP.value:
            return trigger_price, None, trigger_price
        if order_type == OrderType.STP_LMT.value:
            limit_price = round(
                (
                    risk_decision.entry_price
                    if risk_decision.trigger_price is not None
                    else trigger_price + limit_offset
                ),
                2,
            )
            return trigger_price, limit_price, None
        return trigger_price, None, None

    async def place_manual_order(
        self,
        *,
        setup_id: str,
        symbol: str,
        side: str,
        quantity: int,
        order_type: str,
        limit_price: float | None = None,
        trigger_price: float | None = None,
    ) -> OrderRecord:
        """Single manual order without bracket (etape 11).

        Used for the reduce-only manual SELL and for the simulated-only
        unprotected BUY. Submits through the same broker path and event trail
        as setup orders; the ManualOrderService is responsible for the guard
        checks (halt, market closed, reduce-only quantity, protection rules).
        """
        order = OrderRecord(
            id=new_id("ord"),
            setup_id=setup_id,
            symbol=symbol.upper(),
            side=side,
            order_type=order_type,
            quantity=quantity,
            status=OrderStatus.CREATED.value,
            trigger_price=trigger_price,
            limit_price=limit_price,
            stop_price=(
                trigger_price
                if order_type in {OrderType.STP.value, OrderType.STP_LMT.value}
                else None
            ),
        )
        broker_result = await self.broker.submit_order(
            order_record_to_broker_request(order, transmit=True)
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
            self.event_store.record(
                EventLevel.ERROR,
                "manual_order_broker_rejected",
                broker_result.reason or "Manual order rejected by broker",
                setup_id=setup_id,
                symbol=order.symbol,
                data={"order_id": order.id, "status": order.status, "side": side},
            )
            return order
        self.event_store.record(
            EventLevel.ORDER,
            "manual_order_transmitted",
            broker_result.reason or "Manual order transmitted",
            setup_id=setup_id,
            symbol=order.symbol,
            data={
                "order_id": order.id,
                "broker_order_id": order.broker_order_id,
                "side": side,
                "quantity": order.quantity,
                "order_type": order.order_type,
                "limit_price": order.limit_price,
                "trigger_price": order.trigger_price,
            },
        )
        return order

    async def place_stop_order(
        self,
        setup: dict,
        quantity: int,
        stop_loss: float,
        parent_id: str | None = None,
        broker_parent_id: str | None = None,
        transmit: bool = True,
        update_setup_status: bool = True,
    ) -> OrderRecord:
        self._ensure_broker_matches_setup_mode(setup)
        order = OrderRecord(
            id=new_id("stp"),
            setup_id=setup["setup_id"],
            symbol=setup["symbol"],
            side=OrderSide.SELL.value,
            order_type=_managed_stop_order_type(setup, self.default_stop_order_type),
            quantity=quantity,
            status=OrderStatus.CREATED.value,
            stop_price=stop_loss,
            parent_id=parent_id,
            oca_group=f"bracket:{setup['setup_id']}",
        )
        broker_result = await self.broker.submit_order(
            order_record_to_broker_request(
                order,
                parent_id=broker_parent_id,
                transmit=transmit,
            )
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
                    "parent_order_id": parent_id,
                },
            )
            return order
        if update_setup_status:
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
            data={
                "order_id": order.id,
                "stop_loss": stop_loss,
                "parent_order_id": parent_id,
                "protection_status": (
                    "STOP_PENDING_PARENT_FILL" if parent_id else "POSITION_OPEN_STOP_ACTIVE"
                ),
            },
        )
        return order

    async def attach_missing_stop(self, order_id: str) -> OrderRecord:
        entry_order = self.repository.get_order(order_id)
        if not entry_order:
            raise ValueError("Entry order not found")
        if str(entry_order.get("side") or "").upper() != OrderSide.BUY.value:
            raise ValueError("Only BUY entry orders can receive an attached stop")
        if str(entry_order.get("status") or "") not in {
            OrderStatus.CREATED.value,
            OrderStatus.SUBMITTED.value,
        }:
            raise ValueError("Only active entry orders can receive an attached stop")
        broker_parent_id = str(entry_order.get("broker_order_id") or "")
        if not broker_parent_id:
            raise ValueError("Entry order is missing a broker order ID")

        setup_id = str(entry_order.get("setup_id") or "")
        setup = self.repository.get_setup(setup_id)
        if not setup:
            raise ValueError("Setup not found for entry order")
        active_stop = next(
            (
                order
                for order in self.repository.list_orders(setup_id)
                if str(order.get("side") or "").upper() == OrderSide.SELL.value
                and str(order.get("parent_id") or "") == str(entry_order.get("id") or "")
                and str(order.get("status") or "")
                in {
                    OrderStatus.CREATED.value,
                    OrderStatus.SUBMITTED.value,
                }
            ),
            None,
        )
        if active_stop is not None:
            raise DuplicateOrderError("An active protective stop already exists for this entry")

        stop_loss = _protective_stop_from_setup(setup)
        if stop_loss is None or stop_loss <= 0:
            raise ValueError("Setup protective stop is missing or invalid")

        try:
            stop_order = await self.place_stop_order(
                setup,
                quantity=int(entry_order.get("quantity") or 0),
                stop_loss=stop_loss,
                parent_id=str(entry_order["id"]),
                broker_parent_id=broker_parent_id,
                transmit=True,
                update_setup_status=False,
            )
        except Exception as exc:
            await self._cancel_parent_for_failed_protection(
                setup,
                _order_record_from_row(entry_order),
                reason=f"Protective stop repair raised an exception: {exc}",
                protection_status="STOP_REPAIR_EXCEPTION",
            )
            raise
        if stop_order.status in {
            OrderStatus.REJECTED.value,
            OrderStatus.ERROR.value,
        }:
            await self._cancel_parent_for_failed_protection(
                setup,
                _order_record_from_row(entry_order),
                stop_order=stop_order,
                reason="Protective stop repair failed",
                protection_status="STOP_REPAIR_FAILED",
            )
        else:
            self.repository.update_setup_status(
                setup["setup_id"],
                SetupStatus.ENTRY_ORDER_PLACED.value,
                "Protective stop attached to existing entry order",
            )
        return stop_order

    async def cancel_order(self, order_id: str) -> bool:
        order = self.repository.get_order(order_id)
        if not order or not order.get("broker_order_id"):
            # Rows built from unmatched TWS orders carry a synthetic
            # "broker_<id>" id: cancel those directly at the broker.
            if order is None and order_id.startswith("broker_"):
                return await self._cancel_broker_only_order(order_id.removeprefix("broker_"))
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
            return True
        if _broker_reported_missing_order(result.reason):
            reconciled = await self._mark_cancelled_if_absent_from_broker(order)
            if reconciled:
                return True
        return result.accepted

    async def _cancel_broker_only_order(self, broker_order_id: str) -> bool:
        if not broker_order_id:
            return False
        result = await self.broker.cancel_order(broker_order_id)
        if result.accepted:
            self.event_store.record(
                EventLevel.ORDER,
                "order_cancelled",
                "Broker-only order cancelled at TWS",
                data={"broker_order_id": broker_order_id},
            )
        return result.accepted

    async def simulate_fill_order(
        self,
        order_id: str,
        fill_price: float,
    ) -> PositionRecord | None:
        return await self.fill_executor.simulate_fill_order(order_id, fill_price)

    def _ensure_broker_matches_setup_mode(self, setup: dict) -> None:
        setup_mode = str(setup.get("config", {}).get("mode", "paper"))
        broker_mode = str(getattr(self.broker, "account_mode", "unknown"))
        if setup_mode != broker_mode:
            raise BrokerModeMismatchError(
                f"Setup mode '{setup_mode}' cannot use broker account '{broker_mode}'."
            )

    async def _cancel_parent_for_failed_protection(
        self,
        setup: dict,
        entry_order: OrderRecord,
        stop_order: OrderRecord | None = None,
        *,
        reason: str | None = None,
        protection_status: str = "STOP_SUBMISSION_FAILED",
    ) -> None:
        cancelled = False
        if entry_order.broker_order_id:
            result = await self.broker.cancel_order(entry_order.broker_order_id)
            cancelled = bool(result.accepted)
        if cancelled:
            self.repository.update_order_status(entry_order.id, OrderStatus.CANCELLED.value)
        failure_reason = reason or "Protective stop submission failed"
        self.repository.update_setup_status(
            setup["setup_id"],
            SetupStatus.ERROR_REQUIRES_MANUAL_REVIEW.value,
            failure_reason,
        )
        self.event_store.record(
            EventLevel.CRITICAL,
            "entry_order_unprotected_blocked",
            "Entry order blocked because the protective stop could not be attached",
            setup_id=setup["setup_id"],
            symbol=setup["symbol"],
            data={
                "entry_order_id": entry_order.id,
                "entry_broker_order_id": entry_order.broker_order_id,
                "stop_order_id": stop_order.id if stop_order else None,
                "stop_broker_order_id": stop_order.broker_order_id if stop_order else None,
                "parent_cancelled": cancelled,
                "failure_reason": failure_reason,
                "protection_status": protection_status,
            },
        )

    async def _mark_cancelled_if_absent_from_broker(self, order: dict) -> bool:
        try:
            open_orders = await self.broker.open_orders()
        except Exception:
            return False
        local_keys = _local_broker_keys(order)
        for broker_order in open_orders:
            if local_keys.intersection(_broker_request_keys(broker_order)):
                return False
        self.repository.update_order_status(str(order["id"]), OrderStatus.CANCELLED.value)
        self.event_store.record(
            EventLevel.SYNC,
            "order_cancel_reconciled",
            "Order already absent from broker; marked cancelled locally",
            setup_id=order["setup_id"],
            symbol=order["symbol"],
            data={
                "order_id": order["id"],
                "broker_order_id": order.get("broker_order_id"),
                "broker_perm_id": order.get("broker_perm_id"),
            },
        )
        return True


def _protective_stop_from_setup(setup: dict) -> float | None:
    config = setup.get("config", {})
    trailing = config.get("trailing_stop_loss", {}) if isinstance(config, dict) else {}
    if isinstance(trailing, dict):
        if trailing.get("enabled") is not True:
            return None
        trailing_stop = _number_or_none(trailing.get("initial_stop"))
        if trailing_stop is not None:
            return trailing_stop
    return None


def _trailing_initial_stop(setup: dict) -> float | None:
    return _protective_stop_from_setup(setup)


def _trailing_stop_order_ready(setup: dict) -> bool:
    config = setup.get("config", {})
    trailing = config.get("trailing_stop_loss", {}) if isinstance(config, dict) else {}
    if not isinstance(trailing, dict):
        return False
    if trailing.get("enabled") is not True:
        return False
    broker_order = trailing.get("broker_order")
    if not isinstance(broker_order, dict):
        return False
    if broker_order.get("required_before_entry_transmission") is not True:
        return False
    ready = trailing.get("trailing_stop_order_ready")
    if ready is None:
        ready = broker_order.get("trailing_stop_order_ready")
    return ready is True


def _managed_stop_order_type(setup: dict, default_stop_order_type: str) -> str:
    config = setup.get("config", {})
    trailing = config.get("trailing_stop_loss", {}) if isinstance(config, dict) else {}
    broker_order = trailing.get("broker_order", {}) if isinstance(trailing, dict) else {}
    requested = (
        str(broker_order.get("order_type") or "").upper() if isinstance(broker_order, dict) else ""
    )
    if requested in {"TRAIL", "TRAIL_LIMIT", "TRAIL_LMT"}:
        return requested
    return default_stop_order_type


def _number_or_none(value: object) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _order_record_from_row(row: dict) -> OrderRecord:
    return OrderRecord(
        id=str(row["id"]),
        setup_id=str(row["setup_id"]),
        symbol=str(row["symbol"]),
        side=str(row["side"]),
        order_type=str(row["order_type"]),
        quantity=int(row["quantity"]),
        status=str(row["status"]),
        trigger_price=row.get("trigger_price"),
        limit_price=row.get("limit_price"),
        stop_price=row.get("stop_price"),
        broker_order_id=row.get("broker_order_id"),
        broker_perm_id=row.get("broker_perm_id"),
        parent_id=row.get("parent_id"),
        oca_group=row.get("oca_group"),
        created_at=str(row.get("created_at") or utc_now_iso()),
        updated_at=str(row.get("updated_at") or utc_now_iso()),
    )


def _broker_reported_missing_order(reason: str) -> bool:
    text = str(reason or "").strip().lower()
    return "not found" in text or "unknown order" in text


def _local_broker_keys(order: dict) -> set[str]:
    return _clean_key_set(
        {
            order.get("id"),
            order.get("broker_order_id"),
            order.get("broker_perm_id"),
        }
    )


def _broker_request_keys(order: object) -> set[str]:
    return _clean_key_set(
        {
            getattr(order, "client_order_id", None),
            getattr(order, "broker_order_id", None),
            getattr(order, "broker_perm_id", None),
        }
    )


def _clean_key_set(values: set[object]) -> set[str]:
    return {text for value in values for text in [str(value or "").strip()] if text}
