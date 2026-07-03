from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from typing import Any

from app.broker.ib_models import BrokerOrderRequest, BrokerPosition


REPORT_STATE_KEY = "broker_reality"

ACTIVE_LOCAL_ORDER_STATUSES = {"CREATED", "SUBMITTED"}
ORDER_DEPENDENT_SETUP_STATUSES = {
    "ENTRY_ORDER_PLACED",
    "ENTRY_PARTIALLY_FILLED",
    "ENTRY_FILLED",
    "STOP_ORDER_PLACED",
    "STOP_PLACED",
    "IN_POSITION",
    "MANAGING_POSITION",
    "PARTIAL_EXIT",
}
TERMINAL_SETUP_STATUSES = {
    "CLOSED",
    "CANCELLED",
    "EXPIRED",
    "INVALIDATED",
    "ERROR",
    "ERROR_REQUIRES_MANUAL_REVIEW",
}
WORKING_BROKER_STATUSES = {"PENDING_SUBMIT", "TRANSMITTED", "PARTIALLY_FILLED"}
TRANSMITTED_BROKER_STATUSES = {"TRANSMITTED", "PARTIALLY_FILLED"}
TRAILING_STOP_ORDER_TYPES = {
    "TRAIL",
    "TRAIL_LIMIT",
    "TRAIL_LMT",
    "TRAIL_OR_TRAIL_LIMIT",
    "TRAIL_OR_MANAGED_STOP",
}
TERMINAL_BROKER_STATUSES = {
    "FILLED",
    "CANCELLED",
    "REJECTED",
    "INACTIVE_OR_REJECTED",
}
CRITICAL_PROTECTION_STATUSES = {
    "ENTRY_ORDER_WITHOUT_STOP_CRITICAL",
    "POSITION_OPEN_STOP_MISSING_CRITICAL",
    "STOP_MISSING",
    "UNKNOWN_BROKER_STATE",
}


def broker_tracker_config(settings: dict[str, Any] | None) -> dict[str, Any]:
    raw = settings if isinstance(settings, dict) else {}
    configured = raw.get("broker_tracker", {})
    if not isinstance(configured, dict):
        configured = {}
    return {
        "enabled": bool(configured.get("enabled", True)),
        "refresh_seconds": _positive_int(configured.get("refresh_seconds"), 5),
        "stale_after_seconds": _positive_int(configured.get("stale_after_seconds"), 15),
        "block_auto_execution_if_missing": bool(
            configured.get("block_auto_execution_if_missing", True)
        ),
        "block_auto_execution_if_stale": bool(
            configured.get("block_auto_execution_if_stale", True)
        ),
    }


def execution_safety_config(settings: dict[str, Any] | None) -> dict[str, Any]:
    raw = settings if isinstance(settings, dict) else {}
    configured = raw.get("execution_safety", {})
    if not isinstance(configured, dict):
        configured = {}
    return {
        "block_new_entries_if_broker_tracker_stale": bool(
            configured.get("block_new_entries_if_broker_tracker_stale", True)
        ),
        "block_new_entries_if_unprotected_order_exists": bool(
            configured.get("block_new_entries_if_unprotected_order_exists", True)
        ),
        "block_new_entries_if_position_without_stop_exists": bool(
            configured.get("block_new_entries_if_position_without_stop_exists", True)
        ),
        "block_new_entries_if_reconciliation_mismatch": bool(
            configured.get("block_new_entries_if_reconciliation_mismatch", True)
        ),
    }


def normalize_broker_order_status(value: Any, transmit: bool = True) -> str:
    if not transmit:
        return "PREPARED_NOT_TRANSMITTED"
    normalized = str(value or "").strip().upper().replace(" ", "").replace("_", "")
    mapping = {
        "PENDINGSUBMIT": "PENDING_SUBMIT",
        "PRESUBMITTED": "TRANSMITTED",
        "SUBMITTED": "TRANSMITTED",
        "TRANSMITTED": "TRANSMITTED",
        "FILLED": "FILLED",
        "PARTIALLYFILLED": "PARTIALLY_FILLED",
        "PARTIALFILLED": "PARTIALLY_FILLED",
        "PARTIALLY_FILLED": "PARTIALLY_FILLED",
        "CANCELLED": "CANCELLED",
        "APICANCELLED": "CANCELLED",
        "PENDINGCANCEL": "CANCELLED",
        "PENDINGCANCELSUBMIT": "CANCELLED",
        "INACTIVE": "INACTIVE_OR_REJECTED",
        "INACTIVEORREJECTED": "INACTIVE_OR_REJECTED",
        "REJECTED": "REJECTED",
        "CREATED": "PENDING_SUBMIT",
        "ERROR": "REJECTED",
    }
    return mapping.get(normalized, "UNKNOWN")


