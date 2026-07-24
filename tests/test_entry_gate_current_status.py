from __future__ import annotations

import tempfile
import unittest
from copy import deepcopy
from datetime import datetime
from pathlib import Path

from app.broker.tws_connector import SimulatedBrokerConnector
from app.engine.broker_reality import REPORT_STATE_KEY, build_broker_reality_report
from app.engine.entry_order_executor import EntryOrderExecutor
from app.engine.order_manager import OrderManager
from app.engine.risk_engine import RiskEngine, RiskLimits
from app.engine.trading_engine import TradingEngine
from app.models import (
    ENTRY_ELIGIBLE_STATUSES,
    MarketSnapshot,
    SetupRecord,
    SetupSignal,
    SetupStatus,
    SetupType,
    SignalAction,
)
from app.settings import DEFAULT_CONFIG, Settings
from app.storage.database import Database
from app.storage.event_store import EventStore
from app.storage.repositories import TradingRepository
from tests.test_setups import valid_breakout_config

# Statuses reachable once an entry order has already been submitted or the
# setup is already managing/exiting a position: an ENTRY_READY signal must
# never place another entry order for these.
POST_ENTRY_STATUSES = [
    SetupStatus.ENTRY_ORDER_PLACED,
    SetupStatus.ENTRY_PARTIALLY_FILLED,
    SetupStatus.ENTRY_FILLED,
    SetupStatus.STOP_ORDER_PLACED,
    SetupStatus.STOP_PLACED,
    SetupStatus.RECONCILING_EXISTING_POSITION,
    SetupStatus.IN_POSITION,
    SetupStatus.MANAGING_POSITION,
    SetupStatus.PARTIAL_EXIT,
]

ENTRY_CAPABLE_SETUP_TYPES = [
    SetupType.AGGRESSIVE_REBOUND,
    SetupType.BREAKOUT_RETEST,
    SetupType.PULLBACK_CONTINUATION,
    SetupType.MOMENTUM_BREAKOUT,
    SetupType.RANGE_BREAKOUT,
]


def _config_for(setup_id: str, symbol: str, setup_type: str) -> dict:
    config = deepcopy(valid_breakout_config())
    config["setup_id"] = setup_id
    config["symbol"] = symbol
    config["setup_type"] = setup_type
    return config


def _setup_record(
    setup_id: str,
    symbol: str,
    setup_type: str,
    status: SetupStatus,
) -> SetupRecord:
    return SetupRecord(
        setup_id=setup_id,
        symbol=symbol,
        setup_type=setup_type,
        enabled=True,
        mode="paper",
        status=status.value,
        entry_zone="",
        stop_loss=13.85,
        risk_amount=15.0,
        order_status="",
        position_status="",
        last_event="test fixture",
        config=_config_for(setup_id, symbol, setup_type),
    )


def _entry_ready_signal() -> SetupSignal:
    return SetupSignal(
        action=SignalAction.ENTRY_READY,
        reason="Test entry",
        entry_price=14.44,
        stop_loss=13.85,
    )


