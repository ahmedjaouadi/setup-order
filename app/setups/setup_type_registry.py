from __future__ import annotations

from copy import deepcopy
from typing import Any

SUPPORTED_SETUP_TYPES: tuple[str, ...] = (
    "aggressive_rebound",
    "breakout_retest",
    "momentum_breakout",
    "position_management",
    "pullback_continuation",
    "range_breakout",
    "runner",
    "trailing_runner",
)

TRAILING_STOP_TEMPLATE_RULES: list[str] = [
    "trailing_stop_loss.enabled=true",
    "trailing_stop_loss.initial_stop before arming",
    "trailing_stop_loss.broker_order.required_before_entry_transmission=true",
]

# MANAGEMENT_ONLY setups adopt an existing position, so there is no initial entry
# order to transmit. The relevant guarantees are about the already-open position's
# protective stop, never about entry transmission.
TRAILING_STOP_MANAGEMENT_RULES: list[str] = [
    "trailing_stop_loss.enabled=true",
    "trailing_stop_loss.current_stop before arming",
    "broker_safety.block_if_position_without_stop=true",
    "never generate an initial BUY order",
]


SETUP_TYPE_SELECTION_GUIDE: dict[str, str] = {
    "momentum_breakout": (
        "Utiliser seulement si le plan cherche une entree sur cassure directe "
        "d'une resistance avec trigger et limite d'entree."
    ),
    "breakout_retest": (
        "Utiliser si la cassure a deja eu lieu et que le plan attend un retest "
        "sur l'ancienne resistance."
    ),
    "aggressive_rebound": (
        "Utiliser si le plan cherche un rebond agressif depuis une zone de support."
    ),
    "range_breakout": (
        "Utiliser si le titre evolue dans un range clair et qu'on attend une " "cassure du range."
    ),
    "pullback_continuation": (
        "Utiliser si le titre est deja en tendance et qu'on cherche une entree " "sur repli."
    ),
    "runner": ("Utiliser si une position existe deja et que le but est de laisser courir le gain."),
    "trailing_runner": (
        "Utiliser si une position existe deja et que le but principal est de "
        "gerer un trailing stop intelligent."
    ),
    "position_management": (
        "Utiliser si le but est uniquement de gerer une position existante, "
        "sans nouvelle entree."
    ),
}


SETUP_SPECIFIC_OPTIONS: dict[str, dict[str, Any]] = {
    "momentum_breakout": {
        "breakout": {
            "resistance": None,
            "volume_rule_mode": "FLEXIBLE_CONFIRMATION",
            "fast_breakout_volume_ratio_min": 1.5,
            "confirmed_breakout_volume_ratio_min": 0.8,
            "confirmed_breakout_hold_bars": 2,
            "confirmed_breakout_timeframe": "15m",
            "close_above_resistance_required": True,
        },
        "volume_confirmation": {
            "enabled": True,
            "signal_timeframe": "15m",
            "comparison_mode": "SAME_TIME_OF_DAY",
            "average_sample_days": 20,
            "fast_volume_ratio_min": 1.5,
            "normal_volume_ratio_min": 1.0,
            "confirmed_volume_ratio_min": 0.8,
            "confirmed_hold_bars": 2,
            "close_above_level_required": True,
            "reject_detection_enabled": True,
            "max_upper_wick_ratio": 0.5,
        },
        "entry": {
            "enabled": True,
            "order_type": "STP_LMT",
            "trigger_offset": 0.02,
            "limit_offset": 0.05,
            "maximum_limit_price": None,
        },
        "missed_breakout": {
            "retest_zone_min": None,
            "retest_zone_max": None,
        },
        "rearm": {
            "new_local_resistance": None,
            "new_trigger": None,
            "new_limit": None,
        },
    },
    "position_management": {
        "setup_role": "MANAGEMENT_ONLY",
        "entry": {"enabled": False},
        "position_source": {
            "mode": "adopt_existing_ibkr_position",
            "require_existing_position": True,
            "account_position_must_match_direction": True,
        },
        "management": {
            "take_profit_mode": "none",
            "stop_management": {
                "mode": "TRAILING_STOP_LOSS",
                "source": "trailing_stop_loss",
                "never_lower_stop": True,
                "steps": [],
            },
        },
    },
    "breakout_retest": {
        "breakout": {
            "broken_resistance": None,
            "daily_close_above": None,
        },
        "retest": {
            "zone_min": None,
            "zone_max": None,
            "confirmation_required": True,
            "confirmation_timeframe": "15m",
        },
        "entry": {
            "enabled": True,
            "order_type": "LMT",
            "limit_price": None,
            "maximum_limit_price": None,
        },
    },
    "range_breakout": {
        "range": {
            "high": None,
            "low": None,
            "breakout_side": "up",
            "require_close_outside_range": True,
        },
    },
    "pullback_continuation": {
        "trend_filter": {
            "enabled": True,
            "required_trend": "uptrend",
        },
        "pullback": {
            "entry_reference": None,
            "zone_min": None,
            "zone_max": None,
            "confirmation_required": True,
        },
    },
    "aggressive_rebound": {
        "support_zone": {
            "min": None,
            "max": None,
            "invalidation_below": None,
        },
        "rebound_confirmation": {
            "require_bullish_candle": True,
            "require_volume_confirmation": True,
            "confirmation_timeframe": "15m",
        },
    },
    "runner": {
        "setup_role": "MANAGEMENT_ONLY",
        "entry": {"enabled": False},
        "position_source": {
            "mode": "adopt_existing_ibkr_position",
            "require_existing_position": True,
        },
        "management": {
            "take_profit_mode": "none",
            "stop_management": {
                "mode": "TRAILING_STOP_LOSS",
                "source": "trailing_stop_loss",
                "never_lower_stop": True,
                "steps": [],
            },
        },
    },
    "trailing_runner": {
        "setup_role": "MANAGEMENT_ONLY",
        "entry": {"enabled": False},
        "position_source": {
            "mode": "adopt_existing_ibkr_position",
            "require_existing_position": True,
        },
        "management": {
            "take_profit_mode": "none",
            "stop_management": {
                "mode": "TRAILING_STOP_LOSS",
                "source": "trailing_stop_loss",
                "never_lower_stop": True,
                "trail_type": "ATR_OR_STRUCTURE",
                "atr_timeframe": "1h",
                "atr_multiplier": 1.5,
                "steps": [],
            },
        },
    },
}


