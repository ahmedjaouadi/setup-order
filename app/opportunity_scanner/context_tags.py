"""Context tags for detection outcomes (docs/skills.md section 32.2bis).

Each detection outcome must carry the context in which it was produced so the
learning engine can later answer questions like "are lunch-time detections with
rvol < 1.2 profitable?" without re-collecting anything. The tags live inside the
outcome's ``features_snapshot`` under a dedicated ``context_tags`` key so they
stay queryable in SQL via ``json_extract``.

Pure module: no I/O, never raises. A missing ingredient yields ``UNKNOWN`` (or
``None`` for reserved fields), never an exception.
"""

from __future__ import annotations

from datetime import UTC, datetime, time
from typing import Any

from app.utils.market_hours import US_EQUITY_TIMEZONE, coerce_datetime

# Session buckets in New York wall-clock time (skills.md section 25bis).
_OPEN = time(9, 30)
_MORNING = time(10, 0)
_LUNCH = time(11, 30)
_AFTERNOON = time(14, 0)
_POWER_HOUR = time(15, 0)
_CLOSE = time(16, 0)

_WEEKDAYS = ("MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN")

_RVOL_KEYS = ("rvol", "relative_volume", "volume_ratio", "volume_ratio_15m")
_TIMESTAMP_KEYS = ("timestamp", "event_timestamp", "quote_time", "last_update", "detected_at")


def build_context_tags(
    snapshot: dict[str, Any],
    now: datetime | str | None = None,
) -> dict[str, Any]:
    """Return the context tags for a detection snapshot.

    ``now`` overrides the detection moment; when omitted the snapshot timestamp
    is used, falling back to the current UTC time.
    """
    snapshot = snapshot if isinstance(snapshot, dict) else {}
    moment = _resolve_moment(snapshot, now)
    local = moment.astimezone(US_EQUITY_TIMEZONE)
    return {
        "time_bucket": _time_bucket(local),
        "rvol_bucket": _rvol_bucket(_first_number(snapshot, _RVOL_KEYS)),
        "spread_bucket": _spread_bucket(_spread_pct(snapshot)),
        "day_of_week": _WEEKDAYS[local.weekday()],
        # Reserved for later lots; kept as stable columns so the outcome
        # dataset schema does not shift when F3 / external data land.
        "market_regime": "UNKNOWN",
        "had_catalyst": None,
    }


def snapshot_time_bucket(
    snapshot: dict[str, Any],
    now: datetime | str | None = None,
) -> str:
    """Time bucket of a snapshot, for injection into the scanner snapshot.

    Same buckets and same timestamp resolution as ``build_context_tags``
    (skills.md 25bis): declarative rules can then match on ``time_bucket``
    exactly as outcome tags will record it.
    """
    snapshot = snapshot if isinstance(snapshot, dict) else {}
    moment = _resolve_moment(snapshot, now)
    return _time_bucket(moment.astimezone(US_EQUITY_TIMEZONE))


def _resolve_moment(snapshot: dict[str, Any], now: datetime | str | None) -> datetime:
    explicit = coerce_datetime(now) if now is not None else None
    if explicit is not None:
        return explicit
    for key in _TIMESTAMP_KEYS:
        parsed = coerce_datetime(snapshot.get(key))
        if parsed is not None:
            return parsed
    return datetime.now(UTC)


def _time_bucket(local: datetime) -> str:
    if local.weekday() >= 5:
        return "OFF_HOURS"
    clock = local.timetz().replace(tzinfo=None)
    if _OPEN <= clock < _MORNING:
        return "OPEN"
    if _MORNING <= clock < _LUNCH:
        return "MORNING"
    if _LUNCH <= clock < _AFTERNOON:
        return "LUNCH"
    if _AFTERNOON <= clock < _POWER_HOUR:
        return "AFTERNOON"
    if _POWER_HOUR <= clock < _CLOSE:
        return "POWER_HOUR"
    return "OFF_HOURS"


def _rvol_bucket(rvol: float | None) -> str:
    # Boundaries fixed per skills.md 32.2bis / TODO.md 6.1: lower bound
    # inclusive (0.8 -> "0.8-1.2", 1.2 -> "1.2-2.0") and the 1.2-2.0 range is
    # inclusive of 2.0 (2.0 -> "1.2-2.0", only strictly above lands in ">2.0").
    if rvol is None:
        return "UNKNOWN"
    if rvol < 0.8:
        return "<0.8"
    if rvol < 1.2:
        return "0.8-1.2"
    if rvol <= 2.0:
        return "1.2-2.0"
    return ">2.0"


def _spread_bucket(spread_pct: float | None) -> str:
    if spread_pct is None:
        return "UNKNOWN"
    if spread_pct <= 0.1:
        return "tight"
    if spread_pct <= 0.3:
        return "normal"
    return "wide"


def _spread_pct(snapshot: dict[str, Any]) -> float | None:
    explicit = _number(snapshot.get("spread_pct"))
    if explicit is not None:
        return explicit
    bid = _number(snapshot.get("bid"))
    ask = _number(snapshot.get("ask"))
    if bid is None or ask is None or bid > ask:
        return None
    mid = (bid + ask) / 2
    if mid <= 0:
        return None
    return round((ask - bid) / mid * 100, 4)


def _first_number(snapshot: dict[str, Any], keys: tuple[str, ...]) -> float | None:
    for key in keys:
        value = _number(snapshot.get(key))
        if value is not None:
            return value
    return None


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