class _EngineHarness:
    """One isolated TradingEngine + database, so cross-setup broker-reality
    reconciliation of one test's fixtures never leaks into another's."""

    def __init__(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        raw_config = deepcopy(DEFAULT_CONFIG)
        raw_config["storage"]["database_file"] = str(root / "state.sqlite")
        raw_config["storage"]["setups_folder"] = str(root / "setups")
        raw_config["storage"]["logs_folder"] = str(root / "logs")
        # Session/hour policy depends on wall-clock time; disable it so the
        # gate tests are deterministic regardless of when they run.
        raw_config["session_policy"]["enabled"] = False
        self.settings = Settings.from_dict(raw_config)
        self.database = Database(self.settings.database_file)
        self.database.initialize()
        self.repository = TradingRepository(self.database)
        self.broker = SimulatedBrokerConnector()
        self.engine = TradingEngine(self.settings, self.repository, broker=self.broker)

    async def start(self) -> None:
        await self.broker.connect()

    async def close(self) -> None:
        await self.broker.disconnect()
        self.database.close()
        self.tmp.cleanup()

    def seed_market_data(self, symbol: str, price: float = 14.44) -> None:
        # Mirrors production ticks so lifecycle revalidation never falls back
        # to MISSING_MARKET_DATA, whose outcome would otherwise depend on the
        # wall-clock session (see audit/22_diag_lot1.md).
        self.engine.market_data.update(MarketSnapshot(symbol=symbol, price=price, close=price))

    def seed_broker_reality(self) -> None:
        self.repository.set_bot_state(
            REPORT_STATE_KEY,
            build_broker_reality_report(
                local_setups=self.repository.list_setups(),
                local_orders=[],
                local_positions=[],
                broker_orders=[],
                broker_positions=[],
                broker_connected=True,
                settings=self.settings.raw,
            ),
        )


class EntryGateTradingEngineTests(unittest.IsolatedAsyncioTestCase):
    """Exercises the gate through TradingEngine._handle_signal (trading_engine.py:2465)."""

    async def asyncSetUp(self) -> None:
        self.harness = _EngineHarness()
        await self.harness.start()
        self.repository = self.harness.repository
        self.engine = self.harness.engine

    async def asyncTearDown(self) -> None:
        await self.harness.close()

    def _seed_broker_reality(self) -> None:
        self.harness.seed_broker_reality()

    async def test_nominal_entry_transmitted_for_each_setup_type(self) -> None:
        """Proof 1: the gate is transparent for the normal flow, for all 5 entry-capable types."""
        for setup_type in ENTRY_CAPABLE_SETUP_TYPES:
            with self.subTest(setup_type=setup_type.value):
                harness = _EngineHarness()
                await harness.start()
                try:
                    setup_id = f"{setup_type.value.upper()}_2026_001"
                    symbol = setup_type.value.upper()[:6]
                    record = _setup_record(
                        setup_id, symbol, setup_type.value, SetupStatus.WAITING_ENTRY_SIGNAL
                    )
                    harness.repository.upsert_setup(record)
                    harness.seed_broker_reality()
                    harness.seed_market_data(symbol)

                    setup = harness.repository.get_setup(setup_id)
                    self.assertIsNotNone(setup)
                    await harness.engine._handle_signal(
                        setup, SetupStatus.WAITING_ENTRY_SIGNAL, _entry_ready_signal()
                    )

                    updated = harness.repository.get_setup(setup_id)
                    self.assertEqual(updated["status"], "ENTRY_ORDER_PLACED")
                    orders = harness.repository.list_orders(setup_id)
                    self.assertEqual(len(orders), 2)
                finally:
                    await harness.close()

    async def test_entry_ready_blocked_for_each_post_entry_status(self) -> None:
        """Proof 2: ENTRY_READY on any post-entry status places no order and emits entry_gate_blocked, for all 5 entry-capable types."""
        for setup_type in ENTRY_CAPABLE_SETUP_TYPES:
            for status in POST_ENTRY_STATUSES:
                with self.subTest(setup_type=setup_type.value, status=status.value):
                    setup_id = f"{setup_type.value.upper()}_{status.value}_001"
                    symbol = setup_type.value.upper()[:6]
                    record = _setup_record(setup_id, symbol, setup_type.value, status)
                    self.repository.upsert_setup(record)
                    self._seed_broker_reality()

                    setup = self.repository.get_setup(setup_id)
                    self.assertIsNotNone(setup)
                    await self.engine._handle_signal(setup, status, _entry_ready_signal())

                    self.assertEqual(self.repository.list_orders(setup_id), [])
                    events = self.repository.list_events(limit=1, setup_id=setup_id)
                    self.assertEqual(events[0]["event_type"], "entry_gate_blocked")
                    self.assertEqual(events[0]["data"]["current_status"], status.value)

    async def test_whitelist_blocks_hypothetical_status_by_default(self) -> None:
        """Proof 4: a status that is neither pre-entry-eligible nor post-entry is blocked by default (whitelist, not blacklist)."""
        hypothetical_status = SetupStatus.STALE_SETUP
        self.assertNotIn(hypothetical_status, ENTRY_ELIGIBLE_STATUSES)
        setup_id = "HYPOTHETICAL_STATUS_001"
        record = _setup_record(setup_id, "HYPO", "range_breakout", hypothetical_status)
        self.repository.upsert_setup(record)
        self._seed_broker_reality()

        setup = self.repository.get_setup(setup_id)
        self.assertIsNotNone(setup)
        await self.engine._handle_signal(setup, hypothetical_status, _entry_ready_signal())

        self.assertEqual(self.repository.list_orders(setup_id), [])
        events = self.repository.list_events(limit=1, setup_id=setup_id)
        self.assertEqual(events[0]["event_type"], "entry_gate_blocked")

    async def test_replay_2026_06_29_incident_no_second_order(self) -> None:
        """Proof 5 (central): replays the 2026-06-29 incident.

        A range_breakout setup whose entry order is already FILLED at the
        broker while the setup status is still stuck at ENTRY_ORDER_PLACED
        (the local positions table has not caught up yet -- exactly the gap
        that let a real second BUY order through on 2026-06-29). A fresh
        ENTRY_READY signal must not place a second order.
        """
        setup_id = "RANGE_BREAKOUT_INCIDENT_001"
        record = _setup_record(
            setup_id, "RBRK", "range_breakout", SetupStatus.WAITING_ENTRY_SIGNAL
        )
        self.repository.upsert_setup(record)
        self._seed_broker_reality()
        self.harness.seed_market_data("RBRK")

        setup = self.repository.get_setup(setup_id)
        await self.engine._handle_signal(
            setup, SetupStatus.WAITING_ENTRY_SIGNAL, _entry_ready_signal()
        )
        first_orders = self.repository.list_orders(setup_id)
        self.assertEqual(len(first_orders), 2)
        entry_order = next(order for order in first_orders if order["side"] == "BUY")

        # The broker fills the entry, but the setup's own status field and
        # the local positions table have not been reconciled yet.
        self.repository.update_order_status(entry_order["id"], "FILLED")
        stuck_setup = self.repository.get_setup(setup_id)
        self.assertEqual(stuck_setup["status"], "ENTRY_ORDER_PLACED")

        await self.engine._handle_signal(
            stuck_setup, SetupStatus.ENTRY_ORDER_PLACED, _entry_ready_signal()
        )

        second_orders = self.repository.list_orders(setup_id)
        self.assertEqual(len(second_orders), 2, "a second BUY order must not be transmitted")
        events = self.repository.list_events(limit=1, setup_id=setup_id)
        self.assertEqual(events[0]["event_type"], "entry_gate_blocked")
        self.assertEqual(events[0]["data"]["current_status"], "ENTRY_ORDER_PLACED")


class EntryOrderExecutorDefenseInDepthTests(unittest.IsolatedAsyncioTestCase):
    """Proof 3: execute_entry_ready itself refuses a post-entry current_status, independent of the caller."""

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
        self.repository.upsert_setup(
            _setup_record(
                self.config["setup_id"],
                self.config["symbol"],
                "breakout_retest",
                SetupStatus.ENTRY_ORDER_PLACED,
            )
        )

    async def asyncTearDown(self) -> None:
        await self.broker.disconnect()
        self.database.close()
        self.tmp.cleanup()

    async def test_direct_call_with_post_entry_status_returns_true_without_broker_call(
        self,
    ) -> None:
        setup = self.repository.get_setup(self.config["setup_id"])
        self.assertIsNotNone(setup)
        signal = _entry_ready_signal()

        handled = await self.executor.execute_entry_ready(
            setup, signal, SetupStatus.ENTRY_ORDER_PLACED
        )

        self.assertTrue(handled)
        self.assertEqual(self.repository.list_orders(), [])


if __name__ == "__main__":
    unittest.main()
