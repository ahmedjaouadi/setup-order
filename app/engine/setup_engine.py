from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from app.conversion import CanonicalizationResult, canonicalize_setup_config
from app.intelligence import SemanticValidationService
from app.models import EventLevel, SetupStatus, ValidationResult
from app.setups.setup_factory import SetupFactory, UnknownSetupTypeError
from app.setups.setup_roles import (
    setup_allows_entry,
    setup_is_management_only,
    setup_role_from_config,
)
from app.storage.event_store import EventStore
from app.storage.repositories import TradingRepository


class SetupEngine:
    def __init__(
        self,
        repository: TradingRepository,
        event_store: EventStore,
        setups_folder: Path,
    ) -> None:
        self.repository = repository
        self.event_store = event_store
        self.setups_folder = setups_folder
        self.semantic_validation = SemanticValidationService()

    def load_setup_files(self) -> list[dict[str, Any]]:
        configs: list[dict[str, Any]] = []
        for path in sorted(self.setups_folder.glob("*")):
            if path.suffix.lower() not in {".yaml", ".yml", ".json"}:
                continue
            configs.append(self._load_setup_file(path))
        return configs

    def _load_setup_file(self, path: Path) -> dict[str, Any]:
        if path.suffix.lower() == ".json":
            with path.open("r", encoding="utf-8") as handle:
                data = json.load(handle)
        else:
            try:
                import yaml
            except ImportError as exc:
                raise RuntimeError("PyYAML is required to load setup YAML files.") from exc
            with path.open("r", encoding="utf-8") as handle:
                data = yaml.safe_load(handle)
        if not isinstance(data, dict):
            raise ValueError(f"Setup file must contain a mapping: {path}")
        return data

    def validate_setup(self, config: dict[str, Any]) -> ValidationResult:
        canonical = self.canonicalize_config(config)
        return self._validate_canonical_config(
            canonical.config,
            canonical.warnings,
            canonical.mapped_fields,
        )

    def validate_for_arm(self, config: dict[str, Any]) -> ValidationResult:
        canonical = self.canonicalize_config(config)
        base_validation = self._validate_canonical_config(
            canonical.config,
            canonical.warnings,
            canonical.mapped_fields,
        )
        if not base_validation.valid:
            return base_validation

        config = canonical.config
        setup = SetupFactory.create(config)
        role = setup_role_from_config(config, infer_position_management=True)
        errors: list[str] = []
        warnings: list[str] = list(base_validation.warnings)
        trailing = config.get("trailing_stop_loss", {})
        if not isinstance(trailing, dict):
            trailing = {}
        broker_order = trailing.get("broker_order", {})
        if not isinstance(broker_order, dict):
            broker_order = {}

        if not bool(config.get("enabled", True)):
            errors.append("setup.enabled must be true before arming")
        if broker_order.get("required_before_entry_transmission") is not True:
            errors.append(
                "trailing_stop_loss.broker_order.required_before_entry_transmission must be true before arming"
            )

        entry = config.get("entry", {})
        entry_enabled = bool(entry.get("enabled", True)) if isinstance(entry, dict) else False
        if setup_is_management_only(role):
            position_source = config.get("position_source", {})
            if not isinstance(position_source, dict):
                position_source = {}
            if position_source.get("mode") != "adopt_existing_ibkr_position":
                errors.append(
                    "MANAGEMENT_ONLY setup must use position_source.mode=adopt_existing_ibkr_position"
                )
            if entry_enabled:
                errors.append("MANAGEMENT_ONLY setup cannot arm an entry order")
        elif setup_allows_entry(role):
            if not entry_enabled:
                errors.append("entry.enabled must be true before arming an entry setup")
            if setup.worst_case_entry_price() is None:
                errors.append("worst-case entry price is required before arming")
            if setup.stop_loss is None:
                errors.append("trailing_stop_loss.initial_stop is required before arming")

        return ValidationResult(
            valid=not errors,
            errors=_dedupe_messages(errors),
            warnings=_dedupe_messages(warnings),
            details={
                **base_validation.details,
                "arm_validation": {
                    "setup_role": role.value,
                    "initial_status": setup.initial_status().value,
                },
            },
        )

    def canonicalize_config(self, config: dict[str, Any]) -> CanonicalizationResult:
        return canonicalize_setup_config(config)

    def _validate_canonical_config(
        self,
        config: dict[str, Any],
        warnings: list[str] | None = None,
        mapped_fields: list[dict[str, str]] | None = None,
    ) -> ValidationResult:
        semantic_report = self.semantic_validation.validate(config)
        details = {
            "canonical_mapped_fields": list(mapped_fields or []),
            "semantic_validation": semantic_report.to_details(),
        }
        try:
            setup = SetupFactory.create(config)
        except UnknownSetupTypeError as exc:
            return ValidationResult(
                valid=False,
                errors=_dedupe_messages([*semantic_report.errors, str(exc)]),
                warnings=_dedupe_messages([*(warnings or []), *semantic_report.warnings]),
                details=details,
            )
        try:
            result = setup.validate()
        except (TypeError, ValueError) as exc:
            return ValidationResult(
                valid=False,
                errors=_dedupe_messages([*semantic_report.errors, str(exc)]),
                warnings=_dedupe_messages([*(warnings or []), *semantic_report.warnings]),
                details=details,
            )
        all_errors = _dedupe_messages([*semantic_report.errors, *result.errors])
        all_warnings = _dedupe_messages(
            [*(warnings or []), *semantic_report.warnings, *result.warnings]
        )
        return ValidationResult(
            valid=not all_errors,
            errors=all_errors,
            warnings=all_warnings,
            details=details,
        )

    def load_all(self) -> list[dict[str, Any]]:
        loaded: list[dict[str, Any]] = []
        for raw_config in self.load_setup_files():
            canonical = self.canonicalize_config(raw_config)
            config = canonical.config
            validation = self._validate_canonical_config(
                config,
                canonical.warnings,
                canonical.mapped_fields,
            )
            setup_id = str(config.get("setup_id", ""))
            symbol = str(config.get("symbol", "")).upper() or None
            if not validation.valid:
                self.event_store.record(
                    EventLevel.ERROR,
                    "setup_validation_failed",
                    "; ".join(validation.errors),
                    setup_id=setup_id or None,
                    symbol=symbol,
                    data={"errors": validation.errors},
                )
                continue
            setup = SetupFactory.create(config)
            existing = self.repository.get_setup(setup.setup_id)
            status = self._status_after_config_save(setup, existing)
            record = setup.to_record(status)
            if existing:
                record.enabled = bool(existing.get("enabled", record.enabled))
                record.order_status = str(existing.get("order_status") or "")
                record.position_status = str(existing.get("position_status") or "")
                record.last_event = str(existing.get("last_event") or record.last_event)
                record.created_at = str(existing.get("created_at") or record.created_at)
            self.repository.upsert_setup(record)
            self.event_store.record(
                EventLevel.INFO,
                "setup_loaded",
                "Setup loaded and validated",
                setup_id=record.setup_id,
                symbol=record.symbol,
            )
            loaded.append(config)
        return loaded

    def create_or_update_from_config(self, config: dict[str, Any]) -> ValidationResult:
        canonical = self.canonicalize_config(config)
        config = canonical.config
        validation = self._validate_canonical_config(
            config,
            canonical.warnings,
            canonical.mapped_fields,
        )
        if not validation.valid:
            return validation
        setup = SetupFactory.create(config)
        existing = self.repository.get_setup(setup.setup_id)
        status = self._status_after_config_save(setup, existing)
        record = setup.to_record(status)
        if existing:
            record.order_status = str(existing.get("order_status") or "")
            record.position_status = str(existing.get("position_status") or "")
        record.last_event = "Setup saved"
        self.repository.upsert_setup(record)
        self._save_setup_file(config)
        self.event_store.record(
            EventLevel.INFO,
            "setup_saved",
            "Setup saved",
            setup_id=setup.setup_id,
            symbol=setup.symbol,
        )
        return validation

    def arm_setup(self, setup_id: str) -> ValidationResult:
        existing = self.repository.get_setup(setup_id)
        if existing is None:
            raise KeyError(setup_id)
        config = existing.get("config", {})
        validation = self.validate_for_arm(config)
        if not validation.valid:
            self.event_store.record(
                EventLevel.WARNING,
                "setup_arm_failed",
                "; ".join(validation.errors),
                setup_id=setup_id,
                symbol=str(existing.get("symbol", "")).upper() or None,
                data={"errors": validation.errors, "warnings": validation.warnings},
            )
            return validation
        setup = SetupFactory.create(self.canonicalize_config(config).config)
        target_status = setup.initial_status()
        self.repository.update_setup_status(
            setup_id,
            target_status.value,
            "Setup armed",
        )
        self.event_store.record(
            EventLevel.INFO,
            "setup_armed",
            "Setup armed",
            setup_id=setup_id,
            symbol=setup.symbol,
            data={"target_status": target_status.value},
        )
        return validation

    def disarm_setup(self, setup_id: str) -> None:
        existing = self.repository.get_setup(setup_id)
        if existing is None:
            raise KeyError(setup_id)
        self.repository.update_setup_status(
            setup_id,
            SetupStatus.DISABLED.value,
            "Setup disarmed",
        )
        self.event_store.record(
            EventLevel.INFO,
            "setup_disarmed",
            "Setup disarmed",
            setup_id=setup_id,
            symbol=str(existing.get("symbol", "")).upper() or None,
        )

    def disable_setup(self, setup_id: str) -> None:
        self.disarm_setup(setup_id)

    def _status_after_config_save(
        self,
        setup: Any,
        existing: dict[str, Any] | None,
    ) -> SetupStatus | None:
        if not existing:
            return SetupStatus.DISABLED
        previous_status = str(existing.get("status") or "")
        try:
            return SetupStatus(previous_status)
        except ValueError:
            return SetupStatus.DISABLED

    def _save_setup_file(self, config: dict[str, Any]) -> None:
        self.setups_folder.mkdir(parents=True, exist_ok=True)
        path = self._matching_setup_file(str(config["setup_id"]))
        if path is None:
            path = self.setups_folder / f"{_safe_filename(str(config['setup_id']))}.json"
        if path.suffix.lower() in {".yaml", ".yml"}:
            try:
                import yaml
            except ImportError as exc:
                raise RuntimeError("PyYAML is required to save setup YAML files.") from exc
            with path.open("w", encoding="utf-8") as handle:
                yaml.safe_dump(
                    config,
                    handle,
                    sort_keys=False,
                    allow_unicode=True,
                )
            return
        with path.open("w", encoding="utf-8") as handle:
            json.dump(config, handle, indent=2, ensure_ascii=False)
            handle.write("\n")

    def _matching_setup_file(self, setup_id: str) -> Path | None:
        for path in sorted(self.setups_folder.glob("*")):
            if path.suffix.lower() not in {".yaml", ".yml", ".json"}:
                continue
            try:
                data = self.canonicalize_config(self._load_setup_file(path)).config
            except Exception:
                continue
            if str(data.get("setup_id", "")) == setup_id:
                return path
        return None

    def delete_setup_file(self, setup_id: str) -> bool:
        path = self._matching_setup_file(setup_id)
        if path is None:
            return False
        path.unlink()
        return True


def _safe_filename(value: str) -> str:
    safe = "".join(
        char if char.isalnum() or char in {"-", "_", "."} else "_" for char in value
    ).strip("._")
    return safe or "setup"


def _dedupe_messages(messages: list[str]) -> list[str]:
    return list(dict.fromkeys(message for message in messages if message))