def build_broker_reality_report(
    *,
    local_setups: list[dict[str, Any]],
    local_orders: list[dict[str, Any]],
    local_positions: list[dict[str, Any]] | None = None,
    broker_orders: list[BrokerOrderRequest],
    broker_positions: list[BrokerPosition],
    account_summary: dict[str, Any] | None = None,
    executions: list[Any] | None = None,
    broker_connected: bool,
    order_query_error: str | None = None,
    position_query_error: str | None = None,
    account_query_error: str | None = None,
    settings: dict[str, Any] | None = None,
    now: str | None = None,
) -> dict[str, Any]:
    tracker = broker_tracker_config(settings)
    safety = execution_safety_config(settings)
    sync_at = now or _utc_now_iso()
    channels = _channel_statuses(
        order_query_error=order_query_error,
        position_query_error=position_query_error,
        account_query_error=account_query_error,
    )
    positions_by_symbol = {
        str(position.symbol or "").upper(): position
        for position in broker_positions
        if str(position.symbol or "").strip()
    }
    local_positions_by_symbol = {
        str(position.get("symbol") or "").upper(): position
        for position in (local_positions or [])
        if str(position.get("symbol") or "").strip()
    }
    orders_by_setup: dict[str, list[dict[str, Any]]] = {}
    for order in local_orders:
        orders_by_setup.setdefault(str(order.get("setup_id") or ""), []).append(order)

    used_broker_order_ids: set[int] = set()
    used_broker_position_symbols: set[str] = set()
    rows: list[dict[str, Any]] = []
    active_setups = [
        setup
        for setup in local_setups
        if str(setup.get("status") or "") not in TERMINAL_SETUP_STATUSES
    ]
    for setup in active_setups:
        symbol = str(setup.get("symbol") or "").upper()
        broker_position = positions_by_symbol.get(symbol)
        row = _broker_reality_row(
            setup=setup,
            local_orders=orders_by_setup.get(str(setup.get("setup_id") or ""), []),
            broker_orders=broker_orders,
            broker_position=broker_position,
            local_position=local_positions_by_symbol.get(symbol),
            broker_connected=broker_connected,
            sync_at=sync_at,
        )
        used_broker_order_ids.update(row.pop("_used_broker_order_ids", set()))
        if broker_position is not None and _position_quantity(broker_position) != 0:
            used_broker_position_symbols.add(symbol)
        rows.append(row)

    known_setup_symbols = {
        str(setup.get("symbol") or "").upper()
        for setup in active_setups
        if str(setup.get("symbol") or "").strip()
    }
    for symbol, broker_position in sorted(positions_by_symbol.items()):
        if symbol in used_broker_position_symbols or _position_quantity(broker_position) == 0:
            continue
        row = _orphan_broker_position_row(
            broker_position,
            broker_orders=broker_orders,
            local_position=local_positions_by_symbol.get(symbol),
            broker_connected=broker_connected,
            sync_at=sync_at,
        )
        used_broker_order_ids.update(row.pop("_used_broker_order_ids", set()))
        rows.append(row)

    for broker_order in broker_orders:
        if id(broker_order) in used_broker_order_ids:
            continue
        symbol = str(broker_order.symbol or "").upper()
        rows.append(
            _orphan_broker_order_row(
                broker_order,
                broker_connected=broker_connected,
                sync_at=sync_at,
            )
        )

    blocking_reasons = _global_blocking_reasons(
        rows,
        safety=safety,
        broker_connected=broker_connected,
    )
    blocking_reasons.extend(_channel_blocking_reasons(channels))
    broker_tracker_status = "OK" if broker_connected else "DISCONNECTED"
    if not tracker["enabled"]:
        broker_tracker_status = "DISABLED"
    pnl = _pnl_snapshot(account_summary, sync_at, broker_positions)
    if channels["account"]["status"] != "OK":
        pnl["status"] = "STALE"
        pnl["sync_status"] = "ERROR"
        pnl["reason"] = "BROKER_ACCOUNT_QUERY_FAILED"
        pnl["warning"] = "P&L stale - account/pnl query failed"
    remaining_risk = _remaining_risk_summary(rows)
    orders_channel_ok = channels["orders"]["status"] == "OK"
    positions_channel_ok = channels["positions"]["status"] == "OK"
    report = {
        "broker_tracker_status": broker_tracker_status,
        "broker_sync_status": broker_tracker_status,
        "broker_last_sync_at": sync_at,
        "broker_sync_age_seconds": 0,
        "stale_after_seconds": tracker["stale_after_seconds"],
        "refresh_seconds": tracker["refresh_seconds"],
        "broker_connected": bool(broker_connected),
        "channels": channels,
        "auto_execution_blocked": bool(blocking_reasons),
        "blocking_reasons": blocking_reasons,
        "mismatch_count": len([row for row in rows if row["reconciliation_status"] == "MISMATCH"]),
        "critical_count": len([row for row in rows if row.get("critical")]),
        "remaining_risk": remaining_risk["total"],
        "remaining_risk_status": remaining_risk["status"],
        "remaining_risk_reason": remaining_risk["reason"],
        "unprotected_positions": remaining_risk["unprotected_positions"],
        "unprotected_orders": remaining_risk["unprotected_orders"],
        "active_stop_orders": remaining_risk["active_stop_orders"],
        "broker_positions_count": (
            len([position for position in broker_positions if _position_quantity(position) != 0])
            if positions_channel_ok
            else None
        ),
        "broker_active_orders": (
            _broker_order_count(broker_orders, WORKING_BROKER_STATUSES) if orders_channel_ok else None
        ),
        "broker_prepared_not_transmitted_orders": (
            _broker_order_count(broker_orders, {"PREPARED_NOT_TRANSMITTED"})
            if orders_channel_ok
            else None
        ),
        "broker_cancelled_orders": (
            _broker_order_count(broker_orders, {"CANCELLED"}) if orders_channel_ok else None
        ),
        "broker_filled_orders": (
            _broker_order_count(broker_orders, {"FILLED"}) if orders_channel_ok else None
        ),
        "local_positions_count": len(
            [
                position
                for position in (local_positions or [])
                if _dict_number(position, "quantity") not in (None, 0)
            ]
        ),
        "local_active_orders_count": len(
            [
                order
                for order in local_orders
                if str(order.get("status") or "") in ACTIVE_LOCAL_ORDER_STATUSES
            ]
        ),
        "rows": rows,
        "pnl": pnl,
        "execution_count": len(executions or []),
        "safety_gate": build_safety_gate(blocking_reasons),
    }
    return freshen_broker_reality_report(report, settings=settings, now=sync_at)


def freshen_broker_reality_report(
    report: dict[str, Any] | None,
    *,
    settings: dict[str, Any] | None = None,
    now: str | None = None,
) -> dict[str, Any]:
    tracker = broker_tracker_config(settings)
    safety = execution_safety_config(settings)
    current = deepcopy(report or {})
    sync_at = current.get("broker_last_sync_at")
    age = _age_seconds(sync_at, now=now)
    connected = bool(current.get("broker_connected", False))
    previous_status = str(current.get("broker_tracker_status") or "")
    stale = (
        tracker["enabled"]
        and connected
        and age is not None
        and age > tracker["stale_after_seconds"]
    )
    if not tracker["enabled"]:
        status = "DISABLED"
    elif not connected or previous_status == "DISCONNECTED":
        status = "DISCONNECTED"
    elif age is None:
        status = "NOT_RUNNING"
    elif stale:
        status = "STALE"
    else:
        status = "OK"

    current["broker_tracker_status"] = status
    current["broker_sync_status"] = status
    current["broker_sync_age_seconds"] = age
    current["stale_after_seconds"] = tracker["stale_after_seconds"]
    current["refresh_seconds"] = tracker["refresh_seconds"]

    for row in current.get("rows", []) or []:
        if not isinstance(row, dict):
            continue
        row["broker_sync_status"] = status
        row["broker_sync_age_seconds"] = age
        row["broker_last_sync_at"] = sync_at
        if status == "STALE":
            row["warning"] = "Broker state not fresh. Auto execution blocked."

    pnl = current.get("pnl")
    if isinstance(pnl, dict):
        has_values = any(
            pnl.get(field) is not None
            for field in ("daily_pnl", "unrealized_pnl", "realized_pnl")
        )
        pnl["age_seconds"] = age
        if status == "OK" and has_values:
            pnl["status"] = "OK"
            pnl["sync_status"] = "OK"
            pnl.pop("warning", None)
            pnl.pop("reason", None)
        else:
            pnl["status"] = "STALE"
            pnl["sync_status"] = status if status != "OK" else "STALE"
            pnl["reason"] = (
                "NO_RECENT_TWS_PNL_SNAPSHOT"
                if status == "OK"
                else f"BROKER_TRACKER_{status}"
            )
            pnl["warning"] = "P&L stale - broker data not fresh"

    remaining_risk = _remaining_risk_summary(
        [row for row in current.get("rows", []) if isinstance(row, dict)]
    )
    current["remaining_risk"] = remaining_risk["total"]
    current["remaining_risk_status"] = remaining_risk["status"]
    current["remaining_risk_reason"] = remaining_risk["reason"]
    current["unprotected_positions"] = remaining_risk["unprotected_positions"]
    current["unprotected_orders"] = remaining_risk["unprotected_orders"]
    current["active_stop_orders"] = remaining_risk["active_stop_orders"]

    blocking_reasons = _global_blocking_reasons(
        [row for row in current.get("rows", []) if isinstance(row, dict)],
        safety=safety,
        broker_connected=connected,
    )
    blocking_reasons.extend(_channel_blocking_reasons(current.get("channels")))
    if (
        status in {"STALE", "NOT_RUNNING"}
        and (
            tracker["block_auto_execution_if_stale"]
            if status == "STALE"
            else tracker["block_auto_execution_if_missing"]
        )
        and safety["block_new_entries_if_broker_tracker_stale"]
    ):
        blocking_reasons.append(
            "BROKER_TRACKER_NOT_RUNNING"
            if status == "NOT_RUNNING"
            else "BROKER_TRACKER_STALE"
        )
    if status == "DISCONNECTED":
        blocking_reasons.append("TWS_DISCONNECTED")
    current["blocking_reasons"] = _dedupe(blocking_reasons)
    current["auto_execution_blocked"] = bool(current["blocking_reasons"])
    current["safety_gate"] = build_safety_gate(current["blocking_reasons"])
    current["mismatch_count"] = len(
        [
            row
            for row in current.get("rows", [])
            if isinstance(row, dict) and row.get("reconciliation_status") == "MISMATCH"
        ]
    )
    current["critical_count"] = len(
        [row for row in current.get("rows", []) if isinstance(row, dict) and row.get("critical")]
    )
    return current


