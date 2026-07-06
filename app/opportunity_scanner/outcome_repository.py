from __future__ import annotations

import json
from typing import Any

from app.storage.database import Database


class OutcomeRepository:
    """Persistence boundary for detection outcomes (`detection_outcomes`)."""

    def __init__(self, database: Database) -> None:
        self.database = database

    def create_outcome(self, outcome: dict[str, Any]) -> str:
        self.database.execute(
            """
            INSERT OR IGNORE INTO detection_outcomes (
                outcome_id, technique_id, symbol, detected_at, price_at_detection,
                features_snapshot, r_unit_pct, horizon, evaluation_due_at,
                price_at_horizon, forward_return_pct, mfe_pct, mae_pct, label_1r,
                human_feedback, status, payload_json, created_at, opportunity_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                outcome["outcome_id"],
                outcome["technique_id"],
                str(outcome["symbol"]).upper(),
                outcome["detected_at"],
                outcome.get("price_at_detection"),
                json.dumps(outcome.get("features_snapshot") or {}, sort_keys=True),
                outcome.get("r_unit_pct"),
                outcome["horizon"],
                outcome["evaluation_due_at"],
                outcome.get("price_at_horizon"),
                outcome.get("forward_return_pct"),
                outcome.get("mfe_pct"),
                outcome.get("mae_pct"),
                _bool_int(outcome.get("label_1r")),
                outcome.get("human_feedback"),
                outcome.get("status", "PENDING"),
                json.dumps(outcome.get("payload") or {}, sort_keys=True),
                outcome["created_at"],
                outcome.get("opportunity_id"),
            ),
        )
        return str(outcome["outcome_id"])

    def pending_exists(self, technique_id: str, symbol: str, horizon: str, due_date: str) -> bool:
        """Dedup guard: is an identical detection already pending for that window?

        `due_date` is the date part (YYYY-MM-DD) of the evaluation due time, so
        two detections on the same session collapse to one outcome per horizon.
        """
        row = self.database.execute(
            """
            SELECT 1 FROM detection_outcomes
            WHERE technique_id = ? AND symbol = ? AND horizon = ?
              AND status = 'PENDING' AND substr(evaluation_due_at, 1, 10) = ?
            LIMIT 1
            """,
            (technique_id, symbol.upper(), horizon, due_date),
        ).fetchone()
        return row is not None

    def due_outcomes(self, due_at: str, *, limit: int = 500) -> list[dict[str, Any]]:
        rows = self.database.execute(
            """
            SELECT * FROM detection_outcomes
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
            UPDATE detection_outcomes SET
                price_at_horizon = ?, forward_return_pct = ?, mfe_pct = ?,
                mae_pct = ?, label_1r = ?, status = ?, payload_json = ?
            WHERE outcome_id = ?
            """,
            (
                values.get("price_at_horizon"),
                values.get("forward_return_pct"),
                values.get("mfe_pct"),
                values.get("mae_pct"),
                _bool_int(values.get("label_1r")),
                values.get("status", "EVALUATED"),
                json.dumps(values.get("payload") or {}, sort_keys=True),
                outcome_id,
            ),
        )

    def set_feedback(self, outcome_id: str, feedback: str) -> bool:
        cursor = self.database.execute(
            "UPDATE detection_outcomes SET human_feedback = ? WHERE outcome_id = ?",
            (feedback, outcome_id),
        )
        return cursor.rowcount > 0

    def outcomes_for_technique(
        self, technique_id: str, *, status: str | None = None, limit: int = 500
    ) -> list[dict[str, Any]]:
        query = "SELECT * FROM detection_outcomes WHERE technique_id = ?"
        params: list[Any] = [technique_id]
        if status is not None:
            query += " AND status = ?"
            params.append(status)
        query += " ORDER BY detected_at DESC LIMIT ?"
        params.append(limit)
        rows = self.database.execute(query, params).fetchall()
        return [_decode(row) for row in rows]

    def outcomes_for_opportunity(
        self, opportunity_id: str, *, limit: int = 50
    ) -> list[dict[str, Any]]:
        rows = self.database.execute(
            """
            SELECT * FROM detection_outcomes
            WHERE opportunity_id = ?
            ORDER BY detected_at DESC, horizon
            LIMIT ?
            """,
            (opportunity_id, limit),
        ).fetchall()
        return [_decode(row) for row in rows]

    def counts_by_technique(self) -> dict[str, dict[str, int]]:
        """Outcome counters per technique: statuses + correct/wrong/indeterminate.

        `correct`/`wrong`/`indeterminate` only count EVALUATED rows
        (label_1r = 1 / 0 / NULL); PENDING and EXPIRED are tracked separately.
        """
        rows = self.database.execute("""
            SELECT
                technique_id,
                COUNT(*) AS detections_total,
                SUM(CASE WHEN status = 'PENDING' THEN 1 ELSE 0 END) AS pending,
                SUM(CASE WHEN status = 'EVALUATED' THEN 1 ELSE 0 END) AS evaluated,
                SUM(CASE WHEN status = 'EXPIRED' THEN 1 ELSE 0 END) AS expired,
                SUM(CASE WHEN status = 'EVALUATED' AND label_1r = 1 THEN 1 ELSE 0 END)
                    AS correct,
                SUM(CASE WHEN status = 'EVALUATED' AND label_1r = 0 THEN 1 ELSE 0 END)
                    AS wrong,
                SUM(CASE WHEN status = 'EVALUATED' AND label_1r IS NULL THEN 1 ELSE 0 END)
                    AS indeterminate
            FROM detection_outcomes
            GROUP BY technique_id
            """).fetchall()
        return {
            str(row["technique_id"]): {
                "detections_total": int(row["detections_total"] or 0),
                "pending": int(row["pending"] or 0),
                "evaluated": int(row["evaluated"] or 0),
                "expired": int(row["expired"] or 0),
                "correct": int(row["correct"] or 0),
                "wrong": int(row["wrong"] or 0),
                "indeterminate": int(row["indeterminate"] or 0),
            }
            for row in rows
        }

    def stale_pending_count(self, cutoff: str) -> int:
        """PENDING outcomes past due since before `cutoff`: the evaluation job
        should have consumed them — a non-zero count signals a silent outage."""
        row = self.database.execute(
            """
            SELECT COUNT(*) AS stale FROM detection_outcomes
            WHERE status = 'PENDING' AND evaluation_due_at <= ?
            """,
            (cutoff,),
        ).fetchone()
        return int(row["stale"] or 0)

    def evaluated_grouped_by_technique(self) -> dict[str, list[dict[str, Any]]]:
        rows = self.database.execute(
            "SELECT * FROM detection_outcomes WHERE status = 'EVALUATED'"
        ).fetchall()
        grouped: dict[str, list[dict[str, Any]]] = {}
        for raw in rows:
            row = _decode(raw)
            grouped.setdefault(str(row["technique_id"]), []).append(row)
        return grouped


def _decode(row: Any) -> dict[str, Any]:
    result = dict(row)
    for key in ("features_snapshot", "payload_json"):
        if key in result:
            target = "payload" if key == "payload_json" else key
            result[target] = json.loads(result.pop(key) or "{}")
    if result.get("label_1r") is not None:
        result["label_1r"] = int(result["label_1r"])
    return result


def _bool_int(value: Any) -> int | None:
    if value is None:
        return None
    return int(value)
