from __future__ import annotations

import json
import re
from collections.abc import Callable
from contextlib import suppress
from typing import Any

from app.models import utc_now_iso
from app.opportunity_scanner.rule_interpreter import (
    describe_rule,
    parse_rule,
    validate_rule_structure,
)
from app.opportunity_scanner.schemas import (
    TechniqueCreateRequest,
    TechniquePatchRequest,
    TechniqueResponse,
    TechniqueStats,
)
from app.opportunity_scanner.technique_repository import TechniqueRepository

# Injected by P2-a outcome tracking; returns per-technique stats keyed by
# technique_id. Absent -> every technique reports empty stats.
StatsProvider = Callable[[], dict[str, dict[str, Any]]]
# Injected by P2-a; returns the recorded outcome history for one technique.
OutcomesProvider = Callable[[str], list[dict[str, Any]]]
# Injected by P2-b bis; persists human feedback, returns False if the outcome
# is unknown.
FeedbackRecorder = Callable[[str, str], bool]

_SLUG_RE = re.compile(r"[^a-z0-9]+")


class TechniqueNotFoundError(KeyError):
    """Raised when a technique_id does not exist."""


class OutcomeNotFoundError(KeyError):
    """Raised when an outcome_id does not exist."""


class InvalidRuleError(ValueError):
    """Raised when a rule_json fails whitelist validation (maps to HTTP 400)."""


