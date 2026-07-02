from __future__ import annotations

from typing import Any

from app.models import SetupRole
from app.settings import Settings
from app.setups.setup_factory import SetupFactory
from app.setups.setup_type_registry import (
    required_by_setup_type,
    setup_specific_options,
    setup_type_selection_guide,
    validation_rules,
    volume_confirmation_policy_by_setup_type,
)


class SetupTemplateService:
    """Builds user-facing setup configuration templates."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def _setup_specific_options_with_defaults(self) -> dict[str, dict[str, Any]]:
        defaults = self.settings.raw.get("setup_defaults", {})
        entry_defaults = defaults.get("entry", {})
        order_defaults = self.settings.raw.get("orders", {})
        options = setup_specific_options()
        momentum = options.get("momentum_breakout", {})
        if isinstance(momentum.get("entry"), dict):
            momentum["entry"]["order_type"] = order_defaults.get(
                "default_entry_order_type",
                "STP_LMT",
            )
            momentum["entry"]["trigger_offset"] = entry_defaults.get("trigger_offset", 0.02)
            momentum["entry"]["limit_offset"] = entry_defaults.get("limit_offset", 0.05)
        if isinstance(momentum.get("breakout"), dict):
            momentum["breakout"]["fast_breakout_volume_ratio_min"] = defaults.get(
                "momentum",
                {},
            ).get("volume_above_average", 1.5)
            momentum["breakout"]["confirmed_breakout_volume_ratio_min"] = defaults.get(
                "confirmation",
                {},
            ).get("min_volume_ratio", 0.8)
        if isinstance(momentum.get("volume_confirmation"), dict):
            momentum["volume_confirmation"]["signal_timeframe"] = defaults.get(
                "timeframes",
                {},
            ).get("signal", "15m")
            momentum["volume_confirmation"]["fast_volume_ratio_min"] = defaults.get(
                "momentum",
                {},
            ).get("volume_above_average", 1.5)
            momentum["volume_confirmation"]["confirmed_volume_ratio_min"] = defaults.get(
                "confirmation",
                {},
            ).get("min_volume_ratio", 0.8)
        return options

    def _deep_merge(self, base: dict[str, Any], extra: dict[str, Any]) -> dict[str, Any]:
        merged = dict(base)
        for key, value in extra.items():
            existing = merged.get(key)
            if isinstance(existing, dict) and isinstance(value, dict):
                merged[key] = self._deep_merge(existing, value)
            else:
                merged[key] = value
        return merged

    def _build_universal_skeleton(self) -> dict[str, Any]:
        defaults = self.settings.raw.get("setup_defaults", {})
        timeframe_defaults = defaults.get("timeframes", {})
        risk_defaults = self.settings.raw.get("risk", {})
        order_defaults = self.settings.raw.get("orders", {})
        app_mode = str(self.settings.raw.get("app", {}).get("mode", "paper"))
        options = self._setup_specific_options_with_defaults()

        entry = {
            "enabled": True,
            "order_type": order_defaults.get("default_entry_order_type", "STP_LMT"),
            "allowed_order_types": [
                "STP_LMT",
                "LMT",
                "MKT_PROTECTED_BY_TRAILING_STOP",
            ],
            "trigger_offset": defaults.get("entry", {}).get("trigger_offset", 0.02),
            "limit_offset": defaults.get("entry", {}).get("limit_offset", 0.05),
            "trigger_price": None,
            "entry_price": None,
            "limit_price": None,
            "maximum_limit_price": None,
            "cancel_if_not_filled_after_minutes": order_defaults.get(
                "cancel_unfilled_entry_after_minutes",
                30,
            ),
            "requires_trailing_stop_before_transmission": True,
        }
        breakout = {
            "resistance": None,
            "broken_resistance": None,
            "daily_close_above": None,
            "volume_rule_mode": "FLEXIBLE_CONFIRMATION",
            "fast_breakout_volume_ratio_min": defaults.get("momentum", {}).get(
                "volume_above_average",
                1.5,
            ),
            "confirmed_breakout_volume_ratio_min": defaults.get("confirmation", {}).get(
                "min_volume_ratio",
                0.8,
            ),
            "confirmed_breakout_hold_bars": 2,
            "confirmed_breakout_timeframe": timeframe_defaults.get("signal", "15m"),
            "close_above_resistance_required": True,
        }
        management = {
            "take_profit_mode": "none",
            "never_lower_stop": True,
            "position_management_mode": "TRAILING_STOP_ONLY",
            "stop_management": {
                "mode": "TRAILING_STOP_LOSS",
                "source": "trailing_stop_loss",
                "never_lower_stop": True,
            },
        }
        risk = {
            "max_position_amount_usd": risk_defaults.get("max_position_amount_usd", 250),
            "max_risk_usd": risk_defaults.get("max_risk_per_trade_usd", 15),
            "risk_model": "TRAILING_STOP_INITIAL_RISK",
            "emergency_exit_if_stop_fails": True,
            "block_entry_if_risk_unknown": True,
            "block_entry_if_trailing_stop_missing": True,
        }
        trailing_stop_loss = self._trailing_stop_loss_template()
        volume_policy = volume_confirmation_policy_by_setup_type()

        for setup_options in options.values():
            if isinstance(setup_options.get("entry"), dict):
                entry = self._deep_merge(entry, setup_options["entry"])
            if isinstance(setup_options.get("breakout"), dict):
                breakout = self._deep_merge(breakout, setup_options["breakout"])
            if isinstance(setup_options.get("management"), dict):
                management = self._deep_merge(management, setup_options["management"])
            if isinstance(setup_options.get("risk"), dict):
                risk = self._deep_merge(risk, setup_options["risk"])
        risk.pop("initial_stop_loss", None)
        risk.pop("protective_stop", None)

        entry["enabled"] = "AUTO_SELECT"
        entry["order_type"] = "AUTO_SELECT"
        volume_confirmation = options.get("momentum_breakout", {}).get(
            "volume_confirmation",
            {},
        )
        if isinstance(volume_confirmation, dict):
            volume_confirmation = dict(volume_confirmation)
            volume_confirmation["enabled"] = "AUTO_SELECT"

        return {
            "schema_version": "2.0.0",
            "setup_id": "TICKER_YYYYMMDD_001",
            "symbol": "TICKER",
            "enabled": True,
            "mode": app_mode,
            "setup_type": "CHOOSE_ONE_SETUP_TYPE",
            "setup_type_options": SetupFactory.supported_types(),
            "setup_type_selection_guide": setup_type_selection_guide(),
            "setup_role": "AUTO_SELECT",
            "direction": "long",
            "timeframes": {
                "signal": timeframe_defaults.get("signal", "15m"),
                "confirmation": timeframe_defaults.get("confirmation", "1d"),
                "trailing_management": timeframe_defaults.get("signal", "15m"),
                "atr_reference": "1h",
            },
            "entry": entry,
            "risk": risk,
            "trailing_stop_loss": trailing_stop_loss,
            "management": management,
            "breakout": breakout,
            "volume_confirmation": volume_confirmation,
            "volume_confirmation_policy_by_setup_type": volume_policy,
            "missed_breakout": options.get("momentum_breakout", {}).get(
                "missed_breakout",
                {},
            ),
            "rearm": options.get("momentum_breakout", {}).get("rearm", {}),
            "retest": options.get("breakout_retest", {}).get("retest", {}),
            "range": options.get("range_breakout", {}).get("range", {}),
            "trend_filter": options.get("pullback_continuation", {}).get(
                "trend_filter",
                {},
            ),
            "pullback": options.get("pullback_continuation", {}).get("pullback", {}),
            "support_zone": options.get("aggressive_rebound", {}).get("support_zone", {}),
            "rebound_confirmation": options.get("aggressive_rebound", {}).get(
                "rebound_confirmation",
                {},
            ),
            "position_source": {
                "mode": "AUTO_SELECT",
                "require_existing_position": "AUTO_SELECT",
                "account_position_must_match_direction": True,
            },
            "anti_chase": {
                "enabled": True,
                "max_price_above_entry_percent": 1.5,
                "action_if_too_far": "MISSED_BREAKOUT_WAIT_RETEST",
                "block_entry_if_price_above_maximum_limit": True,
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
            "broker_safety": {
                "require_broker_tracker": True,
                "block_if_broker_tracker_stale": True,
                "block_if_tws_disconnected": True,
                "block_if_position_without_stop": True,
                "block_if_entry_order_without_trailing_stop": True,
                "block_if_reconciliation_mismatch": True,
            },
            "entry_decision": {
                "status": "NOT_EVALUATED",
                "decision": "NO_ENTRY",
                "can_send_order": False,
                "display_title": "",
                "display_message": "",
                "next_action": "",
                "blocking_reasons": [],
                "warnings": [],
                "planned_vs_current_risk": None,
            },
            "targets": [],
            "notes": "",
            "expected_output": {
                "format": "FINAL_CANONICAL_SETUP_ONLY",
                "rules": [
                    "return only one final setup JSON",
                    "choose exactly one setup_type from setup_type_options",
                    "replace CHOOSE_ONE_SETUP_TYPE with the selected setup_type",
                    "replace AUTO_SELECT values with final concrete values",
                    "remove all irrelevant sections",
                    "remove _template before saving",
                    "do not return setup_type_options in the final setup",
                    "do not return setup_type_selection_guide in the final setup",
                    "do not return volume_confirmation_policy_by_setup_type in the final setup",
                    "do not return expected_output in the final setup",
                    "all final setups must include trailing_stop_loss.enabled=true",
                    "do not use fixed stop-loss as the main protection model",
                    "use trailing_stop_loss.initial_stop for initial risk calculation",
                    "entry orders cannot be transmitted unless trailing_stop_loss.initial_stop is calculated",
                    "entry orders cannot be transmitted unless trailing stop broker order is ready",
                    "remove legacy risk.initial_stop_loss and risk.protective_stop from final setups",
                    "for MANAGEMENT_ONLY setups, entry.enabled must be false",
                    "for MANAGEMENT_ONLY setups, never generate an initial BUY order",
                ],
            },
            "_template": {
                "template_kind": "UNIVERSAL_SETUP_REQUEST",
                "can_be_saved_as_setup": False,
                "can_be_armed": False,
                "instruction": (
                    "Choisis le setup_type le plus adapte, puis retourne uniquement le "
                    "setup final canonique. Tous les nouveaux setups doivent utiliser "
                    "trailing_stop_loss comme modele principal de protection."
                ),
                "selection_rules": [
                    "choose exactly one setup_type",
                    "do not default to momentum_breakout",
                    "if the setup is only for managing an existing position, use position_management, runner, or trailing_runner",
                    "if setup_role is MANAGEMENT_ONLY, entry.enabled must be false",
                    "never generate an initial BUY order for position_management, runner, or trailing_runner",
                    "all entry setups require trailing_stop_loss.enabled=true",
                    "all management setups require trailing_stop_loss.enabled=true",
                ],
                "required_by_setup_type": required_by_setup_type(),
                "validation_rules": validation_rules(),
            },
        }

    def setup_config_template(self) -> dict[str, Any]:
        options = self._setup_specific_options_with_defaults()
        skeleton = self._build_universal_skeleton()
        volume_policy = volume_confirmation_policy_by_setup_type()
        return {
            "template_type": "universal",
            "skeleton": skeleton,
            "supported_setup_types": SetupFactory.supported_types(),
            "required_fields": [
                "setup_id",
                "symbol",
                "enabled",
                "mode",
                "setup_type",
                "setup_role",
                "direction",
                "entry.enabled",
                "risk.max_position_amount_usd",
                "risk.max_risk_usd",
                "trailing_stop_loss.enabled",
                "trailing_stop_loss.initial_stop",
                "trailing_stop_loss.broker_order.required_before_entry_transmission",
            ],
            "required_by_setup_type": required_by_setup_type(),
            "setup_type_selection_guide": setup_type_selection_guide(),
            "setup_specific_options": options,
            "volume_confirmation_policy_by_setup_type": volume_policy,
            "validation_rules": validation_rules(),
            "expected_output": skeleton["expected_output"],
            "optional_sections": {
                "timeframes": "Signal and confirmation timeframes.",
                "missed_breakout": "Retest zone used when a momentum breakout is missed.",
                "rearm": "Replacement local resistance and prices after a new base.",
                "management": "Take profit and stop management rules after entry.",
                "notes": "Free-form operator notes.",
            },
        }

    def momentum_breakout_template(self) -> dict[str, Any]:
        defaults = self.settings.raw.get("setup_defaults", {})
        timeframe_defaults = defaults.get("timeframes", {})
        risk_defaults = self.settings.raw.get("risk", {})
        order_defaults = self.settings.raw.get("orders", {})
        app_mode = str(self.settings.raw.get("app", {}).get("mode", "paper"))
        options = self._setup_specific_options_with_defaults()
        momentum = options.get("momentum_breakout", {})
        entry = dict(momentum.get("entry", {}))
        entry["cancel_if_not_filled_after_minutes"] = order_defaults.get(
            "cancel_unfilled_entry_after_minutes",
            30,
        )
        template = {
            "setup_id": "TICKER_YYYYMMDD_001",
            "symbol": "TICKER",
            "enabled": True,
            "mode": app_mode,
            "setup_type": "momentum_breakout",
            "setup_role": SetupRole.ENTRY_AND_MANAGEMENT.value,
            "direction": "long",
            "timeframes": {
                "signal": timeframe_defaults.get("signal", "15m"),
                "confirmation": timeframe_defaults.get("confirmation", "1d"),
            },
            "breakout": momentum.get("breakout", {}),
            "volume_confirmation": momentum.get("volume_confirmation", {}),
            "missed_breakout": momentum.get("missed_breakout", {}),
            "rearm": momentum.get("rearm", {}),
            "entry": entry,
            "risk": {
                "max_position_amount_usd": risk_defaults.get(
                    "max_position_amount_usd",
                    250,
                ),
                "max_risk_usd": risk_defaults.get("max_risk_per_trade_usd", 15),
                "risk_model": "TRAILING_STOP_INITIAL_RISK",
                "emergency_exit_if_stop_fails": True,
                "block_entry_if_risk_unknown": True,
                "block_entry_if_trailing_stop_missing": True,
            },
            "trailing_stop_loss": self._trailing_stop_loss_template(),
            "management": {
                "take_profit_mode": "none",
                "never_lower_stop": True,
                "stop_management": {
                    "mode": "TRAILING_STOP_LOSS",
                    "source": "trailing_stop_loss",
                    "never_lower_stop": True,
                    "steps": [],
                },
            },
            "targets": [],
            "notes": "",
        }
        return {
            "template_type": "momentum_breakout",
            "template": template,
            "usage": (
                "Template specifique momentum_breakout. Ne pas utiliser comme "
                "squelette universel AUTO_SELECT."
            ),
            "required_fields_before_arm": [
                "breakout.resistance",
                "entry.maximum_limit_price",
                "trailing_stop_loss.initial_stop",
                "trailing_stop_loss.broker_order.required_before_entry_transmission",
            ],
            "validation_rules": validation_rules().get("momentum_breakout", []),
        }

    @staticmethod
    def _trailing_stop_loss_template() -> dict[str, Any]:
        return {
            "enabled": True,
            "mode": "AUTO_INTELLIGENT",
            "never_lower_stop": True,
            "initial_stop": None,
            "current_stop": None,
            "stop_source": "AUTO_CALCULATED",
            "applies_to": "ENTRY_AND_POSITION_MANAGEMENT",
            "activation": {
                "mode": "ON_ENTRY_FILL",
                "activate_before_entry_transmission": True,
                "entry_order_requires_attached_trailing_stop": True,
            },
            "calculation": {
                "method": "HYBRID_ATR_STRUCTURE",
                "allowed_methods": [
                    "ATR_BASED",
                    "STRUCTURE_BASED",
                    "HYBRID_ATR_STRUCTURE",
                    "PERCENT_BASED_FALLBACK",
                ],
                "default_method": "HYBRID_ATR_STRUCTURE",
                "atr": {
                    "timeframe": "1h",
                    "period": 14,
                    "multiplier_initial": "AUTO",
                    "multiplier_trailing": "AUTO",
                    "min_multiplier": 1.0,
                    "max_multiplier": 3.5,
                },
                "structure": {
                    "reference": "HIGHER_LOW_OR_SUPPORT",
                    "allowed_references": [
                        "HIGHER_LOW",
                        "INTRADAY_SUPPORT",
                        "RANGE_LOW",
                        "BROKEN_RESISTANCE_AS_SUPPORT",
                        "VWAP_PULLBACK",
                        "PREVIOUS_DAY_LOW",
                        "AUTO_SELECT",
                    ],
                    "buffer_policy": "MAX_OF_TICK_SPREAD_ATR_FRACTION",
                    "min_tick_buffer": 2,
                    "spread_buffer_multiplier": 2,
                    "atr_fraction_buffer": 0.1,
                },
                "stock_specific_adjustment": {
                    "enabled": True,
                    "inputs": [
                        "price",
                        "atr_15m",
                        "atr_1h",
                        "average_true_range_percent",
                        "spread_bps",
                        "relative_volume",
                        "liquidity",
                        "gap_percent",
                        "intraday_range_percent",
                        "sector_volatility",
                        "distance_to_support",
                        "distance_to_breakout_level",
                    ],
                    "volatility_regime": "AUTO",
                    "liquidity_regime": "AUTO",
                    "spread_regime": "AUTO",
                },
                "risk_constraints": {
                    "respect_max_risk_usd": True,
                    "respect_max_position_amount_usd": True,
                    "minimum_quantity": 1,
                    "block_if_quantity_zero": True,
                    "block_if_initial_stop_above_entry_for_long": True,
                    "block_if_initial_stop_below_entry_for_short": True,
                },
            },
            "ratchet_rules": {
                "enabled": True,
                "move_only_up_for_long": True,
                "move_only_down_for_short": True,
                "update_on_closed_bar_only": True,
                "timeframe": "15m",
                "min_improvement_required": "AUTO",
                "min_improvement_atr_fraction": 0.15,
                "do_not_update_if_spread_wide": True,
                "do_not_update_outside_rth": True,
                "do_not_update_on_unconfirmed_intrabar_move": True,
                "do_not_lower_stop": True,
                "allow_break_even_move": True,
                "break_even_policy": {
                    "enabled": True,
                    "trigger_after_profit_r_multiple": 1.0,
                    "new_stop": "ENTRY_PRICE_PLUS_FEES_BUFFER",
                },
            },
            "broker_order": {
                "order_type": "TRAIL_OR_MANAGED_STOP",
                "attach_to_entry_order": True,
                "required_before_entry_transmission": True,
                "use_native_ibkr_trailing_order_if_available": True,
                "fallback_to_managed_stop_updates": True,
                "parent_child_bracket_required": True,
                "entry_parent_transmit": False,
                "trailing_stop_child_transmit": True,
                "block_if_broker_stop_not_confirmed": True,
                "trailing_stop_order_ready": False,
            },
            "audit": {
                "record_initial_stop_calculation": True,
                "record_each_stop_update": True,
                "record_reason": True,
                "record_old_stop": True,
                "record_new_stop": True,
                "record_broker_order_id": True,
            },
        }
