from __future__ import annotations

from typing import Any

from app.opportunities.opportunity_expiration_policy import OpportunityExpirationPolicy
from app.storage.repositories import TradingRepository


class OpportunityShortlistService:
    def __init__(
        self,
        repository: TradingRepository,
        settings: dict[str, Any] | None = None,
        expiration_policy: OpportunityExpirationPolicy | None = None,
    ) -> None:
        self.repository = repository
        self.settings = settings or {}
        self.expiration_policy = expiration_policy or OpportunityExpirationPolicy(settings)

    def build(self, *, limit: int | None = None) -> dict[str, Any]:
        policy = self.policy()
        max_items = int(limit or policy["max_items"])
        min_score = float(policy["min_score"])
        include_blocked = bool(policy["include_blocked_with_reason"])
        all_items = self.repository.list_opportunities(limit=500)
        top: list[dict[str, Any]] = []
        blocked: list[dict[str, Any]] = []
        expired: list[dict[str, Any]] = []

        for item in all_items:
            enriched = self._enrich(item)
            status = str(enriched.get("status") or "").upper()
            if self.expiration_policy.is_expired(enriched):
                expired.append({**enriched, "status": "EXPIRED"})
                continue
            if status in {"REJECTED", "BLOCKED"}:
                if include_blocked:
                    blocked.append(enriched)
                continue
            if float(enriched.get("score") or 0) >= min_score:
                top.append(enriched)

        top.sort(key=self._priority_key, reverse=True)
        blocked.sort(key=self._priority_key, reverse=True)
        scenario_rows = self.repository.list_scenario_drafts(limit=50)
        generated_scenarios = [
            row.get("scenario") if isinstance(row.get("scenario"), dict) else row
            for row in scenario_rows
        ]
        items = top[:max_items]
        return {
            "items": items,
            "top_opportunities": items,
            "blocked_opportunities": blocked[:max_items],
            "recently_expired": expired[:max_items],
            "generated_scenarios": generated_scenarios[:max_items],
            "policy": policy,
        }

    def policy(self) -> dict[str, Any]:
        raw = self.settings.get("opportunities", {}).get("shortlist", {})
        if not isinstance(raw, dict):
            raw = {}
        return {
            "max_items": int(raw.get("max_items", 25) or 25),
            "min_score": float(raw.get("min_score", 55) or 55),
            "include_blocked_with_reason": bool(
                raw.get("include_blocked_with_reason", True)
            ),
            "priority_order": raw.get(
                "priority_order",
                [
                    "DATA_QUALITY_OK",
                    "SETUP_SCORE",
                    "VOLUME_SCORE",
                    "DISTANCE_TO_TRIGGER",
                    "FORECAST_ALIGNMENT",
                ],
            ),
        }

    def _enrich(self, opportunity: dict[str, Any]) -> dict[str, Any]:
        payload = opportunity.get("payload") if isinstance(opportunity.get("payload"), dict) else {}
        score = payload.get("score") if isinstance(payload.get("score"), dict) else {}
        components = score.get("components") if isinstance(score.get("components"), dict) else {}
        liquidity = payload.get("liquidity_filter") if isinstance(payload.get("liquidity_filter"), dict) else {}
        selection = payload.get("selection") if isinstance(payload.get("selection"), dict) else {}
        return {
            **opportunity,
            "setup_score": score.get("overall_score", opportunity.get("score")),
            "volume_score": components.get("volume_score"),
            "liquidity_score": components.get("liquidity_score"),
            "forecast_alignment_score": components.get("forecast_alignment_score"),
            "data_quality_ok": not bool(liquidity.get("blocked")),
            "distance_to_trigger": _distance_to_trigger(selection),
            "blocking_reasons": liquidity.get("issues", []),
        }

    @staticmethod
    def _priority_key(item: dict[str, Any]) -> tuple[float, float, float, float, float]:
        distance = item.get("distance_to_trigger")
        distance_score = 100.0 if distance is None else max(0.0, 100.0 - float(distance))
        return (
            100.0 if item.get("data_quality_ok") else 0.0,
            float(item.get("setup_score") or item.get("score") or 0.0),
            float(item.get("volume_score") or 0.0),
            distance_score,
            float(item.get("forecast_alignment_score") or 0.0),
        )


def _distance_to_trigger(selection: dict[str, Any]) -> float | None:
    inputs = selection.get("inputs") if isinstance(selection.get("inputs"), dict) else {}
    price = _number(inputs.get("price"))
    previous_high = _number(inputs.get("previous_high"))
    if price is None or previous_high is None or previous_high == 0:
        return None
    return round(abs(price - previous_high) / previous_high * 100, 4)


def _number(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
