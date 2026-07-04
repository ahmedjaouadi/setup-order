from __future__ import annotations

import time
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from app.models import EventLevel, MarketSnapshot, SignalAction
from app.storage.event_store import EventStore
from app.storage.repositories import TradingRepository

NEAR_READY_THRESHOLD = 0.96
DEFAULT_ALERT_COOLDOWN_SECONDS = 300.0

IGNORED_SCORE_CHECK_LABELS = {
    "Suivi setup",
    "Setup actif",
    "Execution auto TWS",
    "Controle risque",
}

CHECK_STATE_WEIGHTS = {
    "ok": 1.0,
    "info": 0.85,
    "wait": 0.45,
}


class OpportunityAlertService:
    """Scores opportunity readiness and records focused opportunity alerts."""

    def __init__(
        self,
        repository: TradingRepository,
        event_store: EventStore,
        near_ready_threshold: float = NEAR_READY_THRESHOLD,
        cooldown_seconds: float = DEFAULT_ALERT_COOLDOWN_SECONDS,
        monotonic_provider: Callable[[], float] | None = None,
    ) -> None:
        self.repository = repository
        self.event_store = event_store
        self.near_ready_threshold = clamp_score(near_ready_threshold)
        self.cooldown_seconds = max(float(cooldown_seconds), 0.0)
        self.monotonic_provider = monotonic_provider or time.monotonic
        self._alert_dedupe: dict[str, tuple[str, float]] = {}

    def enrich_processed_items(
        self,
        processed: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        for item in processed:
            score = score_processed_item(item, self.near_ready_threshold)
            item["opportunity_score"] = score
            item["readiness_score"] = score["score"]
            item["readiness_percent"] = score["percent"]
            item["readiness_label"] = score["label"]
        return processed

    def record_alerts(
        self,
        snapshot: MarketSnapshot,
        processed: list[dict[str, Any]],
    ) -> None:
        symbol = snapshot.symbol.upper()
        for item in processed:
            score = item.get("opportunity_score")
            if not isinstance(score, dict):
                score = score_processed_item(item, self.near_ready_threshold)
                item["opportunity_score"] = score

            event_type = opportunity_event_type(score, self.near_ready_threshold)
            if event_type is None:
                continue
            signature = opportunity_alert_signature(item, score)
            setup_id = str(item.get("setup_id") or "")
            if self.should_suppress_alert(event_type, symbol, setup_id, signature):
                continue
            self.record_alert(snapshot, item, score, event_type, signature)

    def record_alert(
        self,
        snapshot: MarketSnapshot,
        item: dict[str, Any],
        score: dict[str, Any],
        event_type: str,
        signature: str,
    ) -> None:
        symbol = snapshot.symbol.upper()
        setup_id = str(item.get("setup_id") or "")
        auto_label = "AUTO" if score.get("auto_execution_enabled") else "WATCH"
        level = EventLevel.WARNING if event_type == "opportunity_ready" else EventLevel.INFO
        message = (
            f"Opportunity {symbol} {setup_id}: "
            f"{score.get('percent')}% {score.get('label')} {auto_label}"
        )
        self.event_store.record(
            level,
            event_type,
            message,
            setup_id=setup_id or None,
            symbol=symbol,
            data={
                "setup_id": setup_id,
                "symbol": symbol,
                "setup_type": item.get("setup_type"),
                "action": item.get("action"),
                "reason": item.get("reason"),
                "score": score,
                "alert_signature": signature,
                "snapshot": {
                    "price": snapshot.price,
                    "timestamp": snapshot.timestamp,
                    "timeframe": snapshot.timeframe,
                },
            },
        )

    def should_suppress_alert(
        self,
        event_type: str,
        symbol: str,
        setup_id: str,
        signature: str,
    ) -> bool:
        if self.cooldown_seconds <= 0:
            return False
        key = f"{event_type}:{symbol.upper()}:{setup_id}"
        now = self.monotonic_provider()
        previous = self._alert_dedupe.get(key)
        if previous is not None and previous[0] == signature:
            if now - previous[1] < self.cooldown_seconds:
                return True
            self._alert_dedupe[key] = (signature, now)
            return False

        for event in self.repository.list_events(
            symbol=symbol.upper(),
            event_type=event_type,
            limit=20,
        ):
            if setup_id and event.get("setup_id") != setup_id:
                continue
            data = event.get("data") if isinstance(event.get("data"), dict) else {}
            if data.get("alert_signature") != signature:
                continue
            age = age_seconds(event.get("timestamp"))
            if age is not None and age < self.cooldown_seconds:
                self._alert_dedupe[key] = (signature, now)
                return True
            break

        self._alert_dedupe[key] = (signature, now)
        return False


def score_processed_item(
    item: dict[str, Any],
    near_ready_threshold: float = NEAR_READY_THRESHOLD,
) -> dict[str, Any]:
    action = str(item.get("action") or "")
    trace = item.get("trace") if isinstance(item.get("trace"), dict) else {}
    checks = trace.get("checks") if isinstance(trace.get("checks"), list) else []
    relevant_checks = relevant_score_checks(checks)
    ready = action == SignalAction.ENTRY_READY.value
    score = 1.0 if ready else score_checks(relevant_checks)
    score = clamp_score(score)
    label = opportunity_label(action, score, near_ready_threshold)
    return {
        "score": round(score, 4),
        "percent": round(score * 100, 1),
        "label": label,
        "near_ready_threshold": round(clamp_score(near_ready_threshold), 4),
        "action": action,
        "reason": str(item.get("reason") or ""),
        "auto_execution_enabled": trace_auto_execution_enabled(trace),
        "waiting_checks": checks_by_state(relevant_checks, {"wait"}),
        "blocking_checks": checks_by_state(relevant_checks, {"bad", "error"}),
        "ok_checks": len(checks_by_state(relevant_checks, {"ok"})),
        "total_checks": len(relevant_checks),
        "next_step": str(trace.get("next_step") or ""),
    }


def relevant_score_checks(checks: list[Any]) -> list[dict[str, Any]]:
    relevant: list[dict[str, Any]] = []
    for check in checks:
        if not isinstance(check, dict):
            continue
        label = str(check.get("label") or "")
        if label in IGNORED_SCORE_CHECK_LABELS:
            continue
        relevant.append(check)
    return relevant


def score_checks(checks: list[dict[str, Any]]) -> float:
    if not checks:
        return 0.0
    total = 0.0
    for check in checks:
        state = normalize_check_state(check.get("state"))
        total += CHECK_STATE_WEIGHTS.get(state, 0.0)
    return total / len(checks)


def checks_by_state(
    checks: list[dict[str, Any]],
    states: set[str],
) -> list[dict[str, Any]]:
    selected: list[dict[str, Any]] = []
    for check in checks:
        if normalize_check_state(check.get("state")) not in states:
            continue
        selected.append(
            {
                "label": check.get("label"),
                "state": normalize_check_state(check.get("state")),
                "actual": check.get("actual"),
                "expected": check.get("expected"),
            }
        )
    return selected


def trace_auto_execution_enabled(trace: dict[str, Any]) -> bool:
    checks = trace.get("checks") if isinstance(trace.get("checks"), list) else []
    for check in checks:
        if not isinstance(check, dict):
            continue
        if str(check.get("label") or "") != "Execution auto TWS":
            continue
        actual = str(check.get("actual") or "").strip().upper()
        if actual == "ON":
            return True
        if actual == "OFF":
            return False
    return False


def opportunity_label(
    action: str,
    score: float,
    near_ready_threshold: float = NEAR_READY_THRESHOLD,
) -> str:
    if action == SignalAction.ENTRY_READY.value:
        return "READY"
    if score >= clamp_score(near_ready_threshold):
        return "NEAR_READY"
    if score >= 0.70:
        return "WATCHING"
    if score > 0:
        return "WAITING"
    return "UNKNOWN"


def opportunity_event_type(
    score: dict[str, Any],
    near_ready_threshold: float = NEAR_READY_THRESHOLD,
) -> str | None:
    if score.get("action") == SignalAction.ENTRY_READY.value:
        return "opportunity_ready"
    numeric_score = float_or_none(score.get("score"))
    if numeric_score is not None and numeric_score >= clamp_score(near_ready_threshold):
        return "opportunity_near_ready"
    return None


def opportunity_alert_signature(
    item: dict[str, Any],
    score: dict[str, Any],
) -> str:
    waiting_labels = tuple(
        str(check.get("label") or "")
        for check in score.get("waiting_checks", [])
        if isinstance(check, dict)
    )
    blocking_labels = tuple(
        str(check.get("label") or "")
        for check in score.get("blocking_checks", [])
        if isinstance(check, dict)
    )
    percent_bucket = int(round(float(score.get("percent") or 0)))
    return repr(
        (
            item.get("setup_id"),
            score.get("label"),
            score.get("action"),
            percent_bucket,
            score.get("auto_execution_enabled"),
            waiting_labels,
            blocking_labels,
        )
    )


def normalize_check_state(value: Any) -> str:
    text = str(value or "").strip().lower()
    if text in {"ok", "info", "wait", "bad", "error"}:
        return text
    return "bad" if text else "wait"


def clamp_score(value: Any) -> float:
    numeric = float_or_none(value)
    if numeric is None:
        return 0.0
    return max(0.0, min(numeric, 1.0))


def float_or_none(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def age_seconds(value: Any) -> int | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return max(int((datetime.now(UTC) - parsed).total_seconds()), 0)
