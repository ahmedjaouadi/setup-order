from __future__ import annotations

import json
from math import floor
from typing import Any

from app.models import (
    EventRecord,
    OrderRecord,
    PositionRecord,
    SetupRecord,
    utc_now_iso,
)
from app.storage.database import Database


def _row_to_dict(row: Any) -> dict[str, Any]:
    result = dict(row)
    if "config_json" in result:
        result["config"] = json.loads(result.pop("config_json"))
    if "data_json" in result:
        result["data"] = json.loads(result.pop("data_json"))
    for source_key, target_key in {
        "payload_json": "payload",
        "trace_json": "trace",
        "score_json": "score",
        "features_json": "features",
        "forecast_json": "forecast",
        "ensemble_json": "ensemble",
        "metrics_json": "metrics",
        "trading_metrics_json": "trading_metrics",
        "baseline_comparison_json": "baseline_comparison",
        "policy_json": "policy",
        "scenario_json": "scenario",
        "report_json": "report",
        "member_forecast_ids_json": "member_forecast_ids",
        "sector_exposure_json": "sector_exposure",
        "symbol_exposure_json": "symbol_exposure",
        "correlation_json": "correlation",
        "warnings_json": "warnings",
        "size_reductions_json": "size_reductions",
    }.items():
        if source_key in result:
            result[target_key] = json.loads(result.pop(source_key) or "{}")
    if "enabled" in result:
        result["enabled"] = bool(result["enabled"])
    if "selected_for_symbol" in result:
        result["selected_for_symbol"] = bool(result["selected_for_symbol"])
    if "setup_id" in result and "config" in result and "entry_zone" in result:
        _canonicalize_setup_row(result)
        _enrich_setup_row(result)
    return result


def _canonicalize_setup_row(row: dict[str, Any]) -> None:
    config = row.get("config")
    if not isinstance(config, dict):
        return
    try:
        from app.conversion import canonicalize_setup_config

        row["config"] = canonicalize_setup_config(config).config
    except Exception:
        return


def _enrich_setup_row(row: dict[str, Any]) -> None:
    config = row.get("config", {})
    if not isinstance(config, dict):
        return
    role = str(config.get("setup_role", "ENTRY_AND_MANAGEMENT"))
    trigger = _estimated_trigger_price(config)
    limit = _maximum_limit_price(config, trigger)
    worst_case = limit or trigger
    initial_stop = _initial_trailing_stop(config)
    quantity = _maximum_quantity(config, worst_case, initial_stop)
    row.setdefault("setup_role", role)
    row.setdefault("entry_trigger", _rounded(trigger))
    row.setdefault("maximum_limit_price", _rounded(limit))
    row.setdefault("worst_case_entry_price", _rounded(worst_case))
    row.setdefault("initial_trailing_stop", _rounded(initial_stop))
    row.setdefault("maximum_quantity", quantity)
    row.setdefault(
        "maximum_risk",
        _rounded(_risk_amount(quantity, worst_case, initial_stop)),
    )
    row.setdefault("position_source", _position_source(config))
    row.setdefault("reconciliation_status", _reconciliation_status(row["status"]))


def _decode_stack_experiment(row: Any) -> dict[str, Any]:
    result = dict(row)
    for key in ("symbols", "timeframes", "horizons", "models", "config", "summary"):
        result[key] = json.loads(
            result.pop(f"{key}_json")
            or ("[]" if key in {"symbols", "timeframes", "horizons", "models"} else "{}")
        )
    return result


def _estimated_trigger_price(config: dict[str, Any]) -> float | None:
    entry = config.get("entry", {})
    if not isinstance(entry, dict):
        return None
    for key in ("trigger_price", "entry_price"):
        if entry.get(key) is not None:
            return float(entry[key])
    offset = float(entry.get("trigger_offset", 0.0) or 0.0)
    setup_type = config.get("setup_type")
    if setup_type == "breakout_retest":
        breakout = config.get("breakout", {})
        if breakout.get("daily_close_above") is not None:
            return float(breakout["daily_close_above"]) + offset
    if setup_type == "momentum_breakout":
        breakout = config.get("breakout", {})
        if breakout.get("resistance") is not None:
            return float(breakout["resistance"]) + offset
    if setup_type == "range_breakout":
        range_config = config.get("range", {})
        if range_config.get("high") is not None:
            return float(range_config["high"]) + offset
    if setup_type == "pullback_continuation":
        pullback = config.get("pullback", {})
        if pullback.get("entry_reference") is not None:
            return float(pullback["entry_reference"])
    if setup_type == "aggressive_rebound":
        support = config.get("support_zone", {})
        if support.get("max") is not None:
            return float(support["max"])
    return None


def _maximum_limit_price(
    config: dict[str, Any],
    trigger: float | None,
) -> float | None:
    if trigger is None:
        return None
    entry = config.get("entry", {})
    if not isinstance(entry, dict):
        return None
    if str(entry.get("order_type", "STP_LMT")) != "STP_LMT":
        return None
    if entry.get("maximum_limit_price") is not None:
        return float(entry["maximum_limit_price"])
    if entry.get("limit_price") is not None:
        return float(entry["limit_price"])
    return trigger + float(entry.get("limit_offset", 0.0) or 0.0)


def _initial_trailing_stop(config: dict[str, Any]) -> float | None:
    trailing = config.get("trailing_stop_loss", {})
    if isinstance(trailing, dict):
        value = trailing.get("initial_stop")
        if value is not None:
            return float(value)
    return None


def _maximum_quantity(
    config: dict[str, Any],
    worst_case: float | None,
    initial_stop: float | None,
) -> int | None:
    if worst_case is None or initial_stop is None:
        return None
    risk = config.get("risk", {})
    if not isinstance(risk, dict):
        return None
    max_position = float(risk.get("max_position_amount_usd", 0.0) or 0.0)
    max_risk = float(risk.get("max_risk_usd", 0.0) or 0.0)
    risk_per_share = abs(worst_case - initial_stop)
    if worst_case <= 0 or max_position <= 0 or max_risk <= 0 or risk_per_share <= 0:
        return None
    return min(floor(max_position / worst_case), floor(max_risk / risk_per_share))


def _risk_amount(
    quantity: int | None,
    worst_case: float | None,
    initial_stop: float | None,
) -> float | None:
    if quantity is None or worst_case is None or initial_stop is None:
        return None
    return quantity * abs(worst_case - initial_stop)


def _position_source(config: dict[str, Any]) -> str:
    source = config.get("position_source", {})
    if isinstance(source, dict) and source.get("mode"):
        return str(source["mode"])
    entry = config.get("entry", {})
    if isinstance(entry, dict) and not bool(entry.get("enabled", True)):
        return "manual"
    return "bot"


