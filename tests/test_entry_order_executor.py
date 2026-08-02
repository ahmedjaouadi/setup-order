from __future__ import annotations

import tempfile
import unittest
from copy import deepcopy
from datetime import datetime
from pathlib import Path

from app.broker.ib_models import BrokerPosition
from app.broker.tws_connector import SimulatedBrokerConnector
from app.engine.broker_reality import REPORT_STATE_KEY, build_broker_reality_report
from app.engine.entry_order_executor import EntryOrderExecutor
from app.engine.order_manager import OrderManager
from app.engine.risk_engine import RiskEngine, RiskLimits
from app.models import PositionRecord, SetupSignal, SetupStatus, SignalAction, utc_now_iso
from app.settings import DEFAULT_CONFIG
from app.setups.breakout_retest import BreakoutRetestSetup
from app.storage.database import Database
from app.storage.event_store import EventStore
from app.storage.repositories import TradingRepository
from tests.test_setups import valid_breakout_config


class EntryOrderExecutorTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.database = Database(Path(self.tmp.name) / "state.sqlite")
        self.database.initialize()
        self.repository = TradingRepository(self.database)
        self.event_store = EventStore(self.repository)
        self.broker = SimulatedBrokerConnector()
        await self.broker.connect()
        self.order_manager = OrderManager(
            repository=self.repository,
            event_store=self.event_store,
            broker=self.broker,
        )
        self.executor = EntryOrderExecutor(
            repository=self.repository,
            event_store=self.event_store,
            risk_engine=RiskEngine(RiskLimits.from_config(DEFAULT_CONFIG)),
            order_manager=self.order_manager,
            settings=DEFAULT_CONFIG,
            current_time_provider=lambda: datetime.fromisoformat("2026-06-29T10:45:00-04:00"),
        )
        self.config = valid_breakout_config()
        setup = BreakoutRetestSetup(self.config)
        self.repository.upsert_setup(setup.to_record())
        self.repository.set_bot_state(
            REPORT_STATE_KEY,
            build_broker_reality_report(
                local_setups=self.repository.list_setups(),
                local_orders=[],
                local_positions=[],
                broker_orders=[],
                broker_positions=[],
                broker_connected=True,
                settings=DEFAULT_CONFIG,
            ),
        )

    async def asyncTearDown(self) -> None:
        await self.broker.disconnect()
        self.database.close()
        self.tmp.cleanup()

    async def test_entry_ready_places_entry_order(self) -> None:
        setup = self.repository.get_setup(self.config["setup_id"])
        self.assertIsNotNone(setup)
        signal = SetupSignal(
            action=SignalAction.ENTRY_READY,
            reason="Retest confirmed",
            entry_price=14.44,
            stop_loss=13.85,
        )

        handled = await self.executor.execute_entry_ready(
            setup, signal, SetupStatus(setup["status"])
        )

        self.assertTrue(handled)
        updated = self.repository.get_setup(self.config["setup_id"])
        self.assertEqual(updated["status"], "ENTRY_ORDER_PLACED")
        orders = self.repository.list_orders()
        self.assertEqual(len(orders), 2)
        entry_order = next(order for order in orders if order["side"] == "BUY")
        stop_order = next(order for order in orders if order["side"] == "SELL")
        self.assertEqual(entry_order["trigger_price"], 14.44)
        self.assertEqual(entry_order["limit_price"], 14.49)
        self.assertEqual(stop_order["stop_price"], 13.85)
        self.assertEqual(stop_order["parent_id"], entry_order["id"])
        events = self.repository.list_events(limit=2)
        self.assertEqual(events[0]["event_type"], "entry_order_submitted")

    async def test_missing_entry_price_or_stop_records_rejection(self) -> None:
        setup = self.repository.get_setup(self.config["setup_id"])
        self.assertIsNotNone(setup)
        signal = SetupSignal(
            action=SignalAction.ENTRY_READY,
            reason="Incomplete signal",
            entry_price=None,
            stop_loss=13.85,
        )

        handled = await self.executor.execute_entry_ready(
            setup, signal, SetupStatus(setup["status"])
        )

        self.assertTrue(handled)
        self.assertEqual(self.repository.list_orders(), [])
        events = self.repository.list_events(limit=1)
        self.assertEqual(events[0]["event_type"], "entry_signal_rejected")

    async def test_auto_off_entry_ready_records_signal_without_order(self) -> None:
        self.repository.set_setup_enabled(self.config["setup_id"], False)
        setup = self.repository.get_setup(self.config["setup_id"])
        self.assertIsNotNone(setup)
        signal = SetupSignal(
            action=SignalAction.ENTRY_READY,
            reason="Retest confirmed",
            entry_price=14.44,
            stop_loss=13.85,
        )

        handled = await self.executor.execute_entry_ready(
            setup, signal, SetupStatus(setup["status"])
        )

        self.assertTrue(handled)
        self.assertEqual(self.repository.list_orders(), [])
        updated = self.repository.get_setup(self.config["setup_id"])
        self.assertEqual(updated["status"], setup["status"])
        events = self.repository.list_events(limit=1)
        self.assertEqual(events[0]["event_type"], "entry_auto_execution_disabled")

    async def test_risk_rejection_does_not_place_order(self) -> None:
        config = valid_breakout_config()
        config["trailing_stop_loss"]["initial_stop"] = 15.00
        setup_record = BreakoutRetestSetup(config).to_record()
        self.repository.upsert_setup(setup_record)
        setup = self.repository.get_setup(self.config["setup_id"])
        self.assertIsNotNone(setup)
        signal = SetupSignal(
            action=SignalAction.ENTRY_READY,
            reason="Invalid risk",
            entry_price=14.44,
            stop_loss=15.00,
        )

        handled = await self.executor.execute_entry_ready(
            setup, signal, SetupStatus(setup["status"])
        )

        self.assertTrue(handled)
        self.assertEqual(self.repository.list_orders(), [])
        events = self.repository.list_events(limit=1)
        self.assertEqual(events[0]["event_type"], "entry_rejected_by_risk")

    async def test_trailing_stop_initial_not_ready_blocks_order_submission(self) -> None:
        config = valid_breakout_config()
        config["trailing_stop_loss"] = {
            "enabled": True,
            "mode": "AUTO_INTELLIGENT",
            "initial_stop": None,
        }
        setup_record = BreakoutRetestSetup(config).to_record()
        self.repository.upsert_setup(setup_record)
        setup = self.repository.get_setup(self.config["setup_id"])
        self.assertIsNotNone(setup)
        signal = SetupSignal(
            action=SignalAction.ENTRY_READY,
            reason="Retest confirmed",
            entry_price=14.44,
            stop_loss=None,
        )

        handled = await self.executor.execute_entry_ready(
            setup, signal, SetupStatus(setup["status"])
        )

        self.assertTrue(handled)
        self.assertEqual(self.repository.list_orders(), [])
        events = self.repository.list_events(limit=1)
        self.assertEqual(events[0]["event_type"], "entry_signal_rejected")
        self.assertEqual(
            events[0]["data"]["entry_decision"]["status"],
            "BLOCKED_TRAILING_STOP_NOT_READY",
        )

    async def test_missing_broker_tracker_blocks_order_submission(self) -> None:
        self.repository.set_bot_state(REPORT_STATE_KEY, {})
        setup = self.repository.get_setup(self.config["setup_id"])
        self.assertIsNotNone(setup)
        signal = SetupSignal(
            action=SignalAction.ENTRY_READY,
            reason="Retest confirmed",
            entry_price=14.44,
            stop_loss=13.85,
        )

        handled = await self.executor.execute_entry_ready(
            setup, signal, SetupStatus(setup["status"])
        )

        self.assertTrue(handled)
        self.assertEqual(self.repository.list_orders(), [])
        events = self.repository.list_events(limit=1)
        self.assertEqual(events[0]["event_type"], "entry_blocked_by_broker_reality")
        self.assertEqual(
            events[0]["data"]["blocking_reasons"],
            ["BROKER_TRACKER_NOT_RUNNING"],
        )

    async def test_session_policy_blocks_entry_even_if_action_is_entry_ready(self) -> None:
        setup = self.repository.get_setup(self.config["setup_id"])
        self.assertIsNotNone(setup)
        signal = SetupSignal(
            action=SignalAction.ENTRY_READY,
            reason="Premarket trigger detected",
            entry_price=14.44,
            stop_loss=13.85,
            metadata={
                "analysis": {
                    "decision_status": "PREMARKET_TRIGGER_DETECTED",
                    "display_message": "Le trigger a ete touche avant l'ouverture.",
                    "blocking_conditions": [
                        "BLOCKED_OUTSIDE_REGULAR_MARKET_HOURS",
                    ],
                    "next_action": "PENDING_RTH_CONFIRMATION",
                }
            },
        )

        handled = await self.executor.execute_entry_ready(
            setup, signal, SetupStatus(setup["status"])
        )

        self.assertTrue(handled)
        self.assertEqual(self.repository.list_orders(), [])
        events = self.repository.list_events(limit=1)
        self.assertEqual(events[0]["event_type"], "entry_blocked_by_session_policy")

    async def test_execution_window_blocks_premarket_entry_even_without_signal_metadata(
        self,
    ) -> None:
        executor = EntryOrderExecutor(
            repository=self.repository,
            event_store=self.event_store,
            risk_engine=RiskEngine(RiskLimits.from_config(DEFAULT_CONFIG)),
            order_manager=self.order_manager,
            settings=DEFAULT_CONFIG,
            current_time_provider=lambda: datetime.fromisoformat("2026-06-29T08:01:00-04:00"),
        )
        setup = self.repository.get_setup(self.config["setup_id"])
        self.assertIsNotNone(setup)
        signal = SetupSignal(
            action=SignalAction.ENTRY_READY,
            reason="Breakout confirmed",
            entry_price=14.44,
            stop_loss=13.85,
        )

        handled = await executor.execute_entry_ready(
            setup, signal, SetupStatus(setup["status"])
        )

        self.assertTrue(handled)
        self.assertEqual(self.repository.list_orders(), [])
        events = self.repository.list_events(limit=1)
        self.assertEqual(events[0]["event_type"], "entry_blocked_by_execution_session_window")

    def _entry_signal(self) -> SetupSignal:
        return SetupSignal(
            action=SignalAction.ENTRY_READY,
            reason="Retest confirmed",
            entry_price=14.44,
            stop_loss=13.85,
        )

    def _store_broker_report(self, positions: list[BrokerPosition], **overrides) -> None:
        self.repository.set_bot_state(
            REPORT_STATE_KEY,
            build_broker_reality_report(
                local_setups=self.repository.list_setups(),
                local_orders=[],
                local_positions=[],
                broker_orders=[],
                broker_positions=positions,
                broker_connected=True,
                settings=DEFAULT_CONFIG,
                **overrides,
            ),
        )

    async def test_account_wide_position_count_blocks_earlier_than_local(self) -> None:
        # Root D, D-1, PREUVE 1 applied to the risk_engine gate: the account
        # has more open positions than risk.max_open_positions (5) while the
        # local view has none -- the account-wide count must refuse the entry
        # that a local-only count would have approved.
        self._store_broker_report(
            [
                BrokerPosition(symbol=f"P{i}", quantity=1, average_price=1.0, current_price=1.0)
                for i in range(6)
            ]
        )
        setup = self.repository.get_setup(self.config["setup_id"])
        handled = await self.executor.execute_entry_ready(
            setup, self._entry_signal(), SetupStatus(setup["status"])
        )

        self.assertTrue(handled)
        self.assertEqual(self.repository.list_orders(), [])
        events = self.repository.list_events(limit=1)
        self.assertEqual(events[0]["event_type"], "entry_rejected_by_risk")
        self.assertIn("Maximum number of open positions reached", events[0]["message"])

    async def test_account_wide_capital_blocks_earlier_than_local(self) -> None:
        # Root D, D-1, PREUVE 3: the account-wide capital (average_price *
        # quantity summed over broker_reality rows) exceeds
        # risk.max_total_exposure_usd (1000) while the local view is empty.
        self._store_broker_report(
            [BrokerPosition(symbol="ZZZZ", quantity=100, average_price=50.0, current_price=50.0)]
        )
        setup = self.repository.get_setup(self.config["setup_id"])
        handled = await self.executor.execute_entry_ready(
            setup, self._entry_signal(), SetupStatus(setup["status"])
        )

        self.assertTrue(handled)
        self.assertEqual(self.repository.list_orders(), [])
        events = self.repository.list_events(limit=1)
        self.assertEqual(events[0]["event_type"], "entry_rejected_by_risk")
        self.assertIn("Maximum exposure reached", events[0]["message"])

    async def test_no_double_counting_same_position(self) -> None:
        # Root D, D-1, PREUVE 2 (Q4, the central test) applied to the
        # risk_engine gate: local shows 1 position, the account-wide report
        # shows the SAME 1 position (not 2) -- max(1, 1) = 1 must stay under
        # a threshold of 2, an addition (1 + 1 = 2) would wrongly block.
        settings = deepcopy(DEFAULT_CONFIG)
        settings["risk"]["max_open_positions"] = 2
        # The pre-existing "ZZZZ" position/broker position are unrelated to
        # any locally tracked setup on purpose (simulating broker-side
        # truth this instance does not manage) -- disable the two safety
        # gates that would otherwise flag it as an unprotected orphan
        # position, since this test isolates the risk_engine exposure gate.
        settings["execution_safety"]["block_new_entries_if_reconciliation_mismatch"] = False
        settings["execution_safety"]["block_new_entries_if_position_without_stop_exists"] = False
        executor = EntryOrderExecutor(
            repository=self.repository,
            event_store=self.event_store,
            risk_engine=RiskEngine(RiskLimits.from_config(settings)),
            order_manager=self.order_manager,
            settings=settings,
            current_time_provider=lambda: datetime.fromisoformat("2026-06-29T10:45:00-04:00"),
        )
        self.repository.upsert_position(
            PositionRecord(
                symbol="ZZZZ",
                setup_id="ZZZZ_setup",
                quantity=10,
                average_price=20.0,
                current_price=20.0,
                unrealized_pnl=0.0,
                current_stop=19.0,
                risk_remaining=10.0,
                status="OPEN",
                updated_at=utc_now_iso(),
            )
        )
        self._store_broker_report(
            [BrokerPosition(symbol="ZZZZ", quantity=10, average_price=20.0, current_price=20.0)]
        )
        setup = self.repository.get_setup(self.config["setup_id"])

        handled = await executor.execute_entry_ready(
            setup, self._entry_signal(), SetupStatus(setup["status"])
        )

        self.assertTrue(handled)
        self.assertEqual(len(self.repository.list_orders()), 2)

    async def test_absent_report_falls_back_to_local_and_traces_event(self) -> None:
        # Root D, D-1, PREUVE 4: report absent -> behaviour identical to
        # before D-1 (order still placed since the local view is empty), and
        # a traceability event is recorded for the fallback. The broker
        # tracker's own missing/stale block is disabled here so the fallback
        # event can be observed without being shadowed by that later gate.
        settings = deepcopy(DEFAULT_CONFIG)
        settings["broker_tracker"]["block_auto_execution_if_missing"] = False
        settings["execution_safety"]["block_new_entries_if_broker_tracker_stale"] = False
        executor = EntryOrderExecutor(
            repository=self.repository,
            event_store=self.event_store,
            risk_engine=RiskEngine(RiskLimits.from_config(settings)),
            order_manager=self.order_manager,
            settings=settings,
            current_time_provider=lambda: datetime.fromisoformat("2026-06-29T10:45:00-04:00"),
        )
        self.repository.set_bot_state(REPORT_STATE_KEY, {})
        setup = self.repository.get_setup(self.config["setup_id"])

        handled = await executor.execute_entry_ready(
            setup, self._entry_signal(), SetupStatus(setup["status"])
        )

        self.assertTrue(handled)
        self.assertEqual(len(self.repository.list_orders()), 2)
        events = self.repository.list_events(event_type="exposure_cap_local_fallback")
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["data"]["reason"], "BROKER_REALITY_REPORT_ABSENT")


if __name__ == "__main__":
    unittest.main()
