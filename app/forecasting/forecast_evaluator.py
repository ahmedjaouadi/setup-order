from __future__ import annotations

import math
from typing import Any


class ForecastEvaluator:
    def evaluate(self, actual: list[float], predicted: list[float]) -> dict[str, Any]:
        pairs = [(float(a), float(p)) for a, p in zip(actual, predicted)]
        if not pairs:
            return {"status": "INSUFFICIENT_DATA", "sample_size": 0}
        errors = [p - a for a, p in pairs]
        direction_hits = [
            (pairs[i][0] - pairs[i - 1][0]) * (pairs[i][1] - pairs[i - 1][1]) >= 0
            for i in range(1, len(pairs))
        ]
        return {
            "status": "OK",
            "sample_size": len(pairs),
            "mae": sum(abs(e) for e in errors) / len(errors),
            "rmse": math.sqrt(sum(e * e for e in errors) / len(errors)),
            "mape": sum(abs(e / a) for (a, _), e in zip(pairs, errors) if a)
            / max(1, sum(1 for a, _ in pairs if a)),
            "direction_accuracy": (
                sum(direction_hits) / len(direction_hits) if direction_hits else None
            ),
        }