def broker_reality_blocking_reasons(
    repository: Any,
    settings: dict[str, Any] | None,
    *,
    now: str | None = None,
) -> list[str]:
    tracker = broker_tracker_config(settings)
    if not tracker["enabled"]:
        return []
    report = repository.get_bot_state(REPORT_STATE_KEY, {})
    if not isinstance(report, dict) or not report.get("broker_last_sync_at"):
        safety = execution_safety_config(settings)
        if (
            tracker["block_auto_execution_if_stale"]
            and safety["block_new_entries_if_broker_tracker_stale"]
        ):
            return ["BROKER_TRACKER_NOT_RUNNING"]
        return []
    fresh = freshen_broker_reality_report(report, settings=settings, now=now)
    if not fresh.get("auto_execution_blocked"):
        return []
    return [str(item) for item in fresh.get("blocking_reasons", []) if str(item or "")]


def _broker_reality_row(
    *,
    setup: dict[str, Any],
    local_orders: list[dict[str, Any]],
    broker_orders: list[BrokerOrderRequest],
    broker_position: BrokerPosition | None,
    local_position: dict[str, Any] | None,
    broker_connected: bool,
    sync_at: str,
) -> dict[str, Any]:
    setup_id = str(setup.get("setup_id") or "")
    symbol = str(setup.get("symbol") or "").upper()
    config = setup.get("config", {}) if isinstance(setup.get("config"), dict) else {}
    direction = str(config.get("direction", "long") or "long").strip().lower()
    entry_side = "SELL" if direction == "short" else "BUY"
    stop_side = "BUY" if direction == "short" else "SELL"
    local_entry = _latest_local_order(local_orders, side=entry_side, stop=False)
    local_stop = _latest_local_order(local_orders, side=stop_side, stop=True)
    broker_entry = _matching_broker_entry(
        local_entry,
        broker_orders,
        symbol=symbol,
        side=entry_side,
    )
    broker_stop = _matching_broker_stop(
        local_stop,
        broker_orders,
        symbol=symbol,
        side=stop_side,
        broker_entry=broker_entry,
        local_entry=local_entry,
    )
    used_ids = {id(order) for order in (broker_entry, broker_stop) if order is not None}

    local_status = str(setup.get("status") or "")
    entry_status = (
        _broker_order_status(broker_entry)
        if broker_entry is not None
        else "NO_BROKER_ORDER"
        if local_entry is not None or local_status in ORDER_DEPENDENT_SETUP_STATUSES
        else "NO_ENTRY_ORDER"
    )
    stop_status = _broker_order_status(broker_stop) if broker_stop is not None else "MISSING"
    position_qty = _position_quantity(broker_position)
    market_price = _position_number(broker_position, "market_price") or _position_number(
        broker_position,
        "current_price",
    )
    active_stop_price = _active_stop_price(broker_stop)
    remaining_risk_status, remaining_risk = _remaining_risk_for_position(
        quantity=position_qty,
        current_price=market_price,
        active_stop_price=active_stop_price,
        stop_status=stop_status,
    )
    has_local_position = bool(
        local_position is not None and (_dict_number(local_position, "quantity") or 0) != 0
    )
    configured_stop = _configured_stop(config)
    protection_status = _protection_status(
        broker_connected=broker_connected,
        position_qty=position_qty,
        entry_status=entry_status,
        stop_status=stop_status,
        has_broker_entry=broker_entry is not None,
        has_broker_stop=broker_stop is not None,
        configured_stop=configured_stop,
        local_status=local_status,
    )
    action_required, mismatch_reasons = _row_action_and_mismatch(
        broker_connected=broker_connected,
        local_status=local_status,
        entry_status=entry_status,
        stop_status=stop_status,
        protection_status=protection_status,
        has_local_entry=local_entry is not None,
        has_broker_entry=broker_entry is not None,
        has_local_position=has_local_position,
        position_qty=position_qty,
    )
    reconciliation_status = "MISMATCH" if mismatch_reasons else "OK"
    critical = _row_is_critical(
        mismatch_reasons=mismatch_reasons,
        protection_status=protection_status,
        entry_status=entry_status,
    )
    return {
        "symbol": symbol,
        "setup_id": setup_id,
        "local_setup_status": local_status,
        "local_decision": "",
        "local_order_intent": _local_order_intent(local_entry, local_stop),
        "local_quantity": _local_quantity(local_entry, local_stop),
        "local_entry_trigger": _first_present(
            _dict_number(local_entry, "trigger_price"),
            _dict_number(local_entry, "limit_price"),
        ),
        "configured_stop_price": configured_stop,
        "active_stop_price": active_stop_price,
        "current_price": market_price,
        "average_price": _position_number(broker_position, "average_price"),
        "remaining_risk": remaining_risk,
        "remaining_risk_status": remaining_risk_status,
        "stop_distance": _stop_distance(market_price, active_stop_price),
        "trailing_stop": _is_trailing_stop_order(broker_stop),
        "broker_entry_order_status": entry_status,
        "broker_stop_order_status": stop_status,
        "broker_entry_status": entry_status,
        "broker_stop_status": stop_status,
        "broker_order_status": entry_status,
        "broker_position_status": "OPEN_POSITION" if position_qty != 0 else "NO_POSITION",
        "local_position_status": "OPEN_POSITION" if has_local_position else "NO_POSITION",
        "is_transmitted": entry_status in TRANSMITTED_BROKER_STATUSES,
        "is_active": entry_status in WORKING_BROKER_STATUSES,
        "is_filled": entry_status == "FILLED",
        "is_cancelled": entry_status == "CANCELLED",
        "is_rejected": entry_status in {"REJECTED", "INACTIVE_OR_REJECTED"},
        "filled_quantity": _order_number(broker_entry, "filled_quantity"),
        "remaining_quantity": _order_number(broker_entry, "remaining_quantity"),
        "position_quantity": position_qty,
        "position_qty": position_qty,
        "average_cost": _position_number(broker_position, "average_price"),
        "market_price": market_price,
        "unrealized_pnl": _position_unrealized_pnl(broker_position),
        "realized_pnl": _position_number(broker_position, "realized_pnl"),
        "daily_pnl": _position_number(broker_position, "daily_pnl"),
        "pnl_source": "TWS" if broker_position is not None else "TWS",
        "active_stop_order_id": _order_identifier(broker_stop),
        "broker_stop_order_id": _order_identifier(broker_stop),
        "broker_entry_order_id": _order_identifier(broker_entry),
        "protection_status": protection_status,
        "reconciliation_status": reconciliation_status,
        "mismatch": reconciliation_status == "MISMATCH",
        "mismatch_reasons": mismatch_reasons,
        "critical": critical,
        "auto_execution_blocked": critical,
        "action_required": action_required,
        "broker_sync_status": "OK" if broker_connected else "DISCONNECTED",
        "broker_sync_age_seconds": 0,
        "broker_last_sync_at": sync_at,
        "_used_broker_order_ids": used_ids,
    }


