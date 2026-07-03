from __future__ import annotations

import json
from copy import deepcopy
from dataclasses import asdict, dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any


SUPPORTED_SETUP_TYPES = {
    "aggressive_rebound",
    "breakout_retest",
    "momentum_breakout",
    "position_management",
    "pullback_continuation",
    "range_breakout",
    "runner",
    "trailing_runner",
}


@dataclass(frozen=True, slots=True)
class SemanticValidationIssue:
    source: str
    level: str
    code: str
    path: str
    message: str


@dataclass(slots=True)
class SemanticValidationReport:
    setup_type: str = ""
    schema_files: list[str] = field(default_factory=list)
    issues: list[SemanticValidationIssue] = field(default_factory=list)

    @property
    def errors(self) -> list[str]:
        return [issue.message for issue in self.issues if issue.level == "error"]

    @property
    def warnings(self) -> list[str]:
        return [issue.message for issue in self.issues if issue.level == "warning"]

    @property
    def valid(self) -> bool:
        return not self.errors

    def to_details(self) -> dict[str, Any]:
        return {
            "valid": self.valid,
            "setup_type": self.setup_type,
            "schema_files": list(self.schema_files),
            "error_count": len(self.errors),
            "warning_count": len(self.warnings),
            "issues": [asdict(issue) for issue in self.issues],
        }


