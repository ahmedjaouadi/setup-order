from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from app.forecasting.forecast_repository import ForecastRepository
from app.forecasting.forecast_service import ForecastService
from app.model_lab import ModelLabService
from app.models import MarketSnapshot, SetupRecord
from app.scoring import SetupQualityEngine
from app.setups.creation_snapshot_service import SetupCreationSnapshotService
from app.storage.database import Database
from app.storage.repositories import TradingRepository


async def _market_history(_symbol: str, _timeframe: str) -> dict:
    return {"historical_bars": []}


class V241LegacyStopCleanupTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = TemporaryDirectory()
        self.database = Database(Path(self.tmp.name) / "state.sqlite")
        self.database.initialize()
        self.repository = TradingRepository(self.database)

    def tearDown(self) -> None:
        self.database.close()
        self.tmp.cleanup()

    def _upsert_legacy_setup(self, setup_id: str, symbol: str = "LEG") -> None:
        config = {
            "setup_id": setup_id,
            "symbol": symbol,
            "setup_type": "momentum_breakout",
            "entry": {"trigger_price": 10.0, "limit_price": 10.1},
            "risk": {
                "protective_stop": 9.5,
                "max_risk_usd": 25,
                "max_position_amount_usd": 250,
            },
            "targets": {"first_target": 11.5},
        }
        self.repository.upsert_setup(
            SetupRecord(
                setup_id=setup_id,
                symbol=symbol,
                setup_type="momentum_breakout",
                enabled=False,
                mode="paper",
                status="DISABLED",
                entry_zone="10.0",
                stop_loss=9.5,
                risk_amount=10.0,
                order_status="NONE",
                position_status="NONE",
                last_event="created",
                config=config,
            )
        )

    def test_snapshot_storage_writes_canonical_trailing_stop_initial_stop(self) -> None:
        self._upsert_legacy_setup("SNAP_V241")
        service = SetupCreationSnapshotService(
            self.repository,
            lambda _symbol: MarketSnapshot(symbol="LEG", price=10.2, bid=10.1, ask=10.3),
        )

        snapshot = service.capture("SNAP_V241")

        self.assertEqual(snapshot["trailing_stop_loss"]["initial_stop"], 9.5)
        self.assertNotIn("initial_stop_loss", snapshot)
        self.assertNotIn("initial_stop_loss", snapshot["payload"])
        row = self.database.execute(
            """
            SELECT trailing_stop_initial_stop
            FROM setup_creation_snapshots
            WHERE setup_id = ?
            """,
            ("SNAP_V241",),
        ).fetchone()
        self.assertEqual(row["trailing_stop_initial_stop"], 9.5)
        columns = {
            item["name"]
            for item in self.database.execute(
                "PRAGMA table_info(setup_creation_snapshots)"
            ).fetchall()
        }
        self.assertIn("trailing_stop_initial_stop", columns)
        self.assertNotIn("initial_stop_loss", columns)
        embedded = self.repository.get_setup("SNAP_V241")["config"]["creation_market_snapshot"]
        self.assertEqual(embedded["trailing_stop_loss"]["initial_stop"], 9.5)
        self.assertNotIn("initial_stop_loss", embedded)

    def test_scoring_uses_canonical_trailing_stop_after_legacy_migration(self) -> None:
        self._upsert_legacy_setup("SCORE_V241")

        score = SetupQualityEngine(self.repository).score_setup("SCORE_V241")
        setup = self.repository.get_setup("SCORE_V241")

        self.assertEqual(score["components"]["technical_score"], 100.0)
        self.assertEqual(setup["config"]["trailing_stop_loss"]["initial_stop"], 9.5)
        self.assertNotIn("protective_stop", setup["config"]["risk"])

    def test_forecasting_references_use_canonical_stop_after_legacy_migration(self) -> None:
        self._upsert_legacy_setup("FORECAST_V241", symbol="FCST")
        service = ForecastService(
            settings={"forecasting": {"enabled": True}},
            repository=ForecastRepository(self.database),
            trading_repository=self.repository,
            market_history_provider=_market_history,
        )

        references = service._references_for_symbol("FCST", "FORECAST_V241")

        self.assertEqual(references.stop_level_reference, 9.5)
        self.assertEqual(references.support_level_reference, 9.5)

    def test_model_lab_replay_uses_canonical_stop_after_legacy_migration(self) -> None:
        self._upsert_legacy_setup("LAB_V241", symbol="LAB")
        service = ModelLabService(self.repository)

        run = service.run_backtest_mvp(
            {
                "setup_id": "LAB_V241",
                "candles": [
                    {
                        "timestamp": "2026-06-20T14:30:00+00:00",
                        "open": 10.0,
                        "high": 10.2,
                        "low": 9.4,
                        "close": 9.6,
                    }
                ],
            }
        )
        events = service.backtest_events(run["backtest_id"])
        stop_event = next(event for event in events if event["event_type"] == "SIM_STOP_PLACED")

        self.assertEqual(stop_event["payload"]["stop"], 9.5)

    def test_gui_setup_detail_uses_trailing_stop_initial_stop_not_protective_stop(self) -> None:
        script = Path("app/gui/static/js/app.js").read_text(encoding="utf-8")

        self.assertIn('initial_trailing_stop: "trailing_stop_loss.initial_stop"', script)
        self.assertNotIn("setup.protective_stop", script)
        self.assertNotIn('id: "protective_stop"', script)

    def test_forecasting_and_model_lab_do_not_read_protective_stop_directly(self) -> None:
        for path in (
            Path("app/forecasting/forecast_service.py"),
            Path("app/model_lab/service.py"),
        ):
            self.assertNotIn(
                'setup.get("protective_stop")',
                path.read_text(encoding="utf-8"),
            )


if __name__ == "__main__":
    unittest.main()
