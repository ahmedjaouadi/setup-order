from __future__ import annotations

import asyncio
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest

from fastapi import FastAPI

from app.api import (
    routes_forecasting,
    routes_observability,
    routes_opportunities,
    routes_platform,
    routes_research,
    routes_scoring,
)
from app.data_quality import DataQualityService
from app.event_bus import EventBus
from app.features import FeatureStore
from app.forecasting.forecast_repository import ForecastRepository
from app.forecasting.forecast_service import ForecastService
from app.model_lab import ModelLabService
from app.models import EventLevel, PositionRecord, SetupStatus
from app.observability import ObservabilityService
from app.opportunities import OpportunityScannerService
from app.portfolio_risk import PortfolioRiskService
from app.scoring import SetupQualityEngine
from app.setups.momentum_breakout import MomentumBreakoutSetup
from app.storage.database import Database
from app.storage.event_store import EventStore
from app.storage.repositories import TradingRepository
from tests.test_forecasting import FakeForecastEngine, fake_market_history
from tests.test_setups import valid_momentum_config


class V2PlatformServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.database = Database(root / "state.sqlite")
        self.database.initialize()
        self.repository = TradingRepository(self.database)
        self.event_store = EventStore(self.repository)
        self.forecast_repository = ForecastRepository(self.database)
        self.settings = {
            "forecasting": {"enabled": True, "min_context_bars": 128},
            "risk": {"max_total_exposure_usd": 1000},
        }
        self.forecast = ForecastService(
            settings=self.settings,
            repository=self.forecast_repository,
            trading_repository=self.repository,
            market_history_provider=fake_market_history,
            engine=FakeForecastEngine(),
        )
        self.scoring = SetupQualityEngine(
            self.repository,
            self.forecast_repository,
            self.settings,
        )
        self.scanner = OpportunityScannerService(
            self.repository,
            self.scoring,
            self.event_store,
            self.settings,
        )
        self.data_quality = DataQualityService(self.repository, self.settings)
        self.feature_store = FeatureStore(self.repository, self.settings)
        self.portfolio_risk = PortfolioRiskService(self.repository, self.settings)
        self.model_lab = ModelLabService(self.repository, self.settings)
        self.event_bus = EventBus(self.repository, self.event_store)
        self.observability = ObservabilityService(
            self.repository,
            snapshot_provider=lambda: {
                "runtime": {"status": "RUNNING"},
                "health": {"status": "OK", "broker_status": "CONNECTED"},
                "metrics": {"active_setups": 1, "open_positions": 0, "open_orders": 0},
            },
        )
        self.setup = MomentumBreakoutSetup(deepcopy(valid_momentum_config()))
        self.repository.upsert_setup(self.setup.to_record(SetupStatus.WAITING_ACTIVATION))

    def tearDown(self) -> None:
        self.database.close()
        self.tmp.cleanup()

    def test_scanner_scores_and_persists_non_executable_opportunity(self) -> None:
        result = self.scanner.scan()

        self.assertTrue(result["ok"])
        self.assertEqual(len(result["items"]), 1)
        opportunity = self.repository.get_opportunity(result["items"][0]["opportunity_id"])
        self.assertIsNotNone(opportunity)
        self.assertFalse(opportunity["payload"]["executable"])
        self.assertTrue(self.repository.list_setup_scores(setup_id=self.setup.setup_id))

    def test_support_services_persist_runtime_state(self) -> None:
        self.event_store.record(
            EventLevel.INFO,
            "stock_quote",
            "quote",
            symbol=self.setup.symbol,
            data={
                "bid": 14.10,
                "ask": 14.12,
                "price": 14.11,
                "volume_ratio": 1.8,
                "timestamp": "2999-01-01T00:00:00+00:00",
            },
        )
        self.repository.upsert_position(
            PositionRecord(
                symbol=self.setup.symbol,
                setup_id=self.setup.setup_id,
                quantity=10,
                average_price=14.0,
                current_price=14.2,
                unrealized_pnl=2.0,
                current_stop=13.0,
                risk_remaining=12.0,
                status="OPEN",
            )
        )

        data_quality = self.data_quality.evaluate_symbol(self.setup.symbol)
        features = self.feature_store.snapshot_symbol(self.setup.symbol)
        portfolio = self.portfolio_risk.analyze()
        backtest = self.model_lab.run_backtest({"setup_id": self.setup.setup_id})
        event = self.event_bus.publish("unit_v2_event", symbol=self.setup.symbol)

        self.assertEqual(data_quality["status"], "OK")
        self.assertEqual(features["features"]["spread_pct"], 0.1417)
        self.assertEqual(portfolio["open_positions_count"], 1)
        self.assertEqual(backtest["status"], "COMPLETED")
        self.assertEqual(event["event_type"], "unit_v2_event")
        self.assertTrue(self.repository.list_runtime_events(event_type="unit_v2_event"))

    def test_v2_routes_are_registered_and_respond(self) -> None:
        app = FastAPI()
        app.state.repository = self.repository
        app.state.forecast_repository = self.forecast_repository
        app.state.forecast = self.forecast
        app.state.scoring = self.scoring
        app.state.opportunity_scanner = self.scanner
        app.state.data_quality = self.data_quality
        app.state.feature_store = self.feature_store
        app.state.portfolio_risk = self.portfolio_risk
        app.state.model_lab = self.model_lab
        app.state.event_bus = self.event_bus
        app.state.observability = self.observability
        routers = (
            routes_forecasting.router,
            routes_observability.router,
            routes_opportunities.router,
            routes_platform.router,
            routes_research.router,
            routes_scoring.router,
        )
        for router in routers:
            app.include_router(router)

        paths = {
            route.path
            for router in routers
            for route in router.routes
            if hasattr(route, "path")
        }
        for path in {
            "/api/opportunities",
            "/api/scanner/status",
            "/api/scoring/score-setup/{setup_id}",
            "/api/backtests/run",
            "/api/model-lab/benchmark",
            "/api/forecasting/run",
            "/api/metrics",
            "/api/system/status",
            "/api/decision-trace/{trace_id}",
        }:
            self.assertIn(path, paths)

        scanner_status, scanner_body = asyncio.run(_request(app, "GET", "/api/scanner/status"))
        scoring_status, scoring_body = asyncio.run(
            _request(app, "POST", f"/api/scoring/score-setup/{self.setup.setup_id}")
        )
        metrics_status, metrics_body = asyncio.run(_request(app, "GET", "/api/metrics"))

        self.assertEqual(scanner_status, 200, scanner_body)
        self.assertEqual(scoring_status, 200, scoring_body)
        self.assertEqual(metrics_status, 200, metrics_body)
        self.assertEqual(metrics_body["tws_connection_status"], "CONNECTED")


async def _request(
    app: FastAPI,
    method: str,
    path: str,
    body: dict | None = None,
) -> tuple[int, dict]:
    status_code = 0
    body_parts: list[bytes] = []
    request_sent = False
    raw_body = json.dumps(body or {}).encode("utf-8") if body is not None else b""
    scope = {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": "2.3"},
        "http_version": "1.1",
        "method": method,
        "scheme": "http",
        "path": path,
        "raw_path": path.encode("utf-8"),
        "query_string": b"",
        "headers": [(b"host", b"testserver"), (b"content-type", b"application/json")],
        "client": ("testclient", 50000),
        "server": ("testserver", 80),
    }

    async def receive() -> dict:
        nonlocal request_sent
        if request_sent:
            return {"type": "http.disconnect"}
        request_sent = True
        return {"type": "http.request", "body": raw_body, "more_body": False}

    async def send(message: dict) -> None:
        nonlocal status_code
        if message["type"] == "http.response.start":
            status_code = int(message["status"])
        if message["type"] == "http.response.body":
            body_parts.append(message.get("body", b""))

    await app(scope, receive, send)
    response_body = b"".join(body_parts)
    return status_code, json.loads(response_body.decode("utf-8") or "{}")


if __name__ == "__main__":
    unittest.main()
