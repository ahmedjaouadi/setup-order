from __future__ import annotations

import json
from typing import Any

from app.models import utc_now_iso
from app.storage.database import Database


def _row_to_dict(row: Any) -> dict[str, Any]:
    result = dict(row)
    if "raw_payload_json" in result:
        raw_payload = result.pop("raw_payload_json")
        result["raw_payload"] = json.loads(raw_payload or "{}")
    if "index_membership" in result and isinstance(result["index_membership"], str):
        value = result["index_membership"]
        try:
            result["index_membership"] = json.loads(value) if value else []
        except json.JSONDecodeError:
            result["index_membership"] = [item.strip() for item in value.split(",") if item.strip()]
    return result


class MarketContextRepository:
    def __init__(self, database: Database) -> None:
        self.database = database

    def list_symbol_metadata(self) -> dict[str, dict[str, Any]]:
        rows = self.database.execute("SELECT * FROM symbol_metadata ORDER BY symbol").fetchall()
        return {row["symbol"].upper(): _row_to_dict(row) for row in rows}

    def upsert_symbol_metadata(self, payload: dict[str, Any]) -> None:
        symbol = str(payload["symbol"]).upper()
        index_membership = payload.get("index_membership", [])
        if not isinstance(index_membership, str):
            index_membership = json.dumps(index_membership)
        self.database.execute(
            """
            INSERT INTO symbol_metadata (
                symbol, company_name, sector, industry, sector_etf, market_cap,
                country, exchange, index_membership, custom_priority, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(symbol) DO UPDATE SET
                company_name = excluded.company_name,
                sector = excluded.sector,
                industry = excluded.industry,
                sector_etf = excluded.sector_etf,
                market_cap = excluded.market_cap,
                country = excluded.country,
                exchange = excluded.exchange,
                index_membership = excluded.index_membership,
                custom_priority = excluded.custom_priority,
                updated_at = excluded.updated_at
            """,
            (
                symbol,
                payload.get("company_name"),
                payload.get("sector"),
                payload.get("industry"),
                payload.get("sector_etf"),
                payload.get("market_cap"),
                payload.get("country"),
                payload.get("exchange"),
                index_membership,
                payload.get("custom_priority"),
                payload.get("updated_at") or utc_now_iso(),
            ),
        )

    def upcoming_earnings(self, symbol: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
        query = "SELECT * FROM corporate_earnings"
        params: list[Any] = []
        if symbol:
            query += " WHERE symbol = ?"
            params.append(symbol.upper())
        query += " ORDER BY event_date ASC, event_time ASC LIMIT ?"
        params.append(limit)
        rows = self.database.execute(query, params).fetchall()
        return [_row_to_dict(row) for row in rows]

    def upcoming_dividends(
        self, symbol: str | None = None, limit: int = 50
    ) -> list[dict[str, Any]]:
        query = "SELECT * FROM corporate_dividends"
        params: list[Any] = []
        if symbol:
            query += " WHERE symbol = ?"
            params.append(symbol.upper())
        query += " ORDER BY next_dividend_date ASC LIMIT ?"
        params.append(limit)
        rows = self.database.execute(query, params).fetchall()
        return [_row_to_dict(row) for row in rows]

    def economic_events(self, limit: int = 100) -> list[dict[str, Any]]:
        rows = self.database.execute(
            """
            SELECT * FROM economic_events
            ORDER BY event_date ASC, event_time ASC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
        return [_row_to_dict(row) for row in rows]
