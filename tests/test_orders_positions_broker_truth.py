from __future__ import annotations

import unittest
from types import SimpleNamespace

from app.api import routes_orders, routes_positions
from app.engine.broker_reality import (
    orders_broker_truth_overlay,
    positions_broker_truth_overlay,
)
from app.engine.trading_engine import TradingEngine


def connected_report(rows: list[dict]) -> dict:
    return {"broker_connected": True, "rows": rows}


def entry_order() -> dict:
    return {
        "id": "ord_entry",
        "setup_id": "LUNR_20260630_001",
        "symbol": "LUNR",
        "side": "BUY",
        "order_type": "STP_LMT",
        "quantity": 6,
        "status": "SUBMITTED",
    }


def stop_order() -> dict:
    return {
        "id": "ord_stop",
        "setup_id": "LUNR_20260630_001",
        "symbol": "LUNR",
        "side": "SELL",
        "order_type": "STP",
        "quantity": 6,
        "stop_price": 18.05,
        "status": "SUBMITTED",
    }


def reality_row() -> dict:
    return {
        "setup_id": "LUNR_20260630_001",
        "symbol": "LUNR",
        "broker_entry_order_status": "TRANSMITTED",
        "broker_stop_order_status": "TRANSMITTED",
        "broker_sync_age_seconds": 2,
        "position_quantity": 6,
        "market_price": 21.0,
        "protection_status": "POSITION_OPEN_STOP_ACTIVE",
    }


class OrdersBrokerTruthOverlayTests(unittest.TestCase):
    def test_orders_are_marked_broker_reality_when_connected_and_matched(self) -> None:
        overlaid = orders_broker_truth_overlay(
            [entry_order(), stop_order()],
            connected_report([reality_row()]),
        )

        self.assertTrue(all(item["source"] == "BROKER_REALITY" for item in overlaid))
        self.assertTrue(all(item["broker_verified"] for item in overlaid))
        entry = next(item for item in overlaid if item["id"] == "ord_entry")
        stop = next(item for item in overlaid if item["id"] == "ord_stop")
        self.assertEqual(entry["broker_status"], "TRANSMITTED")
        self.assertEqual(stop["broker_status"], "TRANSMITTED")

    def test_orders_are_marked_local_only_when_broker_disconnected(self) -> None:
        overlaid = orders_broker_truth_overlay(
            [entry_order()],
            {"broker_connected": False, "rows": [reality_row()]},
        )

        self.assertEqual(overlaid[0]["source"], "LOCAL_ONLY")
        self.assertFalse(overlaid[0]["broker_verified"])

    def test_orders_are_marked_local_only_when_report_missing(self) -> None:
        overlaid = orders_broker_truth_overlay([entry_order()], {})

        self.assertEqual(overlaid[0]["source"], "LOCAL_ONLY")
        self.assertFalse(overlaid[0]["broker_verified"])


class PositionsBrokerTruthOverlayTests(unittest.TestCase):
    def test_positions_are_marked_broker_reality_when_connected_and_matched(self) -> None:
        overlaid = positions_broker_truth_overlay(
            [{"symbol": "LUNR", "quantity": 6}],
            connected_report([reality_row()]),
        )

        self.assertEqual(overlaid[0]["source"], "BROKER_REALITY")
        self.assertTrue(overlaid[0]["broker_verified"])
        self.assertEqual(overlaid[0]["broker_position_quantity"], 6)
        self.assertEqual(overlaid[0]["protection_status"], "POSITION_OPEN_STOP_ACTIVE")

    def test_positions_are_marked_local_only_when_report_missing(self) -> None:
        overlaid = positions_broker_truth_overlay([{"symbol": "LUNR", "quantity": 6}], {})

        self.assertEqual(overlaid[0]["source"], "LOCAL_ONLY")
        self.assertFalse(overlaid[0]["broker_verified"])


class MergePositionSnapshotsTests(unittest.TestCase):
    """Broker is the source of truth for the Positions table once connected."""

    def local_position(self, **overrides: object) -> dict:
        return {
            "symbol": "LUNR",
            "setup_id": "LUNR_20260630_001",
            "quantity": 6,
            "average_price": 20.0,
            "current_price": 21.0,
            "current_stop": 18.05,
            **overrides,
        }

    def broker_position(self, **overrides: object) -> dict:
        return {
            "symbol": "LUNR",
            "quantity": 6,
            "average_price": 20.1,
            "current_price": 21.2,
            "unrealized_pnl": 6.6,
            "source": "broker",
            **overrides,
        }

    def test_local_orphan_hidden_when_broker_connected(self) -> None:
        merged = TradingEngine._merge_position_snapshots(
            [self.local_position(symbol="GHOST")],
            [self.broker_position()],
            True,
        )

        symbols = [row["symbol"] for row in merged]
        self.assertEqual(symbols, ["LUNR"])

    def test_local_orphan_kept_when_broker_disconnected(self) -> None:
        merged = TradingEngine._merge_position_snapshots(
            [self.local_position(symbol="GHOST")],
            [],
            False,
        )

        self.assertEqual([row["symbol"] for row in merged], ["GHOST"])

    def test_broker_row_enriched_with_local_stop_and_setup(self) -> None:
        broker_row = self.broker_position()
        broker_row.pop("current_stop", None)
        merged = TradingEngine._merge_position_snapshots(
            [self.local_position()],
            [broker_row],
            True,
        )

        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0]["current_stop"], 18.05)
        self.assertEqual(merged[0]["setup_id"], "LUNR_20260630_001")
        self.assertEqual(merged[0]["average_price"], 20.1)

    def test_broker_only_position_visible_without_local_row(self) -> None:
        merged = TradingEngine._merge_position_snapshots(
            [],
            [self.broker_position()],
            True,
        )

        self.assertEqual([row["symbol"] for row in merged], ["LUNR"])

    def test_disconnected_merge_keeps_previous_overlay_behaviour(self) -> None:
        merged = TradingEngine._merge_position_snapshots(
            [self.local_position()],
            [self.broker_position()],
            False,
        )

        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0]["average_price"], 20.1)
        self.assertEqual(merged[0]["current_stop"], 18.05)


class OrdersPositionsApiSourceTests(unittest.IsolatedAsyncioTestCase):
    async def test_list_orders_api_exposes_broker_truth_source(self) -> None:
        class FakeRepository:
            def list_orders_with_protection(self):
                return [entry_order(), stop_order()]

            def get_bot_state(self, key, default):
                return connected_report([reality_row()])

        request = SimpleNamespace(
            app=SimpleNamespace(state=SimpleNamespace(repository=FakeRepository()))
        )

        result = await routes_orders.list_orders(request)

        self.assertTrue(all(item["source"] == "BROKER_REALITY" for item in result["items"]))

    async def test_list_positions_api_exposes_local_only_source_when_report_missing(self) -> None:
        class FakeRepository:
            def list_positions(self):
                return [{"symbol": "LUNR", "quantity": 6}]

            def get_bot_state(self, key, default):
                return default

        request = SimpleNamespace(
            app=SimpleNamespace(state=SimpleNamespace(repository=FakeRepository()))
        )

        result = await routes_positions.list_positions(request)

        self.assertEqual(result["items"][0]["source"], "LOCAL_ONLY")


if __name__ == "__main__":
    unittest.main()
