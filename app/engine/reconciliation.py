from __future__ import annotations

import math
from typing import Any, TypedDict

from app.broker.ib_models import BrokerExecution, BrokerOrderRequest, BrokerPosition
from app.broker.tws_connector import BrokerConnector
from app.engine.broker_reality import REPORT_STATE_KEY, build_broker_reality_report
from app.models import ConnectionStatus, EventLevel, OrderStatus, PositionRecord, SetupStatus
from app.setups.setup_roles import setup_is_management_only, setup_role_from_config
from app.storage.event_store import EventStore
from app.storage.repositories import TradingRepository


class ReconciliationResult(TypedDict):
    broker_positions: int
    broker_open_orders: int
    broker_executions: int
    local_positions: int
    local_orders: int
    local_orders_updated: int
    local_orders_cancelled: int
    local_orders_filled: int
    local_orders_rejected: int
    local_orders_reactivated: int
    missing_broker_orders: int
    adopted_positions: int
    manual_review_required: int
    broker_reality_rows: int
    reconciliation_mismatches: int
    auto_execution_blocked: bool
    broker_tracker_status: str


class ExecutionMatch(TypedDict):
    quantity: float
    price: float
    execution_count: int
    quantity_matches: bool


class ReconciliationEngine:
    def __init__(
        self,
        repository: TradingRepository,
        event_store: EventStore,
        broker: BrokerConnector,
        settings: dict[str, Any] | None = None,
    ) -> None:
        self.repository = repository
        self.event_store = event_store
        self.broker = broker
        self.settings = settings if isinstance(settings, dict) else {}

    async def run(self) -> ReconciliationResult:
        broker_connected = await self.broker.status() == ConnectionStatus.CONNECTED
        local_setups = self.repository.list_setups()
        local_orders = self.repository.list_orders()
        local_positions = self.repository.list_positions()
        broker_positions: list[BrokerPosition] = []
        position_query_error: str | None = None
        if broker_connected:
            try:
                broker_positions = await self.broker.positions()
            except Exception as exc:
                position_query_error = str(exc)
                self.event_store.record(
                    EventLevel.WARNING,
                    "broker_position_reconciliation_failed",
                    "Broker position reconciliation failed",
                    data={"error": position_query_error},
                )
        broker_orders: list[BrokerOrderRequest] = []
        order_query_error: str | None = None
        broker_account_summary: dict[str, Any] = {}
        account_query_error: str | None = None
        broker_executions: list[Any] = []
        if broker_connected:
            try:
                broker_orders = await self.broker.open_orders()
            except Exception as exc:
                order_query_error = str(exc)
                self.event_store.record(
                    EventLevel.WARNING,
                    "broker_order_reconciliation_failed",
                    "Broker open order reconciliation failed",
                    data={"error": order_query_error},
                )
            broker_account_summary, account_query_error = await _broker_account_summary(self.broker)
            if account_query_error:
                self.event_store.record(
                    EventLevel.WARNING,
                    "broker_account_reconciliation_failed",
                    "Broker account/pnl reconciliation failed",
                    data={"error": account_query_error},
                )
            broker_executions = await _broker_recent_executions(self.broker)
        broker_order_statuses = (
            await _broker_order_statuses(self.broker) if broker_connected else {}
        )
        result: ReconciliationResult = {
            "broker_positions": len(broker_positions),
            "broker_open_orders": len(broker_orders),
            "broker_executions": len(broker_executions),
            "local_positions": len(local_positions),
            "local_orders": len(local_orders),
            "local_orders_updated": 0,
            "local_orders_cancelled": 0,
            "local_orders_filled": 0,
            "local_orders_rejected": 0,
            "local_orders_reactivated": 0,
            "missing_broker_orders": 0,
            "adopted_positions": 0,
            "manual_review_required": 0,
            "broker_reality_rows": 0,
            "reconciliation_mismatches": 0,
            "auto_execution_blocked": False,
            "broker_tracker_status": "DISCONNECTED" if not broker_connected else "OK",
        }
        if not broker_connected:
            self._save_broker_reality_report(
                local_setups=local_setups,
                local_orders=local_orders,
                broker_orders=[],
                broker_positions=[],
                local_positions=local_positions,
                broker_account_summary={},
                broker_executions=[],
                broker_connected=False,
                result=result,
            )
            self.event_store.record(
                EventLevel.SYNC,
                "reconciliation_completed",
                "Reconciliation skipped because broker is disconnected",
                data=dict(result),
            )
            return result
        if order_query_error is None and position_query_error is None:
            self._reconcile_local_orders(
                broker_positions=broker_positions,
                broker_orders=broker_orders,
                broker_order_statuses=broker_order_statuses,
                broker_executions=broker_executions,
                result=result,
            )
        positions_by_symbol = {
            position.symbol.upper(): position
            for position in broker_positions
            if position.quantity != 0
        }
        for setup in local_setups:
            setup_id = str(setup.get("setup_id") or "")
            current_setup = self.repository.get_setup(setup_id) if setup_id else None
            if current_setup:
                setup = current_setup
            if str(setup.get("status") or "") in _TERMINAL_SETUP_STATUSES:
                continue
            config = setup.get("config", {})
            role = setup_role_from_config(config, infer_position_management=True)
            if not setup_is_management_only(role):
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
        self._save_broker_reality_report(
            local_setups=self.repository.list_setups(),
            local_orders=self.repository.list_orders(),
            broker_orders=broker_orders,
            broker_positions=broker_positions,
            local_positions=self.repository.list_positions(),
            broker_account_summary=broker_account_summary,
            broker_executions=broker_executions,
            broker_connected=broker_connected,
            result=result,
            order_query_error=order_query_error,
            position_query_error=position_query_error,
            account_query_error=account_query_error,
        )
        self.event_store.record(
            EventLevel.SYNC,
            "reconciliation_completed",
            "Reconciliation completed",
            data=dict(result),
        )
        return result

    def _save_broker_reality_report(
        self,
        *,
        local_setups: list[dict[str, Any]],
        local_orders: list[dict[str, Any]],
        broker_orders: list[BrokerOrderRequest],
        broker_positions: list[BrokerPosition],
        local_positions: list[dict[str, Any]],
        broker_account_summary: dict[str, Any],
        broker_executions: list[Any],
        broker_connected: bool,
        result: ReconciliationResult,
        order_query_error: str | None = None,
        position_query_error: str | None = None,
        account_query_error: str | None = None,
    ) -> None:
        report = build_broker_reality_report(
            local_setups=local_setups,
            local_orders=local_orders,
            local_positions=local_positions,
            broker_orders=broker_orders,
            broker_positions=broker_positions,
            account_summary=broker_account_summary,
            executions=broker_executions,
            broker_connected=broker_connected,
            order_query_error=order_query_error,
            position_query_error=position_query_error,
            account_query_error=account_query_error,
            settings=self.settings,
        )
        self.repository.set_bot_state(REPORT_STATE_KEY, report)
        result["broker_reality_rows"] = len(report.get("rows", []))
        result["reconciliation_mismatches"] = int(report.get("mismatch_count") or 0)
        result["auto_execution_blocked"] = bool(report.get("auto_execution_blocked"))
        result["broker_tracker_status"] = str(report.get("broker_tracker_status") or "")

    def _reconcile_local_orders(
        self,
        *,
        broker_positions: list[BrokerPosition],
        broker_orders: list[BrokerOrderRequest],
        broker_order_statuses: dict[str, str],
        result: ReconciliationResult,
        broker_executions: list[BrokerExecution] | None = None,
    ) -> None:
        executions = broker_executions or []
        positions_by_symbol = {
            position.symbol.upper(): position
            for position in broker_positions
            if position.quantity != 0
        }
        broker_open_statuses = _open_order_statuses_by_key(broker_orders)
        for order in self.repository.list_orders():
            local_keys = _local_order_keys(order)
            if not local_keys:
                continue
            current_status = str(order.get("status") or "")
            open_status = _first_matching_status(local_keys, broker_open_statuses)
            if open_status:
                if open_status != current_status:
                    self._mark_local_order_status(
                        order,
                        open_status,
                        result,
                        source="broker_open_orders",
                        broker_executions=executions,
                    )
                continue
            if current_status not in _ACTIVE_ORDER_STATUSES:
                continue
            known_status = _first_matching_status(local_keys, broker_order_statuses)
            if not known_status:
                known_status = _infer_missing_order_status(order, positions_by_symbol)
                result["missing_broker_orders"] += 1
            if known_status and known_status != str(order.get("status") or ""):
                self._mark_local_order_status(
                    order,
                    known_status,
                    result,
                    source="broker_reconciliation",
                    missing_from_open_orders=True,
                    broker_executions=executions,
                )

    def _mark_local_order_status(
        self,
        order: dict[str, Any],
        status: str,
        result: ReconciliationResult,
        *,
        source: str,
        missing_from_open_orders: bool = False,
        broker_executions: list[BrokerExecution] | None = None,
    ) -> None:
        order_id = str(order.get("id") or "")
        if not order_id:
            return
        previous_status = str(order.get("status") or "")
        self.repository.update_order_status(order_id, status)
        result["local_orders_updated"] += 1
        if status == OrderStatus.CANCELLED.value:
            result["local_orders_cancelled"] += 1
        elif status == OrderStatus.FILLED.value:
            result["local_orders_filled"] += 1
        elif status == OrderStatus.REJECTED.value:
            result["local_orders_rejected"] += 1
        elif (
            status == OrderStatus.SUBMITTED.value and previous_status not in _ACTIVE_ORDER_STATUSES
        ):
            result["local_orders_reactivated"] += 1
        self.event_store.record(
            EventLevel.SYNC,
            "order_status_reconciled",
            f"Order marked {status} after broker reconciliation",
            setup_id=str(order.get("setup_id") or "") or None,
            symbol=str(order.get("symbol") or "").upper() or None,
            data={
                "order_id": order_id,
                "broker_order_id": order.get("broker_order_id"),
                "broker_perm_id": order.get("broker_perm_id"),
                "previous_status": previous_status,
                "status": status,
                "source": source,
                "missing_from_open_orders": missing_from_open_orders,
            },
        )
        self._update_setup_after_reconciled_order(
            order,
            status,
            broker_executions=broker_executions or [],
        )

    def _update_setup_after_reconciled_order(
        self,
        order: dict[str, Any],
        status: str,
        *,
        broker_executions: list[BrokerExecution] | None = None,
    ) -> None:
        setup_id = str(order.get("setup_id") or "")
        if not setup_id:
            return
        setup = self.repository.get_setup(setup_id)
        if not setup:
            return
        setup_status = str(setup.get("status") or "")
        side = str(order.get("side") or "").upper()
        symbol = str(order.get("symbol") or "").upper()
        if status == OrderStatus.SUBMITTED.value:
            target_status = (
                SetupStatus.STOP_ORDER_PLACED.value
                if side == "SELL"
                else SetupStatus.ENTRY_ORDER_PLACED.value
            )
            if setup_status in _TERMINAL_SETUP_STATUSES or setup_status in {
                SetupStatus.MANUAL_REVIEW_REQUIRED.value,
            }:
                self.repository.update_setup_status(
                    setup_id,
                    target_status,
                    "Open order restored from TWS",
                )
            return
        if status != OrderStatus.CANCELLED.value:
            return
        if setup_status in _TERMINAL_SETUP_STATUSES:
            return
        if side == "SELL" and self.repository.get_position(symbol):
            self.repository.update_setup_status(
                setup_id,
                SetupStatus.MANUAL_REVIEW_REQUIRED.value,
                "Protective stop cancelled in TWS",
            )
            self.event_store.record(
                EventLevel.CRITICAL,
                "protective_stop_cancelled_in_tws",
                "Protective stop was cancelled in TWS while a position is open",
                setup_id=setup_id,
                symbol=symbol,
                data={"order_id": order.get("id"), "broker_order_id": order.get("broker_order_id")},
            )
            return
        if side == "BUY" and setup_status in _ORDER_DEPENDENT_SETUP_STATUSES:
            self.repository.update_setup_status(
                setup_id,
                SetupStatus.CANCELLED.value,
                "Entry order cancelled in TWS",
            )


