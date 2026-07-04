from __future__ import annotations

import json
import sys
from typing import Any

import numpy as np
import timesfm
import torch


def main() -> int:
    request = json.loads(sys.stdin.read() or "{}")
    series = np.asarray(request.get("series") or [], dtype=np.float32)
    horizon = int(request.get("horizon") or 4)
    model_repo = str(request.get("model_repo") or "google/timesfm-2.5-200m-pytorch")
    if series.size == 0:
        _write({"ok": False, "error": "series is empty"})
        return 0

    torch.set_float32_matmul_precision("high")
    model = timesfm.TimesFM_2p5_200M_torch.from_pretrained(model_repo)
    if hasattr(model, "compile"):
        model.compile(
            timesfm.ForecastConfig(
                max_context=int(request.get("max_context") or 1024),
                max_horizon=max(64, horizon),
                normalize_inputs=True,
                use_continuous_quantile_head=True,
                force_flip_invariance=True,
                infer_is_positive=True,
                fix_quantile_crossing=True,
            )
        )
    point_forecast, quantile_forecast = model.forecast(
        horizon=horizon,
        inputs=[series],
    )
    point_path = _first_batch_path(point_forecast, horizon)
    quantile_rows = _first_batch_rows(quantile_forecast, horizon)
    _write(
        {
            "ok": True,
            "q10_path": _quantile_path(quantile_rows, point_path, "low"),
            "q50_path": _quantile_path(quantile_rows, point_path, "median"),
            "q90_path": _quantile_path(quantile_rows, point_path, "high"),
        }
    )
    return 0


def _write(payload: dict[str, Any]) -> None:
    sys.stdout.write(json.dumps(payload, separators=(",", ":")) + "\n")
    sys.stdout.flush()


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


if __name__ == "__main__":
    raise SystemExit(main())
