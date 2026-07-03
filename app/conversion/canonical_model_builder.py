from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any

from app.conversion.alias_resolver import AliasResolver, normalize_key
from app.conversion.canonical_field_registry import load_canonical_fields


NUMERIC_FIELDS = {
    "breakout.daily_close_above",
    "breakout.resistance",
    "entry.cancel_if_not_filled_after_minutes",
    "entry.entry_price",
    "entry.limit_offset",
    "entry.limit_price",
    "entry.maximum_limit_price",
    "entry.minimum_tick",
    "entry.trigger_offset",
    "entry.trigger_price",
    "pullback.entry_reference",
    "range.high",
    "range.low",
    "retest.max_retest_days",
    "retest.no_close_below",
    "retest.zone_max",
    "retest.zone_min",
    "risk.max_position_amount_usd",
    "risk.max_risk_usd",
    "trailing_stop_loss.current_stop",
    "support_zone.max",
    "support_zone.min",
    "trailing_stop_loss.initial_stop",
    "trailing_stop_loss.calculation.atr.period",
    "trailing_stop_loss.calculation.atr.min_multiplier",
    "trailing_stop_loss.calculation.atr.max_multiplier",
    "trailing_stop_loss.calculation.structure.min_tick_buffer",
    "trailing_stop_loss.calculation.structure.spread_buffer_multiplier",
    "trailing_stop_loss.calculation.structure.atr_fraction_buffer",
    "trailing_stop_loss.ratchet_rules.min_improvement_atr_fraction",
    "trailing_stop_loss.ratchet_rules.break_even_policy.trigger_after_profit_r_multiple",
    "trailing_stop_loss.calculation.atr_period",
    "trailing_stop_loss.calculation.min_tick_buffer",
    "trailing_stop_loss.calculation.spread_buffer_multiplier",
}

BOOLEAN_FIELDS = {
    "enabled",
    "entry.enabled",
    "position_source.block_if_position_not_found",
    "position_source.reconcile_on_load",
    "position_source.require_existing_position",
    "risk.block_entry_if_risk_unknown",
    "risk.block_entry_if_trailing_stop_missing",
    "risk.emergency_exit_if_stop_fails",
    "trailing_stop_loss.enabled",
    "trailing_stop_loss.never_lower_stop",
    "trailing_stop_loss.activation.activate_before_entry_transmission",
    "trailing_stop_loss.activation.entry_order_requires_attached_trailing_stop",
    "trailing_stop_loss.broker_order.attach_to_entry_order",
    "trailing_stop_loss.broker_order.block_if_broker_stop_not_confirmed",
    "trailing_stop_loss.broker_order.entry_parent_transmit",
    "trailing_stop_loss.broker_order.fallback_to_managed_stop_updates",
    "trailing_stop_loss.broker_order.parent_child_bracket_required",
    "trailing_stop_loss.broker_order.required_before_entry_transmission",
    "trailing_stop_loss.broker_order.trailing_stop_child_transmit",
    "trailing_stop_loss.broker_order.use_native_ibkr_trailing_order_if_available",
    "trailing_stop_loss.calculation.risk_constraints.block_if_initial_stop_above_entry_for_long",
    "trailing_stop_loss.calculation.risk_constraints.block_if_initial_stop_below_entry_for_short",
    "trailing_stop_loss.calculation.risk_constraints.block_if_quantity_zero",
    "trailing_stop_loss.calculation.risk_constraints.respect_max_position_amount_usd",
    "trailing_stop_loss.calculation.risk_constraints.respect_max_risk_usd",
    "trailing_stop_loss.calculation.stock_specific_adjustment.enabled",
    "trailing_stop_loss.ratchet_rules.allow_break_even_move",
    "trailing_stop_loss.ratchet_rules.break_even_policy.enabled",
    "trailing_stop_loss.ratchet_rules.do_not_lower_stop",
    "trailing_stop_loss.ratchet_rules.do_not_update_on_unconfirmed_intrabar_move",
    "trailing_stop_loss.ratchet_rules.do_not_update_if_spread_wide",
    "trailing_stop_loss.ratchet_rules.do_not_update_outside_rth",
    "trailing_stop_loss.ratchet_rules.enabled",
    "trailing_stop_loss.ratchet_rules.move_only_down_for_short",
    "trailing_stop_loss.ratchet_rules.move_only_up_for_long",
    "trailing_stop_loss.ratchet_rules.update_on_closed_bar_only",
}

