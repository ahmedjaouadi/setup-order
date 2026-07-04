from __future__ import annotations

import tempfile
import unittest
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

from fastapi import HTTPException

from app.api import routes_opportunity_audit
from app.models import SetupStatus
from app.opportunity_audit.api_models import (
    ExpectedOpportunityRequestModel,
    OpportunityReplayRequestModel,
    ReplaySetupRequestModel,
)
from app.setups.breakout_retest import BreakoutRetestSetup
from app.storage.database import Database
from app.storage.repositories import TradingRepository
from tests.test_setups import valid_breakout_config


class OpportunityAuditApiTests(unittest.IsolatedAsyncioTestCase):
    async def test_replay_route_accepts_inline_setup_and_snapshots(self) -> None:
        config = valid_breakout_config()
        request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(repository=None)))

        result = await routes_opportunity_audit.replay_opportunity_audit(
            request,
            OpportunityReplayRequestModel(
                setups=[
                    ReplaySetupRequestModel(
                        config=config,
                        initial_status=SetupStatus.WAITING_ACTIVATION.value,
                    )
                ],
                snapshots=[
                    {
                        "symbol": "UEC",
                        "price": 14.70,
                        "close": 14.70,
                        "daily_close": 14.70,
                    },
                    breakout_retest_entry_payload(),
                ],
                expected_opportunities=[
                    ExpectedOpportunityRequestModel(
                        setup_id=config["setup_id"],
                        by_snapshot_index=1,
                    )
                ],
            ),
        )

        self.assertTrue(result["ok"])
        report = result["report"]
        self.assertEqual(report["summary"]["entries_detected"], 1)
        self.assertEqual(report["summary"]["missed_opportunities"], 0)
        self.assertEqual(
            report["steps"][1]["evaluations"][0]["action"],
            "ENTRY_READY",
        )

    async def test_replay_route_can_load_setup_from_repository(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        database = Database(Path(tmp.name) / "state.sqlite")
        try:
            database.initialize()
            repository = TradingRepository(database)
            config = deepcopy(valid_breakout_config())
            repository.upsert_setup(
                BreakoutRetestSetup(config).to_record(SetupStatus.WAITING_ENTRY_SIGNAL)
            )
            request = SimpleNamespace(
                app=SimpleNamespace(state=SimpleNamespace(repository=repository))
            )

            result = await routes_opportunity_audit.replay_opportunity_audit(
                request,
                OpportunityReplayRequestModel(
                    setup_ids=[config["setup_id"]],
                    snapshots=[breakout_retest_entry_payload()],
                ),
            )

            report = result["report"]
            self.assertEqual(report["summary"]["setup_ids"], [config["setup_id"]])
            self.assertEqual(report["summary"]["entries_detected"], 1)
            self.assertEqual(
                report["steps"][0]["evaluations"][0]["status_before"],
                SetupStatus.WAITING_ENTRY_SIGNAL.value,
            )
        finally:
            database.close()
            tmp.cleanup()

    async def test_replay_route_reports_missing_repository_setup(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        database = Database(Path(tmp.name) / "state.sqlite")
        try:
            database.initialize()
            repository = TradingRepository(database)
            request = SimpleNamespace(
                app=SimpleNamespace(state=SimpleNamespace(repository=repository))
            )

            with self.assertRaises(HTTPException) as ctx:
                await routes_opportunity_audit.replay_opportunity_audit(
                    request,
                    OpportunityReplayRequestModel(
                        setup_ids=["UNKNOWN_SETUP"],
                        snapshots=[breakout_retest_entry_payload()],
                    ),
                )

            self.assertEqual(ctx.exception.status_code, 404)
            self.assertIn("UNKNOWN_SETUP", ctx.exception.detail)
        finally:
            database.close()
            tmp.cleanup()


def breakout_retest_entry_payload() -> dict[str, object]:
    return {
        "symbol": "UEC",
        "price": 14.35,
        "open": 14.20,
        "high": 14.42,
        "close": 14.38,
        "daily_close": 14.70,
        "bullish_candle": True,
        "session": "RTH",
        "market_open_time": "2026-06-13T09:30:00-04:00",
        "current_time": "2026-06-13T10:15:00-04:00",
    }


if __name__ == "__main__":
    unittest.main()
