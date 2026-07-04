from __future__ import annotations

from typing import Any

DEFAULT_THRESHOLDS = {
    "detected": 70.0,
    "watchlist": 40.0,
    "weak": 20.0,
    "strong_perf": 5.0,
    "very_strong_perf": 10.0,
    "rs_spy": 3.0,
    "rs_sector": 2.0,
    "volume_ratio": 1.5,
    "max_spread_pct": 0.35,
}


class OpportunityContextScorer:
    def __init__(self, settings: dict[str, Any] | None = None) -> None:
        self.settings = settings or {}

    def score(self, snapshot: dict[str, Any], opportunity_types: list[str]) -> dict[str, Any]:
        thresholds = self.thresholds()
        stock_perf = _number(_first_value(snapshot, "perf_stock_1d", "stock_perf_1d"))
        rs_sector = _number(_first_value(snapshot, "rs_sector", "relative_strength_vs_sector"))
        rs_spy = _number(_first_value(snapshot, "rs_spy", "relative_strength_vs_spy"))
        volume_ratio = _number(
            _first_value(snapshot, "relative_volume", "volume_ratio", "volume_ratio_15m")
        )
        spread_pct = _spread_pct(snapshot)
        event_risk = str(snapshot.get("event_risk") or "OK").upper()
        price_extended = _is_price_extended(snapshot)
        sector_missing = _sector_metadata_missing(snapshot)

        score = 0.0
        reasons: list[str] = []
        warnings: list[str] = []

        if stock_perf is not None and stock_perf >= thresholds["strong_perf"]:
            score += 30
            reasons.append(f"Stock performance 1D +{_fmt(stock_perf)}%")
        if stock_perf is not None and stock_perf >= thresholds["very_strong_perf"]:
            score += 20
            reasons.append("Strong absolute momentum")
        if "INTRADAY_MOMENTUM_ANOMALY" in opportunity_types:
            score += 20
            reasons.append("Intraday momentum anomaly detected")
        if rs_spy is not None and rs_spy >= thresholds["rs_spy"]:
            score += 20
            reasons.append(f"Relative strength vs SPY +{_fmt(rs_spy)}%")
        if rs_sector is not None and rs_sector >= thresholds["rs_sector"]:
            score += 20
            reasons.append(f"Relative strength vs sector +{_fmt(rs_sector)}%")
        if volume_ratio is not None and volume_ratio >= thresholds["volume_ratio"]:
            score += 20
            reasons.append(f"Relative volume {round(volume_ratio, 2)}x")
        if spread_pct is not None and spread_pct <= thresholds["max_spread_pct"]:
            score += 10
            reasons.append("Spread acceptable")

        discovery_score = _bounded(score)

        if sector_missing:
            warnings.append("SECTOR_METADATA_MISSING")
            if stock_perf is None or stock_perf < thresholds["very_strong_perf"]:
                score -= 20
        if price_extended:
            warnings.append("DO_NOT_CHASE_EXTENDED_PRICE")
            score -= 30
        if event_risk in {"WARNING", "BLOCKING", "HIGH", "CRITICAL", "EVENT_RISK"}:
            warnings.append("EVENT_RISK_NEARBY")
            score -= 30
        if spread_pct is not None and spread_pct > thresholds["max_spread_pct"]:
            warnings.append("SPREAD_TOO_WIDE")
            score -= 20

        risk_adjusted_score = _bounded(score)
        return {
            "score": risk_adjusted_score,
            "discovery_score": discovery_score,
            "risk_adjusted_score": risk_adjusted_score,
            "reasons": reasons or ["No actionable market anomaly detected"],
            "warnings": warnings,
        }

    def thresholds(self) -> dict[str, float]:
        raw = self.settings.get("opportunity_scanner", {}).get("context_thresholds", {})
        if not isinstance(raw, dict):
            raw = {}
        return {
            key: float(raw.get(key, default) or default)
            for key, default in DEFAULT_THRESHOLDS.items()
        }


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


def _spread_pct(snapshot: dict[str, Any]) -> float | None:
    direct = _number(_first_value(snapshot, "spread_pct", "spread_percent"))
    if direct is not None:
        return direct
    spread_bps = _number(snapshot.get("spread_bps"))
    if spread_bps is not None:
        return spread_bps / 100
    bid = _number(snapshot.get("bid"))
    ask = _number(snapshot.get("ask"))
    if bid is None or ask is None or bid <= 0 or ask < bid:
        return None
    mid = (bid + ask) / 2
    return ((ask - bid) / mid) * 100 if mid > 0 else None


def _sector_metadata_missing(snapshot: dict[str, Any]) -> bool:
    status = str(snapshot.get("metadata_status") or "").upper()
    sector = str(snapshot.get("sector") or "").strip().upper()
    return status in {"SECTOR_UNKNOWN", "SECTOR_METADATA_MISSING"} or sector in {
        "",
        "UNKNOWN",
        "NON CLASSE",
    }


def _is_price_extended(snapshot: dict[str, Any]) -> bool:
    if bool(snapshot.get("price_too_far_above_entry")):
        return True
    distance_atr = _number(snapshot.get("distance_to_entry_atr"))
    max_distance = _number(snapshot.get("max_distance_to_entry_atr"))
    if distance_atr is not None and max_distance is not None:
        return distance_atr > max_distance
    return False


def _fmt(value: float) -> str:
    return str(round(value, 2)).rstrip("0").rstrip(".")


def _bounded(score: float) -> float:
    return round(min(100.0, max(0.0, score)), 2)
