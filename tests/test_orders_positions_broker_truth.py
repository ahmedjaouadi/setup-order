from __future__ import annotations

import tempfile
import unittest
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

from app.api import routes_orders, routes_positions
from app.broker.ib_models import BrokerExecution, BrokerOrderRequest, BrokerPosition
from app.engine.broker_reality import (
    orders_broker_truth_overlay,
    positions_broker_truth_overlay,
)
from app.engine.trading_engine import TradingEngine
from app.models import ConnectionStatus, OrderRecord, OrderSide, OrderStatus, OrderType
from app.settings import DEFAULT_CONFIG, Settings
from app.storage.database import Database
from app.storage.repositories import TradingRepository


def connected_report(rows: list[dict]) -> dict:
    from datetime import UTC, datetime

    return {
        "broker_connected": True,
        "broker_tracker_status": "OK",
        "broker_last_sync_at": datetime.now(UTC).isoformat(),
        "rows": rows,
    }


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

    def test_orders_are_marked_local_only_when_report_is_stale(self) -> None:
        # A report persisted by a previous session keeps broker_connected=True;
        # once the tracker is no longer fresh it must not count as broker truth.
        stale_report = {
            "broker_connected": True,
            "broker_tracker_status": "STALE",
            "broker_last_sync_at": "2026-06-30T15:18:05+00:00",
            "rows": [reality_row()],
        }

        overlaid = orders_broker_truth_overlay([entry_order()], stale_report)

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


class ExecutionsBroker:
    """Minimal broker exposing today's fills like the TWS connector does."""

    connector_name = "paper"
    account_mode = "paper"
    display_name = "Executions broker"

    def __init__(self) -> None:
        self.connected = True

    async def status(self) -> ConnectionStatus:
        return ConnectionStatus.CONNECTED if self.connected else ConnectionStatus.DISCONNECTED

    async def positions(self) -> list:
        return []

    async def open_orders(self) -> list:
        return []

    async def account_summary(self) -> dict:
        return {"available": True, "source": "paper"}

    async def recent_executions(self) -> list[BrokerExecution]:
        return [
            BrokerExecution(
                execution_id="exec-1",
                symbol="lunr",
                side="buy",
                quantity=6,
                price=20.15,
                order_id="41",
                broker_perm_id="900001",
                timestamp="2026-07-06T14:31:02+00:00",
            )
        ]

    def drain_audit_entries(self) -> list:
        return []


class TradingBookBroker:
    """Broker fixture used to verify the whole /api/dashboard trading book projection."""

    connector_name = "paper"
    account_mode = "paper"
    display_name = "Trading book broker"

    def __init__(
        self,
        *,
        positions: list[BrokerPosition] | None = None,
        orders: list[BrokerOrderRequest] | None = None,
        connected: bool = True,
    ) -> None:
        self._positions = positions or []
        self._orders = orders or []
        self.connected = connected

    async def status(self) -> ConnectionStatus:
        return ConnectionStatus.CONNECTED if self.connected else ConnectionStatus.DISCONNECTED

    async def positions(self) -> list[BrokerPosition]:
        return list(self._positions)

    async def open_orders(self) -> list[BrokerOrderRequest]:
        return list(self._orders)

    async def recent_executions(self) -> list[BrokerExecution]:
        return []

    async def account_summary(self) -> dict:
        return {"available": True, "source": "paper"}

    def drain_audit_entries(self) -> list:
        return []


def broker_position(symbol: str, quantity: int = 1) -> BrokerPosition:
    return BrokerPosition(
        symbol=symbol,
        quantity=quantity,
        average_price=10.0,
        current_price=11.0,
        market_price=11.0,
        unrealized_pnl=float(quantity),
    )


def broker_order(symbol: str = "LUNR") -> BrokerOrderRequest:
    return BrokerOrderRequest(
        client_order_id=f"ord_{symbol}",
        setup_id=f"{symbol}_SETUP",
        symbol=symbol,
        side="BUY",
        order_type="LMT",
        quantity=5,
        limit_price=20.0,
        status=OrderStatus.SUBMITTED.value,
        broker_status="Submitted",
        broker_order_id="41",
        broker_perm_id="900001",
    )


class BrokerExecutionsSnapshotTests(unittest.IsolatedAsyncioTestCase):
    """The Ordres & Positions page shows today's TWS fills (etape 10.2)."""

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
        self.repository = TradingRepository(self.database)

    async def asyncTearDown(self) -> None:
        self.database.close()
        self.tmp.cleanup()

    async def test_todays_fills_are_exposed_normalized(self) -> None:
        engine = TradingEngine(self.settings, self.repository, broker=ExecutionsBroker())

        rows = await engine._broker_executions_snapshot()

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["symbol"], "LUNR")
        self.assertEqual(rows[0]["side"], "BUY")
        self.assertEqual(rows[0]["quantity"], 6)
        self.assertEqual(rows[0]["price"], 20.15)
        self.assertEqual(rows[0]["order_id"], "41")

    async def test_disconnected_broker_returns_last_known_fills(self) -> None:
        broker = ExecutionsBroker()
        engine = TradingEngine(self.settings, self.repository, broker=broker)
        await engine._broker_executions_snapshot()

        broker.connected = False
        engine._broker_executions_cached_at = 0.0  # force TTL expiry
        rows = await engine._broker_executions_snapshot()

        self.assertEqual([row["symbol"] for row in rows], ["LUNR"])


