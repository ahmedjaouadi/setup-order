from __future__ import annotations

import asyncio
import json
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path

from fastapi import FastAPI

from app.api import routes_setups
from app.broker.tws_connector import SimulatedBrokerConnector
from app.engine.trading_engine import TradingEngine
from app.models import SetupStatus
from app.settings import DEFAULT_CONFIG, Settings
from app.setups.momentum_breakout import MomentumBreakoutSetup
from app.storage.database import Database
from app.storage.repositories import TradingRepository
from tests.test_setups import valid_momentum_config


class SetupArmApiIntegrationTests(unittest.TestCase):
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
        self.engine = TradingEngine(
            settings=self.settings,
            repository=self.repository,
            broker=SimulatedBrokerConnector(),
        )
        self.app = FastAPI()
        self.app.state.engine = self.engine
        self.app.state.repository = self.repository
        self.app.include_router(routes_setups.router)

    def tearDown(self) -> None:
        self.database.close()
        self.tmp.cleanup()

    def test_arm_and_disarm_routes_update_repository_status(self) -> None:
        setup = MomentumBreakoutSetup(valid_momentum_config())
        self.repository.upsert_setup(setup.to_record(SetupStatus.DISABLED))

        preflight_status, preflight_body = asyncio.run(
            _request(self.app, "GET", f"/api/setups/{setup.setup_id}/arm-status")
        )

        self.assertEqual(preflight_status, 200, preflight_body)
        self.assertTrue(preflight_body["armable"])

        armed_status, armed_body = asyncio.run(
            _request(self.app, "POST", f"/api/setups/{setup.setup_id}/arm")
        )

        self.assertEqual(armed_status, 200, armed_body)
        self.assertEqual(
            self.repository.get_setup(setup.setup_id)["status"],
            SetupStatus.WAITING_ACTIVATION.value,
        )

        disarmed_status, disarmed_body = asyncio.run(
            _request(self.app, "POST", f"/api/setups/{setup.setup_id}/disarm")
        )

        self.assertEqual(disarmed_status, 200, disarmed_body)
        self.assertEqual(
            self.repository.get_setup(setup.setup_id)["status"],
            SetupStatus.DISABLED.value,
        )

    def test_setup_detail_payload_includes_setup_conditions(self) -> None:
        setup = MomentumBreakoutSetup(valid_momentum_config())
        self.repository.upsert_setup(setup.to_record(SetupStatus.WAITING_ACTIVATION))

        status, body = asyncio.run(
            _request(self.app, "GET", f"/api/setups/{setup.setup_id}")
        )

        self.assertEqual(status, 200, body)
        conditions = body.get("setup_conditions")
        self.assertIsInstance(conditions, dict)
        self.assertEqual(conditions["setup_type"], "momentum_breakout")
        self.assertEqual(conditions["overall_status"], "watching")
        self.assertFalse(conditions["management_only"])
        self.assertGreater(len(conditions["conditions"]), 0)
        for condition in conditions["conditions"]:
            self.assertIn("id", condition)
            self.assertIn("label", condition)
            self.assertIn("status", condition)
            self.assertIn("target", condition)


async def _request(app: FastAPI, method: str, path: str) -> tuple[int, dict]:
    status_code = 0
    body_parts: list[bytes] = []
    request_sent = False

    scope = {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": "2.3"},
        "http_version": "1.1",
        "method": method,
        "scheme": "http",
        "path": path,
        "raw_path": path.encode("utf-8"),
        "query_string": b"",
        "headers": [(b"host", b"testserver")],
        "client": ("testclient", 50000),
        "server": ("testserver", 80),
    }

    async def receive() -> dict:
        nonlocal request_sent
        if request_sent:
            return {"type": "http.disconnect"}
        request_sent = True
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message: dict) -> None:
        nonlocal status_code
        if message["type"] == "http.response.start":
            status_code = int(message["status"])
        if message["type"] == "http.response.body":
            body_parts.append(message.get("body", b""))

    await app(scope, receive, send)
    body = b"".join(body_parts)
    return status_code, json.loads(body.decode("utf-8") or "{}")


if __name__ == "__main__":
    unittest.main()
