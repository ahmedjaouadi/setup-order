from __future__ import annotations

import json
from typing import Any

from app.models import utc_now_iso
from app.storage.database import Database


def _load_json(value: str | None, default: Any) -> Any:
    if value in (None, ""):
        return default
    return json.loads(value)


class IntelligenceRepository:
    def __init__(self, database: Database) -> None:
        self.database = database

    def save_analysis_bundle(self, bundle: dict[str, Any]) -> None:
        with self.database.transaction() as conn:
            self._insert_analysis(conn, bundle["analysis"])
            self._insert_scenarios(conn, bundle.get("scenarios", []))
            self._insert_fields(conn, bundle.get("fields", []))
            self._insert_ambiguities(conn, bundle.get("ambiguities", []))

    def _insert_analysis(self, conn: Any, analysis: dict[str, Any]) -> None:
        now = utc_now_iso()
        conn.execute(
            """
            INSERT INTO semantic_analyses (
                analysis_id, setup_id, symbol, request_id, idempotency_key,
                analysis_hash, source_type, raw_input_text, primary_scenario_id,
                save_validation_json, arm_validation_json, issues_json, confidence_json,
                schema_version, parser_version, canonical_mapper_version,
                prompt_version, llm_model, previous_analysis_id, provider_name,
                created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                analysis["analysis_id"],
                analysis.get("setup_id"),
                analysis["symbol"],
                analysis.get("request_id"),
                analysis.get("idempotency_key"),
                analysis["analysis_hash"],
                analysis["source_type"],
                analysis["raw_input_text"],
                analysis.get("primary_scenario_id"),
                json.dumps(analysis["save_validation"], sort_keys=True),
                json.dumps(analysis["arm_validation"], sort_keys=True),
                json.dumps(analysis["issues"], sort_keys=True),
                json.dumps(analysis.get("confidence", {}), sort_keys=True),
                analysis["schema_version"],
                analysis["parser_version"],
                analysis["canonical_mapper_version"],
                analysis.get("prompt_version"),
                analysis.get("llm_model"),
                analysis.get("previous_analysis_id"),
                analysis["provider_name"],
                analysis.get("created_at", now),
                analysis.get("updated_at", now),
            ),
        )

    def _insert_scenarios(self, conn: Any, scenarios: list[dict[str, Any]]) -> None:
        now = utc_now_iso()
        for scenario in scenarios:
            conn.execute(
                """
                INSERT INTO extracted_scenarios (
                    scenario_id, analysis_id, symbol, scenario_name, scenario_role,
                    setup_type, status, selected, armed, confidence_json, canonical_config_json,
                    created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    scenario["scenario_id"],
                    scenario["analysis_id"],
                    scenario["symbol"],
                    scenario["scenario_name"],
                    scenario["scenario_role"],
                    scenario["setup_type"],
                    scenario["status"],
                    1 if scenario.get("selected") else 0,
                    1 if scenario.get("armed") else 0,
                    json.dumps(scenario.get("confidence", {}), sort_keys=True),
                    json.dumps(scenario["canonical_config"], sort_keys=True),
                    scenario.get("created_at", now),
                    scenario.get("updated_at", now),
                ),
            )

    def _insert_fields(self, conn: Any, fields: list[dict[str, Any]]) -> None:
        now = utc_now_iso()
        for field in fields:
            conn.execute(
                """
                INSERT INTO extracted_fields (
                    analysis_id, scenario_id, raw_key, normalized_key, canonical_path,
                    raw_value, parsed_value_json, source_text, source_line_start,
                    source_line_end, extraction_method, confidence, validation_status,
                    created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    field["analysis_id"],
                    field.get("scenario_id"),
                    field["raw_key"],
                    field["normalized_key"],
                    field["canonical_path"],
                    field.get("raw_value"),
                    json.dumps(field.get("parsed_value"), sort_keys=True),
                    field.get("source_text"),
                    field.get("source_line_start"),
                    field.get("source_line_end"),
                    field["extraction_method"],
                    field["confidence"],
                    field["validation_status"],
                    field.get("created_at", now),
                    field.get("updated_at", now),
                ),
            )

    def _insert_ambiguities(self, conn: Any, ambiguities: list[dict[str, Any]]) -> None:
        now = utc_now_iso()
        for ambiguity in ambiguities:
            conn.execute(
                """
                INSERT INTO ambiguities (
                    ambiguity_id, analysis_id, scenario_id, field_path, message,
                    options_json, metadata_json, status, resolution_json, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    ambiguity["ambiguity_id"],
                    ambiguity["analysis_id"],
                    ambiguity.get("scenario_id"),
                    ambiguity["field_path"],
                    ambiguity["message"],
                    json.dumps(ambiguity.get("options", []), sort_keys=True),
                    json.dumps(ambiguity.get("metadata", {}), sort_keys=True),
                    ambiguity["status"],
                    json.dumps(ambiguity.get("resolution", {}), sort_keys=True),
                    ambiguity.get("created_at", now),
                    ambiguity.get("updated_at", now),
                ),
            )

    def get_analysis_by_idempotency_key(self, idempotency_key: str) -> dict[str, Any] | None:
        row = self.database.execute(
            """
            SELECT * FROM semantic_analyses
            WHERE idempotency_key = ?
            ORDER BY created_at DESC
            LIMIT 1
            """,
            (idempotency_key,),
        ).fetchone()
        return self._analysis_row(row) if row else None

    def get_latest_analysis_by_hash(self, analysis_hash: str) -> dict[str, Any] | None:
        row = self.database.execute(
            """
            SELECT * FROM semantic_analyses
            WHERE analysis_hash = ?
            ORDER BY created_at DESC
            LIMIT 1
            """,
            (analysis_hash,),
        ).fetchone()
        return self._analysis_row(row) if row else None

    def get_latest_analysis_for_setup(self, setup_id: str) -> dict[str, Any] | None:
        row = self.database.execute(
            """
            SELECT * FROM semantic_analyses
            WHERE setup_id = ?
            ORDER BY created_at DESC, rowid DESC
            LIMIT 1
            """,
            (setup_id,),
        ).fetchone()
        return self._analysis_row(row) if row else None

    def list_analyses_for_setup(self, setup_id: str) -> list[dict[str, Any]]:
        rows = self.database.execute(
            """
            SELECT * FROM semantic_analyses
            WHERE setup_id = ?
            ORDER BY created_at DESC, rowid DESC
            """,
            (setup_id,),
        ).fetchall()
        return [self._analysis_row(row) for row in rows]

    def list_analysis_summaries_for_setup(
        self,
        setup_id: str,
        limit: int | None = None,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        query = """
            SELECT
                a.*,
                (
                    SELECT COUNT(*)
                    FROM extracted_scenarios s
                    WHERE s.analysis_id = a.analysis_id
                ) AS scenario_count,
                (
                    SELECT COUNT(*)
                    FROM ambiguities amb
                    WHERE amb.analysis_id = a.analysis_id
                ) AS ambiguity_count,
                (
                    SELECT COUNT(*)
                    FROM ambiguities amb
                    WHERE amb.analysis_id = a.analysis_id AND amb.status = 'OPEN'
                ) AS open_ambiguity_count,
                (
                    SELECT COUNT(*)
                    FROM extracted_fields f
                    WHERE f.analysis_id = a.analysis_id
                ) AS extracted_field_count
            FROM semantic_analyses a
            WHERE a.setup_id = ?
            ORDER BY a.created_at DESC, a.rowid DESC
        """
        params: list[Any] = [setup_id]
        if limit is not None:
            query += " LIMIT ? OFFSET ?"
            params.extend([limit, offset])
        elif offset:
            query += " LIMIT -1 OFFSET ?"
            params.append(offset)
        rows = self.database.execute(query, params).fetchall()
        return [self._analysis_summary_row(row) for row in rows]

    def count_analyses_for_setup(self, setup_id: str) -> int:
        row = self.database.execute(
            """
            SELECT COUNT(*) AS count
            FROM semantic_analyses
            WHERE setup_id = ?
            """,
            (setup_id,),
        ).fetchone()
        return int(row["count"] if row else 0)

    def get_analysis(self, analysis_id: str) -> dict[str, Any] | None:
        row = self.database.execute(
            "SELECT * FROM semantic_analyses WHERE analysis_id = ?",
            (analysis_id,),
        ).fetchone()
        return self._analysis_row(row) if row else None

    def list_scenarios(self, analysis_id: str) -> list[dict[str, Any]]:
        rows = self.database.execute(
            """
            SELECT * FROM extracted_scenarios
            WHERE analysis_id = ?
            ORDER BY created_at, scenario_id
            """,
            (analysis_id,),
        ).fetchall()
        return [self._scenario_row(row) for row in rows]

    def list_extracted_fields(self, analysis_id: str) -> list[dict[str, Any]]:
        rows = self.database.execute(
            """
            SELECT * FROM extracted_fields
            WHERE analysis_id = ?
            ORDER BY id
            """,
            (analysis_id,),
        ).fetchall()
        return [self._field_row(row) for row in rows]

    def list_ambiguities(self, analysis_id: str) -> list[dict[str, Any]]:
        rows = self.database.execute(
            """
            SELECT * FROM ambiguities
            WHERE analysis_id = ?
            ORDER BY created_at, ambiguity_id
            """,
            (analysis_id,),
        ).fetchall()
        return [self._ambiguity_row(row) for row in rows]

    def resolve_ambiguity(
        self,
        analysis_id: str,
        ambiguity_id: str,
        resolution: dict[str, Any],
    ) -> dict[str, Any] | None:
        now = utc_now_iso()
        self.database.execute(
            """
            UPDATE ambiguities
            SET status = ?, resolution_json = ?, updated_at = ?
            WHERE analysis_id = ? AND ambiguity_id = ?
            """,
            (
                "RESOLVED",
                json.dumps(resolution, sort_keys=True),
                now,
                analysis_id,
                ambiguity_id,
            ),
        )
        row = self.database.execute(
            """
            SELECT * FROM ambiguities
            WHERE analysis_id = ? AND ambiguity_id = ?
            """,
            (analysis_id, ambiguity_id),
        ).fetchone()
        return self._ambiguity_row(row) if row else None

    def _analysis_row(self, row: Any) -> dict[str, Any]:
        return {
            "analysis_id": row["analysis_id"],
            "setup_id": row["setup_id"],
            "symbol": row["symbol"],
            "request_id": row["request_id"],
            "idempotency_key": row["idempotency_key"],
            "analysis_hash": row["analysis_hash"],
            "source_type": row["source_type"],
            "raw_input_text": row["raw_input_text"],
            "primary_scenario_id": row["primary_scenario_id"],
            "save_validation": _load_json(row["save_validation_json"], {}),
            "arm_validation": _load_json(row["arm_validation_json"], {}),
            "issues": _load_json(row["issues_json"], []),
            "confidence": _load_json(row["confidence_json"], {}),
            "schema_version": row["schema_version"],
            "parser_version": row["parser_version"],
            "canonical_mapper_version": row["canonical_mapper_version"],
            "prompt_version": row["prompt_version"],
            "llm_model": row["llm_model"],
            "previous_analysis_id": row["previous_analysis_id"],
            "provider_name": row["provider_name"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    def _analysis_summary_row(self, row: Any) -> dict[str, Any]:
        summary = self._analysis_row(row)
        summary.update(
            {
                "detail_loaded": False,
                "scenario_count": row["scenario_count"],
                "ambiguity_count": row["ambiguity_count"],
                "open_ambiguity_count": row["open_ambiguity_count"],
                "extracted_field_count": row["extracted_field_count"],
            }
        )
        return summary

    def _scenario_row(self, row: Any) -> dict[str, Any]:
        return {
            "scenario_id": row["scenario_id"],
            "analysis_id": row["analysis_id"],
            "symbol": row["symbol"],
            "scenario_name": row["scenario_name"],
            "scenario_role": row["scenario_role"],
            "setup_type": row["setup_type"],
            "status": row["status"],
            "selected": bool(row["selected"]),
            "armed": bool(row["armed"]),
            "confidence": _load_json(row["confidence_json"], {}),
            "canonical_config": _load_json(row["canonical_config_json"], {}),
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    def _field_row(self, row: Any) -> dict[str, Any]:
        return {
            "id": row["id"],
            "analysis_id": row["analysis_id"],
            "scenario_id": row["scenario_id"],
            "raw_key": row["raw_key"],
            "normalized_key": row["normalized_key"],
            "canonical_path": row["canonical_path"],
            "raw_value": row["raw_value"],
            "parsed_value": _load_json(row["parsed_value_json"], None),
            "source_text": row["source_text"],
            "source_line_start": row["source_line_start"],
            "source_line_end": row["source_line_end"],
            "extraction_method": row["extraction_method"],
            "confidence": row["confidence"],
            "validation_status": row["validation_status"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    def _ambiguity_row(self, row: Any) -> dict[str, Any]:
        metadata = _load_json(row["metadata_json"], {})
        return {
            "ambiguity_id": row["ambiguity_id"],
            "analysis_id": row["analysis_id"],
            "scenario_id": row["scenario_id"],
            "field_path": row["field_path"],
            "message": row["message"],
            "options": _load_json(row["options_json"], []),
            "status": row["status"],
            "resolution": _load_json(row["resolution_json"], {}),
            "metadata": metadata,
            "kind": metadata.get("kind"),
            "severity": metadata.get("severity"),
            "confidence_impact": metadata.get("confidence_impact"),
            "suggested_action": metadata.get("suggested_action"),
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }
