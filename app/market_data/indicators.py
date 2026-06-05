from __future__ import annotations


def simple_moving_average(values: list[float], period: int) -> float | None:
    if period <= 0 or len(values) < period:
        return None
    return sum(values[-period:]) / period


def exponential_moving_average(values: list[float], period: int) -> float | None:
    if period <= 0 or len(values) < period:
        return None
    multiplier = 2 / (period + 1)
    ema = sum(values[:period]) / period
    for value in values[period:]:
        ema = (value - ema) * multiplier + ema
    return ema


def true_ranges(rows: list[dict], period: int = 14) -> list[float]:
    ranges: list[float] = []
    for index, row in enumerate(rows):
        high = _number_or_none(row.get("high"))
        low = _number_or_none(row.get("low"))
        close = _number_or_none(row.get("close"))
        if high is None or low is None or close is None:
            continue
        if index == 0:
            ranges.append(high - low)
            continue
        previous_close = _number_or_none(rows[index - 1].get("close"))
        if previous_close is None:
            ranges.append(high - low)
            continue
        ranges.append(
            max(
                high - low,
                abs(high - previous_close),
                abs(low - previous_close),
            )
        )
    return ranges[-period:] if period > 0 else ranges


def average_true_range(rows: list[dict], period: int = 14) -> float | None:
    ranges = true_ranges(rows, period=0)
    if len(ranges) < period:
        return None
    seed = sum(ranges[:period]) / period
    atr = seed
    for value in ranges[period:]:
        atr = ((atr * (period - 1)) + value) / period
    return round(atr, 4)


def _number_or_none(value) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