def _protective_stop(config: dict) -> float | None:
    trailing = config.get("trailing_stop_loss", {})
    if isinstance(trailing, dict):
        value = trailing.get("current_stop", trailing.get("initial_stop"))
        if value is not None:
            return float(value)
    return None


def _matching_stop_order(orders: list, symbol: str):
    for order in orders:
        if order.symbol.upper() != symbol:
            continue
        if order.side != "SELL":
            continue
        if order.stop_price is not None:
            return order
    return None


_ACTIVE_ORDER_STATUSES = {OrderStatus.CREATED.value, OrderStatus.SUBMITTED.value}
_ORDER_DEPENDENT_SETUP_STATUSES = {
    SetupStatus.ENTRY_ORDER_PLACED.value,
    SetupStatus.ENTRY_PARTIALLY_FILLED.value,
    SetupStatus.STOP_ORDER_PLACED.value,
    SetupStatus.STOP_PLACED.value,
}
_TERMINAL_SETUP_STATUSES = {
    SetupStatus.CLOSED.value,
    SetupStatus.CANCELLED.value,
    SetupStatus.EXPIRED.value,
    SetupStatus.INVALIDATED.value,
    SetupStatus.ERROR.value,
    SetupStatus.ERROR_REQUIRES_MANUAL_REVIEW.value,
}