VALIDATION_RULES: dict[str, list[str]] = {
    "global": [
        "Choisir un seul setup_type final.",
        "Ne pas utiliser momentum_breakout par defaut.",
        "Utiliser uniquement les sections pertinentes pour le setup choisi.",
        "Supprimer ou ignorer les sections non pertinentes.",
        "trailing_stop_loss.enabled doit toujours etre true.",
        "trailing_stop_loss.initial_stop est obligatoire avant arming pour tout setup avec entree.",
        "Le risque initial doit etre calcule avec trailing_stop_loss.initial_stop.",
        "Ne jamais utiliser risk.initial_stop_loss comme modele principal.",
        "Ne jamais utiliser risk.protective_stop comme modele principal.",
        "Ne jamais transmettre un ordre si trailing_stop_loss.initial_stop est null.",
        "Ne jamais transmettre un ordre si le trailing stop broker n'est pas pret.",
        "Ne jamais envoyer d'ordre si setup_role = MANAGEMENT_ONLY.",
        "Ne jamais baisser un stop existant.",
    ],
    "entry_setups": [
        "entry.enabled peut etre true.",
        "trailing_stop_loss.initial_stop doit etre inferieur au prix d'entree pour un long.",
        "trailing_stop_loss.initial_stop doit etre superieur au prix d'entree pour un short.",
        "maximum_quantity doit etre calculee selon max_risk_usd et max_position_amount_usd.",
        "can_send_order=true uniquement si trailing_stop_order_ready=true.",
    ],
    "management_only_setups": [
        "entry.enabled doit etre false.",
        "position_source.require_existing_position doit etre true.",
        "Le moteur doit verifier l'existence de la position IBKR.",
        "Le moteur ne doit jamais creer d'ordre BUY initial.",
        "Le moteur doit gerer uniquement le trailing stop de la position existante.",
    ],
    "position_management": [
        "setup_role doit etre MANAGEMENT_ONLY.",
        "entry.enabled doit etre false.",
        "position_source.require_existing_position doit etre true.",
        "Le moteur ne doit jamais creer d'ordre BUY initial.",
        "Le moteur doit verifier l'existence de la position IBKR.",
        "trailing_stop_loss.initial_stop doit etre defini avant armement.",
    ],
    "runner": [
        "entry.enabled doit etre false par defaut.",
        "position existante requise.",
        "management.stop_management.never_lower_stop doit etre true.",
        "trailing_stop_loss.initial_stop doit etre defini avant armement.",
    ],
    "trailing_runner": [
        "entry.enabled doit etre false par defaut.",
        "position existante requise.",
        "trailing_stop_loss.enabled doit etre true.",
        "trailing_stop_loss.mode doit etre AUTO_INTELLIGENT.",
        "never_lower_stop doit etre true.",
        "trailing_stop_loss.initial_stop doit etre defini avant armement.",
    ],
    "momentum_breakout": [
        "breakout.resistance est obligatoire.",
        "entry.enabled peut etre true.",
        "anti_chase obligatoire.",
        "volume_confirmation recommande.",
        "trailing_stop_loss.initial_stop doit etre defini avant armement.",
    ],
    "breakout_retest": [
        "retest.zone_min et retest.zone_max sont obligatoires.",
        "Le setup attend un retour vers une zone, pas une cassure directe.",
        "trailing_stop_loss.initial_stop doit etre defini avant armement.",
    ],
    "range_breakout": [
        "range.high et range.low sont obligatoires.",
        "breakout_side doit etre defini.",
        "trailing_stop_loss.initial_stop doit etre defini avant armement.",
    ],
    "pullback_continuation": [
        "pullback.entry_reference ou pullback.zone_min/zone_max est obligatoire.",
        "La tendance principale doit etre verifiee.",
        "trailing_stop_loss.initial_stop doit etre defini avant armement.",
    ],
    "aggressive_rebound": [
        "support_zone.min et support_zone.max sont obligatoires.",
        "Le setup cherche un rebond depuis support, pas une cassure de resistance.",
        "trailing_stop_loss.initial_stop doit etre defini avant armement.",
    ],
}


