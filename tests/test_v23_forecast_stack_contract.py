from __future__ import annotations

import sys
import unittest
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace

from app.forecasting.adapters import ForecastAdapterRegistry
from app.forecasting.forecast_accuracy_calculator import reliability_grade
from app.forecasting.forecast_accuracy_repository import ForecastAccuracyRepository
from app.forecasting.forecast_accuracy_service import ForecastAccuracyService
from app.forecasting.forecast_ensemble import ForecastStackConsensus
from app.forecasting.forecast_models import ForecastConfig, TimesFMForecastOutput
from app.forecasting.forecast_provider_status import ForecastProviderStatusService
from app.model_lab.darts_experiment_runner import DartsExperimentRunner
from app.model_lab.forecast_stack_benchmark import ForecastStackBenchmarkService
from app.models import MarketSnapshot, SetupRecord
from app.scoring import SetupQualityEngine
from app.setups.creation_snapshot_service import SetupCreationSnapshotService
from app.storage.database import Database
from app.storage.repositories import TradingRepository


class _OutputAdapter:
    def __init__(self, name: str, output: TimesFMForecastOutput) -> None:
        self.name = name
        self.output = output

    def forecast(self, *args, **kwargs):
        return self.output


class _ProviderCatalog:
    def models(self):
        return {
            "timesfm": {"model": "timesfm", "available": True, "reason": "available"},
            "external_models": [
                {"model": "moirai_uni2ts", "available": True, "reason": "available"},
            ],
            "baselines": [],
        }


class ForecastAdapterContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.config = ForecastConfig(
            python_executable=sys.executable,
            target="price",
            min_context_bars=2,
            context_bars=4,
        )
        self.registry = ForecastAdapterRegistry()

    def test_timesfm_adapter_available_when_configured(self) -> None:
        capabilities = self.registry.capabilities("timesfm", self.config)
        self.assertTrue(capabilities.available)
        self.assertTrue(capabilities.supports_quantiles)

    def test_chronos_adapter_returns_normalized_result(self) -> None:
        self.registry._adapters["chronos"] = _OutputAdapter(
            "chronos", TimesFMForecastOutput([2.0, 3.0], [2.5, 3.5], [3.0, 4.0])
        )
        result = self.registry.forecast_normalized(
            "chronos",
            {"symbol": "ABC", "series": [1.0, 2.0], "closes": [1.0, 2.0], "horizon_bars": 2},
            self.config,
        )
        self.assertEqual(result.status, "OK")
        self.assertEqual(result.model_name, "chronos")
        self.assertEqual(result.quantiles["0.50"], [2.5, 3.5])

    def test_lag_llama_adapter_returns_quantiles(self) -> None:
        self.registry._adapters["lag_llama"] = _OutputAdapter(
            "lag_llama",
            TimesFMForecastOutput(
                [1.0],
                [2.0],
                [3.0],
                prob_touch_entry=0.7,
                prob_touch_stop_before_entry=0.2,
            ),
        )
        result = self.registry.forecast_normalized(
            "lag_llama",
            {"symbol": "ABC", "series": [1.0], "closes": [1.0], "horizon_bars": 1},
            self.config,
        )
        self.assertEqual(result.quantiles["0.10"], [1.0])
        self.assertEqual(result.prob_touch_entry, 0.7)

    def test_darts_adapter_not_used_for_runtime_order(self) -> None:
        status = DartsExperimentRunner().provider_status()
        self.assertFalse(status["runtime_allowed"])
        self.assertTrue(status["model_lab_only"])

    def test_neuralforecast_adapter_optional_dependency(self) -> None:
        result = self.registry.forecast_normalized(
            "neuralforecast", {"symbol": "ABC", "series": [1.0]}, self.config
        )
        self.assertIn(result.status, {"MISSING_DEPENDENCY", "LOAD_ERROR"})
        self.assertTrue(result.warnings)

    def test_autogluon_adapter_optional_dependency(self) -> None:
        result = self.registry.forecast_normalized(
            "autogluon", {"symbol": "ABC", "series": [1.0]}, self.config
        )
        self.assertIn(result.status, {"MISSING_DEPENDENCY", "LOAD_ERROR"})

    def test_moirai_uni2ts_uses_standard_statuses(self) -> None:
        service = ForecastProviderStatusService(
            {
                "forecast_stack": {
                    "providers": {
                        "moirai_uni2ts": {
                            "enabled": True,
                            "priority": 3,
                            "role": "experimental_foundation_benchmark",
                            "use_for_model_lab": True,
                        }
                    }
                }
            },
            _ProviderCatalog(),
        )
        provider = service.list()["providers"][0]
        self.assertEqual(provider["status"], "OK")
        self.assertTrue(provider["use_for_model_lab"])

    def test_missing_optional_package_returns_warning_not_crash(self) -> None:
        result = self.registry.forecast_normalized(
            "lag_llama", {"symbol": "ABC", "series": [1.0]}, self.config
        )
        self.assertNotEqual(result.status, "OK")
        self.assertGreater(len(result.warnings), 0)


class ForecastAccuracyAndSnapshotContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = TemporaryDirectory()
        self.database = Database(Path(self.tmp.name) / "state.sqlite")
        self.database.initialize()
        self.repository = TradingRepository(self.database)
        self.accuracy = ForecastAccuracyService(
            ForecastAccuracyRepository(self.database),
            {"forecast_accuracy": {"min_required_samples": 2}},
        )

    def tearDown(self) -> None:
        self.database.close()
        self.tmp.cleanup()

    def _forecast(self, *, generated_at: str | None = None) -> dict:
        return {
            "status": "OK",
            "model": "timesfm",
            "symbol": "TEST",
            "timeframe": "15m",
            "horizon_bars": 1,
            "generated_at": generated_at or datetime.now(UTC).isoformat(),
            "current_price": 100.0,
            "forecast_last_price": 105.0,
            "forecast_expected_return_pct": 5.0,
            "confidence": "HIGH",
            "entry_trigger_reference": 103.0,
            "stop_level_reference": 97.0,
        }

    def _setup(
        self, setup_id: str, snapshot: MarketSnapshot | None = None
    ) -> SetupCreationSnapshotService:
        config = {
            "setup_id": setup_id,
            "symbol": "SNAP",
            "setup_type": "momentum_breakout",
            "entry": {"trigger_price": 11.0, "limit_price": 11.2},
            "risk": {"initial_stop_loss": 9.5},
        }
        self.repository.upsert_setup(
            SetupRecord(
                setup_id=setup_id,
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
        return SetupCreationSnapshotService(self.repository, lambda _symbol: snapshot)

    def test_timesfm_forecast_outcome_created(self) -> None:
        outcome_id = self.accuracy.register(1, self._forecast())
        rows = self.accuracy.outcomes("timesfm")
        self.assertEqual(rows[0]["outcome_id"], outcome_id)
        self.assertEqual(rows[0]["outcome_status"], "PENDING")

    def test_forecast_outcome_evaluated_after_horizon(self) -> None:
        generated = (datetime.now(UTC) - timedelta(hours=1)).isoformat()
        self.accuracy.register(2, self._forecast(generated_at=generated))
        result = self.accuracy.evaluate_due({"TEST": {"path": [101, 103, 104], "price": 104}})
        self.assertEqual(result["evaluated_count"], 1)
        self.assertEqual(result["evaluated"][0]["status"], "EVALUATED")

    def test_direction_correct_computed(self) -> None:
        generated = (datetime.now(UTC) - timedelta(hours=1)).isoformat()
        self.accuracy.register(3, self._forecast(generated_at=generated))
        row = self.accuracy.evaluate_due({"TEST": 104.0})["evaluated"][0]
        self.assertTrue(row["direction_correct"])

    def test_absolute_error_computed(self) -> None:
        generated = (datetime.now(UTC) - timedelta(hours=1)).isoformat()
        self.accuracy.register(4, self._forecast(generated_at=generated))
        row = self.accuracy.evaluate_due({"TEST": 104.0})["evaluated"][0]
        self.assertEqual(row["absolute_error"], 1.0)
        self.assertEqual(row["signed_error"], 1.0)

    def test_scorecard_requires_minimum_samples(self) -> None:
        generated = (datetime.now(UTC) - timedelta(hours=1)).isoformat()
        self.accuracy.register(5, self._forecast(generated_at=generated))
        self.accuracy.evaluate_due({"TEST": 104.0})
        scorecard = self.accuracy.rebuild_scorecards()[0]
        self.assertFalse(scorecard["enough_data"])

    def test_reliability_grade_insufficient_data(self) -> None:
        self.assertEqual(
            reliability_grade(
                {"sample_size": 1, "direction_accuracy": 1, "mape": 0}, min_samples=2
            ),
            "INSUFFICIENT_DATA",
        )

    def test_reliability_grade_A_B_C_D_F(self) -> None:
        cases = [
            (0.7, 0.03, "A"),
            (0.6, 0.05, "B"),
            (0.54, 0.07, "C"),
            (0.49, 0.11, "D"),
            (0.2, 0.5, "F"),
        ]
        self.assertEqual(
            [
                reliability_grade({"sample_size": 30, "direction_accuracy": accuracy, "mape": mape})
                for accuracy, mape, _ in cases
            ],
            [grade for _, _, grade in cases],
        )

    def test_setup_creation_snapshot_created_on_new_setup(self) -> None:
        service = self._setup(
            "SNAP_A", MarketSnapshot(symbol="SNAP", price=10.5, bid=10.4, ask=10.6)
        )
        self.assertEqual(service.capture("SNAP_A")["setup_id"], "SNAP_A")

    def test_snapshot_contains_current_price(self) -> None:
        service = self._setup("SNAP_B", MarketSnapshot(symbol="SNAP", price=10.5))
        self.assertEqual(service.capture("SNAP_B")["last_price"], 10.5)

    def test_snapshot_does_not_replace_entry_price(self) -> None:
        service = self._setup("SNAP_C", MarketSnapshot(symbol="SNAP", price=10.5))
        service.capture("SNAP_C")
        self.assertEqual(
            self.repository.get_setup("SNAP_C")["config"]["entry"]["trigger_price"], 11.0
        )

    def test_snapshot_not_overwritten_on_setup_edit(self) -> None:
        service = self._setup("SNAP_D", MarketSnapshot(symbol="SNAP", price=10.5))
        first = service.capture("SNAP_D")
        second = service.capture("SNAP_D")
        self.assertEqual(first["snapshot_id"], second["snapshot_id"])

    def test_duplicate_setup_creates_new_snapshot(self) -> None:
        first = self._setup("SNAP_E1", MarketSnapshot(symbol="SNAP", price=10.5)).capture("SNAP_E1")
        second = self._setup("SNAP_E2", MarketSnapshot(symbol="SNAP", price=10.5)).capture(
            "SNAP_E2"
        )
        self.assertNotEqual(first["snapshot_id"], second["snapshot_id"])

    def test_creation_snapshot_visible_in_setup_detail(self) -> None:
        service = self._setup("SNAP_F", MarketSnapshot(symbol="SNAP", price=10.5))
        service.capture("SNAP_F")
        embedded = self.repository.get_setup("SNAP_F")["config"]["creation_market_snapshot"]
        self.assertEqual(embedded["last_price"], 10.5)

    def test_missing_market_data_creates_snapshot_with_warning(self) -> None:
        snapshot = self._setup("SNAP_G").capture("SNAP_G")
        self.assertEqual(snapshot["data_quality_status"], "WARNING")

    def test_setup_can_be_saved_even_if_snapshot_quality_warning(self) -> None:
        service = self._setup("SNAP_H")
        self.assertIsNotNone(service.capture("SNAP_H"))
        self.assertIsNotNone(self.repository.get_setup("SNAP_H"))


class ForecastEnsembleAndModelLabContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = TemporaryDirectory()
        self.database = Database(Path(self.tmp.name) / "state.sqlite")
        self.database.initialize()
        self.repository = TradingRepository(self.database)
        self.consensus = ForecastStackConsensus()
        self.benchmark = ForecastStackBenchmarkService(self.repository)

    def tearDown(self) -> None:
        self.database.close()
        self.tmp.cleanup()

    @staticmethod
    def _forecast(model: str, direction: str, **extra):
        return {
            "model": model,
            "status": "OK",
            "direction": direction,
            "forecast_expected_return_pct": 1.0,
            **extra,
        }

    def test_timesfm_chronos_agreement_boosts_score(self) -> None:
        cards = {
            name: {"reliability_grade": "A", "sample_size": 50} for name in ("timesfm", "chronos")
        }
        result = self.consensus.evaluate(
            [self._forecast("timesfm", "UP"), self._forecast("chronos", "UP")], reliability=cards
        )
        self.assertEqual(result["consensus"], "UP")
        self.assertGreater(result["score_impact"], 0)

    def test_timesfm_chronos_disagreement_creates_warning(self) -> None:
        result = self.consensus.evaluate(
            [self._forecast("timesfm", "UP"), self._forecast("chronos", "DOWN")]
        )
        self.assertIn("TIMESFM_CHRONOS_DIVERGENCE", result["warnings"])
        self.assertEqual(result["consensus"], "MIXED")
        self.assertEqual(result["execution_impact"], "WARNING_ONLY")
        self.assertFalse(result["forecast_eligible_for_execution"])

    def test_lag_llama_high_stop_probability_degrades_score(self) -> None:
        result = self.consensus.evaluate(
            [self._forecast("lag_llama", "UP", prob_touch_stop_before_entry=0.8)]
        )
        self.assertLess(result["score_impact"], 0)

    def test_model_without_accuracy_history_cannot_strongly_boost_score(self) -> None:
        result = self.consensus.evaluate([self._forecast("timesfm", "UP")])
        self.assertLessEqual(result["score_impact"], 0)
        self.assertEqual(result["members"][0]["reliability_status"], "ACCURACY_HISTORY_WARMUP")
        self.assertEqual(result["members"][0]["reliability_grade"], "WARMUP")

    def test_unavailable_model_keeps_operational_block_reason(self) -> None:
        result = self.consensus.evaluate(
            [
                {
                    "model": "lag_llama",
                    "status": "WORKER_NOT_CONFIGURED",
                    "error": "lag_llama external python executable is not configured.",
                }
            ]
        )
        member = result["members"][0]

        self.assertEqual(member["reliability_status"], "NOT_APPLICABLE")
        self.assertEqual(member["reliability_grade"], "NOT_APPLICABLE")
        self.assertFalse(member["eligible_for_display"])
        self.assertEqual(member["execution_block_reason"], "WORKER_NOT_CONFIGURED")
        self.assertEqual(member["direction"], "")
        self.assertIsNone(member["prob_touch_entry"])

    def test_timesfm_score_impact_depends_on_reliability(self) -> None:
        forecast = [self._forecast("timesfm", "UP", forecast_expected_return_pct=2.0)]
        weak = self.consensus.evaluate(forecast)
        strong = self.consensus.evaluate(
            forecast,
            reliability={"timesfm": {"reliability_grade": "A", "sample_size": 50}},
        )
        self.assertGreater(strong["score_impact"], weak["score_impact"])

    def test_forecast_accuracy_never_places_order(self) -> None:
        result = self.consensus.evaluate([self._forecast("timesfm", "UP")])
        self.assertFalse(result["used_for_execution"])

    def test_forecast_stack_never_places_order(self) -> None:
        result = self.consensus.evaluate([self._forecast("timesfm", "UP")])
        self.assertFalse(result["used_for_execution"])
        self.assertNotIn("order", result)

    def test_forecast_stack_not_called_when_setup_management_only_for_entry(self) -> None:
        config = {
            "setup_id": "MGMT",
            "symbol": "MGT",
            "setup_type": "position_management",
            "setup_role": "MANAGEMENT_ONLY",
        }
        self.repository.upsert_setup(
            SetupRecord(
                setup_id="MGMT",
                symbol="MGT",
                setup_type="position_management",
                enabled=True,
                mode="paper",
                status="IN_POSITION",
                entry_zone="",
                stop_loss=9.0,
                risk_amount=10.0,
                order_status="NONE",
                position_status="OPEN",
                last_event="created",
                config=config,
            )
        )
        engine = SetupQualityEngine(self.repository)
        score = engine.score_setup("MGMT")
        self.assertEqual(score["components"]["forecast_alignment_score"], 50.0)
        self.assertEqual(score["forecast_signal"]["status"], "NOT_APPLICABLE_MANAGEMENT_ONLY")

    def _comparison(self, **overrides):
        payload = {
            "symbol": "LAB",
            "timeframe": "15m",
            "horizon_bars": 1,
            "actual": [1, 2, 3, 4, 5, 6],
            "predictions": {
                "naive_baseline": [1, 1, 2, 3, 4, 5],
                "atr_baseline": [1, 1.7, 2.7, 3.7, 4.7, 5.7],
                "timesfm": [1, 2, 3, 4, 5, 6],
            },
            "min_required_samples": 3,
        }
        payload.update(overrides)
        return self.benchmark.compare(payload)

    def test_darts_experiment_created(self) -> None:
        result = self._comparison()
        self.assertIsNotNone(self.benchmark.experiment(result["experiment_id"]))

    def test_native_model_lab_runs_installed_provider_adapter(self) -> None:
        class NativeAdapters:
            def forecast_normalized(self, name, request, config):
                del request, config
                return SimpleNamespace(
                    status="OK",
                    point_forecast=[5.0, 6.0],
                    quantiles={"0.10": [4.5, 5.5], "0.50": [5.0, 6.0], "0.90": [5.5, 6.5]},
                    prob_touch_entry=None,
                    prob_touch_stop_before_entry=None,
                    warnings=[],
                )

        forecast_service = SimpleNamespace(
            adapters=NativeAdapters(),
            config=ForecastConfig(target="price"),
            stack_config=SimpleNamespace(
                providers={
                    "neuralforecast": SimpleNamespace(use_for_model_lab=True),
                }
            ),
        )
        benchmark = ForecastStackBenchmarkService(
            self.repository,
            forecast_service=forecast_service,
        )
        result = benchmark.run_native(
            {
                "symbol": "LAB",
                "series": [1, 2, 3, 4, 5, 6],
                "horizon_bars": 2,
                "models": ["neuralforecast"],
                "min_required_samples": 2,
            }
        )
        names = {item["model_name"] for item in result["results"]}
        self.assertEqual(names, {"neuralforecast", "naive_baseline", "atr_baseline"})

    def test_forecast_stack_comparison_saves_results(self) -> None:
        result = self._comparison()
        self.assertEqual(len(self.benchmark.experiment(result["experiment_id"])["results"]), 3)

    def test_model_must_beat_naive_baseline(self) -> None:
        result = self._comparison()
        evaluation = result["summary"]["selection_policy"]["evaluations"]["timesfm"]
        self.assertTrue(evaluation["beats_naive_baseline"])
        self.assertEqual(result["summary"]["selected_model"], "timesfm")

    def test_model_selection_policy_per_symbol_timeframe_horizon(self) -> None:
        first = self._comparison()
        second = self._comparison(timeframe="1h", horizon_bars=4)
        self.assertNotEqual(first["summary"]["selection_key"], second["summary"]["selection_key"])

    def test_walk_forward_no_data_leakage(self) -> None:
        result = self._comparison(
            validation="walk_forward", walk_forward_folds=[{"train_end": 2, "test_start": 3}]
        )
        self.assertTrue(result["summary"]["validation"]["no_data_leakage"])
        with self.assertRaises(ValueError):
            self._comparison(
                validation="walk_forward", walk_forward_folds=[{"train_end": 3, "test_start": 3}]
            )

    def test_experimental_model_not_selected_without_scorecard(self) -> None:
        predictions = {
            "naive_baseline": [1, 1, 2, 3, 4, 5],
            "atr_baseline": [1, 1.7, 2.7, 3.7, 4.7, 5.7],
            "moirai_uni2ts": [1, 2, 3, 4, 5, 6],
        }
        result = self._comparison(predictions=predictions)
        self.assertIsNone(result["summary"]["selected_model"])


if __name__ == "__main__":
    unittest.main()
