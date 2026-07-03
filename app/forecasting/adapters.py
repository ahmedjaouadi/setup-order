from __future__ import annotations

import importlib
import importlib.util
import inspect
import json
import math
import os
import subprocess
from collections.abc import Callable
from contextlib import nullcontext
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any, Protocol

from app.forecasting.provider_statuses import (
    AVAILABLE,
    EXTERNAL_WORKER_CONFIGURED,
    EXTERNAL_WORKER_OK,
    LOAD_ERROR,
    MISSING_DEPENDENCY,
    WORKER_NOT_CONFIGURED,
    WORKER_UNREACHABLE,
    is_available_status,
    status_from_reason,
)
from app.forecasting.base_forecaster import (
    ForecastModelCapabilities,
    NormalizedForecastResult,
)
from app.forecasting.forecast_models import ForecastConfig, TimesFMForecastOutput
from app.forecasting.timesfm_engine import TimesFMForecastError, TimesFMUnavailableError
from app.models import utc_now_iso


class ForecastAdapter(Protocol):
    name: str

    def forecast(
        self,
        series: list[float],
        *,
        closes: list[float],
        horizon: int,
        target: str,
        config: ForecastConfig,
    ) -> TimesFMForecastOutput:
        ...


class ForecastAdapterRegistry:
    def __init__(self, timesfm_engine: Any | None = None) -> None:
        self._adapters: dict[str, ForecastAdapter] = {
            "chronos": ChronosAdapter(),
            "lag_llama": DottedCallableProviderAdapter(
                "lag_llama",
                package_name="lag_llama",
                default_callable="app.forecasting.provider_bridges:lag_llama_forecast",
                required_packages=("torch", "pandas", "gluonts", "huggingface_hub"),
            ),
            "moirai": DottedCallableProviderAdapter(
                "moirai",
                package_name="uni2ts",
                default_callable="app.forecasting.provider_bridges:moirai_uni2ts_forecast",
                required_packages=("pandas", "gluonts"),
            ),
            "moirai_uni2ts": DottedCallableProviderAdapter(
                "moirai_uni2ts",
                package_name="uni2ts",
                default_callable="app.forecasting.provider_bridges:moirai_uni2ts_forecast",
                required_packages=("pandas", "gluonts"),
            ),
            "uni2ts": DottedCallableProviderAdapter(
                "uni2ts",
                package_name="uni2ts",
                default_callable="app.forecasting.provider_bridges:moirai_uni2ts_forecast",
                required_packages=("pandas", "gluonts"),
            ),
            "neuralforecast": NeuralForecastAdapter(),
            "autogluon": AutoGluonAdapter(),
        }
        self.timesfm_engine = timesfm_engine

    def get(self, model_name: str) -> ForecastAdapter | None:
        return self._adapters.get(model_name)

    def names(self) -> list[str]:
        return sorted(self._adapters)

    def availability(self, config: ForecastConfig) -> list[dict[str, Any]]:
        items = []
        for name in self.names():
            adapter = self._adapters[name]
            status, reason = adapter_health(adapter, config)
            items.append(
                {
                    "model": name,
                    "status": status,
                    "available": is_available_status(status),
                    "reason": reason,
                    "runtime_mode": _runtime_mode(provider_options(config, name)),
                    "baseline": False,
                }
            )
        return items

    def status(self, model_name: str, config: ForecastConfig) -> tuple[str, str]:
        adapter = self.get(str(model_name).lower().replace("-", "_"))
        if adapter is None:
            return LOAD_ERROR, "Provider is not registered."
        return adapter_health(adapter, config)

    def readiness(self, model_name: str, config: ForecastConfig) -> tuple[bool, str]:
        status, reason = self.status(model_name, config)
        return is_available_status(status), reason

    def capabilities(
        self,
        model_name: str,
        config: ForecastConfig,
    ) -> ForecastModelCapabilities:
        name = str(model_name).lower().replace("-", "_")
        if name == "timesfm":
            if config.python_executable:
                installed = True
                available = Path(config.python_executable).exists()
                unavailable_reason = None if available else "TimesFM runtime is not configured."
            else:
                try:
                    installed = _module_available("timesfm")
                    if installed:
                        importlib.import_module("timesfm")
                except ModuleNotFoundError:
                    installed = False
                    available = False
                    unavailable_reason = "Missing optional package(s): timesfm"
                except ImportError as exc:
                    installed = True
                    available = False
                    unavailable_reason = f"TimesFM package failed to load: {exc}"
                except Exception as exc:
                    installed = True
                    available = False
                    unavailable_reason = f"TimesFM package failed to load: {exc}"
                else:
                    available = bool(installed)
                    unavailable_reason = None if available else "Missing optional package(s): timesfm"
            return ForecastModelCapabilities(
                model_name=name,
                supports_quantiles=True,
                supports_zero_shot=True,
                installed=installed,
                available=available,
                unavailable_reason=unavailable_reason,
            )
        if name in {"naive_baseline", "atr_baseline"}:
            return ForecastModelCapabilities(model_name=name, installed=True, available=True)
        status, reason = self.status(name, config)
        probabilistic = name == "lag_llama"
        return ForecastModelCapabilities(
            model_name=name,
            supports_quantiles=name in {"chronos", "lag_llama", "moirai_uni2ts", "moirai", "uni2ts"},
            supports_probabilistic_paths=probabilistic,
            supports_zero_shot=name in {"chronos", "lag_llama", "moirai_uni2ts", "moirai", "uni2ts"},
            requires_training=name in {"neuralforecast", "autogluon"},
            requires_local_model_path=False,
            installed=status != MISSING_DEPENDENCY,
            available=is_available_status(status),
            unavailable_reason=None if is_available_status(status) else reason,
        )

    def forecast_normalized(
        self,
        model_name: str,
        request: dict[str, Any],
        config: ForecastConfig,
    ) -> NormalizedForecastResult:
        name = str(model_name).lower().replace("-", "_")
        symbol = str(request.get("symbol") or "").upper()
        timeframe = str(request.get("timeframe") or config.timeframe)
        horizon = int(request.get("horizon_bars") or config.horizon_bars)
        series = _to_float_sequence(request.get("series", []))
        closes = _to_float_sequence(request.get("closes", series))
        target = str(request.get("target") or config.target)
        try:
            if name == "timesfm":
                if self.timesfm_engine is None:
                    raise TimesFMUnavailableError("TimesFM engine is not configured.")
                output = self.timesfm_engine.forecast(series, horizon=horizon, config=config)
            elif name == "naive_baseline":
                point = [series[-1]] * horizon if series else []
                output = TimesFMForecastOutput(point, point, point)
            elif name == "atr_baseline":
                drift = (series[-1] - series[-2]) if len(series) > 1 else 0.0
                point = [series[-1] + drift * (index + 1) for index in range(horizon)] if series else []
                output = TimesFMForecastOutput(point, point, point)
            else:
                adapter = self.get(name)
                if adapter is None:
                    raise TimesFMUnavailableError(f"Provider is not registered: {name}")
                output = adapter.forecast(
                    series,
                    closes=closes,
                    horizon=horizon,
                    target=target,
                    config=config,
                )
        except TimesFMUnavailableError as exc:
            reason = str(exc)
            status = status_from_reason(reason)
            return NormalizedForecastResult(
                model_name=name,
                symbol=symbol,
                timeframe=timeframe,
                horizon_bars=horizon,
                generated_at=utc_now_iso(),
                status=status,
                warnings=[reason],
            )
        except Exception as exc:
            reason = str(exc)
            return NormalizedForecastResult(
                model_name=name,
                symbol=symbol,
                timeframe=timeframe,
                horizon_bars=horizon,
                generated_at=utc_now_iso(),
                status=status_from_reason(reason),
                warnings=[reason],
            )
        point = list(output.q50_path)
        start = closes[-1] if closes else (series[-1] if series else None)
        end = point[-1] if point else None
        expected_return = ((end - start) / start * 100) if start and end is not None and target != "log_return" else None
        direction_value = sum(point) if target == "log_return" else ((end - start) if start is not None and end is not None else 0.0)
        return NormalizedForecastResult(
            model_name=name,
            symbol=symbol,
            timeframe=timeframe,
            horizon_bars=horizon,
            generated_at=utc_now_iso(),
            status="OK",
            point_forecast=point,
            quantiles={"0.10": list(output.q10_path), "0.50": point, "0.90": list(output.q90_path)},
            prediction_intervals=output.prediction_intervals,
            direction="UP" if direction_value > 0 else "DOWN" if direction_value < 0 else "FLAT",
            expected_return_pct=expected_return,
            prob_touch_entry=output.prob_touch_entry,
            prob_touch_stop_before_entry=output.prob_touch_stop_before_entry,
            warnings=list(output.warnings),
        )


