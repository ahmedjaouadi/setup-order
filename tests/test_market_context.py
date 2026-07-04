from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace

from app.api import routes_market_context
from app.market_context.repository import MarketContextRepository
from app.market_context.service import MarketContextService
from app.storage.database import Database


class FakeTradingRepository:
    def __init__(self, setups, events) -> None:
        self._setups = setups
        self._events = events

    def list_setups(self):
        return self._setups

    def list_events(self, limit=100, setup_id=None, symbol=None, level=None, event_type=None):
        rows = self._events
        if event_type:
            rows = [event for event in rows if event["event_type"] == event_type]
        if symbol:
            rows = [event for event in rows if event.get("symbol") == symbol.upper()]
        return rows[:limit]


class FakeMarketRepository:
    def __init__(self, metadata=None) -> None:
        self._metadata = metadata or {}

    def list_symbol_metadata(self):
        return self._metadata

    def upcoming_earnings(self, symbol=None, limit=50):
        return []

    def upcoming_dividends(self, symbol=None, limit=50):
        return []

    def economic_events(self, limit=100):
        return []


def setup_row(setup_id="setup-flnc", symbol="FLNC", enabled=True):
    return {
        "setup_id": setup_id,
        "symbol": symbol,
        "setup_type": "momentum_breakout",
        "enabled": enabled,
        "status": "WAITING_ENTRY_SIGNAL",
        "config": {
            "enabled": True,
            "monitoring": {
                "watch_enabled": True,
                "auto_execution_enabled": enabled,
            },
        },
    }


def quote_event(symbol, price, close):
    return {
        "timestamp": "2026-06-14T14:00:00+00:00",
        "event_type": "stock_quote",
        "symbol": symbol,
        "data": {
            "price": price,
            "close": close,
            "market_data_source": "live",
        },
    }


