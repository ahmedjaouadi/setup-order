from __future__ import annotations

from typing import Any


class ModelScorecardService:
    def __init__(self, repository: Any) -> None:
        self.repository = repository

    def for_symbol(self, symbol: str) -> dict[str, Any]:
        normalized = str(symbol).upper()
        rows = [row for row in self.repository.list_forecast_stack_results(limit=5000) if str(row.get("symbol")).upper() == normalized]
        return {"symbol": normalized, "items": rows, "count": len(rows)}

    def latest_by_scope(self, symbol: str) -> dict[str, dict[str, Any]]:
        latest: dict[str, dict[str, Any]] = {}
        for row in self.for_symbol(symbol)["items"]:
            key = f"{row.get('model_name')}:{row.get('timeframe')}:{row.get('horizon_bars')}"
            latest.setdefault(key, row)
        return latest
