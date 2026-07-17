from __future__ import annotations

from typing import Any

from app.models import MarketSnapshot, SetupSignal, SetupStatus, SignalAction, utc_now_iso
from app.setups.setup_conditions import (
    MANAGEMENT_ONLY_SETUP_TYPES,
    ConditionCheck,
    SetupConditionsDefinition,
    definition_for,
    evaluate_setup_conditions,
    humanize_invalidation_reason,
)
from app.storage.repositories import TradingRepository

OVERALL_WATCHING = "watching"
OVERALL_READY = "ready_to_enter"
OVERALL_ENTERED = "entered"
OVERALL_INVALIDATED = "invalidated"

CONDITION_VALIDATED = "validated"
CONDITION_IN_PROGRESS = "in_progress"
CONDITION_PENDING = "pending"
CONDITION_FAILED = "failed"

_ENTERED_STATUSES = {
    SetupStatus.ENTRY_ORDER_PLACED,
    SetupStatus.ENTRY_PARTIALLY_FILLED,
    SetupStatus.ENTRY_FILLED,
    SetupStatus.STOP_ORDER_PLACED,
    SetupStatus.STOP_PLACED,
    SetupStatus.IN_POSITION,
    SetupStatus.MANAGING_POSITION,
    SetupStatus.PARTIAL_EXIT,
    SetupStatus.CLOSED,
    SetupStatus.RECONCILING_EXISTING_POSITION,
}


def overall_status_from_setup_status(status: SetupStatus | None) -> str:
    if status is None:
        return OVERALL_WATCHING
    if status == SetupStatus.INVALIDATED:
        return OVERALL_INVALIDATED
    if status == SetupStatus.ENTRY_READY:
        return OVERALL_READY
    if status in _ENTERED_STATUSES:
        return OVERALL_ENTERED
    return OVERALL_WATCHING


