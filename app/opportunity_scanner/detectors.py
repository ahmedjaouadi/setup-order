from __future__ import annotations

from typing import Any


def detect_opportunity_types(snapshot: dict[str, Any]) -> list[str]:
    stock_perf = _number(_first_value(snapshot, "perf_stock_1d", "stock_perf_1d"))
    rs_sector = _number(_first_value(snapshot, "rs_sector", "relative_strength_vs_sector"))
    rs_spy = _number(_first_value(snapshot, "rs_spy", "relative_strength_vs_spy"))
    volume_ratio = _number(
        _first_value(snapshot, "relative_volume", "volume_ratio", "volume_ratio_15m")
    )
    gap_pct = _number(snapshot.get("gap_pct"))
    breakout_proximity = _number(snapshot.get("breakout_proximity"))
    new_intraday_high = bool(snapshot.get("new_intraday_high"))

    types: list[str] = []
    if stock_perf is not None and stock_perf >= 5:
        types.append("INTRADAY_MOMENTUM_ANOMALY")
    if (rs_spy is not None and rs_spy >= 3) or (rs_sector is not None and rs_sector >= 2):
        types.append("RELATIVE_STRENGTH_LEADER")
    if volume_ratio is not None and volume_ratio >= 1.5:
        types.append("VOLUME_EXPANSION")
    if breakout_proximity is not None and breakout_proximity <= 1.5:
        types.append("BREAKOUT_CANDIDATE")
    if gap_pct is not None and gap_pct >= 3 and stock_perf is not None and stock_perf > 0:
        types.append("GAP_AND_HOLD")
    if new_intraday_high:
        types.append("WATCHLIST_ANOMALY")
    if "RELATIVE_STRENGTH_LEADER" in types and rs_sector is not None and rs_sector >= 2:
        types.append("SECTOR_LEADER")
    return _unique(types)


def primary_opportunity_type(types: list[str]) -> str:
    priority = [
        "INTRADAY_MOMENTUM_ANOMALY",
        "RELATIVE_STRENGTH_LEADER",
        "VOLUME_EXPANSION",
        "BREAKOUT_CANDIDATE",
        "GAP_AND_HOLD",
        "PULLBACK_AFTER_MOMENTUM",
        "SECTOR_LEADER",
        "WATCHLIST_ANOMALY",
    ]
    for item in priority:
        if item in types:
            return item
    return "WATCHLIST_ANOMALY"


def _first_value(payload: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        value = payload.get(key)
        if value not in (None, ""):
            return value
    return None


def _number(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _unique(values: list[str]) -> list[str]:
    result: list[str] = []
    for value in values:
        if value not in result:
            result.append(value)
    return result