class TradingBookSnapshotTests(unittest.IsolatedAsyncioTestCase):
    """The Ordres & Positions page must use one coherent broker-truth projection."""

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
        self.repository = TradingRepository(self.database)

    async def asyncTearDown(self) -> None:
        self.database.close()
        self.tmp.cleanup()

    async def test_metrics_are_derived_from_current_tables_not_stale_broker_reality(self) -> None:
        """A persisted broker_reality report must not override the current TWS table view."""

        self.repository.set_bot_state(
            "broker_reality",
            connected_report(
                [
                    {
                        **reality_row(),
                        "symbol": "STALE",
                        "position_quantity": 8,
                    }
                ]
            )
            | {
                "broker_positions_count": 8,
                "broker_active_orders": 4,
                "broker_prepared_not_transmitted_orders": 2,
            },
        )
        engine = TradingEngine(
            self.settings,
            self.repository,
            broker=TradingBookBroker(positions=[], orders=[]),
        )

        snapshot = await engine.snapshot()

        self.assertEqual(snapshot["positions"], [])
        self.assertEqual(snapshot["orders"], [])
        self.assertEqual(snapshot["metrics"]["open_positions"], 0)
        self.assertEqual(snapshot["metrics"]["open_orders"], 0)
        self.assertEqual(snapshot["metrics"]["broker_prepared_not_transmitted_orders"], 0)

    async def test_connected_broker_positions_and_orders_drive_the_trading_book(self) -> None:
        positions = [broker_position(f"SYM{i}") for i in range(8)]
        engine = TradingEngine(
            self.settings,
            self.repository,
            broker=TradingBookBroker(positions=positions, orders=[]),
        )

        snapshot = await engine.snapshot()

        self.assertEqual(len(snapshot["positions"]), 8)
        self.assertEqual(snapshot["orders"], [])
        self.assertEqual(snapshot["metrics"]["open_positions"], 8)
        self.assertEqual(snapshot["metrics"]["open_orders"], 0)

    async def test_connected_trading_book_separates_tws_orders_from_local_history(self) -> None:
        self.repository.upsert_order(
            OrderRecord(
                id="ord_old",
                setup_id="OLD_SETUP",
                symbol="OLD",
                side=OrderSide.BUY.value,
                order_type=OrderType.LMT.value,
                quantity=1,
                status=OrderStatus.CANCELLED.value,
                limit_price=9.0,
            )
        )
        engine = TradingEngine(
            self.settings,
            self.repository,
            broker=TradingBookBroker(positions=[], orders=[broker_order()]),
        )

        snapshot = await engine.snapshot()

        self.assertEqual([row["symbol"] for row in snapshot["orders"]], ["LUNR"])
        self.assertEqual(snapshot["orders"][0]["broker_order_status"], "TRANSMITTED")
        self.assertEqual(snapshot["metrics"]["open_orders"], 1)
        self.assertEqual(snapshot["metrics"]["historical_orders"], 1)
        self.assertEqual([row["id"] for row in snapshot["order_history"]], ["ord_old"])

    async def test_disconnected_broker_keeps_local_orders_out_of_active_tws_table(self) -> None:
        self.repository.upsert_order(
            OrderRecord(
                id="ord_local",
                setup_id="LOCAL_SETUP",
                symbol="LUNR",
                side=OrderSide.BUY.value,
                order_type=OrderType.LMT.value,
                quantity=5,
                status=OrderStatus.SUBMITTED.value,
                limit_price=20.0,
                broker_order_id="local-missing",
            )
        )
        engine = TradingEngine(
            self.settings,
            self.repository,
            broker=TradingBookBroker(positions=[], orders=[], connected=False),
        )

        snapshot = await engine.snapshot()

        self.assertEqual(snapshot["orders"], [])
        self.assertEqual(snapshot["metrics"]["open_orders"], 0)
        self.assertEqual(snapshot["metrics"]["historical_orders"], 1)
        self.assertEqual(snapshot["local_order_orphans"], [])
        self.assertEqual(snapshot["order_history"][0]["broker_order_status"], "LOCAL_FALLBACK")

    async def test_local_active_order_without_tws_match_is_an_orphan_not_a_broker_order(self) -> None:
        self.repository.upsert_order(
            OrderRecord(
                id="ord_local",
                setup_id="LOCAL_SETUP",
                symbol="LUNR",
                side=OrderSide.BUY.value,
                order_type=OrderType.LMT.value,
                quantity=5,
                status=OrderStatus.SUBMITTED.value,
                limit_price=20.0,
                broker_order_id="local-missing",
            )
        )
        engine = TradingEngine(
            self.settings,
            self.repository,
            broker=TradingBookBroker(positions=[], orders=[]),
        )

        snapshot = await engine.snapshot()

        self.assertEqual(snapshot["orders"], [])
        self.assertEqual(snapshot["metrics"]["open_orders"], 0)
        self.assertEqual(len(snapshot["local_order_orphans"]), 1)
        self.assertEqual(snapshot["local_order_orphans"][0]["broker_order_status"], "LOCAL_ORPHAN")


if __name__ == "__main__":
    unittest.main()