class SetupConditionTracker:
    """Evaluation sequentielle des conditions d'un setup, avec persistance.

    Alimente par StockMarketMonitor a chaque analyse (vrai snapshot + vrai
    signal moteur); les timestamps de validation sont persistes en base et
    jamais recalcules. Lecture cote API via conditions_payload().
    """

    def __init__(self, repository: TradingRepository) -> None:
        self.repository = repository

    def update_from_evaluation(
        self,
        setup: dict[str, Any],
        current_status: SetupStatus,
        signal: SetupSignal,
        snapshot: MarketSnapshot | None,
    ) -> dict[str, Any] | None:
        definition = definition_for(str(setup.get("setup_type") or ""))
        if definition is None:
            return None
        setup_id = str(setup.get("setup_id") or "")
        if not setup_id:
            return None
        config = setup.get("config") if isinstance(setup.get("config"), dict) else {}
        metadata = signal.metadata if isinstance(signal.metadata, dict) else {}
        analysis = metadata.get("analysis") if isinstance(metadata.get("analysis"), dict) else {}
        overall, invalidation_reason = self._overall_from_signal(current_status, signal)
        floor = max(
            definition.floor_for(current_status),
            definition.floor_for(signal.target_status),
        )
        checks = evaluate_setup_conditions(definition, config, snapshot, analysis, current_status)
        previous = self.repository.get_setup_condition_state(setup_id)
        payload = build_conditions_payload(
            definition=definition,
            setup_id=setup_id,
            checks=checks,
            floor=floor,
            overall=overall,
            invalidation_reason=invalidation_reason,
            previous=previous,
            now=utc_now_iso(),
            hold_reason=str(signal.reason or ""),
        )
        if not _same_payload(payload, previous):
            self.repository.save_setup_condition_state(setup_id, payload)
        return payload

    def conditions_payload(self, setup: dict[str, Any]) -> dict[str, Any]:
        setup_id = str(setup.get("setup_id") or "")
        setup_type = str(setup.get("setup_type") or "")
        status = _status_or_none(setup.get("status"))
        definition = definition_for(setup_type)
        if definition is None:
            return _no_checklist_payload(setup_id, setup_type, status)
        persisted = self.repository.get_setup_condition_state(setup_id)
        if persisted is not None:
            return self._reconcile_with_status(definition, persisted, setup, status)
        config = setup.get("config") if isinstance(setup.get("config"), dict) else {}
        checks = evaluate_setup_conditions(definition, config, None, None, status)
        return build_conditions_payload(
            definition=definition,
            setup_id=setup_id,
            checks=checks,
            floor=definition.floor_for(status),
            overall=overall_status_from_setup_status(status),
            invalidation_reason=str(setup.get("status_reason") or ""),
            previous=None,
            now=utc_now_iso(),
        )

    def _reconcile_with_status(
        self,
        definition: SetupConditionsDefinition,
        persisted: dict[str, Any],
        setup: dict[str, Any],
        status: SetupStatus | None,
    ) -> dict[str, Any]:
        """Le statut du setup peut avoir change hors analyse (lifecycle, ordre
        transmis): l'etat global affiche suit toujours le statut reel."""
        status_overall = overall_status_from_setup_status(status)
        persisted_overall = str(persisted.get("overall_status") or "")
        if status_overall == persisted_overall:
            return persisted
        if status_overall == OVERALL_WATCHING and persisted_overall == OVERALL_READY:
            # Le signal ENTRY_READY est plus fin que le statut (l'ordre peut
            # etre retenu par un garde-fou): la vue du tracker fait foi.
            return persisted
        if status_overall == OVERALL_WATCHING:
            # Rearme par le lifecycle apres invalidation/position: la
            # sequence repart de zero.
            config = setup.get("config") if isinstance(setup.get("config"), dict) else {}
            checks = evaluate_setup_conditions(definition, config, None, None, status)
            return build_conditions_payload(
                definition=definition,
                setup_id=str(setup.get("setup_id") or ""),
                checks=checks,
                floor=definition.floor_for(status),
                overall=OVERALL_WATCHING,
                invalidation_reason="",
                previous=None,
                now=utc_now_iso(),
            )
        adjusted = dict(persisted)
        adjusted["overall_status"] = status_overall
        if status_overall == OVERALL_INVALIDATED:
            # status_reason porte les codes internes du lifecycle
            # (INVALIDATION_LEVEL_BROKEN...): les rendre lisibles ici.
            # Idempotent: une raison deja traduite est renvoyee telle quelle.
            adjusted["invalidation_reason"] = humanize_invalidation_reason(
                str(setup.get("status_reason") or "")
                or str(persisted.get("invalidation_reason") or "")
            )
        adjusted["summary_message"] = _summary_message(
            overall=status_overall,
            invalidation_reason=str(adjusted.get("invalidation_reason") or ""),
            validated_count=sum(
                1
                for condition in adjusted.get("conditions", [])
                if condition.get("status") == CONDITION_VALIDATED
            ),
            total=len(adjusted.get("conditions", [])),
            current_label=_current_label(adjusted),
        )
        return adjusted

    @staticmethod
    def _overall_from_signal(
        current_status: SetupStatus,
        signal: SetupSignal,
    ) -> tuple[str, str]:
        if signal.action == SignalAction.INVALIDATE or signal.target_status == SetupStatus.INVALIDATED:
            return OVERALL_INVALIDATED, str(signal.reason or "")
        if signal.action == SignalAction.ENTRY_READY or signal.target_status == SetupStatus.ENTRY_READY:
            return OVERALL_READY, ""
        status_overall = overall_status_from_setup_status(current_status)
        if status_overall == OVERALL_INVALIDATED:
            return OVERALL_INVALIDATED, str(signal.reason or "")
        return status_overall, ""


def build_conditions_payload(
    definition: SetupConditionsDefinition,
    setup_id: str,
    checks: list[ConditionCheck],
    floor: int,
    overall: str,
    invalidation_reason: str,
    previous: dict[str, Any] | None,
    now: str,
    hold_reason: str = "",
) -> dict[str, Any]:
    previous_conditions = _previous_conditions_by_id(previous, overall)
    total = len(definition.conditions)
    validated_count = min(max(floor, 0), total)
    while validated_count < total and checks[validated_count].met is True:
        validated_count += 1
    if overall in {OVERALL_READY, OVERALL_ENTERED}:
        validated_count = total
    failed_id = (
        definition.failed_condition_for(invalidation_reason)
        if overall == OVERALL_INVALIDATED
        else ""
    )
    conditions: list[dict[str, Any]] = []
    current_step: int | None = None
    for index, (condition, check) in enumerate(zip(definition.conditions, checks)):
        prev = previous_conditions.get(condition.condition_id, {})
        prev_validated = prev.get("status") == CONDITION_VALIDATED
        if overall == OVERALL_INVALIDATED and condition.condition_id == failed_id:
            status = CONDITION_FAILED
            validated_at = None
        elif index < validated_count:
            status = CONDITION_VALIDATED
            validated_at = prev.get("validated_at") if prev_validated else now
        elif overall == OVERALL_INVALIDATED:
            status = prev.get("status") or CONDITION_PENDING
            if status == CONDITION_IN_PROGRESS:
                status = CONDITION_PENDING
            validated_at = prev.get("validated_at") if prev_validated else None
        elif current_step is None:
            status = CONDITION_IN_PROGRESS
            current_step = index
            validated_at = None
        else:
            status = CONDITION_PENDING
            validated_at = None
        observed = (
            str(prev.get("observed_value") or "")
            if status == CONDITION_VALIDATED and prev_validated
            else check.observed_value
        )
        conditions.append(
            {
                "id": condition.condition_id,
                "label": condition.label,
                "description": condition.description,
                "status": status,
                "validated_at": validated_at,
                "observed_value": observed,
                "target": check.target,
            }
        )
    current_label = (
        definition.conditions[current_step].label if current_step is not None else ""
    )
    # Traduit seulement maintenant: failed_condition_for() ci-dessus matche sur
    # la raison BRUTE, la traduire plus tot casserait la condition en echec.
    readable_reason = humanize_invalidation_reason(invalidation_reason)
    return {
        "setup_id": setup_id,
        "setup_type": definition.setup_type,
        "setup_name": definition.setup_name,
        "setup_direction": definition.direction,
        "management_only": False,
        "conditions": conditions,
        "current_step": current_step,
        "overall_status": overall,
        "invalidation_reason": readable_reason if overall == OVERALL_INVALIDATED else "",
        "summary_message": _summary_message(
            overall=overall,
            invalidation_reason=readable_reason,
            validated_count=sum(
                1 for condition in conditions if condition["status"] == CONDITION_VALIDATED
            ),
            total=total,
            current_label=current_label,
            hold_reason=hold_reason,
        ),
        "updated_at": now,
    }


