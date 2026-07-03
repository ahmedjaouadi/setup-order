from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True, slots=True)
class DailyReport:
    report_id: str
    report_date: str
    generated_at: str
    top_opportunities: list[dict[str, Any]] = field(default_factory=list)
    armed_setups: list[dict[str, Any]] = field(default_factory=list)
    blocked_setups: list[dict[str, Any]] = field(default_factory=list)
    forecast_summary: dict[str, Any] = field(default_factory=dict)
    backtests: list[dict[str, Any]] = field(default_factory=list)
    trades: list[dict[str, Any]] = field(default_factory=list)
    risk: dict[str, Any] = field(default_factory=dict)
    errors: list[dict[str, Any]] = field(default_factory=list)
    next_actions: list[str] = field(default_factory=list)
