from __future__ import annotations

import unittest
from unittest.mock import patch

from app.forecasting.adapters import (
    AutoGluonAdapter,
    ForecastAdapterRegistry,
    NeuralForecastAdapter,
    adapter_available,
    output_from_prediction_frame,
    output_from_quantile_tensor,
)
from app.forecasting.forecast_cache import ForecastCache
from app.forecasting.forecast_confidence import forecast_confidence
from app.forecasting.forecast_evaluator import ForecastEvaluator
from app.forecasting.forecast_models import ForecastConfig
from app.forecasting.forecast_provider_status import ForecastProviderStatusService
from app.forecasting.forecast_request_builder import ForecastRequestBuilder
from app.forecasting.forecast_result_normalizer import normalize_forecast_result
from app.model_lab.darts_experiment_runner import DartsExperimentRunner
from app.model_lab.model_drift_detector import ModelDriftDetector


class _Frame:
    def __init__(self, values):
        self._values = values
        self.columns = list(values)

    def __getitem__(self, key):
        return self._values[key]


class _Catalog:
    def models(self):
        return {
            "timesfm": {"model": "timesfm", "available": True, "reason": "available"},
            "external_models": [
                {"model": "chronos", "available": True, "reason": "available"},
            ],
            "baselines": [],
        }


class _FakeTimeSeries:
    def __init__(self, values):
        self._values = list(values)

    @classmethod
    def from_values(cls, values):
        return cls(values)

    def values(self, copy=False):
        del copy
        return [[value] for value in self._values]


class _FakeDartsModel:
    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.train = None

    def fit(self, train):
        self.train = train
        return self

    def predict(self, horizon):
        return _FakeTimeSeries([self.train._values[-1]] * horizon)


