from __future__ import annotations

import logging
import math
from typing import Any, TypedDict

from app.broker.ib_models import BrokerExecution, BrokerOrderRequest, BrokerPosition
from app.broker.tws_connector import BrokerConnector
from app.engine.broker_reality import REPORT_STATE_KEY, build_broker_reality_report
from app.engine.position_manager import PositionManager
from app.engine.post_fill_progression import PostFillProgression
from app.models import (
    ConnectionStatus,
    EventLevel,
    OrderRecord,
    OrderStatus,
    PositionRecord,
    SetupStatus,
)
from app.setups.setup_roles import setup_is_management_only, setup_role_from_config
from app.storage.event_store import EventStore
from app.storage.repositories import TradingRepository
from app.utils.id_generator import new_id

logger = logging.getLogger(__name__)

# Root C (audit 73, cause #3): the exact last_event message the adoption
# loop's own missing-stop branch below writes when it raises the alarm.
# Shared with the check further down that clears the alarm once the stop
# reappears, so the two can never drift out of sync.
ADOPTION_STOP_NOT_FOUND_MESSAGE = "Broker stop order not found"


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
        position_manager: PositionManager | None = None,
    ) -> None:
        self.repository = repository
        self.event_store = event_store
        self.broker = broker
        self.settings = settings if isinstance(settings, dict) else {}
        self.position_manager = position_manager
        self.progression = PostFillProgression(repository, event_store)

    async def run(self, *, startup: bool = False) -> ReconciliationResult:
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
        if startup:
            self._detect_unprotected_entry_orphans(local_setups)
            self._repair_frozen_setups(local_setups)
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
                    ADOPTION_STOP_NOT_FOUND_MESSAGE,
                )
                self.event_store.record(
                    EventLevel.RISK,
                    "adoption_blocked_stop_not_found",
                    ADOPTION_STOP_NOT_FOUND_MESSAGE,
                    setup_id=setup["setup_id"],
                    symbol=symbol,
                )
                continue
            # Root C (audit 73/74, cause #3): a fresh, active stop
            # (stop_order is not None, proven this cycle by
            # _matching_stop_order against the broker's own open orders --
            # never stale) found for a setup that is currently alarmed for
            # exactly this cause is the one legitimate, verified reason to
            # clear a MANUAL_REVIEW_REQUIRED alarm here. Any other alarm
            # cause, or no alarm at all, leaves allow_from_review False --
            # strictly unchanged behaviour, the S5b-3a guard keeps protecting
            # it exactly as before this lot.
            clearing_stop_alarm = (
                stop_order is not None
                and str(setup.get("status") or "") == SetupStatus.MANUAL_REVIEW_REQUIRED.value
                and str(setup.get("last_event") or "") == ADOPTION_STOP_NOT_FOUND_MESSAGE
            )
            if stop_order is not None:
                self._persist_adopted_stop_order(stop_order, setup, symbol)
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
            if clearing_stop_alarm:
                # The allow_from_review flag below is passed ONLY because
                # stop_order is not None proves the broker's own open orders
                # (fresh this cycle) hold an active protective stop again --
                # this is not a bypass of the S5b-3a review guard, it is that
                # guard's second documented legitimate exception (the first
                # is attach_missing_stop, order_manager.py:465): cause #3's
                # alarm ("Broker stop order not found") is verified gone, so
                # the alarm it raised can be cleared.
                self.repository.update_setup_status(
                    setup["setup_id"],
                    SetupStatus.IN_POSITION.value,
                    "Existing IBKR position adopted",
                    allow_from_review=True,
                )
            else:
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
            if clearing_stop_alarm:
                self.event_store.record(
                    EventLevel.SYNC,
                    "adoption_review_cleared_stop_restored",
                    "Broker stop order reappeared; adoption review alarm cleared",
                    setup_id=setup["setup_id"],
                    symbol=symbol,
                    data={"stop_order_id": stop_order.broker_order_id},
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

    def _persist_adopted_stop_order(
        self,
        stop_order: BrokerOrderRequest,
        setup: dict[str, Any],
        symbol: str,
    ) -> None:
        # B-1a (audit 80): the adoption loop above sees the broker's
        # protective stop (stop_order) but, before this, never wrote it to
        # the local orders table -- StopModificationService (root B, B-1b)
        # can only find a broker_order_id to modify via
        # active_stop_order_for_symbol, which reads that table. Idempotent:
        # this loop reruns every reconciliation cycle for any non-terminal
        # adopted setup, so reuse the existing row (upsert on its id) rather
        # than inserting a new one each pass. Anti-theft: a row already
        # owned by a DIFFERENT setup_id for this symbol is never reassigned
        # -- that would silently steal order ownership between setups
        # (audit 80 Q4) -- a fresh row is created instead and flagged.
        setup_id = setup["setup_id"]
        existing = self.repository.active_stop_order_for_symbol(symbol)
        if existing is not None and str(existing.get("setup_id") or "") == str(setup_id):
            order_id = existing["id"]
        elif existing is not None:
            order_id = new_id("adp")
            self.event_store.record(
                EventLevel.RISK,
                "adoption_stop_order_owner_conflict",
                "Active stop order for symbol belongs to a different setup",
                setup_id=setup_id,
                symbol=symbol,
                data={
                    "existing_setup_id": existing.get("setup_id"),
                    "existing_order_id": existing.get("id"),
                },
            )
        else:
            order_id = new_id("adp")
        status = _normalize_order_status(stop_order.status) or OrderStatus.SUBMITTED.value
        self.repository.upsert_order(
            OrderRecord(
                id=order_id,
                setup_id=setup_id,
                symbol=symbol,
                side="SELL",
                order_type=stop_order.order_type,
                quantity=stop_order.quantity,
                status=status,
                stop_price=stop_order.stop_price,
                broker_order_id=stop_order.broker_order_id,
                broker_perm_id=stop_order.broker_perm_id,
                parent_id=None,
                oca_group=stop_order.oca_group,
            )
        )

    def _detect_unprotected_entry_orphans(self, local_setups: list[dict[str, Any]]) -> None:
        # A-3 (audits 58 S58.1, 66): a crash between the entry-order upsert
        # and the stop placement in order_manager.place_entry_order leaves an
        # active BUY entry at the broker with no stop, while the setup never
        # reached ENTRY_ORDER_PLACED. That signature cannot occur during a
        # normal cycle (place_entry_order never yields between the two), so
        # this only runs once, right after startup.
        for setup in local_setups:
            setup_id = str(setup.get("setup_id") or "")
            if not setup_id:
                continue
            current_setup = self.repository.get_setup(setup_id)
            if current_setup:
                setup = current_setup
            status = str(setup.get("status") or "")
            if status in _TERMINAL_SETUP_STATUSES or status in _REVIEW_LOCKED_SETUP_STATUSES:
                continue
            snapshot = self.repository.protection_snapshot_for_setup(setup_id)
            protection_status = str(snapshot.get("protection_status") or "")
            if protection_status not in _UNPROTECTED_ENTRY_ORPHAN_STATUSES:
                continue
            symbol = str(setup.get("symbol") or "").upper()
            data = {
                "protection_status": protection_status,
                "active_entry_order_id": snapshot.get("active_entry_order_id"),
            }
            if snapshot.get("position_open"):
                message = "Filled entry without protective stop after restart"
                self.repository.update_setup_status(
                    setup_id, SetupStatus.MANUAL_REVIEW_REQUIRED.value, message
                )
                self.event_store.record(
                    EventLevel.CRITICAL,
                    "startup_filled_entry_without_stop",
                    message,
                    setup_id=setup_id,
                    symbol=symbol,
                    data=data,
                )
            else:
                message = "Pending entry without protective stop after restart"
                self.repository.update_setup_status(
                    setup_id, SetupStatus.MANUAL_REVIEW_REQUIRED.value, message
                )
                self.event_store.record(
                    EventLevel.RISK,
                    "startup_pending_entry_without_stop",
                    message,
                    setup_id=setup_id,
                    symbol=symbol,
                    data=data,
                )

    def _repair_frozen_setups(self, local_setups: list[dict[str, Any]]) -> None:
        # Root C (audits 69/70): two non-atomic crash windows leave a setup
        # frozen after a restart even though the broker/local state is
        # otherwise consistent. Both branches here write a normal progression
        # status (never MANUAL_REVIEW_REQUIRED) and are idempotent by
        # construction: the entry branch only fires while status is still
        # ENTRY_FILLED, and the exit branch's target (CLOSED) is terminal and
        # filtered out on the next startup.
        for setup in local_setups:
            setup_id = str(setup.get("setup_id") or "")
            if not setup_id:
                continue
            current_setup = self.repository.get_setup(setup_id)
            if current_setup:
                setup = current_setup
            status = str(setup.get("status") or "")
            if status in _TERMINAL_SETUP_STATUSES:
                continue
            symbol = str(setup.get("symbol") or "").upper()

            if status == SetupStatus.ENTRY_FILLED.value:
                # S58.3: crash after the stop was placed at the broker but
                # before the setup progressed past ENTRY_FILLED. A-3's
                # orphan scan does not cover this signature because a stop
                # IS active -- protection_status is POSITION_OPEN_STOP_ACTIVE,
                # not one of the unprotected-orphan statuses.
                snapshot = self.repository.protection_snapshot_for_setup(setup_id)
                if str(snapshot.get("protection_status") or "") != "POSITION_OPEN_STOP_ACTIVE":
                    continue
                protection_verified = self.progression.has_active_protection(setup_id)
                self.progression.mark_in_position(setup_id, protection_verified=protection_verified)
                if protection_verified:
                    self.event_store.record(
                        EventLevel.SYNC,
                        "startup_frozen_entry_repaired",
                        "Filled entry with active protective stop reconciled to IN_POSITION at startup",
                        setup_id=setup_id,
                        symbol=symbol,
                        data={"previous_status": status},
                    )
                continue

            if status in _FROZEN_EXIT_SETUP_STATUSES:
                # Root A / fenêtre A-1: the sell fill soldered the local
                # position to quantity 0 but the crash landed before
                # _handle_sell_fill wrote the setup to CLOSED. positions is
                # indexed by symbol, not setup_id, so the same-setup check is
                # mandatory: a different setup may since have reused the
                # symbol (audit 70 Q1).
                position = self.repository.get_position(symbol)
                if position is None:
                    continue
                if str(position.get("setup_id") or "") != setup_id:
                    continue
                if int(position["quantity"]) != 0:
                    continue
                self.repository.update_setup_status(
                    setup_id,
                    SetupStatus.CLOSED.value,
                    "Position closed reconciled at startup",
                )
                self.event_store.record(
                    EventLevel.SYNC,
                    "startup_frozen_exit_repaired",
                    "Position closed reconciled at startup",
                    setup_id=setup_id,
                    symbol=symbol,
                    data={"previous_status": status},
                )

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
                        broker_positions=broker_positions,
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
                    broker_positions=broker_positions,
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
        broker_positions: list[BrokerPosition] | None = None,
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
            broker_positions=broker_positions or [],
            broker_executions=broker_executions or [],
        )

    def _update_setup_after_reconciled_order(
        self,
        order: dict[str, Any],
        status: str,
        *,
        broker_positions: list[BrokerPosition] | None = None,
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
            if setup_status in _REVIEW_LOCKED_SETUP_STATUSES:
                self.event_store.record(
                    EventLevel.INFO,
                    "reconciliation_skipped_review_locked",
                    f"Setup left in {setup_status} instead of restoring "
                    f"{target_status} from TWS",
                    setup_id=setup_id,
                    symbol=symbol,
                    data={
                        "order_id": str(order.get("id") or ""),
                        "broker_order_id": order.get("broker_order_id"),
                        "preserved_status": setup_status,
                        "target_status": target_status,
                    },
                )
                return
            if setup_status in _TERMINAL_SETUP_STATUSES:
                status_reason = (
                    f"Broker shows an open {side} order for a terminal setup "
                    f"({setup_status}) — needs manual review"
                )
                self.repository.update_setup_status(
                    setup_id,
                    SetupStatus.MANUAL_REVIEW_REQUIRED.value,
                    "Open order for terminal setup — manual review required",
                    status_reason=status_reason,
                )
                self.event_store.record(
                    EventLevel.WARNING,
                    "reconciliation_terminal_setup_open_order",
                    status_reason,
                    setup_id=setup_id,
                    symbol=symbol,
                    data={
                        "order_id": str(order.get("id") or ""),
                        "broker_order_id": order.get("broker_order_id"),
                        "terminal_status": setup_status,
                        "side": side,
                        "target_status": target_status,
                    },
                )
            return
        if status == OrderStatus.FILLED.value:
            if side == "SELL":
                self._handle_sell_fill(
                    order,
                    setup_id=setup_id,
                    symbol=symbol,
                    broker_positions=broker_positions or [],
                    broker_executions=broker_executions or [],
                )
                return
            if side != "BUY":
                return
            if setup_status not in {
                SetupStatus.ENTRY_ORDER_PLACED.value,
                SetupStatus.ENTRY_PARTIALLY_FILLED.value,
            }:
                logger.debug(
                    "Ignoring FILLED order %s for setup %s in unexpected status %s",
                    order.get("id"),
                    setup_id,
                    setup_status,
                )
                return

            quantity, fill_price = self._resolve_fill_details(
                order,
                symbol=symbol,
                broker_positions=broker_positions or [],
                broker_executions=broker_executions or [],
            )
            if quantity is None or fill_price is None:
                self.repository.update_setup_status(
                    setup_id,
                    SetupStatus.ENTRY_FILLED.value,
                    "Entry order filled",
                )
                self.repository.update_setup_status(
                    setup_id,
                    SetupStatus.MANUAL_REVIEW_REQUIRED.value,
                    "Filled but fill price/quantity unavailable",
                )
                self.event_store.record(
                    EventLevel.CRITICAL,
                    "entry_filled_unknown_fill_details",
                    "Entry filled but fill price/quantity unavailable",
                    setup_id=setup_id,
                    symbol=symbol,
                    data={"order_id": str(order.get("id") or "")},
                )
                return

            order_id = str(order.get("id") or "")
            position = self.progression.record_fill(order_id, setup_id, quantity, fill_price, symbol)
            if position is None:
                return

            protection_verified = self.progression.has_active_protection(setup_id)
            if protection_verified:
                self.progression.mark_in_position(setup_id, protection_verified=protection_verified)
                return
            self.repository.update_setup_status(
                setup_id,
                SetupStatus.MANUAL_REVIEW_REQUIRED.value,
                "Filled without active protective stop",
            )
            self.event_store.record(
                EventLevel.CRITICAL,
                "entry_filled_without_protection",
                "Filled without active protective stop",
                setup_id=setup_id,
                symbol=symbol,
                data={"order_id": order_id},
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

    def _resolve_fill_details(
        self,
        order: dict[str, Any],
        *,
        symbol: str,
        broker_positions: list[BrokerPosition],
        broker_executions: list[BrokerExecution],
    ) -> tuple[int | None, float | None]:
        match = _match_executions_to_order(broker_executions, order)
        if match is not None and match["quantity_matches"]:
            return round(match["quantity"]), match["price"]

        if self.repository.get_position(symbol) is not None:
            return None, None
        broker_position = None
        for position in broker_positions:
            if position.symbol.upper() == symbol:
                broker_position = position
                break
        if broker_position is None:
            return None, None
        order_quantity = order.get("quantity")
        if order_quantity is None or not math.isclose(
            broker_position.quantity, float(order_quantity), rel_tol=1e-9, abs_tol=1e-6
        ):
            return None, None
        return int(order_quantity), broker_position.average_price

    def _handle_sell_fill(
        self,
        order: dict[str, Any],
        *,
        setup_id: str,
        symbol: str,
        broker_positions: list[BrokerPosition],
        broker_executions: list[BrokerExecution],
    ) -> None:
        order_id = str(order.get("id") or "")
        # Root cause A / T1: only the matched-executions branch of
        # _resolve_fill_details is trustworthy for a SELL — its broker-position
        # fallback would hand back an entry cost, not a sale price.
        match = _match_executions_to_order(broker_executions, order)
        if match is None or not match["quantity_matches"]:
            self.repository.update_setup_status(
                setup_id,
                SetupStatus.MANUAL_REVIEW_REQUIRED.value,
                "Sell filled but fill price/quantity unavailable",
            )
            self.event_store.record(
                EventLevel.CRITICAL,
                "sell_filled_unknown_fill_details",
                "Sell filled but fill price/quantity unavailable",
                setup_id=setup_id,
                symbol=symbol,
                data={"order_id": order_id},
            )
            return
        sold_quantity = round(match["quantity"])
        sell_price = match["price"]

        previous = self.repository.get_position(symbol)
        if previous is None:
            self.repository.update_setup_status(
                setup_id,
                SetupStatus.MANUAL_REVIEW_REQUIRED.value,
                "Sell filled but no local position exists",
            )
            self.event_store.record(
                EventLevel.CRITICAL,
                "sell_filled_no_local_position",
                "Sell filled but no local position exists",
                setup_id=setup_id,
                symbol=symbol,
                data={"order_id": order_id, "sold_quantity": sold_quantity, "sell_price": sell_price},
            )
            return

        remaining_quantity = int(previous["quantity"]) - sold_quantity

        broker_position = next(
            (position for position in broker_positions if position.symbol.upper() == symbol),
            None,
        )
        if broker_position is not None and not math.isclose(
            broker_position.quantity, remaining_quantity, rel_tol=1e-9, abs_tol=1e-6
        ):
            self.repository.update_setup_status(
                setup_id,
                SetupStatus.MANUAL_REVIEW_REQUIRED.value,
                "Sell filled but broker position quantity disagrees",
            )
            self.event_store.record(
                EventLevel.CRITICAL,
                "sell_filled_broker_quantity_mismatch",
                "Sell filled but broker-reported quantity disagrees with the local computation",
                setup_id=setup_id,
                symbol=symbol,
                data={
                    "order_id": order_id,
                    "local_remaining_quantity": remaining_quantity,
                    "broker_quantity": broker_position.quantity,
                },
            )
            return

        if self.position_manager is None:
            self.repository.update_setup_status(
                setup_id,
                SetupStatus.MANUAL_REVIEW_REQUIRED.value,
                "Sell filled but position manager is not configured",
            )
            self.event_store.record(
                EventLevel.CRITICAL,
                "sell_filled_no_position_manager",
                "Sell filled but position manager is not configured",
                setup_id=setup_id,
                symbol=symbol,
                data={"order_id": order_id},
            )
            return

        average_price = float(previous["average_price"])
        # current_price must be the real sell fill price (audit 62 Q3): it is
        # what PositionManager._notify_if_closed uses as the exit price to
        # feed the circuit breaker's realized PnL — never a generic quote.
        self.position_manager.open_or_update_position(
            setup_id,
            symbol,
            quantity=remaining_quantity,
            average_price=average_price,
            current_price=sell_price,
            stop_loss=previous.get("current_stop"),
        )

        realized_pnl = round((sell_price - average_price) * sold_quantity, 2)
        if remaining_quantity == 0:
            new_status = SetupStatus.CLOSED.value
            reason = "Position closed on sell fill"
        else:
            new_status = SetupStatus.PARTIAL_EXIT.value
            reason = "Position partially exited on sell fill"
        self.repository.update_setup_status(setup_id, new_status, reason)

        self.event_store.record(
            EventLevel.SYNC,
            "position_closed_on_sell",
            reason,
            setup_id=setup_id,
            symbol=symbol,
            data={
                "order_id": order_id,
                "sold_quantity": sold_quantity,
                "sell_price": sell_price,
                "realized_pnl": realized_pnl,
                "remaining_quantity": remaining_quantity,
            },
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
# Statuses meaning "a human must look at this setup". The SUBMITTED branch of
# _update_setup_after_reconciled_order must never overwrite these with an
# order-restore status (S5b-1, audits 35/36): _TERMINAL_SETUP_STATUSES itself
# is left untouched because it is also read by the position-adoption loop
# (reconciliation.py, ~line 162) and by the CANCELLED branch (~line 538/552),
# both out of scope for this fix.
_REVIEW_LOCKED_SETUP_STATUSES = {
    SetupStatus.MANUAL_REVIEW_REQUIRED.value,
    SetupStatus.ERROR_REQUIRES_MANUAL_REVIEW.value,
}
# protection_snapshot_for_setup statuses meaning "an entry order is active and
# no stop is active" (A-3, audits 58 S58.1/66). STOP_SUBMISSION_FAILED is
# deliberately excluded: it means a stop order was attempted and failed,
# which order_manager already handles synchronously via
# _cancel_parent_for_failed_protection — not the startup-orphan case here,
# where no stop order exists at all.
_UNPROTECTED_ENTRY_ORPHAN_STATUSES = {
    "ENTRY_ORDER_PENDING_WITHOUT_STOP_BLOCKED",
    "POSITION_OPEN_STOP_MISSING_CRITICAL",
}
# root C / fenêtre A-1 (audits 69/70): setup statuses that can be left
# stranded when a fully-sold position's local quantity reaches 0 but the
# crash lands before _handle_sell_fill writes CLOSED.
_FROZEN_EXIT_SETUP_STATUSES = {
    SetupStatus.IN_POSITION.value,
    SetupStatus.MANAGING_POSITION.value,
    SetupStatus.PARTIAL_EXIT.value,
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
