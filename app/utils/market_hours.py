from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time, timezone
from typing import Any
from zoneinfo import ZoneInfo


US_EQUITY_TIMEZONE = ZoneInfo("America/New_York")
PREMARKET_OPEN = time(4, 0)
REGULAR_MARKET_OPEN = time(9, 30)
REGULAR_MARKET_CLOSE = time(16, 0)
AFTER_HOURS_CLOSE = time(20, 0)


@dataclass(frozen=True, slots=True)
class MarketSessionContext:
    session: str
    current_time: str
    market_open_time: str
    is_regular_trading_hours: bool


def coerce_datetime(value: datetime | str | None) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)
    text = str(value).strip()
    if not text:
        return None
    normalized = text.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=timezone.utc)


def classify_us_equity_session(current_time: datetime) -> str:
    localized = current_time.astimezone(US_EQUITY_TIMEZONE)
    if localized.weekday() >= 5:
        return "CLOSED"
    clock = localized.timetz().replace(tzinfo=None)
    if PREMARKET_OPEN <= clock < REGULAR_MARKET_OPEN:
        return "PRE_MARKET"
    if REGULAR_MARKET_OPEN <= clock < REGULAR_MARKET_CLOSE:
        return "RTH"
    if REGULAR_MARKET_CLOSE <= clock < AFTER_HOURS_CLOSE:
        return "AFTER_HOURS"
    return "CLOSED"


def current_us_equity_session_context(
    current_time: datetime | str | None = None,
) -> MarketSessionContext:
    parsed = coerce_datetime(current_time) or datetime.now(timezone.utc)
    localized = parsed.astimezone(US_EQUITY_TIMEZONE)
    market_open = datetime.combine(
        localized.date(),
        REGULAR_MARKET_OPEN,
        tzinfo=US_EQUITY_TIMEZONE,
    )
    session = classify_us_equity_session(localized)
    return MarketSessionContext(
        session=session,
        current_time=localized.isoformat(),
        market_open_time=market_open.isoformat(),
        is_regular_trading_hours=session == "RTH",
    )