class ForecastStackMissingModulesTests(unittest.TestCase):
    def test_request_builder_validates_and_normalizes(self):
        request = ForecastRequestBuilder().build(
            symbol="abc", timeframe="15m", horizon_bars=2, bars=[{"close": "10"}]
        )
        self.assertEqual(request["symbol"], "ABC")
        self.assertEqual(request["series"], [10.0])

    def test_result_normalizer_has_common_contract(self):
        result = normalize_forecast_result(
            {"point_forecast": [1, 2]},
            model_name="test",
            symbol="abc",
            timeframe="15m",
            horizon_bars=2,
        )
        self.assertEqual(result.direction, "UP")
        self.assertEqual(result.status, "OK")

    def test_cache_invalidates(self):
        cache = ForecastCache()
        cache.set("key", {"ok": True})
        self.assertIsNotNone(cache.get("key"))
        cache.invalidate("key")
        self.assertIsNone(cache.get("key"))

    def test_confidence_requires_reliability_for_strong_boost(self):
        value = forecast_confidence(
            {"direction_confidence": 0.9}, {"reliability_grade": "A", "sample_size": 30}
        )
        self.assertTrue(value["eligible_for_strong_boost"])

    def test_evaluator_metrics(self):
        metrics = ForecastEvaluator().evaluate([1, 2, 3], [1, 2, 4])
        self.assertEqual(metrics["sample_size"], 3)
        self.assertGreater(metrics["rmse"], 0)

    def test_drift_detector(self):
        drift = ModelDriftDetector().detect(
            {"direction_accuracy": 0.5, "mae": 2}, {"direction_accuracy": 0.65, "mae": 1}
        )
        self.assertTrue(drift["drift_detected"])

    def test_chronos_current_quantile_tensor_is_normalized(self):
        output = output_from_quantile_tensor(
            [[[1.0, 2.0, 3.0], [2.0, 3.0, 4.0]]],
            [[2.1, 3.1]],
            2,
        )
        self.assertEqual(output.q10_path, [1.0, 2.0])
        self.assertEqual(output.q50_path, [2.1, 3.1])
        self.assertEqual(output.q90_path, [3.0, 4.0])

    def test_prediction_frame_quantiles_are_normalized(self):
        output = output_from_prediction_frame(
            _Frame({"mean": [10, 11], "0.1": [9, 10], "0.9": [11, 12]}),
            2,
        )
        self.assertEqual(output.q50_path, [10.0, 11.0])
        self.assertEqual(output.q10_path, [9.0, 10.0])

    def test_neuralforecast_is_ready_without_saved_model_path(self):
        with (
            patch("app.forecasting.adapters._module_available", return_value=True),
            patch(
                "app.forecasting.adapters.importlib.import_module",
                return_value=object(),
            ),
        ):
            self.assertEqual(
                adapter_available(NeuralForecastAdapter(), ForecastConfig()),
                (True, "available"),
            )

    def test_autogluon_is_ready_without_saved_model_path(self):
        with (
            patch("app.forecasting.adapters._module_available", return_value=True),
            patch(
                "app.forecasting.adapters.importlib.import_module",
                return_value=object(),
            ),
        ):
            self.assertEqual(
                adapter_available(AutoGluonAdapter(), ForecastConfig()),
                (True, "available"),
            )

    def test_provider_auto_activates_only_when_ready(self):
        service = ForecastProviderStatusService(
            {
                "forecast_stack": {
                    "providers": {
                        "chronos": {
                            "enabled": False,
                            "auto_enable_when_ready": True,
                            "role": "direct_competitor",
                        }
                    }
                }
            },
            _Catalog(),
        )
        provider = service.list()["providers"][0]
        self.assertEqual(provider["status"], "OK")
        self.assertEqual(provider["worker_status"], "WORKER_READY")
        self.assertEqual(provider["reliability_status"], "ACCURACY_HISTORY_WARMUP")
        self.assertEqual(provider["reliability_grade"], "WARMUP")
        self.assertTrue(provider["available"])
        self.assertFalse(provider["use_for_execution"])

    def test_provider_disabled_by_config_is_reported_explicitly(self):
        service = ForecastProviderStatusService(
            {
                "forecast_stack": {
                    "providers": {
                        "chronos": {
                            "enabled": False,
                            "auto_enable_when_ready": False,
                            "role": "direct_competitor",
                        }
                    }
                }
            },
            _Catalog(),
        )
        provider = service.list()["providers"][0]
        self.assertEqual(provider["status"], "DISABLED_BY_CONFIG")
        self.assertFalse(provider["available"])
        self.assertEqual(provider["forecast_status"], "NOT_RUN")
        self.assertEqual(provider["reliability_status"], "NOT_APPLICABLE")

    def test_registry_uses_bundled_probabilistic_bridges(self):
        registry = ForecastAdapterRegistry()
        self.assertIn(
            "provider_bridges:lag_llama_forecast", registry.get("lag_llama").default_callable
        )
        self.assertIn(
            "provider_bridges:moirai_uni2ts_forecast",
            registry.get("moirai_uni2ts").default_callable,
        )

    def test_darts_native_models_generate_holdout_predictions(self):
        fake_darts = type("Darts", (), {"TimeSeries": _FakeTimeSeries})
        fake_models = type(
            "Models",
            (),
            {
                "NaiveDrift": _FakeDartsModel,
                "NaiveSeasonal": _FakeDartsModel,
                "Theta": _FakeDartsModel,
                "ExponentialSmoothing": _FakeDartsModel,
            },
        )

        def import_module(name):
            return fake_darts if name == "darts" else fake_models

        with (
            patch(
                "app.model_lab.darts_experiment_runner.importlib.util.find_spec",
                return_value=object(),
            ),
            patch(
                "app.model_lab.darts_experiment_runner.importlib.import_module",
                side_effect=import_module,
            ),
        ):
            predictions = DartsExperimentRunner().forecast_models(
                [1, 2, 3, 4, 5],
                horizon=2,
                model_names=["darts_naive_drift", "darts_theta"],
            )
        self.assertEqual(predictions["darts_naive_drift"], [3.0, 3.0])
        self.assertEqual(predictions["darts_theta"], [3.0, 3.0])


if __name__ == "__main__":
    unittest.main()