async def _broker_order_statuses(broker: BrokerConnector) -> dict[str, str]:
    status_reader = getattr(broker, "order_statuses", None)
    if not callable(status_reader):
        return {}
    try:
        statuses = await status_reader()
    except Exception:
        return {}
    if not isinstance(statuses, dict):
        return {}
    return {
        str(key): _normalize_order_status(value)
        for key, value in statuses.items()
        if str(key).strip() and _normalize_order_status(value)
    }


async def _broker_account_summary(broker: BrokerConnector) -> tuple[dict[str, Any], str | None]:
    reader = getattr(broker, "account_summary", None)
    if not callable(reader):
        return {}, None
    try:
        account = await reader()
    except Exception as exc:
        return {}, str(exc)
    return (account if isinstance(account, dict) else {}), None


async def _broker_recent_executions(broker: BrokerConnector) -> list[Any]:
    reader = getattr(broker, "recent_executions", None)
    if not callable(reader):
        return []
    try:
        executions = await reader()
    except Exception:
        return []
    return list(executions or [])


def _open_order_statuses_by_key(orders: list[BrokerOrderRequest]) -> dict[str, str]:
    statuses: dict[str, str] = {}
    for order in orders:
        status = _normalize_order_status(order.status) or OrderStatus.SUBMITTED.value
        for key in _broker_order_keys(order):
            statuses[key] = status
    return statuses


