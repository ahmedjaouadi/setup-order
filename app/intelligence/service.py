from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from typing import Any

from app.conversion import AliasResolver, canonicalize_setup_config, normalize_key
from app.conversion.canonical_field_registry import load_canonical_fields
from app.intelligence.provider import DisabledLLMProvider, LLMProvider
from app.intelligence.repository import IntelligenceRepository
from app.intelligence.semantic_validation_service import SemanticValidationService
from app.setups.setup_factory import SetupFactory, UnknownSetupTypeError
from app.setups.text_converter import (
    _detect_setup_type,
    _extract_breakout_level,
    _extract_labeled_number,
    _extract_ranges,
    _make_setup_id,
    _normalize,
    convert_text_to_setup,
)
from app.utils.id_generator import new_id

SCHEMA_VERSION = "intelligence_v1"
PARSER_VERSION = "intelligence_parser_v1"
CANONICAL_MAPPER_VERSION = "canonical_mapper_v1"
_MISSING = object()
_AMBIGUITY_PENALTIES = {
    "BLOCKER": 0.14,
    "REVIEW": 0.07,
    "INFO": 0.03,
}


class IntelligenceService:
    def __init__(
        self,
        repository: IntelligenceRepository,
        defaults: dict[str, Any],
        provider: LLMProvider | None = None,
    ) -> None:
        self.repository = repository
        self.defaults = defaults
        self.provider = provider or DisabledLLMProvider()
        self.semantic_validation = SemanticValidationService()
        self.canonical_fields = load_canonical_fields()
        self.alias_resolver = AliasResolver(self.canonical_fields)
        self.accepted_aliases = {
            field.canonical_path: list(dict.fromkeys([field.canonical_path, *field.aliases]))
            for field in self.canonical_fields
        }

    async def analyze(self, request: dict[str, Any], *, persist: bool = True) -> dict[str, Any]:
        prepared = self._prepare_analysis_input(request)
        if prepared["issues"] and not prepared["scenario_inputs"]:
            return self._build_error_result(
                prepared,
                prepared["issues"],
                save_allowed=False,
                arm_allowed=False,
                persisted=False,
                reused=False,
            )

        analysis_hash = self._analysis_hash(
            prepared["raw_input_text"],
            prepared["symbol"],
        )
        existing = None
        idempotency_key = prepared["idempotency_key"]
        if persist and idempotency_key:
            existing = self.repository.get_analysis_by_idempotency_key(idempotency_key)
        if persist and existing is None and not prepared["force_new_revision"]:
            existing = self.repository.get_latest_analysis_by_hash(analysis_hash)
        if persist and existing is not None:
            return self._load_persisted_analysis(existing["analysis_id"], reused=True)

        provider_name = prepared["provider_name_override"]
        if provider_name:
            provider_result = {"provider": provider_name}
        else:
            provider_result = await self.provider.analyze(
                {
                    "symbol": prepared["symbol"],
                    "raw_input_text": prepared["raw_input_text"],
                    "source_type": prepared["source_type"],
                    "request_id": prepared["request_id"],
                }
            )

        scenarios: list[dict[str, Any]] = []
        fields: list[dict[str, Any]] = []
        issues = list(prepared["issues"])
        ambiguities: list[dict[str, Any]] = []

        for index, scenario_input in enumerate(prepared["scenario_inputs"], start=1):
            scenario = self._analyze_scenario_input(
                scenario_input,
                analysis_id=prepared["analysis_id"],
                source_text=prepared["raw_input_text"],
                scenario_index=index,
            )
            scenarios.append(scenario["scenario"])
            fields.extend(scenario["fields"])
            issues.extend(scenario["issues"])

        if len(scenarios) > 1:
            ambiguities.append(
                self._scenario_selection_ambiguity(
                    analysis_id=prepared["analysis_id"],
                    scenarios=scenarios,
                )
            )
        ambiguities.extend(
            self._ambiguities_from_issues(
                analysis_id=prepared["analysis_id"],
                issues=issues,
                fields=fields,
            )
        )
        for explicit in prepared["explicit_ambiguities"]:
            ambiguities.append(self._explicit_ambiguity(prepared["analysis_id"], explicit))

        for scenario in scenarios:
            scenario_issues = [
                issue for issue in issues if issue.get("scenario_id") == scenario["scenario_id"]
            ]
            scenario_fields = [
                field for field in fields if field.get("scenario_id") == scenario["scenario_id"]
            ]
            scenario_ambiguities = [
                ambiguity
                for ambiguity in ambiguities
                if ambiguity.get("scenario_id") in {None, scenario["scenario_id"]}
            ]
            scenario["confidence"] = self._scenario_confidence(
                scenario=scenario,
                fields=scenario_fields,
                issues=scenario_issues,
                ambiguities=scenario_ambiguities,
            )

        primary = scenarios[0] if scenarios else None
        primary_save = (
            deepcopy(primary["save_validation"]) if primary else {"allowed": False, "errors": []}
        )
        primary_arm = (
            deepcopy(primary["arm_validation"]) if primary else {"allowed": False, "errors": []}
        )
        primary_setup_id = None
        primary_symbol = prepared["symbol"]
        primary_scenario_id = None
        if primary:
            primary_setup_id = str(primary["canonical_config"].get("setup_id") or "")
            primary_symbol = primary["symbol"]
            primary_scenario_id = primary["scenario_id"]

        previous_analysis_id = prepared["previous_analysis_id_override"]
        if persist and primary_setup_id and previous_analysis_id is None:
            previous = self.repository.get_latest_analysis_for_setup(primary_setup_id)
            if previous is not None:
                previous_analysis_id = previous["analysis_id"]
        if persist and previous_analysis_id is None and prepared["force_new_revision"]:
            previous = self.repository.get_latest_analysis_by_hash(analysis_hash)
            if previous is not None:
                previous_analysis_id = previous["analysis_id"]

        analysis = {
            "analysis_id": prepared["analysis_id"],
            "setup_id": primary_setup_id,
            "symbol": primary_symbol,
            "request_id": prepared["request_id"],
            "idempotency_key": prepared["idempotency_key"],
            "analysis_hash": analysis_hash,
            "source_type": prepared["source_type"],
            "raw_input_text": prepared["raw_input_text"],
            "primary_scenario_id": primary_scenario_id,
            "save_validation": primary_save,
            "arm_validation": primary_arm,
            "issues": issues,
            "confidence": self._analysis_confidence(
                scenarios=scenarios,
                issues=issues,
                ambiguities=ambiguities,
            ),
            "schema_version": SCHEMA_VERSION,
            "parser_version": PARSER_VERSION,
            "canonical_mapper_version": CANONICAL_MAPPER_VERSION,
            "prompt_version": None,
            "llm_model": None,
            "previous_analysis_id": previous_analysis_id,
            "provider_name": str(provider_result.get("provider") or "disabled"),
        }

        result = {
            **analysis,
            "scenarios": scenarios,
            "extracted_fields": fields,
            "ambiguities": ambiguities,
            "persisted": False,
            "reused": False,
        }
        persistable = any(
            bool(item.get("save_validation", {}).get("allowed")) for item in scenarios
        )
        if persist and scenarios and persistable:
            self.repository.save_analysis_bundle(
                {
                    "analysis": analysis,
                    "scenarios": scenarios,
                    "fields": fields,
                    "ambiguities": ambiguities,
                }
            )
            result["persisted"] = True
        return result

    async def validate(self, request: dict[str, Any]) -> dict[str, Any]:
        return await self.analyze(request, persist=False)

    def get_latest_for_setup(self, setup_id: str) -> dict[str, Any] | None:
        analysis = self.repository.get_latest_analysis_for_setup(setup_id)
        if analysis is None:
            return None
        return self._load_persisted_analysis(analysis["analysis_id"], reused=False)

    def list_for_setup(self, setup_id: str) -> list[dict[str, Any]]:
        analyses = self.repository.list_analyses_for_setup(setup_id)
        return [
            self._load_persisted_analysis(item["analysis_id"], reused=False) for item in analyses
        ]

    def list_summaries_for_setup(
        self,
        setup_id: str,
        *,
        limit: int | None = None,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        return self.repository.list_analysis_summaries_for_setup(
            setup_id, limit=limit, offset=offset
        )

    def count_analyses_for_setup(self, setup_id: str) -> int:
        return self.repository.count_analyses_for_setup(setup_id)

    def get_analysis(self, analysis_id: str) -> dict[str, Any] | None:
        return self._load_persisted_analysis(analysis_id, reused=False)

    def get_scenarios(self, analysis_id: str) -> list[dict[str, Any]]:
        analysis = self._load_persisted_analysis(analysis_id, reused=False)
        if analysis is None:
            return []
        return list(analysis.get("scenarios", []))

    def compare_analyses(
        self,
        setup_id: str,
        left_analysis_id: str,
        right_analysis_id: str,
        *,
        left_scenario_id: str | None = None,
        right_scenario_id: str | None = None,
    ) -> dict[str, Any]:
        left = self._load_persisted_analysis(left_analysis_id, reused=False)
        right = self._load_persisted_analysis(right_analysis_id, reused=False)
        if left is None or right is None:
            raise KeyError("Analysis not found")
        self._validate_setup_analysis(setup_id, left)
        self._validate_setup_analysis(setup_id, right)
        left_scenario = self._select_scenario(left, left_scenario_id)
        right_scenario = self._select_scenario(right, right_scenario_id)

        left_values = _flatten_config_for_compare(left_scenario["canonical_config"])
        right_values = _flatten_config_for_compare(right_scenario["canonical_config"])
        field_paths = sorted(set(left_values) | set(right_values))

        field_changes: list[dict[str, Any]] = []
        added = 0
        removed = 0
        changed = 0
        unchanged = 0

        for field_path in field_paths:
            left_value = left_values.get(field_path, _MISSING)
            right_value = right_values.get(field_path, _MISSING)
            if left_value is _MISSING:
                change_type = "ADDED"
                added += 1
            elif right_value is _MISSING:
                change_type = "REMOVED"
                removed += 1
            elif _compare_values_equal(left_value, right_value):
                unchanged += 1
                continue
            else:
                change_type = "CHANGED"
                changed += 1
            field_changes.append(
                {
                    "field_path": field_path,
                    "change_type": change_type,
                    "left_value": None if left_value is _MISSING else left_value,
                    "right_value": None if right_value is _MISSING else right_value,
                }
            )

        left_confidence = float((left.get("confidence") or {}).get("score") or 0.0)
        right_confidence = float((right.get("confidence") or {}).get("score") or 0.0)
        left_open_ambiguities = sum(
            1 for item in left.get("ambiguities", []) if item.get("status") == "OPEN"
        )
        right_open_ambiguities = sum(
            1 for item in right.get("ambiguities", []) if item.get("status") == "OPEN"
        )

        return {
            "setup_id": setup_id,
            "left": self._comparison_side(left, left_scenario),
            "right": self._comparison_side(right, right_scenario),
            "summary": {
                "field_change_count": len(field_changes),
                "changed_count": changed,
                "added_count": added,
                "removed_count": removed,
                "unchanged_count": unchanged,
                "confidence_delta": round(right_confidence - left_confidence, 4),
                "error_delta": self._count_issues(right.get("issues", []), "ERROR")
                - self._count_issues(left.get("issues", []), "ERROR"),
                "warning_delta": self._count_issues(right.get("issues", []), "WARNING")
                - self._count_issues(left.get("issues", []), "WARNING"),
                "open_ambiguity_delta": right_open_ambiguities - left_open_ambiguities,
                "status_changed": left_scenario.get("status") != right_scenario.get("status"),
            },
            "field_changes": field_changes,
        }

    def prepare_rollback(
        self,
        setup_id: str,
        analysis_id: str,
        *,
        scenario_id: str | None = None,
    ) -> dict[str, Any]:
        target_analysis = self._load_persisted_analysis(analysis_id, reused=False)
        if target_analysis is None:
            raise KeyError("Analysis not found")
        self._validate_setup_analysis(setup_id, target_analysis)
        target_scenario = self._select_scenario(target_analysis, scenario_id)
        config = deepcopy(target_scenario["canonical_config"])
        config["setup_id"] = setup_id

        latest = self.repository.get_latest_analysis_for_setup(setup_id)
        latest_detail = (
            self._load_persisted_analysis(latest["analysis_id"], reused=False)
            if latest is not None
            else None
        )
        comparison = None
        if latest_detail is not None and latest_detail["analysis_id"] != analysis_id:
            comparison = self.compare_analyses(
                setup_id,
                latest_detail["analysis_id"],
                analysis_id,
                right_scenario_id=target_scenario["scenario_id"],
            )

        return {
            "setup_id": setup_id,
            "config": config,
            "target_analysis": target_analysis,
            "target_scenario": target_scenario,
            "latest_analysis": latest_detail,
            "comparison": comparison,
        }

    async def record_rollback(
        self,
        setup_id: str,
        analysis_id: str,
        *,
        scenario_id: str | None = None,
    ) -> dict[str, Any]:
        rollback = self.prepare_rollback(setup_id, analysis_id, scenario_id=scenario_id)
        latest = rollback["latest_analysis"]
        request_id = f"rollback:{analysis_id}:{rollback['target_scenario']['scenario_id']}"
        return await self.analyze(
            {
                "payload": rollback["config"],
                "request_id": request_id,
                "force_new_revision": True,
                "_source_type": "ROLLBACK",
                "_provider_name": "rollback",
                "_previous_analysis_id": (latest["analysis_id"] if latest is not None else None),
            },
            persist=True,
        )

    async def resolve_ambiguity(
        self,
        analysis_id: str,
        ambiguity_id: str,
        resolution: dict[str, Any],
    ) -> dict[str, Any] | None:
        analysis = self._load_persisted_analysis(analysis_id, reused=False)
        if analysis is None:
            return None
        ambiguity = self._find_ambiguity(analysis, ambiguity_id)
        if ambiguity is None:
            return None

        clean_resolution = deepcopy(resolution)
        selected_option = clean_resolution.get("selected_option")
        if not isinstance(selected_option, dict):
            selected_option = {}
            clean_resolution["selected_option"] = selected_option
        action = str(selected_option.get("action") or "").upper()
        active_effect: dict[str, Any] = {"type": "NO_CONFIG_CHANGE"}
        resolution_analysis = None

        if ambiguity.get("field_path") == "scenario_selection":
            active_effect = {
                "type": "SCENARIO_SELECTION",
                "selected_scenario_id": selected_option.get("scenario_id"),
            }
        elif action in {"UPDATE_FIELD", "REVIEW_FIELD"}:
            patch = self._resolution_patch_for_field(
                analysis=analysis,
                ambiguity=ambiguity,
                resolution=clean_resolution,
            )
            clean_resolution["applied_patch"] = patch["applied_patch"]
            resolution_analysis = await self.analyze(
                {
                    "payload": patch["config"],
                    "request_id": f"resolve:{analysis_id}:{ambiguity_id}",
                    "force_new_revision": True,
                    "_source_type": "RESOLUTION",
                    "_provider_name": "resolution",
                    "_previous_analysis_id": analysis_id,
                },
                persist=True,
            )
            clean_resolution["resolution_analysis_id"] = resolution_analysis["analysis_id"]
            active_effect = {
                "type": "FIELD_PATCH",
                "field_path": patch["field_path"],
                "scenario_id": patch["scenario_id"],
                "resolution_analysis_id": resolution_analysis["analysis_id"],
                "persisted": bool(resolution_analysis.get("persisted")),
            }

        resolved = self.repository.resolve_ambiguity(
            analysis_id,
            ambiguity_id,
            clean_resolution,
        )
        refreshed = self._load_persisted_analysis(analysis_id, reused=False)
        return {
            "ambiguity": resolved,
            "analysis": refreshed,
            "resolution_analysis": resolution_analysis,
            "active_effect": active_effect,
        }

    def _prepare_analysis_input(self, request: dict[str, Any]) -> dict[str, Any]:
        request_id = str(request.get("request_id") or "") or None
        idempotency_key = str(request.get("idempotency_key") or "") or None
        force_new_revision = bool(request.get("force_new_revision", False))
        symbol = str(request.get("symbol") or "").strip().upper()
        source_type = "PLAIN_TEXT"
        raw_input_text = ""
        scenario_inputs: list[dict[str, Any]] = []
        issues: list[dict[str, Any]] = []

        if request.get("payload") is not None:
            payload = request.get("payload")
            raw_input_text = json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True)
            source_type = "JSON_PAYLOAD"
            scenario_inputs, issues = self._scenario_inputs_from_payload(payload, symbol)
        else:
            text = str(request.get("text") or "")
            raw_input_text = text
            parsed_payload = self._parse_json_text(text)
            if parsed_payload is not None:
                source_type = "JSON_TEXT"
                scenario_inputs, issues = self._scenario_inputs_from_payload(parsed_payload, symbol)
            else:
                scenario_input = self._scenario_input_from_text(symbol, text)
                if scenario_input is None:
                    issues.append(
                        self._issue(
                            code="INVALID_INPUT",
                            field_path=None,
                            message="Unable to extract a scenario from the provided input.",
                            severity="ERROR",
                            source_line=None,
                            accepted_aliases=[],
                        )
                    )
                else:
                    scenario_inputs.append(scenario_input)

        if not symbol:
            for item in scenario_inputs:
                scenario_symbol = str(item["raw_config"].get("symbol") or "").strip().upper()
                if scenario_symbol:
                    symbol = scenario_symbol
                    break

        explicit_ambiguities = request.get("ambiguities", [])
        source_type_override = str(request.get("_source_type") or "").strip().upper()
        provider_name_override = str(request.get("_provider_name") or "").strip()
        previous_analysis_id_override = (
            str(request.get("_previous_analysis_id") or "").strip() or None
        )
        return {
            "analysis_id": new_id("analysis"),
            "request_id": request_id,
            "idempotency_key": idempotency_key,
            "force_new_revision": force_new_revision,
            "symbol": symbol,
            "source_type": source_type_override or source_type,
            "raw_input_text": raw_input_text,
            "scenario_inputs": scenario_inputs,
            "issues": issues,
            "explicit_ambiguities": (
                explicit_ambiguities if isinstance(explicit_ambiguities, list) else []
            ),
            "provider_name_override": provider_name_override or None,
            "previous_analysis_id_override": previous_analysis_id_override,
        }

    def _scenario_inputs_from_payload(
        self,
        payload: Any,
        default_symbol: str,
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        issues: list[dict[str, Any]] = []
        items: list[Any]
        if isinstance(payload, dict) and isinstance(payload.get("scenarios"), list):
            items = list(payload["scenarios"])
        elif isinstance(payload, list):
            items = list(payload)
        elif isinstance(payload, dict):
            items = [payload]
        else:
            return [], [
                self._issue(
                    code="INVALID_INPUT",
                    field_path=None,
                    message="Payload must be a JSON object or an array of scenarios.",
                    severity="ERROR",
                    source_line=None,
                    accepted_aliases=[],
                )
            ]

        scenario_inputs: list[dict[str, Any]] = []
        for index, item in enumerate(items, start=1):
            if not isinstance(item, dict):
                issues.append(
                    self._issue(
                        code="INVALID_SCENARIO",
                        field_path=f"scenarios[{index - 1}]",
                        message="Each scenario payload must be a JSON object.",
                        severity="ERROR",
                        source_line=None,
                        accepted_aliases=[],
                    )
                )
                continue
            raw_config = item.get("config") if isinstance(item.get("config"), dict) else item
            if not isinstance(raw_config, dict):
                issues.append(
                    self._issue(
                        code="INVALID_SCENARIO_CONFIG",
                        field_path=f"scenarios[{index - 1}]",
                        message="Scenario config must be a JSON object.",
                        severity="ERROR",
                        source_line=None,
                        accepted_aliases=[],
                    )
                )
                continue
            raw_config = deepcopy(raw_config)
            if default_symbol and not raw_config.get("symbol"):
                raw_config["symbol"] = default_symbol
            scenario_inputs.append(
                {
                    "raw_config": raw_config,
                    "scenario_name": str(item.get("scenario_name") or f"Scenario {index}"),
                    "scenario_role": str(
                        item.get("scenario_role") or ("PRIMARY" if index == 1 else "ALTERNATIVE")
                    ).upper(),
                    "status": str(item.get("status") or ""),
                    "selected": False,
                    "armed": False,
                    "source_kind": "STRUCTURED_PAYLOAD",
                }
            )
        return scenario_inputs, issues

    def _scenario_input_from_text(
        self,
        symbol: str,
        text: str,
    ) -> dict[str, Any] | None:
        clean_symbol = symbol.strip().upper()
        if not clean_symbol or not text.strip():
            return None
        result = convert_text_to_setup(
            symbol=clean_symbol,
            text=text,
            defaults=self.defaults,
            enabled=True,
        )
        if result.ok and result.config is not None:
            return {
                "raw_config": deepcopy(result.config),
                "scenario_name": "Primary scenario",
                "scenario_role": "PRIMARY",
                "status": "",
                "selected": False,
                "armed": False,
                "source_kind": "TEXT_CONVERTER",
            }
        partial = self._build_partial_text_config(clean_symbol, text)
        if partial is None:
            return None
        return {
            "raw_config": partial,
            "scenario_name": "Primary scenario",
            "scenario_role": "PRIMARY",
            "status": "",
            "selected": False,
            "armed": False,
            "source_kind": "TEXT_FALLBACK",
        }

    def _build_partial_text_config(self, symbol: str, text: str) -> dict[str, Any] | None:
        normalized = _normalize(text)
        setup_type = _detect_setup_type(normalized)
        breakout_level = _extract_breakout_level(normalized)
        stop_loss = _extract_labeled_number(normalized, ["stop", "sl", "hard stop"])
        max_risk = _extract_labeled_number(normalized, ["risque", "risk"])
        max_position = _extract_labeled_number(
            normalized,
            ["budget", "position", "exposition", "capital"],
        )
        ranges = _extract_ranges(normalized)

        config: dict[str, Any] = {
            "setup_id": _make_setup_id(symbol, setup_type),
            "symbol": symbol,
            "enabled": True,
            "mode": str(self.defaults.get("app", {}).get("mode", "paper")),
            "setup_type": setup_type,
            "setup_role": "ENTRY_AND_MANAGEMENT",
            "direction": "long",
            "entry": {
                "enabled": True,
                "order_type": str(
                    self.defaults.get("orders", {}).get("default_entry_order_type", "STP_LMT")
                ),
                "trigger_offset": float(
                    self.defaults.get("setup_defaults", {})
                    .get("entry", {})
                    .get("trigger_offset", 0.02)
                ),
                "limit_offset": float(
                    self.defaults.get("setup_defaults", {})
                    .get("entry", {})
                    .get("limit_offset", 0.05)
                ),
            },
            "risk": {},
        }
        if stop_loss is not None:
            config["trailing_stop_loss"] = {
                "enabled": True,
                "mode": "AUTO_INTELLIGENT",
                "never_lower_stop": True,
                "initial_stop": float(stop_loss),
                "broker_order": {
                    "order_type": "TRAIL_OR_MANAGED_STOP",
                    "attach_to_entry_order": True,
                    "required_before_entry_transmission": True,
                },
            }
        if max_position is not None:
            config["risk"]["max_position_amount_usd"] = float(max_position)
        if max_risk is not None:
            config["risk"]["max_risk_usd"] = float(max_risk)

        zone = ranges[0] if ranges else None
        if setup_type == "breakout_retest":
            if breakout_level is not None:
                config["breakout"] = {"daily_close_above": float(breakout_level)}
            if zone is not None:
                config["retest"] = {
                    "zone_min": min(zone),
                    "zone_max": max(zone),
                }
        elif setup_type == "aggressive_rebound":
            if zone is not None:
                config["support_zone"] = {"min": min(zone), "max": max(zone)}
        elif setup_type == "momentum_breakout":
            if breakout_level is not None:
                config["breakout"] = {"resistance": float(breakout_level)}
        elif setup_type == "pullback_continuation":
            if breakout_level is not None:
                config["pullback"] = {"entry_reference": float(breakout_level)}
        elif setup_type == "range_breakout":
            if zone is not None:
                config["range"] = {"low": min(zone), "high": max(zone)}

        return config

    def _analyze_scenario_input(
        self,
        scenario_input: dict[str, Any],
        *,
        analysis_id: str,
        source_text: str,
        scenario_index: int,
    ) -> dict[str, Any]:
        raw_config = deepcopy(scenario_input["raw_config"])
        raw_setup_id = str(raw_config.get("setup_id") or "").strip()
        canonical = canonicalize_setup_config(raw_config, defaults=self.defaults)
        config = canonical.config
        if not str(config.get("setup_id") or "").strip():
            config["setup_id"] = raw_setup_id or new_id("setup")
        raw_scenario_id = str(scenario_input.get("scenario_id") or "").strip() or (
            str(config["setup_id"])
            if scenario_index == 1
            else f"{config['setup_id']}_S{scenario_index:02d}"
        )
        scenario_id = raw_scenario_id
        if not scenario_id.startswith(f"{analysis_id}:"):
            scenario_id = f"{analysis_id}:{scenario_id}"
        semantic_report = self.semantic_validation.validate(config)
        save_validation = self._save_validation(config)
        arm_validation = self._arm_validation(config, semantic_report, canonical.warnings)
        scenario_status = self._scenario_status(
            explicit_status=str(scenario_input.get("status") or ""),
            save_validation=save_validation,
            arm_validation=arm_validation,
        )
        scenario = {
            "scenario_id": scenario_id,
            "analysis_id": analysis_id,
            "symbol": str(config.get("symbol") or "").upper(),
            "scenario_name": str(
                scenario_input.get("scenario_name")
                or self._scenario_name(config, scenario_input.get("scenario_role"))
            ),
            "scenario_role": str(
                scenario_input.get("scenario_role")
                or ("PRIMARY" if scenario_index == 1 else "ALTERNATIVE")
            ).upper(),
            "setup_type": str(config.get("setup_type") or ""),
            "status": scenario_status,
            "selected": False,
            "armed": False,
            "canonical_config": config,
            "save_validation": save_validation,
            "arm_validation": arm_validation,
        }

        issues = self._structured_issues(
            config=config,
            save_validation=save_validation,
            arm_validation=arm_validation,
            semantic_report=semantic_report,
            canonical_warnings=canonical.warnings,
            scenario_id=scenario_id,
        )
        fields = self._build_extracted_fields(
            analysis_id=analysis_id,
            scenario_id=scenario_id,
            raw_config=raw_config,
            canonical_config=config,
            source_text=source_text,
            source_kind=str(scenario_input.get("source_kind") or "UNKNOWN"),
            issues=issues,
        )
        self._apply_field_validation_status(fields, issues)
        self._attach_issue_source_lines(issues, fields)
        return {"scenario": scenario, "issues": issues, "fields": fields}

    def _save_validation(self, config: dict[str, Any]) -> dict[str, Any]:
        errors: list[str] = []
        if not str(config.get("symbol") or "").strip():
            errors.append("symbol is required to save a scenario draft")
        setup_type = str(config.get("setup_type") or "").strip()
        if not setup_type:
            errors.append("setup_type is required to save a scenario draft")
        elif setup_type not in SetupFactory.supported_types():
            errors.append(f"Unknown setup type: {setup_type}")
        return {"allowed": not errors, "errors": errors}

    def _arm_validation(
        self,
        config: dict[str, Any],
        semantic_report: Any,
        canonical_warnings: list[str],
    ) -> dict[str, Any]:
        errors = list(semantic_report.errors)
        warnings = list(dict.fromkeys([*canonical_warnings, *semantic_report.warnings]))
        try:
            setup = SetupFactory.create(config)
        except UnknownSetupTypeError as exc:
            errors.append(str(exc))
            return {"allowed": False, "errors": list(dict.fromkeys(errors)), "warnings": warnings}
        result = setup.validate()
        errors.extend(result.errors)
        warnings.extend(result.warnings)
        return {
            "allowed": not errors,
            "errors": list(dict.fromkeys(errors)),
            "warnings": list(dict.fromkeys(warnings)),
        }

    def _scenario_status(
        self,
        *,
        explicit_status: str,
        save_validation: dict[str, Any],
        arm_validation: dict[str, Any],
    ) -> str:
        if explicit_status:
            return explicit_status
        if arm_validation["allowed"]:
            return "READY_FOR_REVIEW"
        if save_validation["allowed"]:
            return "REVIEW_REQUIRED"
        return "INVALID_DRAFT"

    def _structured_issues(
        self,
        *,
        config: dict[str, Any],
        save_validation: dict[str, Any],
        arm_validation: dict[str, Any],
        semantic_report: Any,
        canonical_warnings: list[str],
        scenario_id: str,
    ) -> list[dict[str, Any]]:
        issues: list[dict[str, Any]] = []
        for issue in semantic_report.issues:
            issues.append(
                self._issue(
                    code=str(issue.code).upper(),
                    field_path=issue.path if issue.path != "$" else None,
                    message=issue.message,
                    severity="ERROR" if issue.level == "error" else "WARNING",
                    source_line=None,
                    accepted_aliases=self._accepted_aliases_for_path(
                        issue.path if issue.path != "$" else None
                    ),
                    scenario_id=scenario_id,
                )
            )
        for warning in canonical_warnings:
            issues.append(
                self._issue(
                    code="CANONICAL_WARNING",
                    field_path=None,
                    message=warning,
                    severity="WARNING",
                    source_line=None,
                    accepted_aliases=[],
                    scenario_id=scenario_id,
                )
            )
        for message in save_validation["errors"]:
            issues.append(self._issue_from_message(message, "ERROR", scenario_id))
        for message in arm_validation["errors"]:
            issues.append(self._issue_from_message(message, "ERROR", scenario_id))
        for message in arm_validation["warnings"]:
            issues.append(self._issue_from_message(message, "WARNING", scenario_id))
        return _dedupe_issues(issues)

    def _issue_from_message(
        self,
        message: str,
        severity: str,
        scenario_id: str,
    ) -> dict[str, Any]:
        path = None
        code = "VALIDATION_ISSUE"
        custom_message = message
        mapping = {
            "setup_id is required": ("MISSING_REQUIRED_FIELD", "setup_id", "setup_id is required."),
            "symbol is required": ("MISSING_REQUIRED_FIELD", "symbol", "Symbol is required."),
            "trailing_stop_loss.initial_stop must be positive": (
                "MISSING_REQUIRED_FIELD",
                "trailing_stop_loss.initial_stop",
                "Stop trailing initial introuvable ou invalide.",
            ),
            "trailing_stop_loss.initial_stop is required before arming": (
                "MISSING_REQUIRED_FIELD",
                "trailing_stop_loss.initial_stop",
                "Stop trailing initial requis avant armement.",
            ),
            "trailing_stop_loss.broker_order.required_before_entry_transmission must be true before arming": (
                "INVALID_FIELD_VALUE",
                "trailing_stop_loss.broker_order.required_before_entry_transmission",
                "Le trailing stop-loss doit etre pret avant transmission de l'ordre d'entree.",
            ),
            "risk.max_position_amount_usd must be positive": (
                "MISSING_REQUIRED_FIELD",
                "risk.max_position_amount_usd",
                "Budget maximal introuvable ou invalide.",
            ),
            "risk.max_risk_usd must be positive": (
                "MISSING_REQUIRED_FIELD",
                "risk.max_risk_usd",
                "Risque maximal introuvable ou invalide.",
            ),
            "estimated entry price is required": (
                "MISSING_REQUIRED_FIELD",
                "entry.trigger_price",
                "Prix d'entree estimé introuvable.",
            ),
            "retest.zone_min and retest.zone_max are required": (
                "MISSING_REQUIRED_FIELD",
                "retest",
                "Zone de retest incomplète.",
            ),
            "support_zone.min and support_zone.max are required": (
                "MISSING_REQUIRED_FIELD",
                "support_zone",
                "Zone de support incomplète.",
            ),
            "breakout.daily_close_above is required": (
                "MISSING_REQUIRED_FIELD",
                "breakout.daily_close_above",
                "Niveau de breakout journalier introuvable.",
            ),
            "position_source.mode must be adopt_existing_ibkr_position": (
                "INVALID_FIELD_VALUE",
                "position_source.mode",
                "Le mode de source de position est invalide.",
            ),
            "position_source.require_existing_position must be true": (
                "INVALID_FIELD_VALUE",
                "position_source.require_existing_position",
                "La réconciliation exige une position existante.",
            ),
        }
        if message in mapping:
            code, path, custom_message = mapping[message]
        elif "Unknown setup type:" in message:
            code = "UNKNOWN_SETUP_TYPE"
            path = "setup_type"
        elif "mode must be paper or live" in message:
            code = "INVALID_FIELD_VALUE"
            path = "mode"
        elif "setup_role must be" in message:
            code = "INVALID_FIELD_VALUE"
            path = "setup_role"
        elif "stop loss must be below estimated entry price" in message:
            code = "INVALID_RISK_CONFIGURATION"
            path = "trailing_stop_loss.initial_stop"

        return self._issue(
            code=code,
            field_path=path,
            message=custom_message,
            severity=severity,
            source_line=None,
            accepted_aliases=self._accepted_aliases_for_path(path),
            scenario_id=scenario_id,
        )

    def _build_extracted_fields(
        self,
        *,
        analysis_id: str,
        scenario_id: str,
        raw_config: dict[str, Any],
        canonical_config: dict[str, Any],
        source_text: str,
        source_kind: str,
        issues: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        if source_kind == "TEXT_CONVERTER" or source_kind == "TEXT_FALLBACK":
            return self._fields_from_canonical_text(
                analysis_id=analysis_id,
                scenario_id=scenario_id,
                canonical_config=canonical_config,
                source_text=source_text,
                source_kind=source_kind,
                issues=issues,
            )

        source_lines = source_text.splitlines()
        fields: list[dict[str, Any]] = []
        for raw_path, raw_value in _flatten_leaves(raw_config):
            raw_key = raw_path.split(".")[-1]
            canonical_path = self._resolve_canonical_path(raw_path)
            parsed_value = _get_nested(canonical_config, canonical_path) if canonical_path else None
            source_ref = _find_source_reference(
                source_lines,
                raw_key=raw_key,
                raw_value=raw_value,
            )
            extraction_method = "ALIAS_MAPPING"
            if raw_path == canonical_path:
                extraction_method = "DIRECT_MAPPING"
            confidence = 1.0 if extraction_method == "DIRECT_MAPPING" else 0.98
            if isinstance(raw_value, str) and parsed_value != raw_value:
                confidence = 0.95
            fields.append(
                {
                    "analysis_id": analysis_id,
                    "scenario_id": scenario_id,
                    "raw_key": raw_key,
                    "normalized_key": normalize_key(raw_key),
                    "canonical_path": canonical_path or normalize_key(raw_path),
                    "raw_value": _stringify(raw_value),
                    "parsed_value": parsed_value,
                    "source_text": source_ref["source_text"],
                    "source_line_start": source_ref["source_line_start"],
                    "source_line_end": source_ref["source_line_end"],
                    "extraction_method": extraction_method,
                    "confidence": confidence,
                    "validation_status": "VALID",
                }
            )
        return fields

    def _fields_from_canonical_text(
        self,
        *,
        analysis_id: str,
        scenario_id: str,
        canonical_config: dict[str, Any],
        source_text: str,
        source_kind: str,
        issues: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        source_lines = source_text.splitlines()
        fields: list[dict[str, Any]] = []
        for canonical_path, parsed_value in _flatten_leaves(canonical_config):
            if canonical_path in {"setup_id", "enabled", "mode", "setup_role", "direction"}:
                continue
            raw_key = canonical_path.split(".")[-1]
            source_ref = _find_source_reference(
                source_lines,
                raw_key=raw_key,
                raw_value=parsed_value,
            )
            fields.append(
                {
                    "analysis_id": analysis_id,
                    "scenario_id": scenario_id,
                    "raw_key": raw_key,
                    "normalized_key": normalize_key(raw_key),
                    "canonical_path": canonical_path,
                    "raw_value": _stringify(parsed_value),
                    "parsed_value": parsed_value,
                    "source_text": source_ref["source_text"] or source_text.strip(),
                    "source_line_start": source_ref["source_line_start"] or 1,
                    "source_line_end": source_ref["source_line_end"] or 1,
                    "extraction_method": (
                        "TEXT_CONVERTER_PATTERN"
                        if source_kind == "TEXT_CONVERTER"
                        else "TEXT_FALLBACK_PATTERN"
                    ),
                    "confidence": 0.9 if source_kind == "TEXT_CONVERTER" else 0.75,
                    "validation_status": "VALID",
                }
            )
        return fields

    def _apply_field_validation_status(
        self,
        fields: list[dict[str, Any]],
        issues: list[dict[str, Any]],
    ) -> None:
        for field in fields:
            status = "VALID"
            source_line = field.get("source_line_start")
            for issue in issues:
                field_path = issue.get("field_path")
                if not field_path:
                    continue
                if (
                    field["canonical_path"] == field_path
                    or field["canonical_path"].startswith(f"{field_path}.")
                    or field_path.startswith(f"{field['canonical_path']}.")
                ):
                    if issue["severity"] == "ERROR":
                        status = "INVALID"
                        source_line = source_line or issue.get("source_line")
                        break
                    status = "REVIEW"
                    source_line = source_line or issue.get("source_line")
            field["validation_status"] = status
            if source_line and field.get("source_line_start") is None:
                field["source_line_start"] = source_line
                field["source_line_end"] = source_line

    def _scenario_selection_ambiguity(
        self,
        *,
        analysis_id: str,
        scenarios: list[dict[str, Any]],
    ) -> dict[str, Any]:
        return self._ambiguity(
            analysis_id=analysis_id,
            scenario_id=None,
            field_path="scenario_selection",
            message="Multiple scenarios were extracted from the same analysis.",
            options=[
                {
                    "scenario_id": scenario["scenario_id"],
                    "scenario_name": scenario["scenario_name"],
                    "scenario_role": scenario["scenario_role"],
                    "setup_type": scenario["setup_type"],
                    "status": scenario["status"],
                    "save_allowed": bool(scenario.get("save_validation", {}).get("allowed")),
                    "arm_allowed": bool(scenario.get("arm_validation", {}).get("allowed")),
                }
                for scenario in scenarios
            ],
            kind="SCENARIO_SELECTION",
            severity="REVIEW",
            detection_method="MULTI_SCENARIO",
            suggested_action="Choisir le scenario qui doit piloter le setup.",
            evidence={"scenario_count": len(scenarios)},
        )

    def _ambiguities_from_issues(
        self,
        *,
        analysis_id: str,
        issues: list[dict[str, Any]],
        fields: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        ambiguities: list[dict[str, Any]] = []
        seen: set[tuple[str, str, str, str]] = set()
        for issue in issues:
            field_path = str(issue.get("field_path") or "").strip()
            if not field_path:
                continue
            issue_severity = str(issue.get("severity") or "").upper()
            if issue_severity not in {"ERROR", "WARNING"}:
                continue
            issue_code = str(issue.get("code") or "VALIDATION_ISSUE").upper()
            if issue_severity == "WARNING" and issue_code == "CANONICAL_WARNING":
                continue
            scenario_id = str(issue.get("scenario_id") or "")
            message = str(issue.get("message") or "Field requires review.")
            kind = _ambiguity_kind_from_issue_code(issue_code)
            key = (scenario_id, field_path, issue_code, message)
            if key in seen:
                continue
            seen.add(key)
            field = self._field_for_issue_path(fields, field_path, scenario_id)
            severity = "BLOCKER" if issue_severity == "ERROR" else "REVIEW"
            ambiguities.append(
                self._ambiguity(
                    analysis_id=analysis_id,
                    scenario_id=scenario_id or None,
                    field_path=field_path,
                    message=f"{field_path} requires review: {message}",
                    options=self._ambiguity_options_for_issue(issue),
                    kind=kind,
                    severity=severity,
                    detection_method="VALIDATION_ISSUE",
                    suggested_action=self._suggested_action_for_issue(issue),
                    evidence={
                        "issue_code": issue_code,
                        "issue_message": message,
                        "issue_severity": issue_severity,
                        "source_line": issue.get("source_line"),
                        "accepted_aliases": issue.get("accepted_aliases", []),
                        "current_value": field.get("parsed_value") if field else None,
                        "extraction_method": field.get("extraction_method") if field else None,
                    },
                )
            )
        return ambiguities

    def _explicit_ambiguity(
        self,
        analysis_id: str,
        explicit: dict[str, Any],
    ) -> dict[str, Any]:
        if not isinstance(explicit, dict):
            explicit = {"message": str(explicit or "Ambiguity to resolve")}
        metadata = explicit.get("metadata") if isinstance(explicit.get("metadata"), dict) else {}
        kind = str(explicit.get("kind") or metadata.get("kind") or "USER_PROVIDED").upper()
        severity = str(explicit.get("severity") or metadata.get("severity") or "REVIEW").upper()
        confidence_impact = explicit.get("confidence_impact", metadata.get("confidence_impact"))
        options = explicit.get("options", [])
        if not isinstance(options, list):
            options = []
        resolution = explicit.get("resolution", {})
        if not isinstance(resolution, dict):
            resolution = {}
        return self._ambiguity(
            analysis_id=analysis_id,
            scenario_id=explicit.get("scenario_id"),
            field_path=str(explicit.get("field_path") or ""),
            message=str(explicit.get("message") or "Ambiguity to resolve"),
            options=options,
            status=str(explicit.get("status") or "OPEN").upper(),
            resolution=resolution,
            kind=kind,
            severity=severity,
            confidence_impact=confidence_impact,
            detection_method=str(
                explicit.get("detection_method")
                or metadata.get("detection_method")
                or "USER_PROVIDED"
            ),
            suggested_action=str(
                explicit.get("suggested_action")
                or metadata.get("suggested_action")
                or "Verifier cette decision avant armement."
            ),
            evidence=metadata.get("evidence", {}),
            metadata=metadata,
        )

    def _ambiguity(
        self,
        *,
        analysis_id: str,
        scenario_id: Any,
        field_path: str,
        message: str,
        options: list[dict[str, Any]],
        kind: str,
        severity: str,
        detection_method: str,
        suggested_action: str,
        evidence: dict[str, Any] | None = None,
        confidence_impact: Any = None,
        status: str = "OPEN",
        resolution: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        clean_kind = str(kind or "USER_PROVIDED").upper()
        clean_severity = str(severity or "REVIEW").upper()
        if clean_severity not in _AMBIGUITY_PENALTIES:
            clean_severity = "REVIEW"
        impact = _safe_float(confidence_impact)
        if impact is None:
            impact = _AMBIGUITY_PENALTIES[clean_severity]
        impact = round(max(0.0, min(float(impact), 1.0)), 4)
        clean_metadata = {
            **(metadata or {}),
            "kind": clean_kind,
            "severity": clean_severity,
            "confidence_impact": impact,
            "detection_method": str(detection_method or "UNKNOWN"),
            "suggested_action": str(suggested_action or ""),
            "evidence": evidence or {},
        }
        return {
            "ambiguity_id": new_id("amb"),
            "analysis_id": analysis_id,
            "scenario_id": str(scenario_id or "") or None,
            "field_path": field_path,
            "message": message,
            "options": options,
            "status": str(status or "OPEN").upper(),
            "resolution": resolution or {},
            "kind": clean_kind,
            "severity": clean_severity,
            "confidence_impact": impact,
            "suggested_action": clean_metadata["suggested_action"],
            "metadata": clean_metadata,
        }

    def _ambiguity_options_for_issue(self, issue: dict[str, Any]) -> list[dict[str, Any]]:
        field_path = str(issue.get("field_path") or "")
        severity = str(issue.get("severity") or "").upper()
        if severity == "ERROR":
            return [
                {
                    "label": "Corriger le champ",
                    "action": "UPDATE_FIELD",
                    "field_path": field_path,
                    "recommended": True,
                    "confidence_effect": "INCREASE",
                },
                {
                    "label": "Garder en brouillon",
                    "action": "KEEP_DRAFT",
                    "field_path": field_path,
                    "recommended": False,
                    "confidence_effect": "LIMIT_TO_REVIEW",
                },
            ]
        return [
            {
                "label": "Accepter le warning",
                "action": "ACCEPT_WARNING",
                "field_path": field_path,
                "recommended": False,
                "confidence_effect": "NEUTRAL",
            },
            {
                "label": "Reviser le champ",
                "action": "REVIEW_FIELD",
                "field_path": field_path,
                "recommended": True,
                "confidence_effect": "INCREASE",
            },
        ]

    @staticmethod
    def _suggested_action_for_issue(issue: dict[str, Any]) -> str:
        severity = str(issue.get("severity") or "").upper()
        code = str(issue.get("code") or "").upper()
        if (
            severity == "ERROR"
            and _ambiguity_kind_from_issue_code(code) == "MISSING_REQUIRED_FIELD"
        ):
            return "Renseigner ce champ avant armement."
        if severity == "ERROR":
            return "Corriger ou confirmer cette valeur avant armement."
        return "Verifier ce point avant de faire confiance au scenario."

    @staticmethod
    def _field_for_issue_path(
        fields: list[dict[str, Any]],
        field_path: str,
        scenario_id: str,
    ) -> dict[str, Any] | None:
        for field in fields:
            if scenario_id and str(field.get("scenario_id") or "") != scenario_id:
                continue
            canonical_path = str(field.get("canonical_path") or "")
            if (
                canonical_path == field_path
                or canonical_path.startswith(f"{field_path}.")
                or field_path.startswith(f"{canonical_path}.")
            ):
                return field
        return None

    @staticmethod
    def _find_ambiguity(
        analysis: dict[str, Any],
        ambiguity_id: str,
    ) -> dict[str, Any] | None:
        for ambiguity in analysis.get("ambiguities", []):
            if str(ambiguity.get("ambiguity_id") or "") == str(ambiguity_id or ""):
                return ambiguity
        return None

    def _resolution_patch_for_field(
        self,
        *,
        analysis: dict[str, Any],
        ambiguity: dict[str, Any],
        resolution: dict[str, Any],
    ) -> dict[str, Any]:
        selected_option = resolution.get("selected_option")
        if not isinstance(selected_option, dict):
            selected_option = {}
        field_path = str(
            selected_option.get("field_path") or ambiguity.get("field_path") or ""
        ).strip()
        if not field_path:
            raise ValueError("A field path is required to apply this ambiguity resolution")
        if "field_value" in resolution and resolution.get("field_value") is not None:
            raw_value = resolution.get("field_value")
        elif selected_option.get("value") is not None:
            raw_value = selected_option.get("value")
        elif selected_option.get("new_value") is not None:
            raw_value = selected_option.get("new_value")
        else:
            raise ValueError("field_value is required to resolve this field ambiguity")

        scenario = self._select_scenario(
            analysis,
            str(ambiguity.get("scenario_id") or "") or None,
        )
        patched_config = deepcopy(scenario.get("canonical_config", {}))
        canonical_path = self._resolve_canonical_path(field_path)
        _set_nested_config_value(patched_config, canonical_path, raw_value)
        canonical = canonicalize_setup_config(
            patched_config,
            defaults=self.defaults,
        )
        parsed_value = _get_nested(canonical.config, canonical_path)
        return {
            "config": canonical.config,
            "field_path": canonical_path,
            "scenario_id": scenario.get("scenario_id"),
            "applied_patch": {
                "field_path": canonical_path,
                "raw_value": raw_value,
                "parsed_value": parsed_value,
            },
        }

    def _load_persisted_analysis(self, analysis_id: str, *, reused: bool) -> dict[str, Any] | None:
        analysis = self.repository.get_analysis(analysis_id)
        if analysis is None:
            return None
        scenarios = self.repository.list_scenarios(analysis_id)
        ambiguities = self.repository.list_ambiguities(analysis_id)
        fields = self.repository.list_extracted_fields(analysis_id)
        selected_scenario_id = self._selected_scenario_id_from_analysis(
            analysis,
            scenarios,
            ambiguities,
        )
        hydrated_scenarios: list[dict[str, Any]] = []
        for scenario in scenarios:
            config = deepcopy(scenario.get("canonical_config", {}))
            semantic_report = self.semantic_validation.validate(config)
            save_validation = self._save_validation(config)
            arm_validation = self._arm_validation(config, semantic_report, [])
            scenario_id = scenario["scenario_id"]
            scenario_issues = [
                issue
                for issue in analysis.get("issues", [])
                if issue.get("scenario_id") == scenario_id
            ]
            scenario_fields = [field for field in fields if field.get("scenario_id") == scenario_id]
            scenario_ambiguities = [
                ambiguity
                for ambiguity in ambiguities
                if ambiguity.get("scenario_id") in {None, scenario_id}
            ]
            hydrated = {
                **scenario,
                "selected": scenario_id == selected_scenario_id,
                "save_validation": save_validation,
                "arm_validation": arm_validation,
            }
            hydrated["confidence"] = self._scenario_confidence(
                scenario=hydrated,
                fields=scenario_fields,
                issues=scenario_issues,
                ambiguities=scenario_ambiguities,
            )
            hydrated_scenarios.append(hydrated)
        selected_scenario = None
        for scenario in hydrated_scenarios:
            if scenario.get("scenario_id") == selected_scenario_id:
                selected_scenario = scenario
                break
        if selected_scenario is None and hydrated_scenarios:
            selected_scenario = hydrated_scenarios[0]
        analysis_confidence = self._analysis_confidence(
            scenarios=hydrated_scenarios,
            issues=analysis.get("issues", []),
            ambiguities=ambiguities,
        )
        return {
            **analysis,
            "primary_scenario_id": analysis.get("primary_scenario_id") or selected_scenario_id,
            "save_validation": (
                selected_scenario.get("save_validation")
                if selected_scenario is not None
                else analysis.get("save_validation")
            ),
            "arm_validation": (
                selected_scenario.get("arm_validation")
                if selected_scenario is not None
                else analysis.get("arm_validation")
            ),
            "confidence": analysis_confidence,
            "selected_scenario_id": selected_scenario_id,
            "scenarios": hydrated_scenarios,
            "extracted_fields": fields,
            "ambiguities": ambiguities,
            "persisted": True,
            "reused": reused,
        }

    def _build_error_result(
        self,
        prepared: dict[str, Any],
        issues: list[dict[str, Any]],
        *,
        save_allowed: bool,
        arm_allowed: bool,
        persisted: bool,
        reused: bool,
    ) -> dict[str, Any]:
        errors = [issue["message"] for issue in issues if issue["severity"] == "ERROR"]
        warnings = [issue["message"] for issue in issues if issue["severity"] == "WARNING"]
        return {
            "analysis_id": prepared["analysis_id"],
            "setup_id": None,
            "symbol": prepared["symbol"],
            "request_id": prepared["request_id"],
            "idempotency_key": prepared["idempotency_key"],
            "analysis_hash": self._analysis_hash(
                prepared["raw_input_text"],
                prepared["symbol"],
            ),
            "source_type": prepared["source_type"],
            "raw_input_text": prepared["raw_input_text"],
            "primary_scenario_id": None,
            "save_validation": {"allowed": save_allowed, "errors": errors},
            "arm_validation": {"allowed": arm_allowed, "errors": errors, "warnings": warnings},
            "issues": issues,
            "confidence": {
                "score": 0.0,
                "label": "INVALID",
                "summary": "Input could not produce a reusable draft.",
                "components": {
                    "field_mean": 0.0,
                    "error_penalty": 1.0,
                    "warning_penalty": 0.0,
                    "ambiguity_penalty": 0.0,
                },
            },
            "schema_version": SCHEMA_VERSION,
            "parser_version": PARSER_VERSION,
            "canonical_mapper_version": CANONICAL_MAPPER_VERSION,
            "prompt_version": None,
            "llm_model": None,
            "previous_analysis_id": None,
            "provider_name": "disabled",
            "scenarios": [],
            "extracted_fields": [],
            "ambiguities": [],
            "persisted": persisted,
            "reused": reused,
        }

    def _analysis_hash(self, raw_input_text: str, symbol: str) -> str:
        payload = {
            "raw_input_text": raw_input_text,
            "symbol": symbol,
            "parser_version": PARSER_VERSION,
            "schema_version": SCHEMA_VERSION,
            "canonical_mapper_version": CANONICAL_MAPPER_VERSION,
        }
        encoded = json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    def _resolve_canonical_path(self, raw_path: str) -> str:
        canonical = self.alias_resolver.canonical_path(raw_path)
        if canonical:
            return canonical
        resolved = self.alias_resolver.resolve(raw_path)
        if resolved:
            return resolved
        parts = [normalize_key(part) for part in raw_path.split(".") if part]
        return ".".join(parts)

    def _issue(
        self,
        *,
        code: str,
        field_path: str | None,
        message: str,
        severity: str,
        source_line: int | None,
        accepted_aliases: list[str],
        scenario_id: str | None = None,
    ) -> dict[str, Any]:
        return {
            "code": code,
            "field_path": field_path,
            "message": message,
            "severity": severity,
            "source_line": source_line,
            "accepted_aliases": accepted_aliases,
            "scenario_id": scenario_id,
        }

    def _scenario_confidence(
        self,
        *,
        scenario: dict[str, Any],
        fields: list[dict[str, Any]],
        issues: list[dict[str, Any]],
        ambiguities: list[dict[str, Any]],
    ) -> dict[str, Any]:
        if fields:
            adjusted_scores = []
            source_scores = []
            for field in fields:
                confidence = float(field.get("confidence") or 0.0)
                source_scores.append(confidence)
                status = str(field.get("validation_status") or "VALID")
                if status == "INVALID":
                    confidence *= 0.45
                elif status == "REVIEW":
                    confidence *= 0.8
                adjusted_scores.append(confidence)
            field_mean = sum(adjusted_scores) / len(adjusted_scores)
            source_quality = sum(source_scores) / len(source_scores)
        else:
            field_mean = 0.35
            source_quality = 0.35

        coverage = self._critical_field_coverage(scenario.get("canonical_config", {}))

        error_count = sum(1 for issue in issues if issue.get("severity") == "ERROR")
        warning_count = sum(1 for issue in issues if issue.get("severity") == "WARNING")
        ambiguity_stats = self._ambiguity_stats(ambiguities)

        error_penalty = min(error_count * 0.14, 0.56)
        warning_penalty = min(warning_count * 0.04, 0.18)
        ambiguity_penalty = min(ambiguity_stats["open_confidence_impact"], 0.34)

        score = field_mean * 0.62 + coverage["coverage_score"] * 0.25 + source_quality * 0.13
        score -= error_penalty
        score -= warning_penalty
        score -= ambiguity_penalty

        if not scenario.get("save_validation", {}).get("allowed"):
            score = min(score, 0.18)
        elif not scenario.get("arm_validation", {}).get("allowed"):
            score = min(score, 0.62)

        score = round(max(0.0, min(score, 1.0)), 4)
        label = self._confidence_label(score)

        summary = "Scenario looks consistent and armable."
        if not scenario.get("save_validation", {}).get("allowed"):
            summary = "Scenario is not saveable as a draft yet."
        elif not scenario.get("arm_validation", {}).get("allowed"):
            summary = "Scenario can be saved but still needs review before arming."
        elif ambiguity_stats["open_count"]:
            summary = "Scenario is strong but still blocked by open ambiguities."
        elif warning_count:
            summary = "Scenario is valid with some warnings to review."

        components = {
            "field_mean": round(field_mean, 4),
            "source_quality": round(source_quality, 4),
            "coverage_score": round(coverage["coverage_score"], 4),
            "critical_field_count": coverage["critical_field_count"],
            "missing_critical_paths": coverage["missing_critical_paths"],
            "error_count": error_count,
            "warning_count": warning_count,
            "open_ambiguity_count": ambiguity_stats["open_count"],
            "blocker_ambiguity_count": ambiguity_stats["blocker_count"],
            "review_ambiguity_count": ambiguity_stats["review_count"],
            "info_ambiguity_count": ambiguity_stats["info_count"],
            "error_penalty": round(error_penalty, 4),
            "warning_penalty": round(warning_penalty, 4),
            "ambiguity_penalty": round(ambiguity_penalty, 4),
        }
        return {
            "score": score,
            "label": label,
            "summary": summary,
            "components": components,
            "drivers": self._confidence_drivers(components),
        }

    def _analysis_confidence(
        self,
        *,
        scenarios: list[dict[str, Any]],
        issues: list[dict[str, Any]],
        ambiguities: list[dict[str, Any]],
    ) -> dict[str, Any]:
        if not scenarios:
            return {
                "score": 0.0,
                "label": "INVALID",
                "summary": "No scenario could be extracted.",
                "components": {
                    "best_scenario_score": 0.0,
                    "scenario_count": 0,
                    "warning_count": 0,
                    "open_ambiguity_count": 0,
                    "ambiguity_penalty": 0.0,
                },
                "drivers": ["No reusable scenario was extracted."],
            }
        scenario_scores = [
            float((scenario.get("confidence") or {}).get("score") or 0.0) for scenario in scenarios
        ]
        best_scenario_score = max(scenario_scores)
        warning_count = sum(1 for issue in issues if issue.get("severity") == "WARNING")
        error_count = sum(1 for issue in issues if issue.get("severity") == "ERROR")
        ambiguity_stats = self._ambiguity_stats(ambiguities)
        ambiguity_penalty = min(ambiguity_stats["open_confidence_impact"], 0.28)
        scenario_penalty = min(max(len(scenarios) - 1, 0) * 0.03, 0.12)
        warning_penalty = min(warning_count * 0.02, 0.1)
        score = best_scenario_score
        score -= ambiguity_penalty
        score -= scenario_penalty
        score -= warning_penalty
        score = round(max(0.0, min(score, 1.0)), 4)
        label = self._confidence_label(score)
        summary = "Primary scenario is coherent."
        if ambiguity_stats["open_count"]:
            summary = "Analysis contains open ambiguities that still require a decision."
        elif len(scenarios) > 1:
            summary = "Multiple scenarios were extracted from the same input."
        elif warning_count:
            summary = "Analysis is valid with review points."
        components = {
            "best_scenario_score": round(best_scenario_score, 4),
            "scenario_count": len(scenarios),
            "scenario_score_spread": round(max(scenario_scores) - min(scenario_scores), 4),
            "error_count": error_count,
            "warning_count": warning_count,
            "open_ambiguity_count": ambiguity_stats["open_count"],
            "blocker_ambiguity_count": ambiguity_stats["blocker_count"],
            "review_ambiguity_count": ambiguity_stats["review_count"],
            "info_ambiguity_count": ambiguity_stats["info_count"],
            "ambiguity_penalty": round(ambiguity_penalty, 4),
            "scenario_penalty": round(scenario_penalty, 4),
            "warning_penalty": round(warning_penalty, 4),
        }
        return {
            "score": score,
            "label": label,
            "summary": summary,
            "components": components,
            "drivers": self._confidence_drivers(components),
        }

    def _critical_field_coverage(self, config: dict[str, Any]) -> dict[str, Any]:
        critical_paths = self._critical_paths_for_config(config)
        if not critical_paths:
            return {
                "coverage_score": 1.0,
                "critical_field_count": 0,
                "missing_critical_paths": [],
            }
        missing = [path for path in critical_paths if not self._path_has_value(config, path)]
        coverage_score = (len(critical_paths) - len(missing)) / len(critical_paths)
        return {
            "coverage_score": coverage_score,
            "critical_field_count": len(critical_paths),
            "missing_critical_paths": missing,
        }

    @staticmethod
    def _critical_paths_for_config(config: dict[str, Any]) -> list[str]:
        setup_type = str(config.get("setup_type") or "")
        setup_role = str(config.get("setup_role") or "ENTRY_AND_MANAGEMENT").upper()
        paths = ["symbol", "setup_type", "mode", "direction", "setup_role"]
        paths.extend(
            [
                "trailing_stop_loss.enabled",
                "trailing_stop_loss.initial_stop",
            ]
        )
        if setup_role != "MANAGEMENT_ONLY":
            paths.extend(
                [
                    "entry.order_type",
                    "risk.max_position_amount_usd",
                    "risk.max_risk_usd",
                    "trailing_stop_loss.broker_order.required_before_entry_transmission",
                ]
            )
        by_type = {
            "breakout_retest": [
                "breakout.daily_close_above",
                "retest.zone_min",
                "retest.zone_max",
            ],
            "aggressive_rebound": [
                "support_zone.min",
                "support_zone.max",
            ],
            "momentum_breakout": [
                "breakout.resistance",
            ],
            "pullback_continuation": [
                "pullback.entry_reference",
            ],
            "range_breakout": [
                "range.low",
                "range.high",
            ],
            "position_management": [
                "position_source.mode",
            ],
        }
        return list(dict.fromkeys([*paths, *by_type.get(setup_type, [])]))

    @staticmethod
    def _path_has_value(config: dict[str, Any], path: str) -> bool:
        if path == "trailing_stop_loss.initial_stop":
            value = _get_nested(config, "trailing_stop_loss.initial_stop")
            return value not in (None, "")
        value = _get_nested(config, path)
        if value in (None, ""):
            return False
        return True

    @staticmethod
    def _ambiguity_stats(ambiguities: list[dict[str, Any]]) -> dict[str, Any]:
        stats = {
            "open_count": 0,
            "blocker_count": 0,
            "review_count": 0,
            "info_count": 0,
            "open_confidence_impact": 0.0,
        }
        for ambiguity in ambiguities:
            if str(ambiguity.get("status") or "").upper() != "OPEN":
                continue
            metadata = (
                ambiguity.get("metadata") if isinstance(ambiguity.get("metadata"), dict) else {}
            )
            severity = str(
                ambiguity.get("severity") or metadata.get("severity") or "REVIEW"
            ).upper()
            if severity not in _AMBIGUITY_PENALTIES:
                severity = "REVIEW"
            impact = _safe_float(
                ambiguity.get("confidence_impact", metadata.get("confidence_impact"))
            )
            if impact is None:
                impact = _AMBIGUITY_PENALTIES[severity]
            stats["open_count"] += 1
            stats["open_confidence_impact"] += max(0.0, float(impact))
            if severity == "BLOCKER":
                stats["blocker_count"] += 1
            elif severity == "INFO":
                stats["info_count"] += 1
            else:
                stats["review_count"] += 1
        stats["open_confidence_impact"] = round(stats["open_confidence_impact"], 4)
        return stats

    @staticmethod
    def _confidence_label(score: float) -> str:
        if score < 0.35:
            return "INVALID"
        if score < 0.6:
            return "REVIEW"
        if score < 0.82:
            return "MEDIUM"
        return "HIGH"

    @staticmethod
    def _confidence_drivers(components: dict[str, Any]) -> list[str]:
        drivers: list[str] = []
        error_count = int(components.get("error_count") or 0)
        warning_count = int(components.get("warning_count") or 0)
        blocker_count = int(components.get("blocker_ambiguity_count") or 0)
        review_count = int(components.get("review_ambiguity_count") or 0)
        missing_paths = components.get("missing_critical_paths")
        if error_count:
            drivers.append(f"{error_count} validation error(s).")
        if blocker_count:
            drivers.append(f"{blocker_count} blocking ambiguity decision(s).")
        if review_count:
            drivers.append(f"{review_count} ambiguity review point(s).")
        if warning_count:
            drivers.append(f"{warning_count} warning(s).")
        if isinstance(missing_paths, list) and missing_paths:
            drivers.append("Missing critical field(s): " + ", ".join(map(str, missing_paths[:4])))
        if not drivers:
            drivers.append("No blocking issue detected.")
        return drivers

    def _accepted_aliases_for_path(self, field_path: str | None) -> list[str]:
        if not field_path:
            return []
        return list(self.accepted_aliases.get(field_path, []))

    def _selected_scenario_id_from_analysis(
        self,
        analysis: dict[str, Any],
        scenarios: list[dict[str, Any]],
        ambiguities: list[dict[str, Any]],
    ) -> str | None:
        scenario_ids = {
            str(item.get("scenario_id") or "")
            for item in scenarios
            if str(item.get("scenario_id") or "")
        }
        for ambiguity in ambiguities:
            if ambiguity.get("field_path") != "scenario_selection":
                continue
            resolution = ambiguity.get("resolution")
            if not isinstance(resolution, dict):
                continue
            option = resolution.get("selected_option")
            if not isinstance(option, dict):
                continue
            resolved_id = str(option.get("scenario_id") or "").strip()
            if resolved_id and resolved_id in scenario_ids:
                return resolved_id
        primary_scenario_id = str(analysis.get("primary_scenario_id") or "").strip()
        if primary_scenario_id and primary_scenario_id in scenario_ids:
            return primary_scenario_id
        for scenario in scenarios:
            if str(scenario.get("scenario_role") or "").upper() == "PRIMARY":
                return str(scenario.get("scenario_id") or "").strip() or None
        if scenarios:
            return str(scenarios[0].get("scenario_id") or "").strip() or None
        return None

    def _select_scenario(
        self,
        analysis: dict[str, Any],
        scenario_id: str | None = None,
    ) -> dict[str, Any]:
        scenarios = analysis.get("scenarios", [])
        requested = str(scenario_id or "").strip()
        if requested:
            for scenario in scenarios:
                if str(scenario.get("scenario_id") or "") == requested:
                    return scenario
            raise KeyError("Scenario not found")
        selected_id = str(analysis.get("selected_scenario_id") or "").strip()
        if selected_id:
            for scenario in scenarios:
                if str(scenario.get("scenario_id") or "") == selected_id:
                    return scenario
        if scenarios:
            return scenarios[0]
        raise KeyError("Scenario not found")

    def _comparison_side(
        self,
        analysis: dict[str, Any],
        scenario: dict[str, Any],
    ) -> dict[str, Any]:
        return {
            "analysis_id": analysis.get("analysis_id"),
            "created_at": analysis.get("created_at"),
            "source_type": analysis.get("source_type"),
            "provider_name": analysis.get("provider_name"),
            "scenario_id": scenario.get("scenario_id"),
            "scenario_name": scenario.get("scenario_name"),
            "scenario_role": scenario.get("scenario_role"),
            "setup_type": scenario.get("setup_type"),
            "status": scenario.get("status"),
            "confidence": scenario.get("confidence") or {},
            "save_validation": scenario.get("save_validation") or {},
            "arm_validation": scenario.get("arm_validation") or {},
        }

    def _validate_setup_analysis(
        self,
        setup_id: str,
        analysis: dict[str, Any],
    ) -> None:
        if str(analysis.get("setup_id") or "") != str(setup_id or ""):
            raise ValueError("Analysis does not belong to the requested setup")

    @staticmethod
    def _count_issues(issues: list[dict[str, Any]], severity: str) -> int:
        return sum(1 for issue in issues if issue.get("severity") == severity)

    def _scenario_name(self, config: dict[str, Any], scenario_role: Any) -> str:
        role = str(scenario_role or "PRIMARY").replace("_", " ").title()
        setup_type = str(config.get("setup_type") or "scenario").replace("_", " ")
        return f"{role} {setup_type}".strip()

    def _attach_issue_source_lines(
        self,
        issues: list[dict[str, Any]],
        fields: list[dict[str, Any]],
    ) -> None:
        line_by_path = {
            field["canonical_path"]: field.get("source_line_start")
            for field in fields
            if field.get("source_line_start") is not None
        }
        for issue in issues:
            if issue.get("source_line") is not None:
                continue
            field_path = issue.get("field_path")
            if not field_path:
                continue
            if field_path in line_by_path:
                issue["source_line"] = line_by_path[field_path]
                continue
            for path, line in line_by_path.items():
                if path.startswith(f"{field_path}.") or field_path.startswith(f"{path}."):
                    issue["source_line"] = line
                    break

    @staticmethod
    def _parse_json_text(text: str) -> Any | None:
        stripped = str(text or "").strip()
        if not stripped or stripped[0] not in {"{", "["}:
            return None
        try:
            return json.loads(stripped)
        except json.JSONDecodeError:
            return None


def _flatten_leaves(
    payload: dict[str, Any],
    parent: str = "",
) -> list[tuple[str, Any]]:
    items: list[tuple[str, Any]] = []
    for key, value in payload.items():
        path = f"{parent}.{key}" if parent else str(key)
        if isinstance(value, dict):
            items.extend(_flatten_leaves(value, path))
        elif isinstance(value, list):
            for index, item in enumerate(value):
                item_path = f"{path}[{index}]"
                if isinstance(item, dict):
                    items.extend(_flatten_leaves(item, item_path))
                else:
                    items.append((item_path, item))
        else:
            items.append((path, value))
    return items


def _get_nested(payload: dict[str, Any], path: str) -> Any:
    cursor: Any = payload
    for part in path.split("."):
        if not isinstance(cursor, dict) or part not in cursor:
            return None
        cursor = cursor[part]
    return cursor


def _set_nested_config_value(payload: dict[str, Any], path: str, value: Any) -> None:
    parts = [part for part in str(path or "").split(".") if part]
    if not parts:
        return
    cursor = payload
    for part in parts[:-1]:
        existing = cursor.get(part)
        if not isinstance(existing, dict):
            existing = {}
            cursor[part] = existing
        cursor = existing
    cursor[parts[-1]] = value


def _stringify(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False)


def _find_source_reference(
    source_lines: list[str],
    *,
    raw_key: str,
    raw_value: Any,
) -> dict[str, Any]:
    raw_key_text = str(raw_key or "")
    raw_value_text = str(raw_value)
    for index, line in enumerate(source_lines, start=1):
        if raw_key_text and raw_key_text in line:
            return {
                "source_text": line.strip(),
                "source_line_start": index,
                "source_line_end": index,
            }
    for index, line in enumerate(source_lines, start=1):
        if raw_value_text and raw_value_text in line:
            return {
                "source_text": line.strip(),
                "source_line_start": index,
                "source_line_end": index,
            }
    return {"source_text": None, "source_line_start": None, "source_line_end": None}


def _flatten_config_for_compare(payload: dict[str, Any]) -> dict[str, Any]:
    return {path: value for path, value in _flatten_leaves(payload)}


def _compare_values_equal(left: Any, right: Any) -> bool:
    return left == right


def _safe_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _ambiguity_kind_from_issue_code(code: str) -> str:
    normalized = str(code or "VALIDATION_ISSUE").upper()
    if normalized == "TRAILING_STOP_INITIAL_STOP_REQUIRED_BEFORE_ARMING":
        return "MISSING_REQUIRED_FIELD"
    return normalized


def _dedupe_issues(issues: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[tuple[Any, ...]] = set()
    unique: list[dict[str, Any]] = []
    for issue in issues:
        key = (
            issue.get("code"),
            issue.get("field_path"),
            issue.get("message"),
            issue.get("severity"),
            issue.get("scenario_id"),
        )
        if key in seen:
            continue
        seen.add(key)
        unique.append(issue)
    return unique
