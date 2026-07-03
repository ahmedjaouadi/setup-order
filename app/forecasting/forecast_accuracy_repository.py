from __future__ import annotations

import json
from typing import Any

from app.storage.database import Database


class ForecastAccuracyRepository:
    """Persistence boundary for forecast outcomes and reliability scorecards."""

    def __init__(self, database: Database) -> None:
        self.database = database

    def create_outcome(self, outcome: dict[str, Any]) -> str:
        self.database.execute(
            """
            INSERT OR IGNORE INTO forecast_outcomes (
                outcome_id, forecast_id, model_name, model_version, symbol,
                timeframe, horizon_bars, generated_at, evaluation_due_at,
                forecast_price_start, forecast_price_end, forecast_direction,
                forecast_target_time, predicted_return_pct, direction_confidence,
                entry_price_reference, stop_price_reference,
                status, payload_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                outcome["outcome_id"],
                int(outcome["forecast_id"]),
                outcome["model_name"],
                outcome.get("model_version"),
                outcome["symbol"],
                outcome["timeframe"],
                int(outcome["horizon_bars"]),
                outcome["generated_at"],
                outcome["evaluation_due_at"],
                outcome.get("forecast_price_start"),
                outcome.get("forecast_price_end"),
                outcome.get("forecast_direction"),
                outcome.get("forecast_target_time") or outcome["evaluation_due_at"],
                outcome.get("predicted_return_pct"),
                outcome.get("direction_confidence"),
                outcome.get("entry_price_reference"),
                outcome.get("stop_price_reference"),
                outcome.get("status", "PENDING"),
                json.dumps(outcome, sort_keys=True),
            ),
        )
        return str(outcome["outcome_id"])

    def due_outcomes(self, due_at: str, *, limit: int = 500) -> list[dict[str, Any]]:
        rows = self.database.execute(
            """
            SELECT * FROM forecast_outcomes
            WHERE status = 'PENDING' AND evaluation_due_at <= ?
            ORDER BY evaluation_due_at, outcome_id
            LIMIT ?
            """,
            (due_at, limit),
        ).fetchall()
        return [_decode(row) for row in rows]

    def update_outcome(self, outcome_id: str, values: dict[str, Any]) -> None:
        self.database.execute(
            """
            UPDATE forecast_outcomes SET
                evaluated_at = ?, actual_price_end = ?, actual_direction = ?,
                direction_correct = ?, absolute_error = ?, squared_error = ?,
                percentage_error = ?, actual_return_pct = ?, signed_error = ?,
                entry_touched_before_horizon = ?, stop_touched_before_horizon = ?,
                stop_touched_before_entry = ?, quality_bucket = ?,
                status = ?, payload_json = ?
            WHERE outcome_id = ?
            """,
            (
                values.get("evaluated_at"),
                values.get("actual_price_end"),
                values.get("actual_direction"),
                _bool_int(values.get("direction_correct")),
                values.get("absolute_error"),
                values.get("squared_error"),
                values.get("percentage_error"),
                values.get("actual_return_pct"),
                values.get("signed_error"),
                _bool_int(values.get("entry_touched_before_horizon")),
                _bool_int(values.get("stop_touched_before_horizon")),
                _bool_int(values.get("stop_touched_before_entry")),
                values.get("quality_bucket"),
                values.get("status", "EVALUATED"),
                json.dumps(values, sort_keys=True),
                outcome_id,
            ),
        )

    def outcomes(
        self,
        *,
        model_name: str | None = None,
        symbol: str | None = None,
        timeframe: str | None = None,
        horizon_bars: int | None = None,
        status: str | None = None,
        limit: int = 500,
    ) -> list[dict[str, Any]]:
        query = "SELECT * FROM forecast_outcomes"
        clauses: list[str] = []
        params: list[Any] = []
        for column, value in (
            ("model_name", model_name),
            ("symbol", symbol.upper() if symbol else None),
            ("timeframe", timeframe),
            ("horizon_bars", int(horizon_bars) if horizon_bars is not None else None),
            ("status", status),
        ):
            if value is not None:
                clauses.append(f"{column} = ?")
                params.append(value)
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        query += " ORDER BY generated_at DESC LIMIT ?"
        params.append(limit)
        rows = self.database.execute(query, params).fetchall()
        return [_decode(row) for row in rows]

    def replace_scorecards(self, scorecards: list[dict[str, Any]]) -> None:
        with self.database.transaction() as conn:
            conn.execute("DELETE FROM forecast_accuracy_scorecards")
            conn.executemany(
                """
                INSERT INTO forecast_accuracy_scorecards (
                    scorecard_id, model_name, symbol, timeframe, horizon_bars,
                    sample_size, direction_accuracy, mae, rmse, mape,
                    median_absolute_error, entry_touch_accuracy,
                    stop_before_entry_error_rate, calibration_score,
                    enough_data, min_required_samples,
                    reliability_grade, metrics_json, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        item["scorecard_id"], item["model_name"], item.get("symbol"),
                        item.get("timeframe"), item.get("horizon_bars"),
                        item["sample_size"], item.get("direction_accuracy"),
                        item.get("mae"), item.get("rmse"), item.get("mape"),
                        item.get("median_absolute_error"), item.get("entry_touch_accuracy"),
                        item.get("stop_before_entry_error_rate"), item.get("calibration_score"),
                        1 if item.get("enough_data") else 0, item.get("min_required_samples", 30),
                        item["reliability_grade"], json.dumps(item, sort_keys=True),
                        item["updated_at"],
                    )
                    for item in scorecards
                ],
            )

    def scorecards(
        self,
        model_name: str,
        *,
        symbol: str | None = None,
        timeframe: str | None = None,
        horizon_bars: int | None = None,
    ) -> list[dict[str, Any]]:
        query = "SELECT * FROM forecast_accuracy_scorecards WHERE model_name = ?"
        params: list[Any] = [model_name]
        if symbol:
            query += " AND symbol = ?"
            params.append(symbol.upper())
        if timeframe:
            query += " AND timeframe = ?"
            params.append(timeframe)
        if horizon_bars is not None:
            query += " AND horizon_bars = ?"
            params.append(int(horizon_bars))
        query += " ORDER BY sample_size DESC, updated_at DESC"
        return [_decode(row) for row in self.database.execute(query, params).fetchall()]


