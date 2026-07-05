from __future__ import annotations

import json
from typing import Any

# Canonical field name -> raw/alias keys to try in the snapshot, in priority order.
# Mirrors the alias resolution `detectors.py` performs today, so seeding the 7
# builtin rules on top of this interpreter cannot change matching behaviour.
ALIAS_GROUPS: dict[str, tuple[str, ...]] = {
    "perf_stock_1d": ("perf_stock_1d", "stock_perf_1d"),
    "perf_sector_1d": ("perf_sector_1d", "sector_perf_1d"),
    "perf_spy_1d": ("perf_spy_1d", "spy_perf_1d"),
    "rs_spy": ("rs_spy", "relative_strength_vs_spy"),
    "rs_sector": ("rs_sector", "relative_strength_vs_sector"),
    "volume_ratio": ("relative_volume", "volume_ratio", "volume_ratio_15m"),
    "gap_pct": ("gap_pct",),
    "breakout_proximity": ("breakout_proximity",),
    "new_intraday_high": ("new_intraday_high",),
    "spread_pct": ("spread_pct",),
}

ALLOWED_FIELDS: frozenset[str] = frozenset(ALIAS_GROUPS)
ALLOWED_OPERATORS: frozenset[str] = frozenset({">=", ">", "<=", "<", "==", "between"})
ALLOWED_COMBINATORS: frozenset[str] = frozenset({"all", "any"})


def evaluate_rule(rule: dict[str, Any] | str | None, snapshot: dict[str, Any]) -> bool:
    """Evaluate a whitelisted declarative rule against a snapshot.

    Never raises: an unknown field, a missing/None value, a malformed rule or an
    inverted `between` range are all treated as "does not match", not an error.
    """
    parsed = parse_rule(rule)
    if parsed is None or not isinstance(snapshot, dict):
        return False
    return _evaluate_node(parsed, snapshot)


def validate_rule_structure(rule: dict[str, Any] | str | None) -> list[str]:
    """Return a list of validation errors; empty means the rule is well-formed."""
    parsed = parse_rule(rule)
    if parsed is None:
        return ["rule must be a JSON object"]
    errors: list[str] = []
    _collect_errors(parsed, errors)
    return errors


def parse_rule(rule: dict[str, Any] | str | None) -> dict[str, Any] | None:
    if isinstance(rule, dict):
        return rule
    if isinstance(rule, str):
        try:
            parsed = json.loads(rule)
        except (TypeError, ValueError):
            return None
        return parsed if isinstance(parsed, dict) else None
    return None


def _evaluate_node(node: Any, snapshot: dict[str, Any]) -> bool:
    if not isinstance(node, dict):
        return False
    if "all" in node:
        return _evaluate_combinator(node.get("all"), snapshot, require_all=True)
    if "any" in node:
        return _evaluate_combinator(node.get("any"), snapshot, require_all=False)
    return _evaluate_condition(node, snapshot)


def _evaluate_combinator(
    children: Any,
    snapshot: dict[str, Any],
    *,
    require_all: bool,
) -> bool:
    if not isinstance(children, list) or not children:
        return False
    results = [_evaluate_node(child, snapshot) for child in children]
    return all(results) if require_all else any(results)


def _evaluate_condition(condition: dict[str, Any], snapshot: dict[str, Any]) -> bool:
    field = condition.get("field")
    op = condition.get("op")
    if not isinstance(field, str) or field not in ALLOWED_FIELDS:
        return False
    if not isinstance(op, str) or op not in ALLOWED_OPERATORS:
        return False
    actual_raw = _first_value(snapshot, *ALIAS_GROUPS[field])
    if actual_raw is None:
        return False
    expected = condition.get("value")
    if op == "between":
        return _evaluate_between(actual_raw, expected)
    if op == "==":
        return _evaluate_equals(actual_raw, expected)
    actual = _number(actual_raw)
    target = _number(expected)
    if actual is None or target is None:
        return False
    if op == ">=":
        return actual >= target
    if op == ">":
        return actual > target
    if op == "<=":
        return actual <= target
    return actual < target  # op == "<"


def _evaluate_between(actual_raw: Any, expected: Any) -> bool:
    actual = _number(actual_raw)
    if actual is None or not isinstance(expected, list | tuple) or len(expected) != 2:
        return False
    low = _number(expected[0])
    high = _number(expected[1])
    if low is None or high is None or low > high:
        return False
    return low <= actual <= high


def _evaluate_equals(actual_raw: Any, expected: Any) -> bool:
    if isinstance(actual_raw, bool) or isinstance(expected, bool):
        return bool(actual_raw) == bool(expected)
    actual = _number(actual_raw)
    target = _number(expected)
    if actual is None or target is None:
        return False
    return actual == target


def _collect_errors(node: Any, errors: list[str]) -> None:
    if not isinstance(node, dict):
        errors.append(f"rule node must be an object, got {type(node).__name__}")
        return
    combinators = [key for key in ("all", "any") if key in node]
    if combinators:
        for combinator in combinators:
            children = node.get(combinator)
            if not isinstance(children, list) or not children:
                errors.append(f"combinator '{combinator}' must be a non-empty list")
                continue
            for child in children:
                _collect_errors(child, errors)
        return
    if "field" not in node and "op" not in node:
        # Not a condition node either (e.g. a bare `opportunity_type` sibling key
        # at the root) - nothing to validate here.
        return
    field = node.get("field")
    op = node.get("op")
    if field not in ALLOWED_FIELDS:
        errors.append(f"field '{field}' is not whitelisted")
    if op not in ALLOWED_OPERATORS:
        errors.append(f"operator '{op}' is not whitelisted")
    if op == "between":
        value = node.get("value")
        if not isinstance(value, list | tuple) or len(value) != 2:
            errors.append("'between' requires a value of [low, high]")


def _first_value(payload: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        value = payload.get(key)
        if value not in (None, ""):
            return value
    return None


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
