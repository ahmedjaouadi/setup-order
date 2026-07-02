from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from app.conversion import canonicalize_setup_config
from app.utils.id_generator import new_id


@dataclass(slots=True)
class ConversionResult:
    ok: bool
    config: dict[str, Any] | None = None
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    extracted: dict[str, Any] = field(default_factory=dict)


def convert_text_to_setup(
    symbol: str,
    text: str,
    defaults: dict[str, Any],
    enabled: bool = True,
) -> ConversionResult:
    if not text.strip():
        return ConversionResult(ok=False, errors=["Setup text is required"])
    clean_symbol = symbol.strip().upper()
    parsed_json = _parse_json_setup(text)
    if parsed_json is not None:
        config_payload = _setup_payload_from_json(parsed_json)
        config_symbol = str(config_payload.get("symbol", "")).strip().upper()
        clean_symbol = clean_symbol or config_symbol
        if not clean_symbol:
            return ConversionResult(ok=False, errors=["Ticker is required"])
        if config_symbol and config_symbol != clean_symbol:
            return ConversionResult(
                ok=False,
                errors=[
                    (
                        "Ticker field must match setup JSON symbol "
                        f"({clean_symbol} != {config_symbol})"
                    )
                ],
                extracted={"json_detected": True, "symbol": config_symbol},
            )
        config_payload.setdefault("symbol", clean_symbol)
        config_payload.setdefault("enabled", enabled)
        canonical = canonicalize_setup_config(parsed_json, defaults=defaults)
        return ConversionResult(
            ok=True,
            config=canonical.config,
            warnings=[
                "JSON setup detected; text conversion skipped",
                *canonical.warnings,
            ],
            extracted={
                "json_detected": True,
                "canonical_mapped_fields": canonical.mapped_fields,
            },
        )
    if not clean_symbol:
        return ConversionResult(ok=False, errors=["Ticker is required"])

    normalized = _normalize(text)
    setup_type = _detect_setup_type(normalized)
    numbers = _extract_numbers(normalized)
    stop_loss = _extract_labeled_number(normalized, ["stop", "sl", "hard stop"])
    max_risk = _extract_labeled_number(normalized, ["risque", "risk"])
    max_position = _extract_labeled_number(
        normalized,
        ["budget", "position", "exposition", "capital"],
    )
    ranges = _extract_ranges(normalized)
    breakout_level = _extract_breakout_level(normalized)
    warnings: list[str] = []
    errors: list[str] = []

    if stop_loss is None:
        errors.append("Add a stop loss in the setup text")
    if max_risk is None:
        max_risk = float(defaults["risk"]["max_risk_per_trade_usd"])
        warnings.append(f"Risk default used: {max_risk:g} USD")
    if max_position is None:
        max_position = float(defaults["risk"]["max_position_amount_usd"])
        warnings.append(f"Position budget default used: {max_position:g} USD")

    extracted = {
        "setup_type": setup_type,
        "numbers": numbers,
        "ranges": ranges,
        "breakout_level": breakout_level,
        "stop_loss": stop_loss,
        "max_risk_usd": max_risk,
        "max_position_amount_usd": max_position,
    }
    if errors:
        return ConversionResult(
            ok=False,
            errors=errors,
            warnings=warnings,
            extracted=extracted,
        )

    config = _build_config(
        symbol=clean_symbol,
        setup_type=setup_type,
        text=normalized,
        ranges=ranges,
        numbers=numbers,
        breakout_level=breakout_level,
        stop_loss=float(stop_loss),
        max_risk=float(max_risk),
        max_position=float(max_position),
        mode=str(defaults["app"].get("mode", "paper")),
        enabled=enabled,
        defaults=defaults,
    )
    if config is None:
        return ConversionResult(
            ok=False,
            errors=[
                "Not enough price levels detected. Add an entry/breakout level or a price zone."
            ],
            warnings=warnings,
            extracted=extracted,
        )
    canonical = canonicalize_setup_config(config, defaults=defaults)
    return ConversionResult(
        ok=True,
        config=canonical.config,
        warnings=[
            *warnings,
            *canonical.warnings,
        ],
        extracted=extracted,
    )


