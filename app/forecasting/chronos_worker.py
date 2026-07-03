from __future__ import annotations

import json
import os
import sys
from typing import Any

import torch
import chronos


def main() -> int:
    request = json.loads(sys.stdin.read() or "{}")
    series = [float(value) for value in request.get("series") or []]
    horizon = int(request.get("horizon") or 4)
    if not series:
        _write({"ok": False, "error": "series is empty"})
        return 0
    if horizon < 1:
        _write({"ok": False, "error": "horizon must be positive"})
        return 0

    pipeline_cls = (
        getattr(chronos, "Chronos2Pipeline", None)
        or getattr(chronos, "BaseChronosPipeline", None)
        or getattr(chronos, "ChronosPipeline", None)
    )
    if pipeline_cls is None:
        _write({"ok": False, "error": "Chronos pipeline class is unavailable"})
        return 0

    model_repo = str(request.get("model_repo") or "amazon/chronos-2")
    device = _device(str(request.get("device") or "auto"))
    dtype_name = str(request.get("torch_dtype") or "bfloat16")
    load_kwargs: dict[str, Any] = {"device_map": device}
    dtype = getattr(torch, dtype_name, None)
    if dtype is not None:
        load_kwargs["torch_dtype"] = dtype

    try:
        pipeline = pipeline_cls.from_pretrained(model_repo, **load_kwargs)
        context = torch.tensor(series, dtype=torch.float32)
        predict_quantiles = getattr(pipeline, "predict_quantiles", None)
        if callable(predict_quantiles):
            arguments: dict[str, Any] = {
                "prediction_length": horizon,
                "quantile_levels": [0.1, 0.5, 0.9],
            }
            if getattr(chronos, "Chronos2Pipeline", None) is pipeline_cls:
                arguments["inputs"] = context.reshape(1, 1, -1)
            else:
                arguments["context"] = context
            quantiles, mean = predict_quantiles(**arguments)
            q10, q50, q90 = _quantile_paths(quantiles, mean, horizon)
        else:
            samples = pipeline.predict(
                context,
                prediction_length=horizon,
                num_samples=int(request.get("num_samples") or 100),
            )
            q10, q50, q90 = _sample_paths(samples, horizon)
    except Exception as exc:
        _write({"ok": False, "error": f"{type(exc).__name__}: {exc}"})
        return 0

    token_env = str(request.get("hf_token_env") or "HF_TOKEN")
    warnings = [] if os.getenv(token_env) else [
        f"{token_env} is not set; cached models still work and online downloads may be rate-limited."
    ]
    _write(
        {
            "ok": True,
            "q10_path": q10,
            "q50_path": q50,
            "q90_path": q90,
            "warnings": warnings,
        }
    )
    return 0


def _quantile_paths(
    quantiles: Any,
    mean: Any,
    horizon: int,
) -> tuple[list[float], list[float], list[float]]:
    values = _nested_list(quantiles)
    while isinstance(values, list) and len(values) == 1 and isinstance(values[0], list):
        values = values[0]
    rows = values if isinstance(values, list) else []
    if rows and len(rows) == 3 and all(
        isinstance(row, list) and len(row) >= horizon for row in rows
    ):
        q10, q50, q90 = (_float_list(row)[:horizon] for row in rows)
    elif rows and all(
        isinstance(row, list) and len(row) >= 3 for row in rows[:horizon]
    ):
        matrix = [_float_list(row) for row in rows[:horizon]]
        q10 = [row[0] for row in matrix]
        q50 = [row[1] for row in matrix]
        q90 = [row[2] for row in matrix]
    else:
        return _sample_paths(mean, horizon)
    mean_values = _flatten_vector(mean)
    if len(mean_values) >= horizon:
        q50 = mean_values[:horizon]
    return _complete(q10, q50, q90, horizon)


def _sample_paths(samples: Any, horizon: int) -> tuple[list[float], list[float], list[float]]:
    values = _nested_list(samples)
    while isinstance(values, list) and len(values) == 1 and isinstance(values[0], list):
        values = values[0]
    if not isinstance(values, list) or not values:
        raise RuntimeError("Chronos returned no forecast values")
    if all(not isinstance(value, list) for value in values):
        point = _float_list(values)[:horizon]
        return _complete(point, point, point, horizon)
    rows = [_float_list(row) for row in values if isinstance(row, list)]
    if rows and len(rows) == horizon:
        per_step = rows
    elif rows and all(len(row) >= horizon for row in rows):
        per_step = [[row[index] for row in rows] for index in range(horizon)]
    else:
        raise RuntimeError("Chronos returned an unsupported forecast shape")
    q10 = [_quantile(row, 0.1) for row in per_step]
    q50 = [_quantile(row, 0.5) for row in per_step]
    q90 = [_quantile(row, 0.9) for row in per_step]
    return _complete(q10, q50, q90, horizon)


def _complete(
    q10: list[float],
    q50: list[float],
    q90: list[float],
    horizon: int,
) -> tuple[list[float], list[float], list[float]]:
    if len(q50) < horizon:
        raise RuntimeError("Chronos returned fewer predictions than requested")
    q50 = q50[:horizon]
    q10 = q10[:horizon] if len(q10) >= horizon else list(q50)
    q90 = q90[:horizon] if len(q90) >= horizon else list(q50)
    ordered = [sorted((low, median, high)) for low, median, high in zip(q10, q50, q90)]
    return (
        [row[0] for row in ordered],
        [row[1] for row in ordered],
        [row[2] for row in ordered],
    )


def _nested_list(value: Any) -> Any:
    if hasattr(value, "detach"):
        value = value.detach()
    if hasattr(value, "cpu"):
        value = value.cpu()
    if hasattr(value, "tolist"):
        return value.tolist()
    if isinstance(value, tuple):
        return [_nested_list(item) for item in value]
    if isinstance(value, list):
        return [_nested_list(item) for item in value]
    return value


def _flatten_vector(value: Any) -> list[float]:
    values = _nested_list(value)
    while isinstance(values, list) and len(values) == 1 and isinstance(values[0], list):
        values = values[0]
    return _float_list(values) if isinstance(values, list) else []


def _float_list(values: list[Any]) -> list[float]:
    result = []
    for value in values:
        if isinstance(value, list):
            continue
        result.append(float(value))
    return result


def _quantile(values: list[float], level: float) -> float:
    ordered = sorted(values)
    if not ordered:
        raise RuntimeError("Chronos returned an empty sample path")
    position = (len(ordered) - 1) * level
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)


def _device(value: str) -> str:
    normalized = value.strip().lower()
    if normalized == "auto":
        return "cuda" if torch.cuda.is_available() else "cpu"
    return normalized if normalized in {"cpu", "cuda", "mps"} else "cpu"


def _write(payload: dict[str, Any]) -> None:
    sys.stdout.write(json.dumps(payload, separators=(",", ":")) + "\n")
    sys.stdout.flush()


if __name__ == "__main__":
    raise SystemExit(main())