def _local_order_keys(order: dict[str, Any]) -> set[str]:
    keys = {
        order.get("id"),
        order.get("broker_order_id"),
        order.get("broker_perm_id"),
    }
    return _clean_keys(keys)


def _broker_order_keys(order: BrokerOrderRequest) -> set[str]:
    keys = {
        order.client_order_id,
        order.broker_order_id,
        order.broker_perm_id,
    }
    return _clean_keys(keys)


def _clean_keys(values: set[Any]) -> set[str]:
    return {key for value in values for key in [_normalize_key(value)] if key}


def _normalize_key(value: Any) -> str:
    return str(value or "").strip()


def _execution_matches_order(execution: BrokerExecution, order: dict[str, Any]) -> bool:
    execution_side = str(getattr(execution, "side", "") or "").upper()
    order_side = str(order.get("side") or "").upper()
    if execution_side != order_side:
        return False
    execution_order_id = _normalize_key(getattr(execution, "order_id", None))
    order_broker_order_id = _normalize_key(order.get("broker_order_id"))
    if execution_order_id and order_broker_order_id and execution_order_id == order_broker_order_id:
        return True
    execution_perm_id = _normalize_key(getattr(execution, "broker_perm_id", None))
    order_broker_perm_id = _normalize_key(order.get("broker_perm_id"))
    if execution_perm_id and order_broker_perm_id and execution_perm_id == order_broker_perm_id:
        return True
    return False


def _match_executions_to_order(
    executions: list[BrokerExecution],
    order: dict[str, Any],
) -> ExecutionMatch | None:
    matched = [execution for execution in executions if _execution_matches_order(execution, order)]
    if not matched:
        return None
    total_quantity = sum(float(execution.quantity) for execution in matched)
    if total_quantity == 0:
        return None
    weighted_price = (
        sum(float(execution.quantity) * float(execution.price) for execution in matched)
        / total_quantity
    )
    order_quantity = order.get("quantity")
    quantity_matches = order_quantity is not None and math.isclose(
        total_quantity, float(order_quantity), rel_tol=1e-9, abs_tol=1e-6
    )
    return {
        "quantity": total_quantity,
        "price": weighted_price,
        "execution_count": len(matched),
        "quantity_matches": quantity_matches,
    }


def _first_matching_status(keys: set[str], statuses: dict[str, str]) -> str:
    for key in keys:
        status = statuses.get(key)
        if status:
            return status
    return ""


def _normalize_order_status(value: Any) -> str:
    normalized = str(value or "").strip().upper()
    if normalized in {
        OrderStatus.CREATED.value,
        OrderStatus.SUBMITTED.value,
        OrderStatus.FILLED.value,
        OrderStatus.CANCELLED.value,
        OrderStatus.REJECTED.value,
        OrderStatus.ERROR.value,
    }:
        return normalized
    return ""


def _infer_missing_order_status(
    order: dict[str, Any],
    positions_by_symbol: dict[str, BrokerPosition],
) -> str:
    side = str(order.get("side") or "").upper()
    symbol = str(order.get("symbol") or "").upper()
    if side == "BUY" and symbol in positions_by_symbol:
        return OrderStatus.FILLED.value
    return OrderStatus.CANCELLED.value
