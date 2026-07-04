from __future__ import annotations

from datetime import UTC, datetime, timedelta
from threading import RLock
from typing import Any


class ForecastCache:
    """Small thread-safe TTL cache; persistence remains owned by ForecastRepository."""

    def __init__(self, ttl_seconds: int = 1200) -> None:
        self.ttl = timedelta(seconds=max(0, ttl_seconds))
        self._items: dict[str, tuple[datetime, Any]] = {}
        self._lock = RLock()

    def get(self, key: str) -> Any | None:
        with self._lock:
            item = self._items.get(key)
            if item is None:
                return None
            created_at, value = item
            if datetime.now(UTC) - created_at > self.ttl:
                self._items.pop(key, None)
                return None
            return value

    def set(self, key: str, value: Any) -> None:
        with self._lock:
            self._items[key] = (datetime.now(UTC), value)

    def invalidate(self, key: str | None = None) -> None:
        with self._lock:
            self._items.clear() if key is None else self._items.pop(key, None)

    @staticmethod
    def key(model: str, symbol: str, timeframe: str, horizon_bars: int) -> str:
        return f"{model.lower()}:{symbol.upper()}:{timeframe}:{int(horizon_bars)}"
