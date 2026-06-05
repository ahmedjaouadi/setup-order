from __future__ import annotations

from dataclasses import dataclass
from math import floor
from typing import Any

from app.models import RiskDecision


@dataclass(frozen=True, slots=True)
class RiskLimits:
    max_open_positions: int
    max_position_amount_usd: float
    max_risk_per_trade_usd: float
    max_daily_loss_usd: float
    max_total_exposure_usd: float
    allow_short: bool = False

    @classmethod
    def from_config(cls, config: dict[str, Any]) -> "RiskLimits":
        risk = config.get("risk", {})
        return cls(
            max_open_positions=int(risk.get("max_open_positions", 5)),
            max_position_amount_usd=float(risk.get("max_position_amount_usd", 250)),
            max_risk_per_trade_usd=float(risk.get("max_risk_per_trade_usd", 15)),
            max_daily_loss_usd=float(risk.get("max_daily_loss_usd", 50)),
            max_total_exposure_usd=float(risk.get("max_total_exposure_usd", 1000)),
            allow_short=bool(risk.get("allow_short", False)),
        )


class RiskEngine:
    def __init__(self, limits: RiskLimits) -> None:
        self.limits = limits

    @staticmethod
    def worst_case_entry_price(
        setup_config: dict[str, Any],
        trigger_price: float,
    ) -> float:
        entry = setup_config.get("entry", {})
        if not isinstance(entry, dict):
            return trigger_price
        if str(entry.get("order_type", "STP_LMT")) != "STP_LMT":
            return trigger_price
        if entry.get("maximum_limit_price") is not None:
            return float(entry["maximum_limit_price"])
        if entry.get("limit_price") is not None:
            return float(entry["limit_price"])
        return trigger_price + float(entry.get("limit_offset", 0.0) or 0.0)

    def evaluate(
        self,
        setup_config: dict[str, Any],
        entry_price: float,
        stop_loss: float,
        open_positions: int,
        current_exposure_usd: float,
        daily_pnl_usd: float,
    ) -> RiskDecision:
        if entry_price <= 0:
            return RiskDecision(False, "Entry price must be positive")
        if stop_loss <= 0:
            return RiskDecision(False, "Stop loss must be positive")
        worst_case_entry_price = self.worst_case_entry_price(setup_config, entry_price)
        if worst_case_entry_price <= 0:
            return RiskDecision(False, "Worst-case entry price must be positive")
        direction = str(setup_config.get("direction", "long")).lower()
        if direction != "long" and not self.limits.allow_short:
            return RiskDecision(False, "Short trading is disabled")
        if direction == "long" and stop_loss >= worst_case_entry_price:
            return RiskDecision(
                False,
                "For a long setup, stop loss must be below entry price",
            )
        if open_positions >= self.limits.max_open_positions:
            return RiskDecision(False, "Maximum number of open positions reached")
        if daily_pnl_usd <= -abs(self.limits.max_daily_loss_usd):
            return RiskDecision(False, "Daily loss limit reached")

        setup_risk = setup_config.get("risk", {})
        max_position = float(
            setup_risk.get(
                "max_position_amount_usd",
                self.limits.max_position_amount_usd,
            )
        )
        max_risk = float(
            setup_risk.get("max_risk_usd", self.limits.max_risk_per_trade_usd)
        )
        if max_position <= 0 or max_risk <= 0:
            return RiskDecision(False, "Risk budget must be positive")
        remaining_exposure = self.limits.max_total_exposure_usd - current_exposure_usd
        max_position = min(max_position, remaining_exposure)
        if max_position <= 0:
            return RiskDecision(False, "Maximum exposure reached")

        risk_per_share = abs(worst_case_entry_price - stop_loss)
        if risk_per_share <= 0:
            return RiskDecision(False, "Risk per share must be positive")

        qty_by_budget = floor(max_position / worst_case_entry_price)
        qty_by_risk = floor(max_risk / risk_per_share)
        quantity = min(qty_by_budget, qty_by_risk)
        if quantity <= 0:
            return RiskDecision(False, "Calculated quantity is zero")

        position_amount = quantity * worst_case_entry_price
        risk_amount = quantity * risk_per_share
        return RiskDecision(
            approved=True,
            reason="Risk approved",
            quantity=quantity,
            entry_price=round(worst_case_entry_price, 4),
            stop_loss=stop_loss,
            position_amount_usd=round(position_amount, 2),
            risk_amount_usd=round(risk_amount, 2),
            trigger_price=entry_price,
        )