def _previous_conditions_by_id(
    previous: dict[str, Any] | None,
    overall: str,
) -> dict[str, dict[str, Any]]:
    if not isinstance(previous, dict):
        return {}
    # Sequence terminee puis relancee (rearm lifecycle): on repart de zero.
    # ready_to_enter -> watching n'est PAS un rearm: le signal d'entree peut
    # simplement etre retenu par un garde-fou systeme, l'historique est garde.
    if overall == OVERALL_WATCHING and previous.get("overall_status") in {
        OVERALL_ENTERED,
        OVERALL_INVALIDATED,
    }:
        return {}
    conditions = previous.get("conditions")
    if not isinstance(conditions, list):
        return {}
    return {
        str(condition.get("id") or ""): condition
        for condition in conditions
        if isinstance(condition, dict)
    }


def _summary_message(
    overall: str,
    invalidation_reason: str,
    validated_count: int,
    total: int,
    current_label: str,
    hold_reason: str = "",
) -> str:
    if overall == OVERALL_INVALIDATED:
        reason = invalidation_reason or "condition remise en cause"
        return f"Setup invalide: {reason}"
    if overall == OVERALL_READY:
        return "Toutes les conditions sont reunies -> signal d'entree"
    if overall == OVERALL_ENTERED:
        return "Position prise: toutes les conditions ont ete validees"
    base = f"{validated_count}/{total} conditions validees"
    if current_label:
        return f"{base} - etape actuelle: {current_label}"
    if total and validated_count == total and hold_reason:
        # Conditions setup reunies mais entree retenue par un garde-fou
        # systeme (session, trade guards, couts): afficher la raison moteur.
        return f"{base} - entree retenue par les garde-fous systeme: {hold_reason}"
    return base


def _current_label(payload: dict[str, Any]) -> str:
    step = payload.get("current_step")
    conditions = payload.get("conditions")
    if isinstance(step, int) and isinstance(conditions, list) and 0 <= step < len(conditions):
        return str(conditions[step].get("label") or "")
    return ""


def _no_checklist_payload(
    setup_id: str,
    setup_type: str,
    status: SetupStatus | None,
) -> dict[str, Any]:
    management_only = setup_type in MANAGEMENT_ONLY_SETUP_TYPES
    if management_only:
        summary = "Setup de gestion de position: pas de sequence d'entree a verifier."
    else:
        summary = "Aucune checklist de conditions definie pour ce type de setup."
    return {
        "setup_id": setup_id,
        "setup_type": setup_type,
        "setup_name": setup_type,
        "setup_direction": "long",
        "management_only": management_only,
        "conditions": [],
        "current_step": None,
        "overall_status": overall_status_from_setup_status(status),
        "invalidation_reason": "",
        "summary_message": summary,
        "updated_at": utc_now_iso(),
    }


def _same_payload(payload: dict[str, Any], previous: dict[str, Any] | None) -> bool:
    if not isinstance(previous, dict):
        return False
    ignored = {"updated_at"}
    left = {key: value for key, value in payload.items() if key not in ignored}
    right = {key: value for key, value in previous.items() if key not in ignored}
    return left == right


def _status_or_none(value: Any) -> SetupStatus | None:
    try:
        return SetupStatus(str(value))
    except ValueError:
        return None