class ChronosAdapter:
    name = "chronos"

    def __init__(self) -> None:
        self._pipelines: dict[tuple[str, str, str], Any] = {}

    def forecast(
        self,
        series: list[float],
        *,
        closes: list[float],
        horizon: int,
        target: str,
        config: ForecastConfig,
    ) -> TimesFMForecastOutput:
        del closes, target
        if not series:
            raise TimesFMForecastError("Chronos requires a non-empty input series.")
        options = provider_options(config, self.name)
        if _is_external_worker(options):
            return self._forecast_with_external_worker(
                series,
                horizon=horizon,
                config=config,
                options=options,
            )
        chronos = _import_optional("chronos", "chronos-forecasting")
        torch = _import_optional("torch", "torch")
        pipeline_cls = (
            getattr(chronos, "Chronos2Pipeline", None)
            or getattr(chronos, "BaseChronosPipeline", None)
            or getattr(chronos, "ChronosPipeline", None)
        )
        if pipeline_cls is None:
            raise TimesFMUnavailableError(
                "The installed chronos package does not expose a Chronos pipeline class."
            )
        model_repo = str(options.get("model_repo") or "amazon/chronos-2")
        try:
            kwargs = {
                "device_map": _device_map(config.device),
            }
            dtype_name = str(options.get("torch_dtype") or "bfloat16")
            if hasattr(torch, dtype_name):
                kwargs["torch_dtype"] = getattr(torch, dtype_name)
            cache_key = (model_repo, str(kwargs["device_map"]), dtype_name)
            pipeline = self._pipelines.get(cache_key)
            if pipeline is None:
                pipeline = pipeline_cls.from_pretrained(model_repo, **kwargs)
                self._pipelines[cache_key] = pipeline
            context = torch.tensor(series[-config.context_bars :], dtype=torch.float32)
            predict_quantiles = getattr(pipeline, "predict_quantiles", None)
            if callable(predict_quantiles):
                arguments = {
                    "prediction_length": horizon,
                    "quantile_levels": [0.1, 0.5, 0.9],
                }
                if getattr(chronos, "Chronos2Pipeline", None) is pipeline_cls:
                    arguments["inputs"] = context.reshape(1, 1, -1)
                else:
                    arguments["context"] = context
                quantiles, mean = predict_quantiles(**arguments)
                return _with_hf_warning(
                    output_from_quantile_tensor(quantiles, mean, horizon),
                    options,
                )
            samples = pipeline.predict(
                context,
                prediction_length=horizon,
                num_samples=int(options.get("num_samples") or 100),
            )
        except TimesFMUnavailableError:
            raise
        except Exception as exc:
            raise TimesFMForecastError(f"Chronos forecast failed: {exc}") from exc
        return _with_hf_warning(output_from_samples(samples, horizon), options)

    def _forecast_with_external_worker(
        self,
        series: list[float],
        *,
        horizon: int,
        config: ForecastConfig,
        options: dict[str, Any],
    ) -> TimesFMForecastOutput:
        python = Path(str(options.get("python_executable") or ""))
        if not python.is_file():
            raise TimesFMUnavailableError(f"Chronos python executable not found: {python}")
        worker = Path(__file__).with_name("chronos_worker.py")
        request = {
            "series": series[-config.context_bars :],
            "horizon": horizon,
            "model_repo": str(options.get("model_repo") or "amazon/chronos-2"),
            "device": _device_map(str(options.get("device") or config.device)),
            "torch_dtype": str(options.get("torch_dtype") or "bfloat16"),
            "num_samples": int(options.get("num_samples") or 100),
            "hf_token_env": str(options.get("hf_token_env") or "HF_TOKEN"),
            "local_files_only": bool(options.get("local_files_only", False)),
        }
        timeout = int(options.get("worker_timeout_seconds") or config.worker_timeout_seconds)
        worker_env = os.environ.copy()
        if request["local_files_only"]:
            worker_env["HF_HUB_OFFLINE"] = "1"
            worker_env["TRANSFORMERS_OFFLINE"] = "1"
        try:
            completed = subprocess.run(
                [str(python), str(worker)],
                input=json.dumps(request),
                text=True,
                capture_output=True,
                timeout=timeout,
                check=False,
                env=worker_env,
            )
        except subprocess.TimeoutExpired as exc:
            raise TimesFMForecastError(f"Chronos worker timed out after {timeout}s") from exc
        if completed.returncode != 0:
            detail = (completed.stderr or completed.stdout or "").strip()
            raise TimesFMForecastError(
                detail or f"Chronos worker exited with {completed.returncode}"
            )
        payload = _json_from_last_stdout_line(completed.stdout, provider="Chronos")
        if not payload.get("ok"):
            raise TimesFMForecastError(str(payload.get("error") or "Chronos worker failed"))
        output = normalize_provider_output(payload, horizon)
        worker_warnings = payload.get("warnings")
        if isinstance(worker_warnings, list):
            output = TimesFMForecastOutput(
                q10_path=output.q10_path,
                q50_path=output.q50_path,
                q90_path=output.q90_path,
                prob_touch_entry=output.prob_touch_entry,
                prob_touch_stop_before_entry=output.prob_touch_stop_before_entry,
                prediction_intervals=output.prediction_intervals,
                warnings=[str(item) for item in worker_warnings],
            )
        return output


