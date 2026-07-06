from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from typing import Any

from app.models import utc_now_iso
from app.opportunities.shortlist_service import (
    LEVELS_INCOMPLETE,
    LEVELS_READY,
    STOP_SOURCE_ATR_FALLBACK,
    STOP_SOURCE_SCENARIO,
    OpportunityShortlistService,
)
from app.storage.database import Database
from app.storage.repositories import TradingRepository

SETTINGS: dict[str, Any] = {
    "opportunities": {
        "shortlist": {
            "max_items": 25,
            "min_score": 55,
            "include_blocked_with_reason": True,
            "expire_after_minutes": {"15m": 60, "1h": 240, "1d": 1440},
        }
    },
}


def _opportunity(opportunity_id: str, **overrides: Any) -> dict[str, Any]:
    payload = {
        "score": {
            "overall_score": 80,
            "components": {
                "volume_score": 80,
                "liquidity_score": 80,
                "forecast_alignment_score": 50,
            },
        },
        "selection": {"inputs": {"price": 10.0, "previous_high": 10.0}},
        "config": {},
        "market_snapshot": {},
        "liquidity_filter": {"blocked": False, "issues": []},
        "reason": "test",
    }
    payload.update(overrides.pop("payload", {}))
    return {
        "opportunity_id": opportunity_id,
        "symbol": "LUNR",
        "opportunity_type": "momentum_breakout",
        "timeframe": "15m",
        "status": "DETECTED",
        "score": 80,
        "detected_at": utc_now_iso(),
        "payload": payload,
        **overrides,
    }


class ShortlistLevelsTests(unittest.TestCase):
    """Etape 12: every shortlist row carries consultative entry + stop levels."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.database = Database(Path(self.tmp.name) / "state.sqlite")
        self.database.initialize()
        self.repository = TradingRepository(self.database)
        self.service = OpportunityShortlistService(self.repository, SETTINGS)

    def tearDown(self) -> None:
        self.database.close()
        self.tmp.cleanup()

    def _single_item(self) -> dict[str, Any]:
        shortlist = self.service.build()
        self.assertEqual(len(shortlist["items"]), 1)
        return shortlist["items"][0]

    def test_levels_come_from_existing_scenario_draft(self) -> None:
        self.repository.upsert_opportunity(_opportunity("opp_draft"))
        self.repository.add_scenario_draft(
            {
                "scenario_id": "LUNR_MOMENTUM_BREAKOUT_draft_1",
                "source_opportunity_id": "opp_draft",
                "symbol": "LUNR",
                "setup_type": "momentum_breakout",
                "status": "DRAFT",
                "entry": {"trigger_price": 10.5, "limit_price": 10.55},
                "trailing_stop_loss": {"initial_stop": 9.8},
                "ambiguities": [],
            }
        )

        item = self._single_item()

        self.assertEqual(item["levels_source"], "SCENARIO_DRAFT")
        self.assertEqual(item["suggested_entry"], 10.5)
        self.assertEqual(item["suggested_limit"], 10.55)
        self.assertEqual(item["suggested_stop"], 9.8)
        self.assertEqual(item["stop_source"], STOP_SOURCE_SCENARIO)
        self.assertEqual(item["risk_per_share"], 0.7)
        self.assertEqual(item["levels_status"], LEVELS_READY)

    def test_levels_computed_on_the_fly_without_draft(self) -> None:
        self.repository.upsert_opportunity(
            _opportunity(
                "opp_mapper",
                payload={
                    "config": {"trailing_stop_loss": {"initial_stop": 9.5}},
                },
            )
        )

        item = self._single_item()

        self.assertEqual(item["levels_source"], "MAPPER")
        # trigger = previous_high 10.0 + default offset 0.02
        self.assertEqual(item["suggested_entry"], 10.02)
        self.assertEqual(item["suggested_stop"], 9.5)
        self.assertEqual(item["stop_source"], STOP_SOURCE_SCENARIO)
        self.assertEqual(item["risk_per_share"], 0.52)
        self.assertEqual(item["levels_status"], LEVELS_READY)
        # Nothing was persisted by the on-the-fly mapper.
        self.assertEqual(self.repository.list_scenario_drafts(), [])

    def test_missing_structural_stop_falls_back_to_marked_atr_stop(self) -> None:
        self.repository.upsert_opportunity(
            _opportunity(
                "opp_atr",
                payload={"market_snapshot": {"atr_15m": 0.2}},
            )
        )

        item = self._single_item()

        self.assertEqual(item["suggested_entry"], 10.02)
        # stop = entry - 1.5 x ATR = 10.02 - 0.30
        self.assertEqual(item["suggested_stop"], 9.72)
        self.assertEqual(item["stop_source"], STOP_SOURCE_ATR_FALLBACK)
        self.assertEqual(item["levels_status"], LEVELS_READY)
        # The structural ambiguity stays visible even with the ATR fallback.
        fields = [entry.get("field") for entry in item["levels_ambiguities"]]
        self.assertIn("trailing_stop_loss.initial_stop", fields)

    def test_no_levels_at_all_is_explicit_incomplete_without_invented_values(self) -> None:
        self.repository.upsert_opportunity(
            _opportunity(
                "opp_incomplete",
                payload={"selection": {"inputs": {}}, "market_snapshot": {}},
            )
        )

        item = self._single_item()

        self.assertIsNone(item["suggested_entry"])
        self.assertIsNone(item["suggested_stop"])
        self.assertIsNone(item["risk_per_share"])
        self.assertIsNone(item["stop_source"])
        self.assertEqual(item["levels_status"], LEVELS_INCOMPLETE)
        self.assertGreaterEqual(len(item["levels_ambiguities"]), 1)

    def test_atr_multiplier_is_configurable(self) -> None:
        settings = {
            **SETTINGS,
            "opportunities": {
                "shortlist": {
                    **SETTINGS["opportunities"]["shortlist"],
                    "atr_stop_multiplier": 2.0,
                }
            },
        }
        service = OpportunityShortlistService(self.repository, settings)
        self.repository.upsert_opportunity(
            _opportunity(
                "opp_atr_k",
                payload={"market_snapshot": {"atr_15m": 0.2}},
            )
        )

        item = service.build()["items"][0]

        # stop = 10.02 - 2.0 x 0.2
        self.assertEqual(item["suggested_stop"], 9.62)


if __name__ == "__main__":
    unittest.main()
