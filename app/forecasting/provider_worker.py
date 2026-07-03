from __future__ import annotations

import json
import math
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.forecasting.adapters import ForecastAdapterRegistry, normalize_provider_output
from app.forecasting.forecast_models import ForecastConfig


def main() -> int:
    request = json.loads(sys.stdin.read() or "{}")
    model_name = str(request.get("model_name") or "").strip().lower().replace("-", "_")
    if not model_name:
        _write({"ok": False, "error": "model_name is required"})
        return 0

    try:
        config = _forecast_config(request.get("config") or {}, model_name)
        adapter = ForecastAdapterRegistry().get(model_name)
        if adapter is None:
            _write({"ok": False, "error": f"Provider is not registered: {model_name}"})
            return 0
        horizon = int(request.get("horizon") or request.get("horizon_bars") or config.horizon_bars)
        target = str(request.get("target") or config.target)
        series, closes = _series_and_closes(request, target=target)
        output = adapter.forecast(
            series,
            closes=closes,
            horizon=horizon,
            target=target,
            config=config,
        )
        normalized = normalize_provider_output(output, horizon)
    except Exception as exc:
        _write({"ok": False, "error": f"{type(exc).__name__}: {exc}"})
        return 0

    _write(
        {
            "ok": True,
            "provider": model_name,
            "status": "OK",
            "point_forecast": normalized.q50_path,
            "q10_path": normalized.q10_path,
            "q50_path": normalized.q50_path,
            "q90_path": normalized.q90_path,
            "quantiles": {
                "0.1": normalized.q10_path,
                "0.5": normalized.q50_path,
                "0.9": normalized.q90_path,
            },
            "direction": _direction(normalized.q50_path),
            "confidence": None,
            "prob_touch_entry": normalized.prob_touch_entry,
            "prob_touch_stop_before_entry": normalized.prob_touch_stop_before_entry,
            "prediction_intervals": normalized.prediction_intervals,
            "warnings": normalized.warnings,
            "error": None,
        }
    )
    return 0


def _series_and_closes(request: dict[str, Any], *, target: str) -> tuple[list[float], list[float]]:
    series = _float_values(request.get("series"))
    closes = _float_values(request.get("closes"))
    if series:
        return series, closes
    candles = request.get("candles")
    if isinstance(candles, list):
        closes = _float_values(
            [item.get("close") for item in candles if isinstance(item, dict)]
        )
        if target == "log_return":
            series = [
                math.log(current / previous)
                for previous, current in zip(closes, closes[1:])
                if previous > 0 and current > 0
            ]
        else:
            series = list(closes)
    return series, closes


def _float_values(values: Any) -> list[float]:
    if not isinstance(values, list | tuple):
        return []
    output = []
    for value in values:
        try:
            number = float(value)
        except (TypeError, ValueError):
            continue
        if math.isfinite(number):
            output.append(number)
    return output


def _direction(path: list[float]) -> str:
    if len(path) < 2:
        return "FLAT"
    delta = path[-1] - path[0]
    if abs(delta) <= 1e-12:
        return "FLAT"
    return "UP" if delta > 0 else "DOWN"


def _forecast_config(payload: Any, model_name: str) -> ForecastConfig:
    data = payload if isinstance(payload, dict) else {}
    provider_options = data.get("provider_options") if isinstance(data.get("provider_options"), dict) else {}
    sanitized_options: dict[str, dict[str, Any]] = {}
    for key, value in provider_options.items():
        if not isinstance(value, dict):
            continue
        options = dict(value)
        if str(key).lower().replace("-", "_") == model_name:
            # The worker must run the provider in-process; keeping the
            # external_worker runtime here would recurse into a worker spawn
            # that has no python_executable and fail as "not configured".
            options.pop("python_executable", None)
            options.pop("worker_script", None)
            options["runtime_mode"] = "in_process"
        sanitized_options[str(key).lower().replace("-", "_")] = options
    allowed = ForecastConfig.__dataclass_fields__.keys()
    config_data = {key: value for key, value in data.items() if key in allowed}
    config_data["provider_options"] = sanitized_options
    return ForecastConfig(**config_data)


def _write(payload: dict[str, Any]) -> None:
    sys.stdout.write(json.dumps(payload, sort_keys=True))
    sys.stdout.write("\n")
    sys.stdout.flush()


if __name__ == "__main__":
    raise SystemExit(main())
