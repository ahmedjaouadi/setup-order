from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from app.models import EventLevel, SetupStatus, ValidationResult
from app.setups.setup_factory import SetupFactory, UnknownSetupTypeError
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
                raise RuntimeError(
                    "PyYAML is required to load setup YAML files."
                ) from exc
            with path.open("r", encoding="utf-8") as handle:
                data = yaml.safe_load(handle)
        if not isinstance(data, dict):
            raise ValueError(f"Setup file must contain a mapping: {path}")
        return data

    def validate_setup(self, config: dict[str, Any]) -> ValidationResult:
        try:
            setup = SetupFactory.create(config)
        except UnknownSetupTypeError as exc:
            return ValidationResult.failed([str(exc)])
        try:
            return setup.validate()
        except (TypeError, ValueError) as exc:
            return ValidationResult.failed([str(exc)])

    def load_all(self) -> list[dict[str, Any]]:
        loaded: list[dict[str, Any]] = []
        for config in self.load_setup_files():
            validation = self.validate_setup(config)
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
            record = setup.to_record(setup.initial_status())
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
        validation = self.validate_setup(config)
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

    def _status_after_config_save(
        self,
        setup: Any,
        existing: dict[str, Any] | None,
    ) -> SetupStatus | None:
        if not existing:
            return None
        if not setup.enabled:
            return SetupStatus.DISABLED
        previous_status = str(existing.get("status") or "")
        if previous_status == SetupStatus.DISABLED.value:
            return setup.initial_status()
        try:
            return SetupStatus(previous_status)
        except ValueError:
            return setup.initial_status()

    def _save_setup_file(self, config: dict[str, Any]) -> None:
        self.setups_folder.mkdir(parents=True, exist_ok=True)
        path = self._matching_setup_file(str(config["setup_id"]))
        if path is None:
            path = self.setups_folder / f"{_safe_filename(str(config['setup_id']))}.json"
        if path.suffix.lower() in {".yaml", ".yml"}:
            try:
                import yaml
            except ImportError as exc:
                raise RuntimeError(
                    "PyYAML is required to save setup YAML files."
                ) from exc
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
                data = self._load_setup_file(path)
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
        char if char.isalnum() or char in {"-", "_", "."} else "_"
        for char in value
    ).strip("._")
    return safe or "setup"
