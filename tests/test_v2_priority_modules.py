from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from app.event_bus import EventBus
from app.forecasting.forecast_repository import ForecastRepository
from app.model_lab import ModelLabService
from app.models import utc_now_iso
from app.observability import DecisionTrace, ObservabilityService
from app.opportunities import OpportunityScannerService
from app.scoring import SetupQualityEngine
from app.storage.database import Database
from app.storage.event_store import EventStore
from app.storage.repositories import TradingRepository


class V2PriorityModuleTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.database = Database(Path(self.tmp.name) / "state.sqlite")
        self.database.initialize()
        self.repository = TradingRepository(self.database)
        self.event_store = EventStore(self.repository)
        self.forecast_repository = ForecastRepository(self.database)
        self.settings = {
            "opportunities": {
                "shortlist": {
                    "max_items": 25,
                    "min_score": 55,
                    "include_blocked_with_reason": True,
                    "expire_after_minutes": {"15m": 60, "1h": 240, "1d": 1440},
                }
            },
            "risk": {
                "max_position_amount_usd": 250,
                "max_risk_per_trade_usd": 15,
            },
        }
        self.scoring = SetupQualityEngine(
            self.repository,
            self.forecast_repository,
            self.settings,
        )
        self.scanner = OpportunityScannerService(
            self.repository,
            self.scoring,
            self.event_store,
            self.settings,
        )
        self.model_lab = ModelLabService(self.repository, self.settings)
        self.observability = ObservabilityService(self.repository)

    def tearDown(self) -> None:
        self.database.close()
        self.tmp.cleanup()

    def test_shortlist_filters_low_score_and_keeps_blocked_with_reason(self) -> None:
        self.repository.upsert_opportunity(_opportunity("opp_high", 80, "DETECTED"))
        self.repository.upsert_opportunity(_opportunity("opp_low", 40, "DETECTED"))
        blocked = _opportunity("opp_blocked", 39, "REJECTED")
        blocked["payload"]["liquidity_filter"] = {
            "blocked": True,
            "issues": ["spread_too_wide"],
        }
        self.repository.upsert_opportunity(blocked)

        shortlist = self.scanner.shortlist()

        self.assertEqual([item["opportunity_id"] for item in shortlist["items"]], ["opp_high"])
        self.assertEqual(
            shortlist["blocked_opportunities"][0]["blocking_reasons"],
            ["spread_too_wide"],
        )

    def test_generate_scenario_from_momentum_breakout_opportunity_is_unarmed_draft(self) -> None:
        opportunity = _opportunity("opp_momo", 82, "DETECTED")
        opportunity["opportunity_type"] = "momentum_breakout"
        opportunity["payload"]["config"] = {
            "setup_type": "momentum_breakout",
            "breakout": {"resistance": 10.0},
            "entry": {"trigger_offset": 0.02, "limit_offset": 0.05},
            "risk": {},
        }
        self.repository.upsert_opportunity(opportunity)

        result = self.scanner.generate_scenario_draft("opp_momo")

        scenario = result["scenario"]
        self.assertEqual(scenario["status"], "DRAFT")
        self.assertFalse(scenario["selection"]["armed"])
        self.assertEqual(scenario["entry"]["trigger_price"], 10.02)
        self.assertEqual(scenario["entry"]["limit_price"], 10.07)
        self.assertEqual(scenario["ambiguities"][0]["field"], "trailing_stop_loss.initial_stop")
        self.assertTrue(self.repository.list_scenario_drafts(source_opportunity_id="opp_momo"))

    def test_expiration_policy_marks_old_opportunity_expired(self) -> None:
        old = _opportunity("opp_old", 90, "DETECTED")
        old["detected_at"] = "2020-01-01T00:00:00+00:00"
        self.repository.upsert_opportunity(old)

        shortlist = self.scanner.shortlist()

        self.assertEqual(shortlist["recently_expired"][0]["opportunity_id"], "opp_old")
        self.assertEqual(self.repository.get_opportunity("opp_old")["status"], "EXPIRED")

    def test_backtest_mvp_does_not_fill_stp_lmt_gap_above_limit(self) -> None:
        run = self.model_lab.run_backtest_mvp(
            {
                "symbol": "GAP",
                "entry_trigger": 10.0,
                "limit_price": 10.1,
                "stop_loss": 9.5,
                "candles": [
                    {
                        "timestamp": "2026-06-20T14:30:00+00:00",
                        "open": 10.5,
                        "high": 10.7,
                        "low": 10.2,
                        "close": 10.6,
                    }
                ],
            }
        )
        events = self.model_lab.backtest_events(run["backtest_id"])

        self.assertEqual(run["metrics"]["number_of_trades"], 0)
        self.assertIn("SIM_ORDER_PLACED", {event["event_type"] for event in events})
        self.assertNotIn("SIM_ORDER_FILLED", {event["event_type"] for event in events})

    def test_model_scorecard_insufficient_data_is_not_error(self) -> None:
        scorecard = self.model_lab.run_timesfm_benchmark(
            {
                "symbol": "SHORT",
                "historical_bars": [{"close": 10.0}],
                "min_history_bars": 30,
            }
        )

        self.assertEqual(scorecard["selection_decision"], "INSUFFICIENT_DATA")
        self.assertEqual(scorecard["sample_size"], 1)

    def test_decision_trace_filters_by_entity(self) -> None:
        trace_id = self.observability.decision_trace_service.create(
            DecisionTrace(
                entity_type="OPPORTUNITY",
                entity_id="opp_high",
                opportunity_id="opp_high",
                symbol="HIGH",
                decision_type="SCORE_COMPUTED",
                decision="OK",
                human_message="Score computed for opportunity.",
            )
        )

        traces = self.observability.decision_traces_for_entity("OPPORTUNITY", "opp_high")

        self.assertEqual(traces[0]["trace_id"], trace_id)
        self.assertEqual(traces[0]["human_message"], "Score computed for opportunity.")


def _opportunity(opportunity_id: str, score: float, status: str) -> dict:
    return {
        "opportunity_id": opportunity_id,
        "symbol": opportunity_id.split("_")[-1].upper(),
        "opportunity_type": "momentum_breakout",
        "timeframe": "15m",
        "status": status,
        "score": score,
        "detected_at": utc_now_iso(),
        "payload": {
            "score": {
                "overall_score": score,
                "components": {
                    "volume_score": score,
                    "liquidity_score": score,
                    "forecast_alignment_score": 50,
                },
            },
            "selection": {
                "inputs": {
                    "price": 10.0,
                    "previous_high": 10.0,
                }
            },
            "liquidity_filter": {"blocked": False, "issues": []},
            "reason": "test",
        },
    }


if __name__ == "__main__":
    unittest.main()
