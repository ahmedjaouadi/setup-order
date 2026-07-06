from __future__ import annotations

from typing import Any

from app.opportunities.opportunity_expiration_policy import OpportunityExpirationPolicy
from app.opportunities.opportunity_to_scenario_mapper import OpportunityToScenarioMapper
from app.storage.repositories import TradingRepository

LEVELS_READY = "READY"
LEVELS_INCOMPLETE = "INCOMPLETE"
STOP_SOURCE_SCENARIO = "SCENARIO"
STOP_SOURCE_ATR_FALLBACK = "ATR_FALLBACK"
DEFAULT_ATR_STOP_MULTIPLIER = 1.5


class OpportunityShortlistService:
    def __init__(
        self,
        repository: TradingRepository,
        settings: dict[str, Any] | None = None,
        expiration_policy: OpportunityExpirationPolicy | None = None,
        mapper: OpportunityToScenarioMapper | None = None,
    ) -> None:
        self.repository = repository
        self.settings = settings or {}
        self.expiration_policy = expiration_policy or OpportunityExpirationPolicy(settings)
        self.mapper = mapper or OpportunityToScenarioMapper(self.settings)

    def build(self, *, limit: int | None = None) -> dict[str, Any]:
        policy = self.policy()
        max_items = int(limit or policy["max_items"])
        min_score = float(policy["min_score"])
        include_blocked = bool(policy["include_blocked_with_reason"])
        all_items = self.repository.list_opportunities(limit=500)
        scenario_rows = self.repository.list_scenario_drafts(limit=50)
        generated_scenarios = [
            row.get("scenario") if isinstance(row.get("scenario"), dict) else row
            for row in scenario_rows
        ]
        drafts_by_opportunity: dict[str, dict[str, Any]] = {}
        for scenario in generated_scenarios:
            source_id = str(scenario.get("source_opportunity_id") or "")
            if source_id and source_id not in drafts_by_opportunity:
                drafts_by_opportunity[source_id] = scenario
        top: list[dict[str, Any]] = []
        blocked: list[dict[str, Any]] = []
        expired: list[dict[str, Any]] = []

        for item in all_items:
            enriched = self._enrich(item, drafts_by_opportunity)
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
            "include_blocked_with_reason": bool(raw.get("include_blocked_with_reason", True)),
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

    def _enrich(
        self,
        opportunity: dict[str, Any],
        drafts_by_opportunity: dict[str, dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        payload = opportunity.get("payload") if isinstance(opportunity.get("payload"), dict) else {}
        score = payload.get("score") if isinstance(payload.get("score"), dict) else {}
        components = score.get("components") if isinstance(score.get("components"), dict) else {}
        liquidity = (
            payload.get("liquidity_filter")
            if isinstance(payload.get("liquidity_filter"), dict)
            else {}
        )
        selection = payload.get("selection") if isinstance(payload.get("selection"), dict) else {}
        draft = (drafts_by_opportunity or {}).get(str(opportunity.get("opportunity_id") or ""))
        return {
            **opportunity,
            "setup_score": score.get("overall_score", opportunity.get("score")),
            "volume_score": components.get("volume_score"),
            "liquidity_score": components.get("liquidity_score"),
            "forecast_alignment_score": components.get("forecast_alignment_score"),
            "data_quality_ok": not bool(liquidity.get("blocked")),
            "distance_to_trigger": _distance_to_trigger(selection),
            "blocking_reasons": liquidity.get("issues", []),
            "detected_by": payload.get("detected_by"),
            **self._levels(opportunity, draft),
        }

    def _levels(
        self,
        opportunity: dict[str, Any],
        draft_scenario: dict[str, Any] | None,
    ) -> dict[str, Any]:
        """Consultative entry/stop levels (etape 12).

        The scenario draft is the source when it exists; otherwise the mapper
        runs on the fly (pure function, nothing persisted). A missing
        structural stop falls back to an ATR stop explicitly marked as such —
        never an invented value.
        """
        scenario = draft_scenario
        if scenario is None:
            try:
                scenario = self.mapper.map(opportunity)
            except Exception:
                scenario = None
        entry = (scenario or {}).get("entry")
        entry = entry if isinstance(entry, dict) else {}
        trailing = (scenario or {}).get("trailing_stop_loss")
        trailing = trailing if isinstance(trailing, dict) else {}
        suggested_entry = _number(entry.get("trigger_price"))
        suggested_limit = _number(entry.get("limit_price"))
        suggested_stop = _number(trailing.get("initial_stop"))
        stop_source = STOP_SOURCE_SCENARIO if suggested_stop is not None else None
        ambiguities = (scenario or {}).get("ambiguities")
        ambiguities = ambiguities if isinstance(ambiguities, list) else []
        if suggested_stop is None:
            fallback = self._atr_fallback_stop(opportunity, suggested_entry)
            if fallback is not None:
                suggested_stop = fallback
                stop_source = STOP_SOURCE_ATR_FALLBACK
        risk_per_share = None
        if suggested_entry is not None and suggested_stop is not None:
            risk_per_share = round(suggested_entry - suggested_stop, 4)
        levels_status = (
            LEVELS_READY
            if suggested_entry is not None and suggested_stop is not None
            else LEVELS_INCOMPLETE
        )
        return {
            "suggested_entry": suggested_entry,
            "suggested_limit": suggested_limit,
            "suggested_stop": suggested_stop,
            "stop_source": stop_source,
            "risk_per_share": risk_per_share,
            "levels_status": levels_status,
            "levels_ambiguities": ambiguities,
            "levels_source": "SCENARIO_DRAFT" if draft_scenario is not None else "MAPPER",
        }

    def _atr_fallback_stop(
        self,
        opportunity: dict[str, Any],
        suggested_entry: float | None,
    ) -> float | None:
        payload = opportunity.get("payload") if isinstance(opportunity.get("payload"), dict) else {}
        snapshot = (
            payload.get("market_snapshot")
            if isinstance(payload.get("market_snapshot"), dict)
            else {}
        )
        selection = payload.get("selection") if isinstance(payload.get("selection"), dict) else {}
        inputs = selection.get("inputs") if isinstance(selection.get("inputs"), dict) else {}
        atr = _number(snapshot.get("atr_15m"))
        base_price = suggested_entry
        for candidate in (inputs.get("price"), snapshot.get("price"), snapshot.get("last")):
            if base_price is not None:
                break
            base_price = _number(candidate)
        if atr is None or atr <= 0 or base_price is None or base_price <= 0:
            return None
        multiplier = self._atr_stop_multiplier()
        stop = round(base_price - multiplier * atr, 4)
        return stop if stop > 0 else None

    def _atr_stop_multiplier(self) -> float:
        raw = self.settings.get("opportunities", {})
        raw = raw.get("shortlist", {}) if isinstance(raw, dict) else {}
        value = _number(raw.get("atr_stop_multiplier")) if isinstance(raw, dict) else None
        return value if value is not None and value > 0 else DEFAULT_ATR_STOP_MULTIPLIER

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
