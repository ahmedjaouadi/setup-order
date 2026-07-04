from __future__ import annotations

import json
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

from app.api import routes_setups
from app.conversion import canonicalize_setup_config
from app.engine.trading_engine import TradingEngine
from app.models import ConnectionStatus
from app.settings import DEFAULT_CONFIG, Settings
from app.storage.database import Database
from app.storage.repositories import TradingRepository


def _resolve_auto_select(value):
    """Simulate a user/LLM replacing every AUTO_SELECT placeholder with a concrete value."""
    if isinstance(value, list):
        return [_resolve_auto_select(item) for item in value]
    if isinstance(value, dict):
        return {key: _resolve_auto_select(item) for key, item in value.items()}
    if value == "AUTO_SELECT":
        return "RESOLVED"
    return value


MANAGEMENT_ONLY_SETUP_TYPES = ("position_management", "runner", "trailing_runner")


class OfflineBroker:
    connector_name = "paper"
    account_mode = "paper"
    display_name = "Offline broker"

    async def status(self) -> ConnectionStatus:
        return ConnectionStatus.DISCONNECTED

    def drain_audit_entries(self) -> list:
        return []


class SetupTemplateApiContractTests(unittest.IsolatedAsyncioTestCase):
    """Exercises the exact endpoint the GUI 'Generer le modele' button calls:
    GET /api/setups/config-template?template_type=universal
    through route -> engine -> setup_template_service.
    """

    async def asyncSetUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        config = deepcopy(DEFAULT_CONFIG)
        config["storage"]["database_file"] = str(root / "state.sqlite")
        config["storage"]["setups_folder"] = str(root / "setups")
        config["storage"]["logs_folder"] = str(root / "logs")
        self.settings = Settings.from_dict(config)
        self.database = Database(self.settings.database_file)
        self.database.initialize()
        self.repository = TradingRepository(self.database)
        self.engine = TradingEngine(self.settings, self.repository, broker=OfflineBroker())
        self.request = SimpleNamespace(
            app=SimpleNamespace(state=SimpleNamespace(engine=self.engine))
        )

    async def asyncTearDown(self) -> None:
        self.database.close()
        self.tmp.cleanup()

    async def test_universal_template_endpoint_uses_trailing_stop_not_legacy_stop(self) -> None:
        result = await routes_setups.setup_config_template(self.request, template_type="universal")
        skeleton = result["skeleton"]
        risk = skeleton["risk"]

        # Root trailing_stop_loss is the primary protection model.
        self.assertIn("trailing_stop_loss", skeleton)
        self.assertTrue(skeleton["trailing_stop_loss"]["enabled"])

        # No legacy fixed-stop fields anywhere in the risk block.
        self.assertNotIn("initial_stop_loss", risk)
        self.assertNotIn("protective_stop", risk)

        # required_by_setup_type must not reference the legacy fields.
        required_blob = json.dumps(skeleton["_template"]["required_by_setup_type"])
        self.assertNotIn("initial_stop_loss", required_blob)
        self.assertNotIn("protective_stop", required_blob)

    async def test_universal_template_endpoint_has_no_legacy_stop_anywhere(self) -> None:
        result = await routes_setups.setup_config_template(self.request, template_type="universal")

        # Guard the *entire* served payload against legacy fixed-stop keys, so a
        # positive "never use risk.initial_stop_loss" rule is the only allowed mention.
        full_blob = json.dumps(result)
        # These substrings only legitimately appear inside prohibition rules.
        legacy_key_occurrences = full_blob.count('"initial_stop_loss"')
        self.assertEqual(legacy_key_occurrences, 0)
        protective_key_occurrences = full_blob.count('"protective_stop"')
        self.assertEqual(protective_key_occurrences, 0)

    async def test_required_by_setup_type_has_no_legacy_stop_fields(self) -> None:
        result = await routes_setups.setup_config_template(self.request, template_type="universal")
        required = result["skeleton"]["_template"]["required_by_setup_type"]

        for setup_type, fields in required.items():
            blob = json.dumps(fields)
            self.assertNotIn(
                "risk.initial_stop_loss",
                blob,
                f"{setup_type} still requires legacy risk.initial_stop_loss",
            )
            self.assertNotIn(
                "risk.protective_stop",
                blob,
                f"{setup_type} still requires legacy risk.protective_stop",
            )

    async def test_management_only_types_require_existing_position_not_entry_transmission(
        self,
    ) -> None:
        result = await routes_setups.setup_config_template(self.request, template_type="universal")
        required = result["skeleton"]["_template"]["required_by_setup_type"]

        for setup_type in MANAGEMENT_ONLY_SETUP_TYPES:
            rules = required[setup_type]
            self.assertIn("entry.enabled=false", rules, setup_type)
            self.assertIn("position_source.require_existing_position=true", rules, setup_type)
            self.assertIn("trailing_stop_loss.current_stop before arming", rules, setup_type)
            self.assertIn("broker_safety.block_if_position_without_stop=true", rules, setup_type)
            self.assertIn("never generate an initial BUY order", rules, setup_type)
            # No initial entry to transmit for a MANAGEMENT_ONLY setup.
            self.assertNotIn(
                "trailing_stop_loss.broker_order.required_before_entry_transmission=true",
                rules,
                f"{setup_type} must not gate on entry transmission",
            )

    async def test_final_setup_strips_template_helpers_and_auto_select(self) -> None:
        result = await routes_setups.setup_config_template(self.request, template_type="universal")
        skeleton = result["skeleton"]

        # Simulate the operator turning the universal template into a final setup:
        # pick a concrete setup_type and replace every AUTO_SELECT placeholder.
        filled = _resolve_auto_select(deepcopy(skeleton))
        filled["setup_type"] = "position_management"
        filled["setup_role"] = "MANAGEMENT_ONLY"

        canonical = canonicalize_setup_config({"skeleton": filled}).config
        blob = json.dumps(canonical)

        self.assertNotIn("_template", canonical)
        self.assertNotIn("expected_output", canonical)
        self.assertNotIn("setup_type_options", canonical)
        self.assertNotIn("setup_type_selection_guide", canonical)
        self.assertNotIn("required_by_setup_type", canonical)
        self.assertNotIn("AUTO_SELECT", blob)


if __name__ == "__main__":
    unittest.main()