VOLUME_CONFIRMATION_POLICY_BY_SETUP_TYPE: dict[str, dict[str, Any]] = {
    "momentum_breakout": {
        "required_for_entry": True,
    },
    "breakout_retest": {
        "required_for_entry": False,
        "weak_volume_action": "WARNING_ONLY",
    },
    "pullback_continuation": {
        "required_for_entry": False,
        "weak_volume_action": "WARNING_ONLY",
    },
    "aggressive_rebound": {
        "required_for_entry": False,
        "weak_volume_action": "WARNING_ONLY",
    },
    "range_breakout": {
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
}


REQUIRED_BY_SETUP_TYPE: dict[str, list[str]] = {
    "momentum_breakout": [
        "breakout.resistance",
        "entry.maximum_limit_price before arming",
        *TRAILING_STOP_TEMPLATE_RULES,
    ],
    "breakout_retest": [
        "breakout.daily_close_above",
        "retest.zone_min",
        "retest.zone_max",
        *TRAILING_STOP_TEMPLATE_RULES,
    ],
    "aggressive_rebound": [
        "support_zone.min",
        "support_zone.max",
        *TRAILING_STOP_TEMPLATE_RULES,
    ],
    "range_breakout": [
        "range.high",
        "range.low",
        *TRAILING_STOP_TEMPLATE_RULES,
    ],
    "pullback_continuation": [
        "pullback.entry_reference",
        *TRAILING_STOP_TEMPLATE_RULES,
    ],
    "runner": [
        "setup_role=MANAGEMENT_ONLY",
        "entry.enabled=false",
        "position_source.mode=adopt_existing_ibkr_position",
        "position_source.require_existing_position=true",
        *TRAILING_STOP_MANAGEMENT_RULES,
        "management.stop_management.steps",
    ],
    "trailing_runner": [
        "setup_role=MANAGEMENT_ONLY",
        "entry.enabled=false",
        "position_source.mode=adopt_existing_ibkr_position",
        "position_source.require_existing_position=true",
        "trailing_stop_loss.mode=AUTO_INTELLIGENT",
        "trailing_stop_loss.never_lower_stop=true",
        *TRAILING_STOP_MANAGEMENT_RULES,
    ],
    "position_management": [
        "setup_role=MANAGEMENT_ONLY",
        "entry.enabled=false",
        "position_source.mode=adopt_existing_ibkr_position",
        "position_source.require_existing_position=true",
        *TRAILING_STOP_MANAGEMENT_RULES,
    ],
}


def supported_setup_types() -> list[str]:
    return list(SUPPORTED_SETUP_TYPES)


def setup_type_selection_guide() -> dict[str, str]:
    return {name: SETUP_TYPE_SELECTION_GUIDE[name] for name in SUPPORTED_SETUP_TYPES}


def setup_specific_options() -> dict[str, dict[str, Any]]:
    return {name: deepcopy(SETUP_SPECIFIC_OPTIONS[name]) for name in SUPPORTED_SETUP_TYPES}


def validation_rules() -> dict[str, list[str]]:
    return {name: list(rules) for name, rules in VALIDATION_RULES.items()}


def required_by_setup_type() -> dict[str, list[str]]:
    return {name: list(REQUIRED_BY_SETUP_TYPE[name]) for name in SUPPORTED_SETUP_TYPES}


def volume_confirmation_policy_by_setup_type() -> dict[str, dict[str, Any]]:
    return {
        name: deepcopy(VOLUME_CONFIRMATION_POLICY_BY_SETUP_TYPE[name])
        for name in SUPPORTED_SETUP_TYPES
    }
