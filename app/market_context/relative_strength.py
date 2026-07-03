from __future__ import annotations


def percent_change(current: float | None, previous: float | None) -> float | None:
    if current is None or previous in (None, 0):
        return None
    return round(((current - previous) / previous) * 100, 4)


def relative_strength(left: float | None, right: float | None) -> float | None:
    if left is None or right is None:
        return None
    return round(left - right, 4)


def market_context_score(
    stock_perf_1d: float | None,
    sector_perf_1d: float | None,
    spy_perf_1d: float | None,
    event_penalty: int = 0,
) -> int:
    score = 0
    if stock_perf_1d is not None:
        score += 25 if stock_perf_1d > 0 else -20 if stock_perf_1d < 0 else 0
    if stock_perf_1d is not None and sector_perf_1d is not None:
        score += 25 if stock_perf_1d > sector_perf_1d else -20 if stock_perf_1d < sector_perf_1d else 0
    if stock_perf_1d is not None and spy_perf_1d is not None:
        score += 25 if stock_perf_1d > spy_perf_1d else -20 if stock_perf_1d < spy_perf_1d else 0
    if sector_perf_1d is not None:
        score += 15 if sector_perf_1d > 0 else -10 if sector_perf_1d < 0 else 0
    if sector_perf_1d is not None and spy_perf_1d is not None:
        score += 10 if sector_perf_1d > spy_perf_1d else -10 if sector_perf_1d < spy_perf_1d else 0
    return max(-100, min(100, score - event_penalty))


def context_status(score: int) -> str:
    if score >= 60:
        return "STRONG_CONTEXT"
    if score >= 20:
        return "POSITIVE_CONTEXT"
    if score > -20:
        return "NEUTRAL_CONTEXT"
    if score > -60:
        return "WEAK_CONTEXT"
    return "BLOCKED_OR_RISKY_CONTEXT"
