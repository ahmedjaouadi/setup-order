from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest

from app.engine.setup_engine import SetupEngine
from app.engine.setup_status_reporter import SetupStatusReporter
from app.models import EventLevel, SignalAction
from app.settings import DEFAULT_CONFIG, Settings
from app.setups.breakout_retest import BreakoutRetestSetup
from app.storage.database import Database
from app.storage.event_store import EventStore
from app.storage.repositories import TradingRepository
from tests.test_setups import valid_breakout_config


class SetupStatusReporterTests(unittest.TestCase):
    def setUp(self) -> None:
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
        self.event_store = EventStore(self.repository)
        self.setup_engine = SetupEngine(
            repository=self.repository,
            event_store=self.event_store,
            setups_folder=self.settings.setups_folder,
        )
        self.broker = SimpleNamespace(
            connector_name="simulated",
            account_mode="paper",
        )
        self.reporter = SetupStatusReporter(
            settings=self.settings,
            repository=self.repository,
            setup_engine=self.setup_engine,
            broker_provider=lambda: self.broker,
        )

    def tearDown(self) -> None:
        self.database.close()
        self.tmp.cleanup()

    def test_reports_watched_scenario_without_trading_engine(self) -> None:
        setup = BreakoutRetestSetup(valid_breakout_config())
        self.repository.upsert_setup(setup.to_record())

        status = self.reporter.configuration_status()

        self.assertEqual(status["active_configuration"]["loaded_setup_count"], 1)
        self.assertEqual(status["active_configuration"]["enabled_setup_count"], 1)
        self.assertEqual(status["active_configuration"]["watched_setup_count"], 1)
        self.assertEqual(
            status["active_configuration"]["broker_connector"],
            "simulated",
        )
        scenario = status["current_scenario"]
        self.assertEqual(scenario["setup_id"], setup.setup_id)
        self.assertTrue(scenario["armed"])
        self.assertEqual(scenario["armed_state"], "ARMED_WAITING")
        self.assertEqual(scenario["missing_required_parameters"], [])
        self.assertIn("breakout", scenario["awaited_condition"].lower())

    def test_auto_off_scenario_is_still_watched_but_not_armed(self) -> None:
        setup = BreakoutRetestSetup(valid_breakout_config())
        self.repository.upsert_setup(setup.to_record())
        self.repository.set_setup_enabled(setup.setup_id, False)

        status = self.reporter.configuration_status()

        self.assertEqual(status["active_configuration"]["watched_setup_count"], 1)
        self.assertEqual(status["active_configuration"]["enabled_setup_count"], 0)
        self.assertEqual(
            status["active_configuration"]["auto_execution_enabled_count"],
            0,
        )
        scenario = status["current_scenario"]
        self.assertEqual(scenario["setup_id"], setup.setup_id)
        self.assertFalse(scenario["enabled"])
        self.assertFalse(scenario["auto_execution_enabled"])
        self.assertTrue(scenario["watched"])
        self.assertFalse(scenario["armed"])
        self.assertEqual(scenario["armed_state"], "WATCH_ONLY")
        self.assertIn("Surveillance uniquement", scenario["expected_action"])

    def test_uses_latest_analysis_for_conditions_and_actions(self) -> None:
        setup = BreakoutRetestSetup(valid_breakout_config())
        self.repository.upsert_setup(setup.to_record())
        self.event_store.record(
            EventLevel.INFO,
            "stock_analysis",
            "UEC analysis",
            setup_id=setup.setup_id,
            symbol=setup.symbol,
            data={
                "processed": [
                    {
                        "setup_id": setup.setup_id,
                        "action": SignalAction.ENTRY_READY.value,
                        "reason": "Retest confirmed",
                        "metadata": {
                            "analysis": {
                                "blocking_conditions": ["spread too wide"],
                            },
                        },
                        "trace": {
                            "checks": [],
                            "next_step": "fallback should not win",
                        },
                    }
                ],
            },
        )

        status = self.reporter.configuration_status()

        scenario = status["current_scenario"]
        self.assertEqual(
            scenario["awaited_condition"],
            "Conditions bloquantes: spread too wide",
        )
        self.assertEqual(
            scenario["expected_action"],
            "Verifier le risque puis envoyer un bracket protege (entree + stop).",
        )
        self.assertEqual(scenario["latest_analysis_action"], "ENTRY_READY")
        self.assertEqual(scenario["latest_analysis_reason"], "Retest confirmed")


if __name__ == "__main__":
    unittest.main()