def _parse_json_setup(text: str) -> dict[str, Any] | None:
    stripped = text.strip()
    if not stripped.startswith("{"):
        return None
    try:
        parsed = json.loads(stripped)
    except json.JSONDecodeError:
        return None
    if not isinstance(parsed, dict):
        return None
    return parsed


def _setup_payload_from_json(parsed: dict[str, Any]) -> dict[str, Any]:
    skeleton = parsed.get("skeleton")
    return skeleton if isinstance(skeleton, dict) else parsed


def _build_config(
    symbol: str,
    setup_type: str,
    text: str,
    ranges: list[tuple[float, float]],
    numbers: list[float],
    breakout_level: float | None,
    stop_loss: float,
    max_risk: float,
    max_position: float,
    mode: str,
    enabled: bool,
    defaults: dict[str, Any],
) -> dict[str, Any] | None:
    setup_defaults = defaults.get("setup_defaults", {})
    entry_defaults = setup_defaults.get("entry", {})
    order_defaults = defaults.get("orders", {})
    timeframe_defaults = setup_defaults.get("timeframes", {})
    breakout_defaults = setup_defaults.get("breakout", {})
    retest_defaults = setup_defaults.get("retest", {})
    confirmation_defaults = setup_defaults.get("confirmation", {})
    momentum_defaults = setup_defaults.get("momentum", {})
    range_defaults = setup_defaults.get("range", {})
    base = {
        "setup_id": _make_setup_id(symbol, setup_type),
        "symbol": symbol,
        "enabled": enabled,
        "mode": mode,
        "setup_type": setup_type,
        "setup_role": "ENTRY_AND_MANAGEMENT",
        "direction": "long",
        "entry": {
            "enabled": True,
            "order_type": order_defaults.get("default_entry_order_type", "STP_LMT"),
            "trigger_offset": entry_defaults.get("trigger_offset", 0.02),
            "limit_offset": entry_defaults.get("limit_offset", 0.05),
            "cancel_if_not_filled_after_minutes": order_defaults.get(
                "cancel_unfilled_entry_after_minutes",
                30,
            ),
        },
        "risk": {
            "max_position_amount_usd": max_position,
            "max_risk_usd": max_risk,
            "risk_model": "TRAILING_STOP_INITIAL_RISK",
            "emergency_exit_if_stop_fails": True,
            "block_entry_if_risk_unknown": True,
            "block_entry_if_trailing_stop_missing": True,
        },
        "trailing_stop_loss": {
            "enabled": True,
            "mode": "AUTO_INTELLIGENT",
            "never_lower_stop": True,
            "initial_stop": stop_loss,
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
                "atr_timeframe": "1h",
                "atr_period": 14,
                "atr_multiplier_initial": "AUTO",
                "atr_multiplier_trailing": "AUTO",
                "structure_reference": "higher_low_or_support",
                "buffer_policy": "MAX_OF_TICK_SPREAD_ATR_FRACTION",
            },
            "ratchet_rules": {
                "update_on_closed_bar_only": True,
                "timeframe": "15m",
                "do_not_lower_stop": True,
                "do_not_update_outside_rth": True,
                "do_not_update_if_spread_wide": True,
            },
            "broker_order": {
                "order_type": "TRAIL_OR_MANAGED_STOP",
                "attach_to_entry_order": True,
                "required_before_entry_transmission": True,
                "use_native_ibkr_trailing_order_if_available": True,
                "fallback_to_managed_stop_updates": True,
            },
        },
        "management": {
            "take_profit_mode": "none",
            "never_lower_stop": True,
            "stop_management": {
                "mode": "TRAILING_STOP_LOSS",
                "source": "trailing_stop_loss",
                "never_lower_stop": True,
                "steps": _extract_stop_steps(text),
            },
        },
        "targets": _extract_targets(text),
    }

    if setup_type == "breakout_retest":
        zone = _first_range(ranges)
        if zone is None:
            return None
        level = breakout_level or zone[1]
        return {
            **base,
            "timeframes": {
                "signal": timeframe_defaults.get("signal", "15m"),
                "confirmation": timeframe_defaults.get("confirmation", "1d"),
            },
            "breakout": {
                "enabled": True,
                "daily_close_above": level,
                "valid_for_days": breakout_defaults.get("valid_for_days", 5),
            },
            "retest": {
                "enabled": True,
                "zone_min": zone[0],
                "zone_max": zone[1],
                "no_close_below": zone[0],
                "max_retest_days": retest_defaults.get("max_retest_days", 5),
            },
            "confirmation": {
                "bullish_candle_required": True,
                "close_above_previous_high": True,
                "min_volume_ratio": confirmation_defaults.get("min_volume_ratio", 0.8),
            },
        }

    if setup_type == "aggressive_rebound":
        zone = _first_range(ranges)
        if zone is None:
            return None
        return {
            **base,
            "support_zone": {"min": zone[0], "max": zone[1]},
            "conditions": {
                "price_touched_zone": True,
                "no_close_below_support": True,
                "bullish_candle_required": True,
                "close_above_previous_high": True,
            },
            "invalidation": {
                "close_below": zone[0],
                "hard_stop": stop_loss,
            },
        }

    if setup_type == "pullback_continuation":
        reference = breakout_level or _first_price_above_stop(numbers, stop_loss)
        if reference is None:
            return None
        return {
            **base,
            "pullback": {"entry_reference": reference},
            "trend_filter": {
                "price_above_ema_20": True,
                "ema_20_above_ema_50": True,
            },
            "confirmation": {"bullish_reversal_candle": True},
        }

    if setup_type == "momentum_breakout":
        resistance = breakout_level or _first_price_above_stop(numbers, stop_loss)
        if resistance is None:
            return None
        return {
            **base,
            "breakout": {
                "resistance": resistance,
                "price_above_resistance": True,
                "volume_above_average": _extract_volume_ratio(text)
                or momentum_defaults.get("volume_above_average", 1.5),
                "relative_strength_required": "relative" in text,
            },
        }

    if setup_type == "range_breakout":
        zone = _first_range(ranges)
        if zone is None:
            return None
        return {
            **base,
            "range": {
                "high": zone[1],
                "low": zone[0],
                "min_days_inside_range": range_defaults.get("min_days_inside_range", 5),
            },
            "invalidation": {"close_back_inside_range": True},
        }

    if setup_type == "runner":
        entry_price = breakout_level or _first_price_above_stop(numbers, stop_loss)
        if entry_price is None:
            return None
        base["entry"] = {**base["entry"], "entry_price": entry_price}
        return {
            **base,
            "take_profit": {"enabled": False},
            "stop_management": {
                "mode": "TRAILING_STOP_LOSS",
                "source": "trailing_stop_loss",
                "never_lower_stop": True,
            },
        }

    return None


