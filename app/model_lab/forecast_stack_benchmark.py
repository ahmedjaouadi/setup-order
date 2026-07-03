from __future__ import annotations

from typing import Any

from app.model_lab.darts_experiment_runner import DartsExperimentRunner
from app.model_lab.model_selection_policy import ForecastModelSelectionPolicy
from app.models import utc_now_iso
from app.storage.repositories import TradingRepository
from app.utils.id_generator import new_id


class ForecastStackBenchmarkService:
    def __init__(self, repository: TradingRepository, forecast_service: Any | None = None) -> None:
        self.repository = repository
        self.forecast_service = forecast_service
        darts_options: dict[str, Any] = {}
        if forecast_service is not None:
            provider = forecast_service.stack_config.providers.get("darts")
            if provider is not None and isinstance(provider.options, dict):
                darts_options = provider.options
        self.runner = DartsExperimentRunner(
            python_executable=str(darts_options.get("python_executable") or ""),
            worker_timeout_seconds=int(darts_options.get("worker_timeout_seconds") or 180),
        )
        self.selection_policy = ForecastModelSelectionPolicy()

    def run_native(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Build holdout predictions with installed Darts models, then compare them."""
        series = _numbers(payload.get("series", payload.get("actual")))
        horizon = int(payload.get("horizon_bars") or 1)
        if len(series) <= horizon:
            raise ValueError("series must contain more observations than horizon_bars")
        raw_models = payload.get("models")
        models = (
            [str(item).strip().lower().replace("-", "_") for item in raw_models]
            if isinstance(raw_models, list)
            else ["darts_naive_drift", "darts_theta"]
        )
        darts_models = [name for name in models if name.startswith("darts_")]
        provider_models = [
            name for name in models
            if name not in {"naive_baseline", "atr_baseline"} and not name.startswith("darts_")
        ]
        predictions = (
            self.runner.forecast_models(
                series,
                horizon=horizon,
                model_names=darts_models,
                season_length=int(payload.get("season_length") or 4),
            )
            if darts_models
            else {}
        )
        train = series[:-horizon]
        actual = series[-horizon:]
        predictions.update(self._provider_predictions(
            provider_models,
            train,
            horizon=horizon,
            symbol=str(payload.get("symbol") or "LAB").upper(),
            timeframe=str(payload.get("timeframe") or "15m"),
        ))
        last = train[-1]
        drift = train[-1] - train[-2] if len(train) > 1 else 0.0
        predictions["naive_baseline"] = [last] * horizon
        predictions["atr_baseline"] = [last + drift * (index + 1) for index in range(horizon)]
        return self.compare({
            **payload,
            "actual": actual,
            "predictions": predictions,
            "framework": "darts_offline_native",
            "validation": payload.get("validation") or "holdout",
        })

    def _provider_predictions(
        self,
        model_names: list[str],
        series: list[float],
        *,
        horizon: int,
        symbol: str,
        timeframe: str,
    ) -> dict[str, dict[str, Any]]:
        if not model_names:
            return {}
        if self.forecast_service is None:
            raise RuntimeError("Forecast providers are not connected to Model Lab.")
        predictions: dict[str, dict[str, Any]] = {}
        for name in model_names:
            provider = self.forecast_service.stack_config.providers.get(name)
            if provider is not None and not provider.use_for_model_lab:
                raise ValueError(f"Provider is not allowed in Model Lab: {name}")
            result = self.forecast_service.adapters.forecast_normalized(
                name,
                {
                    "symbol": symbol,
                    "timeframe": timeframe,
                    "horizon_bars": horizon,
                    "target": "price",
                    "series": series,
                    "closes": series,
                },
                self.forecast_service.config,
            )
            if result.status != "OK" or not result.point_forecast:
                detail = "; ".join(result.warnings) or result.status
                raise RuntimeError(f"Provider {name} is not ready: {detail}")
            predictions[name] = {
                "point_forecast": list(result.point_forecast),
                "quantiles": result.quantiles or {},
                "prob_touch_entry": result.prob_touch_entry,
                "prob_touch_stop_before_entry": result.prob_touch_stop_before_entry,
            }
        return predictions

    def compare(self, payload: dict[str, Any]) -> dict[str, Any]:
        symbol = str(payload.get("symbol") or "").upper()
        if not symbol:
            raise ValueError("symbol is required")
        actual = _numbers(payload.get("actual"))
        predictions = payload.get("predictions") if isinstance(payload.get("predictions"), dict) else {}
        if not predictions:
            raise ValueError("predictions by model are required")
        timeframe = str(payload.get("timeframe") or "15m")
        horizon = int(payload.get("horizon_bars") or 1)
        validation: dict[str, Any] = {"mode": "holdout", "no_data_leakage": None}
        if payload.get("validation") == "walk_forward":
            folds = payload.get("walk_forward_folds")
            if not isinstance(folds, list) or not folds:
                folds = _default_walk_forward_folds(len(actual))
            validation = {"mode": "walk_forward", **self.runner.validate_walk_forward(folds)}
        experiment_id = new_id("fsx")
        started = utc_now_iso()
        experiment = {
            "experiment_id": experiment_id, "name": payload.get("name") or f"{symbol} forecast stack",
            "symbols": [symbol], "timeframes": [timeframe], "horizons": [horizon],
            "models": list(predictions), "config": {**payload, "execution_mode": "offline_only"},
            "status": "RUNNING", "started_at": started,
        }
        self.repository.add_forecast_stack_experiment(experiment)
        results: list[dict[str, Any]] = []
        for model_name, values in predictions.items():
            model_payload = values if isinstance(values, dict) else {"point_forecast": values}
            point_forecast = _numbers(
                model_payload.get("point_forecast", model_payload.get("predictions", model_payload.get("q50", values)))
            )
            metrics = self.runner.evaluate(
                actual,
                point_forecast,
                quantiles=model_payload.get("quantiles"),
                probabilities=model_payload,
                actual_events=payload.get("actual_events") if isinstance(payload.get("actual_events"), dict) else None,
            )
            trading_metrics = _trading_metrics(
                payload.get("trading_metrics", {}).get(model_name, {})
                if isinstance(payload.get("trading_metrics"), dict) else {}
            )
            results.append({
                "model_name": str(model_name),
                "metrics": metrics,
                "trading_metrics": trading_metrics,
            })
        ranked = sorted(results, key=lambda item: (item["metrics"].get("status") != "OK", item["metrics"].get("mae", float("inf"))))
        policy = self.selection_policy.select(
            ranked,
            min_samples=int(payload.get("min_required_samples") or 30),
            experimental_scorecards=(
                payload.get("experimental_scorecards")
                if isinstance(payload.get("experimental_scorecards"), dict) else {}
            ),
        )
        persisted = []
        for rank, item in enumerate(ranked, start=1):
            result = {
                "result_id": new_id("fsr"), "experiment_id": experiment_id,
                "model_name": item["model_name"], "symbol": symbol, "timeframe": timeframe,
                "horizon_bars": horizon,
                "metrics": {
                    **item["metrics"],
                    "selection_evaluation": policy["evaluations"].get(item["model_name"], {}),
                },
                "trading_metrics": item["trading_metrics"],
                "rank_overall": rank,
                "selected_for_symbol": item["model_name"] == policy["selected_model"],
                "selection_evaluation": policy["evaluations"].get(item["model_name"], {}),
                "created_at": utc_now_iso(),
            }
            self.repository.add_forecast_stack_result(result)
            persisted.append(result)
        summary = {
            "winner": persisted[0]["model_name"],
            "selected_model": policy["selected_model"],
            "fallback_model": policy["fallback_model"],
            "selection_policy": policy,
            "selection_key": f"{symbol}:{timeframe}:{horizon}",
            "validation": validation,
            "result_count": len(persisted),
            "safety": "offline_scoring_only",
        }
        finished = utc_now_iso()
        self.repository.update_forecast_stack_experiment(experiment_id, status="COMPLETED", finished_at=finished, summary=summary)
        return {**experiment, "status": "COMPLETED", "finished_at": finished, "summary": summary, "results": persisted}

    def experiments(self) -> list[dict[str, Any]]:
        experiments = self.repository.list_forecast_stack_experiments()
        for experiment in experiments:
            experiment["results"] = self.repository.list_forecast_stack_results(
                experiment_id=experiment["experiment_id"]
            )
        return experiments

    def scorecard(self, symbol: str) -> dict[str, Any]:
        normalized = str(symbol or "").upper()
        rows = [
            row
            for row in self.repository.list_forecast_stack_results(limit=5000)
            if str(row.get("symbol") or "").upper() == normalized
        ]
        latest: dict[tuple[str, str, int], dict[str, Any]] = {}
        for row in rows:
            key = (str(row.get("model_name")), str(row.get("timeframe")), int(row.get("horizon_bars") or 0))
            latest.setdefault(key, row)
        return {
            "symbol": normalized,
            "items": list(latest.values()),
            "selection_scope": "symbol_timeframe_horizon",
        }

    def experiment(self, experiment_id: str) -> dict[str, Any] | None:
        item = self.repository.get_forecast_stack_experiment(experiment_id)
        if item:
            item["results"] = self.repository.list_forecast_stack_results(experiment_id=experiment_id)
        return item


def _numbers(values: Any) -> list[float]:
    if not isinstance(values, list):
        return []
    result = []
    for value in values:
        try:
            result.append(float(value))
        except (TypeError, ValueError):
            continue
    return result


def _trading_metrics(values: Any) -> dict[str, Any]:
    raw = values if isinstance(values, dict) else {}
    return {
        key: raw.get(key)
        for key in (
            "entry_touch_accuracy",
            "stop_before_entry_error_rate",
            "false_positive_rate",
            "forecast_filter_pnl_delta",
            "forecast_filter_drawdown_delta",
            "setup_score_improvement",
            "missed_good_setup_rate",
        )
    }


def _default_walk_forward_folds(size: int) -> list[dict[str, int]]:
    if size < 3:
        raise ValueError("At least 3 observations are required for walk-forward validation")
    split = max(1, size // 2)
    return [
        {"train_start": 0, "train_end": split - 1, "test_start": split, "test_end": size - 1}
    ]
