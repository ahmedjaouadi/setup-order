from __future__ import annotations

import json
import os
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch

from app.forecasting.adapters import ChronosAdapter, adapter_available
from app.forecasting.forecast_models import ForecastConfig
from app.forecasting.forecast_repository import ForecastRepository
from app.model_lab.darts_experiment_runner import DartsExperimentRunner
from app.settings import load_local_env
from app.storage.database import Database


class ChronosDartsIntegrationTests(unittest.TestCase):
    def test_chronos_external_worker_is_normalized_and_non_executing(self) -> None:
        config = ForecastConfig(
            target="price",
            provider_options={
                "chronos": {
                    "python_executable": sys.executable,
                    "model_repo": "amazon/chronos-2",
                }
            },
        )
        completed = SimpleNamespace(
            returncode=0,
            stdout=json.dumps(
                {
                    "ok": True,
                    "q10_path": [10.0, 11.0],
                    "q50_path": [11.0, 12.0],
                    "q90_path": [12.0, 13.0],
                    "warnings": ["HF_TOKEN is not set"],
                }
            ),
            stderr="",
        )
        with patch("app.forecasting.adapters.subprocess.run", return_value=completed):
            output = ChronosAdapter().forecast(
                [8.0, 9.0],
                closes=[8.0, 9.0],
                horizon=2,
                target="price",
                config=config,
            )
        self.assertEqual(output.q50_path, [11.0, 12.0])
        self.assertTrue(output.warnings)
        self.assertEqual(
            adapter_available(ChronosAdapter(), config), (True, "external worker configured")
        )

    def test_darts_external_worker_remains_model_lab_only(self) -> None:
        completed = SimpleNamespace(
            returncode=0,
            stdout=json.dumps(
                {
                    "ok": True,
                    "predictions": {"darts_naive_drift": [4.0, 5.0]},
                    "offline_only": True,
                }
            ),
            stderr="",
        )
        runner = DartsExperimentRunner(python_executable=sys.executable)
        with patch(
            "app.model_lab.darts_experiment_runner.subprocess.run",
            return_value=completed,
        ):
            predictions = runner.forecast_models(
                [1.0, 2.0, 3.0, 4.0],
                horizon=2,
                model_names=["darts_naive_drift"],
            )
        self.assertEqual(predictions["darts_naive_drift"], [4.0, 5.0])
        self.assertFalse(runner.provider_status()["runtime_allowed"])

    def test_forecast_is_mirrored_to_forecast_runs_with_execution_blocked(self) -> None:
        with TemporaryDirectory() as folder:
            database = Database(Path(folder) / "state.sqlite")
            database.initialize()
            repository = ForecastRepository(database)
            payload = {
                "symbol": "ABC",
                "timeframe": "15m",
                "model": "chronos",
                "target": "price",
                "context_bars": 64,
                "horizon_bars": 2,
                "generated_at": "2026-06-21T10:00:00+00:00",
                "status": "OK",
                "forecast_status": "BULLISH",
                "metric_score": 75,
                "confidence": "MEDIUM",
            }
            try:
                repository.insert_forecast(payload)
                run = repository.get_forecast_run(payload["forecast_id"])
            finally:
                database.close()
        self.assertIsNotNone(run)
        self.assertFalse(run["forecast"]["execution_allowed"])
        self.assertFalse(run["forecast"]["used_for_decision"])

    def test_local_env_does_not_override_process_environment(self) -> None:
        key = "SETUP_ORDER_TEST_HF_TOKEN"
        previous = os.environ.get(key)
        try:
            os.environ[key] = "already-set"
            with TemporaryDirectory() as folder:
                env_file = Path(folder) / ".env"
                env_file.write_text(f"{key}=from-file\n", encoding="utf-8")
                load_local_env(env_file)
            self.assertEqual(os.environ[key], "already-set")
        finally:
            if previous is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = previous


if __name__ == "__main__":
    unittest.main()
