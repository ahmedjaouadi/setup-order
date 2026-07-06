from __future__ import annotations

import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from app.forecasting.forecast_repository import ForecastRepository
from app.opportunities import OpportunityScannerService
from app.scoring import SetupQualityEngine
from app.storage.database import Database
from app.storage.event_store import EventStore
from app.storage.repositories import TradingRepository

# The staleness gate compares the snapshot timestamp to the wall clock, so a
# hardcoded "fresh" timestamp rots as time passes: derive it from now instead.
FRESH = (datetime.now(UTC) - timedelta(seconds=10)).isoformat()
STALE = "2020-01-01T00:00:00+00:00"  # decades old -> always stale


def _candidate(quote: dict[str, Any]) -> dict[str, Any]:
    return {
        "symbol": "AAPL",
        "sources": ["scanner"],
        "metadata": {"sector": "TECH"},
        "quote": {"symbol": "AAPL", "timeframe": "15m", **quote},
    }


def _healthy_quote(**overrides: Any) -> dict[str, Any]:
    quote = {
        "price": 100.0,
        "open": 99.0,
        "high": 101.0,
        "low": 98.5,
        "close": 100.0,
        "bid": 99.98,
        "ask": 100.02,
        "volume": 5_000_000,
        "volume_ratio": 1.6,
        "spread_pct": 0.04,
        "timestamp": FRESH,
    }
    quote.update(overrides)
    return quote


class DataQualityGateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.database = Database(Path(self.tmp.name) / "state.sqlite")
        self.database.initialize()
        self.repository = TradingRepository(self.database)
        self.event_store = EventStore(self.repository)
        self.forecast_repository = ForecastRepository(self.database)
        self.settings: dict[str, Any] = {"risk": {"max_risk_per_trade_usd": 15}}
        self.scoring = SetupQualityEngine(self.repository, self.forecast_repository, self.settings)
        self.scanner = OpportunityScannerService(
            self.repository, self.scoring, self.event_store, self.settings
        )

    def tearDown(self) -> None:
        self.database.close()
        self.tmp.cleanup()

    def _run(self, quote: dict[str, Any]) -> dict[str, Any]:
        return self.scanner._opportunity_from_candidate(_candidate(quote), self.scanner.config())

    def _gate_traces(self) -> list[dict[str, Any]]:
        return self.repository.list_decision_traces(decision_type="SCANNER_GATE", symbol="AAPL")

    def _outcome_count(self) -> int:
        rows = self.database.execute("SELECT COUNT(*) AS n FROM detection_outcomes").fetchone()
        return int(rows["n"])

    def test_stale_snapshot_is_paused_with_reason_code(self) -> None:
        opportunity = self._run(_healthy_quote(timestamp=STALE))
        self.assertEqual(opportunity["status"], "REJECTED")
        self.assertEqual(opportunity["payload"]["data_quality"]["status"], "PAUSED")
        self.assertEqual(opportunity["payload"]["data_quality"]["reason_code"], "STALE_DATA")
        # Exactly one qualified SCANNER_GATE trace, no double-tracing by liquidity.
        traces = self._gate_traces()
        self.assertEqual(len(traces), 1)
        self.assertEqual(traces[0]["final_decision"], "PAUSED:STALE_DATA")
        # No detection outcome is ever born on suspect data.
        self.assertEqual(self._outcome_count(), 0)

    def test_incoherent_ohlc_is_missing_market_data(self) -> None:
        opportunity = self._run(_healthy_quote(high=90.0, low=110.0, timestamp=None))
        self.assertEqual(opportunity["status"], "REJECTED")
        self.assertEqual(
            opportunity["payload"]["data_quality"]["reason_code"], "MISSING_MARKET_DATA"
        )
        self.assertEqual(self._outcome_count(), 0)

    def test_inverted_bid_ask_is_blocked(self) -> None:
        opportunity = self._run(_healthy_quote(bid=100.10, ask=99.90, timestamp=None))
        self.assertEqual(opportunity["status"], "REJECTED")
        self.assertEqual(
            opportunity["payload"]["data_quality"]["reason_code"], "MISSING_MARKET_DATA"
        )

    def test_missing_price_is_blocked(self) -> None:
        opportunity = self._run({"volume": 5_000_000, "bid": 99.9, "ask": 100.1, "timestamp": None})
        self.assertEqual(opportunity["status"], "REJECTED")
        self.assertEqual(opportunity["payload"]["data_quality"]["status"], "PAUSED")

    def test_liquidity_block_records_no_outcome_and_traces_gate(self) -> None:
        # Non-regression: a candidate that passes the quality gate but fails the
        # liquidity filter must never spawn a detection outcome, and its refusal
        # is traced as a qualified SCANNER_GATE (skills.md 2.5).
        opportunity = self._run(_healthy_quote(spread_pct=0.9))
        self.assertEqual(opportunity["status"], "REJECTED")
        self.assertTrue(opportunity["payload"]["liquidity_filter"]["blocked"])
        traces = self._gate_traces()
        self.assertEqual(len(traces), 1)
        self.assertEqual(traces[0]["final_decision"], "NO_GO:SPREAD_TOO_WIDE")
        self.assertEqual(self._outcome_count(), 0)

    def test_healthy_snapshot_passes_gate(self) -> None:
        opportunity = self._run(_healthy_quote())
        self.assertNotIn("data_quality", opportunity["payload"])
        self.assertFalse(opportunity["payload"]["liquidity_filter"]["blocked"])
        # No PAUSED gate trace for a clean snapshot.
        self.assertEqual(self._gate_traces(), [])


if __name__ == "__main__":
    unittest.main()
