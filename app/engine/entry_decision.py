from __future__ import annotations

from typing import Any

from app.models import SetupSignal, SetupStatus, SignalAction
from app.setups.setup_roles import setup_allows_entry, setup_role_from_config


DISPLAY_BY_STATUS: dict[str, tuple[str, str, str]] = {
    "ENTRY_READY": (
        "Entree possible",
        "Toutes les conditions critiques sont valides et le bracket avec stop protecteur est pret.",
        "READY",
    ),
    "BLOCKED_MISSING_PROTECTIVE_STOP_ORDER": (
        "Entree bloquee - stop-loss non pret",
        "Le setup est valide, mais aucun ordre stop-loss protecteur broker-ready n'est pret a etre attache.",
        "BLOCKED",
    ),
    "ACTIVE_ENTRY_ORDER_UNPROTECTED": (
        "Ordre actif non protege",
        "Un ordre d'entree existe deja mais aucun stop-loss protecteur attache n'a ete trouve.",
        "BLOCKED",
    ),
    "DUPLICATE_ORDER_BLOCKED": (
        "Ordre deja actif",
        "Un ordre actif protege existe deja pour ce setup.",
        "BLOCKED",
    ),
    "POSITION_OPEN_STOP_MISSING_CRITICAL": (
        "Position ouverte sans stop actif",
        "Une position ouverte existe sans stop-loss broker actif. Toute nouvelle entree reste bloquee.",
        "BLOCKED",
    ),
    "STOP_SUBMISSION_FAILED": (
        "Stop-loss broker rejete",
        "Le broker a refuse le stop protecteur. L'entree automatique reste bloquee jusqu'a resolution.",
        "BLOCKED",
    ),
    "WATCH_ONLY_TRIGGERED": (
        "Signal detecte - surveillance uniquement",
        "Le signal est detecte, mais l'execution automatique n'est pas autorisee.",
        "WATCH_ONLY",
    ),
    "MISSED_BREAKOUT": (
        "Entree bloquee - breakout manque",
        "Le prix est trop loin de la zone d'entree prevue. Le bot ne court pas apres le prix.",
        "MISSED",
    ),
    "MISSED_BREAKDOWN": (
        "Entree bloquee - breakdown manque",
        "Le prix est trop loin de la zone d'entree prevue pour un setup short.",
        "MISSED",
    ),
    "ENTRY_LIMIT_EXCEEDED": (
        "Entree bloquee - prix au-dessus de la limite",
        "Le prix executable depasse la limite d'entree configuree.",
        "BLOCKED",
    ),
    "PRICE_ALREADY_ABOVE_MAXIMUM_LIMIT": (
        "Entree bloquee - prix au-dessus de la limite",
        "Le prix ask depasse la limite maximale d'entree.",
        "BLOCKED",
    ),
    "PRICE_TOO_FAR_ABOVE_ENTRY": (
        "Entree bloquee - prix trop loin de la zone prevue",
        "Le prix executable est au-dessus de la zone d'entree autorisee.",
        "BLOCKED",
    ),
    "PRICE_TOO_FAR_BELOW_ENTRY": (
        "Entree bloquee - prix trop loin de la zone prevue",
        "Le prix executable est sous la zone d'entree autorisee.",
        "BLOCKED",
    ),
    "WAITING_RETEST": (
        "Attente d'un retest",
        "Le setup attend un retour dans la zone prevue avant toute nouvelle entree.",
        "WAITING_RETEST",
    ),
    "REARM_REQUIRED": (
        "Setup a corriger / recalculer",
        "Le setup doit etre rearme avec une nouvelle zone ou une nouvelle limite.",
        "BLOCKED",
    ),
    "INVALID_CONFIGURATION": (
        "Configuration invalide",
        "La configuration du setup ne permet pas une decision d'entree sure.",
        "BLOCKED",
    ),
    "MISSING_MARKET_DATA": (
        "Donnees marche insuffisantes",
        "Les donnees critiques de marche sont absentes ou non pretes.",
        "BLOCKED",
    ),
    "PAUSED_MISSING_MARKET_DATA": (
        "Donnees marche insuffisantes",
        "Les donnees critiques de marche sont absentes ou non pretes.",
        "BLOCKED",
    ),
    "VOLUME_DATA_MISSING": (
        "Donnees volume insuffisantes",
        "Le volume comparable ne peut pas etre calcule pour confirmer le setup.",
        "BLOCKED",
    ),
    "BLOCKED_BY_RISK": (
        "Entree bloquee - risque depasse",
        "Le risque executable courant depasse le risque maximal autorise.",
        "BLOCKED",
    ),
    "REJECTED_BY_RISK": (
        "Entree bloquee - risque depasse",
        "Le sizing calcule ne respecte pas les contraintes de risque.",
        "BLOCKED",
    ),
    "BLOCKED_BY_SIZE": (
        "Entree bloquee - quantite nulle",
        "La quantite maximale calculee est nulle.",
        "BLOCKED",
    ),
    "BLOCKED_BY_SESSION_MISMATCH": (
        "Entree bloquee - confirmation de session requise",
        "La bougie de signal et le prix live ne proviennent pas de la meme session.",
        "BLOCKED",
    ),
    "PREMARKET_TRIGGER_DETECTED": (
        "Signal detecte hors marche - entree bloquee",
        "Le trigger a ete touche avant l'ouverture. Attente de confirmation en marche regulier.",
        "WAITING",
    ),
    "AFTER_HOURS_TRIGGER_DETECTED": (
        "Signal detecte after-hours - entree bloquee",
        "Le setup parait confirme apres la cloture. Attente de la prochaine session reguliere.",
        "WAITING",
    ),
    "RTH_CONFIRMATION_REQUIRED": (
        "Confirmation RTH requise",
        "Le moteur doit confirmer la session reguliere avant toute entree automatique.",
        "WAITING",
    ),
    "BLOCKED_OUTSIDE_REGULAR_MARKET_HOURS": (
        "Entree bloquee hors marche regulier",
        "Le setup a ete detecte hors RTH et ne peut pas devenir executable avant confirmation reguliere.",
        "WAITING",
    ),
    "WAITING_AFTER_OPEN_BARS": (
        "Attente de confirmation apres ouverture",
        "Le marche vient d'ouvrir. Le bot attend avant de revalider le setup.",
        "WAITING",
    ),
    "WAITING_VOLUME_CONFIRMATION": (
        "Attente confirmation volume",
        "Le prix a declenche le setup, mais le volume doit encore confirmer.",
        "WAITING",
    ),
    "PRICE_TRIGGERED_WEAK_VOLUME": (
        "Attente confirmation volume",
        "Le prix est au-dessus du niveau, mais le volume ne confirme pas encore.",
        "WAITING",
    ),
    "WAITING_CONFIRMATION": (
        "Attente confirmation",
        "Le setup attend encore une confirmation critique.",
        "WAITING",
    ),
    "WAITING_TRIGGER": (
        "Attente du trigger",
        "Le prix n'a pas encore atteint le trigger d'entree.",
        "WAITING",
    ),
    "DISABLED": (
        "Setup desactive",
        "Le setup n'autorise pas d'entree tant qu'il est desactive.",
        "BLOCKED",
    ),
    "ENTRY_DISABLED": (
        "Entree desactivee",
        "La section entry.enabled interdit une entree initiale.",
        "BLOCKED",
    ),
    "MANAGEMENT_ONLY": (
        "Gestion uniquement",
        "Ce setup gere une position existante et ne doit jamais creer d'ordre BUY initial.",
        "BLOCKED",
    ),
}


