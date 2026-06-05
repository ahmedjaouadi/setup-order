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
    if "enabled" in result:
        result["enabled"] = bool(result["enabled"])
    if "setup_id" in result and "config" in result:
        _enrich_setup_row(result)
    return result


def _enrich_setup_row(row: dict[str, Any]) -> None:
    config = row.get("config", {})
    if not isinstance(config, dict):
        return
    role = str(config.get("setup_role", "ENTRY_AND_MANAGEMENT"))
    trigger = _estimated_trigger_price(config)
    limit = _maximum_limit_price(config, trigger)
    worst_case = limit or trigger
    protective_stop = _protective_stop(config)
    quantity = _maximum_quantity(config, worst_case, protective_stop)
    row.setdefault("setup_role", role)
    row.setdefault("entry_trigger", _rounded(trigger))
    row.setdefault("maximum_limit_price", _rounded(limit))
    row.setdefault("worst_case_entry_price", _rounded(worst_case))
    row.setdefault("protective_stop", _rounded(protective_stop))
    row.setdefault("maximum_quantity", quantity)
    row.setdefault(
        "maximum_risk",
        _rounded(_risk_amount(quantity, worst_case, protective_stop)),
    )
    row.setdefault("position_source", _position_source(config))
    row.setdefault("reconciliation_status", _reconciliation_status(row["status"]))


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


def _protective_stop(config: dict[str, Any]) -> float | None:
    risk = config.get("risk", {})
    if not isinstance(risk, dict):
        return None
    value = risk.get("protective_stop", risk.get("initial_stop_loss"))
    return float(value) if value is not None else None


def _maximum_quantity(
    config: dict[str, Any],
    worst_case: float | None,
    protective_stop: float | None,
) -> int | None:
    if worst_case is None or protective_stop is None:
        return None
    risk = config.get("risk", {})
    if not isinstance(risk, dict):
        return None
    max_position = float(risk.get("max_position_amount_usd", 0.0) or 0.0)
    max_risk = float(risk.get("max_risk_usd", 0.0) or 0.0)
    risk_per_share = abs(worst_case - protective_stop)
    if worst_case <= 0 or max_position <= 0 or max_risk <= 0 or risk_per_share <= 0:
        return None
    return min(floor(max_position / worst_case), floor(max_risk / risk_per_share))


def _risk_amount(
    quantity: int | None,
    worst_case: float | None,
    protective_stop: float | None,
) -> float | None:
    if quantity is None or worst_case is None or protective_stop is None:
        return None
    return quantity * abs(worst_case - protective_stop)


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
        rows = self.database.execute(
            "SELECT * FROM setups ORDER BY symbol, setup_id"
        ).fetchall()
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
    ) -> None:
        self.database.execute(
            """
            UPDATE setups
            SET status = ?, last_event = ?, updated_at = ?
            WHERE setup_id = ?
            """,
            (status, last_event, utc_now_iso(), setup_id),
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
            rows = self.database.execute(
                "SELECT * FROM orders ORDER BY created_at DESC"
            ).fetchall()
        return [_row_to_dict(row) for row in rows]

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
        rows = self.database.execute(
            "SELECT * FROM positions ORDER BY symbol"
        ).fetchall()
        return [_row_to_dict(row) for row in rows]

    def get_position(self, symbol: str) -> dict[str, Any] | None:
        row = self.database.execute(
            "SELECT * FROM positions WHERE symbol = ?",
            (symbol.upper(),),
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

    def list_events(
        self,
        limit: int = 100,
        setup_id: str | None = None,
        symbol: str | None = None,
        level: str | None = None,
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
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        query += " ORDER BY id DESC LIMIT ?"
        params.append(limit)
        rows = self.database.execute(query, params).fetchall()
        return [_row_to_dict(row) for row in rows]

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