def _orphan_broker_position_row(
    broker_position: BrokerPosition,
    *,
    broker_orders: list[BrokerOrderRequest],
    local_position: dict[str, Any] | None,
    broker_connected: bool,
    sync_at: str,
) -> dict[str, Any]:
    symbol = str(broker_position.symbol or "").upper()
    position_qty = _position_quantity(broker_position)
    entry_side = "BUY" if position_qty > 0 else "SELL"
    stop_side = "SELL" if position_qty > 0 else "BUY"
    broker_entry = _matching_broker_entry(
        None,
        broker_orders,
        symbol=symbol,
        side=entry_side,
    )
    broker_stop = _matching_broker_stop(
        None,
        broker_orders,
        symbol=symbol,
        side=stop_side,
        broker_entry=broker_entry,
        local_entry=None,
    )
    used_ids = {id(order) for order in (broker_entry, broker_stop) if order is not None}
    entry_status = _broker_order_status(broker_entry) if broker_entry is not None else "FILLED"
    stop_status = _broker_order_status(broker_stop) if broker_stop is not None else "MISSING"
    market_price = _position_number(broker_position, "market_price") or _position_number(
        broker_position,
        "current_price",
    )
    active_stop_price = _active_stop_price(broker_stop)
    remaining_risk_status, remaining_risk = _remaining_risk_for_position(
        quantity=position_qty,
        current_price=market_price,
        active_stop_price=active_stop_price,
        stop_status=stop_status,
    )
    stop_working = stop_status in WORKING_BROKER_STATUSES
    protection_status = (
        "POSITION_OPEN_STOP_ACTIVE"
        if broker_connected and stop_working
        else "POSITION_OPEN_STOP_MISSING_CRITICAL"
        if broker_connected
        else "UNKNOWN_BROKER_STATE"
    )
    mismatch_reasons = ["BROKER_POSITION_WITHOUT_ACTIVE_LOCAL_SETUP"]
    if local_position is None or (_dict_number(local_position, "quantity") or 0) == 0:
        mismatch_reasons.append("MISMATCH_POSITION_COUNT")
    if protection_status == "POSITION_OPEN_STOP_MISSING_CRITICAL":
        mismatch_reasons.append("BROKER_POSITION_WITHOUT_STOP")
    if not broker_connected:
        mismatch_reasons.append("TWS_DISCONNECTED")
    mismatch_reasons = _dedupe(mismatch_reasons)
    if "BROKER_POSITION_WITHOUT_STOP" in mismatch_reasons:
        action_required = (
            "Position is open without an active TWS stop. Create stop-loss or flatten manually."
        )
    else:
        action_required = (
            "Broker position exists in TWS without an active local setup. Adopt or link it."
        )
    critical = protection_status in CRITICAL_PROTECTION_STATUSES
    return {
        "symbol": symbol,
        "setup_id": (local_position or {}).get("setup_id") or f"broker:{symbol}",
        "local_setup_status": "NO_ACTIVE_LOCAL_SETUP",
        "local_decision": "",
        "local_order_intent": "ADOPT_OR_LINK_BROKER_POSITION",
        "local_quantity": _dict_number(local_position, "quantity"),
        "local_entry_trigger": None,
        "configured_stop_price": _dict_number(local_position, "current_stop"),
        "active_stop_price": active_stop_price,
        "current_price": market_price,
        "average_price": _position_number(broker_position, "average_price"),
        "remaining_risk": remaining_risk,
        "remaining_risk_status": remaining_risk_status,
        "stop_distance": _stop_distance(market_price, active_stop_price),
        "trailing_stop": _is_trailing_stop_order(broker_stop),
        "broker_entry_order_status": entry_status,
        "broker_stop_order_status": stop_status,
        "broker_entry_status": entry_status,
        "broker_stop_status": stop_status,
        "broker_order_status": entry_status,
        "broker_position_status": "OPEN_POSITION",
        "local_position_status": "OPEN_POSITION" if local_position else "NO_POSITION",
        "is_transmitted": entry_status in TRANSMITTED_BROKER_STATUSES,
        "is_active": entry_status in WORKING_BROKER_STATUSES,
        "is_filled": entry_status == "FILLED",
        "is_cancelled": entry_status == "CANCELLED",
        "is_rejected": entry_status in {"REJECTED", "INACTIVE_OR_REJECTED"},
        "filled_quantity": _order_number(broker_entry, "filled_quantity"),
        "remaining_quantity": _order_number(broker_entry, "remaining_quantity"),
        "position_quantity": position_qty,
        "position_qty": position_qty,
        "average_cost": _position_number(broker_position, "average_price"),
        "market_price": market_price,
        "unrealized_pnl": _position_unrealized_pnl(broker_position),
        "realized_pnl": _position_number(broker_position, "realized_pnl"),
        "daily_pnl": _position_number(broker_position, "daily_pnl"),
        "pnl_source": "TWS",
        "active_stop_order_id": _order_identifier(broker_stop),
        "broker_stop_order_id": _order_identifier(broker_stop),
        "broker_entry_order_id": _order_identifier(broker_entry),
        "protection_status": protection_status,
        "reconciliation_status": "MISMATCH",
        "mismatch": True,
        "mismatch_reasons": mismatch_reasons,
        "critical": critical,
        "auto_execution_blocked": critical,
        "action_required": action_required,
        "broker_sync_status": "OK" if broker_connected else "DISCONNECTED",
        "broker_sync_age_seconds": 0,
        "broker_last_sync_at": sync_at,
        "_used_broker_order_ids": used_ids,
    }