def _reconciliation_status(status: str) -> str:
    if status == "RECONCILING_EXISTING_POSITION":
        return "PENDING"
    if status in {"IN_POSITION", "MANAGING_POSITION"}:
        return "OK"
    if status in {"MANUAL_REVIEW_REQUIRED", "ERROR_REQUIRES_MANUAL_REVIEW"}:
        return "MANUAL_REVIEW_REQUIRED"
    return ""


def _rounded(value: float | None) -> float | None:
    return round(value, 4) if value is not None else None


def _snapshot_initial_stop(snapshot: dict[str, Any]) -> float | None:
    trailing = snapshot.get("trailing_stop_loss")
    if isinstance(trailing, dict):
        value = _optional_float(trailing.get("initial_stop"))
        if value is not None:
            return value
    value = _optional_float(snapshot.get("trailing_stop_initial_stop"))
    if value is not None:
        return value
    return _legacy_snapshot_initial_stop(snapshot)


def _legacy_snapshot_initial_stop(snapshot: dict[str, Any]) -> float | None:
    return _optional_float(snapshot.get("initial_stop_loss"))


def _normalize_setup_creation_snapshot(snapshot: dict[str, Any]) -> dict[str, Any]:
    initial_stop = _snapshot_initial_stop(snapshot)
    trailing = snapshot.get("trailing_stop_loss")
    trailing = dict(trailing) if isinstance(trailing, dict) else {}
    trailing["initial_stop"] = initial_stop
    snapshot["trailing_stop_loss"] = trailing
    snapshot["trailing_stop_initial_stop"] = initial_stop
    snapshot.pop("initial_stop_loss", None)

    payload = snapshot.get("payload")
    if isinstance(payload, dict):
        payload_initial_stop = _snapshot_initial_stop(payload)
        if payload_initial_stop is None:
            payload_initial_stop = initial_stop
        payload_trailing = payload.get("trailing_stop_loss")
        payload_trailing = dict(payload_trailing) if isinstance(payload_trailing, dict) else {}
        payload_trailing["initial_stop"] = payload_initial_stop
        payload["trailing_stop_loss"] = payload_trailing
        payload.pop("initial_stop_loss", None)
        payload.pop("trailing_stop_initial_stop", None)
    return snapshot


def _optional_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


ACTIVE_ORDER_STATUSES = {"CREATED", "SUBMITTED"}
STOP_FAILURE_STATUSES = {"REJECTED", "ERROR"}


def _is_open_position(position: dict[str, Any], setup_id: str) -> bool:
    if str(position.get("setup_id") or "") != setup_id:
        return False
    try:
        return int(position.get("quantity") or 0) != 0
    except (TypeError, ValueError):
        return False


def _is_stop_order(order: dict[str, Any]) -> bool:
    return str(order.get("side") or "").upper() == "SELL" and order.get("stop_price") is not None


def _is_entry_order(order: dict[str, Any]) -> bool:
    return str(order.get("side") or "").upper() == "BUY"


def _is_active_order(order: dict[str, Any]) -> bool:
    return str(order.get("status") or "") in ACTIVE_ORDER_STATUSES


def _matching_stop_order(
    orders: list[dict[str, Any]],
    *,
    parent_id: str | None = None,
    active_only: bool = False,
) -> dict[str, Any] | None:
    for order in orders:
        if not _is_stop_order(order):
            continue
        if parent_id is not None and str(order.get("parent_id") or "") != parent_id:
            continue
        if active_only and not _is_active_order(order):
            continue
        return order
    return None


def _protection_snapshot(
    setup_id: str,
    orders: list[dict[str, Any]],
    positions: list[dict[str, Any]],
) -> dict[str, Any]:
    open_position = any(_is_open_position(position, setup_id) for position in positions)
    active_entry_order = next(
        (order for order in orders if _is_entry_order(order) and _is_active_order(order)),
        None,
    )
    active_stop_order = _matching_stop_order(
        orders,
        parent_id=str(active_entry_order.get("id") or "") if active_entry_order else None,
        active_only=True,
    )
    if active_stop_order is None:
        active_stop_order = _matching_stop_order(orders, active_only=True)
    failed_stop_order = _matching_stop_order(orders)
    if (
        failed_stop_order
        and str(failed_stop_order.get("status") or "") not in STOP_FAILURE_STATUSES
    ):
        failed_stop_order = None
    protection_status = "NO_ENTRY_ORDER"
    blocking_reasons: list[str] = []
    if open_position and active_stop_order is None:
        protection_status = "POSITION_OPEN_STOP_MISSING_CRITICAL"
        blocking_reasons.append("POSITION_OPEN_WITHOUT_PROTECTIVE_STOP")
    elif active_entry_order and active_stop_order:
        protection_status = "BRACKET_ORDER_SUBMITTED"
    elif active_entry_order and failed_stop_order:
        protection_status = "STOP_SUBMISSION_FAILED"
        blocking_reasons.append("PROTECTIVE_STOP_SUBMISSION_FAILED")
    elif active_entry_order:
        protection_status = "ENTRY_ORDER_PENDING_WITHOUT_STOP_BLOCKED"
        blocking_reasons.append("ACTIVE_ORDER_WITHOUT_PROTECTIVE_STOP")
    elif active_stop_order and open_position:
        protection_status = "POSITION_OPEN_STOP_ACTIVE"
    return {
        "setup_id": setup_id,
        "position_open": open_position,
        "active_entry_order_id": active_entry_order.get("id") if active_entry_order else None,
        "active_stop_order_id": active_stop_order.get("id") if active_stop_order else None,
        "has_active_stop_order": active_stop_order is not None,
        "protection_status": protection_status,
        "blocking_reasons": blocking_reasons,
    }


