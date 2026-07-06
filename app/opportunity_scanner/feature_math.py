"""Pure feature maths shared by the scanner and the feature store (F1 lot).

Single source of truth for the derived features of skills.md 5.1/7.1/19: ATR as
a percent of price, session VWAP and the signed distance to it. Both the scanner
snapshot (``app/opportunities/scanner.py``) and ``FeatureStore`` call these, so
the numbers can never drift between the detection path and the outcome tracker.

Pure module: no I/O, never raises. A missing ingredient yields ``None`` (never
0, never an exception).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from app.utils.market_hours import (
    REGULAR_MARKET_CLOSE,
    REGULAR_MARKET_OPEN,
    US_EQUITY_TIMEZONE,
    coerce_datetime,
)

_PRICE_KEYS = ("price", "last", "close", "price_at_detection")
_ATR_KEYS = ("atr_15m", "atr_1h")


def atr_pct(quote: dict[str, Any]) -> float | None:
    """ATR as a percent of price: ``atr_15m / price * 100``.

    Falls back to ``atr_1h`` when the 15m ATR is absent (skills.md 7.1). Any
    missing or non-positive ingredient yields ``None``.
    """
    if not isinstance(quote, dict):
        return None
    price = _first_number(quote, _PRICE_KEYS)
    if price is None or price <= 0:
        return None
    atr = _first_number(quote, _ATR_KEYS)
    if atr is None or atr <= 0:
        return None
    return round(atr / price * 100, 4)


def session_vwap(bars: Any) -> float | None:
    """Session VWAP: ``sum(typical_price * volume) / sum(volume)``.

    ``typical_price = (high + low + close) / 3`` over the day's RTH bars. The
    caller is responsible for passing only regular-session bars. Returns ``None``
    when no usable bar is present or the cumulated volume is zero — never 0.
    """
    if not isinstance(bars, list) or not bars:
        return None
    numerator = 0.0
    volume_total = 0.0
    for bar in bars:
        if not isinstance(bar, dict):
            continue
        high = _number(bar.get("high"))
        low = _number(bar.get("low"))
        close = _number(bar.get("close", bar.get("price")))
        volume = _number(bar.get("volume"))
        if high is None or low is None or close is None or volume is None or volume <= 0:
            continue
        typical = (high + low + close) / 3
        numerator += typical * volume
        volume_total += volume
    if volume_total <= 0:
        return None
    return round(numerator / volume_total, 6)


def rth_session_bars(
    bars: Any,
    *,
    now: datetime | str | None = None,
) -> list[dict[str, Any]]:
    """Keep only the bars of the current New York session, inside RTH.

    ``now`` anchors the session day (defaults to the current UTC time). A bar
    whose ``date`` cannot be parsed, falls on another NY calendar day or starts
    outside 09:30-16:00 NY is dropped. Naive timestamps are assumed UTC, the
    same convention as ``coerce_datetime``.
    """
    if not isinstance(bars, list) or not bars:
        return []
    anchor = coerce_datetime(now) if now is not None else None
    if anchor is None:
        anchor = datetime.now(UTC)
    session_date = anchor.astimezone(US_EQUITY_TIMEZONE).date()
    selected: list[dict[str, Any]] = []
    for bar in bars:
        if not isinstance(bar, dict):
            continue
        moment = coerce_datetime(bar.get("date"))
        if moment is None:
            continue
        local = moment.astimezone(US_EQUITY_TIMEZONE)
        if local.date() != session_date:
            continue
        clock = local.timetz().replace(tzinfo=None)
        if REGULAR_MARKET_OPEN <= clock < REGULAR_MARKET_CLOSE:
            selected.append(bar)
    return selected


def price_above(price: Any, level: Any) -> bool | None:
    """``price > level`` as a tri-state boolean (skills.md 4.1).

    ``None`` when either side is missing, so a rule on the flag simply does not
    match instead of asserting a trend that was never computed.
    """
    price_value = _number(price)
    level_value = _number(level)
    if price_value is None or level_value is None:
        return None
    return price_value > level_value


def dist_vwap_pct(price: float | None, vwap: float | None) -> float | None:
    """Signed distance to VWAP in percent: ``(price - vwap) / vwap * 100``.

    Negative when the price trades below VWAP. ``None`` if either input is
    missing or VWAP is non-positive.
    """
    price = _number(price)
    vwap = _number(vwap)
    if price is None or vwap is None or vwap <= 0:
        return None
    return round((price - vwap) / vwap * 100, 4)


def _first_number(quote: dict[str, Any], keys: tuple[str, ...]) -> float | None:
    for key in keys:
        value = _number(quote.get(key))
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
