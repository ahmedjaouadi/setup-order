from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any


DEFAULT_CONFIG: dict[str, Any] = {
    "app": {
        "environment": "development",
        "mode": "simulation",
        "timezone": "Europe/Paris",
    },
    "broker": {
        "host": "127.0.0.1",
        "port": 7497,
        "paper_port": 7497,
        "live_port": 7496,
        "client_id": 1001,
        "reconnect": True,
        "reconnect_interval_seconds": 5,
        "tws_audit_enabled": True,
        "market_data_source": "historical",
        "historical_duration": "30 D",
        "historical_bar_size": "1 day",
        "historical_what_to_show": "TRADES",
        "historical_use_rth": True,
        "connector": "simulated",
    },
    "risk": {
        "max_open_positions": 5,
        "max_position_amount_usd": 250,
        "max_risk_per_trade_usd": 15,
        "max_daily_loss_usd": 50,
        "max_total_exposure_usd": 1000,
        "allow_short": False,
    },
    "market": {
        "allow_premarket": False,
        "allow_after_hours": False,
        "stale_data_seconds": 20,
        "tws_stock_poll_enabled": True,
        "tws_stock_poll_interval_seconds": 15,
        "tws_stock_quote_timeout_seconds": 4,
    },
    "orders": {
        "default_entry_order_type": "STP_LMT",
        "default_stop_order_type": "STP",
        "cancel_unfilled_entry_after_minutes": 30,
    },
    "setup_defaults": {
        "timeframes": {
            "signal": "15m",
            "confirmation": "1d",
        },
        "entry": {
            "trigger_offset": 0.02,
            "limit_offset": 0.05,
        },
        "breakout": {
            "valid_for_days": 5,
        },
        "retest": {
            "max_retest_days": 5,
        },
        "confirmation": {
            "min_volume_ratio": 0.8,
        },
        "momentum": {
            "volume_above_average": 1.5,
        },
        "range": {
            "min_days_inside_range": 5,
        },
    },
    "storage": {
        "database_file": "data/trading_state.sqlite",
        "setups_folder": "data/setups",
        "logs_folder": "data/logs",
    },
    "gui": {
        "host": "127.0.0.1",
        "port": 8000,
        "require_login": False,
    },
    "safety": {
        "emergency_exit_if_stop_fails": False,
        "max_stop_submit_retries": 3,
    },
    "emergency_stop": {
        "cancel_entry_orders": True,
        "keep_existing_stops": True,
        "close_positions_market": False,
    },
    "alerts": {
        "enabled": True,
        "telegram_enabled": False,
        "email_enabled": False,
    },
}


@dataclass(frozen=True, slots=True)
class Settings:
    raw: dict[str, Any]
    database_file: Path
    setups_folder: Path
    logs_folder: Path
    app_mode: str
    timezone: str
    broker_connector: str
    gui_host: str
    gui_port: int

    @classmethod
    def from_dict(cls, config: dict[str, Any]) -> "Settings":
        storage = config["storage"]
        app = config["app"]
        broker = config["broker"]
        gui = config["gui"]
        return cls(
            raw=config,
            database_file=Path(storage["database_file"]),
            setups_folder=Path(storage["setups_folder"]),
            logs_folder=Path(storage["logs_folder"]),
            app_mode=str(app["mode"]),
            timezone=str(app["timezone"]),
            broker_connector=str(broker.get("connector", "simulated")),
            gui_host=str(gui.get("host", "127.0.0.1")),
            gui_port=int(gui.get("port", 8000)),
        )


def deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def load_yaml_file(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        import yaml
    except ImportError as exc:
        raise RuntimeError(
            "PyYAML is required to load YAML files. Install requirements.txt first."
        ) from exc
    with path.open("r", encoding="utf-8") as handle:
        data = yaml.safe_load(handle) or {}
    if not isinstance(data, dict):
        raise ValueError(f"YAML root must be a mapping: {path}")
    return data


def load_settings(path: str | Path = "config.yaml") -> Settings:
    config_path = Path(path)
    overrides = load_yaml_file(config_path)
    merged = deep_merge(DEFAULT_CONFIG, overrides)
    settings = Settings.from_dict(merged)
    settings.database_file.parent.mkdir(parents=True, exist_ok=True)
    settings.setups_folder.mkdir(parents=True, exist_ok=True)
    settings.logs_folder.mkdir(parents=True, exist_ok=True)
    return settings