SETUP_ROLE_VALUES = {
    "entry_and_management": "ENTRY_AND_MANAGEMENT",
    "entry_only": "ENTRY_ONLY",
    "management_only": "MANAGEMENT_ONLY",
}

TEMPLATE_HELPER_KEYS = {
    "expected_output",
    "setup_type_options",
    "setup_type_selection_guide",
    "supported_setup_types",
    "required_by_setup_type",
    "validation_rules",
    "selection_rules",
    "volume_confirmation_policy_by_setup_type",
}


@dataclass(slots=True)
class CanonicalizationResult:
    config: dict[str, Any]
    warnings: list[str] = field(default_factory=list)
    mapped_fields: list[dict[str, str]] = field(default_factory=list)


def canonicalize_setup_config(
    raw_config: dict[str, Any],
    *,
    defaults: dict[str, Any] | None = None,
    resolver: AliasResolver | None = None,
) -> CanonicalizationResult:
    if not isinstance(raw_config, dict):
        raise TypeError("Setup config must be a mapping")

    raw_config, wrapper_warnings = _unwrap_setup_template(raw_config)
    active_resolver = resolver or AliasResolver(load_canonical_fields())
    mapped_fields: list[dict[str, str]] = []
    canonical = _canonicalize_mapping(
        deepcopy(raw_config),
        resolver=active_resolver,
        mapped_fields=mapped_fields,
    )
    warnings = [
        *wrapper_warnings,
        *_normalize_setup_metadata(canonical, defaults),
    ]
    return CanonicalizationResult(
        config=canonical,
        warnings=warnings,
        mapped_fields=mapped_fields,
    )


