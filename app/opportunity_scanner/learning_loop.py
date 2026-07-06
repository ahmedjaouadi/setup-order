from __future__ import annotations

import json
from collections.abc import Callable
from contextlib import suppress
from copy import deepcopy
from datetime import UTC, datetime
from typing import Any

from app.models import utc_now_iso
from app.opportunity_scanner.rule_interpreter import parse_rule
from app.opportunity_scanner.technique_repository import TechniqueRepository
from app.utils.market_hours import classify_us_equity_session

StatsProvider = Callable[[], dict[str, dict[str, Any]]]

DEFAULT_MAX_ACTIVE = 20
DEFAULT_MIN_SAMPLES = 30
DEFAULT_VARIANT_FACTORS: tuple[float, ...] = (0.8, 1.2)
DEFAULT_MAX_VARIANTS_PER_PARENT = 4

# Only these comparison operators carry a single scalar numeric threshold that a
# one-parameter-at-a-time mutation can tune (skills.md 32.2ter). `==` (string or
# boolean equality) and `between` (two bounds) are deliberately left alone.
_MUTABLE_OPS: frozenset[str] = frozenset({">=", ">", "<=", "<"})


class LearningLoop:
    """Promotes, retires and spawns detection techniques from measured stats.

    Every automatic mutation is gated by the kill-switch
    (`opportunity_scanner.learning.enabled`, default false), only runs in RTH,
    and is traced in `decision_traces`. Decisions key off `expectancy_r`, never
    the raw forward return. Builtins are never retired, only disabled.
    """

    def __init__(
        self,
        repository: TechniqueRepository,
        stats_provider: StatsProvider,
        *,
        event_store: Any | None = None,
        settings: dict[str, Any] | None = None,
    ) -> None:
        self.repository = repository
        self.stats_provider = stats_provider
        self.event_store = event_store
        config = _learning_config(settings)
        self.enabled = bool(config.get("enabled", False))
        self.max_active = int(config.get("max_active", DEFAULT_MAX_ACTIVE))
        self.min_samples = int(config.get("min_samples", DEFAULT_MIN_SAMPLES))
        factors = config.get("variant_factors") or DEFAULT_VARIANT_FACTORS
        self.variant_factors = tuple(float(factor) for factor in factors)
        self.max_variants_per_parent = int(
            config.get("max_variants_per_parent", DEFAULT_MAX_VARIANTS_PER_PARENT)
        )

    def run(self, *, now: datetime | None = None) -> dict[str, Any]:
        if not self.enabled:
            return {"enabled": False, "decisions": []}
        moment = now or datetime.now(UTC)
        if classify_us_equity_session(moment) != "RTH":
            return {"enabled": True, "skipped": "outside_rth", "decisions": []}

        stats = self.stats_provider() or {}
        iso = utc_now_iso()
        decisions: list[dict[str, Any]] = []

        techniques = self.repository.list_all()
        active_count = sum(1 for row in techniques if row["status"] == "ACTIVE" and row["enabled"])

        # Phase 1: resolve CANDIDATE variants (promote if better than parent, else retire).
        for tech in techniques:
            if tech["status"] != "CANDIDATE" or not tech["enabled"]:
                continue
            stat = stats.get(tech["technique_id"])
            if not _ready(stat, self.min_samples):
                continue
            expectancy = _number(stat.get("expectancy_r")) if stat else None
            parent_stat = stats.get(str(tech.get("parent_id") or ""))
            parent_expectancy = _number(parent_stat.get("expectancy_r")) if parent_stat else None
            better = expectancy is not None and (
                parent_expectancy is None or expectancy > parent_expectancy
            )
            details = {"expectancy_r": expectancy, "parent_expectancy_r": parent_expectancy}
            if better and active_count < self.max_active:
                self.repository.update_fields(
                    tech["technique_id"],
                    {"status": "ACTIVE", "origin": "learned", "enabled": 1, "updated_at": iso},
                )
                active_count += 1
                decisions.append(self._trace("PROMOTED", tech, details))
            else:
                reason = "capped" if better else "not_better_than_parent"
                self.repository.retire(tech["technique_id"], updated_at=iso)
                decisions.append(
                    self._trace("CANDIDATE_RETIRED", tech, {**details, "reason": reason})
                )

        # Phase 2: evaluate ACTIVE techniques against a fresh snapshot.
        techniques = self.repository.list_all()
        children = _candidate_children(techniques)
        for tech in techniques:
            if tech["status"] != "ACTIVE" or not tech["enabled"]:
                continue
            stat = stats.get(tech["technique_id"])
            if not _ready(stat, self.min_samples):
                continue
            expectancy = _number(stat.get("expectancy_r")) if stat else None
            if expectancy is None:
                continue
            if expectancy < 0:
                if tech["origin"] == "builtin":
                    # Builtins are indelible: disable only, never RETIRED.
                    self.repository.update_fields(
                        tech["technique_id"], {"enabled": 0, "updated_at": iso}
                    )
                    decisions.append(
                        self._trace("BUILTIN_DISABLED", tech, {"expectancy_r": expectancy})
                    )
                else:
                    self.repository.retire(tech["technique_id"], updated_at=iso)
                    decisions.append(self._trace("RETIRED", tech, {"expectancy_r": expectancy}))
                active_count -= 1
            elif expectancy > 0 and not children.get(tech["technique_id"]):
                for variant in self._spawn_variants(tech, iso):
                    decisions.append(
                        self._trace(
                            "VARIANT_SPAWNED",
                            variant,
                            {
                                "parent_id": tech["technique_id"],
                                "expectancy_r": expectancy,
                                "mutated_field": variant.get("mutated_field"),
                                "factor": variant.get("factor"),
                            },
                        )
                    )
        return {"enabled": True, "decisions": decisions}

    def _spawn_variants(self, parent: dict[str, Any], iso: str) -> list[dict[str, Any]]:
        """Spawn one-parameter-at-a-time variants of a parent (skills.md 32.2ter).

        Each variant mutates exactly ONE numeric leaf threshold of the parent
        rule by one factor, so a later promotion/retirement can attribute the
        effect to that single change. Leaves are taken in rule order and the
        number of variants created is capped by `max_variants_per_parent`.
        """
        parent_rule = parse_rule(parent.get("rule_json")) or {}
        leaves = _numeric_leaves(parent_rule)
        if not leaves:
            return []
        parent_id = parent["technique_id"]
        parent_name = str(parent.get("name") or parent_id)
        created: list[dict[str, Any]] = []
        for index, (field, _value) in enumerate(leaves):
            for factor in self.variant_factors:
                if len(created) >= self.max_variants_per_parent:
                    return created
                tag = _factor_tag(factor)
                variant_id = self._unique_id(f"{parent_id}_{field}_{tag}")
                mutated_rule = _mutate_nth_leaf(parent_rule, index, factor)
                inserted = self.repository.insert_if_absent(
                    {
                        "technique_id": variant_id,
                        "name": f"{parent_name} ({field} {tag})",
                        "description": (
                            f"Mutation de {field} ×{factor} (skills.md §32.2ter — "
                            f"un paramètre à la fois). Variante de {parent_name}."
                        ),
                        "rule_json": json.dumps(mutated_rule),
                        "enabled": True,
                        "origin": "learned",
                        "parent_id": parent_id,
                        "status": "CANDIDATE",
                        "created_at": iso,
                        "updated_at": iso,
                    }
                )
                if inserted:
                    created.append(
                        {
                            "technique_id": variant_id,
                            "name": variant_id,
                            "mutated_field": field,
                            "factor": factor,
                        }
                    )
        return created

    def _unique_id(self, base: str) -> str:
        candidate = base
        suffix = 2
        while self.repository.get(candidate) is not None:
            candidate = f"{base}_{suffix}"
            suffix += 1
        return candidate

    def _trace(self, action: str, tech: dict[str, Any], details: dict[str, Any]) -> dict[str, Any]:
        decision = {"action": action, "technique_id": tech["technique_id"], **details}
        if self.event_store is not None:
            with suppress(Exception):
                self.event_store.record_decision_trace(
                    decision_type="TECHNIQUE_LEARNING",
                    final_decision=action,
                    trace={"technique_id": tech["technique_id"], **details},
                )
        return decision


