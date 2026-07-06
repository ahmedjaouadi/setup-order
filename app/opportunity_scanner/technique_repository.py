from __future__ import annotations

from typing import Any

from app.storage.database import Database

_UPDATABLE_FIELDS = frozenset(
    {"name", "description", "rule_json", "enabled", "status", "parent_id", "updated_at"}
)


class TechniqueRepository:
    """Persists and queries the detection technique library (`detection_techniques`)."""

    def __init__(self, database: Database) -> None:
        self.database = database

    def insert_if_absent(self, technique: dict[str, Any]) -> bool:
        cursor = self.database.execute(
            """
            INSERT OR IGNORE INTO detection_techniques (
                technique_id, name, description, rule_json, enabled,
                origin, parent_id, status, created_at, updated_at,
                config_version, revision
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                technique["technique_id"],
                technique["name"],
                technique.get("description", ""),
                technique["rule_json"],
                1 if technique.get("enabled", True) else 0,
                technique["origin"],
                technique.get("parent_id"),
                technique.get("status", "ACTIVE"),
                technique["created_at"],
                technique["updated_at"],
                # Versioning (skills.md 30bis): learned variants and seeds are
                # born at revision 1; their lineage is carried by parent_id.
                str(technique.get("config_version", "1")),
                int(technique.get("revision", 1)),
            ),
        )
        return cursor.rowcount > 0

    def get(self, technique_id: str) -> dict[str, Any] | None:
        cursor = self.database.execute(
            "SELECT * FROM detection_techniques WHERE technique_id = ?",
            (technique_id,),
        )
        row = cursor.fetchone()
        return dict(row) if row is not None else None

    def list_all(self) -> list[dict[str, Any]]:
        # Ordered by rowid (insertion order), not created_at: builtin seeds share
        # the same timestamp, and evaluation order must stay deterministic.
        cursor = self.database.execute("SELECT * FROM detection_techniques ORDER BY rowid ASC")
        return [dict(row) for row in cursor.fetchall()]

    def list_active(self) -> list[dict[str, Any]]:
        cursor = self.database.execute("""
            SELECT * FROM detection_techniques
            WHERE enabled = 1 AND status IN ('ACTIVE', 'CANDIDATE')
            ORDER BY rowid ASC
            """)
        return [dict(row) for row in cursor.fetchall()]

    def update_fields(self, technique_id: str, fields: dict[str, Any]) -> bool:
        updates = {key: value for key, value in fields.items() if key in _UPDATABLE_FIELDS}
        if not updates:
            return False
        set_clause = ", ".join(f"{key} = ?" for key in updates)
        values = [*updates.values(), technique_id]
        cursor = self.database.execute(
            f"UPDATE detection_techniques SET {set_clause} WHERE technique_id = ?",
            values,
        )
        return cursor.rowcount > 0

    def bump_revision(self, technique_id: str, *, updated_at: str) -> int | None:
        """Increment a technique's `revision` (skills.md 30bis). Returns the new revision.

        Called when — and only when — a technique's `rule_json` changes, so the
        learning engine can replay a past decision against the rule of its epoch.
        """
        cursor = self.database.execute(
            """
            UPDATE detection_techniques
            SET revision = revision + 1, updated_at = ?
            WHERE technique_id = ?
            """,
            (updated_at, technique_id),
        )
        if cursor.rowcount <= 0:
            return None
        row = self.get(technique_id)
        return int(row["revision"]) if row is not None else None

    def retire(self, technique_id: str, *, updated_at: str) -> bool:
        """Soft delete: flips status to RETIRED and disables the technique. Never deletes the row."""
        return self.update_fields(
            technique_id,
            {"status": "RETIRED", "enabled": 0, "updated_at": updated_at},
        )
