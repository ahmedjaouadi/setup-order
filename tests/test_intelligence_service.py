from __future__ import annotations

import tempfile
import unittest
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

from fastapi import HTTPException

from app.api import routes_intelligence
from app.broker.tws_connector import SimulatedBrokerConnector
from app.engine.trading_engine import TradingEngine
from app.intelligence.api_models import (
    AnalyzeRequestModel,
    CompareAnalysesRequestModel,
    ResolveAmbiguityRequestModel,
    RollbackAnalysisRequestModel,
)
from app.intelligence.repository import IntelligenceRepository
from app.intelligence.service import IntelligenceService
from app.settings import DEFAULT_CONFIG, Settings
from app.storage.database import Database
from app.storage.repositories import TradingRepository
from tests.test_setups import valid_breakout_config, valid_momentum_config


class IntelligenceServiceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.database_path = Path(self.tmp.name) / "state.sqlite"
        self.database = Database(self.database_path)
        self.database.initialize()
        self.repository = IntelligenceRepository(self.database)
        self.service = IntelligenceService(
            repository=self.repository,
            defaults=deepcopy(DEFAULT_CONFIG),
        )

    async def asyncTearDown(self) -> None:
        self.database.close()
        self.tmp.cleanup()

    async def test_persists_analysis_and_reloads_after_restart(self) -> None:
        payload = {
            "payload": {
                "setup_id": "UEC_20260611_001",
                "symbol": "UEC",
                "enabled": True,
                "mode": "paper",
                "setup_type": "breakout_retest",
                "setup_role": "ENTRY_AND_MANAGEMENT",
                "direction": "long",
                "daily_close_above": 14.50,
                "retest_zone_min": 14.10,
                "retest_zone_max": 14.50,
                "entry_enabled": True,
                "entry_order_type": "STP_LMT",
                "trigger_offset": 0.02,
                "limit_offset": 0.05,
                "SL": 13.85,
                "budget": 200,
                "risque": 15,
            }
        }

        created = await self.service.analyze(payload, persist=True)
        self.assertTrue(created["persisted"])
        analysis_id = created["analysis_id"]

        self.database.close()
        reopened_db = Database(self.database_path)
        reopened_db.initialize()
        reopened_repo = IntelligenceRepository(reopened_db)
        reopened_service = IntelligenceService(
            repository=reopened_repo,
            defaults=deepcopy(DEFAULT_CONFIG),
        )
        reloaded = reopened_service.get_latest_for_setup("UEC_20260611_001")

        self.assertIsNotNone(reloaded)
        self.assertEqual(reloaded["analysis_id"], analysis_id)
        self.assertEqual(reloaded["schema_version"], "intelligence_v1")
        self.assertEqual(reloaded["parser_version"], "intelligence_parser_v1")
        self.assertIn("confidence", reloaded)
        self.assertGreater(reloaded["confidence"]["score"], 0)
        reopened_db.close()

    async def test_persists_canonical_field_and_provenance(self) -> None:
        result = await self.service.analyze(
            {
                "payload": {
                    "setup_id": "UEC_20260611_002",
                    "symbol": "UEC",
                    "enabled": True,
                    "mode": "paper",
                    "setup_type": "breakout_retest",
                    "setup_role": "ENTRY_AND_MANAGEMENT",
                    "direction": "long",
                    "daily_close_above": 14.50,
                    "retest_zone_min": 14.10,
                    "retest_zone_max": 14.50,
                    "entry_enabled": True,
                    "entry_order_type": "STP_LMT",
                    "trigger_offset": 0.02,
                    "limit_offset": 0.05,
                    "SL": "13.85",
                    "budget": "200",
                    "risque": "15",
                }
            },
            persist=True,
        )

        stop_field = next(
            field
            for field in result["extracted_fields"]
            if field["canonical_path"] == "trailing_stop_loss.initial_stop"
        )
        self.assertEqual(stop_field["parsed_value"], 13.85)
        self.assertEqual(stop_field["extraction_method"], "ALIAS_MAPPING")
        self.assertIsNotNone(stop_field["source_text"])
        self.assertIsNotNone(stop_field["source_line_start"])
        self.assertEqual(stop_field["validation_status"], "VALID")

    async def test_persists_multi_scenarios_and_ambiguity(self) -> None:
        first = valid_breakout_config()
        first["setup_id"] = "UEC_MULTI_001"
        second = valid_momentum_config()
        second["setup_id"] = "NOK_MULTI_001"
        result = await self.service.analyze(
            {
                "payload": {
                    "scenarios": [
                        {
                            "scenario_name": "Scenario principal",
                            "scenario_role": "PRIMARY",
                            "config": first,
                        },
                        {
                            "scenario_name": "Scenario prudent",
                            "scenario_role": "PRUDENT",
                            "config": second,
                        },
                    ]
                }
            },
            persist=True,
        )

        self.assertTrue(result["persisted"])
        self.assertEqual(len(result["scenarios"]), 2)
        self.assertEqual(len(result["ambiguities"]), 1)
        self.assertGreater(result["confidence"]["score"], 0)
        stored = self.repository.list_scenarios(result["analysis_id"])
        self.assertEqual(len(stored), 2)
        self.assertEqual(
            {item["scenario_role"] for item in stored},
            {"PRIMARY", "PRUDENT"},
        )
        self.assertTrue(all("confidence" in item for item in stored))
        ambiguities = self.repository.list_ambiguities(result["analysis_id"])
        self.assertEqual(len(ambiguities), 1)
        self.assertEqual(ambiguities[0]["field_path"], "scenario_selection")
        self.assertEqual(ambiguities[0]["kind"], "SCENARIO_SELECTION")
        self.assertEqual(ambiguities[0]["severity"], "REVIEW")
        self.assertGreater(ambiguities[0]["confidence_impact"], 0)

    async def test_enriches_validation_ambiguities_and_confidence_components(self) -> None:
        result = await self.service.analyze(
            {
                "payload": {
                    "setup_id": "UEC_AMBIGUITY_001",
                    "symbol": "UEC",
                    "enabled": True,
                    "mode": "paper",
                    "setup_type": "breakout_retest",
                    "setup_role": "ENTRY_AND_MANAGEMENT",
                    "direction": "long",
                    "daily_close_above": 14.50,
                    "retest_zone_min": 14.10,
                    "retest_zone_max": 14.50,
                    "entry_enabled": True,
                    "budget": 200,
                    "risque": 15,
                }
            },
            persist=True,
        )

        stop_ambiguity = next(
            item
            for item in result["ambiguities"]
            if item["field_path"] == "trailing_stop_loss.initial_stop"
        )
        self.assertEqual(stop_ambiguity["kind"], "MISSING_REQUIRED_FIELD")
        self.assertEqual(stop_ambiguity["severity"], "BLOCKER")
        self.assertEqual(stop_ambiguity["metadata"]["detection_method"], "VALIDATION_ISSUE")
        self.assertGreater(stop_ambiguity["confidence_impact"], 0)
        self.assertTrue(stop_ambiguity["options"])

        confidence = result["scenarios"][0]["confidence"]
        self.assertIn("coverage_score", confidence["components"])
        self.assertIn("source_quality", confidence["components"])
        self.assertIn("ambiguity_penalty", confidence["components"])
        self.assertGreater(confidence["components"]["ambiguity_penalty"], 0)
        self.assertTrue(confidence["drivers"])

        reloaded = self.service.get_analysis(result["analysis_id"])
        self.assertIsNotNone(reloaded)
        persisted = next(
            item
            for item in reloaded["ambiguities"]
            if item["field_path"] == "trailing_stop_loss.initial_stop"
        )
        self.assertEqual(persisted["metadata"]["kind"], "MISSING_REQUIRED_FIELD")
        self.assertEqual(persisted["severity"], "BLOCKER")

    async def test_resolving_scenario_selection_updates_selected_scenario(self) -> None:
        first = valid_breakout_config()
        first["setup_id"] = "UEC_SELECT_001"
        second = valid_momentum_config()
        second["setup_id"] = "NOK_SELECT_001"
        result = await self.service.analyze(
            {
                "payload": {
                    "scenarios": [
                        {"scenario_name": "Primary", "config": first},
                        {"scenario_name": "Alternative", "config": second},
                    ]
                }
            },
            persist=True,
        )
        ambiguity = next(
            item for item in result["ambiguities"] if item["field_path"] == "scenario_selection"
        )
        target = result["scenarios"][1]

        resolved = await self.service.resolve_ambiguity(
            result["analysis_id"],
            ambiguity["ambiguity_id"],
            {"selected_option": {"scenario_id": target["scenario_id"]}},
        )

        self.assertIsNotNone(resolved)
        self.assertEqual(
            resolved["active_effect"]["selected_scenario_id"],
            target["scenario_id"],
        )
        self.assertEqual(
            resolved["analysis"]["selected_scenario_id"],
            target["scenario_id"],
        )
        selected = [
            scenario for scenario in resolved["analysis"]["scenarios"] if scenario["selected"]
        ]
        self.assertEqual(len(selected), 1)
        self.assertEqual(selected[0]["scenario_id"], target["scenario_id"])

    async def test_resolving_field_ambiguity_creates_resolution_revision(self) -> None:
        result = await self.service.analyze(
            {
                "payload": {
                    "setup_id": "UEC_RESOLVE_FIELD_001",
                    "symbol": "UEC",
                    "enabled": True,
                    "mode": "paper",
                    "setup_type": "breakout_retest",
                    "setup_role": "ENTRY_AND_MANAGEMENT",
                    "direction": "long",
                    "daily_close_above": 14.50,
                    "retest_zone_min": 14.10,
                    "retest_zone_max": 14.50,
                    "entry_enabled": True,
                    "budget": 200,
                    "risque": 15,
                }
            },
            persist=True,
        )
        ambiguity = next(
            item
            for item in result["ambiguities"]
            if item["field_path"] == "trailing_stop_loss.initial_stop"
        )

        resolved = await self.service.resolve_ambiguity(
            result["analysis_id"],
            ambiguity["ambiguity_id"],
            {
                "selected_option": {
                    "action": "UPDATE_FIELD",
                    "field_path": "trailing_stop_loss.initial_stop",
                },
                "field_value": "13.85",
            },
        )

        self.assertIsNotNone(resolved)
        self.assertEqual(resolved["ambiguity"]["status"], "RESOLVED")
        self.assertEqual(resolved["active_effect"]["type"], "FIELD_PATCH")
        revision = resolved["resolution_analysis"]
        self.assertIsNotNone(revision)
        self.assertTrue(revision["persisted"])
        self.assertEqual(revision["source_type"], "RESOLUTION")
        self.assertEqual(revision["previous_analysis_id"], result["analysis_id"])
        self.assertEqual(
            revision["scenarios"][0]["canonical_config"]["trailing_stop_loss"]["initial_stop"],
            13.85,
        )
        self.assertTrue(revision["arm_validation"]["allowed"])

    async def test_save_validation_can_pass_while_arm_validation_fails(self) -> None:
        result = await self.service.analyze(
            {
                "payload": {
                    "setup_id": "UEC_DRAFT_001",
                    "symbol": "UEC",
                    "enabled": True,
                    "mode": "paper",
                    "setup_type": "breakout_retest",
                    "setup_role": "ENTRY_AND_MANAGEMENT",
                    "direction": "long",
                    "daily_close_above": 14.50,
                    "retest_zone_min": 14.10,
                    "retest_zone_max": 14.50,
                    "entry_enabled": True,
                    "budget": 200,
                    "risque": 15,
                }
            },
            persist=True,
        )

        self.assertTrue(result["save_validation"]["allowed"])
        self.assertFalse(result["arm_validation"]["allowed"])
        self.assertTrue(result["persisted"])
        self.assertTrue(
            any(
                issue["field_path"] == "trailing_stop_loss.initial_stop"
                for issue in result["issues"]
            )
        )

    async def test_idempotence_reuses_existing_analysis(self) -> None:
        payload = {
            "payload": valid_breakout_config(),
            "idempotency_key": "analysis-uec-idem-1",
        }

        first = await self.service.analyze(payload, persist=True)
        second = await self.service.analyze(payload, persist=True)

        self.assertEqual(first["analysis_id"], second["analysis_id"])
        self.assertTrue(second["reused"])
        analyses = self.repository.list_analyses_for_setup(valid_breakout_config()["setup_id"])
        self.assertEqual(len(analyses), 1)

    async def test_lists_lightweight_analysis_summaries(self) -> None:
        config = valid_breakout_config()
        config["setup_id"] = "UEC_SUMMARY_001"
        created = await self.service.analyze({"payload": config}, persist=True)

        summaries = self.service.list_summaries_for_setup(config["setup_id"])

        self.assertEqual(len(summaries), 1)
        self.assertEqual(summaries[0]["analysis_id"], created["analysis_id"])
        self.assertFalse(summaries[0]["detail_loaded"])
        self.assertEqual(summaries[0]["scenario_count"], 1)
        self.assertIn("open_ambiguity_count", summaries[0])
        self.assertNotIn("scenarios", summaries[0])
        self.assertNotIn("extracted_fields", summaries[0])

    async def test_lists_paginated_analysis_summaries(self) -> None:
        config = valid_breakout_config()
        config["setup_id"] = "UEC_SUMMARY_PAGE_001"

        first = await self.service.analyze({"payload": config}, persist=True)

        second_config = deepcopy(config)
        second_config["trigger_offset"] = 0.03
        second = await self.service.analyze(
            {"payload": second_config, "force_new_revision": True},
            persist=True,
        )

        third_config = deepcopy(config)
        third_config["trigger_offset"] = 0.04
        third = await self.service.analyze(
            {"payload": third_config, "force_new_revision": True},
            persist=True,
        )

        first_page = self.service.list_summaries_for_setup(config["setup_id"], limit=2, offset=0)
        second_page = self.service.list_summaries_for_setup(config["setup_id"], limit=2, offset=2)

        self.assertEqual(
            [item["analysis_id"] for item in first_page],
            [third["analysis_id"], second["analysis_id"]],
        )
        self.assertEqual([item["analysis_id"] for item in second_page], [first["analysis_id"]])
        self.assertEqual(self.service.count_analyses_for_setup(config["setup_id"]), 3)

    async def test_compare_analyses_reports_changed_fields_and_revision_chain(self) -> None:
        first_config = valid_breakout_config()
        first_config["setup_id"] = "UEC_COMPARE_001"
        second_config = deepcopy(first_config)
        second_config["trailing_stop_loss"]["initial_stop"] = 13.95
        second_config["retest"]["zone_min"] = 14.2

        first = await self.service.analyze({"payload": first_config}, persist=True)
        second = await self.service.analyze(
            {"payload": second_config, "force_new_revision": True},
            persist=True,
        )

        self.assertEqual(second["previous_analysis_id"], first["analysis_id"])

        comparison = self.service.compare_analyses(
            first_config["setup_id"],
            first["analysis_id"],
            second["analysis_id"],
        )

        self.assertGreaterEqual(comparison["summary"]["changed_count"], 2)
        changed_paths = {item["field_path"] for item in comparison["field_changes"]}
        self.assertIn("trailing_stop_loss.initial_stop", changed_paths)
        self.assertIn("retest.zone_min", changed_paths)
        self.assertEqual(comparison["left"]["analysis_id"], first["analysis_id"])
        self.assertEqual(comparison["right"]["analysis_id"], second["analysis_id"])


