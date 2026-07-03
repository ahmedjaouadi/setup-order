from __future__ import annotations

import argparse
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def main() -> int:
    parser = argparse.ArgumentParser(description="Export a local daily runtime report.")
    parser.add_argument("--database", default="data/trading_state.sqlite")
    parser.add_argument("--output-dir", default="data/reports")
    args = parser.parse_args()

    database = Path(args.database)
    if not database.exists():
        parser.error(f"Database not found: {database}")
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    connection = sqlite3.connect(database)
    connection.row_factory = sqlite3.Row
    try:
        report = build_report(connection)
    finally:
        connection.close()

    report_date = datetime.now(timezone.utc).date().isoformat()
    output_path = output_dir / f"daily_report.{report_date}.json"
    output_path.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    print(output_path)
    return 0


def build_report(connection: sqlite3.Connection) -> dict[str, Any]:
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "setups": count_by(connection, "setups", "status"),
        "orders": count_by(connection, "orders", "status"),
        "positions": rows(connection, "SELECT * FROM positions ORDER BY symbol"),
        "recent_events": rows(connection, "SELECT * FROM events ORDER BY id DESC LIMIT 100"),
        "recent_decision_traces": decoded_rows(
            connection,
            "SELECT * FROM decision_traces ORDER BY created_at DESC LIMIT 100",
            json_columns={"trace_json": "trace"},
        ),
        "latest_portfolio_snapshot": decoded_one(
            connection,
            "SELECT * FROM portfolio_snapshots ORDER BY created_at DESC LIMIT 1",
            json_columns={
                "sector_exposure_json": "sector_exposure",
                "symbol_exposure_json": "symbol_exposure",
                "correlation_json": "correlation",
                "warnings_json": "warnings",
                "size_reductions_json": "size_reductions",
            },
        ),
    }


def count_by(connection: sqlite3.Connection, table: str, column: str) -> dict[str, int]:
    result = {}
    for row in connection.execute(
        f"SELECT {column}, COUNT(*) AS count FROM {table} GROUP BY {column}"
    ):
        result[str(row[column])] = int(row["count"])
    return result


def rows(connection: sqlite3.Connection, query: str) -> list[dict[str, Any]]:
    return [dict(row) for row in connection.execute(query).fetchall()]


def decoded_rows(
    connection: sqlite3.Connection,
    query: str,
    *,
    json_columns: dict[str, str],
) -> list[dict[str, Any]]:
    return [decode_row(row, json_columns) for row in connection.execute(query).fetchall()]


def decoded_one(
    connection: sqlite3.Connection,
    query: str,
    *,
    json_columns: dict[str, str],
) -> dict[str, Any] | None:
    row = connection.execute(query).fetchone()
    return decode_row(row, json_columns) if row else None


def decode_row(row: sqlite3.Row, json_columns: dict[str, str]) -> dict[str, Any]:
    payload = dict(row)
    for source, target in json_columns.items():
        if source in payload:
            payload[target] = json.loads(payload.pop(source) or "{}")
    return payload


if __name__ == "__main__":
    raise SystemExit(main())