class TechniqueService:
    """Business logic for the detection technique library (CRUD + stats).

    Routers stay thin: they translate these exceptions into HTTP status codes.
    All rule validation happens here so an invalid rule is rejected at write
    time (400), never silently at evaluation time.
    """

    def __init__(
        self,
        repository: TechniqueRepository,
        stats_provider: StatsProvider | None = None,
        outcomes_provider: OutcomesProvider | None = None,
        feedback_recorder: FeedbackRecorder | None = None,
        event_store: Any | None = None,
    ) -> None:
        self.repository = repository
        self.stats_provider = stats_provider
        self.outcomes_provider = outcomes_provider
        self.feedback_recorder = feedback_recorder
        self.event_store = event_store

    def list_techniques(self) -> list[dict[str, Any]]:
        stats = self._stats()
        return [self._to_response(row, stats) for row in self.repository.list_all()]

    def get_technique(self, technique_id: str) -> dict[str, Any]:
        row = self._require(technique_id)
        return self._to_response(row, self._stats())

    def create_technique(self, request: TechniqueCreateRequest) -> dict[str, Any]:
        rule_json = self._build_rule_json(request.rule, request.opportunity_type)
        technique_id = self._unique_id(request.name)
        now = utc_now_iso()
        inserted = self.repository.insert_if_absent(
            {
                "technique_id": technique_id,
                "name": request.name,
                "description": request.description,
                "rule_json": rule_json,
                "enabled": request.enabled,
                "origin": "manual",
                "parent_id": None,
                "status": "ACTIVE",
                "created_at": now,
                "updated_at": now,
            }
        )
        if not inserted:
            raise InvalidRuleError(f"technique_id already exists: {technique_id}")
        return self.get_technique(technique_id)

    def update_technique(self, technique_id: str, patch: TechniquePatchRequest) -> dict[str, Any]:
        existing = self._require(technique_id)
        fields: dict[str, Any] = {}
        rule_before = str(existing.get("rule_json") or "")
        rule_after: str | None = None
        if patch.rule is not None or patch.opportunity_type is not None:
            current = parse_rule(existing.get("rule_json")) or {}
            rule = patch.rule if patch.rule is not None else _strip_type(current)
            opportunity_type = (
                patch.opportunity_type
                if patch.opportunity_type is not None
                else _opportunity_type_of(current)
            )
            rule_after = self._build_rule_json(rule, opportunity_type)
            fields["rule_json"] = rule_after
        if patch.name is not None:
            fields["name"] = patch.name
        if patch.description is not None:
            fields["description"] = patch.description
        if patch.enabled is not None:
            fields["enabled"] = 1 if patch.enabled else 0
        if fields:
            now = utc_now_iso()
            fields["updated_at"] = now
            self.repository.update_fields(technique_id, fields)
            # Versioning (skills.md 30bis): only a change to the RULE bumps the
            # revision — a rename or an enabled toggle does not version. Every
            # rule change is auditable in decision_traces so a past decision can
            # be replayed against the rule of its epoch.
            if rule_after is not None and rule_after != rule_before:
                self._version_rule_change(technique_id, existing, rule_before, rule_after)
        return self.get_technique(technique_id)

    def _version_rule_change(
        self,
        technique_id: str,
        existing: dict[str, Any],
        rule_before: str,
        rule_after: str,
    ) -> None:
        revision_from = int(existing.get("revision") or 1)
        revision_to = self.repository.bump_revision(technique_id, updated_at=utc_now_iso())
        if revision_to is None:
            revision_to = revision_from + 1
        if self.event_store is None:
            return
        with suppress(Exception):
            self.event_store.record_decision_trace(
                decision_type="TECHNIQUE_REVISION",
                final_decision=f"REVISION_{revision_from}_TO_{revision_to}",
                trace={
                    "technique_id": technique_id,
                    "revision_from": revision_from,
                    "revision_to": revision_to,
                    "rule_before": parse_rule(rule_before) or {},
                    "rule_after": parse_rule(rule_after) or {},
                },
            )

    def retire_technique(self, technique_id: str) -> dict[str, Any]:
        existing = self._require(technique_id)
        now = utc_now_iso()
        if existing.get("origin") == "builtin":
            # Builtins are never RETIRED - they can only be disabled, so a future
            # release can re-enable them and the seed stays authoritative.
            self.repository.update_fields(technique_id, {"enabled": 0, "updated_at": now})
        else:
            self.repository.retire(technique_id, updated_at=now)
        return self.get_technique(technique_id)

    def list_outcomes(self, technique_id: str) -> list[dict[str, Any]]:
        # 404 if the technique is unknown; empty history until outcomes exist.
        self._require(technique_id)
        if self.outcomes_provider is None:
            return []
        return self.outcomes_provider(technique_id)

    def set_outcome_feedback(self, outcome_id: str, feedback: str) -> dict[str, Any]:
        """Persist human feedback on a detection outcome. 404 if unknown."""
        if self.feedback_recorder is None or not self.feedback_recorder(outcome_id, feedback):
            raise OutcomeNotFoundError(outcome_id)
        return {"ok": True, "outcome_id": outcome_id, "human_feedback": feedback}

    def _require(self, technique_id: str) -> dict[str, Any]:
        row = self.repository.get(technique_id)
        if row is None:
            raise TechniqueNotFoundError(technique_id)
        return row

    def _build_rule_json(self, rule: Any, opportunity_type: str | None) -> str:
        if not isinstance(rule, dict):
            raise InvalidRuleError("rule must be a JSON object")
        merged: dict[str, Any] = {**_strip_type(rule)}
        if opportunity_type:
            merged["opportunity_type"] = opportunity_type
        errors = validate_rule_structure(merged)
        if errors:
            raise InvalidRuleError("; ".join(errors))
        return json.dumps(merged)

    def _unique_id(self, name: str) -> str:
        base = _SLUG_RE.sub("_", name.strip().lower()).strip("_") or "technique"
        candidate = f"manual_{base}"
        suffix = 2
        while self.repository.get(candidate) is not None:
            candidate = f"manual_{base}_{suffix}"
            suffix += 1
        return candidate

    def _stats(self) -> dict[str, dict[str, Any]]:
        if self.stats_provider is None:
            return {}
        return self.stats_provider()

    def _to_response(self, row: dict[str, Any], stats: dict[str, dict[str, Any]]) -> dict[str, Any]:
        rule = parse_rule(row.get("rule_json")) or {}
        technique_id = str(row.get("technique_id"))
        raw_stats = stats.get(technique_id, {})
        return TechniqueResponse(
            technique_id=technique_id,
            name=str(row.get("name") or ""),
            description=str(row.get("description") or ""),
            rule=rule,
            rule_summary=describe_rule(rule),
            opportunity_type=_opportunity_type_of(rule),
            enabled=bool(row.get("enabled")),
            origin=str(row.get("origin") or ""),
            parent_id=row.get("parent_id"),
            status=str(row.get("status") or ""),
            config_version=str(row.get("config_version") or "1"),
            revision=int(row.get("revision") or 1),
            created_at=str(row.get("created_at") or ""),
            updated_at=str(row.get("updated_at") or ""),
            stats=TechniqueStats(**raw_stats) if raw_stats else TechniqueStats(),
        ).model_dump()


def _strip_type(rule: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in rule.items() if key != "opportunity_type"}


def _opportunity_type_of(rule: dict[str, Any]) -> str | None:
    value = rule.get("opportunity_type")
    return value if isinstance(value, str) and value else None