def build_entry_decision(
    *,
    setup: dict[str, Any],
    current_status: SetupStatus,
    signal: SetupSignal,
    metadata: dict[str, Any],
) -> dict[str, Any]:
    config = setup.get("config") if isinstance(setup.get("config"), dict) else {}
    analysis = metadata.get("analysis") if isinstance(metadata.get("analysis"), dict) else {}
    status = str(
        analysis.get("decision_status")
        or (signal.target_status.value if signal.target_status else "")
        or signal.action.value
        or current_status.value
    )
    status = _normalize_status(status, current_status)

    missing = _string_list(analysis.get("missing_conditions"))
    blocking = _string_list(analysis.get("blocking_conditions"))
    warnings = _string_list(analysis.get("warnings"))
    if missing and status in {"HOLD", "STATUS_CHANGE"}:
        status = "MISSING_MARKET_DATA"

    role = setup_role_from_config(config, infer_position_management=True)
    entry = config.get("entry") if isinstance(config.get("entry"), dict) else {}
    entry_enabled = entry.get("enabled", True) is not False
    runtime_enabled = bool(setup.get("enabled"))
    config_enabled = config.get("enabled", True) is not False
    can_send_order = (
        signal.action == SignalAction.ENTRY_READY
        and status == "ENTRY_READY"
        and runtime_enabled
        and config_enabled
        and entry_enabled
        and setup_allows_entry(role)
        and not blocking
        and not missing
    )
    protection_ready = _protective_stop_order_ready(
        config=config,
        signal=signal,
        analysis=analysis,
    )
    protection_status = "NO_ENTRY_ORDER"
    if can_send_order and not protection_ready:
        status = "BLOCKED_MISSING_PROTECTIVE_STOP_ORDER"
        blocking = sorted(set(blocking + ["PROTECTIVE_STOP_ORDER_NOT_READY"]))
        can_send_order = False
        protection_status = "ENTRY_ORDER_PENDING_WITHOUT_STOP_BLOCKED"
    elif protection_ready:
        protection_status = "NO_ENTRY_ORDER"
    else:
        protection_status = "ENTRY_ORDER_PENDING_WITHOUT_STOP_BLOCKED"
    decision = str(
        analysis.get("decision")
        or ("ENTRY_ALLOWED" if can_send_order else "NO_ENTRY")
    )
    if not can_send_order and decision == "ENTRY_ALLOWED":
        decision = "NO_ENTRY"

    title, message, readiness = _display(status)
    title = str(analysis.get("display_title") or title)
    message = str(analysis.get("display_message") or message)
    readiness = str(analysis.get("readiness_label") or readiness)
    if not entry_enabled and status == "ENTRY_READY":
        status = "ENTRY_DISABLED"
        title, message, readiness = _display(status)
        decision = "NO_ENTRY"
    if not setup_allows_entry(role):
        status = "MANAGEMENT_ONLY"
        title, message, readiness = _display(status)
        decision = "NO_ENTRY"
    if not runtime_enabled or not config_enabled:
        warnings.append("WATCH_ONLY_OR_DISABLED_RUNTIME")

    return {
        "status": status,
        "decision": decision,
        "can_send_order": can_send_order,
        "display_title": title,
        "display_message": message,
        "readiness_label": readiness,
        "next_action": str(analysis.get("next_action") or ""),
        "blocking_reasons": blocking,
        "missing_conditions": missing,
        "warnings": sorted(set(warnings)),
        "planned_vs_current_risk": _planned_vs_current_risk(analysis),
        "protective_stop_order_ready": protection_ready,
        "configured_stop_price": _configured_stop_price(signal=signal, analysis=analysis),
        "protective_stop_order_id": None,
        "protection_status": protection_status,
    }


