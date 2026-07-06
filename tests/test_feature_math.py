from __future__ import annotations

import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from app.features.store import FeatureStore
from app.forecasting.forecast_repository import ForecastRepository
from app.opportunities import OpportunityScannerService
from app.opportunity_scanner.feature_math import (
    atr_pct,
    dist_vwap_pct,
    price_above,
    rth_session_bars,
    session_vwap,
)
from app.scoring import SetupQualityEngine
from app.storage.database import Database
from app.storage.event_store import EventStore
from app.storage.repositories import TradingRepository
from app.utils.market_hours import US_EQUITY_TIMEZONE

# VWAP worked out by hand: typical prices 100, 102, 104 weighted 1000/2000/1000
# -> (100*1000 + 102*2000 + 104*1000) / 4000 = 102.0
VWAP_BARS: list[dict[str, Any]] = [
    {"high": 101.0, "low": 99.0, "close": 100.0, "volume": 1000},
    {"high": 103.0, "low": 101.0, "close": 102.0, "volume": 2000},
    {"high": 105.0, "low": 103.0, "close": 104.0, "volume": 1000},
]


def _ny_bar(hour: int, minute: int, *, days_ago: int = 0, **fields: Any) -> dict[str, Any]:
    day = datetime.now(UTC).astimezone(US_EQUITY_TIMEZONE) - timedelta(days=days_ago)
    moment = day.replace(hour=hour, minute=minute, second=0, microsecond=0)
    bar = {"high": 101.0, "low": 99.0, "close": 100.0, "volume": 1000}
    bar.update(fields)
    bar["date"] = moment.isoformat()
    return bar


class AtrPctTests(unittest.TestCase):
    def test_atr_15m_over_price(self) -> None:
        self.assertEqual(atr_pct({"price": 100.0, "atr_15m": 2.0}), 2.0)

    def test_falls_back_to_atr_1h(self) -> None:
        self.assertEqual(atr_pct({"price": 200.0, "atr_1h": 3.0}), 1.5)

    def test_missing_ingredients_yield_none(self) -> None:
        self.assertIsNone(atr_pct({"price": 100.0}))
        self.assertIsNone(atr_pct({"atr_15m": 2.0}))
        self.assertIsNone(atr_pct({"price": 0, "atr_15m": 2.0}))
        self.assertIsNone(atr_pct({"price": 100.0, "atr_15m": -1}))
        self.assertIsNone(atr_pct({}))
        self.assertIsNone(atr_pct(None))  # type: ignore[arg-type]


class SessionVwapTests(unittest.TestCase):
    def test_vwap_on_synthetic_series(self) -> None:
        self.assertEqual(session_vwap(VWAP_BARS), 102.0)

    def test_zero_volume_yields_none_never_zero(self) -> None:
        bars = [{"high": 101.0, "low": 99.0, "close": 100.0, "volume": 0}]
        self.assertIsNone(session_vwap(bars))

    def test_no_usable_bar_yields_none(self) -> None:
        self.assertIsNone(session_vwap([]))
        self.assertIsNone(session_vwap(None))
        self.assertIsNone(session_vwap([{"close": 100.0}]))
        self.assertIsNone(session_vwap(["not a bar"]))


class DistVwapPctTests(unittest.TestCase):
    def test_price_below_vwap_is_negative(self) -> None:
        self.assertEqual(dist_vwap_pct(100.0, 102.0), round((100 - 102) / 102 * 100, 4))
        self.assertLess(dist_vwap_pct(100.0, 102.0), 0)

    def test_price_above_vwap_is_positive(self) -> None:
        self.assertEqual(dist_vwap_pct(103.02, 102.0), 1.0)

    def test_missing_inputs_yield_none(self) -> None:
        self.assertIsNone(dist_vwap_pct(None, 102.0))
        self.assertIsNone(dist_vwap_pct(100.0, None))
        self.assertIsNone(dist_vwap_pct(100.0, 0.0))


class RthSessionBarsTests(unittest.TestCase):
    def test_keeps_only_todays_rth_bars(self) -> None:
        bars = [
            _ny_bar(9, 15),  # pre-market -> dropped
            _ny_bar(9, 30),  # first RTH bar -> kept
            _ny_bar(15, 45),  # last RTH bar -> kept
            _ny_bar(16, 0),  # close -> dropped
            _ny_bar(10, 0, days_ago=1),  # previous session -> dropped
            {"high": 1, "low": 1, "close": 1, "volume": 1},  # no date -> dropped
        ]
        kept = rth_session_bars(bars)
        self.assertEqual(len(kept), 2)

    def test_never_raises_on_garbage(self) -> None:
        self.assertEqual(rth_session_bars(None), [])
        self.assertEqual(rth_session_bars("bars"), [])
        self.assertEqual(rth_session_bars([{"date": "not a date"}]), [])


