from __future__ import annotations

import tempfile
import unittest
from copy import deepcopy
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

from app.engine.setup_lifecycle_service import (
    SetupLifecycleService,
    revalidate_setup,
)
from app.models import MarketSnapshot, SetupStatus, utc_now_iso
from app.storage.database import Database
from app.storage.event_store import EventStore
from app.storage.repositories import TradingRepository

BROKER_OK = {
    "broker_connected": True,
    "broker_tracker_status": "OK",
    "broker_sync_status": "OK",
    "remaining_risk_status": "OK",
    "auto_execution_blocked": False,
    "blocking_reasons": [],
}

# Deterministic clocks so tests do not depend on when they run: revalidation
# behaves differently when the US market is open vs closed.
RTH_NOW = datetime(2026, 7, 6, 14, 0, tzinfo=UTC)  # Monday 10:00 ET -> regular session
MARKET_CLOSED_NOW = datetime(2026, 7, 4, 14, 0, tzinfo=UTC)  # Saturday -> market closed


def lifecycle_config(**overrides) -> dict:
    config = {
        "setup_id": "TEST_LIFECYCLE_001",
        "symbol": "TEST",
        "enabled": True,
        "mode": "paper",
        "setup_type": "momentum_breakout",
        "setup_role": "ENTRY_AND_MANAGEMENT",
        "direction": "long",
        "entry": {
            "enabled": True,
            "order_type": "STP_LMT",
            "trigger_price": 100.0,
            "entry_price": 100.0,
            "limit_price": 100.30,
            "maximum_limit_price": 100.30,
        },
        "risk": {
            "max_position_amount_usd": 1000,
            "max_risk_usd": 50,
        },
        "trailing_stop_loss": {
            "enabled": True,
            "initial_stop": 95.0,
            "current_stop": 95.0,
            "broker_order": {
                "required_before_entry_transmission": True,
                "trailing_stop_order_ready": True,
            },
        },
        "support_zone": {
            "min": 96.0,
            "max": 98.0,
            "invalidation_below": 95.0,
        },
        "anti_chase": {
            "enabled": True,
            "max_price_above_entry_percent": 1.5,
        },
    }
    for key, value in overrides.items():
        if isinstance(value, dict) and isinstance(config.get(key), dict):
            config[key] = {**config[key], **value}
        else:
            config[key] = value
    return config


def lifecycle_setup(
    status: str = SetupStatus.WAITING_ACTIVATION.value,
    config: dict | None = None,
    **overrides,
) -> dict:
    config = config if config is not None else lifecycle_config()
    setup = {
        "setup_id": config["setup_id"],
        "symbol": config["symbol"],
        "setup_type": config.get("setup_type", "momentum_breakout"),
        "enabled": True,
        "mode": "paper",
        "status": status,
        "config": config,
        "created_at": utc_now_iso(),
        "updated_at": utc_now_iso(),
    }
    setup.update(overrides)
    return setup


def market(price: float, **overrides) -> MarketSnapshot:
    values = {
        "symbol": "TEST",
        "price": price,
        "bid": price - 0.01,
        "ask": price + 0.01,
        "close": price,
    }
    values.update(overrides)
    return MarketSnapshot(**values)