def _unwrap_setup_template(raw_config: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    skeleton = raw_config.get("skeleton")
    if not isinstance(skeleton, dict):
        return raw_config, []
    return _strip_template_helper_keys(skeleton), [
        "Setup template wrapper detected; using skeleton and ignoring helper metadata."
    ]


def _strip_template_helper_keys(value: Any) -> Any:
    if isinstance(value, list):
        return [_strip_template_helper_keys(item) for item in value]
    if not isinstance(value, dict):
        return value
    cleaned: dict[str, Any] = {}
    for key, item in value.items():
        key_text = str(key)
        if key_text.startswith("_") or key_text in TEMPLATE_HELPER_KEYS:
            continue
        cleaned[key_text] = _strip_template_helper_keys(item)
    return cleaned


def _canonicalize_mapping(
    payload: dict[str, Any],
    *,
    resolver: AliasResolver,
    mapped_fields: list[dict[str, str]],
    parent_path: str = "",
) -> dict[str, Any]:
    canonical: dict[str, Any] = {}
    canonical_parent = resolver.canonical_path(parent_path) or _normalize_section_path(
        parent_path
    )

    for raw_key, raw_value in payload.items():
        canonical_path = _resolve_canonical_path(
            raw_key=str(raw_key),
            parent_path=parent_path,
            canonical_parent=canonical_parent,
            resolver=resolver,
        )
        value = _canonicalize_value(
            raw_value,
            resolver=resolver,
            mapped_fields=mapped_fields,
            canonical_path=canonical_path,
        )
        output_path = _relative_output_path(canonical_path, canonical_parent)
        _set_nested_value(canonical, output_path, value)
        if canonical_path != _joined_path(parent_path, str(raw_key)):
            mapped_fields.append(
                {
                    "raw_key": _joined_path(parent_path, str(raw_key)),
                    "canonical_path": canonical_path,
                }
            )
        elif output_path != str(raw_key):
            mapped_fields.append(
                {
                    "raw_key": _joined_path(parent_path, str(raw_key)),
                    "canonical_path": canonical_path,
                }
            )
    return canonical


def _canonicalize_value(
    value: Any,
    *,
    resolver: AliasResolver,
    mapped_fields: list[dict[str, str]],
    canonical_path: str,
) -> Any:
    if isinstance(value, dict):
        return _canonicalize_mapping(
            value,
            resolver=resolver,
            mapped_fields=mapped_fields,
            parent_path=canonical_path,
        )
    if isinstance(value, list):
        normalized_items: list[Any] = []
        for item in value:
            if isinstance(item, dict):
                normalized_items.append(
                    _canonicalize_mapping(
                        item,
                        resolver=resolver,
                        mapped_fields=mapped_fields,
                        parent_path="",
                    )
                )
            elif isinstance(item, list):
                normalized_items.append(
                    _canonicalize_value(
                        item,
                        resolver=resolver,
                        mapped_fields=mapped_fields,
                        canonical_path=canonical_path,
                    )
                )
            else:
                normalized_items.append(_coerce_scalar_value(canonical_path, item))
        return normalized_items
    return _coerce_scalar_value(canonical_path, value)


def _resolve_canonical_path(
    *,
    raw_key: str,
    parent_path: str,
    canonical_parent: str | None,
    resolver: AliasResolver,
) -> str:
    raw_path = _joined_path(parent_path, raw_key)

    canonical = resolver.canonical_path(raw_path)
    if canonical:
        return canonical

    alias_match = resolver.resolve(raw_path)
    if alias_match:
        return alias_match

    leaf_match = resolver.resolve(raw_key)
    if leaf_match and canonical_parent:
        leaf_parent = leaf_match.rsplit(".", 1)[0] if "." in leaf_match else ""
        if leaf_parent == canonical_parent:
            return leaf_match

    if canonical_parent:
        return _joined_path(canonical_parent, normalize_key(raw_key))
    return normalize_key(raw_key)


def _coerce_scalar_value(canonical_path: str, value: Any) -> Any:
    if not isinstance(value, str):
        return value
    text = value.strip()
    if text == "":
        return ""

    normalized_path = canonical_path.lower()
    lower = text.lower()
    if normalized_path in BOOLEAN_FIELDS:
        if lower in {"true", "yes", "1", "on"}:
            return True
        if lower in {"false", "no", "0", "off"}:
            return False

    if normalized_path in NUMERIC_FIELDS:
        return _parse_number(text)

    return value


def _parse_number(raw_value: str) -> float | str:
    cleaned = (
        raw_value.strip()
        .replace("$", "")
        .replace("USD", "")
        .replace("usd", "")
        .replace(",", ".")
        .strip()
    )
    try:
        return float(cleaned)
    except ValueError:
        return raw_value


def _set_nested_value(target: dict[str, Any], path: str, value: Any) -> None:
    parts = [part for part in path.split(".") if part]
    if not parts:
        return
    cursor = target
    for part in parts[:-1]:
        existing = cursor.get(part)
        if not isinstance(existing, dict):
            existing = {}
            cursor[part] = existing
        cursor = existing
    cursor[parts[-1]] = value


def _relative_output_path(canonical_path: str, canonical_parent: str | None) -> str:
    if canonical_parent and canonical_path.startswith(f"{canonical_parent}."):
        return canonical_path[len(canonical_parent) + 1 :]
    return canonical_path


def _joined_path(parent: str, child: str) -> str:
    if not parent:
        return str(child)
    return f"{parent}.{child}"


def _normalize_section_path(path: str) -> str | None:
    if not path:
        return None
    normalized = ".".join(normalize_key(part) for part in str(path).split(".") if part)
    return normalized or None


def _normalize_setup_metadata(
    config: dict[str, Any],
    defaults: dict[str, Any] | None,
) -> list[str]:
    warnings: list[str] = []
    symbol = config.get("symbol")
    if isinstance(symbol, str):
        config["symbol"] = symbol.strip().upper()

    setup_type = config.get("setup_type")
    if isinstance(setup_type, str):
        config["setup_type"] = setup_type.strip().lower()

    direction = config.get("direction")
    if isinstance(direction, str):
        config["direction"] = direction.strip().lower()

    setup_role = config.get("setup_role")
    if isinstance(setup_role, str):
        normalized_role = normalize_key(setup_role)
        config["setup_role"] = SETUP_ROLE_VALUES.get(normalized_role, setup_role.strip())

    entry = config.get("entry")
    if isinstance(entry, dict):
        order_type = entry.get("order_type")
        if isinstance(order_type, str):
            entry["order_type"] = order_type.strip().upper().replace(" ", "_")

    risk = config.get("risk")
    if isinstance(risk, dict):
        initial_stop = _coerce_optional_number(risk.get("initial_stop_loss"))
        protective_stop = _coerce_optional_number(risk.get("protective_stop"))
    else:
        initial_stop = None
        protective_stop = None

    trailing = config.get("trailing_stop_loss")
    if isinstance(trailing, dict):
        mode = trailing.get("mode")
        if isinstance(mode, str):
            trailing["mode"] = mode.strip().upper()
            if trailing["mode"] == "LEGACY_INITIAL_STOP_MIGRATED":
                trailing["mode"] = "AUTO_INTELLIGENT"
                trailing.setdefault("migration_status", "MIGRATED_TO_TRAILING_STOP")
        trailing_stop = trailing.get("initial_stop")
        legacy_stop = initial_stop if initial_stop is not None else protective_stop
        if trailing_stop is None and legacy_stop is not None:
            trailing["initial_stop"] = legacy_stop
            trailing.setdefault("current_stop", legacy_stop)
            trailing.setdefault("mode", "AUTO_INTELLIGENT")
            trailing.setdefault("migration_status", "MIGRATED_TO_TRAILING_STOP")
            warnings.append("MIGRATED_TO_TRAILING_STOP")
        if trailing.get("enabled") is None:
            trailing["enabled"] = True
        trailing.setdefault("never_lower_stop", True)
        config["trailing_stop_loss"] = _merge_trailing_stop_defaults(
            trailing,
            initial_stop=trailing.get("initial_stop") if trailing.get("initial_stop") is not None else legacy_stop,
            migrated=trailing.get("migration_status") == "MIGRATED_TO_TRAILING_STOP",
        )
    elif initial_stop is not None or protective_stop is not None:
        config["trailing_stop_loss"] = _legacy_trailing_stop(initial_stop or protective_stop)
        warnings.append("MIGRATED_TO_TRAILING_STOP")
    else:
        config["trailing_stop_loss"] = _merge_trailing_stop_defaults(
            {
                "enabled": True,
                "initial_stop": None,
                "current_stop": None,
                "stop_source": "AUTO_CALCULATED",
            },
            initial_stop=None,
            migrated=False,
        )

    trailing = config.get("trailing_stop_loss")
    if isinstance(trailing, dict):
        trailing_stop = trailing.get("initial_stop")
        risk = config.setdefault("risk", {})
        if isinstance(risk, dict):
            if (
                trailing_stop is not None
                and (
                risk.get("initial_stop_loss") not in (None, trailing_stop)
                or risk.get("protective_stop") not in (None, trailing_stop)
                )
            ):
                warnings.append("LEGACY_STOP_FIELDS_IGNORED_IN_FAVOR_OF_TRAILING_STOP")
            risk.pop("initial_stop_loss", None)
            risk.pop("protective_stop", None)
            risk.pop("never_lower_stop", None)
            risk.pop("trailing_stop_loss", None)

    default_mode = str(
        ((defaults or {}).get("app") or {}).get("mode", "paper")
    ).strip().lower()
    if default_mode not in {"paper", "live"}:
        default_mode = "paper"

    mode = config.get("mode")
    raw_mode = str(mode or default_mode).strip().lower()
    if raw_mode in {"simulation", "simulated"}:
        warnings.append("Legacy simulation mode was promoted to paper.")
        raw_mode = "paper"
    if raw_mode not in {"paper", "live"}:
        warnings.append(f"Unknown mode '{raw_mode}' was replaced with {default_mode}.")
        raw_mode = default_mode
    config["mode"] = raw_mode
    return warnings


def _legacy_trailing_stop(initial_stop: Any) -> dict[str, Any]:
    return {
        "enabled": True,
        "mode": "AUTO_INTELLIGENT",
        "initial_stop": initial_stop,
        "current_stop": initial_stop,
        "never_lower_stop": True,
        "stop_source": "MIGRATED_FROM_LEGACY_STOP",
        "applies_to": "ENTRY_AND_POSITION_MANAGEMENT",
        "migration_status": "MIGRATED_TO_TRAILING_STOP",
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
            "do_not_lower_stop": True,
            "do_not_update_outside_rth": True,
            "do_not_update_if_spread_wide": True,
            "do_not_update_on_unconfirmed_intrabar_move": True,
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


def _merge_trailing_stop_defaults(
    trailing: dict[str, Any],
    *,
    initial_stop: Any,
    migrated: bool,
) -> dict[str, Any]:
    defaults = _legacy_trailing_stop(initial_stop)
    if not migrated:
        defaults["mode"] = "AUTO_INTELLIGENT"
    return _deep_merge(defaults, trailing)


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    merged = deepcopy(base)
    for key, value in override.items():
        existing = merged.get(key)
        if isinstance(existing, dict) and isinstance(value, dict):
            merged[key] = _deep_merge(existing, value)
        else:
            merged[key] = value
    return merged


def _coerce_optional_number(value: Any) -> Any:
    if value in (None, "") or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return value
    if isinstance(value, str):
        return _parse_number(value)
    return value