class DottedCallableProviderAdapter:
    def __init__(
        self,
        name: str,
        *,
        package_name: str,
        default_callable: str,
        required_packages: tuple[str, ...] = (),
    ) -> None:
        self.name = name
        self.package_name = package_name
        self.default_callable = default_callable
        self.required_packages = required_packages

    def forecast(
        self,
        series: list[float],
        *,
        closes: list[float],
        horizon: int,
        target: str,
        config: ForecastConfig,
    ) -> TimesFMForecastOutput:
        options = provider_options(config, self.name)
        if _is_external_worker(options):
            return _forecast_with_external_provider_worker(
                self.name,
                series,
                closes=closes,
                horizon=horizon,
                target=target,
                config=config,
                options=options,
            )
        _import_optional(self.package_name, self.package_name)
        callable_path = str(options.get("callable") or self.default_callable)
        provider = _load_callable(callable_path)
        try:
            output = provider(
                series=series,
                closes=closes,
                horizon=horizon,
                target=target,
                config=config,
                options=options,
            )
        except Exception as exc:
            raise TimesFMForecastError(f"{self.name} forecast failed: {exc}") from exc
        return normalize_provider_output(output, horizon)


class NeuralForecastAdapter:
    name = "neuralforecast"

    def forecast(
        self,
        series: list[float],
        *,
        closes: list[float],
        horizon: int,
        target: str,
        config: ForecastConfig,
    ) -> TimesFMForecastOutput:
        del closes, target
        options = provider_options(config, self.name)
        if _is_external_worker(options):
            return _forecast_with_external_provider_worker(
                self.name,
                series,
                closes=[],
                horizon=horizon,
                target=config.target,
                config=config,
                options=options,
            )
        nf_module = _import_optional("neuralforecast", "neuralforecast")
        pandas = _import_optional("pandas", "pandas")
        try:
            neural_forecast_cls = getattr(nf_module, "NeuralForecast")
            frame = pandas.DataFrame(
                {
                    "unique_id": ["series"] * len(series),
                    "ds": range(len(series)),
                    "y": series,
                }
            )
            model_path = str(options.get("model_path") or "").strip()
            if model_path:
                predictor = neural_forecast_cls.load(path=model_path)
                model_names: list[str] = []
            else:
                model_names = _configured_model_names(
                    options,
                    default=("NHITS",),
                )
                models_module = importlib.import_module("neuralforecast.models")
                models = [
                    _build_neuralforecast_model(
                        models_module,
                        model_name,
                        horizon=horizon,
                        context_size=len(series),
                        max_steps=int(options.get("max_steps") or 25),
                    )
                    for model_name in model_names
                ]
                # The frame's ds column is an integer range, so the frequency
                # must be an integer as well; pandas offsets like "15min" are
                # rejected by NeuralForecast for integer time axes.
                raw_freq = str(options.get("freq") or "").strip()
                predictor = neural_forecast_cls(
                    models=models,
                    freq=int(raw_freq) if raw_freq.lstrip("-").isdigit() else 1,
                )
                predictor.fit(df=frame)
            prediction = predictor.predict(df=frame)
        except Exception as exc:
            raise TimesFMForecastError(f"NeuralForecast forecast failed: {exc}") from exc
        return output_from_prediction_frame(
            prediction,
            horizon,
            preferred_columns=model_names,
        )