class PriceAboveTests(unittest.TestCase):
    def test_tri_state(self) -> None:
        self.assertTrue(price_above(100.0, 95.0))
        self.assertFalse(price_above(100.0, 105.0))
        self.assertFalse(price_above(100.0, 100.0))
        self.assertIsNone(price_above(None, 95.0))
        self.assertIsNone(price_above(100.0, None))


class FeatureStoreF1Tests(unittest.TestCase):
    def test_features_from_quote_exposes_atr_pct(self) -> None:
        features = FeatureStore._features_from_quote({"price": 100.0, "atr_15m": 2.0})
        self.assertEqual(features["atr_pct"], 2.0)

    def test_historical_sma_50_needs_fifty_closes(self) -> None:
        bars_49 = [{"close": 100.0 + i} for i in range(49)]
        self.assertIsNone(FeatureStore._features_from_bars(bars_49)["historical_sma_50"])
        bars_50 = [{"close": 100.0} for _ in range(50)]
        self.assertEqual(FeatureStore._features_from_bars(bars_50)["historical_sma_50"], 100.0)


class ScannerSnapshotF1Tests(unittest.TestCase):
    """The scanner snapshot carries the F1 features (TODO 7.1-7.5)."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.database = Database(Path(self.tmp.name) / "state.sqlite")
        self.database.initialize()
        self.repository = TradingRepository(self.database)
        self.event_store = EventStore(self.repository)
        forecast_repository = ForecastRepository(self.database)
        settings: dict[str, Any] = {}
        scoring = SetupQualityEngine(self.repository, forecast_repository, settings)
        self.scanner = OpportunityScannerService(
            self.repository, scoring, self.event_store, settings
        )

    def tearDown(self) -> None:
        self.database.close()
        self.tmp.cleanup()

    def _snapshot(self, quote: dict[str, Any]) -> dict[str, Any]:
        candidate = {"symbol": "AAPL", "sources": ["test"], "metadata": {}, "quote": quote}
        return self.scanner._context_snapshot_from_candidate(candidate, quote)

    def test_full_quote_produces_every_f1_feature(self) -> None:
        bars = [
            _ny_bar(10, 0, high=101.0, low=99.0, close=100.0, volume=1000),
            _ny_bar(10, 15, high=103.0, low=101.0, close=102.0, volume=2000),
            _ny_bar(10, 30, high=105.0, low=103.0, close=104.0, volume=1000),
        ]
        quote = {
            "price": 100.0,
            "atr_15m": 2.0,
            "volume_ratio": 1.6,
            "historical_bars": bars,
            "historical_ema_20": 95.0,
            "historical_sma_50": 105.0,
            "timestamp": "2026-07-06T14:00:00+00:00",  # 10:00 ET -> MORNING
        }
        snapshot = self._snapshot(quote)
        self.assertEqual(snapshot["rvol"], 1.6)
        self.assertEqual(snapshot["atr_pct"], 2.0)
        self.assertEqual(snapshot["vwap"], 102.0)
        self.assertEqual(snapshot["dist_vwap_pct"], round((100 - 102) / 102 * 100, 4))
        self.assertEqual(snapshot["time_bucket"], "MORNING")
        self.assertTrue(snapshot["price_above_ema20"])
        self.assertFalse(snapshot["price_above_sma50"])

    def test_canonical_rvol_wins_over_aliases(self) -> None:
        snapshot = self._snapshot({"price": 100.0, "rvol": 2.5, "volume_ratio": 1.0})
        self.assertEqual(snapshot["rvol"], 2.5)

    def test_missing_ingredients_degrade_to_none(self) -> None:
        snapshot = self._snapshot({"price": 100.0, "timestamp": "2026-07-06T14:00:00+00:00"})
        self.assertIsNone(snapshot["atr_pct"])
        self.assertIsNone(snapshot["vwap"])
        self.assertIsNone(snapshot["dist_vwap_pct"])
        self.assertIsNone(snapshot["price_above_ema20"])
        self.assertIsNone(snapshot["price_above_sma50"])
        self.assertIsNone(snapshot["rvol"])
        # time_bucket is always present (skills.md 25bis).
        self.assertEqual(snapshot["time_bucket"], "MORNING")


if __name__ == "__main__":
    unittest.main()
