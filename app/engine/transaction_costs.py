"""Transaction costs, fees and slippage model (docs/skills.md section 24bis).

On low-risk trades (max_risk_usd = 15) round-trip costs can eat 10-30% of the
risk budget. This module estimates the full round-trip cost, gates entries on
``cost_to_risk_ratio`` and provides the realistic simulated fill used in
paper mode (never a perfect fill at the trigger).
"""

from __future__ import annotations

from typing import Any

from app.engine.trade_guards import REASON_RISK_TOO_HIGH, STATUS_NO_GO

COST_GATE_OK = "OK"
COST_GATE_WARNING = "WARNING"
COST_GATE_NO_GO = "NO_GO"


def transaction_cost_settings(settings: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(settings, dict):
        return {}
    guards = settings.get("trade_guards")
    guards = guards if isinstance(guards, dict) else {}
    config = guards.get("transaction_costs")
    return config if isinstance(config, dict) else {}


def estimate_round_trip_cost_usd(
    *,
    quantity: int,
    spread: float | None,
    settings: dict[str, Any] | None,
) -> dict[str, Any]:
    """Estimated total cost for entry + exit (skills.md 24bis.2).

    estimated_total_cost = commissions + expected_slippage + spread/2,
    doubled for the round trip.
    """

    config = transaction_cost_settings(settings)
    quantity = max(int(quantity or 0), 0)
    commission_per_order = _number(config.get("commission_per_order_usd"), 1.0) or 0.0
    commission_per_share = _number(config.get("commission_per_share_usd"), 0.005) or 0.0
    regulatory_per_order = _number(config.get("regulatory_fees_per_order_usd"), 0.05) or 0.0
    slippage_per_share = _number(config.get("expected_slippage_per_share_usd"), 0.01) or 0.0
    spread_value = max(_number(spread, 0.0) or 0.0, 0.0)

    commissions = 2 * (commission_per_order + commission_per_share * quantity)
    regulatory = 2 * regulatory_per_order
    slippage = 2 * slippage_per_share * quantity
    spread_cost = 2 * (spread_value / 2) * quantity

    total = commissions + regulatory + slippage + spread_cost
    return {
        "quantity": quantity,
        "commissions_usd": round(commissions, 4),
        "regulatory_fees_usd": round(regulatory, 4),
        "expected_slippage_usd": round(slippage, 4),
        "spread_cost_usd": round(spread_cost, 4),
        "estimated_total_cost_usd": round(total, 4),
        "spread_used": spread_value,
    }


def evaluate_cost_gate(
    *,
    quantity: int,
    spread: float | None,
    max_risk_usd: float,
    settings: dict[str, Any] | None,
) -> dict[str, Any]:
    """Apply the 24bis.2 rules and return a gate payload.

    - ratio > max_cost_to_risk_ratio (0.30): status NO_GO / RISK_TOO_HIGH
    - ratio > warn_cost_to_risk_ratio (0.15): WARNING (quality penalty)
    """

    config = transaction_cost_settings(settings)
    estimate = estimate_round_trip_cost_usd(
        quantity=quantity,
        spread=spread,
        settings=settings,
    )
    result: dict[str, Any] = {
        **estimate,
        "max_risk_usd": max_risk_usd,
        "cost_to_risk_ratio": None,
        "gate": COST_GATE_OK,
        "status": None,
        "reason_code": None,
    }
    if config.get("enabled", True) is False:
        return result
    if max_risk_usd is None or max_risk_usd <= 0:
        return result
    ratio = estimate["estimated_total_cost_usd"] / float(max_risk_usd)
    warn_ratio = _number(config.get("warn_cost_to_risk_ratio"), 0.15) or 0.15
    max_ratio = _number(config.get("max_cost_to_risk_ratio"), 0.30) or 0.30
    result["cost_to_risk_ratio"] = round(ratio, 4)
    result["warn_cost_to_risk_ratio"] = warn_ratio
    result["max_cost_to_risk_ratio"] = max_ratio
    if ratio > max_ratio:
        result["gate"] = COST_GATE_NO_GO
        result["status"] = STATUS_NO_GO
        result["reason_code"] = REASON_RISK_TOO_HIGH
    elif ratio > warn_ratio:
        result["gate"] = COST_GATE_WARNING
    return result


def simulated_fill_price(
    *,
    trigger_price: float,
    spread: float | None,
    settings: dict[str, Any] | None,
    direction: str = "long",
) -> float:
    """Realistic paper fill (skills.md 24bis.2).

    simulated_fill = trigger_price + max(0.01, 0.5 * spread) for a long
    (mirrored for a short). Paper mode must never assume a perfect fill.
    """

    config = transaction_cost_settings(settings)
    min_slippage = _number(config.get("simulated_fill_min_slippage_usd"), 0.01) or 0.01
    spread_fraction = _number(config.get("simulated_fill_spread_fraction"), 0.5) or 0.5
    spread_value = max(_number(spread, 0.0) or 0.0, 0.0)
    slippage = max(min_slippage, spread_fraction * spread_value)
    if str(direction or "long").strip().lower() == "short":
        return round(trigger_price - slippage, 4)
    return round(trigger_price + slippage, 4)


def _number(value: Any, default: float | None = None) -> float | None:
    if value in (None, ""):
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default
