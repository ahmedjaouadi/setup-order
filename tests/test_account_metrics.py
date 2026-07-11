from __future__ import annotations

import tempfile
import unittest
from copy import deepcopy
from pathlib import Path

from app.broker.ib_models import BrokerOrderRequest, BrokerPosition
from app.engine.trading_engine import TradingEngine
from app.models import ConnectionStatus, OrderRecord, OrderStatus
from app.settings import DEFAULT_CONFIG, Settings
from app.storage.database import Database
from app.storage.repositories import TradingRepository


class FakeAccountBroker:
    connector_name = "paper"
    account_mode = "paper"
    display_name = "Fake account broker"

    def __init__(self, account: dict) -> None:
        self.account = account
        self.calls = 0

    async def account_summary(self) -> dict:
        self.calls += 1
        return self.account


class FakeSnapshotBroker(FakeAccountBroker):
    def __init__(
        self,
        account: dict,
        positions: list[BrokerPosition],
        open_orders: list[BrokerOrderRequest] | None = None,
    ) -> None:
        super().__init__(account)
        self._positions = positions
        self._open_orders = open_orders or []

    async def status(self) -> ConnectionStatus:
        return ConnectionStatus.CONNECTED

    async def positions(self) -> list[BrokerPosition]:
        return self._positions

    async def open_orders(self) -> list[BrokerOrderRequest]:
        return self._open_orders

    def drain_audit_entries(self) -> list:
        return []


class AccountMetricsTests(unittest.IsolatedAsyncioTestCase):
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

    async def test_account_equity_change_is_not_reported_as_trading_pnl(self) -> None:
        broker = FakeAccountBroker(
            {
                "available": True,
                "source": "paper",
                "currency": "USD",
                "net_liquidation": 10500,
                "previous_day_equity": 10400,
                "realized_pnl": 0,
                "unrealized_pnl": 0,
            }
        )
        engine = TradingEngine(self.settings, self.repository, broker=broker)
        today = engine._local_date()
        self.repository.set_bot_state(
            "account_history",
            {
                "initial_equity": 10000,
                "daily_start": {today: 10400},
            },
        )

        account = await engine._account_snapshot(positions_pnl=0)

        self.assertEqual(account["today_pnl"], 0)
        self.assertEqual(account["pnl_until_yesterday"], 0)
        self.assertEqual(account["equity_change_today"], 100)
        self.assertEqual(account["equity_change_until_yesterday"], 400)

    async def test_account_snapshot_exposes_live_today_pnl_estimate(self) -> None:
        broker = FakeAccountBroker(
            {
                "available": True,
                "source": "paper",
                "currency": "USD",
                "net_liquidation": 10500,
                "previous_day_equity": 10400,
                "realized_pnl": 12.5,
                "unrealized_pnl": 8.0,
                "today_pnl": 18.0,
            }
        )
        engine = TradingEngine(self.settings, self.repository, broker=broker)

        account = await engine._account_snapshot(positions_pnl=21.25)

        self.assertEqual(account["today_pnl_broker"], 18.0)
        self.assertEqual(account["today_pnl_live_estimate"], 33.75)
        self.assertEqual(account["positions_unrealized_pnl"], 21.25)

    async def test_account_snapshot_keeps_broker_today_pnl_when_realized_is_missing(self) -> None:
        broker = FakeAccountBroker(
            {
                "available": True,
                "source": "paper",
                "currency": "USD",
                "net_liquidation": 10500,
                "today_pnl": 22.3,
            }
        )
        engine = TradingEngine(self.settings, self.repository, broker=broker)

        account = await engine._account_snapshot(positions_pnl=0)

        self.assertEqual(account["today_pnl_broker"], 22.3)
        self.assertEqual(account["today_pnl_live_estimate"], 22.3)

    async def test_account_snapshot_reuses_recent_broker_cache(self) -> None:
        broker = FakeAccountBroker(
            {
                "available": True,
                "source": "paper",
                "currency": "USD",
                "net_liquidation": 10500,
                "previous_day_equity": 10400,
                "realized_pnl": 5.0,
                "unrealized_pnl": 7.5,
                "today_pnl": 12.5,
            }
        )
        engine = TradingEngine(self.settings, self.repository, broker=broker)

        first = await engine._account_snapshot(positions_pnl=7.5)
        second = await engine._account_snapshot(positions_pnl=9.25)

        self.assertEqual(broker.calls, 1)
        self.assertEqual(first["today_pnl_live_estimate"], 12.5)
        self.assertEqual(second["today_pnl_live_estimate"], 14.25)

    async def test_snapshot_uses_broker_positions_when_local_positions_are_empty(self) -> None:
        broker = FakeSnapshotBroker(
            {
                "available": False,
                "source": "paper",
                "currency": "USD",
            },
            [
                BrokerPosition(
                    symbol="LUNR",
                    quantity=5,
                    average_price=20.0,
                    current_price=20.5,
                )
            ],
        )
        engine = TradingEngine(self.settings, self.repository, broker=broker)

        snapshot = await engine.snapshot()

        self.assertEqual(snapshot["metrics"]["open_positions"], 1)
        self.assertEqual(snapshot["metrics"]["positions_pnl"], 2.5)
        # Broker connected but no fresh TWS PnL report: today_pnl must stay
        # empty (TWS_STALE), never back-filled from local estimates.
        self.assertIsNone(snapshot["metrics"]["today_pnl"])
        self.assertEqual(snapshot["metrics"]["pnl_display_source"], "TWS_STALE")
        self.assertEqual(snapshot["positions"][0]["symbol"], "LUNR")
        self.assertEqual(snapshot["positions"][0]["setup_id"], "broker:LUNR")

    async def test_snapshot_overlays_broker_open_order_on_cancelled_local_order(self) -> None:
        self.repository.upsert_order(
            OrderRecord(
                id="stp_local",
                setup_id="LUNR_20260630_001",
                symbol="LUNR",
                side="SELL",
                order_type="STP",
                quantity=5,
                status=OrderStatus.CANCELLED.value,
                stop_price=19.45,
                broker_order_id="7001",
            )
        )
        broker = FakeSnapshotBroker(
            {
                "available": False,
                "source": "paper",
                "currency": "USD",
            },
            [],
            [
                BrokerOrderRequest(
                    client_order_id="7001",
                    setup_id="broker",
                    symbol="LUNR",
                    side="SELL",
                    order_type="STP",
                    quantity=5,
                    stop_price=19.45,
                    status=OrderStatus.SUBMITTED.value,
                    broker_order_id="7001",
                )
            ],
        )
        engine = TradingEngine(self.settings, self.repository, broker=broker)

        snapshot = await engine.snapshot()

        self.assertEqual(snapshot["metrics"]["open_orders"], 1)
        self.assertEqual(snapshot["orders"][0]["id"], "stp_local")
        self.assertEqual(snapshot["orders"][0]["status"], OrderStatus.SUBMITTED.value)


if __name__ == "__main__":
    unittest.main()
