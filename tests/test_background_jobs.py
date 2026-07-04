from __future__ import annotations

import unittest
from types import SimpleNamespace

from app.background_jobs import (
    auto_evaluate_forecast_accuracy,
    auto_rebuild_opportunity_shortlist,
    auto_recalculate_forecasts,
)
from app.models import MarketSnapshot


class _FakeForecastService:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []
        self.config = SimpleNamespace(timeframe="15m")

    async def forecast_ensemble(
        self,
        symbol: str,
        *,
        timeframe: str | None = None,
        setup_id: str | None = None,
        force_refresh: bool = False,
    ) -> dict[str, object]:
        call = {
            "symbol": symbol,
            "timeframe": timeframe,
            "setup_id": setup_id,
            "force_refresh": force_refresh,
        }
        self.calls.append(call)
        return {
            "status": "OK",
            "generated_at": "2026-06-23T00:00:00+00:00",
            "forecast_id": f"forecast_{setup_id}",
        }


class _FakeRepository:
    def list_setups(self) -> list[dict[str, object]]:
        return [
            {
                "setup_id": "setup_1",
                "symbol": "FLNC",
                "status": "WAITING_ENTRY_SIGNAL",
                "config": {"timeframes": {"signal": "1h"}},
            },
            {
                "setup_id": "setup_2",
                "symbol": "NOK",
                "status": "CLOSED",
                "config": {"timeframe": "15m"},
            },
        ]


class _FakeEngine:
    def runtime_state(self) -> dict[str, str]:
        return {"connection": "CONNECTED"}


class _FakeScanner:
    def __init__(self) -> None:
        self.calls = 0

    def rebuild_shortlist(self) -> dict[str, object]:
        self.calls += 1
        return {
            "ok": True,
            "scan": {"ok": True, "summary": {"shortlisted": 1}},
            "shortlist": {"items": [{"symbol": "FLNC"}]},
            "items": [{"symbol": "FLNC"}],
        }


class _FakeAccuracyRepository:
    def __init__(self) -> None:
        self.due_calls = 0

    def due_outcomes(self, _due_at: str) -> list[dict[str, object]]:
        self.due_calls += 1
        return [{"outcome_id": "out_1", "symbol": "FLNC"}]


class _FakeAccuracyService:
    def __init__(self) -> None:
        self.evaluate_prices: dict[str, object] | None = None
        self.rebuild_calls = 0

    def evaluate_due(self, prices: dict[str, object]) -> dict[str, object]:
        self.evaluate_prices = prices
        return {
            "evaluated_count": 1,
            "evaluated": [{"outcome_id": "out_1", "status": "EVALUATED"}],
            "skipped_without_price": [],
        }

    def rebuild_scorecards(self) -> list[dict[str, object]]:
        self.rebuild_calls += 1
        return [{"scorecard_id": "score_1"}]


class _FakeMarketData:
    def latest(self, symbol: str) -> MarketSnapshot | None:
        if symbol.upper() != "FLNC":
            return None
        return MarketSnapshot(
            symbol="FLNC",
            price=104.0,
            close=104.0,
            high=105.0,
            low=101.0,
            historical_bars=[{"close": 101.0}, {"close": 103.0}, {"close": 104.0}],
        )


class BackgroundJobTests(unittest.IsolatedAsyncioTestCase):
    async def test_auto_recalculate_forecasts_only_refreshes_active_setups(self) -> None:
        forecast_service = _FakeForecastService()
        app = SimpleNamespace(
            state=SimpleNamespace(
                forecast=forecast_service,
                repository=_FakeRepository(),
                engine=_FakeEngine(),
            )
        )

        summary = await auto_recalculate_forecasts(app)

        self.assertTrue(summary["ok"])
        self.assertEqual(summary["setup_count"], 1)
        self.assertEqual(summary["successful_count"], 1)
        self.assertEqual(summary["failed_count"], 0)
        self.assertEqual(len(forecast_service.calls), 1)
        self.assertEqual(
            forecast_service.calls[0],
            {
                "symbol": "FLNC",
                "timeframe": "1h",
                "setup_id": "setup_1",
                "force_refresh": True,
            },
        )

    async def test_auto_rebuild_opportunity_shortlist_runs_the_scanner(self) -> None:
        scanner = _FakeScanner()
        app = SimpleNamespace(state=SimpleNamespace(opportunity_scanner=scanner))

        summary = await auto_rebuild_opportunity_shortlist(app)

        self.assertTrue(summary["ok"])
        self.assertEqual(scanner.calls, 1)
        self.assertEqual(summary["items"], [{"symbol": "FLNC"}])

    async def test_auto_evaluate_forecast_accuracy_updates_due_outcomes(self) -> None:
        accuracy = _FakeAccuracyService()
        accuracy_repository = _FakeAccuracyRepository()
        app = SimpleNamespace(
            state=SimpleNamespace(
                forecast_accuracy=accuracy,
                forecast_accuracy_repository=accuracy_repository,
                repository=_FakeRepository(),
                engine=SimpleNamespace(market_data=_FakeMarketData()),
            )
        )

        summary = await auto_evaluate_forecast_accuracy(app)

        self.assertTrue(summary["ok"])
        self.assertEqual(summary["due_count"], 1)
        self.assertEqual(summary["priced_symbol_count"], 1)
        self.assertEqual(summary["evaluated_count"], 1)
        self.assertEqual(summary["scorecard_count"], 1)
        self.assertEqual(accuracy.rebuild_calls, 1)
        self.assertEqual(
            accuracy.evaluate_prices,
            {
                "FLNC": {
                    "price": 104.0,
                    "high": 105.0,
                    "low": 101.0,
                    "path": [101.0, 103.0, 104.0],
                }
            },
        )


if __name__ == "__main__":
    unittest.main()