def _decode(row: Any) -> dict[str, Any]:
    result = dict(row)
    for key in ("payload_json", "metrics_json"):
        if key in result:
            result[key.removesuffix("_json")] = json.loads(result.pop(key) or "{}")
    if result.get("direction_correct") is not None:
        result["direction_correct"] = bool(result["direction_correct"])
    for key in (
        "entry_touched_before_horizon",
        "stop_touched_before_horizon",
        "stop_touched_before_entry",
        "enough_data",
    ):
        if result.get(key) is not None:
            result[key] = bool(result[key])
    if "generated_at" in result:
        result.setdefault("forecast_generated_at", result["generated_at"])
    if "evaluation_due_at" in result:
        result.setdefault("forecast_target_time", result["evaluation_due_at"])
    if "forecast_price_start" in result:
        result.setdefault("price_at_forecast", result["forecast_price_start"])
    if "forecast_price_end" in result:
        result.setdefault("predicted_price", result["forecast_price_end"])
    if "actual_price_end" in result:
        result.setdefault("actual_price_at_horizon", result["actual_price_end"])
    if "forecast_direction" in result:
        result.setdefault("predicted_direction", result["forecast_direction"])
    if "percentage_error" in result:
        result.setdefault("absolute_percentage_error", result["percentage_error"])
    if "status" in result:
        result.setdefault("outcome_status", result["status"])
    if "mae" in result:
        result.setdefault("mean_absolute_error", result["mae"])
    if "mape" in result:
        result.setdefault("mean_absolute_percentage_error", result["mape"])
    if "updated_at" in result:
        result.setdefault("computed_at", result["updated_at"])
    return result


def _bool_int(value: Any) -> int | None:
    if value is None:
        return None
    return 1 if bool(value) else 0