def _orphan_broker_order_row(
    broker_order: BrokerOrderRequest,
    *,
    broker_connected: bool,
    sync_at: str,
) -> dict[str, Any]:
    side = str(broker_order.side or "").upper()
    status = _broker_order_status(broker_order)
    is_stop = _is_stop_broker_order(broker_order, side)
    return {
        "symbol": str(broker_order.symbol or "").upper(),
        "setup_id": "broker",
        "local_setup_status": "NO_LOCAL_SETUP",
        "local_decision": "",
        "local_order_intent": "UNKNOWN_EXTERNAL_BROKER_ORDER",
        "local_quantity": None,
        "local_entry_trigger": None,
        "configured_stop_price": None,
        "active_stop_price": _active_stop_price(broker_order) if is_stop else None,
        "current_price": None,
        "average_price": None,
        "remaining_risk": None,
        "remaining_risk_status": "NO_OPEN_POSITION",
        "stop_distance": None,
        "trailing_stop": _is_trailing_stop_order(broker_order) if is_stop else False,
        "broker_entry_order_status": "NO_ENTRY_ORDER" if is_stop else status,
        "broker_stop_order_status": status if is_stop else "MISSING",
        "broker_entry_status": "NO_ENTRY_ORDER" if is_stop else status,
        "broker_stop_status": status if is_stop else "MISSING",
        "broker_order_status": status,
        "broker_position_status": "NO_POSITION",
        "local_position_status": "NO_POSITION",
        "is_transmitted": status in TRANSMITTED_BROKER_STATUSES,
        "is_active": status in WORKING_BROKER_STATUSES,
        "is_filled": status == "FILLED",
        "is_cancelled": status == "CANCELLED",
        "is_rejected": status in {"REJECTED", "INACTIVE_OR_REJECTED"},
        "filled_quantity": _order_number(broker_order, "filled_quantity"),
        "remaining_quantity": _order_number(broker_order, "remaining_quantity"),
        "position_quantity": 0,
        "position_qty": 0,
        "average_cost": None,
        "market_price": None,
        "unrealized_pnl": None,
        "realized_pnl": None,
        "daily_pnl": None,
        "pnl_source": "TWS",
        "active_stop_order_id": _order_identifier(broker_order) if is_stop else None,
        "broker_stop_order_id": _order_identifier(broker_order) if is_stop else None,
        "broker_entry_order_id": None if is_stop else _order_identifier(broker_order),
        "protection_status": "UNKNOWN_BROKER_STATE",
        "reconciliation_status": "MISMATCH",
        "mismatch": True,
        "mismatch_reasons": ["BROKER_ORDER_WITHOUT_LOCAL_SETUP"],
        "critical": True,
        "auto_execution_blocked": True,
        "action_required": "Broker order exists in TWS without a linked local setup.",
        "broker_sync_status": "OK" if broker_connected else "DISCONNECTED",
        "broker_sync_age_seconds": 0,
        "broker_last_sync_at": sync_at,
    }


def _protection_status(
    *,
    broker_connected: bool,
    position_qty: int,
    entry_status: str,
    stop_status: str,
    has_broker_entry: bool,
    has_broker_stop: bool,
    configured_stop: float | None,
    local_status: str,
) -> str:
    if not broker_connected:
        return "UNKNOWN_BROKER_STATE"
    stop_working = stop_status in WORKING_BROKER_STATUSES
    entry_working = entry_status in WORKING_BROKER_STATUSES
    if position_qty != 0:
        return "POSITION_OPEN_STOP_ACTIVE" if stop_working else "POSITION_OPEN_STOP_MISSING_CRITICAL"
    if entry_working and stop_working:
        return "PROTECTED"
    if entry_working:
        if stop_status == "PREPARED_NOT_TRANSMITTED":
            return "STOP_PREPARED_NOT_TRANSMITTED"
        return "ENTRY_ORDER_WITHOUT_STOP_CRITICAL"
    if has_broker_entry and entry_status == "PREPARED_NOT_TRANSMITTED":
        if has_broker_stop and stop_status == "PREPARED_NOT_TRANSMITTED":
            return "STOP_PREPARED_NOT_TRANSMITTED"
        return "STOP_MISSING"
    if configured_stop is not None and (
        local_status in ORDER_DEPENDENT_SETUP_STATUSES or has_broker_entry
    ):
        return "STOP_MISSING"
    return "NO_POSITION_NO_ENTRY_ORDER"


def _row_action_and_mismatch(
    *,
    broker_connected: bool,
    local_status: str,
    entry_status: str,
    stop_status: str,
    protection_status: str,
    has_local_entry: bool,
    has_broker_entry: bool,
    has_local_position: bool,
    position_qty: int,
) -> tuple[str, list[str]]:
    reasons: list[str] = []
    if not broker_connected:
        reasons.append("TWS_DISCONNECTED")
    if local_status in ORDER_DEPENDENT_SETUP_STATUSES and not has_broker_entry:
        reasons.append("LOCAL_ACTIVE_BROKER_ORDER_MISSING")
    if has_local_entry and entry_status == "NO_BROKER_ORDER":
        reasons.append("LOCAL_ORDER_MISSING_IN_TWS")
    if entry_status == "PREPARED_NOT_TRANSMITTED":
        reasons.append("BROKER_ORDER_PREPARED_NOT_TRANSMITTED")
    if entry_status in {"REJECTED", "INACTIVE_OR_REJECTED"}:
        reasons.append("BROKER_ORDER_REJECTED")
    if protection_status in {
        "ENTRY_ORDER_WITHOUT_STOP_CRITICAL",
        "STOP_MISSING",
        "STOP_PREPARED_NOT_TRANSMITTED",
    } and (local_status in ORDER_DEPENDENT_SETUP_STATUSES or has_broker_entry):
        reasons.append("BROKER_STOP_NOT_ACTIVE")
    if position_qty != 0 and protection_status == "POSITION_OPEN_STOP_MISSING_CRITICAL":
        reasons.append("BROKER_POSITION_WITHOUT_STOP")
    if stop_status in {"REJECTED", "INACTIVE_OR_REJECTED"}:
        reasons.append("BROKER_STOP_REJECTED")
    if position_qty != 0 and not has_local_position:
        reasons.append("MISMATCH_POSITION_COUNT")
    if position_qty == 0 and has_local_position:
        reasons.append("LOCAL_POSITION_MISSING_IN_TWS")
    reasons = _dedupe(reasons)
    if not reasons:
        return "OK", []
    if "TWS_DISCONNECTED" in reasons:
        return "Connect TWS before trusting order, stop or P&L state.", reasons
    if "BROKER_POSITION_WITHOUT_STOP" in reasons:
        return (
            "Position is open without an active TWS stop. Create stop-loss or flatten manually.",
            reasons,
        )
    if "BROKER_ORDER_PREPARED_NOT_TRANSMITTED" in reasons:
        return "Do not mark as active. Transmit manually in TWS or cancel the prepared order.", reasons
    if "BROKER_STOP_NOT_ACTIVE" in reasons:
        return "Stop-loss missing or not transmitted in TWS. Auto execution blocked.", reasons
    if "MISMATCH_POSITION_COUNT" in reasons:
        return "Broker position exists in TWS but local position state is missing.", reasons
    if "LOCAL_POSITION_MISSING_IN_TWS" in reasons:
        return "Local position exists but TWS has no matching open position.", reasons
    if "LOCAL_ACTIVE_BROKER_ORDER_MISSING" in reasons:
        return "Local setup says active, but TWS has no matching working order.", reasons
    if "BROKER_ORDER_REJECTED" in reasons:
        return "Broker rejected or inactivated the order. Manual review required.", reasons
    return "Mismatch detected. Manual review required.", reasons