class MarketContextServiceTests(unittest.TestCase):
    def test_heatmap_scores_relative_strength_and_auto_badges(self) -> None:
        service = MarketContextService(
            market_repository=FakeMarketRepository(
                {
                    "FLNC": {
                        "symbol": "FLNC",
                        "sector": "Industrials",
                        "industry": "Energy Storage",
                        "sector_etf": "XLI",
                    }
                }
            ),
            trading_repository=FakeTradingRepository(
                setups=[setup_row()],
                events=[
                    quote_event("FLNC", 103, 100),
                    quote_event("XLI", 101, 100),
                    quote_event("SPY", 100.5, 100),
                    {
                        "timestamp": "2026-06-14T14:00:01+00:00",
                        "event_type": "stock_analysis",
                        "symbol": "FLNC",
                        "setup_id": "setup-flnc",
                        "data": {
                            "processed": [
                                {
                                    "setup_id": "setup-flnc",
                                    "action": "ENTRY_READY",
                                    "opportunity_score": {"percent": 97.5},
                                }
                            ]
                        },
                    },
                ],
            ),
            symbol_metadata_path=Path("missing-symbol-metadata.yaml"),
        )

        heatmap = service.heatmap()
        node = heatmap["nodes"][0]
        detail = service.symbol_detail("FLNC")

        self.assertEqual(node["id"], "FLNC")
        self.assertEqual(node["status"], "STRONG_CONTEXT")
        self.assertIn("AUTO_ALLOWED", node["badges"])
        self.assertIn("ENTRY_READY", node["badges"])
        self.assertEqual(detail["relative_strength_vs_sector"], 2.0)
        self.assertEqual(detail["setup_proximity_percent"], 97.5)

    def test_watch_only_setup_remains_visible(self) -> None:
        service = MarketContextService(
            market_repository=FakeMarketRepository(
                {"NOK": {"symbol": "NOK", "sector": "Technology"}}
            ),
            trading_repository=FakeTradingRepository(
                setups=[setup_row(setup_id="setup-nok", symbol="NOK", enabled=False)],
                events=[quote_event("NOK", 10, 10)],
            ),
            symbol_metadata_path=Path("missing-symbol-metadata.yaml"),
        )

        node = service.heatmap()["nodes"][0]

        self.assertEqual(node["id"], "NOK")
        self.assertIn("WATCH_ONLY", node["badges"])
        self.assertFalse(node["auto_execution_enabled"])

    def test_sector_can_be_inferred_from_sector_etf(self) -> None:
        service = MarketContextService(
            market_repository=FakeMarketRepository(
                {
                    "ARM": {
                        "symbol": "ARM",
                        "company_name": "Arm Holdings",
                        "sector_etf": "XLK",
                    }
                }
            ),
            trading_repository=FakeTradingRepository(
                setups=[setup_row(setup_id="setup-arm", symbol="ARM", enabled=True)],
                events=[quote_event("ARM", 100, 99), quote_event("XLK", 101, 100)],
            ),
            symbol_metadata_path=Path("missing-symbol-metadata.yaml"),
        )

        node = service.heatmap()["nodes"][0]
        detail = service.symbol_detail("ARM")

        self.assertEqual(node["sector"], "Technology")
        self.assertEqual(detail["sector"], "Technology")
        self.assertEqual(detail["sector_etf"], "XLK")
        self.assertEqual(detail["metadata_status"], "SECTOR_PROVIDER_MISSING")

    def test_manual_override_marks_metadata_status(self) -> None:
        with TemporaryDirectory() as folder:
            symbol_metadata = Path(folder) / "symbol_metadata.yaml"
            symbol_metadata.write_text(
                "\n".join(
                    [
                        "symbol_overrides:",
                        "  POWI:",
                        "    sector: Technology",
                        "    industry: Semiconductors",
                        "    sector_etf: SMH",
                    ]
                ),
                encoding="utf-8",
            )
            service = MarketContextService(
                market_repository=FakeMarketRepository(),
                trading_repository=FakeTradingRepository(
                    setups=[setup_row(setup_id="setup-powi", symbol="POWI", enabled=True)],
                    events=[quote_event("POWI", 80, 79), quote_event("SMH", 101, 100)],
                ),
                symbol_metadata_path=symbol_metadata,
            )

            detail = service.symbol_detail("POWI")

        self.assertEqual(detail["sector"], "Technology")
        self.assertEqual(detail["metadata_status"], "SECTOR_MANUAL_OVERRIDE")
        self.assertEqual(detail["metadata_source"], "manual_override")


class MarketContextRepositoryTests(unittest.TestCase):
    def test_symbol_metadata_round_trip(self) -> None:
        with TemporaryDirectory() as folder:
            database = Database(Path(folder) / "state.sqlite")
            database.initialize()
            repository = MarketContextRepository(database)

            repository.upsert_symbol_metadata(
                {
                    "symbol": "NVTS",
                    "company_name": "Navitas Semiconductor",
                    "sector": "Technology",
                    "industry": "Semiconductors",
                    "sector_etf": "SMH",
                    "custom_priority": 3,
                }
            )

            metadata = repository.list_symbol_metadata()
            database.close()

        self.assertEqual(metadata["NVTS"]["sector"], "Technology")
        self.assertEqual(metadata["NVTS"]["sector_etf"], "SMH")
        self.assertEqual(metadata["NVTS"]["custom_priority"], 3)


class MarketContextRouteTests(unittest.IsolatedAsyncioTestCase):
    async def test_heatmap_route_uses_market_context_service(self) -> None:
        class Service:
            def heatmap(self, view="WATCHLIST"):
                return {"view": view, "nodes": []}

        request = SimpleNamespace(
            app=SimpleNamespace(state=SimpleNamespace(market_context=Service()))
        )

        result = await routes_market_context.market_context_heatmap(request, view="AUTO_ALLOWED")

        self.assertEqual(result["view"], "AUTO_ALLOWED")


if __name__ == "__main__":
    unittest.main()
