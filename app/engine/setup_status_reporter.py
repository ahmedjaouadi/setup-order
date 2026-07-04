from __future__ import annotations

from collections.abc import Callable
from typing import Any

from app.engine.setup_diagnostics import (
    TERMINAL_SETUP_STATUSES,
    WAITING_SETUP_STATUSES,
)
from app.engine.setup_engine import SetupEngine
from app.models import SetupStatus, SignalAction
from app.settings import Settings
from app.setups.setup_roles import setup_is_management_only, setup_role_from_config
from app.storage.repositories import TradingRepository


class SetupStatusReporter:
    """Builds read-only setup diagnostics for the UI/API."""

    def __init__(
        self,
        settings: Settings,
        repository: TradingRepository,
        setup_engine: SetupEngine,
        broker_provider: Callable[[], Any],
    ) -> None:
        self.settings = settings
        self.repository = repository
        self.setup_engine = setup_engine
        self.broker_provider = broker_provider

    def configuration_status(self) -> dict[str, Any]:
        setups = self.repository.list_setups()
        events = self.repository.list_events(limit=500)
        scenarios = [self._configuration_scenario_status(setup, events) for setup in setups]
        watched = [scenario for scenario in scenarios if scenario["watched"]]
        broker = self.broker_provider()
        return {
            "active_configuration": {
                "app_mode": self.settings.raw.get("app", {}).get("mode"),
                "broker_connector": str(getattr(broker, "connector_name", "")),
                "broker_account_mode": str(getattr(broker, "account_mode", "")),
                "setups_folder": str(self.settings.setups_folder),
                "database_file": str(self.settings.database_file),
                "loaded_setup_count": len(setups),
                "enabled_setup_count": len([setup for setup in setups if setup.get("enabled")]),
                "auto_execution_enabled_count": len(
                    [setup for setup in setups if setup.get("enabled")]
                ),
                "watched_setup_count": len(watched),
            },
            "current_scenario": watched[0] if len(watched) == 1 else None,
            "currently_monitored": [
                {
                    "setup_id": scenario["setup_id"],
                    "symbol": scenario["symbol"],
                    "status": scenario["status"],
                }
                for scenario in watched
            ],
            "scenarios": scenarios,
        }

    def _configuration_scenario_status(
        self,
        setup: dict[str, Any],
        events: list[dict[str, Any]],
    ) -> dict[str, Any]:
        config = setup.get("config", {})
        if not isinstance(config, dict):
            config = {}
        validation = self.setup_engine.validate_setup(config)
        status = str(setup.get("status") or "")
        enabled_runtime = bool(setup.get("enabled"))
        enabled_config = config.get("enabled", True) is not False
        auto_execution_enabled = enabled_runtime and enabled_config
        waiting = status in WAITING_SETUP_STATUSES or status.startswith("WAITING")
        terminal = status in TERMINAL_SETUP_STATUSES
        watched = validation.valid and not terminal
        armed = watched and auto_execution_enabled
        latest_analysis = self._latest_analysis_for_setup(setup, events)
        analysis_item = latest_analysis.get("item") if latest_analysis else None
        analysis_event = latest_analysis.get("event") if latest_analysis else None
        awaited_condition = self._awaited_condition(setup, analysis_item)
        next_action = self._expected_action(setup, analysis_item)
        return {
            "setup_id": setup.get("setup_id"),
            "symbol": setup.get("symbol"),
            "setup_type": setup.get("setup_type"),
            "setup_role": setup_role_from_config(config).value,
            "mode": setup.get("mode") or config.get("mode"),
            "status": status,
            "enabled": enabled_runtime,
            "config_enabled": enabled_config,
            "auto_execution_enabled": auto_execution_enabled,
            "armed": armed,
            "waiting": waiting and watched,
            "armed_state": self._armed_state(
                status=status,
                auto_execution_enabled=auto_execution_enabled,
                terminal=terminal,
                validation_valid=validation.valid,
                waiting=waiting,
            ),
            "watched": watched,
            "missing_required_parameters": validation.errors,
            "validation_warnings": validation.warnings,
            "awaited_condition": awaited_condition,
            "expected_action": next_action,
            "latest_analysis_at": (
                analysis_event.get("timestamp") if isinstance(analysis_event, dict) else None
            ),
            "latest_analysis_action": (
                analysis_item.get("action") if isinstance(analysis_item, dict) else None
            ),
            "latest_analysis_reason": (
                analysis_item.get("reason") if isinstance(analysis_item, dict) else None
            ),
        }

    @staticmethod
    def _armed_state(
        status: str,
        auto_execution_enabled: bool,
        terminal: bool,
        validation_valid: bool,
        waiting: bool,
    ) -> str:
        if not validation_valid:
            return "CONFIG_INVALID"
        if terminal:
            return "TERMINAL"
        if not auto_execution_enabled:
            return "WATCH_ONLY"
        if status == SetupStatus.ENTRY_READY.value:
            return "ENTRY_READY"
        if status in {
            SetupStatus.IN_POSITION.value,
            SetupStatus.MANAGING_POSITION.value,
            SetupStatus.ENTRY_ORDER_PLACED.value,
            SetupStatus.ENTRY_PARTIALLY_FILLED.value,
            SetupStatus.ENTRY_FILLED.value,
            SetupStatus.STOP_ORDER_PLACED.value,
            SetupStatus.STOP_PLACED.value,
        }:
            return "ACTIVE"
        if waiting:
            return "ARMED_WAITING"
        return "ARMED"

    @staticmethod
    def _latest_analysis_for_setup(
        setup: dict[str, Any],
        events: list[dict[str, Any]],
    ) -> dict[str, Any] | None:
        setup_id = str(setup.get("setup_id") or "")
        symbol = str(setup.get("symbol") or "")
        for event in events:
            if event.get("event_type") != "stock_analysis":
                continue
            data = event.get("data") if isinstance(event.get("data"), dict) else {}
            processed = data.get("processed")
            if not isinstance(processed, list):
                continue
            for item in processed:
                if not isinstance(item, dict):
                    continue
                if str(item.get("setup_id") or "") == setup_id:
                    return {"event": event, "item": item}
            if len(processed) == 1 and str(event.get("symbol") or "") == symbol:
                item = processed[0]
                if isinstance(item, dict):
                    return {"event": event, "item": item}
        return None

    def _awaited_condition(
        self,
        setup: dict[str, Any],
        analysis_item: dict[str, Any] | None,
    ) -> str:
        condition = self._condition_from_analysis(analysis_item)
        if condition:
            return condition
        return self._static_awaited_condition(setup)

    @staticmethod
    def _condition_from_analysis(analysis_item: dict[str, Any] | None) -> str:
        if not isinstance(analysis_item, dict):
            return ""
        metadata = (
            analysis_item.get("metadata") if isinstance(analysis_item.get("metadata"), dict) else {}
        )
        analysis = metadata.get("analysis") if isinstance(metadata.get("analysis"), dict) else {}
        for key, label in (
            ("missing_conditions", "Conditions manquantes"),
            ("blocking_conditions", "Conditions bloquantes"),
        ):
            values = analysis.get(key)
            if isinstance(values, list) and values:
                return f"{label}: {', '.join(str(value) for value in values)}"
        trace = analysis_item.get("trace") if isinstance(analysis_item.get("trace"), dict) else {}
        checks = trace.get("checks")
        if not isinstance(checks, list):
            return ""
        ignored_labels = {
            "Setup actif",
            "Suivi setup",
            "Execution auto TWS",
            "Statut suivi",
        }
        for check in checks:
            if not isinstance(check, dict):
                continue
            if str(check.get("state") or "") in {"ok", "info"}:
                continue
            label = str(check.get("label") or "")
            if label in ignored_labels:
                continue
            parts = [label]
            if check.get("expected") not in (None, ""):
                parts.append(f"attendu={check['expected']}")
            if check.get("actual") not in (None, ""):
                parts.append(f"actuel={check['actual']}")
            if check.get("detail"):
                parts.append(str(check["detail"]))
            return " | ".join(parts)
        return ""

    def _expected_action(
        self,
        setup: dict[str, Any],
        analysis_item: dict[str, Any] | None,
    ) -> str:
        action = self._action_from_analysis(analysis_item)
        if action:
            return action
        return self._static_expected_action(setup)

    @staticmethod
    def _action_from_analysis(analysis_item: dict[str, Any] | None) -> str:
        if not isinstance(analysis_item, dict):
            return ""
        action = str(analysis_item.get("action") or "")
        target_status = analysis_item.get("target_status")
        if action == SignalAction.ENTRY_READY.value:
            return "Verifier le risque puis envoyer un bracket protege (entree + stop)."
        if action == SignalAction.STATUS_CHANGE.value and target_status:
            return f"Passer au statut {target_status} et continuer la surveillance."
        if action == SignalAction.INVALIDATE.value:
            return "Invalider le scenario et stopper la recherche d'entree."
        if action == SignalAction.RAISE_STOP.value:
            return "Relever le stop de protection selon la regle de gestion."
        metadata = (
            analysis_item.get("metadata") if isinstance(analysis_item.get("metadata"), dict) else {}
        )
        analysis = metadata.get("analysis") if isinstance(metadata.get("analysis"), dict) else {}
        next_action = analysis.get("next_action")
        if next_action and str(next_action) != "WAIT":
            return str(next_action)
        trace = analysis_item.get("trace") if isinstance(analysis_item.get("trace"), dict) else {}
        return str(trace.get("next_step") or "")

    @staticmethod
    def _static_awaited_condition(setup: dict[str, Any]) -> str:
        config = setup.get("config", {})
        if not isinstance(config, dict):
            config = {}
        setup_type = str(setup.get("setup_type") or config.get("setup_type") or "")
        status = str(setup.get("status") or "")
        if status == SetupStatus.ENTRY_READY.value:
            return "Signal d'entree pret; en attente du controle risque."
        if status in TERMINAL_SETUP_STATUSES:
            return "Aucune condition: scenario terminal."
        if status == SetupStatus.RECONCILING_EXISTING_POSITION.value:
            return "Attendre la reconciliation avec la position IBKR existante."
        if status in {SetupStatus.IN_POSITION.value, SetupStatus.MANAGING_POSITION.value}:
            return "Attendre qu'une regle de gestion de position soit atteinte."
        if setup_type == "breakout_retest":
            if status == SetupStatus.WAITING_ACTIVATION.value:
                return "Attendre une cloture journaliere au-dessus du niveau breakout.daily_close_above."
            return "Attendre un retest dans la zone configuree avec confirmation haussiere."
        if setup_type == "momentum_breakout":
            if status == SetupStatus.WAITING_RETEST.value:
                return (
                    "Attendre le retest de la zone missed_breakout puis une nouvelle confirmation."
                )
            return "Attendre breakout au-dessus de breakout.resistance avec volume, spread et risque valides."
        if setup_type == "aggressive_rebound":
            if status == SetupStatus.WAITING_ACTIVATION.value:
                return "Attendre que le prix touche la zone support_zone."
            return "Attendre une confirmation haussiere apres rebond support."
        if setup_type == "range_breakout":
            return "Attendre une cassure au-dessus de range.high sans cloture sous range.low."
        if setup_type == "pullback_continuation":
            if status == SetupStatus.WAITING_ACTIVATION.value:
                return "Attendre la confirmation de tendance EMA20 > EMA50."
            return "Attendre un pullback vers EMA20 avec confirmation haussiere."
        if setup_type in {"runner", "position_management"}:
            return "Attendre qu'une regle de stop management soit atteinte."
        return "Attendre le prochain scan stock."

    @staticmethod
    def _static_expected_action(setup: dict[str, Any]) -> str:
        config = setup.get("config", {})
        if not isinstance(config, dict):
            config = {}
        setup_type = str(setup.get("setup_type") or config.get("setup_type") or "")
        status = str(setup.get("status") or "")
        setup_role = setup_role_from_config(config)
        auto_execution_enabled = (
            bool(setup.get("enabled")) and config.get("enabled", True) is not False
        )
        if not auto_execution_enabled:
            return "Surveillance uniquement: aucune execution automatique TWS."
        if status in TERMINAL_SETUP_STATUSES:
            return "Aucune action automatique: scenario terminal."
        if setup_is_management_only(setup_role) or setup_type in {
            "runner",
            "position_management",
        }:
            return "Mettre a jour le stop si une regle de gestion est validee."
        if status == SetupStatus.WAITING_ACTIVATION.value and setup_type in {
            "breakout_retest",
            "aggressive_rebound",
            "pullback_continuation",
        }:
            return "Passer a WAITING_ENTRY_SIGNAL puis continuer la surveillance."
        return "Creer un signal ENTRY_READY, verifier le risque, puis envoyer un bracket protege."
