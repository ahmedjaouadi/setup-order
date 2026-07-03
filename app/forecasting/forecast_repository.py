from __future__ import annotations

import json
from typing import Any

from app.models import utc_now_iso
from app.storage.database import Database
from app.utils.id_generator import new_id


def _row_to_dict(row: Any) -> dict[str, Any]:
    result = dict(row)
    payload = json.loads(result.pop("forecast_payload_json") or "{}")
    result["forecast"] = payload
    result["used_for_decision"] = bool(result.get("used_for_decision"))
    return result


class ForecastRepository:
    def __init__(self, database: Database) -> None:
        self.database = database

    def insert_forecast(self, forecast: dict[str, Any]) -> int:
        now = utc_now_iso()
        forecast.setdefault("forecast_id", new_id("forecast"))
        forecast["used_for_decision"] = False
        forecast["decision_impact"] = "NONE"
        forecast["execution_allowed"] = False
        cursor = self.database.execute(
            """
            INSERT INTO forecast_metrics (
                setup_id, symbol, timeframe, model_name, model_version, target,
                context_bars, horizon_bars, input_start_time, input_end_time,
                generated_at, current_price, forecast_expected_return_pct,
                forecast_last_price, metric_score, forecast_status, confidence,
                q10_above_support, median_above_entry_trigger,
                forecast_payload_json, used_for_decision, status, error, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                forecast.get("setup_id"),
                str(forecast.get("symbol", "")).upper(),
                forecast.get("timeframe"),
                forecast.get("model"),
                forecast.get("model_version", forecast.get("model")),
                forecast.get("target"),
                forecast.get("context_bars"),
                forecast.get("horizon_bars"),
                forecast.get("input_start_time"),
                forecast.get("input_end_time"),
                forecast.get("generated_at"),
                forecast.get("current_price"),
                forecast.get("forecast_expected_return_pct"),
                forecast.get("forecast_last_price"),
                forecast.get("metric_score"),
                forecast.get("forecast_status"),
                forecast.get("confidence"),
                _bool_to_int(forecast.get("q10_above_support")),
                _bool_to_int(forecast.get("median_above_entry_trigger")),
                json.dumps(forecast, sort_keys=True),
                0,
                forecast.get("status"),
                forecast.get("error"),
                now,
            ),
        )
        metric_id = int(cursor.lastrowid)
        forecast["forecast_metric_id"] = metric_id
        self.database.execute(
            """
            INSERT INTO forecast_runs (
                forecast_id, setup_id, scenario_id, symbol, timeframe,
                model_name, status, forecast_json, error, created_at, completed_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(forecast_id) DO UPDATE SET
                status = excluded.status,
                forecast_json = excluded.forecast_json,
                error = excluded.error,
                completed_at = excluded.completed_at
            """,
            (
                forecast["forecast_id"],
                forecast.get("setup_id"),
                forecast.get("scenario_id"),
                str(forecast.get("symbol", "")).upper(),
                forecast.get("timeframe"),
                forecast.get("model"),
                forecast.get("status"),
                json.dumps(forecast, sort_keys=True),
                forecast.get("error"),
                now,
                now,
            ),
        )
        return metric_id

    def get_forecast_run(self, forecast_id: str) -> dict[str, Any] | None:
        row = self.database.execute(
            "SELECT * FROM forecast_runs WHERE forecast_id = ?",
            (forecast_id,),
        ).fetchone()
        return _forecast_run_to_dict(row) if row else None

    def list_forecast_runs(
        self,
        *,
        symbol: str | None = None,
        model_name: str | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        query = "SELECT * FROM forecast_runs"
        clauses: list[str] = []
        params: list[Any] = []
        if symbol:
            clauses.append("symbol = ?")
            params.append(symbol.upper())
        if model_name:
            clauses.append("model_name = ?")
            params.append(model_name)
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        query += " ORDER BY created_at DESC LIMIT ?"
        params.append(limit)
        rows = self.database.execute(query, params).fetchall()
        return [_forecast_run_to_dict(row) for row in rows]

    def latest_forecast(
        self,
        symbol: str,
        *,
        timeframe: str | None = None,
        model_name: str | None = None,
        setup_id: str | None = None,
    ) -> dict[str, Any] | None:
        query = "SELECT * FROM forecast_metrics WHERE symbol = ?"
        params: list[Any] = [symbol.upper()]
        if timeframe:
            query += " AND timeframe = ?"
            params.append(timeframe)
        if model_name:
            query += " AND model_name = ?"
            params.append(model_name)
        if setup_id:
            query += " AND setup_id = ?"
            params.append(setup_id)
        query += " ORDER BY generated_at DESC, id DESC LIMIT 1"
        row = self.database.execute(query, params).fetchone()
        return _row_to_dict(row) if row else None

    def history(
        self,
        symbol: str,
        *,
        timeframe: str | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        query = "SELECT * FROM forecast_metrics WHERE symbol = ?"
        params: list[Any] = [symbol.upper()]
        if timeframe:
            query += " AND timeframe = ?"
            params.append(timeframe)
        query += " ORDER BY generated_at DESC, id DESC LIMIT ?"
        params.append(limit)
        rows = self.database.execute(query, params).fetchall()
        return [_row_to_dict(row) for row in rows]

    def latest_for_model(self, model_name: str) -> dict[str, Any] | None:
        row = self.database.execute(
            """
            SELECT * FROM forecast_metrics
            WHERE model_name = ?
            ORDER BY generated_at DESC, id DESC
            LIMIT 1
            """,
            (model_name,),
        ).fetchone()
        return _row_to_dict(row) if row else None

    def history_for_model(
        self,
        model_name: str,
        *,
        status: str | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        query = "SELECT * FROM forecast_metrics WHERE model_name = ?"
        params: list[Any] = [model_name]
        if status:
            query += " AND status = ?"
            params.append(status)
        query += " ORDER BY generated_at DESC, id DESC LIMIT ?"
        params.append(limit)
        return [_row_to_dict(row) for row in self.database.execute(query, params).fetchall()]

    def get_forecast_metric(self, forecast_id: int | str) -> dict[str, Any] | None:
        row = self.database.execute(
            "SELECT * FROM forecast_metrics WHERE id = ?",
            (int(forecast_id),),
        ).fetchone()
        return _row_to_dict(row) if row else None

    def latest_for_symbols(
        self,
        symbols: list[str],
        *,
        timeframe: str | None = None,
    ) -> dict[str, dict[str, Any]]:
        latest: dict[str, dict[str, Any]] = {}
        for symbol in sorted({item.upper() for item in symbols if item}):
            row = self.latest_forecast(symbol, timeframe=timeframe)
            if row:
                latest[symbol] = row
        return latest

    def insert_ensemble(self, ensemble: dict[str, Any]) -> str:
        ensemble_id = str(ensemble["ensemble_id"])
        self.database.execute(
            """
            INSERT INTO forecast_ensembles (
                ensemble_id, symbol, timeframe, horizon, status,
                member_forecast_ids_json, ensemble_json, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(ensemble_id) DO UPDATE SET
                status = excluded.status,
                member_forecast_ids_json = excluded.member_forecast_ids_json,
                ensemble_json = excluded.ensemble_json
            """,
            (
                ensemble_id,
                str(ensemble.get("symbol", "")).upper(),
                ensemble.get("timeframe"),
                str(ensemble.get("horizon_bars") or ensemble.get("horizon") or ""),
                ensemble.get("status", "OK"),
                json.dumps(ensemble.get("member_forecast_ids", []), sort_keys=True),
                json.dumps(ensemble, sort_keys=True),
                ensemble.get("created_at") or utc_now_iso(),
            ),
        )
        return ensemble_id

    def get_ensemble(self, ensemble_id: str) -> dict[str, Any] | None:
        row = self.database.execute(
            "SELECT * FROM forecast_ensembles WHERE ensemble_id = ?",
            (ensemble_id,),
        ).fetchone()
        return _ensemble_row_to_dict(row) if row else None

    def list_ensembles(
        self,
        *,
        symbol: str | None = None,
        timeframe: str | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        query = "SELECT * FROM forecast_ensembles"
        clauses = []
        params: list[Any] = []
        if symbol:
            clauses.append("symbol = ?")
            params.append(symbol.upper())
        if timeframe:
            clauses.append("timeframe = ?")
            params.append(timeframe)
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        query += " ORDER BY created_at DESC LIMIT ?"
        params.append(limit)
        rows = self.database.execute(query, params).fetchall()
        return [_ensemble_row_to_dict(row) for row in rows]


def _bool_to_int(value: Any) -> int | None:
    if value is None:
        return None
    return 1 if bool(value) else 0


def _ensemble_row_to_dict(row: Any) -> dict[str, Any]:
    result = dict(row)
    result["member_forecast_ids"] = json.loads(
        result.pop("member_forecast_ids_json") or "[]"
    )
    result["ensemble"] = json.loads(result.pop("ensemble_json") or "{}")
    return result


def _forecast_run_to_dict(row: Any) -> dict[str, Any]:
    result = dict(row)
    result["forecast"] = json.loads(result.pop("forecast_json") or "{}")
    return result
