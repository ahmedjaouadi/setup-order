from __future__ import annotations

from typing import Any


class ForecastRequestBuilder:
    def build(
        self,
        *,
        symbol: str,
        timeframe: str,
        horizon_bars: int,
        bars: list[dict[str, Any]],
        target: str = "price",
        setup_id: str | None = None,
    ) -> dict[str, Any]:
        if not str(symbol).strip():
            raise ValueError("symbol is required")
        if horizon_bars <= 0:
            raise ValueError("horizon_bars must be positive")
        closes = [_number(row.get("close")) for row in bars]
        closes = [value for value in closes if value is not None]
        if not closes:
            raise ValueError("at least one valid close is required")
        return {
            "symbol": str(symbol).strip().upper(),
            "timeframe": str(timeframe),
            "horizon_bars": int(horizon_bars),
            "target": str(target),
            "series": closes,
            "closes": closes,
            "setup_id": setup_id,
            "input_start_time": bars[0].get("date") or bars[0].get("timestamp"),
            "input_end_time": bars[-1].get("date") or bars[-1].get("timestamp"),
        }


def _number(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