class RevalidateSetupTests(unittest.TestCase):
    def test_price_too_far_above_entry_marks_stale(self) -> None:
        setup = lifecycle_setup()
        result = revalidate_setup(setup, market(105.0), BROKER_OK, None)

        self.assertIn(
            result["status"],
            {
                SetupStatus.STALE_SETUP.value,
                SetupStatus.MISSED_BREAKOUT_WAIT_RETEST.value,
            },
        )
        self.assertEqual(result["status_reason"], "PRICE_TOO_FAR_ABOVE_ENTRY")
        self.assertFalse(result["can_send_order"])
        self.assertFalse(result["can_be_armed"])
        self.assertTrue(result["last_revalidated_at"])

    def test_support_broken_marks_invalidated(self) -> None:
        setup = lifecycle_setup()
        result = revalidate_setup(setup, market(94.5), BROKER_OK, None)

        self.assertEqual(result["status"], SetupStatus.INVALIDATED.value)
        self.assertIn(
            result["status_reason"],
            {"INVALIDATION_LEVEL_BROKEN", "SUPPORT_BROKEN", "TECHNICAL_THESIS_BROKEN"},
        )
        self.assertFalse(result["can_send_order"])
        self.assertFalse(result["can_be_armed"])

    def test_missing_market_data_blocks_not_invalidates(self) -> None:
        setup = lifecycle_setup()

        for snapshot in (
            None,
            MarketSnapshot(symbol="TEST", price=0.0, bid=None, ask=None, close=None),
        ):
            result = revalidate_setup(setup, snapshot, BROKER_OK, RTH_NOW)
            self.assertEqual(result["status"], SetupStatus.BLOCKED.value)
            self.assertEqual(result["status_reason"], "MISSING_MARKET_DATA")
            self.assertNotEqual(result["status"], SetupStatus.INVALIDATED.value)
            self.assertIn("MISSING_MARKET_DATA", result["blocking_reasons"])
            self.assertFalse(result["can_send_order"])

    def test_missing_market_data_when_market_closed_preserves_status(self) -> None:
        # Weekend / market closed: no live quote is expected. The setup must keep
        # a readable waiting status instead of flipping to BLOCKED, and no entry
        # can be sent.
        setup = lifecycle_setup()
        for snapshot in (
            None,
            MarketSnapshot(symbol="TEST", price=0.0, bid=None, ask=None, close=None),
        ):
            result = revalidate_setup(setup, snapshot, BROKER_OK, MARKET_CLOSED_NOW)
            self.assertEqual(result["status"], SetupStatus.WAITING_ACTIVATION.value)
            self.assertEqual(result["status_reason"], "MARKET_CLOSED")
            self.assertNotIn("MISSING_MARKET_DATA", result["blocking_reasons"])
            self.assertFalse(result["can_send_order"])

    def test_blocked_setup_recovers_when_market_closed(self) -> None:
        # An already-BLOCKED setup must not stay stuck on BLOCKED over the
        # weekend just because live data stopped flowing.
        setup = lifecycle_setup(status=SetupStatus.BLOCKED.value)
        result = revalidate_setup(setup, None, BROKER_OK, MARKET_CLOSED_NOW)
        self.assertEqual(result["status"], SetupStatus.WAITING_ACTIVATION.value)
        self.assertEqual(result["status_reason"], "MARKET_CLOSED")

    def test_disconnected_broker_when_market_closed_does_not_block(self) -> None:
        setup = lifecycle_setup()
        broker_reality = {**BROKER_OK, "broker_connected": False}
        result = revalidate_setup(setup, market(99.0), broker_reality, MARKET_CLOSED_NOW)
        self.assertNotEqual(result["status"], SetupStatus.BLOCKED.value)
        self.assertFalse(result["can_send_order"])

    def test_waiting_activation_only_when_still_valid(self) -> None:
        setup = lifecycle_setup()
        result = revalidate_setup(setup, market(99.0), BROKER_OK, RTH_NOW)

        self.assertEqual(result["status"], SetupStatus.WAITING_ACTIVATION.value)
        self.assertEqual(result["status_reason"], "SETUP_VALID")
        self.assertEqual(result["blocking_reasons"], [])
        self.assertTrue(result["can_be_armed"])
        self.assertTrue(result["can_send_order"])

    def test_trigger_missed_with_retest_zone_marks_missed_breakout(self) -> None:
        config = lifecycle_config(
            missed_breakout={
                "retest_zone_min": 99.5,
                "retest_zone_max": 100.5,
            },
        )
        setup = lifecycle_setup(config=config)
        result = revalidate_setup(setup, market(105.0), BROKER_OK, None)

        self.assertEqual(
            result["status"],
            SetupStatus.MISSED_BREAKOUT_WAIT_RETEST.value,
        )
        self.assertEqual(result["status_reason"], "PRICE_TOO_FAR_ABOVE_ENTRY")
        self.assertFalse(result["can_send_order"])

    def test_management_only_position_missing_blocks_or_invalidates(self) -> None:
        config = lifecycle_config(
            setup_role="MANAGEMENT_ONLY",
            entry={"enabled": False},
            position_source={
                "mode": "adopt_existing_ibkr_position",
                "require_existing_position": True,
            },
        )
        setup = lifecycle_setup(
            status=SetupStatus.RECONCILING_EXISTING_POSITION.value,
            config=config,
        )
        broker_reality = {**BROKER_OK, "position_open": False}
        result = revalidate_setup(setup, market(99.0), broker_reality, None)

        self.assertIn(
            result["status"],
            {SetupStatus.BLOCKED.value, SetupStatus.INVALIDATED.value},
        )
        self.assertFalse(result["can_send_order"])

    # --- couverture complementaire ---

    def test_stop_above_entry_for_long_marks_invalidated(self) -> None:
        config = lifecycle_config(
            trailing_stop_loss={"initial_stop": 101.0, "current_stop": 101.0},
        )
        setup = lifecycle_setup(config=config)
        result = revalidate_setup(setup, market(99.0), BROKER_OK, None)

        self.assertEqual(result["status"], SetupStatus.INVALIDATED.value)
        self.assertEqual(result["status_reason"], "STOP_ABOVE_ENTRY_FOR_LONG")
        self.assertFalse(result["can_send_order"])

    def test_broker_disconnected_blocks(self) -> None:
        setup = lifecycle_setup()
        broker_reality = {**BROKER_OK, "broker_connected": False}
        result = revalidate_setup(setup, market(99.0), broker_reality, RTH_NOW)

        self.assertEqual(result["status"], SetupStatus.BLOCKED.value)
        self.assertEqual(result["status_reason"], "BROKER_DISCONNECTED")
        self.assertFalse(result["can_send_order"])

    def test_setup_too_old_marks_stale(self) -> None:
        created = (datetime.now(UTC) - timedelta(days=45)).isoformat()
        setup = lifecycle_setup(created_at=created)
        result = revalidate_setup(setup, market(99.0), BROKER_OK, None)

        self.assertEqual(result["status"], SetupStatus.STALE_SETUP.value)
        self.assertEqual(result["status_reason"], "SETUP_TOO_OLD")
        self.assertFalse(result["can_be_armed"])

    def test_explicit_expiration_marks_expired(self) -> None:
        config = lifecycle_config(
            expiration={"expires_at": (datetime.now(UTC) - timedelta(hours=1)).isoformat()},
        )
        setup = lifecycle_setup(config=config)
        result = revalidate_setup(setup, market(99.0), BROKER_OK, None)

        self.assertEqual(result["status"], SetupStatus.EXPIRED.value)
        self.assertEqual(result["status_reason"], "TIME_EXPIRED")

    def test_trailing_stop_not_ready_blocks_order_but_not_waiting(self) -> None:
        config = lifecycle_config()
        config["trailing_stop_loss"]["broker_order"]["trailing_stop_order_ready"] = False
        setup = lifecycle_setup(config=config)
        result = revalidate_setup(setup, market(99.0), BROKER_OK, None)

        self.assertEqual(result["status"], SetupStatus.WAITING_ACTIVATION.value)
        self.assertIn("TRAILING_STOP_NOT_READY", result["blocking_reasons"])
        self.assertFalse(result["can_send_order"])
        self.assertTrue(result["can_be_armed"])


class SetupLifecycleServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.database = Database(Path(self.tmp.name) / "state.sqlite")
        self.database.initialize()
        self.repository = TradingRepository(self.database)
        self.event_store = EventStore(self.repository)
        self.service = SetupLifecycleService(
            repository=self.repository,
            event_store=self.event_store,
        )

    def tearDown(self) -> None:
        self.database.close()
        self.tmp.cleanup()

    def _insert_setup(self, setup: dict) -> None:
        from app.models import SetupRecord

        record = SetupRecord(
            setup_id=setup["setup_id"],
            symbol=setup["symbol"],
            setup_type=setup["setup_type"],
            enabled=bool(setup.get("enabled", True)),
            mode=setup.get("mode", "paper"),
            status=setup["status"],
            entry_zone="",
            stop_loss=None,
            risk_amount=None,
            order_status="",
            position_status="",
            last_event="test",
            config=setup["config"],
            created_at=setup.get("created_at", utc_now_iso()),
        )
        self.repository.upsert_setup(record)

    def test_apply_persists_status_reason_and_revalidated_at(self) -> None:
        setup = lifecycle_setup()
        self._insert_setup(setup)

        result = self.service.revalidate_and_apply(
            self.repository.get_setup(setup["setup_id"]),
            market_snapshot=market(105.0),
            broker_reality=deepcopy(BROKER_OK),
        )

        stored = self.repository.get_setup(setup["setup_id"])
        self.assertEqual(stored["status"], result["status"])
        self.assertIn(
            stored["status"],
            {
                SetupStatus.STALE_SETUP.value,
                SetupStatus.MISSED_BREAKOUT_WAIT_RETEST.value,
            },
        )
        self.assertEqual(stored["status_reason"], "PRICE_TOO_FAR_ABOVE_ENTRY")
        self.assertTrue(stored["last_revalidated_at"])

    def test_apply_recovers_blocked_setup_when_valid_again(self) -> None:
        setup = lifecycle_setup(status=SetupStatus.BLOCKED.value)
        self._insert_setup(setup)

        self.service.revalidate_and_apply(
            self.repository.get_setup(setup["setup_id"]),
            market_snapshot=market(99.0),
            broker_reality=deepcopy(BROKER_OK),
        )

        stored = self.repository.get_setup(setup["setup_id"])
        self.assertEqual(stored["status"], SetupStatus.WAITING_ACTIVATION.value)
        self.assertEqual(stored["status_reason"], "SETUP_VALID")

    def test_apply_does_not_touch_position_statuses(self) -> None:
        setup = lifecycle_setup(status=SetupStatus.IN_POSITION.value)
        self._insert_setup(setup)

        result = self.service.revalidate_and_apply(
            self.repository.get_setup(setup["setup_id"]),
            market_snapshot=market(105.0),
            broker_reality=deepcopy(BROKER_OK),
        )

        stored = self.repository.get_setup(setup["setup_id"])
        self.assertEqual(stored["status"], SetupStatus.IN_POSITION.value)
        self.assertEqual(result["status"], SetupStatus.IN_POSITION.value)


if __name__ == "__main__":
    unittest.main()