class AutoGluonAdapter:
    name = "autogluon"

    def forecast(
        self,
        series: list[float],
        *,
        closes: list[float],
        horizon: int,
        target: str,
        config: ForecastConfig,
    ) -> TimesFMForecastOutput:
        del closes, target
        options = provider_options(config, self.name)
        if _is_external_worker(options):
            return _forecast_with_external_provider_worker(
                self.name,
                series,
                closes=[],
                horizon=horizon,
                target=config.target,
                config=config,
                options=options,
            )
        ts_module = _import_optional("autogluon.timeseries", "autogluon.timeseries")
        pandas = _import_optional("pandas", "pandas")
        try:
            predictor_cls = getattr(ts_module, "TimeSeriesPredictor")
            data_cls = getattr(ts_module, "TimeSeriesDataFrame")
            timestamps = pandas.date_range(
                start="2000-01-01",
                periods=len(series),
                freq=str(options.get("freq") or _frequency(config.timeframe)),
            )
            data_frame = pandas.DataFrame(
                {
                    "item_id": ["series"] * len(series),
                    "timestamp": timestamps,
                    "target": series,
                }
            )
            train_data = data_cls.from_data_frame(
                data_frame,
                id_column="item_id",
                timestamp_column="timestamp",
            )
            model_path = str(options.get("model_path") or "").strip()
            # ignore_cleanup_errors: AutoGluon's file logger keeps
            # predictor_log.txt open on Windows, which would otherwise turn a
            # successful forecast into a cleanup failure.
            context = (
                nullcontext(None)
                if model_path
                else TemporaryDirectory(
                    prefix="setup-order-autogluon-",
                    ignore_cleanup_errors=True,
                )
            )
            with context as temporary_path:
                if model_path:
                    predictor = predictor_cls.load(model_path)
                else:
                    predictor_kwargs: dict[str, Any] = {
                        "prediction_length": horizon,
                        "target": "target",
                        "quantile_levels": [0.1, 0.5, 0.9],
                        "eval_metric": str(options.get("eval_metric") or "MASE"),
                        "path": temporary_path,
                        "log_to_file": False,
                    }
                    predictor = predictor_cls(
                        **_supported_predictor_kwargs(predictor_cls, predictor_kwargs)
                    )
                    fit_kwargs: dict[str, Any] = {
                        "train_data": train_data,
                        "presets": str(options.get("presets") or "fast_training"),
                    }
                    time_limit = int(options.get("time_limit_seconds") or 60)
                    if time_limit > 0:
                        fit_kwargs["time_limit"] = time_limit
                    predictor.fit(**fit_kwargs)
                prediction = predictor.predict(train_data)
        except Exception as exc:
            raise TimesFMForecastError(f"AutoGluon forecast failed: {exc}") from exc
        return output_from_prediction_frame(prediction, horizon)


def adapter_available(adapter: ForecastAdapter, config: ForecastConfig) -> tuple[bool, str]:
    status, reason = adapter_health(adapter, config)
    return is_available_status(status), reason


