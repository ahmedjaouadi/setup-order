from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

from app.forecasting.forecast_models import ForecastConfig, TimesFMForecastOutput


class TimesFMUnavailableError(RuntimeError):
    pass


class TimesFMForecastError(RuntimeError):
    pass


class TimesFMEngine:
    """Thin optional adapter around the official TimesFM package."""

    def __init__(self) -> None:
        self._model: Any | None = None
        self._loaded_repo: str | None = None

    def forecast(
        self,
        series: list[float],
        *,
        horizon: int,
        config: ForecastConfig,
    ) -> TimesFMForecastOutput:
        if config.python_executable:
            return self._forecast_with_external_worker(series, horizon=horizon, config=config)
        model = self._load_model(config)
        try:
            point_forecast, quantile_forecast = model.forecast(
                horizon=horizon,
                inputs=[series],
            )
        except Exception as exc:  # pragma: no cover - depends on optional model runtime.
            raise TimesFMForecastError(str(exc)) from exc
        point_path = _first_batch_path(point_forecast, horizon)
        quantile_rows = _first_batch_rows(quantile_forecast, horizon)
        return TimesFMForecastOutput(
            q10_path=_quantile_path(quantile_rows, point_path, "low"),
            q50_path=_quantile_path(quantile_rows, point_path, "median"),
            q90_path=_quantile_path(quantile_rows, point_path, "high"),
        )

    def _forecast_with_external_worker(
        self,
        series: list[float],
        *,
        horizon: int,
        config: ForecastConfig,
    ) -> TimesFMForecastOutput:
        python = Path(config.python_executable)
        if not python.exists():
            raise TimesFMUnavailableError(f"TimesFM python executable not found: {python}")
        worker = Path(__file__).with_name("timesfm_worker.py")
        request = {
            "series": series,
            "horizon": horizon,
            "model_repo": config.model_repo,
            "max_context": max(config.context_bars, config.min_context_bars),
        }
        try:
            completed = subprocess.run(
                [str(python), str(worker)],
                input=json.dumps(request),
                text=True,
                capture_output=True,
                timeout=config.worker_timeout_seconds,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise TimesFMForecastError(
                f"TimesFM worker timed out after {config.worker_timeout_seconds}s"
            ) from exc
        if completed.returncode != 0:
            detail = (completed.stderr or completed.stdout or "").strip()
            raise TimesFMForecastError(
                detail or f"TimesFM worker exited with {completed.returncode}"
            )
        payload = _json_from_last_stdout_line(completed.stdout)
        if not payload.get("ok"):
            raise TimesFMForecastError(str(payload.get("error") or "TimesFM worker failed"))
        return TimesFMForecastOutput(
            q10_path=[float(value) for value in payload.get("q10_path", [])],
            q50_path=[float(value) for value in payload.get("q50_path", [])],
            q90_path=[float(value) for value in payload.get("q90_path", [])],
        )

    def _load_model(self, config: ForecastConfig) -> Any:
        if self._model is not None and self._loaded_repo == config.model_repo:
            return self._model
        try:
            import timesfm  # type: ignore[import-not-found]
        except Exception as exc:  # pragma: no cover - environment dependent.
            raise TimesFMUnavailableError(
                "TimesFM package is not installed. Install timesfm[torch] to enable forecasts."
            ) from exc

        model_cls = getattr(timesfm, "TimesFM_2p5_200M_torch", None)
        if model_cls is None or not hasattr(model_cls, "from_pretrained"):
            raise TimesFMUnavailableError(
                "Installed TimesFM package does not expose TimesFM_2p5_200M_torch.from_pretrained."
            )
        try:
            self._model = model_cls.from_pretrained(config.model_repo)
            self._loaded_repo = config.model_repo
        except Exception as exc:  # pragma: no cover - depends on local model/cache.
            raise TimesFMUnavailableError(str(exc)) from exc
        self._compile_model_if_supported(self._model, config)
        return self._model

    @staticmethod
    def _compile_model_if_supported(model: Any, config: ForecastConfig) -> None:
        if not hasattr(model, "compile"):
            return
        try:
            import timesfm  # type: ignore[import-not-found]

            model.compile(
                timesfm.ForecastConfig(
                    max_context=max(config.context_bars, config.min_context_bars),
                    max_horizon=max(64, config.horizon_bars),
                    normalize_inputs=True,
                    use_continuous_quantile_head=True,
                    force_flip_invariance=True,
                    infer_is_positive=True,
                    fix_quantile_crossing=True,
                )
            )
        except Exception as exc:  # pragma: no cover - optional runtime.
            raise TimesFMUnavailableError(str(exc)) from exc


def _first_batch_path(payload: Any, horizon: int) -> list[float]:
    rows = _tolist(payload)
    if rows and isinstance(rows[0], list):
        rows = rows[0]
    return [_float_or_zero(item) for item in rows[:horizon]]


def _first_batch_rows(payload: Any, horizon: int) -> list[list[float]]:
    rows = _tolist(payload)
    if rows and isinstance(rows[0], list) and rows[0] and isinstance(rows[0][0], list):
        rows = rows[0]
    output: list[list[float]] = []
    for row in rows[:horizon]:
        if isinstance(row, list):
            output.append([_float_or_zero(item) for item in row])
    return output


def _quantile_path(
    rows: list[list[float]],
    fallback: list[float],
    position: str,
) -> list[float]:
    if not rows:
        return list(fallback)
    values: list[float] = []
    for fallback_value, row in zip(fallback, rows):
        if not row:
            values.append(fallback_value)
        elif position == "low":
            values.append(row[0])
        elif position == "high":
            values.append(row[-1])
        else:
            values.append(row[len(row) // 2])
    return values


def _json_from_last_stdout_line(stdout: str) -> dict[str, Any]:
    for line in reversed([item.strip() for item in stdout.splitlines() if item.strip()]):
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            return payload
    raise TimesFMForecastError("TimesFM worker did not return JSON.")


def _tolist(payload: Any) -> list[Any]:
    if hasattr(payload, "tolist"):
        value = payload.tolist()
        return value if isinstance(value, list) else [value]
    if isinstance(payload, tuple):
        return list(payload)
    if isinstance(payload, list):
        return payload
    return [payload]


def _float_or_zero(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0
