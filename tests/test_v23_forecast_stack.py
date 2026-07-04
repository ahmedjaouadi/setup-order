from __future__ import annotations

import unittest
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory

from app.forecasting.forecast_accuracy_repository import ForecastAccuracyRepository
from app.forecasting.forecast_accuracy_service import ForecastAccuracyService
from app.forecasting.forecast_provider_status import ForecastProviderStatusService
from app.forecasting.forecast_repository import ForecastRepository
from app.model_lab.forecast_stack_benchmark import ForecastStackBenchmarkService
from app.models import MarketSnapshot, SetupRecord
from app.scoring import SetupQualityEngine
from app.setups.creation_snapshot_service import SetupCreationSnapshotService
from app.storage.database import Database
from app.storage.repositories import TradingRepository


class _FakeForecastService:
    def models(self):
        return {
            "timesfm": {"model": "timesfm", "available": True, "reason": "available"},
            "external_models": [
                {
                    "model": "chronos",
                    "available": False,
                    "reason": "Missing optional package(s): chronos",
                }
            ],
            "baselines": [
                {"model": "naive_baseline", "available": True, "reason": "deterministic"},
                {"model": "atr_baseline", "available": True, "reason": "deterministic"},
            ],
        }


class _CatalogBackedForecastService:
    def __init__(self, catalog, repository=None, accuracy_service=None):
        self._catalog = catalog
        self.repository = repository
        self.accuracy_service = accuracy_service

    def models(self):
        return self._catalog

    @staticmethod
    def _persisted_model_name(name: str) -> str:
        return name