def adapter_health(adapter: ForecastAdapter, config: ForecastConfig) -> tuple[str, str]:
    if isinstance(adapter, ChronosAdapter):
        options = provider_options(config, adapter.name)
        if _is_external_worker(options):
            return _external_module_sequence_health(
                ("chronos", "torch"),
                options,
                provider=adapter.name,
            )
        status, reason = _module_sequence_health(("chronos", "torch"))
        if not is_available_status(status):
            return status, reason
        return AVAILABLE, "available"
    elif isinstance(adapter, NeuralForecastAdapter):
        options = provider_options(config, adapter.name)
        if _is_external_worker(options):
            return _external_module_sequence_health(
                ("neuralforecast", "pandas"),
                options,
                provider=adapter.name,
            )
        status, reason = _module_sequence_health(("neuralforecast", "neuralforecast.models", "pandas"))
        if not is_available_status(status):
            return status, reason
        return AVAILABLE, "available"
    elif isinstance(adapter, AutoGluonAdapter):
        options = provider_options(config, adapter.name)
        if _is_external_worker(options):
            return _external_module_sequence_health(
                ("autogluon.timeseries", "pandas"),
                options,
                provider=adapter.name,
            )
        status, reason = _module_sequence_health(("autogluon.timeseries", "pandas"))
        if not is_available_status(status):
            return status, reason
        return AVAILABLE, "available"
    elif isinstance(adapter, DottedCallableProviderAdapter):
        options = provider_options(config, adapter.name)
        packages = (adapter.package_name, *adapter.required_packages)
        if _is_external_worker(options):
            status, reason = _external_module_sequence_health(
                packages,
                options,
                provider=adapter.name,
            )
        else:
            status, reason = _module_sequence_health(packages)
        if not is_available_status(status):
            return status, reason
        callable_path = str(options.get("callable") or adapter.default_callable)
        try:
            _load_callable(callable_path)
        except TimesFMUnavailableError as exc:
            return LOAD_ERROR, str(exc)
        return status, reason
    else:
        return AVAILABLE, "available"


def provider_options(config: ForecastConfig, model_name: str) -> dict[str, Any]:
    raw = getattr(config, "provider_options", {})
    if not isinstance(raw, dict):
        return {}
    options = raw.get(model_name, {})
    return options if isinstance(options, dict) else {}


def _external_python_path(options: dict[str, Any]) -> Path | None:
    raw = str(options.get("python_executable") or "").strip()
    if not raw:
        return None
    return Path(raw).expanduser()


def _runtime_mode(options: dict[str, Any], *, default: str = "in_process") -> str:
    configured = str(options.get("runtime_mode") or "").strip().lower().replace("-", "_")
    if configured:
        return configured
    return "external_worker" if _external_python_path(options) is not None else default


def _is_external_worker(options: dict[str, Any]) -> bool:
    return _runtime_mode(options) == "external_worker"


def _external_worker_script_path(options: dict[str, Any]) -> Path:
    raw = str(options.get("worker_script") or "").strip()
    return Path(raw).expanduser() if raw else Path(__file__).with_name("provider_worker.py")


def _external_module_sequence_health(
    packages: tuple[str, ...],
    options: dict[str, Any],
    *,
    provider: str,
) -> tuple[str, str]:
    python = _external_python_path(options)
    if python is None:
        return WORKER_NOT_CONFIGURED, f"{provider} external python executable is not configured."
    if not python.is_file():
        return WORKER_UNREACHABLE, f"{provider} python executable not found: {python}"
    worker = _external_worker_script_path(options)
    if not worker.is_file():
        return WORKER_UNREACHABLE, f"{provider} worker script not found: {worker}"
    if not bool(options.get("strict_dependency_probe", False)):
        return EXTERNAL_WORKER_CONFIGURED, "external worker configured"
    code = (
        "import importlib.util,json,sys\n"
        "packages=json.loads(sys.stdin.read() or '[]')\n"
        "for package in packages:\n"
        "    try:\n"
        "        spec=importlib.util.find_spec(package)\n"
        "    except ModuleNotFoundError as exc:\n"
        "        print(json.dumps({'ok':False,'status':'MISSING_DEPENDENCY','reason':'Missing optional package(s): '+str(getattr(exc,'name',None) or package)}))\n"
        "        raise SystemExit(0)\n"
        "    except ImportError as exc:\n"
        "        print(json.dumps({'ok':False,'status':'LOAD_ERROR','reason':'Optional package load error for '+package+': '+str(exc)}))\n"
        "        raise SystemExit(0)\n"
        "    except Exception as exc:\n"
        "        print(json.dumps({'ok':False,'status':'LOAD_ERROR','reason':'Optional package load error for '+package+': '+str(exc)}))\n"
        "        raise SystemExit(0)\n"
        "    if spec is None:\n"
        "        print(json.dumps({'ok':False,'status':'MISSING_DEPENDENCY','reason':'Missing optional package(s): '+package}))\n"
        "        raise SystemExit(0)\n"
        "print(json.dumps({'ok':True}))\n"
    )
    timeout = int(options.get("health_timeout_seconds") or 5)
    try:
        completed = subprocess.run(
            [str(python), "-c", code],
            input=json.dumps(list(packages)),
            text=True,
            capture_output=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return WORKER_UNREACHABLE, f"{provider} dependency probe timed out after {timeout}s"
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "").strip()
        return WORKER_UNREACHABLE, detail or f"{provider} dependency probe exited with {completed.returncode}"
    try:
        payload = _json_from_last_stdout_line(completed.stdout, provider=f"{provider} dependency probe")
    except TimesFMForecastError as exc:
        return WORKER_UNREACHABLE, str(exc)
    if payload.get("ok"):
        return EXTERNAL_WORKER_OK, "external worker healthcheck OK"
    status = str(payload.get("status") or LOAD_ERROR)
    reason = str(payload.get("reason") or f"{provider} dependency probe failed")
    return (MISSING_DEPENDENCY if status == MISSING_DEPENDENCY else LOAD_ERROR), reason


