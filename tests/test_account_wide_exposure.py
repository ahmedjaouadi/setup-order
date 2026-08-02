from __future__ import annotations

import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path

from app.broker.ib_models import BrokerPosition
from app.engine.broker_reality import (
    REPORT_STATE_KEY,
    account_wide_exposure,
    build_broker_reality_report,
)
from app.storage.database import Database
from app.storage.repositories import TradingRepository


class AccountWideExposureTests(unittest.TestCase):
    """Root D, D-1: the helper that reads the account-wide exposure from the
    cached broker_reality report (audit 87/88, audit/ORDRE_D1.md)."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.database = Database(Path(self.tmp.name) / "state.sqlite")
        self.database.initialize()
        self.repository = TradingRepository(self.database)

    def tearDown(self) -> None:
        self.database.close()
        self.tmp.cleanup()

    def store_report(
        self,
        positions: list[BrokerPosition],
        *,
        now: str | None = None,
        connected: bool = True,
    ) -> None:
        report = build_broker_reality_report(
            local_setups=[],
            local_orders=[],
            local_positions=[],
            broker_orders=[],
            broker_positions=positions,
            broker_connected=connected,
            now=now,
        )
        self.repository.set_bot_state(REPORT_STATE_KEY, report)

    def test_absent_report_is_not_fresh(self) -> None:
        exposure = account_wide_exposure(self.repository, {})
        self.assertFalse(exposure.fresh)
        self.assertIsNone(exposure.positions_count)
        self.assertIsNone(exposure.capital_usd)
        self.assertEqual(exposure.fallback_reason, "BROKER_REALITY_REPORT_ABSENT")

    def test_fresh_report_counts_positions_and_sums_capital(self) -> None:
        self.store_report(
            [
                BrokerPosition(symbol="AAAA", quantity=10, average_price=20.0, current_price=21.0),
                BrokerPosition(symbol="BBBB", quantity=5, average_price=100.0, current_price=99.0),
            ]
        )
        exposure = account_wide_exposure(self.repository, {})
        self.assertTrue(exposure.fresh)
        self.assertEqual(exposure.positions_count, 2)
        self.assertAlmostEqual(exposure.capital_usd, 10 * 20.0 + 5 * 100.0)

    def test_zero_quantity_positions_excluded_from_capital_and_count(self) -> None:
        self.store_report(
            [
                BrokerPosition(symbol="AAAA", quantity=0, average_price=20.0, current_price=21.0),
                BrokerPosition(symbol="BBBB", quantity=3, average_price=10.0, current_price=11.0),
            ]
        )
        exposure = account_wide_exposure(self.repository, {})
        self.assertTrue(exposure.fresh)
        self.assertEqual(exposure.positions_count, 1)
        self.assertAlmostEqual(exposure.capital_usd, 30.0)

    def test_stale_report_falls_back(self) -> None:
        old = (datetime.now(UTC) - timedelta(seconds=9999)).isoformat()
        self.store_report(
            [BrokerPosition(symbol="AAAA", quantity=10, average_price=20.0, current_price=21.0)],
            now=old,
        )
        exposure = account_wide_exposure(self.repository, {})
        self.assertFalse(exposure.fresh)
        self.assertIsNone(exposure.positions_count)
        self.assertIsNone(exposure.capital_usd)
        self.assertEqual(exposure.fallback_reason, "BROKER_REALITY_REPORT_STALE")

    def test_disconnected_report_falls_back(self) -> None:
        self.store_report(
            [BrokerPosition(symbol="AAAA", quantity=10, average_price=20.0, current_price=21.0)],
            connected=False,
        )
        exposure = account_wide_exposure(self.repository, {})
        self.assertFalse(exposure.fresh)
        self.assertEqual(exposure.fallback_reason, "BROKER_REALITY_REPORT_DISCONNECTED")


if __name__ == "__main__":
    unittest.main()
