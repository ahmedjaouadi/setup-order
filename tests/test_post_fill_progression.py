from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from app.engine.post_fill_progression import PostFillProgression
from app.models import OrderRecord, OrderStatus, OrderType
from app.setups.breakout_retest import BreakoutRetestSetup
from app.storage.database import Database
from app.storage.event_store import EventStore
from app.storage.repositories import TradingRepository
from tests.test_setups import valid_breakout_config


class PostFillProgressionTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.database = Database(Path(self.tmp.name) / "state.sqlite")
        self.database.initialize()
        self.repository = TradingRepository(self.database)
        self.event_store = EventStore(self.repository)
        self.progression = PostFillProgression(self.repository, self.event_store)
        self.config = valid_breakout_config()
        setup = BreakoutRetestSetup(self.config)
        self.repository.upsert_setup(setup.to_record())
        self.order = OrderRecord(
            id="ord-1",
            setup_id=self.config["setup_id"],
            symbol=self.config["symbol"],
            side="BUY",
            order_type=OrderType.STP_LMT.value,
            quantity=10,
            status=OrderStatus.SUBMITTED.value,
            broker_order_id="broker-1",
        )
        self.repository.upsert_order(self.order)

    async def asyncTearDown(self) -> None:
        self.database.close()
        self.tmp.cleanup()

    def _add_active_stop_order(self) -> None:
        self.repository.upsert_order(
            OrderRecord(
                id="stp-1",
                setup_id=self.config["setup_id"],
                symbol=self.config["symbol"],
                side="SELL",
                order_type=OrderType.STP.value,
                quantity=10,
                status=OrderStatus.SUBMITTED.value,
                stop_price=13.85,
                parent_id=self.order.id,
            )
        )

    def test_record_fill_with_active_stop_reaches_in_position(self) -> None:
        self._add_active_stop_order()

        position = self.progression.record_fill(
            order_id=self.order.id,
            setup_id=self.config["setup_id"],
            quantity=10,
            fill_price=14.44,
            symbol=self.config["symbol"],
        )

        self.assertIsNotNone(position)
        self.assertEqual(position.current_stop, 13.85)
        self.assertEqual(
            self.repository.get_order(self.order.id)["status"], OrderStatus.FILLED.value
        )
        self.assertEqual(
            self.repository.get_setup(self.config["setup_id"])["status"], "ENTRY_FILLED"
        )
        event_types = {event["event_type"] for event in self.repository.list_events(limit=5)}
        self.assertIn("entry_filled", event_types)

        protection_verified = self.progression.has_active_protection(self.config["setup_id"])
        self.assertTrue(protection_verified)

        self.progression.mark_in_position(
            self.config["setup_id"], protection_verified=protection_verified
        )
        self.assertEqual(
            self.repository.get_setup(self.config["setup_id"])["status"], "IN_POSITION"
        )

    def test_record_fill_without_initial_stop_requires_manual_review(self) -> None:
        config = valid_breakout_config()
        config["setup_id"] = "UEC_2026_002"
        config["trailing_stop_loss"] = dict(config["trailing_stop_loss"])
        config["trailing_stop_loss"].pop("initial_stop", None)
        setup = BreakoutRetestSetup(config)
        self.repository.upsert_setup(setup.to_record())
        order = OrderRecord(
            id="ord-2",
            setup_id=config["setup_id"],
            symbol=config["symbol"],
            side="BUY",
            order_type=OrderType.STP_LMT.value,
            quantity=10,
            status=OrderStatus.SUBMITTED.value,
            broker_order_id="broker-2",
        )
        self.repository.upsert_order(order)

        position = self.progression.record_fill(
            order_id=order.id,
            setup_id=config["setup_id"],
            quantity=10,
            fill_price=14.44,
            symbol=config["symbol"],
        )

        self.assertIsNone(position)
        updated = self.repository.get_setup(config["setup_id"])
        self.assertEqual(updated["status"], "ERROR_REQUIRES_MANUAL_REVIEW")
        event_types = {event["event_type"] for event in self.repository.list_events(limit=5)}
        self.assertIn("entry_fill_missing_trailing_stop", event_types)

    def test_mark_in_position_without_verified_protection_does_not_write(self) -> None:
        self.progression.record_fill(
            order_id=self.order.id,
            setup_id=self.config["setup_id"],
            quantity=10,
            fill_price=14.44,
            symbol=self.config["symbol"],
        )

        protection_verified = self.progression.has_active_protection(self.config["setup_id"])
        self.assertFalse(protection_verified)

        self.progression.mark_in_position(self.config["setup_id"], protection_verified=False)

        self.assertEqual(
            self.repository.get_setup(self.config["setup_id"])["status"], "ENTRY_FILLED"
        )


if __name__ == "__main__":
    unittest.main()
