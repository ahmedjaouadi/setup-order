from __future__ import annotations

from dataclasses import dataclass


@dataclass(slots=True)
class Candle:
    symbol: str
    timeframe: str
    open: float
    high: float
    low: float
    close: float
    volume: int = 0


class CandleBuilder:
    def from_ohlc(
        self,
        symbol: str,
        timeframe: str,
        open_price: float,
        high: float,
        low: float,
        close: float,
        volume: int = 0,
    ) -> Candle:
        return Candle(
            symbol=symbol.upper(),
            timeframe=timeframe,
            open=open_price,
            high=high,
            low=low,
            close=close,
            volume=volume,
        )