def _enrich_orders_with_protection(
    orders: list[dict[str, Any]],
    positions: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    if not orders:
        return []
    orders_by_setup: dict[str, list[dict[str, Any]]] = {}
    for order in orders:
        orders_by_setup.setdefault(str(order.get("setup_id") or ""), []).append(order)
    snapshots = {
        setup_id: _protection_snapshot(setup_id, setup_orders, positions)
        for setup_id, setup_orders in orders_by_setup.items()
    }
    stop_by_parent = {
        str(order.get("parent_id") or ""): order
        for order in orders
        if _is_stop_order(order) and order.get("parent_id")
    }
    enriched: list[dict[str, Any]] = []
    for order in orders:
        row = dict(order)
        snapshot = snapshots.get(str(order.get("setup_id") or ""), {})
        stop_order = None
        if _is_entry_order(order):
            stop_order = stop_by_parent.get(str(order.get("id") or ""))
        elif _is_stop_order(order):
            stop_order = order
        row["entry_order_id"] = (
            order.get("id") if _is_entry_order(order) else order.get("parent_id")
        )
        row["stop_order_id"] = stop_order.get("id") if isinstance(stop_order, dict) else None
        row["stop_order_status"] = (
            stop_order.get("status") if isinstance(stop_order, dict) else None
        )
        row["bracket_order"] = bool(
            (_is_entry_order(order) and stop_order)
            or (_is_stop_order(order) and order.get("parent_id"))
        )
        row["protection_status"] = snapshot.get("protection_status", "NO_ENTRY_ORDER")
        row["protection_blocking_reasons"] = snapshot.get("blocking_reasons", [])
        enriched.append(row)
    return enriched


class TradingRepository:
    def __init__(self, database: Database) -> None:
        self.database = database

    def upsert_setup(self, record: SetupRecord) -> None:
        now = utc_now_iso()
        self.database.execute(
            """
            INSERT INTO setups (
                setup_id, symbol, setup_type, enabled, mode, status, entry_zone,
                stop_loss, risk_amount, order_status, position_status, last_event,
                config_json, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(setup_id) DO UPDATE SET
                symbol = excluded.symbol,
                setup_type = excluded.setup_type,
                enabled = excluded.enabled,
                mode = excluded.mode,
                status = excluded.status,
                entry_zone = excluded.entry_zone,
                stop_loss = excluded.stop_loss,
                risk_amount = excluded.risk_amount,
                order_status = excluded.order_status,
                position_status = excluded.position_status,
                last_event = excluded.last_event,
                config_json = excluded.config_json,
                updated_at = excluded.updated_at
            """,
            (
                record.setup_id,
                record.symbol,
                record.setup_type,
                1 if record.enabled else 0,
                record.mode,
                record.status,
                record.entry_zone,
                record.stop_loss,
                record.risk_amount,
                record.order_status,
                record.position_status,
                record.last_event,
                json.dumps(record.config, sort_keys=True),
                record.created_at,
                now,
            ),
        )

    def list_setups(self) -> list[dict[str, Any]]:
        rows = self.database.execute("SELECT * FROM setups ORDER BY symbol, setup_id").fetchall()
        return [_row_to_dict(row) for row in rows]

    def get_setup(self, setup_id: str) -> dict[str, Any] | None:
        row = self.database.execute(
            "SELECT * FROM setups WHERE setup_id = ?",
            (setup_id,),
        ).fetchone()
        return _row_to_dict(row) if row else None

    def update_setup_status(
        self,
        setup_id: str,
        status: str,
        last_event: str,
        status_reason: str | None = None,
        last_revalidated_at: str | None = None,
    ) -> None:
        if status_reason is None and last_revalidated_at is None:
            self.database.execute(
                """
                UPDATE setups
                SET status = ?, last_event = ?, updated_at = ?
                WHERE setup_id = ?
                """,
                (status, last_event, utc_now_iso(), setup_id),
            )
            return
        self.database.execute(
            """
            UPDATE setups
            SET status = ?, last_event = ?, status_reason = ?,
                last_revalidated_at = COALESCE(?, last_revalidated_at),
                updated_at = ?
            WHERE setup_id = ?
            """,
            (
                status,
                last_event,
                status_reason or "",
                last_revalidated_at,
                utc_now_iso(),
                setup_id,
            ),
        )

    def update_setup_revalidation(
        self,
        setup_id: str,
        status_reason: str,
        last_revalidated_at: str,
    ) -> None:
        """Record a revalidation result without changing the setup status."""
        self.database.execute(
            """
            UPDATE setups
            SET status_reason = ?, last_revalidated_at = ?
            WHERE setup_id = ?
            """,
            (status_reason or "", last_revalidated_at, setup_id),
        )

    def set_setup_enabled(self, setup_id: str, enabled: bool) -> None:
        self.database.execute(
            """
            UPDATE setups
            SET enabled = ?, updated_at = ?
            WHERE setup_id = ?
            """,
            (1 if enabled else 0, utc_now_iso(), setup_id),
        )

    def delete_setup(self, setup_id: str) -> None:
        self.database.execute(
            "DELETE FROM setups WHERE setup_id = ?",
            (setup_id,),
        )

    def add_setup_creation_snapshot(self, snapshot: dict[str, Any]) -> str:
        snapshot = _normalize_setup_creation_snapshot(dict(snapshot))
        snapshot_id = str(snapshot["snapshot_id"])
        initial_stop = snapshot.get("trailing_stop_initial_stop")
        self.database.execute(
            """
            INSERT OR IGNORE INTO setup_creation_snapshots (
                snapshot_id, setup_id, scenario_id, opportunity_id, symbol,
                captured_at, last_price, bid, ask, mid_price, spread_pct,
                volume, volume_ratio, atr_15m, atr_1h, vwap,
                entry_trigger_price, entry_limit_price, trailing_stop_initial_stop,
                distance_to_trigger_pct, distance_to_limit_pct,
                distance_to_stop_pct, data_quality_status,
                data_quality_issues_json, source, payload_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                snapshot_id,
                snapshot["setup_id"],
                snapshot.get("scenario_id"),
                snapshot.get("opportunity_id"),
                snapshot["symbol"],
                snapshot["captured_at"],
                snapshot.get("last_price"),
                snapshot.get("bid"),
                snapshot.get("ask"),
                snapshot.get("mid_price"),
                snapshot.get("spread_pct"),
                snapshot.get("volume"),
                snapshot.get("volume_ratio"),
                snapshot.get("atr_15m"),
                snapshot.get("atr_1h"),
                snapshot.get("vwap"),
                snapshot.get("entry_trigger_price"),
                snapshot.get("entry_limit_price"),
                initial_stop,
                snapshot.get("distance_to_trigger_pct"),
                snapshot.get("distance_to_limit_pct"),
                snapshot.get("distance_to_stop_pct"),
                snapshot["data_quality_status"],
                json.dumps(snapshot.get("data_quality_issues", []), sort_keys=True),
                snapshot["source"],
                json.dumps(snapshot, sort_keys=True),
            ),
        )
        return snapshot_id

    def get_setup_creation_snapshot(self, setup_id: str) -> dict[str, Any] | None:
        row = self.database.execute(
            "SELECT * FROM setup_creation_snapshots WHERE setup_id = ?",
            (setup_id,),
        ).fetchone()
        if row is None:
            return None
        result = dict(row)
        result["data_quality_issues"] = json.loads(result.pop("data_quality_issues_json") or "[]")
        result["payload"] = json.loads(result.pop("payload_json") or "{}")
        return _normalize_setup_creation_snapshot(result)

    def attach_setup_creation_snapshot(
        self,
        setup_id: str,
        snapshot: dict[str, Any],
    ) -> None:
        """Embed the immutable snapshot in the setup document without replacing it."""
        row = self.database.execute(
            "SELECT config_json FROM setups WHERE setup_id = ?",
            (setup_id,),
        ).fetchone()
        if row is None:
            raise KeyError(setup_id)
        config = json.loads(row["config_json"] or "{}")
        if not isinstance(config, dict) or config.get("creation_market_snapshot"):
            return
        embedded = {
            key: value
            for key, value in _normalize_setup_creation_snapshot(dict(snapshot)).items()
            if key not in {"payload", "snapshot_id", "setup_id", "trailing_stop_initial_stop"}
        }
        config["creation_market_snapshot"] = embedded
        self.database.execute(
            "UPDATE setups SET config_json = ?, updated_at = ? WHERE setup_id = ?",
            (json.dumps(config, sort_keys=True), utc_now_iso(), setup_id),
        )

    def upsert_order(self, record: OrderRecord) -> None:
        now = utc_now_iso()
        self.database.execute(
            """
            INSERT INTO orders (
                id, setup_id, symbol, side, order_type, quantity, status,
                trigger_price, limit_price, stop_price, broker_order_id,
                broker_perm_id, parent_id, oca_group, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                setup_id = excluded.setup_id,
                symbol = excluded.symbol,
                side = excluded.side,
                order_type = excluded.order_type,
                quantity = excluded.quantity,
                status = excluded.status,
                trigger_price = excluded.trigger_price,
                limit_price = excluded.limit_price,
                stop_price = excluded.stop_price,
                broker_order_id = excluded.broker_order_id,
                broker_perm_id = excluded.broker_perm_id,
                parent_id = excluded.parent_id,
                oca_group = excluded.oca_group,
                updated_at = excluded.updated_at
            """,
            (
                record.id,
                record.setup_id,
                record.symbol,
                record.side,
                record.order_type,
                record.quantity,
                record.status,
                record.trigger_price,
                record.limit_price,
                record.stop_price,
                record.broker_order_id,
                record.broker_perm_id,
                record.parent_id,
                record.oca_group,
                record.created_at,
                now,
            ),
        )

    def list_orders(self, setup_id: str | None = None) -> list[dict[str, Any]]:
        if setup_id:
            rows = self.database.execute(
                "SELECT * FROM orders WHERE setup_id = ? ORDER BY created_at DESC",
                (setup_id,),
            ).fetchall()
        else:
            rows = self.database.execute("SELECT * FROM orders ORDER BY created_at DESC").fetchall()
        return [_row_to_dict(row) for row in rows]

    def list_orders_with_protection(
        self,
        setup_id: str | None = None,
        *,
        orders: list[dict[str, Any]] | None = None,
        positions: list[dict[str, Any]] | None = None,
    ) -> list[dict[str, Any]]:
        order_rows = orders if orders is not None else self.list_orders(setup_id)
        if setup_id and orders is not None:
            order_rows = [
                order for order in order_rows if str(order.get("setup_id") or "") == setup_id
            ]
        return _enrich_orders_with_protection(
            order_rows,
            positions if positions is not None else self.list_positions(),
        )

    def get_order(self, order_id: str) -> dict[str, Any] | None:
        row = self.database.execute(
            "SELECT * FROM orders WHERE id = ?",
            (order_id,),
        ).fetchone()
        return _row_to_dict(row) if row else None

    def update_order_status(self, order_id: str, status: str) -> None:
        self.database.execute(
            "UPDATE orders SET status = ?, updated_at = ? WHERE id = ?",
            (status, utc_now_iso(), order_id),
        )

    def update_order_stop_price(self, order_id: str, stop_price: float) -> None:
        self.database.execute(
            "UPDATE orders SET stop_price = ?, updated_at = ? WHERE id = ?",
            (stop_price, utc_now_iso(), order_id),
        )

    def active_stop_order_for_symbol(self, symbol: str) -> dict[str, Any] | None:
        row = self.database.execute(
            """
            SELECT * FROM orders
            WHERE symbol = ?
              AND side = 'SELL'
              AND status IN ('CREATED', 'SUBMITTED')
              AND stop_price IS NOT NULL
            ORDER BY created_at DESC
            LIMIT 1
            """,
            (symbol.upper(),),
        ).fetchone()
        return _row_to_dict(row) if row else None

    def delete_order(self, order_id: str) -> None:
        self.database.execute(
            "DELETE FROM orders WHERE id = ?",
            (order_id,),
        )

    def active_orders_for_setup(self, setup_id: str) -> list[dict[str, Any]]:
        rows = self.database.execute(
            """
            SELECT * FROM orders
            WHERE setup_id = ?
              AND status IN ('CREATED', 'SUBMITTED')
            ORDER BY created_at DESC
            """,
            (setup_id,),
        ).fetchall()
        return [_row_to_dict(row) for row in rows]

    def protection_snapshot_for_setup(self, setup_id: str) -> dict[str, Any]:
        return _protection_snapshot(
            setup_id,
            self.list_orders(setup_id),
            self.list_positions(),
        )

    def upsert_position(self, record: PositionRecord) -> None:
        self.database.execute(
            """
            INSERT INTO positions (
                symbol, setup_id, quantity, average_price, current_price,
                unrealized_pnl, current_stop, risk_remaining, status, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(symbol) DO UPDATE SET
                setup_id = excluded.setup_id,
                quantity = excluded.quantity,
                average_price = excluded.average_price,
                current_price = excluded.current_price,
                unrealized_pnl = excluded.unrealized_pnl,
                current_stop = excluded.current_stop,
                risk_remaining = excluded.risk_remaining,
                status = excluded.status,
                updated_at = excluded.updated_at
            """,
            (
                record.symbol,
                record.setup_id,
                record.quantity,
                record.average_price,
                record.current_price,
                record.unrealized_pnl,
                record.current_stop,
                record.risk_remaining,
                record.status,
                record.updated_at,
            ),
        )

    def list_positions(self) -> list[dict[str, Any]]:
        rows = self.database.execute("SELECT * FROM positions ORDER BY symbol").fetchall()
        return [_row_to_dict(row) for row in rows]

    def get_position(self, symbol: str) -> dict[str, Any] | None:
        row = self.database.execute(
            "SELECT * FROM positions WHERE symbol = ?",
            (symbol.upper(),),
        ).fetchone()
        return _row_to_dict(row) if row else None

    def record_equity_snapshot(
        self,
        *,
        net_liquidation: float | None,
        daily_pnl: float | None,
        positions_pnl: float | None,
        open_positions: int,
        source: str,
        captured_at: str | None = None,
    ) -> None:
        self.database.execute(
            """
            INSERT INTO equity_snapshots (
                captured_at, net_liquidation, daily_pnl, positions_pnl,
                open_positions, source
            )
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                captured_at or utc_now_iso(),
                net_liquidation,
                daily_pnl,
                positions_pnl,
                int(open_positions),
                source,
            ),
        )

    def list_equity_snapshots(self, limit: int = 500) -> list[dict[str, Any]]:
        rows = self.database.execute(
            """
            SELECT * FROM (
                SELECT * FROM equity_snapshots ORDER BY id DESC LIMIT ?
            ) ORDER BY id ASC
            """,
            (limit,),
        ).fetchall()
        return [_row_to_dict(row) for row in rows]

    def latest_equity_snapshot(self) -> dict[str, Any] | None:
        row = self.database.execute(
            "SELECT * FROM equity_snapshots ORDER BY id DESC LIMIT 1"
        ).fetchone()
        return _row_to_dict(row) if row else None

    def add_event(self, record: EventRecord) -> None:
        self.database.execute(
            """
            INSERT INTO events (
                timestamp, level, event_type, setup_id, symbol, message, data_json
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                record.timestamp,
                record.level,
                record.event_type,
                record.setup_id,
                record.symbol,
                record.message,
                json.dumps(record.data, sort_keys=True),
            ),
        )

    def add_runtime_event(self, event: dict[str, Any]) -> None:
        self.database.execute(
            """
            INSERT INTO runtime_events (
                event_id, event_type, aggregate_type, aggregate_id, symbol,
                payload_json, correlation_id, causation_id, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                event["event_id"],
                event["event_type"],
                event.get("aggregate_type"),
                event.get("aggregate_id"),
                event.get("symbol"),
                json.dumps(event.get("payload", {}), sort_keys=True),
                event.get("correlation_id"),
                event.get("causation_id"),
                event["created_at"],
            ),
        )

    def add_decision_trace(self, trace: dict[str, Any]) -> None:
        self.database.execute(
            """
            INSERT INTO decision_traces (
                trace_id, symbol, setup_id, scenario_id, opportunity_id,
                decision_type, final_decision, trace_json, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                trace["trace_id"],
                trace.get("symbol"),
                trace.get("setup_id"),
                trace.get("scenario_id"),
                trace.get("opportunity_id"),
                trace["decision_type"],
                trace["final_decision"],
                json.dumps(trace.get("trace", {}), sort_keys=True),
                trace["created_at"],
            ),
        )

    def list_events(
        self,
        limit: int = 100,
        setup_id: str | None = None,
        symbol: str | None = None,
        level: str | None = None,
        event_type: str | None = None,
    ) -> list[dict[str, Any]]:
        query = "SELECT * FROM events"
        clauses = []
        params: list[Any] = []
        if setup_id:
            clauses.append("setup_id = ?")
            params.append(setup_id)
        if symbol:
            clauses.append("symbol = ?")
            params.append(symbol.upper())
        if level:
            clauses.append("level = ?")
            params.append(level.upper())
        if event_type:
            clauses.append("event_type = ?")
            params.append(event_type)
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        query += " ORDER BY id DESC LIMIT ?"
        params.append(limit)
        rows = self.database.execute(query, params).fetchall()
        return [_row_to_dict(row) for row in rows]

    def count_events(
        self,
        setup_id: str | None = None,
        symbol: str | None = None,
        level: str | None = None,
        event_type: str | None = None,
    ) -> int:
        query = "SELECT COUNT(*) FROM events"
        clauses = []
        params: list[Any] = []
        if setup_id:
            clauses.append("setup_id = ?")
            params.append(setup_id)
        if symbol:
            clauses.append("symbol = ?")
            params.append(symbol.upper())
        if level:
            clauses.append("level = ?")
            params.append(level.upper())
        if event_type:
            clauses.append("event_type = ?")
            params.append(event_type)
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        row = self.database.execute(query, params).fetchone()
        return int(row[0]) if row else 0

    def list_runtime_events(
        self,
        limit: int = 100,
        event_type: str | None = None,
        symbol: str | None = None,
        aggregate_id: str | None = None,
    ) -> list[dict[str, Any]]:
        query = "SELECT * FROM runtime_events"
        clauses = []
        params: list[Any] = []
        if event_type:
            clauses.append("event_type = ?")
            params.append(event_type)
        if symbol:
            clauses.append("symbol = ?")
            params.append(symbol.upper())
        if aggregate_id:
            clauses.append("aggregate_id = ?")
            params.append(aggregate_id)
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        query += " ORDER BY created_at DESC LIMIT ?"
        params.append(limit)
        rows = self.database.execute(query, params).fetchall()
        return [_row_to_dict(row) for row in rows]

    def list_decision_traces(
        self,
        limit: int = 100,
        setup_id: str | None = None,
        symbol: str | None = None,
        scenario_id: str | None = None,
        opportunity_id: str | None = None,
        decision_type: str | None = None,
        final_decision: str | None = None,
        date: str | None = None,
    ) -> list[dict[str, Any]]:
        query = "SELECT * FROM decision_traces"
        clauses = []
        params: list[Any] = []
        if setup_id:
            clauses.append("setup_id = ?")
            params.append(setup_id)
        if symbol:
            clauses.append("symbol = ?")
            params.append(symbol.upper())
        if scenario_id:
            clauses.append("scenario_id = ?")
            params.append(scenario_id)
        if opportunity_id:
            clauses.append("opportunity_id = ?")
            params.append(opportunity_id)
        if decision_type:
            clauses.append("decision_type = ?")
            params.append(decision_type)
        if final_decision:
            clauses.append("final_decision = ?")
            params.append(final_decision)
        if date:
            clauses.append("created_at LIKE ?")
            params.append(f"{date}%")
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        query += " ORDER BY created_at DESC LIMIT ?"
        params.append(limit)
        rows = self.database.execute(query, params).fetchall()
        return [_row_to_dict(row) for row in rows]

    def get_decision_trace(self, trace_id: str) -> dict[str, Any] | None:
        row = self.database.execute(
            "SELECT * FROM decision_traces WHERE trace_id = ?",
            (trace_id,),
        ).fetchone()
        return _row_to_dict(row) if row else None

    def upsert_opportunity(self, opportunity: dict[str, Any]) -> str:
        now = utc_now_iso()
        opportunity_id = str(opportunity["opportunity_id"])
        self.database.execute(
            """
            INSERT INTO opportunities (
                opportunity_id, symbol, opportunity_type, timeframe, status,
                score, detected_at, updated_at, payload_json
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(opportunity_id) DO UPDATE SET
                symbol = excluded.symbol,
                opportunity_type = excluded.opportunity_type,
                timeframe = excluded.timeframe,
                status = excluded.status,
                score = excluded.score,
                updated_at = excluded.updated_at,
                payload_json = excluded.payload_json
            """,
            (
                opportunity_id,
                str(opportunity.get("symbol", "")).upper(),
                opportunity.get("opportunity_type", "setup"),
                opportunity.get("timeframe", "15m"),
                opportunity.get("status", "DETECTED"),
                opportunity.get("score"),
                opportunity.get("detected_at") or now,
                now,
                json.dumps(opportunity.get("payload", opportunity), sort_keys=True),
            ),
        )
        return opportunity_id

    def list_opportunities(
        self,
        *,
        status: str | None = None,
        symbol: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        query = "SELECT * FROM opportunities"
        clauses = []
        params: list[Any] = []
        if status:
            clauses.append("status = ?")
            params.append(status)
        if symbol:
            clauses.append("symbol = ?")
            params.append(symbol.upper())
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        query += " ORDER BY score DESC, detected_at DESC LIMIT ?"
        params.append(limit)
        rows = self.database.execute(query, params).fetchall()
        return [_row_to_dict(row) for row in rows]

    def get_opportunity(self, opportunity_id: str) -> dict[str, Any] | None:
        row = self.database.execute(
            "SELECT * FROM opportunities WHERE opportunity_id = ?",
            (opportunity_id,),
        ).fetchone()
        return _row_to_dict(row) if row else None

    def update_opportunity_status(self, opportunity_id: str, status: str) -> None:
        self.database.execute(
            """
            UPDATE opportunities
            SET status = ?, updated_at = ?
            WHERE opportunity_id = ?
            """,
            (status, utc_now_iso(), opportunity_id),
        )

    def add_scenario_draft(self, scenario: dict[str, Any]) -> str:
        scenario_id = str(scenario["scenario_id"])
        now = utc_now_iso()
        self.database.execute(
            """
            INSERT INTO scenario_drafts (
                scenario_id, source_opportunity_id, symbol, setup_type, status,
                scenario_json, created_at, updated_at, reviewed_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(scenario_id) DO UPDATE SET
                status = excluded.status,
                scenario_json = excluded.scenario_json,
                updated_at = excluded.updated_at,
                reviewed_at = excluded.reviewed_at
            """,
            (
                scenario_id,
                scenario.get("source_opportunity_id") or scenario.get("opportunity_id"),
                str(scenario.get("symbol", "")).upper(),
                scenario.get("setup_type", "scenario"),
                scenario.get("status", "DRAFT"),
                json.dumps(scenario, sort_keys=True),
                scenario.get("created_at") or now,
                now,
                scenario.get("reviewed_at"),
            ),
        )
        return scenario_id

    def get_scenario_draft(self, scenario_id: str) -> dict[str, Any] | None:
        row = self.database.execute(
            "SELECT * FROM scenario_drafts WHERE scenario_id = ?",
            (scenario_id,),
        ).fetchone()
        return _row_to_dict(row) if row else None

    def list_scenario_drafts(
        self,
        *,
        source_opportunity_id: str | None = None,
        symbol: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        query = "SELECT * FROM scenario_drafts"
        clauses = []
        params: list[Any] = []
        if source_opportunity_id:
            clauses.append("source_opportunity_id = ?")
            params.append(source_opportunity_id)
        if symbol:
            clauses.append("symbol = ?")
            params.append(symbol.upper())
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        query += " ORDER BY created_at DESC LIMIT ?"
        params.append(limit)
        rows = self.database.execute(query, params).fetchall()
        return [_row_to_dict(row) for row in rows]

    def add_feature_snapshot(self, snapshot: dict[str, Any]) -> str:
        snapshot_id = str(snapshot["snapshot_id"])
        self.database.execute(
            """
            INSERT INTO feature_snapshots (
                snapshot_id, symbol, timeframe, features_json, created_at
            )
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                snapshot_id,
                str(snapshot.get("symbol", "")).upper(),
                snapshot.get("timeframe", "15m"),
                json.dumps(snapshot.get("features", {}), sort_keys=True),
                snapshot.get("created_at") or utc_now_iso(),
            ),
        )
        return snapshot_id

    def latest_feature_snapshot(
        self,
        symbol: str,
        *,
        timeframe: str | None = None,
    ) -> dict[str, Any] | None:
        query = "SELECT * FROM feature_snapshots WHERE symbol = ?"
        params: list[Any] = [symbol.upper()]
        if timeframe:
            query += " AND timeframe = ?"
            params.append(timeframe)
        query += " ORDER BY created_at DESC LIMIT 1"
        row = self.database.execute(query, params).fetchone()
        return _row_to_dict(row) if row else None

    def add_data_quality_event(self, event: dict[str, Any]) -> str:
        event_id = str(event["event_id"])
        self.database.execute(
            """
            INSERT INTO data_quality_events (
                event_id, symbol, severity, event_type, message, payload_json, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                event_id,
                str(event.get("symbol", "")).upper() if event.get("symbol") else None,
                event.get("severity", "INFO"),
                event.get("event_type", "data_quality"),
                event.get("message", ""),
                json.dumps(event.get("payload", {}), sort_keys=True),
                event.get("created_at") or utc_now_iso(),
            ),
        )
        return event_id

    def list_data_quality_events(
        self,
        *,
        symbol: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        query = "SELECT * FROM data_quality_events"
        params: list[Any] = []
        if symbol:
            query += " WHERE symbol = ?"
            params.append(symbol.upper())
        query += " ORDER BY created_at DESC LIMIT ?"
        params.append(limit)
        rows = self.database.execute(query, params).fetchall()
        return [_row_to_dict(row) for row in rows]

    def add_setup_score(self, score: dict[str, Any]) -> str:
        score_id = str(score["score_id"])
        self.database.execute(
            """
            INSERT INTO setup_scores (
                score_id, setup_id, scenario_id, opportunity_id, symbol,
                overall_score, score_json, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                score_id,
                score.get("setup_id"),
                score.get("scenario_id"),
                score.get("opportunity_id"),
                str(score.get("symbol", "")).upper() if score.get("symbol") else None,
                score.get("overall_score", 0),
                json.dumps(score, sort_keys=True),
                score.get("created_at") or utc_now_iso(),
            ),
        )
        return score_id

    def get_setup_score(self, score_id: str) -> dict[str, Any] | None:
        row = self.database.execute(
            "SELECT * FROM setup_scores WHERE score_id = ?",
            (score_id,),
        ).fetchone()
        return _row_to_dict(row) if row else None

    def list_setup_scores(
        self,
        *,
        setup_id: str | None = None,
        symbol: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        query = "SELECT * FROM setup_scores"
        clauses = []
        params: list[Any] = []
        if setup_id:
            clauses.append("setup_id = ?")
            params.append(setup_id)
        if symbol:
            clauses.append("symbol = ?")
            params.append(symbol.upper())
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        query += " ORDER BY created_at DESC LIMIT ?"
        params.append(limit)
        rows = self.database.execute(query, params).fetchall()
        return [_row_to_dict(row) for row in rows]

    def add_backtest_run(self, run: dict[str, Any]) -> str:
        backtest_id = str(run["backtest_id"])
        self.database.execute(
            """
            INSERT INTO backtest_runs (
                backtest_id, setup_id, scenario_id, symbol, timeframe, status,
                metrics_json, config_json, created_at, completed_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(backtest_id) DO UPDATE SET
                status = excluded.status,
                metrics_json = excluded.metrics_json,
                completed_at = excluded.completed_at
            """,
            (
                backtest_id,
                run.get("setup_id"),
                run.get("scenario_id"),
                str(run.get("symbol", "")).upper(),
                run.get("timeframe", "15m"),
                run.get("status", "COMPLETED"),
                json.dumps(run.get("metrics", {}), sort_keys=True),
                json.dumps(run.get("config", {}), sort_keys=True),
                run.get("created_at") or utc_now_iso(),
                run.get("completed_at"),
            ),
        )
        return backtest_id

    def list_backtest_runs(self, *, limit: int = 100) -> list[dict[str, Any]]:
        rows = self.database.execute(
            "SELECT * FROM backtest_runs ORDER BY created_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [_row_to_dict(row) for row in rows]

    def get_backtest_run(self, backtest_id: str) -> dict[str, Any] | None:
        row = self.database.execute(
            "SELECT * FROM backtest_runs WHERE backtest_id = ?",
            (backtest_id,),
        ).fetchone()
        return _row_to_dict(row) if row else None

    def add_backtest_trade(self, trade: dict[str, Any]) -> str:
        trade_id = str(trade["trade_id"])
        self.database.execute(
            """
            INSERT INTO backtest_trades (
                trade_id, backtest_id, symbol, entry_time, exit_time, entry_price,
                exit_price, quantity, pnl, payload_json, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                trade_id,
                trade["backtest_id"],
                str(trade.get("symbol", "")).upper(),
                trade.get("entry_time"),
                trade.get("exit_time"),
                trade.get("entry_price"),
                trade.get("exit_price"),
                trade.get("quantity"),
                trade.get("pnl"),
                json.dumps(trade.get("payload", trade), sort_keys=True),
                trade.get("created_at") or utc_now_iso(),
            ),
        )
        return trade_id

    def list_backtest_trades(self, backtest_id: str) -> list[dict[str, Any]]:
        rows = self.database.execute(
            """
            SELECT * FROM backtest_trades
            WHERE backtest_id = ?
            ORDER BY created_at, trade_id
            """,
            (backtest_id,),
        ).fetchall()
        return [_row_to_dict(row) for row in rows]

    def add_backtest_event(self, event: dict[str, Any]) -> str:
        event_id = str(event["event_id"])
        self.database.execute(
            """
            INSERT INTO backtest_events (
                event_id, backtest_id, event_type, symbol, payload_json, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                event_id,
                event["backtest_id"],
                event["event_type"],
                str(event.get("symbol", "")).upper() if event.get("symbol") else None,
                json.dumps(event.get("payload", {}), sort_keys=True),
                event.get("created_at") or utc_now_iso(),
            ),
        )
        return event_id

    def list_backtest_events(self, backtest_id: str) -> list[dict[str, Any]]:
        rows = self.database.execute(
            """
            SELECT * FROM backtest_events
            WHERE backtest_id = ?
            ORDER BY created_at, event_id
            """,
            (backtest_id,),
        ).fetchall()
        return [_row_to_dict(row) for row in rows]

    def add_model_benchmark(self, benchmark: dict[str, Any]) -> str:
        benchmark_id = str(benchmark["benchmark_id"])
        self.database.execute(
            """
            INSERT INTO model_benchmarks (
                benchmark_id, model_name, symbol, timeframe, horizon,
                metrics_json, beats_baseline, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                benchmark_id,
                benchmark.get("model_name", "ensemble"),
                str(benchmark.get("symbol", "")).upper(),
                benchmark.get("timeframe", "15m"),
                str(benchmark.get("horizon", "")),
                json.dumps(benchmark.get("metrics", {}), sort_keys=True),
                1 if benchmark.get("beats_baseline") else 0,
                benchmark.get("created_at") or utc_now_iso(),
            ),
        )
        return benchmark_id

    def list_model_benchmarks(
        self,
        *,
        symbol: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        query = "SELECT * FROM model_benchmarks"
        params: list[Any] = []
        if symbol:
            query += " WHERE symbol = ?"
            params.append(symbol.upper())
        query += " ORDER BY created_at DESC LIMIT ?"
        params.append(limit)
        rows = self.database.execute(query, params).fetchall()
        return [_row_to_dict(row) for row in rows]

    def add_model_scorecard(self, scorecard: dict[str, Any]) -> str:
        scorecard_id = str(scorecard["scorecard_id"])
        self.database.execute(
            """
            INSERT INTO model_scorecards (
                scorecard_id, model_name, symbol, timeframe, horizon_bars,
                metrics_json, baseline_comparison_json, selection_decision,
                sample_size, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                scorecard_id,
                scorecard.get("model_name", "timesfm"),
                str(scorecard.get("symbol", "")).upper(),
                scorecard.get("timeframe", "15m"),
                int(scorecard.get("horizon_bars") or 0),
                json.dumps(scorecard.get("metrics", {}), sort_keys=True),
                json.dumps(scorecard.get("baseline_comparison", {}), sort_keys=True),
                scorecard.get("selection_decision", "INSUFFICIENT_DATA"),
                int(scorecard.get("sample_size") or 0),
                scorecard.get("created_at") or utc_now_iso(),
            ),
        )
        return scorecard_id

    def list_model_scorecards(
        self,
        *,
        model_name: str | None = None,
        symbol: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        query = "SELECT * FROM model_scorecards"
        clauses = []
        params: list[Any] = []
        if model_name:
            clauses.append("model_name = ?")
            params.append(model_name)
        if symbol:
            clauses.append("symbol = ?")
            params.append(symbol.upper())
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        query += " ORDER BY created_at DESC LIMIT ?"
        params.append(limit)
        rows = self.database.execute(query, params).fetchall()
        return [_row_to_dict(row) for row in rows]

    def set_model_selection_policy(self, policy: dict[str, Any]) -> str:
        policy_id = str(policy["policy_id"])
        self.database.execute(
            """
            INSERT INTO model_selection_policy (
                policy_id, model_name, symbol, timeframe, horizon_bars,
                selection_decision, weight_multiplier, reason, policy_json, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(policy_id) DO UPDATE SET
                selection_decision = excluded.selection_decision,
                weight_multiplier = excluded.weight_multiplier,
                reason = excluded.reason,
                policy_json = excluded.policy_json,
                updated_at = excluded.updated_at
            """,
            (
                policy_id,
                policy.get("model_name", "timesfm"),
                str(policy.get("symbol", "")).upper() if policy.get("symbol") else None,
                policy.get("timeframe"),
                policy.get("horizon_bars"),
                policy.get("selection_decision", "INSUFFICIENT_DATA"),
                float(policy.get("weight_multiplier", 0.0)),
                policy.get("reason", ""),
                json.dumps(policy, sort_keys=True),
                policy.get("updated_at") or utc_now_iso(),
            ),
        )
        return policy_id

    def list_model_selection_policy(self) -> list[dict[str, Any]]:
        rows = self.database.execute(
            "SELECT * FROM model_selection_policy ORDER BY updated_at DESC"
        ).fetchall()
        return [_row_to_dict(row) for row in rows]

    def add_forecast_stack_experiment(self, experiment: dict[str, Any]) -> str:
        experiment_id = str(experiment["experiment_id"])
        self.database.execute(
            """
            INSERT INTO forecast_stack_experiments (
                experiment_id, name, symbols_json, timeframes_json, horizons_json,
                models_json, config_json, status, started_at, finished_at, summary_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                experiment_id,
                experiment.get("name", "Forecast stack comparison"),
                json.dumps(experiment.get("symbols", [])),
                json.dumps(experiment.get("timeframes", [])),
                json.dumps(experiment.get("horizons", [])),
                json.dumps(experiment.get("models", [])),
                json.dumps(experiment.get("config", {}), sort_keys=True),
                experiment.get("status", "RUNNING"),
                experiment.get("started_at") or utc_now_iso(),
                experiment.get("finished_at"),
                json.dumps(experiment.get("summary", {}), sort_keys=True),
            ),
        )
        return experiment_id

    def update_forecast_stack_experiment(
        self, experiment_id: str, *, status: str, finished_at: str, summary: dict[str, Any]
    ) -> None:
        self.database.execute(
            "UPDATE forecast_stack_experiments SET status = ?, finished_at = ?, summary_json = ? WHERE experiment_id = ?",
            (status, finished_at, json.dumps(summary, sort_keys=True), experiment_id),
        )

    def add_forecast_stack_result(self, result: dict[str, Any]) -> str:
        result_id = str(result["result_id"])
        self.database.execute(
            """
            INSERT INTO forecast_stack_results (
                result_id, experiment_id, model_name, symbol, timeframe,
                horizon_bars, metrics_json, trading_metrics_json, rank_overall,
                selected_for_symbol, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                result_id,
                result["experiment_id"],
                result["model_name"],
                result["symbol"],
                result["timeframe"],
                result["horizon_bars"],
                json.dumps(result.get("metrics", {}), sort_keys=True),
                json.dumps(result.get("trading_metrics", {}), sort_keys=True),
                result.get("rank_overall"),
                1 if result.get("selected_for_symbol") else 0,
                result.get("created_at") or utc_now_iso(),
            ),
        )
        return result_id

    def list_forecast_stack_experiments(self, *, limit: int = 100) -> list[dict[str, Any]]:
        rows = self.database.execute(
            "SELECT * FROM forecast_stack_experiments ORDER BY started_at DESC LIMIT ?", (limit,)
        ).fetchall()
        return [_decode_stack_experiment(row) for row in rows]

    def get_forecast_stack_experiment(self, experiment_id: str) -> dict[str, Any] | None:
        row = self.database.execute(
            "SELECT * FROM forecast_stack_experiments WHERE experiment_id = ?", (experiment_id,)
        ).fetchone()
        return _decode_stack_experiment(row) if row else None

    def list_forecast_stack_results(
        self, *, experiment_id: str | None = None, limit: int = 500
    ) -> list[dict[str, Any]]:
        query = "SELECT * FROM forecast_stack_results"
        params: list[Any] = []
        if experiment_id:
            query += " WHERE experiment_id = ?"
            params.append(experiment_id)
        query += " ORDER BY created_at DESC, rank_overall LIMIT ?"
        params.append(limit)
        return [_row_to_dict(row) for row in self.database.execute(query, params).fetchall()]

    def add_portfolio_snapshot(self, snapshot: dict[str, Any]) -> str:
        snapshot_id = str(snapshot["snapshot_id"])
        self.database.execute(
            """
            INSERT INTO portfolio_snapshots (
                snapshot_id, total_exposure_usd, open_positions_count,
                sector_exposure_json, symbol_exposure_json, correlation_json,
                warnings_json, size_reductions_json, risk_status, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                snapshot_id,
                snapshot.get("total_exposure_usd", 0),
                snapshot.get("open_positions_count", 0),
                json.dumps(snapshot.get("sector_exposure", {}), sort_keys=True),
                json.dumps(snapshot.get("symbol_exposure", {}), sort_keys=True),
                json.dumps(snapshot.get("correlation", {}), sort_keys=True),
                json.dumps(snapshot.get("warnings", []), sort_keys=True),
                json.dumps(snapshot.get("size_reductions", {}), sort_keys=True),
                snapshot.get("risk_status", "OK"),
                snapshot.get("created_at") or utc_now_iso(),
            ),
        )
        return snapshot_id

    def latest_portfolio_snapshot(self) -> dict[str, Any] | None:
        row = self.database.execute(
            "SELECT * FROM portfolio_snapshots ORDER BY created_at DESC LIMIT 1"
        ).fetchone()
        return _row_to_dict(row) if row else None

    def add_daily_report(self, report: dict[str, Any]) -> str:
        report_id = str(report["report_id"])
        self.database.execute(
            """
            INSERT INTO daily_reports (
                report_id, report_date, report_json, markdown, html, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(report_id) DO UPDATE SET
                report_json = excluded.report_json,
                markdown = excluded.markdown,
                html = excluded.html,
                created_at = excluded.created_at
            """,
            (
                report_id,
                report["report_date"],
                json.dumps(report.get("report", report), sort_keys=True),
                report.get("markdown", ""),
                report.get("html", ""),
                report.get("created_at") or utc_now_iso(),
            ),
        )
        return report_id

    def latest_daily_report(self) -> dict[str, Any] | None:
        row = self.database.execute(
            "SELECT * FROM daily_reports ORDER BY report_date DESC, created_at DESC LIMIT 1"
        ).fetchone()
        return _row_to_dict(row) if row else None

    def get_daily_report(self, report_date: str) -> dict[str, Any] | None:
        row = self.database.execute(
            """
            SELECT * FROM daily_reports
            WHERE report_date = ?
            ORDER BY created_at DESC
            LIMIT 1
            """,
            (report_date,),
        ).fetchone()
        return _row_to_dict(row) if row else None

    def set_bot_state(self, key: str, value: dict[str, Any]) -> None:
        self.database.execute(
            """
            INSERT INTO bot_state (key, value_json, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(key) DO UPDATE SET
                value_json = excluded.value_json,
                updated_at = excluded.updated_at
            """,
            (key, json.dumps(value, sort_keys=True), utc_now_iso()),
        )

    def get_bot_state(self, key: str, default: dict[str, Any] | None = None) -> dict[str, Any]:
        row = self.database.execute(
            "SELECT value_json FROM bot_state WHERE key = ?",
            (key,),
        ).fetchone()
        if not row:
            return default or {}
        return json.loads(row["value_json"])