def _forecast_with_external_provider_worker(
    model_name: str,
    series: list[float],
    *,
    closes: list[float],
    horizon: int,
    target: str,
    config: ForecastConfig,
    options: dict[str, Any],
) -> TimesFMForecastOutput:
    python = _external_python_path(options)
    if python is None:
        raise TimesFMUnavailableError(f"{model_name} worker is not configured.")
    if not python.is_file():
        raise TimesFMUnavailableError(f"{model_name} python executable not found: {python}")
    worker = _external_worker_script_path(options)
    if not worker.is_file():
        raise TimesFMUnavailableError(f"{model_name} worker script not found: {worker}")
    request = {
        "model_name": model_name,
        "series": series[-config.context_bars :],
        "closes": closes[-config.context_bars :] if closes else [],
        "horizon": horizon,
        "target": target,
        "config": config.to_dict(),
    }
    timeout = int(
        options.get("timeout_seconds")
        or options.get("worker_timeout_seconds")
        or config.worker_timeout_seconds
    )
    worker_env = os.environ.copy()
    repo_root = str(Path(__file__).resolve().parents[2])
    if worker_env.get("PYTHONPATH"):
        worker_env["PYTHONPATH"] = repo_root + os.pathsep + worker_env["PYTHONPATH"]
    else:
        worker_env["PYTHONPATH"] = repo_root
    if bool(options.get("local_files_only", False)):
        worker_env["HF_HUB_OFFLINE"] = "1"
        worker_env["TRANSFORMERS_OFFLINE"] = "1"
    try:
        completed = subprocess.run(
            [str(python), str(worker)],
            input=json.dumps(request),
            text=True,
            capture_output=True,
            timeout=timeout,
            check=False,
            env=worker_env,
        )
    except subprocess.TimeoutExpired as exc:
        raise TimesFMForecastError(f"{model_name} worker timed out after {timeout}s") from exc
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "").strip()
        raise TimesFMForecastError(detail or f"{model_name} worker exited with {completed.returncode}")
    payload = _json_from_last_stdout_line(completed.stdout, provider=model_name)
    if not payload.get("ok"):
        raise TimesFMForecastError(str(payload.get("error") or f"{model_name} worker failed"))
    return normalize_provider_output(payload, horizon)


def _with_hf_warning(
    output: TimesFMForecastOutput,
    options: dict[str, Any],
) -> TimesFMForecastOutput:
    token_env = str(options.get("hf_token_env") or "HF_TOKEN")
    if os.getenv(token_env):
        return output
    return TimesFMForecastOutput(
        q10_path=output.q10_path,
        q50_path=output.q50_path,
        q90_path=output.q90_path,
        prob_touch_entry=output.prob_touch_entry,
        prob_touch_stop_before_entry=output.prob_touch_stop_before_entry,
        prediction_intervals=output.prediction_intervals,
        warnings=[
            *output.warnings,
            f"{token_env} is not set; cached models still work and online downloads may be rate-limited.",
        ],
    )


def _json_from_last_stdout_line(stdout: str, *, provider: str) -> dict[str, Any]:
    for line in reversed([item.strip() for item in stdout.splitlines() if item.strip()]):
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            return payload
    raise TimesFMForecastError(f"{provider} worker did not return JSON.")


def normalize_provider_output(output: Any, horizon: int) -> TimesFMForecastOutput:
    if isinstance(output, TimesFMForecastOutput):
        return output
    if isinstance(output, dict):
        quantiles = output.get("quantiles") if isinstance(output.get("quantiles"), dict) else {}
        q10 = _sequence_from_mapping(output, "q10_path", "q10", "p10", "0.1", "0.10") or _sequence_from_mapping(quantiles, "0.1", "0.10", "q10")
        q50 = _sequence_from_mapping(output, "q50_path", "q50", "median", "p50", "0.5", "0.50") or _sequence_from_mapping(quantiles, "0.5", "0.50", "q50")
        q90 = _sequence_from_mapping(output, "q90_path", "q90", "p90", "0.9", "0.90") or _sequence_from_mapping(quantiles, "0.9", "0.90", "q90")
        if q50:
            completed = _complete_quantiles(q10, q50, q90, horizon)
            return TimesFMForecastOutput(
                q10_path=completed.q10_path,
                q50_path=completed.q50_path,
                q90_path=completed.q90_path,
                prob_touch_entry=_probability(output.get("prob_touch_entry")),
                prob_touch_stop_before_entry=_probability(output.get("prob_touch_stop_before_entry")),
                prediction_intervals=(
                    output.get("prediction_intervals")
                    if isinstance(output.get("prediction_intervals"), dict)
                    else None
                ),
                warnings=[str(item) for item in output.get("warnings", [])] if isinstance(output.get("warnings"), list) else [],
            )
    return output_from_samples(output, horizon)


