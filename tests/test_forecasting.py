from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from app.forecasting.forecast_models import ForecastConfig, TimesFMForecastOutput
from app.forecasting.forecast_repository import ForecastRepository
from app.forecasting.forecast_service import ForecastService
from app.storage.database import Database


class FakeForecastEngine:
    def __init__(self):
        self.calls = 0
        self.series = None

    def forecast(self, series, *, horizon: int, config: ForecastConfig):
        self.calls += 1
        self.series = series
        return TimesFMForecastOutput(
            q10_path=[0.001] * horizon,
            q50_path=[0.01] * horizon,
            q90_path=[0.02] * horizon,
        )


class TinyMoveForecastEngine:
    def forecast(self, series, *, horizon: int, config: ForecastConfig):
        del series, config
        return TimesFMForecastOutput(
            q10_path=[0.0] * horizon,
            q50_path=[0.0000001] * horizon,
            q90_path=[0.0000002] * horizon,
        )


class FakeTradingRepository:
    def __init__(self):
        self.setup = {
            "setup_id": "FLNC_20260615_001",
            "symbol": "FLNC",
            "entry_trigger": 10.4,
            "config": {
                "risk": {},
                "trailing_stop_loss": {
                    "enabled": True,
                    "initial_stop": 9.4,
                    "broker_order": {
                        "required_before_entry_transmission": True,
                    },
                },
                "support_zone": {"min": 9.5, "max": 9.8},
                "entry": {"trigger_price": 10.4},
            },
        }
        self.events = []

    def list_setups(self):
        return [self.setup]

    def get_setup(self, setup_id):
        return self.setup if setup_id == self.setup["setup_id"] else None

    def list_events(
        self,
        limit=100,
        setup_id=None,
        symbol=None,
        level=None,
        event_type=None,
    ):
        del setup_id, level
        rows = [
            event
            for event in self.events
            if (not symbol or event.get("symbol") == symbol)
            and (not event_type or event.get("event_type") == event_type)
        ]
        return rows[:limit]


async def fake_market_history(symbol: str, timeframe: str):
    bars = [
        {
            "date": f"20260615 10:{index % 60:02d}:00",
            "close": 10 + index * 0.01,
        }
        for index in range(130)
    ]
    return {
        "symbol": symbol,
        "timeframe": timeframe,
        "available": True,
        "historical_bars": bars,
    }


class ForecastingTests(unittest.IsolatedAsyncioTestCase):
    async def test_forecast_metric_is_persisted_and_never_used_for_decision(self) -> None:
        with TemporaryDirectory() as folder:
            database = Database(Path(folder) / "state.sqlite")
            database.initialize()
            repository = ForecastRepository(database)
            service = ForecastService(
                settings={
                    "forecasting": {
                        "enabled": True,
                        "min_context_bars": 128,
                        "context_bars": 512,
                        "horizon_bars": 4,
                        "target": "log_return",
                        "use_for_decision": True,
                    }
                },
                repository=repository,
                trading_repository=FakeTradingRepository(),
                market_history_provider=fake_market_history,
                engine=FakeForecastEngine(),
            )

            result = await service.forecast(
                "FLNC",
                setup_id="FLNC_20260615_001",
            )
            history = service.history("FLNC")
            watchlist = service.watchlist()
            database.close()

        self.assertEqual(result["status"], "OK")
        self.assertEqual(result["forecast_status"], "BULLISH")
        self.assertEqual(result["metric_score"], 100)
        self.assertFalse(result["used_for_decision"])
        self.assertEqual(result["decision_impact"], "NONE")
        self.assertTrue(result["q10_above_support"])
        self.assertTrue(result["median_above_entry_trigger"])
        self.assertEqual(result["direction"], "UP")
        self.assertEqual(result["direction_basis"], "q50_last_vs_reference_price")
        self.assertEqual(result["reference_price"], 11.29)
        self.assertEqual(result["median_end_price"], result["q50_end_price"])
        self.assertGreater(result["median_end_price"], result["reference_price"])
        self.assertEqual(len(history), 1)
        self.assertEqual(history[0]["symbol"], "FLNC")
        self.assertEqual(watchlist[0]["forecast_status"], "BULLISH")
        self.assertFalse(watchlist[0]["used_for_decision"])

    async def test_direction_uses_final_q50_vs_reference_price_with_flat_threshold(self) -> None:
        with TemporaryDirectory() as folder:
            database = Database(Path(folder) / "state.sqlite")
            database.initialize()
            service = ForecastService(
                settings={"forecasting": {"enabled": True, "min_context_bars": 128}},
                repository=ForecastRepository(database),
                trading_repository=FakeTradingRepository(),
                market_history_provider=fake_market_history,
                engine=TinyMoveForecastEngine(),
            )

            result = await service.forecast("FLNC", setup_id="FLNC_20260615_001")
            database.close()

        self.assertEqual(result["status"], "OK")
        self.assertEqual(result["direction"], "FLAT")
        self.assertEqual(result["direction_basis"], "q50_last_vs_reference_price")
        self.assertIsNotNone(result["reference_price"])
        self.assertIsNotNone(result["median_end_price"])
        self.assertLess(abs(result["median_end_price"] - result["reference_price"]), 0.01)

    async def test_insufficient_data_returns_status_without_crashing(self) -> None:
        async def short_history(symbol: str, timeframe: str):
            return {"historical_bars": [{"date": "20260615 10:00:00", "close": 10.0}]}

        with TemporaryDirectory() as folder:
            database = Database(Path(folder) / "state.sqlite")
            database.initialize()
            service = ForecastService(
                settings={"forecasting": {"enabled": True, "min_context_bars": 128}},
                repository=ForecastRepository(database),
                trading_repository=FakeTradingRepository(),
                market_history_provider=short_history,
                engine=FakeForecastEngine(),
            )

            result = await service.forecast("FLNC")
            database.close()

        self.assertEqual(result["status"], "INSUFFICIENT_DATA")
        self.assertIn("minimum required", result["error"])
        self.assertFalse(result["used_for_decision"])

    async def test_forecast_uses_persisted_stock_quote_bars_when_provider_is_empty(self) -> None:
        async def empty_history(symbol: str, timeframe: str):
            return {"historical_bars": [], "message": "broker history unavailable"}

        trading_repository = FakeTradingRepository()
        trading_repository.events = [
            {
                "event_type": "stock_quote",
                "symbol": "FLNC",
                "data": {
                    "historical_bar_size": "15 mins",
                    "historical_bars": [
                        {
                            "date": f"20260615 10:{index:02d}:00",
                            "close": 10 + index * 0.01,
                        }
                        for index in range(6)
                    ],
                },
            }
        ]

        with TemporaryDirectory() as folder:
            database = Database(Path(folder) / "state.sqlite")
            database.initialize()
            service = ForecastService(
                settings={"forecasting": {"enabled": True, "min_context_bars": 6}},
                repository=ForecastRepository(database),
                trading_repository=trading_repository,
                market_history_provider=empty_history,
                engine=FakeForecastEngine(),
            )

            result = await service.forecast("FLNC", timeframe="15m")
            database.close()

        self.assertEqual(result["status"], "OK")
        self.assertEqual(result["context_bars"], 6)
        self.assertEqual(result["input_start_time"], "20260615 10:00:00")

    async def test_cached_only_returns_fast_status_without_running_timesfm(self) -> None:
        history_calls = 0

        async def counted_history(symbol: str, timeframe: str):
            nonlocal history_calls
            history_calls += 1
            return await fake_market_history(symbol, timeframe)

        with TemporaryDirectory() as folder:
            database = Database(Path(folder) / "state.sqlite")
            database.initialize()
            engine = FakeForecastEngine()
            service = ForecastService(
                settings={"forecasting": {"enabled": True, "min_context_bars": 128}},
                repository=ForecastRepository(database),
                trading_repository=FakeTradingRepository(),
                market_history_provider=counted_history,
                engine=engine,
            )

            result = await service.forecast("FLNC", timeframe="15m", cached_only=True)
            database.close()

        self.assertEqual(result["status"], "NO_CACHED_FORECAST")
        self.assertFalse(result["used_for_decision"])
        self.assertEqual(history_calls, 0)
        self.assertEqual(engine.calls, 0)

    async def test_cached_forecast_reuse_and_force_refresh(self) -> None:
        with TemporaryDirectory() as folder:
            database = Database(Path(folder) / "state.sqlite")
            database.initialize()
            repository = ForecastRepository(database)
            engine = FakeForecastEngine()
            service = ForecastService(
                settings={
                    "forecasting": {
                        "enabled": True,
                        "min_context_bars": 128,
                        "context_bars": 512,
                        "horizon_bars": 4,
                        "target": "log_return",
                    }
                },
                repository=repository,
                trading_repository=FakeTradingRepository(),
                market_history_provider=fake_market_history,
                engine=engine,
            )

            first = await service.forecast("FLNC", setup_id="FLNC_20260615_001")
            cached = await service.forecast(
                "FLNC",
                setup_id="FLNC_20260615_001",
                cached_only=True,
            )
            refreshed = await service.forecast(
                "FLNC",
                setup_id="FLNC_20260615_001",
                force_refresh=True,
            )
            database.close()

        self.assertEqual(first["status"], "OK")
        self.assertEqual(cached["status"], "OK")
        self.assertTrue(cached["cache_hit"])
        self.assertEqual(refreshed["status"], "OK")
        self.assertFalse(refreshed.get("cache_hit", False))
        self.assertEqual(engine.calls, 2)

    async def test_cached_forecast_filters_by_setup_id_before_ordering(self) -> None:
        with TemporaryDirectory() as folder:
            database = Database(Path(folder) / "state.sqlite")
            database.initialize()
            repository = ForecastRepository(database)
            service = ForecastService(
                settings={
                    "forecasting": {
                        "enabled": True,
                        "min_context_bars": 128,
                        "context_bars": 512,
                        "horizon_bars": 4,
                        "target": "log_return",
                    }
                },
                repository=repository,
                trading_repository=FakeTradingRepository(),
                market_history_provider=fake_market_history,
                engine=FakeForecastEngine(),
            )

            first = await service.forecast("FLNC", setup_id="FLNC_20260615_001")
            repository.insert_forecast(
                {
                    **first,
                    "forecast_id": "forecast_other_setup",
                    "setup_id": "FLNC_OTHER_SETUP",
                    "status": "MODEL_ERROR",
                    "forecast_status": "MODEL_ERROR",
                    "error": "newer forecast from another setup",
                    "generated_at": "2999-01-01T00:00:00+00:00",
                }
            )

            cached = await service.forecast(
                "FLNC",
                setup_id="FLNC_20260615_001",
                cached_only=True,
            )
            summary = service.stack_summary(
                "FLNC",
                timeframe="15m",
                setup_id="FLNC_20260615_001",
            )
            database.close()

        self.assertEqual(cached["status"], "OK")
        self.assertEqual(cached["setup_id"], "FLNC_20260615_001")
        timesfm = next(item for item in summary["members"] if item["model_name"] == "timesfm")
        self.assertEqual(timesfm["status"], "OK")

    async def test_forecast_ensemble_uses_deterministic_baselines(self) -> None:
        with TemporaryDirectory() as folder:
            database = Database(Path(folder) / "state.sqlite")
            database.initialize()
            repository = ForecastRepository(database)
            service = ForecastService(
                settings={"forecasting": {"enabled": True, "min_context_bars": 128}},
                repository=repository,
                trading_repository=FakeTradingRepository(),
                market_history_provider=fake_market_history,
                engine=FakeForecastEngine(),
            )

            result = await service.forecast_ensemble(
                "FLNC",
                models=["naive_baseline", "atr_baseline"],
            )
            ensembles = repository.list_ensembles(symbol="FLNC")
            database.close()

        self.assertIn(result["status"], {"OK", "PARTIAL"})
        self.assertEqual(result["model"], "ensemble")
        self.assertEqual(result["model_count"], 2)
        self.assertEqual(result["successful_model_count"], 2)
        self.assertFalse(result["used_for_decision"])
        self.assertEqual(result["decision_impact"], "NONE")
        self.assertEqual(len(result["member_forecasts"]), 2)
        self.assertEqual(ensembles[0]["ensemble"]["ensemble_id"], result["ensemble_id"])

    async def test_default_ensemble_skips_unavailable_primary_and_runs_baselines(self) -> None:
        with TemporaryDirectory() as folder:
            database = Database(Path(folder) / "state.sqlite")
            database.initialize()
            repository = ForecastRepository(database)
            service = ForecastService(
                settings={"forecasting": {"enabled": True, "min_context_bars": 128}},
                repository=repository,
                trading_repository=FakeTradingRepository(),
                market_history_provider=fake_market_history,
                engine=FakeForecastEngine(),
            )

            # Every non-baseline provider must be unavailable for this test:
            # patching adapters.status keeps the assertion valid even on a
            # machine where chronos (or another provider) is truly installed.
            with (
                patch.object(
                    service,
                    "_timesfm_availability",
                    return_value={
                        "model": "timesfm",
                        "status": "MISSING_DEPENDENCY",
                        "available": False,
                        "reason": "Missing optional package(s): timesfm",
                    },
                ),
                patch.object(
                    service.adapters,
                    "status",
                    return_value=("MISSING_DEPENDENCY", "unavailable in test"),
                ),
            ):
                result = await service.forecast_ensemble("FLNC")
            database.close()

        self.assertIn(result["status"], {"OK", "PARTIAL"})
        self.assertEqual(result["models"], ("naive_baseline", "atr_baseline"))
        self.assertEqual(result["successful_model_count"], 2)
        self.assertEqual(result["model_count"], 2)

    async def test_model_catalog_displays_disabled_phase_two_providers(self) -> None:
        settings = {
            "forecasting": {"enabled": True},
            "forecast_stack": {
                "providers": {
                    "chronos": {
                        "enabled": False,
                        "auto_enable_when_ready": True,
                        "priority": 1,
                        "role": "direct_competitor",
                    },
                    "lag_llama": {
                        "enabled": False,
                        "auto_enable_when_ready": False,
                        "priority": 1,
                        "role": "probabilistic",
                    },
                    "moirai_uni2ts": {
                        "enabled": False,
                        "auto_enable_when_ready": False,
                        "priority": 3,
                        "role": "experimental_foundation_benchmark",
                    },
                    "neuralforecast": {
                        "enabled": False,
                        "auto_enable_when_ready": False,
                        "priority": 2,
                        "role": "deep_learning_models",
                    },
                    "autogluon": {
                        "enabled": False,
                        "auto_enable_when_ready": False,
                        "priority": 2,
                        "role": "automl_baseline",
                    },
                    "darts": {
                        "enabled": False,
                        "auto_enable_when_ready": True,
                        "priority": 1,
                        "role": "benchmark_framework",
                        "use_for_runtime_forecast": False,
                        "use_for_model_lab": True,
                    },
                },
            },
        }
        external_models = [
            {
                "model": "chronos",
                "status": "AVAILABLE",
                "available": True,
                "reason": "available",
                "baseline": False,
            },
            {
                "model": "lag_llama",
                "status": "AVAILABLE",
                "available": True,
                "reason": "available",
                "baseline": False,
            },
            {
                "model": "moirai",
                "status": "AVAILABLE",
                "available": True,
                "reason": "available",
                "baseline": False,
            },
            {
                "model": "moirai_uni2ts",
                "status": "AVAILABLE",
                "available": True,
                "reason": "available",
                "baseline": False,
            },
            {
                "model": "uni2ts",
                "status": "AVAILABLE",
                "available": True,
                "reason": "available",
                "baseline": False,
            },
            {
                "model": "neuralforecast",
                "status": "AVAILABLE",
                "available": True,
                "reason": "available",
                "baseline": False,
            },
            {
                "model": "autogluon",
                "status": "AVAILABLE",
                "available": True,
                "reason": "available",
                "baseline": False,
            },
        ]
        with TemporaryDirectory() as folder:
            database = Database(Path(folder) / "state.sqlite")
            database.initialize()
            repository = ForecastRepository(database)
            with (
                patch.object(
                    ForecastService,
                    "_timesfm_availability",
                    return_value={
                        "model": "timesfm",
                        "status": "AVAILABLE",
                        "available": True,
                        "reason": "available",
                        "baseline": False,
                    },
                ),
                patch(
                    "app.forecasting.forecast_service.ForecastAdapterRegistry.availability",
                    return_value=external_models,
                ),
                patch(
                    "app.forecasting.forecast_service.DartsExperimentRunner.provider_status",
                    return_value={"status": "AVAILABLE", "reason": "available"},
                ),
            ):
                service = ForecastService(
                    settings=settings,
                    repository=repository,
                    trading_repository=FakeTradingRepository(),
                    market_history_provider=fake_market_history,
                    engine=FakeForecastEngine(),
                )
                result = service.models()
            database.close()

        by_name = {
            item["model"]: item
            for item in [result["timesfm"], *result["external_models"], *result["baselines"]]
        }
        self.assertEqual(by_name["chronos"]["status"], "OK")
        self.assertEqual(by_name["darts"]["status"], "OK")
        self.assertEqual(by_name["naive_baseline"]["status"], "OK")
        self.assertEqual(by_name["atr_baseline"]["status"], "OK")
        self.assertEqual(by_name["lag_llama"]["status"], "DISABLED_BY_CONFIG")
        self.assertEqual(by_name["moirai"]["status"], "DISABLED_BY_CONFIG")
        self.assertEqual(by_name["moirai_uni2ts"]["status"], "DISABLED_BY_CONFIG")
        self.assertEqual(by_name["uni2ts"]["status"], "DISABLED_BY_CONFIG")
        self.assertEqual(by_name["neuralforecast"]["status"], "DISABLED_BY_CONFIG")
        self.assertEqual(by_name["autogluon"]["status"], "DISABLED_BY_CONFIG")
        self.assertIn("chronos", result["available_models"])
        self.assertIn("darts", result["available_models"])
        self.assertIn("naive_baseline", result["available_models"])
        self.assertIn("atr_baseline", result["available_models"])
        self.assertNotIn("lag_llama", result["available_models"])
        self.assertNotIn("moirai_uni2ts", result["available_models"])
        self.assertNotIn("neuralforecast", result["available_models"])
        self.assertNotIn("autogluon", result["available_models"])


if __name__ == "__main__":
    unittest.main()