def _row_is_critical(
    *,
    mismatch_reasons: list[str],
    protection_status: str,
    entry_status: str,
) -> bool:
    if protection_status in CRITICAL_PROTECTION_STATUSES:
        return True
    critical_reasons = {
        "TWS_DISCONNECTED",
        "LOCAL_ACTIVE_BROKER_ORDER_MISSING",
        "LOCAL_ORDER_MISSING_IN_TWS",
        "BROKER_ORDER_PREPARED_NOT_TRANSMITTED",
        "BROKER_ORDER_REJECTED",
        "BROKER_STOP_NOT_ACTIVE",
        "BROKER_POSITION_WITHOUT_STOP",
        "BROKER_STOP_REJECTED",
    }
    return bool(critical_reasons.intersection(mismatch_reasons)) or entry_status in {
        "REJECTED",
        "INACTIVE_OR_REJECTED",
    }


def _global_blocking_reasons(
    rows: list[dict[str, Any]],
    *,
    safety: dict[str, Any],
    broker_connected: bool,
) -> list[str]:
    reasons: list[str] = []
    if not broker_connected:
        reasons.append("TWS_DISCONNECTED")
    for row in rows:
        if (
            safety["block_new_entries_if_unprotected_order_exists"]
            and row.get("protection_status") == "ENTRY_ORDER_WITHOUT_STOP_CRITICAL"
        ):
            reasons.append("ENTRY_ORDER_ACTIVE_WITHOUT_STOP_LOSS")
        if (
            safety["block_new_entries_if_position_without_stop_exists"]
            and row.get("protection_status") == "POSITION_OPEN_STOP_MISSING_CRITICAL"
        ):
            reasons.append("POSITION_OPEN_WITHOUT_STOP_LOSS")
        if (
            safety["block_new_entries_if_reconciliation_mismatch"]
            and row.get("reconciliation_status") == "MISMATCH"
        ):
            reasons.append("RECONCILIATION_MISMATCH")
        if row.get("broker_entry_status") in {"REJECTED", "INACTIVE_OR_REJECTED"}:
            reasons.append("TWS_ORDER_REJECTED")
    return _dedupe(reasons)


SAFETY_GATE_CONDITIONS: dict[str, tuple[str, ...]] = {
    "tws_disconnected": ("TWS_DISCONNECTED",),
    "broker_report_stale": ("BROKER_TRACKER_STALE",),
    "broker_tracker_missing": ("BROKER_TRACKER_NOT_RUNNING",),
    "broker_query_partial_failure": (
        "BROKER_ORDERS_QUERY_FAILED",
        "BROKER_POSITIONS_QUERY_FAILED",
        "BROKER_ACCOUNT_QUERY_FAILED",
    ),
    "critical_mismatch": ("RECONCILIATION_MISMATCH", "TWS_ORDER_REJECTED"),
    "position_without_stop": ("POSITION_OPEN_WITHOUT_STOP_LOSS",),
    "entry_order_without_stop": ("ENTRY_ORDER_ACTIVE_WITHOUT_STOP_LOSS",),
}


def build_safety_gate(blocking_reasons: list[str]) -> dict[str, Any]:
    reasons = {str(reason) for reason in blocking_reasons if str(reason or "")}
    conditions = {
        name: any(code in reasons for code in codes)
        for name, codes in SAFETY_GATE_CONDITIONS.items()
    }
    return {
        "auto_execution_blocked": bool(reasons),
        "blocking_reasons": [str(reason) for reason in blocking_reasons if str(reason or "")],
        "conditions": conditions,
    }


def _channel_statuses(
    *,
    order_query_error: str | None,
    position_query_error: str | None,
    account_query_error: str | None,
) -> dict[str, dict[str, Any]]:
    return {
        "orders": _channel_status(order_query_error),
        "positions": _channel_status(position_query_error),
        "account": _channel_status(account_query_error),
    }


def _channel_status(error: str | None) -> dict[str, Any]:
    if error:
        return {"status": "ERROR", "error": str(error)}
    return {"status": "OK", "error": None}


_CHANNEL_BLOCKING_REASON_BY_NAME = {
    "orders": "BROKER_ORDERS_QUERY_FAILED",
    "positions": "BROKER_POSITIONS_QUERY_FAILED",
    "account": "BROKER_ACCOUNT_QUERY_FAILED",
}


def _channel_blocking_reasons(channels: dict[str, Any] | None) -> list[str]:
    if not isinstance(channels, dict):
        return []
    reasons: list[str] = []
    for name, reason in _CHANNEL_BLOCKING_REASON_BY_NAME.items():
        channel = channels.get(name)
        if isinstance(channel, dict) and channel.get("status") != "OK":
            reasons.append(reason)
    return reasons


