from __future__ import annotations

from app.models import MarketSnapshot


class MarketDataService:
    def __init__(self) -> None:
        self._latest: dict[str, MarketSnapshot] = {}

    def update(self, snapshot: MarketSnapshot) -> None:
        self._latest[snapshot.symbol.upper()] = snapshot

    def latest(self, symbol: str) -> MarketSnapshot | None:
        return self._latest.get(symbol.upper())

    def all_latest(self) -> list[MarketSnapshot]:
        return list(self._latest.values())

