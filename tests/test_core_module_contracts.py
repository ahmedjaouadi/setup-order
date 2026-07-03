from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from app.engine.setup_engine import SetupEngine
from app.models import EventLevel, SetupStatus
from app.setups.momentum_breakout import MomentumBreakoutSetup
from app.storage.database import Database
from app.storage.event_store import EventStore
from app.storage.repositories import TradingRepository
from tests.test_setups import valid_momentum_config


class CoreModuleContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.database = Database(root / "state.sqlite")
        self.database.initialize()
        self.repository = TradingRepository(self.database)
        self.event_store = EventStore(self.repository)
        self.setup_engine = SetupEngine(
            repository=self.repository,
            event_store=self.event_store,
            setups_folder=root / "setups",
        )

    def tearDown(self) -> None:
        self.database.close()
        self.tmp.cleanup()

    def test_setup_engine_arms_valid_saved_setup(self) -> None:
        setup = MomentumBreakoutSetup(valid_momentum_config())
        self.repository.upsert_setup(setup.to_record(SetupStatus.DISABLED))

        validation = self.setup_engine.arm_setup(setup.setup_id)

        self.assertTrue(validation.valid)
        saved = self.repository.get_setup(setup.setup_id)
        self.assertIsNotNone(saved)
        self.assertEqual(saved["status"], SetupStatus.WAITING_ACTIVATION.value)

    def test_event_store_persists_runtime_event_and_decision_trace(self) -> None:
        self.event_store.record(
            EventLevel.INFO,
            "unit_event",
            "Unit event",
            symbol="NOK",
            data={"status": SetupStatus.WAITING_ACTIVATION},
        )
        runtime_event_id = self.event_store.record_runtime(
            "unit_runtime_event",
            aggregate_type="setup",
            aggregate_id="SETUP_1",
            symbol="NOK",
            payload={"ok": True},
        )
        trace_id = self.event_store.record_decision_trace(
            decision_type="ENTRY_REJECTED",
            final_decision="BLOCKED_BY_TEST",
            symbol="NOK",
            setup_id="SETUP_1",
            trace={
                "rules_evaluated": [
                    {"rule_id": "UNIT", "result": "FAILED"},
                ],
            },
        )

        events = self.repository.list_events(event_type="unit_event")
        runtime_events = self.repository.list_runtime_events(
            event_type="unit_runtime_event"
        )
        traces = self.repository.list_decision_traces(setup_id="SETUP_1")

        self.assertEqual(events[0]["data"]["status"], SetupStatus.WAITING_ACTIVATION.value)
        self.assertEqual(runtime_events[0]["event_id"], runtime_event_id)
        self.assertEqual(runtime_events[0]["payload"]["ok"], True)
        self.assertEqual(traces[0]["trace_id"], trace_id)
        self.assertEqual(traces[0]["final_decision"], "BLOCKED_BY_TEST")


if __name__ == "__main__":
    unittest.main()
