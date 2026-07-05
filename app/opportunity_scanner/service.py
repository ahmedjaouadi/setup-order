from __future__ import annotations

from collections.abc import Callable
from typing import Any

from app.models import utc_now_iso
from app.opportunity_scanner.detectors import (
    detect_opportunity_types,
    primary_opportunity_type,
)
from app.opportunity_scanner.schemas import (
    CREATE_SETUP_CANDIDATE_OR_WAIT_FOR_RETEST,
    MONITOR_FOR_CONFIRMATION,
    NO_ACTION,
    NO_OPPORTUNITY,
    OPPORTUNITY_DETECTED,
    WAIT_FOR_RETEST,
    WATCHLIST_ONLY,
    WATCHLIST_OPPORTUNITY,
    WEAK_OPPORTUNITY,
    OpportunitySignal,
)
from app.opportunity_scanner.scoring import OpportunityContextScorer
from app.opportunity_scanner.technique_evaluator import TechniqueEvaluator

TechniqueProvider = Callable[[], list[dict[str, Any]]]


class MarketContextOpportunityScanner:
    """Turns descriptive market context snapshots into non-executable signals.

    When `technique_provider` is supplied, detection is delegated to the
    persisted technique library (`detection_techniques`) instead of the
    hardcoded rules in `detectors.py`. Without one, behaviour is unchanged
    (legacy hardcoded rules) - this keeps callers that don't have a database
    (e.g. plain unit tests) working exactly as before.
    """

    def __init__(
        self,
        settings: dict[str, Any] | None = None,
        technique_provider: TechniqueProvider | None = None,
    ) -> None:
        self.settings = settings or {}
        self.scorer = OpportunityContextScorer(settings)
        self.technique_provider = technique_provider
        self.technique_evaluator = TechniqueEvaluator()

    def evaluate(self, snapshot: dict[str, Any]) -> dict[str, Any]:
        normalized = self._normalize_snapshot(snapshot)
        types, detected_by, matched_ids = self._detect(normalized)
        score_payload = self.scorer.score(normalized, types)
        status = self._status(score_payload, types)
        recommended_action = self._recommended_action(status, score_payload["warnings"])
        badges = self._badges(status, types, score_payload["warnings"])
        primary_type = primary_opportunity_type(types)
        signal = OpportunitySignal(
            symbol=str(normalized.get("symbol") or "").upper(),
            opportunity_status=status,
            opportunity_type=primary_type,
            opportunity_types=types or ["WATCHLIST_ANOMALY"],
            opportunity_score=score_payload["score"],
            discovery_score=score_payload["discovery_score"],
            risk_adjusted_score=score_payload["risk_adjusted_score"],
            reasons=score_payload["reasons"],
            warnings=score_payload["warnings"],
            recommended_next_action=recommended_action,
            can_send_order=False,
            badges=badges,
            source_snapshot={**normalized, "detected_at": utc_now_iso()},
            detected_by=detected_by.get(primary_type),
            detected_by_techniques=matched_ids,
        )
        return signal.to_dict()

    def _detect(self, normalized: dict[str, Any]) -> tuple[list[str], dict[str, str], list[str]]:
        if self.technique_provider is None:
            return detect_opportunity_types(normalized), {}, []
        techniques = self.technique_provider()
        types, detected_by = self.technique_evaluator.evaluate(techniques, normalized)
        matched_ids = self.technique_evaluator.matched_technique_ids(techniques, normalized)
        return types, detected_by, matched_ids

    def scan(self, snapshots: list[dict[str, Any]]) -> dict[str, Any]:
        items = [self.evaluate(snapshot) for snapshot in snapshots]
        items.sort(
            key=lambda item: (
                item.get("opportunity_status") == OPPORTUNITY_DETECTED,
                float(item.get("opportunity_score") or 0),
                float(item.get("discovery_score") or 0),
            ),
            reverse=True,
        )
        return {"items": items, "count": len(items)}

    def _normalize_snapshot(self, snapshot: dict[str, Any]) -> dict[str, Any]:
        normalized = dict(snapshot)
        stock_perf = _number(_first_value(normalized, "perf_stock_1d", "stock_perf_1d"))
        sector_perf = _number(_first_value(normalized, "perf_sector_1d", "sector_perf_1d"))
        spy_perf = _number(_first_value(normalized, "perf_spy_1d", "spy_perf_1d"))
        if stock_perf is None:
            stock_perf = _percent_change(
                _number(_first_value(normalized, "price", "last", "close")),
                _number(_first_value(normalized, "previous_close", "daily_close")),
            )
        normalized["stock_perf_1d"] = stock_perf
        normalized["sector_perf_1d"] = sector_perf
        normalized["spy_perf_1d"] = spy_perf
        normalized.setdefault(
            "relative_strength_vs_sector", _relative_strength(stock_perf, sector_perf)
        )
        normalized.setdefault("relative_strength_vs_spy", _relative_strength(stock_perf, spy_perf))
        return normalized

    def _status(self, score_payload: dict[str, Any], types: list[str]) -> str:
        thresholds = self.scorer.thresholds()
        score = float(score_payload["risk_adjusted_score"])
        discovery_score = float(score_payload["discovery_score"])
        if discovery_score >= thresholds["detected"] and types:
            return OPPORTUNITY_DETECTED
        if score >= thresholds["watchlist"] or discovery_score >= thresholds["watchlist"]:
            return WATCHLIST_OPPORTUNITY
        if score >= thresholds["weak"] or discovery_score >= thresholds["weak"]:
            return WEAK_OPPORTUNITY
        return NO_OPPORTUNITY

    @staticmethod
    def _recommended_action(status: str, warnings: list[str]) -> str:
        if "DO_NOT_CHASE_EXTENDED_PRICE" in warnings:
            return WAIT_FOR_RETEST
        if status == OPPORTUNITY_DETECTED:
            return CREATE_SETUP_CANDIDATE_OR_WAIT_FOR_RETEST
        if status == WATCHLIST_OPPORTUNITY:
            return MONITOR_FOR_CONFIRMATION
        if status == WEAK_OPPORTUNITY:
            return WATCHLIST_ONLY
        return NO_ACTION

    @staticmethod
    def _badges(status: str, types: list[str], warnings: list[str]) -> list[str]:
        badges = []
        if status == OPPORTUNITY_DETECTED:
            badges.append("OPPORTUNITY")
        elif status == WATCHLIST_OPPORTUNITY:
            badges.append("WATCH")
        if "INTRADAY_MOMENTUM_ANOMALY" in types:
            badges.append("MOMENTUM")
        if "VOLUME_EXPANSION" in types:
            badges.append("VOLUME")
        if "BREAKOUT_CANDIDATE" in types:
            badges.append("RETEST")
        if "DO_NOT_CHASE_EXTENDED_PRICE" in warnings:
            badges.append("EXTENDED")
        return badges


def _first_value(payload: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        value = payload.get(key)
        if value not in (None, ""):
            return value
    return None


def _number(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _percent_change(current: float | None, previous: float | None) -> float | None:
    if current is None or previous in (None, 0):
        return None
    return round(((current - previous) / previous) * 100, 4)


def _relative_strength(left: float | None, right: float | None) -> float | None:
    if left is None or right is None:
        return None
    return round(left - right, 4)