def attach_entry_decision(
    *,
    setup: dict[str, Any],
    current_status: SetupStatus,
    signal: SetupSignal,
    metadata: dict[str, Any],
) -> dict[str, Any]:
    decision = build_entry_decision(
        setup=setup,
        current_status=current_status,
        signal=signal,
        metadata=metadata,
    )
    metadata["entry_decision"] = decision
    analysis = metadata.get("analysis")
    if isinstance(analysis, dict):
        analysis["entry_decision"] = decision
    return metadata


def _normalize_status(status: str, current_status: SetupStatus) -> str:
    value = status.strip().upper()
    if value in {"PAUSED_MISSING_MARKET_DATA", "MISSING_REQUIRED_DATA"}:
        return "MISSING_MARKET_DATA"
    if value == "HOLD":
        return current_status.value
    return value


def _display(status: str) -> tuple[str, str, str]:
    return DISPLAY_BY_STATUS.get(
        status,
        (
            status.replace("_", " ").title(),
            "Decision finale produite par le moteur.",
            "READY" if status == "ENTRY_READY" else "BLOCKED" if "BLOCK" in status else "WAITING",
        ),
    )


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value if item not in (None, "")]


def _planned_vs_current_risk(analysis: dict[str, Any]) -> dict[str, Any] | None:
    risk = analysis.get("risk_preview")
    if not isinstance(risk, dict):
        return None
    return {
        "planned_entry_price": risk.get("worst_case_entry_price"),
        "current_executable_price": risk.get("current_executable_price"),
        "stop_loss": risk.get("initial_stop"),
        "planned_risk_per_share": risk.get("risk_per_share"),
        "current_risk_per_share": risk.get("current_risk_per_share"),
        "planned_max_quantity": risk.get("maximum_quantity"),
        "current_max_quantity_by_risk": risk.get("current_max_quantity_by_risk"),
        "max_risk_usd": risk.get("max_risk_usd"),
        "current_risk_for_planned_quantity": risk.get("current_risk_for_planned_quantity"),
        "risk_status": risk.get("risk_status"),
    }


def _configured_stop_price(*, signal: SetupSignal, analysis: dict[str, Any]) -> float | None:
    if signal.stop_loss is not None:
        return float(signal.stop_loss)
    trailing = analysis.get("trailing_stop_loss")
    if isinstance(trailing, dict):
        value = trailing.get("initial_stop")
    else:
        value = None
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _protective_stop_order_ready(
    *,
    config: dict[str, Any],
    signal: SetupSignal,
    analysis: dict[str, Any],
) -> bool:
    stop_price = _configured_stop_price(signal=signal, analysis=analysis)
    if stop_price is None or stop_price <= 0:
        return False
    trailing = config.get("trailing_stop_loss")
    if not isinstance(trailing, dict) or trailing.get("enabled") is not True:
        return False
    broker_order = trailing.get("broker_order")
    if not isinstance(broker_order, dict):
        return False
    if broker_order.get("required_before_entry_transmission") is not True:
        return False
    broker_ready = trailing.get("trailing_stop_order_ready")
    if broker_ready is None:
        broker_ready = broker_order.get("trailing_stop_order_ready")
    if broker_ready is not True:
        return False
    risk = analysis.get("risk_preview")
    if isinstance(risk, dict):
        maximum_quantity = risk.get("maximum_quantity")
        if maximum_quantity is not None:
            try:
                if int(maximum_quantity) <= 0:
                    return False
            except (TypeError, ValueError):
                return False
    return True
