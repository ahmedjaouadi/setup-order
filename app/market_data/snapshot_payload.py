from __future__ import annotations

from typing import Any

from app.models import MarketSnapshot

FLOAT_FIELDS = {
    "open",
    "high",
    "low",
    "close",
    "bid",
    "ask",
    "spread",
    "spread_bps",
    "volume",
    "bar_volume_15m",
    "avg_volume_15m",
    "volume_ratio_15m",
    "current_bar_volume",
    "previous_high",
    "daily_close",
    "volume_ratio",
    "volume_ratio_closed_bar",
    "volume_ratio_live",
    "average_volume_ratio_last_2_bars",
    "elapsed_ratio",
    "projected_volume",
    "minimum_tick",
    "atr_15m",
    "atr_1h",
    "last_successful_atr_1h",
    "atr_1h_age_seconds",
    "last_confirmed_higher_low",
    "support_level",
    "successful_retest_low",
    "structural_support",
    "close_1h",
    "ema_20",
    "ema_50",
    "market_data_type_requested",
    "market_data_type_actual",
}

INT_FIELDS = {
    "bar_count",
    "bars_15m_count",
    "bars_1h_count",
    "bars_required_for_atr",
    "bars_above_resistance",
    "last_ibkr_error_code",
    "volume_sample_days",
    "volume_sample_count",
}

BOOL_FIELDS = {
    "breakout_already_detected",
    "new_higher_low_confirmed",
    "bullish_candle",
    "atr_1h_use_rth",
    "historical_1h_available",
}

STRING_FIELDS = {
    "timestamp",
    "timeframe",
    "market_data_source",
    "live_quote_source",
    "live_market_data_status",
    "atr_1h_status",
    "atr_1h_bar_size",
    "atr_1h_duration",
    "historical_1h_error",
    "last_successful_atr_1h_at",
    "last_ibkr_error_message",
    "bar_date",
    "hybrid_signal_bar_size",
    "hybrid_atr_1h_bar_size",
    "market_open_time",
    "current_time",
    "volume_status",
    "volume_timeframe",
    "volume_comparison_mode",
}


def market_snapshot_from_payload(payload: dict[str, Any]) -> MarketSnapshot:
    if not isinstance(payload, dict):
        raise ValueError("Market snapshot payload must be a JSON object")
    if payload.get("symbol") in (None, ""):
        raise ValueError("Market snapshot symbol is required")
    if payload.get("price") in (None, ""):
        raise ValueError("Market snapshot price is required")

    values: dict[str, Any] = {
        "symbol": str(payload["symbol"]).upper(),
        "price": float(payload["price"]),
    }
    for key in FLOAT_FIELDS:
        value = _float_or_none(payload.get(key))
        if value is not None:
            values[key] = value
    for key in INT_FIELDS:
        value = _int_or_none(payload.get(key))
        if value is not None:
            values[key] = value
    for key in BOOL_FIELDS:
        if key in payload:
            values[key] = _bool_value(payload.get(key))
    for key in STRING_FIELDS:
        value = payload.get(key)
        if value not in (None, ""):
            values[key] = str(value)

    if payload.get("session"):
        values["session"] = str(payload["session"]).upper()
    if isinstance(payload.get("hybrid_sources"), dict):
        values["hybrid_sources"] = payload["hybrid_sources"]
    if isinstance(payload.get("market_data_readiness"), dict):
        values["market_data_readiness"] = payload["market_data_readiness"]
    if isinstance(payload.get("historical_bars"), list):
        values["historical_bars"] = payload["historical_bars"]

    return MarketSnapshot(**values)


def _float_or_none(value: Any) -> float | None:
    if value in (None, ""):
        return None
    return float(value)


def _int_or_none(value: Any) -> int | None:
    if value in (None, ""):
        return None
    return int(value)


def _bool_value(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if value in (None, ""):
        return False
    if isinstance(value, (int, float)):
        return value != 0
    return str(value).strip().lower() in {"1", "true", "yes", "on"}
