"""Data quality gate for the detection scanner (docs/skills.md section 28bis).

No analysis is reliable on defective data. Before any technique evaluation, the
snapshot is validated: staleness, OHLC coherence, bid < ask, price present.
A failure yields ``(PAUSED, STALE_DATA | MISSING_MARKET_DATA)`` and the scan is
skipped for that candidate — no opportunity, and above all no detection outcome
recorded on a suspect price (which would poison the learning dataset).

Pure module: no I/O, never raises. Status and reason codes come from the shared
``app.decision_codes`` vocabulary (skills.md section 2.5) so a scanner refusal
speaks the exact same language as an engine refusal.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from app.decision_codes import (
    REASON_MISSING_MARKET_DATA,
    REASON_STALE_DATA,
    STATUS_OK,
    STATUS_PAUSED,
)
from app.utils.market_hours import coerce_datetime

DEFAULT_STALENESS_MAX_SECONDS = 1800  # 2x a 15m timeframe.

_PRICE_KEYS = ("price", "last", "close", "price_at_detection")
_TIMESTAMP_KEYS = ("timestamp", "event_timestamp", "quote_time", "last_update")
_OHLC_EPSILON = 1e-6

# Ordered so that "missing/corrupt" issues rank before "stale": when both are
# present the reason code reported is MISSING_MARKET_DATA (skills.md section 29
# checks missing data before staleness).
_ISSUE_REASON: dict[str, str] = {
    "missing_price": REASON_MISSING_MARKET_DATA,
    "ohlc_incoherent": REASON_MISSING_MARKET_DATA,
    "bid_ask_inverted": REASON_MISSING_MARKET_DATA,
    "stale_data": REASON_STALE_DATA,
}


def evaluate_snapshot_quality(
    snapshot: dict[str, Any],
    *,
    now: datetime | str | None = None,
    staleness_max_seconds: float = DEFAULT_STALENESS_MAX_SECONDS,
) -> dict[str, Any]:
    """Return ``{"status", "reason_code", "issues"}`` for a snapshot."""
    snapshot = snapshot if isinstance(snapshot, dict) else {}
    issues = _issues(snapshot, now, staleness_max_seconds)
    if not issues:
        return {"status": STATUS_OK, "reason_code": None, "issues": []}
    reason_code = _ISSUE_REASON.get(issues[0], REASON_MISSING_MARKET_DATA)
    return {"status": STATUS_PAUSED, "reason_code": reason_code, "issues": issues}


def _issues(
    snapshot: dict[str, Any],
    now: datetime | str | None,
    staleness_max_seconds: float,
) -> list[str]:
    issues: list[str] = []
    price = _first_number(snapshot, _PRICE_KEYS)
    if price is None or price <= 0:
        issues.append("missing_price")
    if not _ohlc_coherent(snapshot):
        issues.append("ohlc_incoherent")
    bid = _number(snapshot.get("bid"))
    ask = _number(snapshot.get("ask"))
    if bid is not None and ask is not None and bid > ask:
        issues.append("bid_ask_inverted")
    if _is_stale(snapshot, now, staleness_max_seconds):
        issues.append("stale_data")
    return issues


def _ohlc_coherent(snapshot: dict[str, Any]) -> bool:
    high = _number(snapshot.get("high"))
    low = _number(snapshot.get("low"))
    open_ = _number(snapshot.get("open"))
    close = _number(snapshot.get("close"))
    if high is not None and low is not None and high + _OHLC_EPSILON < low:
        return False
    if high is not None:
        for value in (open_, close, low):
            if value is not None and high + _OHLC_EPSILON < value:
                return False
    if low is not None:
        for value in (open_, close):
            if value is not None and value + _OHLC_EPSILON < low:
                return False
    return True


def _is_stale(
    snapshot: dict[str, Any],
    now: datetime | str | None,
    staleness_max_seconds: float,
) -> bool:
    if staleness_max_seconds <= 0:
        return False
    stamp = None
    for key in _TIMESTAMP_KEYS:
        stamp = coerce_datetime(snapshot.get(key))
        if stamp is not None:
            break
    if stamp is None:
        # No timestamp: staleness cannot be proven, so it is not asserted.
        return False
    reference = coerce_datetime(now) or datetime.now(UTC)
    age_seconds = (reference - stamp).total_seconds()
    return age_seconds > staleness_max_seconds


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