def _numeric_leaves(rule: dict[str, Any]) -> list[tuple[str, float]]:
    """Return the (field, value) of each mutable numeric leaf, in rule order."""
    leaves: list[tuple[str, float]] = []
    _walk_leaves(rule, leaves)
    return leaves


def _walk_leaves(node: Any, leaves: list[tuple[str, float]]) -> None:
    if not isinstance(node, dict):
        return
    for combinator in ("all", "any"):
        children = node.get(combinator)
        if isinstance(children, list):
            for child in children:
                _walk_leaves(child, leaves)
            return
    field = node.get("field")
    op = node.get("op")
    number = _leaf_number(node.get("value"))
    if isinstance(field, str) and op in _MUTABLE_OPS and number is not None:
        leaves.append((field, number))


def _mutate_nth_leaf(rule: dict[str, Any], index: int, factor: float) -> dict[str, Any]:
    """Deep-copy `rule`, scaling only the `index`-th mutable numeric leaf."""
    copy = deepcopy(rule)
    _apply_leaf_mutation(copy, index, factor, [0])
    return copy


def _apply_leaf_mutation(node: Any, index: int, factor: float, counter: list[int]) -> None:
    if not isinstance(node, dict):
        return
    for combinator in ("all", "any"):
        children = node.get(combinator)
        if isinstance(children, list):
            for child in children:
                _apply_leaf_mutation(child, index, factor, counter)
            return
    field = node.get("field")
    op = node.get("op")
    number = _leaf_number(node.get("value"))
    if isinstance(field, str) and op in _MUTABLE_OPS and number is not None:
        if counter[0] == index:
            node["value"] = round(number * factor, 6)
        counter[0] += 1