class V23ForecastStackTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = TemporaryDirectory()
        self.database = Database(Path(self.tmp.name) / "state.sqlite")
        self.database.initialize()
        self.repository = TradingRepository(self.database)

    def tearDown(self) -> None:
        self.database.close()
        self.tmp.cleanup()

    def test_forecast_outcome_evaluates_after_horizon_and_builds_scorecard(self) -> None:
        service = ForecastAccuracyService(
            ForecastAccuracyRepository(self.database),
            {"forecast_accuracy": {"min_required_samples": 1}},
        )
        generated = (datetime.now(UTC) - timedelta(hours=2)).isoformat()
        outcome_id = service.register(
            1,
            {
                "status": "OK",
                "model": "timesfm",
                "symbol": "TEST",
                "timeframe": "15m",
                "horizon_bars": 4,
                "generated_at": generated,
                "current_price": 100.0,
                "forecast_last_price": 105.0,
            },
        )

        result = service.evaluate_due({"TEST": 104.0})
        scorecards = service.rebuild_scorecards()

        self.assertIsNotNone(outcome_id)
        self.assertEqual(result["evaluated_count"], 1)
        self.assertTrue(result["evaluated"][0]["direction_correct"])
        self.assertEqual(scorecards[0]["sample_size"], 1)
        self.assertIn(scorecards[0]["reliability_grade"], {"A", "B", "C", "D", "F"})

    def test_creation_snapshot_is_immutable_and_allows_missing_market_data(self) -> None:
        config = {
            "setup_id": "SNAP_001",
            "symbol": "SNAP",
            "setup_type": "momentum_breakout",
            "entry": {"trigger_price": 11.0, "limit_price": 11.2},
            "risk": {"initial_stop_loss": 9.5},
        }
        self.repository.upsert_setup(
            SetupRecord(
                setup_id="SNAP_001",
                symbol="SNAP",
                setup_type="momentum_breakout",
                enabled=False,
                mode="paper",
                status="DISABLED",
                entry_zone="11.0",
                stop_loss=9.5,
                risk_amount=15.0,
                order_status="NONE",
                position_status="NONE",
                last_event="created",
                config=config,
            )
        )
        current: dict[str, MarketSnapshot | None] = {"value": None}
        service = SetupCreationSnapshotService(self.repository, lambda _symbol: current["value"])

        first = service.capture("SNAP_001")
        current["value"] = MarketSnapshot(symbol="SNAP", price=10.5, bid=10.4, ask=10.6)
        second = service.capture("SNAP_001")

        self.assertEqual(first["snapshot_id"], second["snapshot_id"])
        self.assertEqual(first["data_quality_status"], "WARNING")
        self.assertIsNone(second["last_price"])

    def test_provider_status_is_explicit_and_never_enables_execution(self) -> None:
        service = ForecastProviderStatusService(
            {
                "forecast_stack": {
                    "providers": {
                        "timesfm": {"enabled": True, "priority": 0, "role": "primary"},
                        "chronos": {"enabled": True, "priority": 1, "role": "direct_competitor"},
                    }
                }
            },
            _FakeForecastService(),
        )

        result = service.list()
        by_name = {item["model_name"]: item for item in result["providers"]}

        self.assertEqual(by_name["timesfm"]["status"], "OK")
        self.assertEqual(by_name["chronos"]["status"], "MISSING_DEPENDENCY")
        self.assertEqual(by_name["timesfm"]["reliability_status"], "ACCURACY_HISTORY_WARMUP")
        self.assertEqual(by_name["timesfm"]["reliability_grade"], "WARMUP")
        self.assertEqual(by_name["chronos"]["dependency_status"], "DEPENDENCY_MISSING")
        self.assertEqual(by_name["chronos"]["worker_status"], "DEPENDENCY_MISSING")
        self.assertTrue(all(not item["use_for_execution"] for item in result["providers"]))

    def test_provider_status_surfaces_latest_forecast_summary_fields(self) -> None:
        forecast_repository = ForecastRepository(self.database)
        forecast_repository.insert_forecast(
            {
                "status": "OK",
                "model": "timesfm",
                "symbol": "SAFE",
                "timeframe": "15m",
                "target": "price",
                "context_bars": 40,
                "horizon_bars": 4,
                "generated_at": datetime.now(UTC).isoformat(),
                "current_price": 100.0,
                "forecast_last_price": 105.0,
                "forecast_expected_return_pct": 5.0,
                "forecast_status": "BULLISH",
                "confidence": "HIGH",
                "direction": "UP",
                "direction_confidence": 0.82,
                "uncertainty_width_pct": 12.0,
                "q10_end_price": 98.0,
                "q90_end_price": 110.0,
                "metric_score": 95,
            }
        )
        service = ForecastProviderStatusService(
            {
                "forecast_stack": {
                    "providers": {
                        "timesfm": {"enabled": True, "priority": 0, "role": "primary"},
                    }
                }
            },
            _CatalogBackedForecastService(
                {
                    "timesfm": {
                        "model": "timesfm",
                        "status": "OK",
                        "available": True,
                        "reason": "available",
                    }
                },
                repository=forecast_repository,
            ),
        )

        provider = service.list()["providers"][0]

        self.assertEqual(provider["forecast_status"], "FORECAST_OK")
        self.assertEqual(provider["direction"], "UP")
        self.assertEqual(provider["confidence"], "HIGH")
        self.assertEqual(provider["confidence_display"], "HIGH (82%)")
        self.assertEqual(provider["expected_move_pct"], 5.0)
        self.assertEqual(provider["uncertainty_width_pct"], 12.0)
        self.assertEqual(provider["forecast_horizon"], "4 x 15m")
        self.assertEqual(provider["execution_block_reason"], "FORECAST_STACK_ADVISORY_ONLY")
        self.assertIsNone(provider["last_error"])

    def test_provider_status_requires_error_details_and_keeps_history_visible(self) -> None:
        forecast_repository = ForecastRepository(self.database)
        forecast_repository.insert_forecast(
            {
                "status": "WORKER_ERROR",
                "model": "lag_llama",
                "symbol": "FAIL",
                "timeframe": "15m",
                "target": "price",
                "context_bars": 40,
                "horizon_bars": 4,
                "generated_at": datetime.now(UTC).isoformat(),
                "current_price": 100.0,
                "forecast_status": "MODEL_ERROR",
                "confidence": "LOW",
                "error": "",
                "metric_score": 0,
            }
        )
        accuracy_repository = ForecastAccuracyRepository(self.database)
        accuracy_repository.replace_scorecards(
            [
                {
                    "scorecard_id": "scorecard_fail",
                    "model_name": "lag_llama",
                    "symbol": "FAIL",
                    "timeframe": "15m",
                    "horizon_bars": 4,
                    "sample_size": 10,
                    "direction_accuracy": 0.55,
                    "mae": 1.2,
                    "rmse": 1.8,
                    "mape": 0.06,
                    "reliability_grade": "",
                    "updated_at": datetime.now(UTC).isoformat(),
                }
            ]
        )
        service = ForecastProviderStatusService(
            {
                "forecast_stack": {
                    "providers": {
                        "lag_llama": {"enabled": True, "priority": 1, "role": "probabilistic"},
                    }
                }
            },
            _CatalogBackedForecastService(
                {
                    "timesfm": {
                        "model": "timesfm",
                        "status": "OK",
                        "available": True,
                        "reason": "available",
                    },
                    "external_models": [
                        {
                            "model": "lag_llama",
                            "status": "EXTERNAL_WORKER_OK",
                            "available": True,
                            "reason": "external worker healthcheck OK",
                            "runtime_mode": "external_worker",
                        }
                    ],
                    "baselines": [],
                },
                repository=forecast_repository,
                accuracy_service=ForecastAccuracyService(accuracy_repository),
            ),
        )

        provider = service.list()["providers"][0]

        self.assertEqual(provider["worker_status"], "WORKER_READY")
        self.assertEqual(provider["forecast_status"], "FORECAST_ERROR")
        self.assertEqual(provider["execution_block_reason"], "FORECAST_FAILED")
        self.assertEqual(provider["historical_accuracy_status"], "INSUFFICIENT_ACCURACY_HISTORY")
        self.assertEqual(provider["reliability_status"], "INSUFFICIENT_ACCURACY_HISTORY")
        self.assertIn("status 'WORKER_ERROR'", provider["last_error"])

    def test_model_lab_only_provider_has_explicit_not_run_reason(self) -> None:
        service = ForecastProviderStatusService(
            {
                "forecast_stack": {
                    "providers": {
                        "darts": {
                            "enabled": True,
                            "auto_enable_when_ready": True,
                            "priority": 1,
                            "role": "benchmark_framework",
                            "use_for_runtime_forecast": False,
                            "use_for_model_lab": True,
                        },
                    }
                }
            },
            _CatalogBackedForecastService(
                {
                    "timesfm": {
                        "model": "timesfm",
                        "status": "OK",
                        "available": True,
                        "reason": "available",
                    },
                    "external_models": [
                        {
                            "model": "darts",
                            "status": "OK",
                            "available": True,
                            "reason": "available",
                            "runtime_mode": "in_process",
                            "model_lab_only": True,
                        }
                    ],
                    "baselines": [],
                }
            ),
        )

        provider = service.list()["providers"][0]

        self.assertEqual(provider["forecast_status"], "NOT_RUN")
        self.assertEqual(provider["execution_block_reason"], "BENCHMARK_FRAMEWORK_ONLY")
        self.assertTrue(provider["eligible_for_display"])
        self.assertTrue(provider["use_for_model_lab"])

    def test_offline_stack_comparison_persists_ranked_results(self) -> None:
        service = ForecastStackBenchmarkService(self.repository)
        result = service.compare(
            {
                "symbol": "RANK",
                "timeframe": "15m",
                "horizon_bars": 1,
                "actual": [10, 11, 12, 13],
                "predictions": {
                    "naive_baseline": [10, 10, 11, 12],
                    "timesfm": [10, 11, 12, 13],
                },
            }
        )
        persisted = service.experiment(result["experiment_id"])

        self.assertEqual(result["summary"]["winner"], "timesfm")
        self.assertEqual(len(persisted["results"]), 2)
        self.assertEqual(persisted["status"], "COMPLETED")

    def test_forecast_score_boost_requires_reliability_history(self) -> None:
        forecast_repository = ForecastRepository(self.database)
        accuracy_repository = ForecastAccuracyRepository(self.database)
        accuracy = ForecastAccuracyService(accuracy_repository)
        forecast_repository.insert_forecast(
            {
                "status": "OK",
                "model": "timesfm",
                "symbol": "SAFE",
                "timeframe": "15m",
                "target": "price",
                "context_bars": 40,
                "horizon_bars": 4,
                "generated_at": datetime.now(UTC).isoformat(),
                "current_price": 100.0,
                "forecast_last_price": 105.0,
                "forecast_expected_return_pct": 5.0,
                "forecast_slope": "UP",
                "forecast_status": "BULLISH",
                "confidence": "HIGH",
                "metric_score": 95,
            }
        )
        scoring = SetupQualityEngine(
            self.repository, forecast_repository, {}, forecast_accuracy_service=accuracy
        )

        without_history = scoring._forecast_score("SAFE")
        accuracy_repository.replace_scorecards(
            [
                {
                    "scorecard_id": "scorecard_safe",
                    "model_name": "timesfm",
                    "symbol": "SAFE",
                    "timeframe": "15m",
                    "horizon_bars": 4,
                    "sample_size": 40,
                    "direction_accuracy": 0.7,
                    "mae": 0.5,
                    "rmse": 0.6,
                    "mape": 0.03,
                    "reliability_grade": "A",
                    "updated_at": datetime.now(UTC).isoformat(),
                }
            ]
        )
        with_history = scoring._forecast_score("SAFE")

        self.assertEqual(without_history, 50.0)
        self.assertGreater(with_history, without_history)


if __name__ == "__main__":
    unittest.main()
