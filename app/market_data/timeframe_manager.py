from __future__ import annotations


SUPPORTED_TIMEFRAMES = {"1m", "5m", "15m", "1h", "1d"}


def is_supported_timeframe(timeframe: str) -> bool:
    return timeframe in SUPPORTED_TIMEFRAMES

