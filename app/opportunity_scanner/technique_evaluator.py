from __future__ import annotations

from typing import Any

from app.opportunity_scanner.rule_interpreter import evaluate_rule, parse_rule


class TechniqueEvaluator:
    """Evaluates the active technique library against a market context snapshot."""

    def evaluate(
        self,
        techniques: list[dict[str, Any]],
        snapshot: dict[str, Any],
    ) -> tuple[list[str], dict[str, str]]:
        """Return (matched opportunity types, {opportunity_type: technique_id})."""
        types: list[str] = []
        detected_by: dict[str, str] = {}
        for technique in techniques:
            opportunity_type = _opportunity_type(technique)
            if opportunity_type is None:
                continue
            if not evaluate_rule(technique.get("rule_json"), snapshot):
                continue
            detected_by.setdefault(opportunity_type, str(technique["technique_id"]))
            if opportunity_type not in types:
                types.append(opportunity_type)
        return types, detected_by

    def matched_technique_ids(
        self,
        techniques: list[dict[str, Any]],
        snapshot: dict[str, Any],
    ) -> list[str]:
        """Every technique whose rule matches, not deduplicated by type.

        Outcome tracking needs all matches (including CANDIDATE variants that
        share an opportunity type with their parent), whereas `evaluate` keeps
        one winner per type for the scanner's opportunity output.
        """
        matched: list[str] = []
        for technique in techniques:
            if _opportunity_type(technique) is None:
                continue
            if evaluate_rule(technique.get("rule_json"), snapshot):
                matched.append(str(technique["technique_id"]))
        return matched


def _opportunity_type(technique: dict[str, Any]) -> str | None:
    parsed = parse_rule(technique.get("rule_json"))
    if parsed is None:
        return None
    opportunity_type = parsed.get("opportunity_type")
    if isinstance(opportunity_type, str) and opportunity_type:
        return opportunity_type
    return None