class SemanticValidationService:
    def __init__(self, schema_dir: Path | None = None) -> None:
        self.schema_dir = schema_dir or _default_schema_dir()

    def validate(self, config: dict[str, Any]) -> SemanticValidationReport:
        config = _with_legacy_trailing_stop(config)
        setup_type = str(config.get("setup_type") or "").strip().lower()
        report = SemanticValidationReport(setup_type=setup_type)
        schemas = self._schemas_for_setup_type(setup_type)
        report.schema_files = [name for name, _ in schemas]

        for schema_name, schema in schemas:
            self._validate_schema_node(
                value=config,
                schema=schema,
                path="",
                issues=report.issues,
                source=f"schema:{schema_name}",
            )

        self._validate_common_semantics(config, report.issues)
        self._validate_setup_specific_semantics(setup_type, config, report.issues)
        report.issues = _dedupe_issues(report.issues)
        return report

    def _schemas_for_setup_type(self, setup_type: str) -> list[tuple[str, dict[str, Any]]]:
        schemas = [("setup.base.schema.json", self._load_schema("setup.base.schema.json"))]
        if setup_type in SUPPORTED_SETUP_TYPES:
            filename = f"setup.{setup_type}.schema.json"
            schemas.append((filename, self._load_schema(filename)))
        return schemas

    @lru_cache(maxsize=32)
    def _load_schema(self, filename: str) -> dict[str, Any]:
        path = self.schema_dir / filename
        with path.open("r", encoding="utf-8") as handle:
            payload = json.load(handle)
        if not isinstance(payload, dict):
            raise ValueError(f"Schema file must contain an object: {path}")
        return payload

    def _validate_schema_node(
        self,
        *,
        value: Any,
        schema: dict[str, Any],
        path: str,
        issues: list[SemanticValidationIssue],
        source: str,
    ) -> None:
        expected_type = schema.get("type")
        if expected_type is not None:
            if not _matches_type(value, expected_type):
                issues.append(
                    SemanticValidationIssue(
                        source=source,
                        level="error",
                        code="type_mismatch",
                        path=path or "$",
                        message=(
                            f"{path or 'root'} must be {expected_type}, "
                            f"got {_value_type_name(value)}"
                        ),
                    )
                )
                return

        type_names = (
            set(expected_type)
            if isinstance(expected_type, list)
            else {expected_type}
            if expected_type is not None
            else set()
        )

        if "enum" in schema and value not in schema["enum"]:
            issues.append(
                SemanticValidationIssue(
                    source=source,
                    level="error",
                    code="enum_mismatch",
                    path=path or "$",
                    message=(
                        f"{path or 'root'} must be one of {schema['enum']}, got {value!r}"
                    ),
                )
            )
            return

        if "const" in schema and value != schema["const"]:
            issues.append(
                SemanticValidationIssue(
                    source=source,
                    level="error",
                    code="const_mismatch",
                    path=path or "$",
                    message=f"{path or 'root'} must be {schema['const']!r}",
                )
            )
            return

        if "string" in type_names and isinstance(value, str):
            min_length = schema.get("minLength")
            if isinstance(min_length, int) and len(value) < min_length:
                issues.append(
                    SemanticValidationIssue(
                        source=source,
                        level="error",
                        code="string_too_short",
                        path=path or "$",
                        message=f"{path or 'root'} must contain at least {min_length} characters",
                    )
                )
            return

        if type_names.intersection({"number", "integer"}) and isinstance(value, (int, float)) and not isinstance(value, bool):
            minimum = schema.get("minimum")
            if minimum is not None and value < minimum:
                issues.append(
                    SemanticValidationIssue(
                        source=source,
                        level="error",
                        code="minimum",
                        path=path or "$",
                        message=f"{path or 'root'} must be >= {minimum}",
                    )
                )
            exclusive_minimum = schema.get("exclusiveMinimum")
            if exclusive_minimum is not None and value <= exclusive_minimum:
                issues.append(
                    SemanticValidationIssue(
                        source=source,
                        level="error",
                        code="exclusive_minimum",
                        path=path or "$",
                        message=f"{path or 'root'} must be > {exclusive_minimum}",
                    )
                )
            return

        if "array" in type_names and isinstance(value, list):
            item_schema = schema.get("items")
            if isinstance(item_schema, dict):
                for index, item in enumerate(value):
                    self._validate_schema_node(
                        value=item,
                        schema=item_schema,
                        path=_child_path(path, f"[{index}]"),
                        issues=issues,
                        source=source,
                    )
            return

        if "object" not in type_names or not isinstance(value, dict):
            return

        properties = schema.get("properties", {})
        required = schema.get("required", [])
        for name in required:
            if name not in value:
                issues.append(
                    SemanticValidationIssue(
                        source=source,
                        level="error",
                        code="missing_required",
                        path=_child_path(path, name),
                        message=f"{_child_path(path, name)} is required",
                    )
                )

        additional_allowed = schema.get("additionalProperties", True)
        for key, child in value.items():
            child_schema = properties.get(key)
            child_path = _child_path(path, key)
            if isinstance(child_schema, dict):
                self._validate_schema_node(
                    value=child,
                    schema=child_schema,
                    path=child_path,
                    issues=issues,
                    source=source,
                )
            elif additional_allowed is False:
                issues.append(
                    SemanticValidationIssue(
                        source=source,
                        level="warning",
                        code="unknown_property",
                        path=child_path,
                        message=f"{child_path} is not declared in {source}",
                    )
                )

    def _validate_common_semantics(
        self,
        config: dict[str, Any],
        issues: list[SemanticValidationIssue],
    ) -> None:
        entry = _mapping(config.get("entry"))
        risk = _mapping(config.get("risk"))
        trigger_price = _first_number(entry.get("trigger_price"), entry.get("entry_price"))
        limit_price = _first_number(
            entry.get("maximum_limit_price"),
            entry.get("limit_price"),
        )
        if (
            trigger_price is not None
            and limit_price is not None
            and limit_price < trigger_price
        ):
            issues.append(
                SemanticValidationIssue(
                    source="semantic",
                    level="error",
                    code="limit_below_trigger",
                    path="entry.maximum_limit_price",
                    message=(
                        "entry.maximum_limit_price must be greater than or equal to "
                        "entry.trigger_price"
                    ),
                )
            )

        max_position = _first_number(risk.get("max_position_amount_usd"))
        max_risk = _first_number(risk.get("max_risk_usd"))
        if (
            max_position is not None
            and max_risk is not None
            and max_risk > max_position
        ):
            issues.append(
                SemanticValidationIssue(
                    source="semantic",
                    level="warning",
                    code="risk_above_position_budget",
                    path="risk.max_risk_usd",
                    message=(
                        "risk.max_risk_usd is above risk.max_position_amount_usd; "
                        "verify the capital and risk budget."
                    ),
                )
            )

        self._validate_trailing_stop_required(config, issues)

        targets = config.get("targets")
        if isinstance(targets, list):
            for index, item in enumerate(targets):
                target = _mapping(item)
                zone_min = _first_number(target.get("zone_min"))
                zone_max = _first_number(target.get("zone_max"))
                if (
                    zone_min is not None
                    and zone_max is not None
                    and zone_min > zone_max
                ):
                    issues.append(
                        SemanticValidationIssue(
                            source="semantic",
                            level="error",
                            code="reversed_target_zone",
                            path=f"targets[{index}]",
                            message=(
                                f"targets[{index}] has zone_min above zone_max"
                            ),
                        )
                    )

    def _validate_trailing_stop_required(
        self,
        config: dict[str, Any],
        issues: list[SemanticValidationIssue],
    ) -> None:
        trailing = config.get("trailing_stop_loss")
        entry = _mapping(config.get("entry"))
        role = str(config.get("setup_role") or "").strip().upper()
        entry_enabled = entry.get("enabled", True) is not False

        if not isinstance(trailing, dict):
            issues.append(
                SemanticValidationIssue(
                    source="semantic",
                    level="error",
                    code="TRAILING_STOP_LOSS_SECTION_MISSING",
                    path="trailing_stop_loss",
                    message="TRAILING_STOP_LOSS_SECTION_MISSING",
                )
            )
            return

        if trailing.get("enabled") is not True:
            issues.append(
                SemanticValidationIssue(
                    source="semantic",
                    level="error",
                    code="TRAILING_STOP_LOSS_REQUIRED",
                    path="trailing_stop_loss.enabled",
                    message="TRAILING_STOP_LOSS_REQUIRED",
                )
            )

        initial_stop = _first_number(trailing.get("initial_stop"))
        if initial_stop is None:
            issues.append(
                SemanticValidationIssue(
                    source="semantic",
                    level="error",
                    code="TRAILING_STOP_INITIAL_STOP_REQUIRED_BEFORE_ARMING",
                    path="trailing_stop_loss.initial_stop",
                    message="trailing_stop_loss.initial_stop is required before arming",
                )
            )

        if entry_enabled:
            broker_order = _mapping(trailing.get("broker_order"))
            if broker_order.get("required_before_entry_transmission") is not True:
                issues.append(
                    SemanticValidationIssue(
                        source="semantic",
                        level="error",
                        code="TRAILING_STOP_BROKER_ORDER_REQUIRED",
                        path="trailing_stop_loss.broker_order.required_before_entry_transmission",
                        message=(
                            "trailing_stop_loss.broker_order.required_before_entry_transmission "
                            "must be true before arming"
                        ),
                    )
                )

        if trailing.get("never_lower_stop") is not True:
            issues.append(
                SemanticValidationIssue(
                    source="semantic",
                    level="error",
                    code="TRAILING_STOP_NEVER_LOWER_REQUIRED",
                    path="trailing_stop_loss.never_lower_stop",
                    message="trailing_stop_loss.never_lower_stop must be true",
                )
            )

    def _validate_setup_specific_semantics(
        self,
        setup_type: str,
        config: dict[str, Any],
        issues: list[SemanticValidationIssue],
    ) -> None:
        if setup_type == "breakout_retest":
            breakout = _mapping(config.get("breakout"))
            retest = _mapping(config.get("retest"))
            daily_level = _first_number(breakout.get("daily_close_above"))
            zone_min = _first_number(retest.get("zone_min"))
            zone_max = _first_number(retest.get("zone_max"))
            if zone_min is not None and zone_max is not None and zone_min > zone_max:
                issues.append(
                    SemanticValidationIssue(
                        source="semantic",
                        level="error",
                        code="reversed_retest_zone",
                        path="retest",
                        message="retest.zone_min must be less than or equal to retest.zone_max",
                    )
                )
            if (
                daily_level is not None
                and zone_max is not None
                and daily_level < zone_max
            ):
                issues.append(
                    SemanticValidationIssue(
                        source="semantic",
                        level="warning",
                        code="breakout_below_retest_ceiling",
                        path="breakout.daily_close_above",
                        message=(
                            "breakout.daily_close_above is below retest.zone_max; "
                            "verify the breakout threshold and retest zone."
                        ),
                    )
                )
            return

        if setup_type == "aggressive_rebound":
            support = _mapping(config.get("support_zone"))
            zone_min = _first_number(support.get("min"))
            zone_max = _first_number(support.get("max"))
            if zone_min is not None and zone_max is not None and zone_min > zone_max:
                issues.append(
                    SemanticValidationIssue(
                        source="semantic",
                        level="error",
                        code="reversed_support_zone",
                        path="support_zone",
                        message="support_zone.min must be less than or equal to support_zone.max",
                    )
                )
            return

        if setup_type == "momentum_breakout":
            breakout = _mapping(config.get("breakout"))
            missed_breakout = _mapping(config.get("missed_breakout"))
            entry = _mapping(config.get("entry"))
            resistance = _first_number(breakout.get("resistance"))
            zone_min = _first_number(missed_breakout.get("retest_zone_min"))
            zone_max = _first_number(missed_breakout.get("retest_zone_max"))
            max_limit = _first_number(
                entry.get("maximum_limit_price"),
                entry.get("limit_price"),
            )
            if zone_min is not None and zone_max is not None and zone_min > zone_max:
                issues.append(
                    SemanticValidationIssue(
                        source="semantic",
                        level="error",
                        code="reversed_missed_breakout_zone",
                        path="missed_breakout",
                        message=(
                            "missed_breakout.retest_zone_min must be less than or equal to "
                            "missed_breakout.retest_zone_max"
                        ),
                    )
                )
            if (
                resistance is not None
                and max_limit is not None
                and max_limit < resistance
            ):
                issues.append(
                    SemanticValidationIssue(
                        source="semantic",
                        level="error",
                        code="momentum_limit_below_resistance",
                        path="entry.maximum_limit_price",
                        message=(
                            "entry.maximum_limit_price must not be below breakout.resistance "
                            "for a momentum breakout setup."
                        ),
                    )
                )
            return

        if setup_type == "range_breakout":
            range_config = _mapping(config.get("range"))
            low = _first_number(range_config.get("low"))
            high = _first_number(range_config.get("high"))
            if low is not None and high is not None and low >= high:
                issues.append(
                    SemanticValidationIssue(
                        source="semantic",
                        level="error",
                        code="invalid_range_bounds",
                        path="range",
                        message="range.low must be less than range.high",
                    )
                )
            return

        if setup_type in {"runner", "trailing_runner"}:
            stop_management = _mapping(_mapping(config.get("management")).get("stop_management"))
            steps = stop_management.get("steps")
            if not isinstance(steps, list) or not steps:
                issues.append(
                    SemanticValidationIssue(
                        source="semantic",
                        level="warning",
                        code="runner_without_stop_steps",
                        path="management.stop_management.steps",
                        message=f"{setup_type} setup has no stop management steps yet.",
                    )
                )
            return

        if setup_type == "position_management":
            management = _mapping(config.get("management"))
            stop_management = _mapping(management.get("stop_management"))
            rules = stop_management.get("rules")
            steps = stop_management.get("steps")
            mode = str(stop_management.get("mode") or "").strip().lower()
            has_rules = isinstance(rules, list) and bool(rules)
            has_steps = isinstance(steps, list) and bool(steps)
            if not has_rules and not has_steps and mode != "structure_based_trailing":
                issues.append(
                    SemanticValidationIssue(
                        source="semantic",
                        level="warning",
                        code="management_without_rules",
                        path="management.stop_management",
                        message=(
                            "Position management setup has no stop rules yet; "
                            "it can reconcile a position but will not tighten the stop."
                        ),
                    )
                )


