from __future__ import annotations

from datetime import datetime, timezone
from typing import Any


DEFAULT_EXPIRATION_MINUTES = {
    "15m": 60,
    "1h": 240,
    "1d": 1440,
}


class OpportunityExpirationPolicy:
    def __init__(self, settings: dict[str, Any] | None = None) -> None:
        self.settings = settings or {}

    def is_expired(self, opportunity: dict[str, Any], *, now: datetime | None = None) -> bool:
        if str(opportunity.get("status") or "").upper() == "EXPIRED":
            return True
        detected_at = _parse_datetime(opportunity.get("detected_at"))
        if detected_at is None:
            return False
        now = now or datetime.now(timezone.utc)
        age_minutes = (now - detected_at).total_seconds() / 60
        return age_minutes > self.expire_after_minutes(str(opportunity.get("timeframe") or "15m"))

    def expire_after_minutes(self, timeframe: str) -> int:
        raw = (
            self.settings.get("opportunities", {})
            .get("shortlist", {})
            .get("expire_after_minutes", {})
        )
        if not isinstance(raw, dict):
            raw = {}
        value = raw.get(timeframe, DEFAULT_EXPIRATION_MINUTES.get(timeframe, 240))
        try:
            return int(value)
        except (TypeError, ValueError):
            return DEFAULT_EXPIRATION_MINUTES.get(timeframe, 240)


def _parse_datetime(value: Any) -> datetime | None:
    if not value:
        return None
    text = str(value)
    if text.endswith("Z"):
        text = f"{text[:-1]}+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed
