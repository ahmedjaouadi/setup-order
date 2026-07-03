from __future__ import annotations

import importlib
import inspect
from pathlib import Path
from typing import Any


def lag_llama_forecast(
    *,
    series: list[float],
    closes: list[float],
    horizon: int,
    target: str,
    config: Any,
    options: dict[str, Any],
) -> Any:
    """Run Lag-Llama through its GluonTS estimator API across package releases."""
    del closes, target
    torch = importlib.import_module("torch")
    estimator_module = importlib.import_module("lag_llama.gluon.estimator")
    estimator_cls = getattr(estimator_module, "LagLlamaEstimator")
    checkpoint_path = _checkpoint_path(options)
    checkpoint = torch.load(checkpoint_path, map_location="cpu", weights_only=False)
    model_kwargs = (
        checkpoint.get("hyper_parameters", {}).get("model_kwargs", {})
        if isinstance(checkpoint, dict)
        else {}
    )
    estimator_kwargs = {
        "ckpt_path": checkpoint_path,
        "prediction_length": horizon,
        "context_length": min(len(series), int(options.get("context_length") or config.context_bars)),
        "input_size": model_kwargs.get("input_size"),
        "n_layer": model_kwargs.get("n_layer"),
        "n_embd_per_head": model_kwargs.get("n_embd_per_head"),
        "n_head": model_kwargs.get("n_head"),
        "scaling": str(options.get("scaling") or "mean"),
        "time_feat": True,
        "nonnegative_pred_samples": False,
        "batch_size": 1,
        "num_parallel_samples": int(options.get("num_samples") or 100),
        "device": torch.device(_device(config.device)),
    }
    estimator = estimator_cls(**_supported_kwargs(estimator_cls, estimator_kwargs))
    transformation = estimator.create_transformation()
    with _full_checkpoint_loading(torch):
        lightning_module = estimator.create_lightning_module()
    predictor = estimator.create_predictor(transformation, lightning_module)
    forecast = next(iter(predictor.predict(_gluonts_dataset(series, config.timeframe))))
    return getattr(forecast, "samples", forecast)


def moirai_uni2ts_forecast(
    *,
    series: list[float],
    closes: list[float],
    horizon: int,
    target: str,
    config: Any,
    options: dict[str, Any],
) -> Any:
    """Run Moirai/Uni2TS as an offline zero-shot probabilistic forecast."""
    del closes, target
    moirai = importlib.import_module("uni2ts.model.moirai")
    module_cls = getattr(moirai, "MoiraiModule")
    forecast_cls = getattr(moirai, "MoiraiForecast")
    model_repo = str(options.get("model_repo") or "Salesforce/moirai-1.1-R-small")
    module = module_cls.from_pretrained(model_repo)
    forecast_kwargs = {
        "module": module,
        "prediction_length": horizon,
        "context_length": min(len(series), int(options.get("context_length") or config.context_bars)),
        "patch_size": options.get("patch_size") or "auto",
        "num_samples": int(options.get("num_samples") or 100),
        "target_dim": 1,
        "feat_dynamic_real_dim": 0,
        "past_feat_dynamic_real_dim": 0,
    }
    model = forecast_cls(**_supported_kwargs(forecast_cls, forecast_kwargs))
    predictor = model.create_predictor(batch_size=int(options.get("batch_size") or 1))
    forecast = next(iter(predictor.predict(_gluonts_dataset(series, config.timeframe))))
    return getattr(forecast, "samples", forecast)


def _checkpoint_path(options: dict[str, Any]) -> str:
    configured = str(options.get("checkpoint_path") or "").strip()
    if configured:
        path = Path(configured).expanduser()
        if not path.exists():
            raise RuntimeError(f"Lag-Llama checkpoint not found: {path}")
        return str(path)
    hub = importlib.import_module("huggingface_hub")
    return str(hub.hf_hub_download(
        repo_id=str(options.get("checkpoint_repo") or "time-series-foundation-models/Lag-Llama"),
        filename=str(options.get("checkpoint_file") or "lag-llama.ckpt"),
    ))


def _gluonts_dataset(series: list[float], timeframe: str) -> Any:
    pandas = importlib.import_module("pandas")
    dataset_module = importlib.import_module("gluonts.dataset.pandas")
    pandas_dataset = getattr(dataset_module, "PandasDataset")
    values = pandas.Series(
        series,
        index=pandas.period_range(
            start="2000-01-01",
            periods=len(series),
            freq=_frequency(timeframe),
        ),
        dtype="float32",
    )
    return pandas_dataset({"series": values})


def _supported_kwargs(callable_object: Any, values: dict[str, Any]) -> dict[str, Any]:
    values = {key: value for key, value in values.items() if value is not None}
    try:
        parameters = inspect.signature(callable_object).parameters
    except (TypeError, ValueError):
        return values
    if any(item.kind is inspect.Parameter.VAR_KEYWORD for item in parameters.values()):
        return values
    return {key: value for key, value in values.items() if key in parameters}


def _frequency(timeframe: str) -> str:
    return {
        "3m": "3min", "10m": "10min", "15m": "15min", "30m": "30min",
        "1h": "h", "4h": "4h", "1d": "D",
    }.get(str(timeframe).lower(), "15min")


def _device(value: str) -> str:
    normalized = str(value or "auto").lower()
    return "cpu" if normalized == "auto" else normalized


class _full_checkpoint_loading:
    """Temporarily restore weights_only=False for torch.load calls.

    Lag-Llama reloads its trusted local checkpoint internally without passing
    weights_only=False, which torch>=2.6 rejects (hyper_parameters contain
    gluonts distribution objects, not just tensors).
    """

    def __init__(self, torch_module: Any) -> None:
        self.torch = torch_module
        self.original_load = torch_module.load

    def __enter__(self) -> None:
        original = self.original_load

        def load(*args: Any, **kwargs: Any) -> Any:
            kwargs.setdefault("weights_only", False)
            return original(*args, **kwargs)

        self.torch.load = load

    def __exit__(self, *exc_info: Any) -> None:
        self.torch.load = self.original_load
