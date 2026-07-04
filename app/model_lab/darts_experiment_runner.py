from __future__ import annotations

import importlib
import importlib.util
import json
import math
import subprocess
from pathlib import Path
from typing import Any

from app.forecasting.provider_statuses import (
    AVAILABLE,
    EXTERNAL_WORKER_CONFIGURED,
    LOAD_ERROR,
    MISSING_DEPENDENCY,
    WORKER_UNREACHABLE,
)


class DartsExperimentRunner:
    """Offline metric runner. Darts remains optional and is never used in live trading."""

    def __init__(
        self,
        *,
        python_executable: str = "",
        worker_timeout_seconds: int = 180,
    ) -> None:
        self.python_executable = str(python_executable or "").strip()
        self.worker_timeout_seconds = max(1, int(worker_timeout_seconds))

    def provider_status(self) -> dict[str, Any]:
        external_configured = bool(self.python_executable)
        external = bool(external_configured and Path(self.python_executable).is_file())
        if external_configured:
            installed = external
            status = EXTERNAL_WORKER_CONFIGURED if external else WORKER_UNREACHABLE
            reason = (
                "external Darts worker configured for offline Model Lab only"
                if external
                else f"Darts python executable not found: {self.python_executable}"
            )
        else:
            try:
                installed = importlib.util.find_spec("darts") is not None
            except ModuleNotFoundError:
                installed = False
                status = MISSING_DEPENDENCY
                reason = "Missing optional package(s): darts"
            except Exception as exc:
                installed = True
                status = LOAD_ERROR
                reason = f"Darts package probe failed: {exc}"
            else:
                status = AVAILABLE if installed else MISSING_DEPENDENCY
                reason = "available" if installed else "Missing optional package(s): darts"
        return {
            "model_name": "darts",
            "installed": installed,
            "status": status,
            "reason": reason,
            "runtime_allowed": False,
            "model_lab_only": True,
            "execution_mode": "external_worker" if external_configured else "in_process",
            "supported_models": [
                "darts_naive_drift",
                "darts_naive_seasonal",
                "darts_theta",
                "darts_exponential_smoothing",
            ],
        }

    def forecast_models(
        self,
        values: list[float],
        *,
        horizon: int,
        model_names: list[str] | tuple[str, ...] | None = None,
        season_length: int = 4,
    ) -> dict[str, list[float]]:
        """Fit native Darts models for an offline holdout experiment."""
        if horizon < 1 or len(values) <= horizon:
            raise ValueError("series must contain more observations than horizon_bars")
        if self.python_executable:
            return self._forecast_with_external_worker(
                values,
                horizon=horizon,
                model_names=model_names,
                season_length=season_length,
            )
        if importlib.util.find_spec("darts") is None:
            raise RuntimeError("Optional package 'u8darts' is not installed.")
        darts_module = importlib.import_module("darts")
        models_module = importlib.import_module("darts.models")
        time_series_cls = darts_module.TimeSeries
        train = time_series_cls.from_values(values[:-horizon])
        requested = list(model_names or ("darts_naive_drift", "darts_theta"))
        constructors = {
            "darts_naive_drift": ("NaiveDrift", {}),
            "darts_naive_seasonal": ("NaiveSeasonal", {"K": max(1, season_length)}),
            "darts_theta": ("Theta", {}),
            "darts_exponential_smoothing": ("ExponentialSmoothing", {}),
        }
        predictions: dict[str, list[float]] = {}
        for raw_name in requested:
            name = str(raw_name).strip().lower().replace("-", "_")
            if name not in constructors:
                raise ValueError(f"Unsupported native Darts model: {raw_name}")
            class_name, kwargs = constructors[name]
            model_cls = getattr(models_module, class_name, None)
            if model_cls is None:
                raise RuntimeError(f"Installed Darts package does not expose {class_name}.")
            model = model_cls(**kwargs)
            model.fit(train)
            predictions[name] = _darts_values(model.predict(horizon), horizon)
        return predictions

    def _forecast_with_external_worker(
        self,
        values: list[float],
        *,
        horizon: int,
        model_names: list[str] | tuple[str, ...] | None,
        season_length: int,
    ) -> dict[str, list[float]]:
        python = Path(self.python_executable)
        if not python.is_file():
            raise RuntimeError(f"Darts python executable not found: {python}")
        worker = Path(__file__).with_name("darts_worker.py")
        request = {
            "values": values,
            "horizon": horizon,
            "model_names": list(model_names or ("darts_naive_drift", "darts_theta")),
            "season_length": season_length,
        }
        try:
            completed = subprocess.run(
                [str(python), str(worker)],
                input=json.dumps(request),
                text=True,
                capture_output=True,
                timeout=self.worker_timeout_seconds,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError(
                f"Darts worker timed out after {self.worker_timeout_seconds}s"
            ) from exc
        if completed.returncode != 0:
            detail = (completed.stderr or completed.stdout or "").strip()
            raise RuntimeError(detail or f"Darts worker exited with {completed.returncode}")
        payload = _json_from_last_stdout_line(completed.stdout)
        if not payload.get("ok"):
            raise RuntimeError(str(payload.get("error") or "Darts worker failed"))
        raw_predictions = payload.get("predictions")
        if not isinstance(raw_predictions, dict):
            raise RuntimeError("Darts worker returned no predictions")
        return {
            str(name): [float(value) for value in path]
            for name, path in raw_predictions.items()
            if isinstance(path, list)
        }

    def evaluate(
        self,
        actual: list[float],
        predicted: list[float],
        *,
        quantiles: dict[str, list[float]] | None = None,
        probabilities: dict[str, Any] | None = None,
        actual_events: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        size = min(len(actual), len(predicted))
        if size < 2:
            return {"sample_size": size, "status": "INSUFFICIENT_DATA"}
        actual, predicted = actual[:size], predicted[:size]
        errors = [forecast - observed for forecast, observed in zip(predicted, actual)]
        percentage = [
            abs(error) / abs(observed) for error, observed in zip(errors, actual) if observed
        ]
        actual_dirs = [_direction(actual[i] - actual[i - 1]) for i in range(1, size)]
        forecast_dirs = [_direction(predicted[i] - actual[i - 1]) for i in range(1, size)]
        metrics = {
            "status": "OK",
            "sample_size": size,
            "mae": sum(abs(value) for value in errors) / size,
            "rmse": math.sqrt(sum(value * value for value in errors) / size),
            "mape": sum(percentage) / len(percentage) if percentage else None,
            "direction_accuracy": sum(a == b for a, b in zip(actual_dirs, forecast_dirs))
            / len(actual_dirs),
        }
        lower = _quantile_path(quantiles, "0.10", "0.1", "q10")
        upper = _quantile_path(quantiles, "0.90", "0.9", "q90")
        coverage_count = min(size, len(lower), len(upper))
        if coverage_count:
            coverage = (
                sum(
                    lower[index] <= actual[index] <= upper[index] for index in range(coverage_count)
                )
                / coverage_count
            )
            metrics["quantile_coverage"] = coverage
            metrics["calibration_error"] = abs(0.8 - coverage)
        else:
            metrics["quantile_coverage"] = None
            metrics["calibration_error"] = None
        probabilities = probabilities or {}
        actual_events = actual_events or {}
        metrics["brier_score_touch_entry"] = _brier(
            probabilities.get("prob_touch_entry"),
            actual_events.get("entry_touched"),
        )
        metrics["brier_score_touch_stop"] = _brier(
            probabilities.get("prob_touch_stop_before_entry"),
            actual_events.get("stop_before_entry"),
        )
        return metrics

    def validate_walk_forward(self, folds: list[dict[str, Any]]) -> dict[str, Any]:
        checked = 0
        for fold in folds:
            train_end = int(fold.get("train_end", -1))
            test_start = int(fold.get("test_start", -1))
            if train_end < 0 or test_start < 0 or train_end >= test_start:
                raise ValueError("Walk-forward leakage: train_end must be before test_start")
            checked += 1
        if not checked:
            raise ValueError("walk_forward_folds are required")
        return {"status": "OK", "fold_count": checked, "no_data_leakage": True}


def _direction(delta: float) -> str:
    return "UP" if delta > 0 else "DOWN" if delta < 0 else "FLAT"


def _quantile_path(
    quantiles: dict[str, list[float]] | None,
    *keys: str,
) -> list[float]:
    if not isinstance(quantiles, dict):
        return []
    for key in keys:
        values = quantiles.get(key)
        if isinstance(values, list):
            return [float(value) for value in values]
    return []


def _brier(probability: Any, actual: Any) -> float | None:
    if probability is None or actual is None:
        return None
    value = max(0.0, min(1.0, float(probability)))
    return (value - float(bool(actual))) ** 2


def _darts_values(series: Any, horizon: int) -> list[float]:
    values = series.values(copy=False) if hasattr(series, "values") else series
    if hasattr(values, "tolist"):
        values = values.tolist()
    result: list[float] = []
    for item in values if isinstance(values, list | tuple) else []:
        while isinstance(item, list | tuple) and item:
            item = item[0]
        try:
            result.append(float(item))
        except (TypeError, ValueError):
            continue
    if len(result) < horizon:
        raise RuntimeError("Darts returned fewer predictions than requested.")
    return result[:horizon]


def _json_from_last_stdout_line(stdout: str) -> dict[str, Any]:
    for line in reversed([item.strip() for item in stdout.splitlines() if item.strip()]):
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            return payload
    raise RuntimeError("Darts worker did not return JSON")
