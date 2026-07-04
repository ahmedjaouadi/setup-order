from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

DEFAULT_CONFIG: dict[str, Any] = {
    "app": {
        "environment": "development",
        "mode": "paper",
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
        "stock_exchange": "SMART",
        "primary_exchange": "",
        "primary_exchange_by_symbol": {},
        "market_data_source": "hybrid",
        "market_data_type": 1,
        "market_data_type_fallbacks": [],
        "live_quote_wait_seconds": 2.0,
        "historical_request_timeout_seconds": 12,
        "market_data_ttl": {
            "live_quote_seconds": 20,
            "hybrid_signal_seconds": 60,
            "atr_15m_seconds": 1200,
            "atr_1h_seconds": 5400,
            "historical_seconds": 300,
        },
        "historical_duration": "30 D",
        "historical_bar_size": "1 day",
        "hybrid_signal_duration": "5 D",
        "hybrid_signal_bar_size": "15 mins",
        "hybrid_atr_1h_duration": "30 D",
        "hybrid_atr_1h_bar_size": "1 hour",
        "historical_what_to_show": "TRADES",
        "historical_use_rth": True,
        "connector": "paper",
    },
    "risk": {
        "max_open_positions": 5,
        "max_position_amount_usd": 250,
        "max_risk_per_trade_usd": 15,
        "max_daily_loss_usd": 50,
        "max_total_exposure_usd": 1000,
        "allow_short": False,
    },
    "trailing_stop_loss": {
        "enabled_by_default": True,
        "required_for_all_new_setups": True,
        "legacy_fixed_stop_allowed": False,
        "legacy_fields": {
            "allow_initial_stop_loss": False,
            "allow_protective_stop": False,
            "migrate_existing_setups": True,
        },
        "default_mode": "AUTO_INTELLIGENT",
        "default_method": "HYBRID_ATR_STRUCTURE",
        "atr": {
            "timeframe": "1h",
            "period": 14,
            "multiplier_initial_mode": "AUTO",
            "multiplier_trailing_mode": "AUTO",
            "min_multiplier": 1.0,
            "max_multiplier": 3.5,
        },
        "structure": {
            "enabled": True,
            "references": [
                "HIGHER_LOW",
                "INTRADAY_SUPPORT",
                "RANGE_LOW",
                "BROKEN_RESISTANCE_AS_SUPPORT",
                "VWAP_PULLBACK",
                "PREVIOUS_DAY_LOW",
            ],
            "buffer_policy": "MAX_OF_TICK_SPREAD_ATR_FRACTION",
            "min_tick_buffer": 2,
            "spread_buffer_multiplier": 2,
            "atr_fraction_buffer": 0.1,
        },
        "stock_specific_adjustment": {
            "enabled": True,
            "use_volatility_regime": True,
            "use_liquidity_regime": True,
            "use_spread_regime": True,
            "use_relative_volume": True,
            "use_gap_percent": True,
            "use_intraday_range_percent": True,
        },
        "ratchet": {
            "enabled": True,
            "update_on_closed_bar_only": True,
            "timeframe": "15m",
            "never_lower_stop": True,
            "do_not_update_outside_rth": True,
            "do_not_update_if_spread_wide": True,
            "min_improvement_atr_fraction": 0.15,
            "allow_break_even_move": True,
            "break_even_trigger_r_multiple": 1.0,
        },
        "broker": {
            "prefer_native_ibkr_trailing": True,
            "fallback_to_managed_stop_updates": True,
            "require_attached_stop_before_entry_transmission": True,
            "require_parent_child_bracket": True,
            "block_if_broker_stop_not_confirmed": True,
        },
        "safety": {
            "block_entry_if_initial_stop_missing": True,
            "block_entry_if_trailing_stop_not_ready": True,
            "block_entry_if_risk_unknown": True,
            "block_entry_if_quantity_zero": True,
            "block_if_position_without_stop": True,
            "block_if_entry_order_without_stop": True,
            "never_lower_stop": True,
        },
    },
    "market": {
        "allow_premarket": False,
        "allow_after_hours": False,
        "stale_data_seconds": 20,
        "tws_stock_poll_enabled": True,
        "tws_stock_poll_interval_seconds": 15,
        "tws_stock_quote_timeout_seconds": 4,
        "tws_stock_poll_total_timeout_seconds": 120,
        "tws_historical_timeout_seconds": 15,
        "heartbeat_stale_seconds": 180,
        "tws_stock_poll_max_concurrency": 5,
        "opportunity_near_ready_threshold": 0.96,
        "opportunity_alert_cooldown_seconds": 300,
        "event_deduplication": {
            "enabled": True,
            "repeated_hold_cooldown_seconds": 300,
        },
    },
    "market_data": {
        "require_live_market_data_for_live_orders": True,
        "require_live_market_data_for_paper_orders": False,
        "allow_delayed_market_data_in_paper": True,
        "allow_delayed_market_data_in_simulation": True,
    },
    "broker_tracker": {
        "enabled": True,
        "refresh_seconds": 2,
        "stale_after_seconds": 10,
        "block_auto_execution_if_missing": True,
        "block_auto_execution_if_stale": True,
    },
    "execution_safety": {
        "block_new_entries_if_broker_tracker_stale": True,
        "block_new_entries_if_unprotected_order_exists": True,
        "block_new_entries_if_position_without_stop_exists": True,
        "block_new_entries_if_reconciliation_mismatch": True,
    },
    "indicators": {
        "atr_1h": {
            "required_for_paper": False,
            "required_for_live": True,
            "allow_stale_in_paper": True,
            "stale_after_minutes": 120,
        },
    },
    "market_context": {
        "enabled": True,
        "reference_index": "SPY",
        "sector_etf": "AUTO",
        "require_market_positive": False,
        "require_sector_positive": False,
        "require_stock_above_sector": True,
        "require_stock_above_market": True,
        "warn_if_sector_red": True,
        "warn_if_stock_lagging_sector": True,
        "block_if_market_broadly_red": False,
    },
    "forecasting": {
        "enabled": True,
        "provider": "timesfm",
        "model": "timesfm_2_5_200m",
        "model_repo": "google/timesfm-2.5-200m-pytorch",
        "python_executable": "",
        "default_models": [
            "timesfm",
            "chronos",
            "lag_llama",
            "moirai",
            "moirai_uni2ts",
            "uni2ts",
            "neuralforecast",
            "autogluon",
            "naive_baseline",
            "atr_baseline",
        ],
        "worker_timeout_seconds": 180,
        "auto_recalc_interval_seconds": 900,
        "backend": "torch",
        "device": "auto",
        "timeframe": "15m",
        "target": "log_return",
        "context_bars": 256,
        "min_context_bars": 96,
        "horizon_bars": 4,
        "recalc_on_closed_bar_only": True,
        "stale_after_minutes": 20,
        "use_for_decision": False,
        "display_in_gui": True,
        "persist_results": True,
        "providers": {
            "chronos": {
                "runtime_mode": "in_process",
                "use_for_scoring_only": True,
                "model_repo": "amazon/chronos-2",
                "torch_dtype": "bfloat16",
                "hf_token_env": "HF_TOKEN",
                "local_files_only": True,
                "worker_timeout_seconds": 300,
            },
            "darts": {
                "runtime_mode": "in_process",
                "use_for_scoring_only": True,
            },
            "naive_baseline": {
                "runtime_mode": "in_process",
                "use_for_scoring_only": True,
            },
            "atr_baseline": {
                "runtime_mode": "in_process",
                "use_for_scoring_only": True,
            },
            "lag_llama": {
                "runtime_mode": "external_worker",
                "use_for_scoring_only": True,
                "callable": "app.forecasting.provider_bridges:lag_llama_forecast",
                "checkpoint_repo": "time-series-foundation-models/Lag-Llama",
                "checkpoint_file": "lag-llama.ckpt",
                "num_samples": 100,
            },
            "moirai": {
                "runtime_mode": "external_worker",
                "use_for_scoring_only": True,
                "callable": "app.forecasting.provider_bridges:moirai_uni2ts_forecast",
                "model_repo": "Salesforce/moirai-1.1-R-small",
                "num_samples": 100,
            },
            "moirai_uni2ts": {
                "runtime_mode": "external_worker",
                "use_for_scoring_only": True,
                "callable": "app.forecasting.provider_bridges:moirai_uni2ts_forecast",
                "model_repo": "Salesforce/moirai-1.1-R-small",
                "num_samples": 100,
            },
            "uni2ts": {
                "runtime_mode": "external_worker",
                "use_for_scoring_only": True,
                "callable": "app.forecasting.provider_bridges:moirai_uni2ts_forecast",
                "model_repo": "Salesforce/moirai-1.1-R-small",
                "num_samples": 100,
            },
            "neuralforecast": {
                "runtime_mode": "external_worker",
                "use_for_scoring_only": True,
                "model_path": "",
                "models": ["NHITS", "NBEATS", "PatchTST", "iTransformer"],
                "max_steps": 25,
            },
            "autogluon": {
                "runtime_mode": "external_worker",
                "use_for_scoring_only": True,
                "model_path": "",
                "presets": "fast_training",
                "time_limit_seconds": 60,
            },
        },
        "score_thresholds": {
            "bullish": 75,
            "neutral_bullish": 60,
            "neutral": 40,
        },
    },
    "forecast_accuracy": {
        "auto_evaluate_interval_seconds": 900,
        "min_required_samples": 30,
        "grades": {
            "A": {"min_direction_accuracy": 0.62, "max_mape": 0.04},
            "B": {"min_direction_accuracy": 0.57, "max_mape": 0.06},
            "C": {"min_direction_accuracy": 0.52, "max_mape": 0.08},
            "D": {"min_direction_accuracy": 0.48, "max_mape": 0.12},
        },
    },
    "forecast_stack": {
        "enabled": True,
        "execution_mode": "scoring_only",
        "primary_model": "timesfm",
        "active_models": [
            "timesfm",
            "chronos",
            "lag_llama",
            "moirai",
            "moirai_uni2ts",
            "uni2ts",
            "neuralforecast",
            "autogluon",
            "naive_baseline",
            "atr_baseline",
            "darts",
        ],
        "comparison_models": ["chronos", "lag_llama", "darts"],
        "advanced_models": ["neuralforecast", "autogluon"],
        "experimental_models": ["moirai", "moirai_uni2ts", "uni2ts"],
        "horizons": {"15m": [4, 8, 16], "1h": [4, 8, 24], "1d": [3, 5, 10]},
        "providers": {
            "timesfm": {
                "enabled": True,
                "priority": 0,
                "role": "primary",
                "use_for_setup_score": True,
            },
            "naive_baseline": {
                "enabled": True,
                "priority": 0,
                "role": "baseline",
                "use_for_model_lab": True,
            },
            "atr_baseline": {
                "enabled": True,
                "priority": 0,
                "role": "baseline",
                "use_for_model_lab": True,
            },
            "chronos": {
                "enabled": True,
                "auto_enable_when_ready": True,
                "priority": 1,
                "role": "direct_competitor",
                "use_for_setup_score": True,
                "use_for_execution": False,
                "use_for_model_lab": True,
            },
            "lag_llama": {
                "enabled": True,
                "auto_enable_when_ready": True,
                "priority": 1,
                "role": "probabilistic",
                "use_for_setup_score": True,
                "use_for_execution": False,
                "use_for_model_lab": True,
            },
            "darts": {
                "enabled": True,
                "auto_enable_when_ready": True,
                "priority": 1,
                "role": "benchmark_framework",
                "use_for_runtime_forecast": False,
                "use_for_model_lab": True,
                "runtime_mode": "in_process",
                "worker_timeout_seconds": 180,
            },
            "neuralforecast": {
                "enabled": True,
                "auto_enable_when_ready": True,
                "priority": 2,
                "role": "deep_learning_models",
                "use_for_setup_score": True,
                "use_for_execution": False,
                "use_for_model_lab": True,
            },
            "autogluon": {
                "enabled": True,
                "auto_enable_when_ready": True,
                "priority": 2,
                "role": "automl_baseline",
                "use_for_setup_score": True,
                "use_for_execution": False,
                "use_for_model_lab": True,
            },
            "moirai": {
                "enabled": True,
                "auto_enable_when_ready": True,
                "priority": 3,
                "role": "experimental_foundation_benchmark",
                "use_for_setup_score": True,
                "use_for_execution": False,
                "use_for_model_lab": True,
            },
            "moirai_uni2ts": {
                "enabled": True,
                "auto_enable_when_ready": True,
                "priority": 3,
                "role": "experimental_foundation_benchmark",
                "use_for_setup_score": True,
                "use_for_execution": False,
                "use_for_model_lab": True,
            },
            "uni2ts": {
                "enabled": True,
                "auto_enable_when_ready": True,
                "priority": 3,
                "role": "experimental_foundation_benchmark",
                "use_for_setup_score": True,
                "use_for_execution": False,
                "use_for_model_lab": True,
            },
        },
        "safety": {
            "block_order_from_forecast": True,
            "use_forecast_for_scoring_only": True,
            "require_accuracy_history_before_score_boost": True,
            "min_accuracy_samples_for_boost": 30,
        },
    },
    "opportunity_scanner": {
        "enabled": True,
        "default_timeframes": ["15m", "1h", "1d"],
        "max_candidates_per_scan": 100,
        "max_shortlisted": 20,
        "scan_interval_seconds": 30,
        "universe": {
            "source": "watchlist",
            "max_symbols": 200,
            "default_watchlist_file": "data/watchlists/default.yaml",
            "include_active_setups": True,
            "include_open_positions": True,
            "include_recent_quotes": True,
        },
        "filters": {
            "min_price": 1.0,
            "max_price": 1000.0,
            "min_volume": 100000,
            "min_volume_ratio": 0.8,
            "max_spread_pct": 0.35,
            "allow_missing_quote_for_setup": True,
        },
        "context_thresholds": {
            "detected": 70,
            "watchlist": 40,
            "weak": 20,
            "strong_perf": 5,
            "very_strong_perf": 10,
            "rs_spy": 3,
            "rs_sector": 2,
            "volume_ratio": 1.5,
            "max_spread_pct": 0.35,
        },
        "scanners": {
            "momentum_breakout": {"enabled": True, "min_volume_ratio": 1.5},
            "breakout_retest": {"enabled": True, "retest_max_distance_atr": 1.0},
            "reclaim": {"enabled": True, "min_volume_ratio": 1.0},
            "pullback_continuation": {"enabled": True},
        },
    },
    "event_risk": {
        "earnings": {
            "enabled": True,
            "warn_before_days": 10,
            "block_before_days": 2,
            "block_on_earnings_day": True,
            "require_manual_review_if_unknown_time": True,
        },
        "dividends": {
            "enabled": True,
            "warn_before_days": 5,
            "block_before_days": 1,
            "block_on_ex_dividend_day": True,
        },
        "economic": {
            "enabled": True,
            "block_high_impact_us_events": True,
            "block_minutes_before": 30,
            "block_minutes_after": 30,
            "warn_minutes_before": 120,
            "affected_symbols": ["ALL_US_STOCKS"],
        },
    },
    "orders": {
        "default_entry_order_type": "STP_LMT",
        "default_stop_order_type": "STP",
        "cancel_unfilled_entry_after_minutes": 30,
    },
    "lifecycle": {
        "revalidate_interval_seconds": 30,
        "max_age_days": 30,
        "price_too_far_percent": 1.5,
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
    "entry_decision": {
        "use_final_engine_decision_for_gui": True,
        "gui_must_not_rebuild_decision_from_partial_checks": True,
        "anti_chase": {
            "enabled": True,
            "long_rule": "ASK_ABOVE_MAXIMUM_LIMIT_PLUS_STALE_BUFFER",
            "short_rule": "BID_BELOW_MINIMUM_LIMIT_MINUS_STALE_BUFFER",
            "action_if_triggered": "MISSED_BREAKOUT",
            "next_action": "WAITING_RETEST",
        },
        "risk": {
            "recalculate_current_executable_risk": True,
            "block_if_current_risk_exceeds_max_risk": True,
            "show_planned_vs_current_risk": True,
        },
        "session_consistency": {
            "enabled": True,
            "warn_if_signal_bar_from_previous_rth": True,
            "block_entry_if_pre_market_gap_invalidates_setup": True,
            "require_current_session_confirmation": True,
        },
    },
    "session_policy": {
        "enabled": True,
        "require_regular_trading_hours_for_entry": True,
        "allow_premarket_entry": False,
        "allow_after_hours_entry": False,
        "wait_after_open_minutes": 30,
        "wait_closed_bars_after_open": 2,
        "wait_bars_timeframe": "15m",
        "require_rth_volume_confirmation": True,
        "require_rth_spread_check": True,
        "require_rth_risk_recalculation": True,
        "extended_hours": {
            "allow_detection": True,
            "allow_auto_execution": False,
            "allow_manual_review": True,
        },
    },
    "volume_confirmation_policy_by_setup_type": {
        "momentum_breakout": {
            "required_for_entry": True,
            "weak_volume_action": "WAITING_VOLUME_CONFIRMATION",
        },
        "breakout_retest": {
            "required_for_entry": False,
            "weak_volume_action": "WARNING_ONLY",
        },
        "pullback_continuation": {
            "required_for_entry": False,
            "weak_volume_action": "WARNING_ONLY",
        },
        "position_management": {
            "required_for_entry": False,
            "weak_volume_action": "IGNORE_FOR_MANAGEMENT",
        },
        "runner": {
            "required_for_entry": False,
            "weak_volume_action": "IGNORE_FOR_MANAGEMENT",
        },
        "trailing_runner": {
            "required_for_entry": False,
            "weak_volume_action": "IGNORE_FOR_MANAGEMENT",
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
    def from_dict(cls, config: dict[str, Any]) -> Settings:
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
            broker_connector=str(broker.get("connector", "paper")),
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
    load_local_env(config_path.parent / ".env")
    overrides = load_yaml_file(config_path)
    merged = deep_merge(DEFAULT_CONFIG, overrides)
    settings = Settings.from_dict(merged)
    settings.database_file.parent.mkdir(parents=True, exist_ok=True)
    settings.setups_folder.mkdir(parents=True, exist_ok=True)
    settings.logs_folder.mkdir(parents=True, exist_ok=True)
    return settings


def load_local_env(path: str | Path = ".env") -> None:
    """Load local secrets without overriding explicitly configured environment values."""
    env_path = Path(path)
    if not env_path.is_file():
        return
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].lstrip()
        key, separator, value = line.partition("=")
        key = key.strip()
        if not separator or not key or any(character.isspace() for character in key):
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        os.environ.setdefault(key, value)
