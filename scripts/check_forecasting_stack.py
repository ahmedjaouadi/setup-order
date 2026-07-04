from __future__ import annotations

import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.forecasting.forecast_provider_status import ForecastProviderStatusService
from app.forecasting.forecast_service import ForecastService
from app.settings import load_settings


def main() -> None:
    settings = load_settings()
    forecast_service = ForecastService(
        settings=settings.raw,
        repository=None,
        trading_repository=object(),
        market_history_provider=lambda *args, **kwargs: {},
    )
    catalog = forecast_service.models()
    provider_status = ForecastProviderStatusService(settings.raw, forecast_service).list()
    catalog_rows = [catalog["timesfm"], *catalog["external_models"], *catalog["baselines"]]
    print(
        json.dumps(
            {
                "execution_mode": provider_status["execution_mode"],
                "model_catalog": [
                    {
                        "model": row["model"],
                        "status": row["status"],
                        "available": row["available"],
                        "reason": row["reason"],
                        "baseline": row["baseline"],
                    }
                    for row in catalog_rows
                ],
                "providers": [
                    {
                        "model_name": item["model_name"],
                        "status": item["status"],
                        "available": item["available"],
                        "configured": item["configured"],
                        "reason": item["unavailable_reason"],
                        "auto_enable_when_ready": item["activation_mode"] == "AUTO_WHEN_READY",
                        "runtime_allowed": item["use_for_execution"],
                        "model_lab_allowed": item["use_for_model_lab"],
                    }
                    for item in provider_status["providers"]
                ],
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
