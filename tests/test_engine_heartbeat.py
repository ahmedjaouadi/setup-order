from __future__ import annotations

import tempfile
import unittest
from copy import deepcopy
from pathlib import Path

from app.engine.broker_reality import REPORT_STATE_KEY
from app.engine.trading_engine import TradingEngine
from app.models import ConnectionStatus
from app.settings import DEFAULT_CONFIG, Settings
from app.storage.database import Database
from app.storage.repositories import TradingRepository


class ToggleBroker:
    connector_name = "paper"
    account_mode = "paper"
    display_name = "Toggle broker"

    def __init__(self) -> None:
        self.connected = True

    async def status(self) -> ConnectionStatus:
        return ConnectionStatus.CONNECTED if self.connected else ConnectionStatus.DISCONNECTED

    async def positions(self) -> list:
        return []

    async def open_orders(self) -> list:
        return []

    async def account_summary(self) -> dict:
        return {"available": True, "source": "paper"}

    async def recent_executions(self) -> list:
        return []

    def drain_audit_entries(self) -> list:
        return []


class EngineHeartbeatDisconnectTests(unittest.IsolatedAsyncioTestCase):
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

    async def asyncTearDown(self) -> None:
        self.database.close()
        self.tmp.cleanup()

    async def test_broker_disconnect_blocks_immediately(self) -> None:
        broker = ToggleBroker()
        engine = TradingEngine(self.settings, self.repository, broker=broker)

        await engine._heartbeat(poll_stocks=False)
        report = self.repository.get_bot_state(REPORT_STATE_KEY, {})
        self.assertFalse(report.get("auto_execution_blocked"))

        broker.connected = False
        await engine._heartbeat(poll_stocks=False)

        report = self.repository.get_bot_state(REPORT_STATE_KEY, {})
        self.assertTrue(report.get("auto_execution_blocked"))
        self.assertIn("TWS_DISCONNECTED", report.get("blocking_reasons", []))


if __name__ == "__main__":
    unittest.main()
