from __future__ import annotations

import asyncio
import importlib
import importlib.util
import math
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from app.conversion import canonicalize_setup_config
from app.forecasting.adapters import ForecastAdapterRegistry
from app.forecasting.forecast_ensemble import ForecastStackConsensus
from app.forecasting.forecast_models import (
    ForecastConfig,
    ForecastReferences,
    ForecastResult,
    TimesFMForecastOutput,
)
from app.forecasting.forecast_repository import ForecastRepository
from app.forecasting.forecast_stack_config import ForecastStackConfig
from app.forecasting.provider_statuses import (
    AVAILABLE,
    DISABLED_BY_CONFIG,
    EXTERNAL_WORKER_CONFIGURED,
    LOAD_ERROR,
    MISSING_DEPENDENCY,
    WORKER_UNREACHABLE,
    is_available_status,
    status_from_reason,
)
from app.forecasting.timesfm_engine import (
    TimesFMEngine,
    TimesFMForecastError,
    TimesFMUnavailableError,
)
from app.model_lab.darts_experiment_runner import DartsExperimentRunner
from app.models import utc_now_iso
from app.utils.id_generator import new_id

MarketHistoryProvider = Callable[[str, str], Awaitable[dict[str, Any]]]

FORECAST_TIMEFRAME_BAR_SIZES = {
    "3m": "3 mins",
    "10m": "10 mins",
    "15m": "15 mins",
    "30m": "30 mins",
    "1h": "1 hour",
    "4h": "4 hours",
    "1d": "1 day",
}

CACHED_FORECAST_TIMEFRAMES = {"3m", "15m", "1h", "1d"}


