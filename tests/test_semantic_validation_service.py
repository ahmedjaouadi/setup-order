from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from app.engine.setup_engine import SetupEngine
from app.intelligence import SemanticValidationService
from app.storage.database import Database
from app.storage.event_store import EventStore
from app.storage.repositories import TradingRepository
from tests.test_setups import valid_breakout_config, valid_momentum_config


class SemanticValidationServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.service = SemanticValidationService()

    def test_reports_applied_schema_files(self) -> None:
        report = self.service.validate(valid_breakout_config())

        self.assertTrue(report.valid)
        self.assertEqual(
            report.schema_files,
            ["setup.base.schema.json", "setup.breakout_retest.schema.json"],
        )

    def test_schema_flags_structural_type_mismatch(self) -> None:
        config = valid_breakout_config()
        config["entry"]["enabled"] = "yes"

        report = self.service.validate(config)

        self.assertFalse(report.valid)
        self.assertTrue(any("entry.enabled must be boolean" in error for error in report.errors))

    def test_semantic_flags_momentum_limit_below_resistance(self) -> None:
        config = valid_momentum_config()
        config["entry"]["maximum_limit_price"] = 15.70

        report = self.service.validate(config)

        self.assertFalse(report.valid)
        self.assertIn(
            "entry.maximum_limit_price must not be below breakout.resistance "
            "for a momentum breakout setup.",
            report.errors,
        )

    def test_entry_setup_requires_trailing_initial_stop(self) -> None:
        config = valid_momentum_config()
        config["trailing_stop_loss"]["initial_stop"] = None

        report = self.service.validate(config)

        self.assertFalse(report.valid)
        self.assertIn(
            "trailing_stop_loss.initial_stop is required before arming",
            report.errors,
        )

    def test_management_setup_requires_initial_trailing_stop_before_arming(self) -> None:
        config = valid_momentum_config()
        config["setup_type"] = "position_management"
        config["setup_role"] = "MANAGEMENT_ONLY"
        config["entry"] = {"enabled": False}
        config["position_source"] = {
            "mode": "adopt_existing_ibkr_position",
            "require_existing_position": True,
        }
        config["management"] = {
            "stop_management": {
                "mode": "TRAILING_STOP_LOSS",
                "steps": [],
            }
        }
        config["trailing_stop_loss"]["initial_stop"] = None
        config["trailing_stop_loss"]["current_stop"] = 14.90

        report = self.service.validate(config)

        self.assertFalse(report.valid)
        self.assertIn(
            "trailing_stop_loss.initial_stop is required before arming",
            report.errors,
        )


class SetupEngineSemanticValidationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.database = Database(root / "state.sqlite")
        self.database.initialize()
        self.repository = TradingRepository(self.database)
        self.event_store = EventStore(self.repository)
        self.engine = SetupEngine(
            repository=self.repository,
            event_store=self.event_store,
            setups_folder=root / "setups",
        )

    def tearDown(self) -> None:
        self.database.close()
        self.tmp.cleanup()

    def test_validate_setup_exposes_semantic_details_and_canonical_mapping(self) -> None:
        validation = self.engine.validate_setup(
            {
                "setup_id": "UEC_20260611_001",
                "symbol": "uec",
                "enabled": True,
                "mode": "paper",
                "setup_type": "breakout_retest",
                "setup_role": "entry_and_management",
                "direction": "long",
                "daily_close_above": 14.20,
                "retest_zone_min": 14.10,
                "retest_zone_max": 14.50,
                "entry_enabled": True,
                "entry_order_type": "stp_lmt",
                "trigger_offset": 0.02,
                "limit_offset": 0.05,
                "SL": 13.85,
                "budget": 200,
                "risque": 15,
            }
        )

        self.assertTrue(validation.valid)
        self.assertIn(
            "breakout.daily_close_above is below retest.zone_max; "
            "verify the breakout threshold and retest zone.",
            validation.warnings,
        )
        self.assertTrue(validation.details["canonical_mapped_fields"])
        semantic = validation.details["semantic_validation"]
        self.assertFalse(semantic["error_count"])
        self.assertEqual(semantic["warning_count"], 1)
        self.assertEqual(
            semantic["schema_files"],
            ["setup.base.schema.json", "setup.breakout_retest.schema.json"],
        )


if __name__ == "__main__":
    unittest.main()