class FailingIntelligenceRepository(IntelligenceRepository):
    def _insert_ambiguities(self, conn: object, ambiguities: list[dict[str, object]]) -> None:
        raise RuntimeError("boom")


class IntelligenceTransactionTests(unittest.IsolatedAsyncioTestCase):
    async def test_rolls_back_transaction_if_child_insert_fails(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        try:
            database = Database(Path(tmp.name) / "state.sqlite")
            database.initialize()
            repository = FailingIntelligenceRepository(database)
            service = IntelligenceService(repository=repository, defaults=deepcopy(DEFAULT_CONFIG))

            with self.assertRaises(RuntimeError):
                await service.analyze(
                    {
                        "payload": {
                            "scenarios": [
                                {"config": valid_breakout_config()},
                                {"config": valid_momentum_config()},
                            ]
                        }
                    },
                    persist=True,
                )

            self.assertEqual(
                database.execute("SELECT COUNT(*) AS count FROM semantic_analyses").fetchone()[
                    "count"
                ],
                0,
            )
            self.assertEqual(
                database.execute("SELECT COUNT(*) AS count FROM extracted_scenarios").fetchone()[
                    "count"
                ],
                0,
            )
            self.assertEqual(
                database.execute("SELECT COUNT(*) AS count FROM extracted_fields").fetchone()[
                    "count"
                ],
                0,
            )
        finally:
            database.close()
            tmp.cleanup()


class IntelligenceApiTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.database = Database(Path(self.tmp.name) / "state.sqlite")
        self.database.initialize()
        repository = IntelligenceRepository(self.database)
        service = IntelligenceService(repository=repository, defaults=deepcopy(DEFAULT_CONFIG))
        self.request = SimpleNamespace(
            app=SimpleNamespace(state=SimpleNamespace(intelligence=service))
        )

    async def asyncTearDown(self) -> None:
        self.database.close()
        self.tmp.cleanup()

    async def test_returns_structured_api_error(self) -> None:
        with self.assertRaises(HTTPException) as ctx:
            await routes_intelligence.analyze_intelligence(
                self.request,
                AnalyzeRequestModel(payload={"enabled": True}),
            )

        self.assertEqual(ctx.exception.status_code, 422)
        detail = ctx.exception.detail
        self.assertEqual(detail["code"], "INTELLIGENCE_VALIDATION_FAILED")
        self.assertIn("save_validation", detail)
        self.assertIn("issues", detail)
        self.assertTrue(detail["issues"])

    async def test_compare_setup_analyses_route_returns_field_deltas(self) -> None:
        first_config = valid_breakout_config()
        first_config["setup_id"] = "UEC_COMPARE_API_001"
        second_config = deepcopy(first_config)
        second_config["trailing_stop_loss"]["initial_stop"] = 13.75

        first = await self.request.app.state.intelligence.analyze(
            {"payload": first_config},
            persist=True,
        )
        second = await self.request.app.state.intelligence.analyze(
            {"payload": second_config, "force_new_revision": True},
            persist=True,
        )

        comparison = await routes_intelligence.compare_setup_analyses(
            self.request,
            first_config["setup_id"],
            CompareAnalysesRequestModel(
                left_analysis_id=first["analysis_id"],
                right_analysis_id=second["analysis_id"],
            ),
        )

        self.assertGreaterEqual(comparison["summary"]["changed_count"], 1)
        changed_paths = {item["field_path"] for item in comparison["field_changes"]}
        self.assertIn("trailing_stop_loss.initial_stop", changed_paths)

    async def test_list_setup_analyses_route_can_return_summaries(self) -> None:
        config = valid_breakout_config()
        config["setup_id"] = "UEC_SUMMARY_API_001"
        await self.request.app.state.intelligence.analyze(
            {"payload": config},
            persist=True,
        )

        result = await routes_intelligence.list_setup_analyses(
            self.request,
            config["setup_id"],
            summary=True,
        )

        self.assertEqual(len(result["items"]), 1)
        self.assertFalse(result["items"][0]["detail_loaded"])
        self.assertEqual(result["items"][0]["scenario_count"], 1)
        self.assertNotIn("scenarios", result["items"][0])

    async def test_list_setup_analyses_route_supports_pagination(self) -> None:
        config = valid_breakout_config()
        config["setup_id"] = "UEC_SUMMARY_API_PAGE_001"

        await self.request.app.state.intelligence.analyze(
            {"payload": config},
            persist=True,
        )
        second_config = deepcopy(config)
        second_config["trigger_offset"] = 0.03
        second = await self.request.app.state.intelligence.analyze(
            {"payload": second_config, "force_new_revision": True},
            persist=True,
        )
        third_config = deepcopy(config)
        third_config["trigger_offset"] = 0.04
        await self.request.app.state.intelligence.analyze(
            {"payload": third_config, "force_new_revision": True},
            persist=True,
        )

        result = await routes_intelligence.list_setup_analyses(
            self.request,
            config["setup_id"],
            summary=True,
            limit=1,
            offset=1,
        )

        self.assertEqual(result["limit"], 1)
        self.assertEqual(result["offset"], 1)
        self.assertEqual(result["total_count"], 3)
        self.assertTrue(result["has_more"])
        self.assertEqual(len(result["items"]), 1)
        self.assertEqual(result["items"][0]["analysis_id"], second["analysis_id"])
        self.assertNotIn("scenarios", result["items"][0])

    async def test_resolve_ambiguity_route_can_create_resolution_revision(self) -> None:
        created = await self.request.app.state.intelligence.analyze(
            {
                "payload": {
                    "setup_id": "UEC_RESOLVE_API_001",
                    "symbol": "UEC",
                    "enabled": True,
                    "mode": "paper",
                    "setup_type": "breakout_retest",
                    "setup_role": "ENTRY_AND_MANAGEMENT",
                    "direction": "long",
                    "daily_close_above": 14.50,
                    "retest_zone_min": 14.10,
                    "retest_zone_max": 14.50,
                    "entry_enabled": True,
                    "budget": 200,
                    "risque": 15,
                }
            },
            persist=True,
        )
        ambiguity = next(
            item
            for item in created["ambiguities"]
            if item["field_path"] == "trailing_stop_loss.initial_stop"
        )

        result = await routes_intelligence.resolve_analysis_ambiguity(
            self.request,
            created["analysis_id"],
            ambiguity["ambiguity_id"],
            ResolveAmbiguityRequestModel(
                selected_option={
                    "action": "UPDATE_FIELD",
                    "field_path": "trailing_stop_loss.initial_stop",
                },
                field_value="13.85",
            ),
        )

        self.assertEqual(result["active_effect"]["type"], "FIELD_PATCH")
        self.assertIsNotNone(result["resolution_analysis"])
        self.assertEqual(result["resolution_analysis"]["source_type"], "RESOLUTION")


class IntelligenceRollbackApiTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        config = deepcopy(DEFAULT_CONFIG)
        config["storage"]["database_file"] = str(root / "state.sqlite")
        config["storage"]["setups_folder"] = str(root / "setups")
        config["storage"]["logs_folder"] = str(root / "logs")
        self.settings = Settings.from_dict(config)
        self.database = Database(self.settings.database_file)
        self.database.initialize()
        self.trading_repository = TradingRepository(self.database)
        intelligence_repository = IntelligenceRepository(self.database)
        self.service = IntelligenceService(
            repository=intelligence_repository,
            defaults=deepcopy(config),
        )
        self.broker = SimulatedBrokerConnector()
        await self.broker.connect()
        self.engine = TradingEngine(
            settings=self.settings,
            repository=self.trading_repository,
            broker=self.broker,
        )
        self.request = SimpleNamespace(
            app=SimpleNamespace(
                state=SimpleNamespace(
                    intelligence=self.service,
                    engine=self.engine,
                )
            )
        )

    async def asyncTearDown(self) -> None:
        await self.broker.disconnect()
        self.database.close()
        self.tmp.cleanup()

    async def test_rollback_route_restores_setup_and_records_new_revision(self) -> None:
        first_config = valid_breakout_config()
        first_config["setup_id"] = "UEC_ROLLBACK_001"
        first_config["trailing_stop_loss"]["initial_stop"] = 13.85
        second_config = deepcopy(first_config)
        second_config["trailing_stop_loss"]["initial_stop"] = 14.05
        second_config["retest"]["zone_max"] = 14.65

        self.assertTrue((await self.engine.save_setup(first_config))["ok"])
        first_analysis = await self.service.analyze({"payload": first_config}, persist=True)

        self.assertTrue((await self.engine.save_setup(second_config))["ok"])
        second_analysis = await self.service.analyze(
            {"payload": second_config, "force_new_revision": True},
            persist=True,
        )

        result = await routes_intelligence.rollback_setup_analysis(
            self.request,
            first_config["setup_id"],
            RollbackAnalysisRequestModel(analysis_id=first_analysis["analysis_id"]),
        )

        self.assertTrue(result["ok"])
        self.assertTrue(result["history_persisted"])
        self.assertEqual(
            result["rollback_analysis"]["previous_analysis_id"],
            second_analysis["analysis_id"],
        )
        self.assertEqual(result["rollback_analysis"]["source_type"], "ROLLBACK")
        restored = self.trading_repository.get_setup(first_config["setup_id"])
        self.assertEqual(
            restored["config"]["trailing_stop_loss"]["initial_stop"],
            13.85,
        )
        latest = self.service.get_latest_for_setup(first_config["setup_id"])
        self.assertIsNotNone(latest)
        self.assertEqual(
            latest["analysis_id"],
            result["rollback_analysis"]["analysis_id"],
        )


if __name__ == "__main__":
    unittest.main()
