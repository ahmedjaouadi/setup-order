from __future__ import annotations

from typing import Any

# --- weighted quality score (skills.md 9.1, TODO step 8) --------------------
#
# Seven weighted components; each sums the sub-criteria computable with the
# features available today (F1). A sub-criterion that cannot be computed
# contributes 0 AND is listed in `unavailable`, so scores stay comparable
# between symbols and the transparency requirement of 9.1 holds. The score
# NEVER replaces the automatic refusals: the data-quality gate and the
# liquidity filter run before any scoring and win regardless of the score.
QUALITY_COMPONENT_WEIGHTS: dict[str, int] = {
    "trend_quality": 20,
    "structure_quality": 20,
    "volume_quality": 15,
    "risk_quality": 20,
    "market_context": 10,
    "fundamental_context": 10,
    "execution_quality": 5,
}

DEFAULT_QUALITY_CONFIG: dict[str, Any] = {
    # atr_pct considered a sane volatility for an intraday-swing candidate.
    "atr_pct_range": [0.5, 5.0],
    # Average volume (shares/15m bar or daily average, whichever the quote
    # carries) below which the liquidity sub-criterion earns nothing.
    "min_average_volume": 100_000,
    "rvol_threshold": 1.5,
    "tight_spread_pct": 0.3,
}

# Interpretation scale of skills.md 9.1.
_GRADE_BOUNDS: tuple[tuple[float, str], ...] = (
    (80.0, "EXCELLENT"),
    (65.0, "ACCEPTABLE"),
    (50.0, "WEAK"),
)