def output_from_quantile_tensor(
    quantiles: Any,
    mean: Any,
    horizon: int,
) -> TimesFMForecastOutput:
    """Normalize Chronos' [batch, horizon, quantile] output across releases."""
    values = _nested_list(quantiles)
    while isinstance(values, list) and len(values) == 1 and isinstance(values[0], list):
        values = values[0]
    rows = values if isinstance(values, list) else []
    if rows and len(rows) == 3 and all(isinstance(row, list) and len(row) >= horizon for row in rows):
        q10, q50, q90 = (_to_float_sequence(row)[:horizon] for row in rows)
    elif rows and all(isinstance(row, list) and len(row) >= 3 for row in rows[:horizon]):
        matrix = [_to_float_sequence(row) for row in rows[:horizon]]
        q10 = [row[0] for row in matrix]
        q50 = [row[1] for row in matrix]
        q90 = [row[2] for row in matrix]
    else:
        return output_from_samples(mean, horizon)
    mean_values = _flatten_singleton_vector(mean)
    if len(mean_values) >= horizon:
        q50 = mean_values[:horizon]
    return _complete_quantiles(q10, q50, q90, horizon)


def output_from_prediction_frame(
    frame: Any,
    horizon: int,
    *,
    preferred_columns: list[str] | None = None,
) -> TimesFMForecastOutput:
    """Extract point and quantile paths from NeuralForecast/AutoGluon frames."""
    columns = [str(column) for column in getattr(frame, "columns", [])]
    paths: dict[str, list[float]] = {}
    for original, name in zip(getattr(frame, "columns", []), columns):
        try:
            values = _to_float_sequence(frame[original])
        except (KeyError, TypeError):
            continue
        if values:
            paths[name] = values[-horizon:]
    if not paths:
        return normalize_provider_output(frame, horizon)

    q10 = _named_path(paths, "0.1", "0.10", "q10", "p10")
    q50 = _named_path(paths, "0.5", "0.50", "median", "q50", "mean")
    q90 = _named_path(paths, "0.9", "0.90", "q90", "p90")
    if q50:
        return _complete_quantiles(q10, q50, q90, horizon)

    ignored = {"unique_id", "ds", "item_id", "timestamp"}
    candidates = [
        values
        for name, values in paths.items()
        if name.lower() not in ignored
        and (
            not preferred_columns
            or any(name.lower().startswith(preferred.lower()) for preferred in preferred_columns)
        )
    ]
    if not candidates:
        candidates = [values for name, values in paths.items() if name.lower() not in ignored]
    return output_from_samples(candidates, horizon)


def _build_neuralforecast_model(
    models_module: Any,
    model_name: str,
    *,
    horizon: int,
    context_size: int,
    max_steps: int,
) -> Any:
    model_cls = getattr(models_module, model_name, None)
    if model_cls is None:
        raise TimesFMUnavailableError(f"NeuralForecast model is unavailable: {model_name}")
    kwargs: dict[str, Any] = {
        "h": horizon,
        "input_size": max(horizon * 2, min(context_size, horizon * 8)),
        "max_steps": max_steps,
    }
    try:
        parameters = inspect.signature(model_cls).parameters
    except (TypeError, ValueError):
        parameters = {}
    if "n_series" in parameters:
        kwargs["n_series"] = 1
    if parameters and not any(item.kind is inspect.Parameter.VAR_KEYWORD for item in parameters.values()):
        kwargs = {key: value for key, value in kwargs.items() if key in parameters}
    return model_cls(**kwargs)


def _configured_model_names(
    options: dict[str, Any],
    *,
    default: tuple[str, ...],
) -> list[str]:
    raw = options.get("models", options.get("model"))
    if isinstance(raw, str):
        names = [raw]
    elif isinstance(raw, list | tuple):
        names = [str(item) for item in raw]
    else:
        names = list(default)
    return [name.strip() for name in names if name.strip()]


def _frequency(timeframe: str) -> str:
    return {
        "3m": "3min",
        "10m": "10min",
        "15m": "15min",
        "30m": "30min",
        "1h": "h",
        "4h": "4h",
        "1d": "D",
    }.get(str(timeframe).lower(), "15min")


def _supported_predictor_kwargs(callable_object: Any, values: dict[str, Any]) -> dict[str, Any]:
    try:
        parameters = inspect.signature(callable_object).parameters
    except (TypeError, ValueError):
        return values
    if any(item.kind is inspect.Parameter.VAR_KEYWORD for item in parameters.values()):
        return values
    return {key: value for key, value in values.items() if key in parameters}


def _named_path(paths: dict[str, list[float]], *names: str) -> list[float]:
    normalized = {key.lower(): values for key, values in paths.items()}
    for name in names:
        if name in normalized:
            return normalized[name]
    return []