def _detect_setup_type(text: str) -> str:
    if any(word in text for word in ["runner", "laisser courir", "sans take profit"]):
        return "runner"
    if any(word in text for word in ["range", "boite", "box", "consolidation"]):
        return "range_breakout"
    if any(word in text for word in ["momentum", "volume", "relative strength"]):
        return "momentum_breakout"
    if any(word in text for word in ["pullback", "repli", "ema", "vwap", "moyenne mobile"]):
        return "pullback_continuation"
    if any(word in text for word in ["rebond", "support", "bounce"]):
        return "aggressive_rebound"
    if any(word in text for word in ["breakout", "cassure", "casser", "retest", "retour"]):
        return "breakout_retest"
    return "breakout_retest"


def _normalize(text: str) -> str:
    text = text.replace(",", ".")
    text = text.replace("$", " usd ")
    text = text.replace("€", " eur ")
    text = text.replace("–", "-").replace("—", "-")
    text = "".join(
        char
        for char in unicodedata.normalize("NFKD", text)
        if not unicodedata.combining(char)
    )
    return re.sub(r"\s+", " ", text.lower()).strip()


def _extract_numbers(text: str) -> list[float]:
    return [float(item) for item in re.findall(r"(?<![a-z])\d+(?:\.\d+)?", text)]


