from __future__ import annotations

import socket
import tempfile
import threading
import time
import unittest
from contextlib import closing
from copy import deepcopy
from pathlib import Path

import uvicorn
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app.api import routes_dashboard, routes_setups
from app.broker.tws_connector import SimulatedBrokerConnector
from app.engine.trading_engine import TradingEngine
from app.models import SetupStatus
from app.settings import DEFAULT_CONFIG, Settings
from app.setups.momentum_breakout import MomentumBreakoutSetup
from app.storage.database import Database
from app.storage.repositories import TradingRepository
from tests.test_setups import valid_momentum_config

try:
    from playwright.sync_api import Error as PlaywrightError
    from playwright.sync_api import sync_playwright
except ImportError:  # pragma: no cover - optional local browser dependency
    PlaywrightError = None
    sync_playwright = None


@unittest.skipIf(sync_playwright is None, "Playwright is not installed")
class SetupLifecycleBrowserTests(unittest.TestCase):
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
        setup = MomentumBreakoutSetup(valid_momentum_config())
        self.setup_id = setup.setup_id
        self.repository.upsert_setup(setup.to_record(SetupStatus.DISABLED))
        self.app = _build_browser_app(self.settings, self.repository, self.engine)
        self.port = _free_port()
        self.server = uvicorn.Server(
            uvicorn.Config(
                self.app,
                host="127.0.0.1",
                port=self.port,
                log_level="warning",
            )
        )
        self.thread = threading.Thread(target=self.server.run, daemon=True)
        self.thread.start()
        self._wait_for_server()

    def tearDown(self) -> None:
        self.server.should_exit = True
        self.thread.join(timeout=5)
        self.database.close()
        self.tmp.cleanup()

    def test_setup_list_and_detail_arm_disarm_buttons(self) -> None:
        try:
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch()
                page = browser.new_page()
                page.goto(f"http://127.0.0.1:{self.port}/setups")
                page.locator(
                    f'button[data-action="arm-setup"][data-setup="{self.setup_id}"]'
                ).click()
                page.wait_for_selector(
                    f'button[data-action="disarm-setup"][data-setup="{self.setup_id}"]'
                )
                self.assertEqual(
                    self.repository.get_setup(self.setup_id)["status"],
                    SetupStatus.WAITING_ACTIVATION.value,
                )

                page.goto(f"http://127.0.0.1:{self.port}/setups/{self.setup_id}")
                page.locator("#setup-config-disarm").click()
                page.wait_for_function(
                    "() => document.querySelector('#setup-config-disarm')?.disabled === true"
                )
                self.assertEqual(
                    self.repository.get_setup(self.setup_id)["status"],
                    SetupStatus.DISABLED.value,
                )
                browser.close()
        except PlaywrightError as exc:
            self.skipTest(f"Playwright browser is not available: {exc}")

    def _wait_for_server(self) -> None:
        deadline = time.time() + 10
        while time.time() < deadline:
            with socket.socket() as probe:
                try:
                    probe.connect(("127.0.0.1", self.port))
                    return
                except OSError:
                    time.sleep(0.05)
        raise RuntimeError("Browser test server did not start")


def _build_browser_app(
    settings: Settings,
    repository: TradingRepository,
    engine: TradingEngine,
) -> FastAPI:
    app = FastAPI()
    app.state.settings = settings
    app.state.repository = repository
    app.state.engine = engine
    app.state.templates = Jinja2Templates(directory="app/gui/templates")
    app.mount("/static", StaticFiles(directory="app/gui/static"), name="static")
    app.include_router(routes_dashboard.router)
    app.include_router(routes_setups.router)
    return app


def _free_port() -> int:
    with closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


if __name__ == "__main__":
    unittest.main()