def _nested_list(value: Any) -> Any:
    if hasattr(value, "detach"):
        value = value.detach()
    if hasattr(value, "cpu"):
        value = value.cpu()
    if hasattr(value, "tolist"):
        return value.tolist()
    return value


def _flatten_singleton_vector(value: Any) -> list[float]:
    value = _nested_list(value)
    while isinstance(value, list) and len(value) == 1 and isinstance(value[0], list):
        value = value[0]
    return _to_float_sequence(value)


def output_from_samples(samples: Any, horizon: int) -> TimesFMForecastOutput:
    rows = _to_nested_float_rows(samples)
    if not rows:
        raise TimesFMForecastError("Provider returned no numeric forecast samples.")
    if len(rows) == 1 and len(rows[0]) >= horizon:
        point = rows[0][:horizon]
        return TimesFMForecastOutput(q10_path=point, q50_path=point, q90_path=point)
    length = min(horizon, min(len(row) for row in rows if row))
    if length <= 0:
        raise TimesFMForecastError("Provider returned empty forecast paths.")
    columns = [[row[index] for row in rows if len(row) > index] for index in range(length)]
    q10 = [_quantile(column, 0.10) for column in columns]
    q50 = [_quantile(column, 0.50) for column in columns]
    q90 = [_quantile(column, 0.90) for column in columns]
    return TimesFMForecastOutput(q10_path=q10, q50_path=q50, q90_path=q90)


def _complete_quantiles(
    q10: list[float],
    q50: list[float],
    q90: list[float],
    horizon: int,
) -> TimesFMForecastOutput:
    q50 = _fit_horizon(q50, horizon)
    if not q10:
        q10 = list(q50)
    if not q90:
        q90 = list(q50)
    return TimesFMForecastOutput(
        q10_path=_fit_horizon(q10, len(q50)),
        q50_path=q50,
        q90_path=_fit_horizon(q90, len(q50)),
    )


def _sequence_from_mapping(payload: dict[str, Any], *keys: str) -> list[float]:
    for key in keys:
        if key in payload:
            return _to_float_sequence(payload[key])
    return []


def _to_nested_float_rows(value: Any) -> list[list[float]]:
    if hasattr(value, "to_numpy"):
        value = value.to_numpy()
    if hasattr(value, "tolist"):
        value = value.tolist()
    if isinstance(value, dict):
        return [_to_float_sequence(value.get("prediction") or value.get("mean") or value.get("q50") or [])]
    if isinstance(value, list | tuple):
        if not value:
            return []
        if all(_is_number(item) for item in value):
            return [_to_float_sequence(value)]
        rows: list[list[float]] = []
        for item in value:
            rows.extend(_to_nested_float_rows(item))
        return rows
    return []


def _to_float_sequence(value: Any) -> list[float]:
    if hasattr(value, "tolist"):
        value = value.tolist()
    if isinstance(value, list | tuple):
        numbers = []
        for item in value:
            if _is_number(item):
                numbers.append(float(item))
        return numbers
    if _is_number(value):
        return [float(value)]
    return []


def _fit_horizon(values: list[float], horizon: int) -> list[float]:
    if not values:
        return []
    if len(values) >= horizon:
        return values[:horizon]
    return values + [values[-1]] * (horizon - len(values))


def _quantile(values: list[float], quantile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(float(value) for value in values if math.isfinite(float(value)))
    if not ordered:
        return 0.0
    if len(ordered) == 1:
        return ordered[0]
    position = (len(ordered) - 1) * quantile
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[int(position)]
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)


def _is_number(value: Any) -> bool:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return False
    return math.isfinite(number)


def _load_callable(path: str) -> Callable[..., Any]:
    module_name, separator, function_name = path.partition(":")
    if not separator:
        module_name, _, function_name = path.rpartition(".")
    if not module_name or not function_name:
        raise TimesFMUnavailableError(f"Invalid provider callable path: {path}")
    try:
        module = importlib.import_module(module_name)
    except (ImportError, ModuleNotFoundError) as exc:
        raise TimesFMUnavailableError(f"Provider callable module is not available: {module_name}") from exc
    provider = getattr(module, function_name, None)
    if not callable(provider):
        raise TimesFMUnavailableError(f"Provider callable is not available: {path}")
    return provider


def _module_available(package: str) -> bool:
    try:
        return importlib.util.find_spec(package) is not None
    except ModuleNotFoundError:
        return False


def _module_sequence_health(packages: tuple[str, ...]) -> tuple[str, str]:
    missing = [package for package in packages if not _module_available(package)]
    if missing:
        return MISSING_DEPENDENCY, f"Missing optional package(s): {', '.join(missing)}"
    return AVAILABLE, "available"


def _import_optional(module_name: str, install_name: str) -> Any:
    try:
        return importlib.import_module(module_name)
    except ImportError as exc:
        raise TimesFMUnavailableError(
            f"Optional forecast provider '{install_name}' is not installed."
        ) from exc


def _device_map(device: str) -> str:
    normalized = str(device or "auto").lower()
    if normalized in {"cpu", "cuda", "mps", "auto"}:
        return normalized
    return "auto"


def _probability(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return max(0.0, min(1.0, number)) if math.isfinite(number) else None