def orders_broker_truth_overlay(
    orders: list[dict[str, Any]],
    report: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    report = report if isinstance(report, dict) else {}
    broker_connected = bool(report.get("broker_connected"))
    rows_by_setup = {
        str(row.get("setup_id") or ""): row
        for row in report.get("rows", []) or []
        if isinstance(row, dict)
    }
    overlaid: list[dict[str, Any]] = []
    for order in orders:
        row = dict(order)
        reality_row = rows_by_setup.get(str(order.get("setup_id") or "")) if broker_connected else None
        if reality_row is not None:
            is_stop = _local_order_is_stop(order)
            row["source"] = "BROKER_REALITY"
            row["broker_verified"] = True
            row["broker_status"] = (
                reality_row.get("broker_stop_order_status")
                if is_stop
                else reality_row.get("broker_entry_order_status")
            )
            row["broker_sync_age_seconds"] = reality_row.get("broker_sync_age_seconds")
        else:
            row["source"] = "LOCAL_ONLY"
            row["broker_verified"] = False
            row["broker_status"] = None
            row["broker_sync_age_seconds"] = None
        overlaid.append(row)
    return overlaid


def positions_broker_truth_overlay(
    positions: list[dict[str, Any]],
    report: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    report = report if isinstance(report, dict) else {}
    broker_connected = bool(report.get("broker_connected"))
    rows_by_symbol: dict[str, dict[str, Any]] = {}
    for row in report.get("rows", []) or []:
        symbol = str(row.get("symbol") or "").upper() if isinstance(row, dict) else ""
        if symbol:
            rows_by_symbol[symbol] = row
    overlaid: list[dict[str, Any]] = []
    for position in positions:
        row = dict(position)
        symbol = str(position.get("symbol") or "").upper()
        reality_row = rows_by_symbol.get(symbol) if broker_connected else None
        if reality_row is not None:
            row["source"] = "BROKER_REALITY"
            row["broker_verified"] = True
            row["broker_position_quantity"] = reality_row.get("position_quantity")
            row["broker_market_price"] = reality_row.get("market_price")
            row["protection_status"] = reality_row.get("protection_status")
            row["broker_sync_age_seconds"] = reality_row.get("broker_sync_age_seconds")
        else:
            row["source"] = "LOCAL_ONLY"
            row["broker_verified"] = False
            row["broker_sync_age_seconds"] = None
        overlaid.append(row)
    return overlaid


def _matching_broker_entry(
    local_entry: dict[str, Any] | None,
    broker_orders: list[BrokerOrderRequest],
    *,
    symbol: str,
    side: str,
) -> BrokerOrderRequest | None:
    by_key = _broker_orders_by_key(broker_orders)
    if local_entry is not None:
        for key in _local_order_keys(local_entry):
            candidate = by_key.get(key)
            if candidate is not None:
                return candidate
    candidates = [
        order
        for order in broker_orders
        if str(order.symbol or "").upper() == symbol
        and str(order.side or "").upper() == side
    ]
    return _preferred_broker_order(candidates)


def _matching_broker_stop(
    local_stop: dict[str, Any] | None,
    broker_orders: list[BrokerOrderRequest],
    *,
    symbol: str,
    side: str,
    broker_entry: BrokerOrderRequest | None,
    local_entry: dict[str, Any] | None,
) -> BrokerOrderRequest | None:
    by_key = _broker_orders_by_key(broker_orders)
    if local_stop is not None:
        for key in _local_order_keys(local_stop):
            candidate = by_key.get(key)
            if candidate is not None:
                return candidate
    candidates = [
        order
        for order in broker_orders
        if str(order.symbol or "").upper() == symbol
        and _is_stop_broker_order(order, side)
    ]
    parent_keys = _parent_keys(broker_entry, local_entry)
    parent_matches = [
        order
        for order in candidates
        if str(order.parent_id or "").strip() in parent_keys
    ]
    return _preferred_broker_order(parent_matches or candidates)


def _preferred_broker_order(
    candidates: list[BrokerOrderRequest],
) -> BrokerOrderRequest | None:
    if not candidates:
        return None
    return sorted(candidates, key=_broker_order_preference)[0]


def _broker_order_preference(order: BrokerOrderRequest) -> int:
    status = _broker_order_status(order)
    if status in WORKING_BROKER_STATUSES:
        return 0
    if status == "PREPARED_NOT_TRANSMITTED":
        return 1
    if status in {"REJECTED", "INACTIVE_OR_REJECTED"}:
        return 2
    if status in TERMINAL_BROKER_STATUSES:
        return 3
    return 4


def _broker_order_status(order: BrokerOrderRequest) -> str:
    status = order.broker_status or order.raw_status or order.status
    return normalize_broker_order_status(status, transmit=bool(order.transmit))


def _is_stop_broker_order(order: BrokerOrderRequest, side: str) -> bool:
    if str(order.side or "").upper() != side:
        return False
    order_type = str(order.order_type or "").upper().replace(" ", "_")
    return order.stop_price is not None or order_type in {
        "STP",
        "STP_LMT",
        *TRAILING_STOP_ORDER_TYPES,
    }


def _latest_local_order(
    orders: list[dict[str, Any]],
    *,
    side: str,
    stop: bool,
) -> dict[str, Any] | None:
    candidates = [
        order
        for order in orders
        if str(order.get("side") or "").upper() == side
        and (_local_order_is_stop(order) if stop else not _local_order_is_stop(order))
    ]
    active = [
        order
        for order in candidates
        if str(order.get("status") or "") in ACTIVE_LOCAL_ORDER_STATUSES
    ]
    return (active or candidates or [None])[0]


def _local_order_is_stop(order: dict[str, Any]) -> bool:
    order_type = str(order.get("order_type") or "").upper().replace(" ", "_")
    return order.get("stop_price") is not None or order_type in {
        "STP",
        *TRAILING_STOP_ORDER_TYPES,
    }


def _broker_orders_by_key(
    broker_orders: list[BrokerOrderRequest],
) -> dict[str, BrokerOrderRequest]:
    result: dict[str, BrokerOrderRequest] = {}
    for order in broker_orders:
        for key in _broker_order_keys(order):
            result[key] = order
    return result


def _broker_order_keys(order: BrokerOrderRequest) -> set[str]:
    return _clean_keys({order.client_order_id, order.broker_order_id, order.broker_perm_id})


def _local_order_keys(order: dict[str, Any]) -> set[str]:
    return _clean_keys({order.get("id"), order.get("broker_order_id"), order.get("broker_perm_id")})


def _parent_keys(
    broker_entry: BrokerOrderRequest | None,
    local_entry: dict[str, Any] | None,
) -> set[str]:
    keys: set[Any] = set()
    if broker_entry is not None:
        keys.update({broker_entry.client_order_id, broker_entry.broker_order_id, broker_entry.broker_perm_id})
    if local_entry is not None:
        keys.update({local_entry.get("id"), local_entry.get("broker_order_id"), local_entry.get("broker_perm_id")})
    return _clean_keys(keys)


def _clean_keys(values: set[Any]) -> set[str]:
    return {
        text
        for value in values
        for text in [str(value or "").strip()]
        if text
    }


def _configured_stop(config: dict[str, Any]) -> float | None:
    trailing = config.get("trailing_stop_loss", {})
    if isinstance(trailing, dict):
        trailing_stop = _number_or_none(trailing.get("initial_stop"))
        if trailing_stop is not None:
            return trailing_stop
    return None


def _local_order_intent(
    local_entry: dict[str, Any] | None,
    local_stop: dict[str, Any] | None,
) -> str:
    if local_entry and local_stop:
        return "CREATE_ENTRY_WITH_STOP"
    if local_entry:
        return "CREATE_ENTRY"
    if local_stop:
        return "CREATE_STOP"
    return ""


def _local_quantity(
    local_entry: dict[str, Any] | None,
    local_stop: dict[str, Any] | None,
) -> int | None:
    for order in (local_entry, local_stop):
        value = _dict_number(order, "quantity")
        if value is not None:
            return int(value)
    return None


def _order_identifier(order: BrokerOrderRequest | None) -> str | None:
    if order is None:
        return None
    return order.broker_order_id or order.client_order_id or order.broker_perm_id


def _order_number(order: BrokerOrderRequest | None, field: str) -> float | None:
    if order is None:
        return None
    return _number_or_none(getattr(order, field, None))


def _position_number(position: BrokerPosition | None, field: str) -> float | None:
    if position is None:
        return None
    return _number_or_none(getattr(position, field, None))


def _dict_number(row: dict[str, Any] | None, field: str) -> float | None:
    if not isinstance(row, dict):
        return None
    return _number_or_none(row.get(field))


def _pnl_snapshot(
    account: dict[str, Any] | None,
    sync_at: str,
    broker_positions: list[BrokerPosition],
) -> dict[str, Any]:
    account = account or {}
    daily_pnl = _number_or_none(account.get("today_pnl"))
    unrealized_pnl = _number_or_none(account.get("unrealized_pnl"))
    realized_pnl = _number_or_none(account.get("realized_pnl"))
    position_unrealized = _sum_numbers(
        _position_unrealized_pnl(position)
        for position in broker_positions
        if _position_quantity(position) != 0
    )
    position_daily = _sum_numbers(
        _position_number(position, "daily_pnl")
        for position in broker_positions
        if _position_quantity(position) != 0
    )
    if unrealized_pnl is None:
        unrealized_pnl = position_unrealized
    if daily_pnl is None:
        daily_pnl = position_daily
    available = bool(account.get("available")) or any(
        value is not None for value in (daily_pnl, unrealized_pnl, realized_pnl)
    )
    total_pnl = None
    if unrealized_pnl is not None or realized_pnl is not None:
        total_pnl = (unrealized_pnl or 0.0) + (realized_pnl or 0.0)
    return {
        "daily_pnl": daily_pnl,
        "unrealized_pnl": unrealized_pnl,
        "realized_pnl": realized_pnl,
        "total_pnl": total_pnl,
        "source": "TWS",
        "status": "OK" if available else "STALE",
        "last_update": sync_at if available else None,
        "age_seconds": 0 if available else None,
        "sync_status": "OK" if available else "UNAVAILABLE",
        "reason": None if available else "NO_RECENT_TWS_PNL_SNAPSHOT",
    }


def _active_stop_price(order: BrokerOrderRequest | None) -> float | None:
    if order is None or _broker_order_status(order) not in WORKING_BROKER_STATUSES:
        return None
    return _number_or_none(order.stop_price)


def _is_trailing_stop_order(order: BrokerOrderRequest | None) -> bool:
    if order is None:
        return False
    order_type = str(order.order_type or "").upper().replace(" ", "_")
    return order_type in TRAILING_STOP_ORDER_TYPES


def _remaining_risk_for_position(
    *,
    quantity: int,
    current_price: float | None,
    active_stop_price: float | None,
    stop_status: str,
) -> tuple[str, float | None]:
    if quantity == 0:
        return "NO_OPEN_POSITION", 0.0
    if stop_status not in WORKING_BROKER_STATUSES or active_stop_price is None:
        return "UNKNOWN_CRITICAL", None
    if current_price is None:
        return "UNKNOWN_PRICE", None
    if quantity > 0:
        return "OK", round(max(current_price - active_stop_price, 0.0) * abs(quantity), 2)
    return "OK", round(max(active_stop_price - current_price, 0.0) * abs(quantity), 2)


def _stop_distance(
    current_price: float | None,
    active_stop_price: float | None,
) -> float | None:
    if current_price is None or active_stop_price is None:
        return None
    return round(abs(current_price - active_stop_price), 4)


def _remaining_risk_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    total = 0.0
    unknown = False
    unprotected_positions = 0
    unprotected_orders = 0
    active_stop_orders = 0
    for row in rows:
        if row.get("broker_stop_status") in WORKING_BROKER_STATUSES:
            active_stop_orders += 1
        if row.get("protection_status") == "POSITION_OPEN_STOP_MISSING_CRITICAL":
            unprotected_positions += 1
        if row.get("protection_status") == "ENTRY_ORDER_WITHOUT_STOP_CRITICAL":
            unprotected_orders += 1
        status = str(row.get("remaining_risk_status") or "")
        if status.startswith("UNKNOWN"):
            unknown = True
            continue
        value = _number_or_none(row.get("remaining_risk"))
        if value is not None:
            total += value
    if unknown or unprotected_positions or unprotected_orders:
        return {
            "total": None,
            "status": "UNKNOWN_CRITICAL",
            "reason": "POSITION_OPEN_STOP_MISSING_CRITICAL"
            if unprotected_positions
            else "ENTRY_ORDER_WITHOUT_STOP_CRITICAL"
            if unprotected_orders
            else "MISSING_PRICE_OR_STOP",
            "unprotected_positions": unprotected_positions,
            "unprotected_orders": unprotected_orders,
            "active_stop_orders": active_stop_orders,
        }
    return {
        "total": round(total, 2),
        "status": "OK",
        "reason": "",
        "unprotected_positions": unprotected_positions,
        "unprotected_orders": unprotected_orders,
        "active_stop_orders": active_stop_orders,
    }


def _broker_order_count(
    broker_orders: list[BrokerOrderRequest],
    statuses: set[str],
) -> int:
    return len(
        [
            order
            for order in broker_orders
            if _broker_order_status(order) in statuses
        ]
    )


def _position_quantity(position: BrokerPosition | None) -> int:
    if position is None:
        return 0
    try:
        return int(float(getattr(position, "quantity", 0) or 0))
    except (TypeError, ValueError):
        return 0


def _position_unrealized_pnl(position: BrokerPosition | None) -> float | None:
    if position is None:
        return None
    explicit = _position_number(position, "unrealized_pnl")
    if explicit is not None:
        return explicit
    quantity = _position_quantity(position)
    average_price = _position_number(position, "average_price")
    current_price = _position_number(position, "market_price") or _position_number(
        position,
        "current_price",
    )
    if average_price is None or current_price is None:
        return None
    return round((current_price - average_price) * quantity, 2)


def _sum_numbers(values: Any) -> float | None:
    total = 0.0
    found = False
    for value in values:
        number = _number_or_none(value)
        if number is None:
            continue
        total += number
        found = True
    return round(total, 2) if found else None


def _first_present(*values: Any) -> Any:
    for value in values:
        if value not in (None, ""):
            return value
    return None


def _number_or_none(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _positive_int(value: Any, default: int) -> int:
    try:
        parsed = int(float(value))
    except (TypeError, ValueError):
        parsed = default
    return max(1, parsed)


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _age_seconds(value: Any, *, now: str | None = None) -> int | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    if now:
        try:
            current = datetime.fromisoformat(str(now))
        except ValueError:
            current = datetime.now(timezone.utc)
    else:
        current = datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    return max(int((current - parsed).total_seconds()), 0)


def _dedupe(items: list[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for item in items:
        text = str(item or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        result.append(text)
    return result