def _default_schema_dir() -> Path:
    return Path(__file__).resolve().parents[2] / "config" / "schemas"


def _mapping(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _with_legacy_trailing_stop(config: dict[str, Any]) -> dict[str, Any]:
    trailing = config.get("trailing_stop_loss")
    if isinstance(trailing, dict):
        return config
    risk = config.get("risk")
    if not isinstance(risk, dict):
        return config
    legacy_stop = risk.get("initial_stop_loss", risk.get("protective_stop"))
    if legacy_stop is None:
        return config
    migrated = deepcopy(config)
    migrated_risk = migrated.get("risk", {})
    if isinstance(migrated_risk, dict):
        migrated_risk.pop("initial_stop_loss", None)
        migrated_risk.pop("protective_stop", None)
        migrated_risk.pop("never_lower_stop", None)
        migrated_risk.pop("trailing_stop_loss", None)
    migrated["trailing_stop_loss"] = {
        "enabled": True,
        "mode": "AUTO_INTELLIGENT",
        "initial_stop": legacy_stop,
        "current_stop": legacy_stop,
        "never_lower_stop": True,
        "stop_source": "MIGRATED_FROM_LEGACY_STOP",
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
            "min_tick_buffer": 2,
            "spread_buffer_multiplier": 2,
        },
        "ratchet_rules": {
            "move_only_up_for_long": True,
            "move_only_down_for_short": True,
            "update_on_closed_bar_only": True,
            "timeframe": "15m",
            "min_improvement_required": "AUTO",
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
    }
    return migrated


def _first_number(*values: Any) -> float | None:
    for value in values:
        if value in (None, ""):
            continue
        if isinstance(value, bool):
            continue
        try:
            return float(value)
        except (TypeError, ValueError):
            continue
    return None


def _child_path(parent: str, child: str) -> str:
    if child.startswith("["):
        return f"{parent}{child}" if parent else child
    return f"{parent}.{child}" if parent else child


def _matches_type(value: Any, expected_type: Any) -> bool:
    if isinstance(expected_type, list):
        return any(_matches_type(value, item) for item in expected_type)
    if expected_type == "object":
        return isinstance(value, dict)
    if expected_type == "array":
        return isinstance(value, list)
    if expected_type == "string":
        return isinstance(value, str)
    if expected_type == "boolean":
        return isinstance(value, bool)
    if expected_type == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if expected_type == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if expected_type == "null":
        return value is None
    return True


def _value_type_name(value: Any) -> str:
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, dict):
        return "object"
    if isinstance(value, list):
        return "array"
    if isinstance(value, str):
        return "string"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "number"
    if value is None:
        return "null"
    return type(value).__name__


def _dedupe_issues(
    issues: list[SemanticValidationIssue],
) -> list[SemanticValidationIssue]:
    seen: set[tuple[str, str, str, str]] = set()
    unique: list[SemanticValidationIssue] = []
    for issue in issues:
        key = (issue.level, issue.code, issue.path, issue.message)
        if key in seen:
            continue
        seen.add(key)
        unique.append(issue)
    return unique