class ForecastService:
    def __init__(
        self,
        *,
        settings: dict[str, Any],
        repository: ForecastRepository,
        trading_repository: Any,
        market_history_provider: MarketHistoryProvider,
        engine: Any | None = None,
        accuracy_service: Any | None = None,
    ) -> None:
        self.config = ForecastConfig.from_settings(settings)
        self.stack_config = ForecastStackConfig.from_settings(settings)
        self.repository = repository
        self.trading_repository = trading_repository
        self.market_history_provider = market_history_provider
        self.engine = engine or TimesFMEngine()
        self.adapters = ForecastAdapterRegistry(timesfm_engine=self.engine)
        self.accuracy_service = accuracy_service
        self.stack_consensus = ForecastStackConsensus()

    async def forecast(
        self,
        symbol: str,
        *,
        timeframe: str | None = None,
        horizon: int | None = None,
        target: str | None = None,
        setup_id: str | None = None,
        cached_only: bool = False,
        force_refresh: bool = False,
        model_name: str | None = None,
    ) -> dict[str, Any]:
        normalized_symbol = str(symbol or "").strip().upper()
        if not normalized_symbol:
            return self._status_payload("", "INVALID_INPUT", "symbol is required")
        if not self.config.enabled:
            return self._status_payload(normalized_symbol, "DISABLED", "Forecasting is disabled.")

        timeframe = str(timeframe or self.config.timeframe)
        horizon = int(horizon or self.config.horizon_bars)
        target = str(target or self.config.target)
        model_name = _normalize_model_name(model_name or self.config.provider)
        persisted_model_name = self._persisted_model_name(model_name)
        provider = _stack_provider_for_model(self.stack_config.providers, model_name)
        if provider is not None and not provider.use_for_runtime_forecast:
            return self._status_payload(
                normalized_symbol,
                DISABLED_BY_CONFIG,
                f"Forecast provider '{model_name}' is restricted to offline Model Lab jobs.",
                timeframe=timeframe,
                horizon=horizon,
                target=target,
                model_name=persisted_model_name,
            )
        if provider is not None and not provider.enabled and not provider.auto_enable_when_ready:
            return self._status_payload(
                normalized_symbol,
                DISABLED_BY_CONFIG,
                f"Forecast provider '{model_name}' is disabled by forecast_stack configuration.",
                timeframe=timeframe,
                horizon=horizon,
                target=target,
                model_name=persisted_model_name,
            )
        if model_name not in {"timesfm", "naive_baseline", "atr_baseline"}:
            status, reason = self.adapters.status(model_name, self.config)
            if not is_available_status(status):
                return self._status_payload(
                    normalized_symbol,
                    status,
                    reason,
                    timeframe=timeframe,
                    horizon=horizon,
                    target=target,
                    model_name=persisted_model_name,
                )
        cached = self._cached_forecast(
            normalized_symbol,
            timeframe,
            setup_id,
            model_name=persisted_model_name,
        )
        if cached is not None and not force_refresh:
            return cached
        if cached_only:
            return self._status_payload(
                normalized_symbol,
                "NO_CACHED_FORECAST",
                f"No cached forecast is available for {persisted_model_name}.",
                timeframe=timeframe,
                horizon=horizon,
                target=target,
                model_name=persisted_model_name,
            )
        try:
            market_payload = await self.market_history_provider(normalized_symbol, timeframe)
        except Exception as exc:
            market_payload = {"message": str(exc), "historical_bars": []}

        bars = _bars_from_market_payload(market_payload)
        if not bars:
            fallback_payload = self._historical_bars_from_events(normalized_symbol, timeframe)
            if fallback_payload:
                market_payload = fallback_payload
                bars = _bars_from_market_payload(market_payload)
        if not bars:
            payload = self._status_payload(
                normalized_symbol,
                "NO_MARKET_DATA",
                market_payload.get("message") or "No historical bars available.",
                timeframe=timeframe,
                horizon=horizon,
                target=target,
            )
            self._persist(payload)
            return payload
        if len(bars) < self.config.min_context_bars:
            payload = self._status_payload(
                normalized_symbol,
                "INSUFFICIENT_DATA",
                f"Only {len(bars)} bars available, minimum required is {self.config.min_context_bars}",
                timeframe=timeframe,
                horizon=horizon,
                target=target,
                context_bars=len(bars),
                current_price=bars[-1]["close"],
                input_start_time=bars[0].get("date"),
                input_end_time=bars[-1].get("date"),
            )
            self._persist(payload)
            return payload

        context_bars = min(len(bars), self.config.context_bars)
        context = bars[-context_bars:]
        closes = [bar["close"] for bar in context]
        current_price = closes[-1]
        model_series = _target_series(closes, target)
        references = self._references_for_symbol(normalized_symbol, setup_id)
        try:
            forecast = await self._forecast_with_model(
                model_name,
                model_series=model_series,
                closes=closes,
                horizon=horizon,
                target=target,
                config=self.config,
            )
        except TimesFMUnavailableError as exc:
            payload = self._status_payload(
                normalized_symbol,
                status_from_reason(str(exc)),
                str(exc),
                timeframe=timeframe,
                horizon=horizon,
                target=target,
                context_bars=context_bars,
                current_price=current_price,
                input_start_time=context[0].get("date"),
                input_end_time=context[-1].get("date"),
                references=references,
                model_name=persisted_model_name,
            )
            self._persist(payload)
            return payload
        except TimesFMForecastError as exc:
            payload = self._status_payload(
                normalized_symbol,
                status_from_reason(str(exc)),
                str(exc),
                timeframe=timeframe,
                horizon=horizon,
                target=target,
                context_bars=context_bars,
                current_price=current_price,
                input_start_time=context[0].get("date"),
                input_end_time=context[-1].get("date"),
                references=references,
                model_name=persisted_model_name,
            )
            self._persist(payload)
            return payload

        payload = self._build_ok_payload(
            symbol=normalized_symbol,
            timeframe=timeframe,
            horizon=horizon,
            target=target,
            context_bars=context_bars,
            current_price=current_price,
            input_start_time=context[0].get("date"),
            input_end_time=context[-1].get("date"),
            forecast=forecast,
            references=references,
            model_name=persisted_model_name,
        )
        self._persist(payload)
        return payload

    async def forecast_ensemble(
        self,
        symbol: str,
        *,
        timeframe: str | None = None,
        horizon: int | None = None,
        target: str | None = None,
        setup_id: str | None = None,
        models: list[str] | tuple[str, ...] | None = None,
        force_refresh: bool = False,
    ) -> dict[str, Any]:
        normalized_symbol = str(symbol or "").strip().upper()
        if not normalized_symbol:
            return self._status_payload("", "INVALID_INPUT", "symbol is required")
        selected_models = (
            _normalize_models(models) if models is not None else self._runtime_forecast_models()
        )
        timeframe = str(timeframe or self.config.timeframe)
        horizon = int(horizon or self.config.horizon_bars)
        target = str(target or self.config.target)
        members = []
        for model_name in selected_models:
            members.append(
                await self.forecast(
                    normalized_symbol,
                    timeframe=timeframe,
                    horizon=horizon,
                    target=target,
                    setup_id=setup_id,
                    force_refresh=force_refresh,
                    model_name=model_name,
                )
            )
        ok_members = [
            item for item in members if item.get("status") == "OK" and item.get("q50_path")
        ]
        if not ok_members:
            payload = self._status_payload(
                normalized_symbol,
                "NO_MODEL_OUTPUT",
                "No model produced an OK forecast.",
                timeframe=timeframe,
                horizon=horizon,
                target=target,
                model_name="ensemble",
            )
            payload.update(
                {
                    "ensemble_id": new_id("ens"),
                    "models": selected_models,
                    "member_forecasts": _member_summaries(members),
                    "successful_model_count": 0,
                    "model_count": len(selected_models),
                }
            )
            payload["member_forecast_ids"] = [
                item["forecast_id"] for item in members if item.get("forecast_id")
            ]
            self._persist(payload)
            self.repository.insert_ensemble(payload)
            return payload

        current_price = _first_number(*[member.get("current_price") for member in ok_members])
        if current_price is None:
            current_price = 0.0
        q10_path = _average_paths([member.get("q10_path", []) for member in ok_members])
        q50_path = _average_paths([member.get("q50_path", []) for member in ok_members])
        q90_path = _average_paths([member.get("q90_path", []) for member in ok_members])
        references = self._references_for_symbol(normalized_symbol, setup_id)
        reference_price = _reference_price(current_price)
        median_end_price = q50_path[-1] if q50_path else None
        q10_end_price = q10_path[-1] if q10_path else None
        q90_end_price = q90_path[-1] if q90_path else None
        expected_return = (
            ((median_end_price - current_price) / current_price) * 100
            if current_price > 0 and median_end_price is not None
            else None
        )
        direction = _direction_from_reference(reference_price, median_end_price)
        slope = _forecast_slope(q50_path)
        q10_above_support = (
            min(q10_path) > references.support_level_reference
            if q10_path and references.support_level_reference is not None
            else None
        )
        median_above_entry = (
            q50_path[-1] > references.entry_trigger_reference
            if q50_path and references.entry_trigger_reference is not None
            else None
        )
        score = _metric_score(
            expected_return_pct=expected_return,
            q10_above_support=q10_above_support,
            median_above_current=bool(q50_path and q50_path[-1] > current_price),
            slope=direction,
        )
        generated_at = utc_now_iso()
        result = ForecastResult(
            symbol=normalized_symbol,
            timeframe=timeframe,
            model="ensemble",
            target=target,
            context_bars=max(int(member.get("context_bars") or 0) for member in ok_members),
            horizon_bars=horizon,
            generated_at=generated_at,
            current_price=_round(current_price),
            reference_price=_round(reference_price),
            forecast_last_price=_round(median_end_price),
            forecast_expected_return_pct=_round(expected_return),
            median_end_price=_round(median_end_price),
            median_return_pct=_round(expected_return),
            q10_end_price=_round(q10_end_price),
            q50_end_price=_round(median_end_price),
            q90_end_price=_round(q90_end_price),
            direction_basis="q50_last_vs_reference_price",
            forecast_path=[_round(value) for value in q50_path],
            q10_path=[_round(value) for value in q10_path],
            q50_path=[_round(value) for value in q50_path],
            q90_path=[_round(value) for value in q90_path],
            support_level_reference=references.support_level_reference,
            entry_trigger_reference=references.entry_trigger_reference,
            stop_level_reference=references.stop_level_reference,
            median_above_entry_trigger=median_above_entry,
            q10_above_support=q10_above_support,
            forecast_slope=slope,
            direction=direction,
            forecast_status=_forecast_status(score, self.config.score_thresholds),
            confidence="MEDIUM" if len(ok_members) >= 2 else "LOW",
            metric_score=score,
            status="OK" if len(ok_members) == len(selected_models) else "PARTIAL",
            setup_id=references.setup_id,
        )
        payload = result.to_dict()
        payload.update(
            {
                "ensemble_id": new_id("ens"),
                "models": selected_models,
                "member_forecasts": _member_summaries(members),
                "successful_model_count": len(ok_members),
                "model_count": len(selected_models),
                "member_forecast_ids": [
                    item["forecast_id"] for item in members if item.get("forecast_id")
                ],
            }
        )
        payload["forecast_stack_summary"] = self.stack_consensus.evaluate(
            members,
            reliability=self._reliability_for_members(members),
        )
        self._persist(payload)
        self.repository.insert_ensemble(payload)
        return payload

    def _runtime_forecast_models(self) -> tuple[str, ...]:
        selected: list[str] = []
        for model_name in _normalize_models(self.config.default_models):
            provider = _stack_provider_for_model(self.stack_config.providers, model_name)
            if provider is not None and not provider.use_for_runtime_forecast:
                continue
            if (
                provider is not None
                and not provider.enabled
                and not provider.auto_enable_when_ready
            ):
                continue
            if model_name == "timesfm":
                if is_available_status(str(self._timesfm_availability().get("status") or "")):
                    selected.append(model_name)
                continue
            if model_name in {"naive_baseline", "atr_baseline"}:
                selected.append(model_name)
                continue
            status, _reason = self.adapters.status(model_name, self.config)
            if is_available_status(status):
                selected.append(model_name)
        baselines = [name for name in ("naive_baseline", "atr_baseline") if name not in selected]
        return tuple([*selected, *baselines])

    def stack_summary(
        self,
        symbol: str,
        *,
        timeframe: str | None = None,
        setup_id: str | None = None,
    ) -> dict[str, Any]:
        normalized_symbol = str(symbol or "").upper()
        selected_timeframe = str(timeframe or self.config.timeframe)
        members: list[dict[str, Any]] = []
        for model_name in _normalize_models(self.config.default_models):
            row = self.repository.latest_forecast(
                normalized_symbol,
                timeframe=selected_timeframe,
                model_name=self._persisted_model_name(model_name),
                setup_id=setup_id,
            )
            forecast = row.get("forecast") if isinstance(row, dict) else None
            if isinstance(forecast, dict):
                members.append(forecast)
            else:
                members.append(
                    self._uncached_stack_member_status(
                        normalized_symbol,
                        selected_timeframe,
                        model_name,
                        setup_id=setup_id,
                    )
                )
        return {
            "symbol": normalized_symbol,
            "timeframe": selected_timeframe,
            "setup_id": setup_id,
            **self.stack_consensus.evaluate(
                members,
                reliability=self._reliability_for_members(members),
            ),
        }

    def _uncached_stack_member_status(
        self,
        symbol: str,
        timeframe: str,
        model_name: str,
        *,
        setup_id: str | None = None,
    ) -> dict[str, Any]:
        persisted_model_name = self._persisted_model_name(model_name)
        provider = _stack_provider_for_model(self.stack_config.providers, model_name)
        status = "NO_CACHED_FORECAST"
        reason = f"No cached forecast is available for {persisted_model_name}."
        if provider is not None and not provider.use_for_runtime_forecast:
            status = DISABLED_BY_CONFIG
            reason = f"Forecast provider '{model_name}' is restricted to offline Model Lab jobs."
        elif provider is not None and not provider.enabled and not provider.auto_enable_when_ready:
            status = DISABLED_BY_CONFIG
            reason = (
                f"Forecast provider '{model_name}' is disabled by forecast_stack configuration."
            )
        payload = self._status_payload(
            symbol,
            status,
            reason,
            timeframe=timeframe,
            model_name=persisted_model_name,
        )
        payload["setup_id"] = setup_id
        return payload

    def _reliability_for_members(
        self,
        members: list[dict[str, Any]],
    ) -> dict[str, dict[str, Any]]:
        if self.accuracy_service is None:
            return {}
        result: dict[str, dict[str, Any]] = {}
        for member in members:
            name = _normalize_model_name(member.get("model") or member.get("model_name") or "")
            if not name:
                continue
            lookup_name = "timesfm" if name.startswith("timesfm") else name
            rows = self.accuracy_service.scorecards(
                lookup_name,
                symbol=member.get("symbol"),
                timeframe=member.get("timeframe"),
            )
            if rows:
                result[lookup_name] = rows[0]
        return result

    def history(
        self,
        symbol: str,
        *,
        timeframe: str | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        rows = self.repository.history(
            symbol,
            timeframe=timeframe or self.config.timeframe,
            limit=limit,
        )
        return [row["forecast"] for row in rows]

    def watchlist(self, *, timeframe: str | None = None) -> list[dict[str, Any]]:
        setups = self.trading_repository.list_setups()
        symbols = [str(setup.get("symbol", "")).upper() for setup in setups]
        latest = self.repository.latest_for_symbols(
            symbols,
            timeframe=timeframe or self.config.timeframe,
        )
        rows: list[dict[str, Any]] = []
        for setup in setups:
            symbol = str(setup.get("symbol", "")).upper()
            forecast = latest.get(symbol, {}).get("forecast")
            if not forecast:
                continue
            rows.append(
                {
                    "symbol": symbol,
                    "setup_id": setup.get("setup_id"),
                    "metric_score": forecast.get("metric_score"),
                    "forecast_status": forecast.get("forecast_status"),
                    "expected_return_pct": forecast.get("forecast_expected_return_pct"),
                    "last_update": forecast.get("generated_at"),
                    "status": forecast.get("status"),
                    "used_for_decision": False,
                }
            )
        return rows

    def models(self) -> dict[str, Any]:
        timesfm_model = self._timesfm_availability()
        external_models = []
        for item in self.adapters.availability(self.config):
            catalog_item = dict(item)
            if str(catalog_item.get("status") or "") == "AVAILABLE":
                catalog_item["status"] = AVAILABLE
            provider_name = _normalize_model_name(str(catalog_item.get("model") or ""))
            provider = _stack_provider_for_model(self.stack_config.providers, provider_name)
            if (
                provider is not None
                and not provider.enabled
                and not provider.auto_enable_when_ready
            ):
                catalog_item["status"] = DISABLED_BY_CONFIG
                catalog_item["available"] = False
                catalog_item["reason"] = "Disabled by forecast_stack configuration."
            external_models.append(catalog_item)
        darts_provider = self.stack_config.providers.get("darts")
        darts_runtime_options = self.config.provider_options.get("darts", {})
        darts_options = {
            **(darts_runtime_options if isinstance(darts_runtime_options, dict) else {}),
            **(darts_provider.options if darts_provider is not None else {}),
        }
        darts_status_payload = DartsExperimentRunner(
            python_executable=str(darts_options.get("python_executable") or ""),
            worker_timeout_seconds=int(darts_options.get("worker_timeout_seconds") or 180),
        ).provider_status()
        darts_status = str(darts_status_payload.get("status") or LOAD_ERROR)
        if darts_status == "AVAILABLE":
            darts_status = AVAILABLE
        darts_available = is_available_status(darts_status)
        darts_reason = str(
            darts_status_payload.get("reason")
            or ("available" if darts_available else "Missing optional package(s): darts")
        )
        external_models.append(
            {
                "model": "darts",
                "status": darts_status,
                "available": darts_available,
                "reason": darts_reason,
                "runtime_mode": str(darts_status_payload.get("execution_mode") or "in_process"),
                "baseline": False,
                "model_lab_only": True,
            }
        )
        if (
            darts_provider is not None
            and not darts_provider.enabled
            and not darts_provider.auto_enable_when_ready
        ):
            external_models[-1]["status"] = DISABLED_BY_CONFIG
            external_models[-1]["available"] = False
            external_models[-1]["reason"] = "Disabled by forecast_stack configuration."
        baselines = [
            {
                "model": "naive_baseline",
                "status": AVAILABLE,
                "available": True,
                "reason": "deterministic baseline for benchmark comparison",
                "runtime_mode": "in_process",
                "baseline": True,
            },
            {
                "model": "atr_baseline",
                "status": AVAILABLE,
                "available": True,
                "reason": "ATR drift baseline for benchmark comparison",
                "runtime_mode": "in_process",
                "baseline": True,
            },
        ]
        available_models = [
            item["model"]
            for item in [timesfm_model, *external_models, *baselines]
            if item.get("available")
        ]
        return {
            "default_models": list(self.config.default_models),
            "configured_models": list(self.config.default_models),
            "available_models": available_models,
            "timesfm": timesfm_model,
            "external_models": external_models,
            "baselines": baselines,
            "decision_policy": "Forecasts are scoring signals only and never submit orders.",
        }

    def _timesfm_availability(self) -> dict[str, Any]:
        if self.config.python_executable:
            python = Path(self.config.python_executable)
            if python.exists():
                return {
                    "model": "timesfm",
                    "status": EXTERNAL_WORKER_CONFIGURED,
                    "available": True,
                    "reason": "external TimesFM worker is configured",
                    "runtime_mode": "external_worker",
                    "baseline": False,
                }
            return {
                "model": "timesfm",
                "status": WORKER_UNREACHABLE,
                "available": False,
                "reason": f"TimesFM python executable not found: {python}",
                "runtime_mode": "external_worker",
                "baseline": False,
            }
        if _module_available("timesfm"):
            try:
                importlib.import_module("timesfm")
            except ModuleNotFoundError as exc:
                missing_name = getattr(exc, "name", None) or "timesfm"
                return {
                    "model": "timesfm",
                    "status": MISSING_DEPENDENCY,
                    "available": False,
                    "reason": f"Missing optional package(s): {missing_name}",
                    "runtime_mode": "in_process",
                    "baseline": False,
                }
            except ImportError as exc:
                return {
                    "model": "timesfm",
                    "status": LOAD_ERROR,
                    "available": False,
                    "reason": f"TimesFM package failed to load: {exc}",
                    "runtime_mode": "in_process",
                    "baseline": False,
                }
            except Exception as exc:
                return {
                    "model": "timesfm",
                    "status": LOAD_ERROR,
                    "available": False,
                    "reason": f"TimesFM package failed to load: {exc}",
                    "runtime_mode": "in_process",
                    "baseline": False,
                }
            return {
                "model": "timesfm",
                "status": AVAILABLE,
                "available": True,
                "reason": "available",
                "runtime_mode": "in_process",
                "baseline": False,
            }
        return {
            "model": "timesfm",
            "status": MISSING_DEPENDENCY,
            "available": False,
            "reason": "Missing optional package(s): timesfm",
            "runtime_mode": "in_process",
            "baseline": False,
        }

    def _build_ok_payload(
        self,
        *,
        symbol: str,
        timeframe: str,
        horizon: int,
        target: str,
        context_bars: int,
        current_price: float,
        input_start_time: str | None,
        input_end_time: str | None,
        forecast: TimesFMForecastOutput,
        references: ForecastReferences,
        model_name: str | None = None,
    ) -> dict[str, Any]:
        q10_path = _price_path(current_price, forecast.q10_path, target)
        q50_path = _price_path(current_price, forecast.q50_path, target)
        q90_path = _price_path(current_price, forecast.q90_path, target)
        reference_price = _reference_price(current_price)
        median_end_price = q50_path[-1] if q50_path else None
        q10_end_price = q10_path[-1] if q10_path else None
        q90_end_price = q90_path[-1] if q90_path else None
        expected_return = (
            ((median_end_price - current_price) / current_price) * 100
            if current_price > 0 and median_end_price is not None
            else None
        )
        median_above_entry = (
            q50_path[-1] > references.entry_trigger_reference
            if q50_path and references.entry_trigger_reference is not None
            else None
        )
        q10_above_support = (
            min(q10_path) > references.support_level_reference
            if q10_path and references.support_level_reference is not None
            else None
        )
        q10_above_stop = (
            min(q10_path) > references.stop_level_reference
            if q10_path and references.stop_level_reference is not None
            else None
        )
        direction = _direction_from_reference(reference_price, median_end_price)
        slope = _forecast_slope(q50_path)
        score = _metric_score(
            expected_return_pct=expected_return,
            q10_above_support=q10_above_support,
            median_above_current=bool(q50_path and q50_path[-1] > current_price),
            slope=direction,
        )
        status = _forecast_status(score, self.config.score_thresholds)
        prob_touch_entry = forecast.prob_touch_entry
        prob_touch_stop = forecast.prob_touch_stop_before_entry
        forecast_warnings = list(forecast.warnings)
        if str(model_name or "").lower() == "lag_llama" and (
            prob_touch_entry is None or prob_touch_stop is None
        ):
            estimated_entry, estimated_stop = _estimate_touch_probabilities(
                q10_path,
                q50_path,
                q90_path,
                entry=references.entry_trigger_reference,
                stop=references.stop_level_reference,
            )
            prob_touch_entry = prob_touch_entry if prob_touch_entry is not None else estimated_entry
            prob_touch_stop = prob_touch_stop if prob_touch_stop is not None else estimated_stop
            if estimated_entry is not None or estimated_stop is not None:
                forecast_warnings.append("TOUCH_PROBABILITIES_ESTIMATED_FROM_QUANTILES")
        result = ForecastResult(
            symbol=symbol,
            timeframe=timeframe,
            model=model_name or self.config.model,
            target=target,
            context_bars=context_bars,
            horizon_bars=horizon,
            generated_at=utc_now_iso(),
            current_price=round(current_price, 4),
            reference_price=_round(reference_price),
            forecast_last_price=_round(median_end_price),
            forecast_expected_return_pct=_round(expected_return),
            median_end_price=_round(median_end_price),
            median_return_pct=_round(expected_return),
            q10_end_price=_round(q10_end_price),
            q50_end_price=_round(median_end_price),
            q90_end_price=_round(q90_end_price),
            direction_basis="q50_last_vs_reference_price",
            forecast_path=[_round(value) for value in q50_path],
            q10_path=[_round(value) for value in q10_path],
            q50_path=[_round(value) for value in q50_path],
            q90_path=[_round(value) for value in q90_path],
            support_level_reference=references.support_level_reference,
            entry_trigger_reference=references.entry_trigger_reference,
            stop_level_reference=references.stop_level_reference,
            median_above_entry_trigger=median_above_entry,
            q10_above_support=q10_above_support,
            q10_above_stop=q10_above_stop,
            forecast_slope=slope,
            direction=direction,
            direction_confidence=_confidence_value(
                context_bars,
                self.config.context_bars,
                q10_path,
                q90_path,
            ),
            prob_touch_entry=prob_touch_entry,
            prob_touch_stop_before_entry=prob_touch_stop,
            uncertainty_width_pct=_uncertainty_width_pct(current_price, q10_path, q90_path),
            prediction_intervals=forecast.prediction_intervals,
            warnings=forecast_warnings,
            forecast_status=status,
            confidence=_confidence(context_bars, self.config.context_bars, q10_path, q90_path),
            metric_score=score,
            status="OK",
            input_start_time=input_start_time,
            input_end_time=input_end_time,
            setup_id=references.setup_id,
        )
        return result.to_dict()

    def _status_payload(
        self,
        symbol: str,
        status: str,
        error: str,
        *,
        timeframe: str | None = None,
        horizon: int | None = None,
        target: str | None = None,
        context_bars: int = 0,
        current_price: float | None = None,
        input_start_time: str | None = None,
        input_end_time: str | None = None,
        references: ForecastReferences | None = None,
        model_name: str | None = None,
    ) -> dict[str, Any]:
        references = references or ForecastReferences()
        result = ForecastResult(
            symbol=symbol,
            timeframe=timeframe or self.config.timeframe,
            model=model_name or self.config.model,
            target=target or self.config.target,
            context_bars=context_bars,
            horizon_bars=horizon or self.config.horizon_bars,
            generated_at=utc_now_iso(),
            current_price=current_price,
            support_level_reference=references.support_level_reference,
            entry_trigger_reference=references.entry_trigger_reference,
            stop_level_reference=references.stop_level_reference,
            forecast_status=status,
            confidence="LOW",
            metric_score=0,
            status=status,
            error=error,
            input_start_time=input_start_time,
            input_end_time=input_end_time,
            setup_id=references.setup_id,
        )
        return result.to_dict()

    def _persist(self, payload: dict[str, Any]) -> None:
        if self.config.persist_results:
            forecast_id = self.repository.insert_forecast(payload)
            if self.accuracy_service is not None:
                self.accuracy_service.register(forecast_id, payload)

    def _cached_forecast(
        self,
        symbol: str,
        timeframe: str,
        setup_id: str | None,
        *,
        model_name: str | None = None,
    ) -> dict[str, Any] | None:
        if str(timeframe or "").strip().lower() not in CACHED_FORECAST_TIMEFRAMES:
            return None
        row = self.repository.latest_forecast(
            symbol,
            timeframe=timeframe,
            model_name=model_name,
            setup_id=setup_id,
        )
        if not row:
            return None
        forecast = row.get("forecast") if isinstance(row.get("forecast"), dict) else None
        if not forecast:
            return None
        if setup_id and str(forecast.get("setup_id") or "") != str(setup_id):
            return None
        return {
            **forecast,
            "used_for_decision": False,
            "decision_impact": "NONE",
            "status": forecast.get("status") or row.get("status") or "OK",
            "error": forecast.get("error") or row.get("error"),
            "cache_hit": True,
        }

    async def _forecast_with_model(
        self,
        model_name: str,
        *,
        model_series: list[float],
        closes: list[float],
        horizon: int,
        target: str,
        config: ForecastConfig,
    ) -> TimesFMForecastOutput:
        if model_name == "timesfm":
            return await asyncio.to_thread(
                self.engine.forecast,
                model_series,
                horizon=horizon,
                config=config,
            )
        if model_name == "naive_baseline":
            return _naive_baseline_forecast(model_series, horizon)
        if model_name == "atr_baseline":
            return _atr_baseline_forecast(model_series, closes, horizon, target)
        adapter = self.adapters.get(model_name)
        if adapter is not None:
            return await asyncio.to_thread(
                adapter.forecast,
                model_series,
                closes=closes,
                horizon=horizon,
                target=target,
                config=config,
            )
        raise TimesFMForecastError(f"Unknown forecast model: {model_name}")

    def _persisted_model_name(self, model_name: str) -> str:
        return self.config.model if model_name == "timesfm" else model_name

    def _references_for_symbol(
        self,
        symbol: str,
        setup_id: str | None,
    ) -> ForecastReferences:
        setup = None
        if setup_id:
            setup = self.trading_repository.get_setup(setup_id)
        if setup is None:
            setup = next(
                (
                    item
                    for item in self.trading_repository.list_setups()
                    if str(item.get("symbol", "")).upper() == symbol
                ),
                None,
            )
        if not setup:
            return ForecastReferences()
        setup = _canonical_setup(setup)
        config = setup.get("config", {}) if isinstance(setup, dict) else {}
        support = _first_number(
            setup.get("support_level"),
            config.get("support_level"),
            _nested_number(config, "support_zone", "min"),
            _nested_number(config, "retest", "zone_min"),
            _nested_number(config, "trailing_stop_loss", "initial_stop"),
        )
        entry_trigger = _first_number(
            setup.get("entry_trigger"),
            _nested_number(config, "entry", "trigger_price"),
            _nested_number(config, "entry", "entry_price"),
            _nested_number(config, "breakout", "resistance"),
            _nested_number(config, "breakout", "daily_close_above"),
        )
        stop = _first_number(
            _nested_number(config, "trailing_stop_loss", "initial_stop"),
        )
        return ForecastReferences(
            setup_id=str(setup.get("setup_id")) if setup.get("setup_id") else None,
            support_level_reference=support,
            entry_trigger_reference=entry_trigger,
            stop_level_reference=stop,
        )

    def _historical_bars_from_events(
        self,
        symbol: str,
        timeframe: str,
    ) -> dict[str, Any] | None:
        list_events = getattr(self.trading_repository, "list_events", None)
        if not callable(list_events):
            return None
        expected_bar_size = _bar_size_for_timeframe(timeframe)
        try:
            events = list_events(limit=50, symbol=symbol, event_type="stock_quote")
        except TypeError:
            return None
        fallback: dict[str, Any] | None = None
        for event in events:
            data = event.get("data") if isinstance(event, dict) else None
            if not isinstance(data, dict):
                continue
            bars = data.get("historical_bars")
            if not isinstance(bars, list) or not bars:
                continue
            bar_size = str(
                data.get("historical_bar_size") or data.get("hybrid_signal_bar_size") or ""
            )
            payload = {
                "symbol": symbol,
                "timeframe": timeframe,
                "historical_bars": bars,
                "historical_bar_size": bar_size,
                "source": "event_store",
                "message": "Using persisted stock_quote historical bars.",
            }
            if expected_bar_size and bar_size.lower() == expected_bar_size.lower():
                return payload
            if fallback is None:
                fallback = payload
        return fallback


def _bars_from_market_payload(payload: dict[str, Any]) -> list[dict[str, Any]]:
    rows = payload.get("historical_bars")
    if not isinstance(rows, list):
        rows = []
    bars: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        close = _first_number(row.get("close"))
        if close is None or close <= 0:
            continue
        bars.append(
            {
                "date": row.get("date") or row.get("timestamp") or row.get("bar_date"),
                "close": close,
            }
        )
    return bars


def _bar_size_for_timeframe(timeframe: str) -> str:
    return FORECAST_TIMEFRAME_BAR_SIZES.get(str(timeframe or "").strip().lower(), "")


def _target_series(closes: list[float], target: str) -> list[float]:
    if target == "log_return":
        returns: list[float] = []
        for previous, current in zip(closes, closes[1:]):
            if previous > 0 and current > 0:
                returns.append(math.log(current / previous))
        return returns
    return list(closes)


def _price_path(current_price: float, forecast_path: list[float], target: str) -> list[float]:
    if target != "log_return":
        return list(forecast_path)
    price = current_price
    path: list[float] = []
    for value in forecast_path:
        price *= math.exp(value)
        path.append(price)
    return path


def _metric_score(
    *,
    expected_return_pct: float | None,
    q10_above_support: bool | None,
    median_above_current: bool,
    slope: str,
) -> int:
    score = 0
    if expected_return_pct is not None and expected_return_pct > 0:
        score += 30
    if expected_return_pct is not None and expected_return_pct >= 0.5:
        score += 20
    if q10_above_support is True:
        score += 20
    if median_above_current:
        score += 20
    if slope == "UP":
        score += 10
    return min(score, 100)


def _forecast_status(score: int, thresholds: dict[str, int]) -> str:
    if score >= thresholds.get("bullish", 75):
        return "BULLISH"
    if score >= thresholds.get("neutral_bullish", 60):
        return "NEUTRAL_BULLISH"
    if score >= thresholds.get("neutral", 40):
        return "NEUTRAL"
    if score >= 20:
        return "WEAK"
    return "BEARISH"


def _forecast_slope(path: list[float]) -> str:
    if len(path) < 2:
        return "FLAT"
    delta = path[-1] - path[0]
    if abs(delta) < max(abs(path[0]) * 0.0005, 0.0001):
        return "FLAT"
    return "UP" if delta > 0 else "DOWN"


def _reference_price(current_price: float | None) -> float | None:
    if current_price is None:
        return None
    if not isinstance(current_price, int | float):
        return None
    return float(current_price) if float(current_price) > 0 else None


def _direction_from_reference(
    reference_price: float | None,
    median_end_price: float | None,
) -> str:
    if reference_price is None or median_end_price is None:
        return "FLAT"
    delta = float(median_end_price) - float(reference_price)
    if abs(delta) < max(abs(float(reference_price)) * 0.0005, 0.0001):
        return "FLAT"
    return "UP" if delta > 0 else "DOWN"


def _confidence(
    context_bars: int,
    preferred_context: int,
    q10_path: list[float],
    q90_path: list[float],
) -> str:
    if context_bars < preferred_context:
        return "LOW"
    if q10_path and q90_path and q90_path[-1] > q10_path[-1]:
        return "MEDIUM"
    return "LOW"


def _confidence_value(
    context_bars: int,
    preferred_context: int,
    q10_path: list[float],
    q90_path: list[float],
) -> float:
    context_factor = min(1.0, context_bars / max(1, preferred_context))
    if not q10_path or not q90_path:
        return round(0.4 * context_factor, 4)
    center = (abs(q90_path[-1]) + abs(q10_path[-1])) / 2
    width = abs(q90_path[-1] - q10_path[-1])
    interval_factor = 1.0 / (1.0 + width / center) if center else 0.5
    return round(max(0.0, min(1.0, 0.5 * context_factor + 0.5 * interval_factor)), 4)


def _uncertainty_width_pct(
    current_price: float,
    q10_path: list[float],
    q90_path: list[float],
) -> float | None:
    if current_price <= 0 or not q10_path or not q90_path:
        return None
    return round(abs(q90_path[-1] - q10_path[-1]) / current_price * 100, 4)


def _estimate_touch_probabilities(
    q10_path: list[float],
    q50_path: list[float],
    q90_path: list[float],
    *,
    entry: float | None,
    stop: float | None,
) -> tuple[float | None, float | None]:
    size = min(len(q10_path), len(q50_path), len(q90_path))
    if size == 0:
        return None, None
    entry_probabilities: list[float] = []
    stop_probabilities: list[float] = []
    for low, median, high in zip(q10_path[:size], q50_path[:size], q90_path[:size]):
        sigma = max(0.0, (high - low) / (2 * 1.2815515655446004))
        if entry is not None:
            entry_probabilities.append(_normal_tail(entry, median, sigma, upper=True))
        if stop is not None:
            stop_probabilities.append(_normal_tail(stop, median, sigma, upper=False))
    touch_entry = max(entry_probabilities) if entry_probabilities else None
    touch_stop = max(stop_probabilities) if stop_probabilities else None
    stop_before_entry = (
        touch_stop * (1.0 - 0.5 * touch_entry)
        if touch_stop is not None and touch_entry is not None
        else touch_stop
    )
    return _round_probability(touch_entry), _round_probability(stop_before_entry)


def _normal_tail(level: float, median: float, sigma: float, *, upper: bool) -> float:
    if sigma <= 1e-12:
        probability_below = 1.0 if level >= median else 0.0
    else:
        z = (level - median) / sigma
        probability_below = 0.5 * (1.0 + math.erf(z / math.sqrt(2.0)))
    return 1.0 - probability_below if upper else probability_below


def _round_probability(value: float | None) -> float | None:
    return round(max(0.0, min(1.0, value)), 4) if value is not None else None


def _first_number(*values: Any) -> float | None:
    for value in values:
        if value in (None, ""):
            continue
        try:
            return float(value)
        except (TypeError, ValueError):
            continue
    return None


def _nested_number(payload: dict[str, Any], section: str, key: str) -> float | None:
    item = payload.get(section)
    if not isinstance(item, dict):
        return None
    return _first_number(item.get(key))


def _canonical_setup(setup: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(setup)
    config = setup.get("config") if isinstance(setup.get("config"), dict) else {}
    try:
        normalized["config"] = canonicalize_setup_config(config).config
    except Exception:
        normalized["config"] = dict(config)
    return normalized


def _round(value: float | None) -> float | None:
    return round(value, 4) if value is not None else None


def _module_available(package: str) -> bool:
    try:
        return importlib.util.find_spec(package) is not None
    except ModuleNotFoundError:
        return False


def _normalize_model_name(model_name: str) -> str:
    normalized = str(model_name or "").strip().lower().replace("-", "_")
    aliases = {
        "timesfm_2_5_200m": "timesfm",
        "timesfm_2.5_200m": "timesfm",
        "naive": "naive_baseline",
        "baseline": "naive_baseline",
        "atr": "atr_baseline",
        "chronos_t5": "chronos",
        "lag-llama": "lag_llama",
        "lagllama": "lag_llama",
        "moirai_moe": "moirai",
        "neural_forecast": "neuralforecast",
        "auto_gluon": "autogluon",
    }
    return aliases.get(normalized, normalized or "timesfm")


def _normalize_models(models: list[str] | tuple[str, ...]) -> list[str]:
    normalized: list[str] = []
    for model in models:
        name = _normalize_model_name(model)
        if name not in normalized:
            normalized.append(name)
    return normalized or ["timesfm"]


def _stack_provider_for_model(
    providers: dict[str, Any],
    model_name: str,
) -> Any | None:
    provider = providers.get(model_name)
    if provider is None and model_name in {"moirai", "uni2ts"}:
        provider = providers.get("moirai_uni2ts")
    return provider


def _naive_baseline_forecast(
    model_series: list[float],
    horizon: int,
) -> TimesFMForecastOutput:
    last_value = model_series[-1] if model_series else 0.0
    path = [last_value] * horizon
    return TimesFMForecastOutput(q10_path=path, q50_path=path, q90_path=path)


def _atr_baseline_forecast(
    model_series: list[float],
    closes: list[float],
    horizon: int,
    target: str,
) -> TimesFMForecastOutput:
    if target == "log_return":
        returns = model_series[-30:] if model_series else [0.0]
        drift = sum(returns) / len(returns)
        volatility = _stddev(returns) or abs(drift) or 0.001
        return TimesFMForecastOutput(
            q10_path=[drift - volatility] * horizon,
            q50_path=[drift] * horizon,
            q90_path=[drift + volatility] * horizon,
        )

    current = closes[-1] if closes else (model_series[-1] if model_series else 0.0)
    moves = [abs(current - previous) for previous, current in zip(closes, closes[1:])]
    atr = (sum(moves[-14:]) / min(len(moves), 14)) if moves else max(current * 0.005, 0.01)
    drift = (closes[-1] - closes[-15]) / 14 if len(closes) >= 15 else 0.0
    q50_path = [current + drift * step for step in range(1, horizon + 1)]
    q10_path = [value - atr for value in q50_path]
    q90_path = [value + atr for value in q50_path]
    return TimesFMForecastOutput(q10_path=q10_path, q50_path=q50_path, q90_path=q90_path)


def _average_paths(paths: list[list[Any]]) -> list[float]:
    usable = [
        [float(value) for value in path if value is not None]
        for path in paths
        if isinstance(path, list) and path
    ]
    if not usable:
        return []
    length = min(len(path) for path in usable)
    return [sum(path[index] for path in usable) / len(usable) for index in range(length)]


def _member_summaries(members: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "forecast_id": member.get("forecast_id"),
            "model": member.get("model"),
            "status": member.get("status"),
            "forecast_status": member.get("forecast_status"),
            "metric_score": member.get("metric_score"),
            "expected_return_pct": member.get("forecast_expected_return_pct"),
            "error": member.get("error"),
        }
        for member in members
    ]


def _stddev(values: list[float]) -> float:
    if len(values) < 2:
        return 0.0
    average = sum(values) / len(values)
    variance = sum((value - average) ** 2 for value in values) / (len(values) - 1)
    return math.sqrt(variance)
