from __future__ import annotations

import json
import sys
from typing import Any

from darts import TimeSeries
from darts import models as darts_models


def main() -> int:
    request = json.loads(sys.stdin.read() or "{}")
    values = [float(value) for value in request.get("values") or []]
    horizon = int(request.get("horizon") or 1)
    if horizon < 1 or len(values) <= horizon:
        _write({"ok": False, "error": "series must contain more observations than horizon"})
        return 0
    constructors = {
        "darts_naive_drift": ("NaiveDrift", {}),
        "darts_naive_seasonal": (
            "NaiveSeasonal",
            {"K": max(1, int(request.get("season_length") or 4))},
        ),
        "darts_theta": ("Theta", {}),
        "darts_exponential_smoothing": ("ExponentialSmoothing", {}),
    }
    requested = request.get("model_names") or ["darts_naive_drift", "darts_theta"]
    train = TimeSeries.from_values(values[:-horizon])
    predictions: dict[str, list[float]] = {}
    try:
        for raw_name in requested:
            name = str(raw_name).strip().lower().replace("-", "_")
            if name not in constructors:
                raise ValueError(f"Unsupported native Darts model: {raw_name}")
            class_name, kwargs = constructors[name]
            model_cls = getattr(darts_models, class_name, None)
            if model_cls is None:
                raise RuntimeError(f"Installed Darts package does not expose {class_name}")
            model = model_cls(**kwargs)
            model.fit(train)
            predictions[name] = _values(model.predict(horizon), horizon)
    except Exception as exc:
        _write({"ok": False, "error": f"{type(exc).__name__}: {exc}"})
        return 0
    _write({"ok": True, "predictions": predictions, "offline_only": True})
    return 0


def _values(series: Any, horizon: int) -> list[float]:
    values = series.values(copy=False) if hasattr(series, "values") else series
    if hasattr(values, "tolist"):
        values = values.tolist()
    result: list[float] = []
    for item in values if isinstance(values, (list, tuple)) else []:
        while isinstance(item, (list, tuple)) and item:
            item = item[0]
        result.append(float(item))
    if len(result) < horizon:
        raise RuntimeError("Darts returned fewer predictions than requested")
    return result[:horizon]


def _write(payload: dict[str, Any]) -> None:
    sys.stdout.write(json.dumps(payload, separators=(",", ":")) + "\n")
    sys.stdout.flush()


if __name__ == "__main__":
    raise SystemExit(main())