def _extract_ranges(text: str) -> list[tuple[float, float]]:
    pattern = r"(\d+(?:\.\d+)?)\s*(?:-|a|to|jusqu'a)\s*(\d+(?:\.\d+)?)"
    ranges: list[tuple[float, float]] = []
    for left, right in re.findall(pattern, text):
        a = float(left)
        b = float(right)
        ranges.append((min(a, b), max(a, b)))
    return ranges


def _extract_labeled_number(text: str, labels: list[str]) -> float | None:
    for label in labels:
        pattern = rf"{re.escape(label)}(?:\s+(?:max|initial|loss|usd|de)){{0,4}}\s*[:=]?\s*(\d+(?:\.\d+)?)"
        match = re.search(pattern, text)
        if match:
            return float(match.group(1))
    return None


def _extract_breakout_level(text: str) -> float | None:
    patterns = [
        r"(?:daily close|cloture|close|cassure|breakout|resistance|au-dessus|above|>)\s*(?:de|a|above|>)?\s*(\d+(?:\.\d+)?)",
        r"(\d+(?:\.\d+)?)\s*(?:breakout|cassure|resistance)",
    ]
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            return float(match.group(1))
    return None


def _extract_volume_ratio(text: str) -> float | None:
    match = re.search(r"volume(?:\s+above|\s+>)?\s*(\d+(?:\.\d+)?)x?", text)
    return float(match.group(1)) if match else None


def _extract_targets(text: str) -> list[dict[str, Any]]:
    targets: list[dict[str, Any]] = []
    for index, (zone_min, zone_max) in enumerate(
        re.findall(r"(?:target|objectif|tp)\s*\d*\s*[:=]?\s*(\d+(?:\.\d+)?)\s*(?:-|a|to)\s*(\d+(?:\.\d+)?)", text),
        start=1,
    ):
        low = float(zone_min)
        high = float(zone_max)
        targets.append(
            {
                "name": f"target_{index}",
                "zone_min": min(low, high),
                "zone_max": max(low, high),
                "action": "notify_and_raise_stop",
            }
        )
    return targets


def _extract_stop_steps(text: str) -> list[dict[str, Any]]:
    steps: list[dict[str, Any]] = []
    pattern = (
        r"(?:si|when|above|au-dessus)\s*(?:prix\s*)?"
        r"(\d+(?:\.\d+)?)\s*(?:stop|sl)\s*(?:a|to|=|:)?\s*(\d+(?:\.\d+)?)"
    )
    for index, (price, stop) in enumerate(re.findall(pattern, text), start=1):
        steps.append(
            {
                "name": f"raise_stop_{index}",
                "when_price_above": float(price),
                "new_stop": float(stop),
            }
        )
    return steps


def _first_range(ranges: list[tuple[float, float]]) -> tuple[float, float] | None:
    return ranges[0] if ranges else None


def _first_price_above_stop(numbers: list[float], stop_loss: float) -> float | None:
    for number in numbers:
        if number > stop_loss:
            return number
    return None


def _make_setup_id(symbol: str, setup_type: str) -> str:
    today = datetime.now(timezone.utc).strftime("%Y%m%d")
    suffix = new_id("txt").split("_", 1)[1]
    return f"{symbol}_{setup_type.upper()}_{today}_{suffix}"