def _factor_tag(factor: float) -> str:
    """Compact id/name tag for a mutation factor: 1.2 -> ``p20``, 0.8 -> ``m20``."""
    pct = int(round(abs(1 - factor) * 100))
    return f"{'p' if factor >= 1 else 'm'}{pct}"


def _leaf_number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def scale_rule(node: Any, factor: float) -> Any:
    """Return a copy of a rule with every numeric threshold scaled by `factor`.

    Retained as a pure, tested helper; the learning loop no longer uses it in
    production (variants mutate a single leaf, see `_spawn_variants`).
    """
    if not isinstance(node, dict):
        return node
    result: dict[str, Any] = {}
    for key, value in node.items():
        if key in ("all", "any") and isinstance(value, list):
            result[key] = [scale_rule(child, factor) for child in value]
        elif key == "value":
            result[key] = _scale_value(value, factor)
        else:
            result[key] = value
    return result


def _scale_value(value: Any, factor: float) -> Any:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return round(value * factor, 6)
    if isinstance(value, list):
        return [_scale_value(item, factor) for item in value]
    return value


def _candidate_children(techniques: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    children: dict[str, list[dict[str, Any]]] = {}
    for tech in techniques:
        parent_id = tech.get("parent_id")
        if parent_id and tech["status"] == "CANDIDATE":
            children.setdefault(str(parent_id), []).append(tech)
    return children


def _learning_config(settings: dict[str, Any] | None) -> dict[str, Any]:
    scanner = (settings or {}).get("opportunity_scanner", {})
    config = scanner.get("learning", {}) if isinstance(scanner, dict) else {}
    return config if isinstance(config, dict) else {}


def _ready(stat: dict[str, Any] | None, min_samples: int) -> bool:
    return bool(stat) and int((stat or {}).get("sample_size", 0)) >= min_samples


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