def compute_quality_score(
    snapshot: dict[str, Any],
    settings: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Weighted 7-component quality score (skills.md 9.1).

    Returns ``quality_score`` (/100), ``score_grade`` (EXCELLENT / ACCEPTABLE /
    WEAK / NO_GO), a per-component ``components`` breakdown and the list of
    ``unavailable`` sub-criteria. In F1, structure_quality (F2),
    market_context (F3) and fundamental_context (external data) are frozen at
    0 by construction - documented here so nobody reads a low score as a bad
    setup rather than missing features.
    """
    snapshot = snapshot if isinstance(snapshot, dict) else {}
    config = _quality_config(settings)
    unavailable: list[str] = []
    components: dict[str, dict[str, Any]] = {}

    components["trend_quality"] = _trend_quality(snapshot, unavailable)
    components["structure_quality"] = _frozen_component(
        "structure_quality",
        ("levels", "consolidation", "higher_timeframe_alignment"),
        unavailable,
        note="Awaits the F2 lot (zones, consolidation) - worth 0 in F1.",
    )
    components["volume_quality"] = _volume_quality(snapshot, config, unavailable)
    components["risk_quality"] = _risk_quality(snapshot, config, unavailable)
    components["market_context"] = _frozen_component(
        "market_context",
        ("spy_above_vwap", "qqq_above_vwap", "vix_trend"),
        unavailable,
        note="Awaits the F3 lot (market regime) - worth 0 in F1.",
    )
    components["fundamental_context"] = _frozen_component(
        "fundamental_context",
        ("catalyst", "short_interest", "earnings_window"),
        unavailable,
        note="Awaits external data sources - worth 0 in F1.",
    )
    components["execution_quality"] = _execution_quality(snapshot, config, unavailable)

    total = round(sum(float(component["score"]) for component in components.values()), 2)
    return {
        "quality_score": total,
        "score_grade": quality_grade(total),
        "components": components,
        "unavailable": unavailable,
    }


def quality_grade(score: float) -> str:
    """Grade per the skills.md 9.1 scale: >=80 / 65-79 / 50-64 / <50."""
    for bound, grade in _GRADE_BOUNDS:
        if score >= bound:
            return grade
    return "NO_GO"


def _quality_config(settings: dict[str, Any] | None) -> dict[str, Any]:
    raw = (settings or {}).get("opportunity_scanner", {})
    raw = raw.get("quality_score", {}) if isinstance(raw, dict) else {}
    if not isinstance(raw, dict):
        raw = {}
    return {**DEFAULT_QUALITY_CONFIG, **raw}


def _trend_quality(snapshot: dict[str, Any], unavailable: list[str]) -> dict[str, Any]:
    criteria: dict[str, float] = {}
    above_ema20 = snapshot.get("price_above_ema20")
    above_sma50 = snapshot.get("price_above_sma50")
    if isinstance(above_ema20, bool) and isinstance(above_sma50, bool):
        criteria["above_key_averages"] = 2.0 if (above_ema20 and above_sma50) else 0.0
    else:
        unavailable.append("trend_quality.above_key_averages")
    daily_return = _number(snapshot.get("return_20_bar_pct"))
    if daily_return is not None:
        criteria["daily_bullish"] = 8.0 if daily_return > 0 else 0.0
    else:
        unavailable.append("trend_quality.daily_bullish")
    intraday = _number(_first_value(snapshot, "perf_stock_1d", "stock_perf_1d"))
    if intraday is not None:
        criteria["intraday_aligned"] = 6.0 if intraday > 0 else 0.0
    else:
        unavailable.append("trend_quality.intraday_aligned")
    unavailable.append("trend_quality.higher_lows")  # awaits F2
    return _component(criteria, QUALITY_COMPONENT_WEIGHTS["trend_quality"])


def _volume_quality(
    snapshot: dict[str, Any],
    config: dict[str, Any],
    unavailable: list[str],
) -> dict[str, Any]:
    criteria: dict[str, float] = {}
    rvol = _number(
        _first_value(snapshot, "rvol", "relative_volume", "volume_ratio", "volume_ratio_15m")
    )
    if rvol is not None:
        threshold = float(config.get("rvol_threshold", 1.5))
        criteria["rvol_confirmed"] = 7.0 if rvol >= threshold else 0.0
    else:
        unavailable.append("volume_quality.rvol_confirmed")
    # The two remaining sub-criteria (accumulation pattern, breakout volume)
    # need the F2 state features.
    unavailable.append("volume_quality.accumulation_pattern")
    unavailable.append("volume_quality.breakout_volume")
    return _component(criteria, QUALITY_COMPONENT_WEIGHTS["volume_quality"])


def _risk_quality(
    snapshot: dict[str, Any],
    config: dict[str, Any],
    unavailable: list[str],
) -> dict[str, Any]:
    criteria: dict[str, float] = {}
    atr = _number(snapshot.get("atr_pct"))
    if atr is not None:
        low, high = _range(config.get("atr_pct_range"), 0.5, 5.0)
        criteria["atr_pct_healthy"] = 4.0 if low <= atr <= high else 0.0
    else:
        unavailable.append("risk_quality.atr_pct_healthy")
    # Structural stop distance / R multiple need an execution plan, which a
    # detection-only opportunity does not have yet.
    unavailable.append("risk_quality.structural_stop")
    return _component(criteria, QUALITY_COMPONENT_WEIGHTS["risk_quality"])


def _execution_quality(
    snapshot: dict[str, Any],
    config: dict[str, Any],
    unavailable: list[str],
) -> dict[str, Any]:
    criteria: dict[str, float] = {}
    spread = _spread_pct(snapshot)
    if spread is not None:
        criteria["tight_spread"] = 3.0 if spread <= float(config["tight_spread_pct"]) else 0.0
    else:
        unavailable.append("execution_quality.tight_spread")
    average_volume = _number(
        _first_value(snapshot, "avg_volume_15m", "average_volume_20", "volume")
    )
    if average_volume is not None:
        criteria["liquidity"] = (
            2.0 if average_volume >= float(config["min_average_volume"]) else 0.0
        )
    else:
        unavailable.append("execution_quality.liquidity")
    return _component(criteria, QUALITY_COMPONENT_WEIGHTS["execution_quality"])


def _frozen_component(
    name: str,
    sub_criteria: tuple[str, ...],
    unavailable: list[str],
    *,
    note: str,
) -> dict[str, Any]:
    unavailable.extend(f"{name}.{criterion}" for criterion in sub_criteria)
    component = _component({}, QUALITY_COMPONENT_WEIGHTS[name])
    component["note"] = note
    return component


def _component(criteria: dict[str, float], weight: int) -> dict[str, Any]:
    score = round(min(float(weight), sum(criteria.values())), 2)
    return {"score": score, "max": weight, "criteria": criteria}


def _range(value: Any, default_low: float, default_high: float) -> tuple[float, float]:
    if isinstance(value, list | tuple) and len(value) == 2:
        low = _number(value[0])
        high = _number(value[1])
        if low is not None and high is not None and low <= high:
            return low, high
    return default_low, default_high


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
        # quality_score (skills.md 9.1) ships alongside the legacy scores during
        # the observation phase: `_status` still keys off discovery/risk scores
        # and only switches over once the new score has been observed live.
        quality = compute_quality_score(snapshot, self.settings)
        return {
            "score": risk_adjusted_score,
            "discovery_score": discovery_score,
            "risk_adjusted_score": risk_adjusted_score,
            "quality_score": quality["quality_score"],
            "score_grade": quality["score_grade"],
            "score_breakdown": {
                "components": quality["components"],
                "unavailable": quality["unavailable"],
            },
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
