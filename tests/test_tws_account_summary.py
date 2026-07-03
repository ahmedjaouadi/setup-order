from __future__ import annotations

import asyncio
import unittest

from app.broker.tws_connector import IbAsyncTwsConnector
from app.models import ConnectionStatus


class FakePnL:
    dailyPnL = 23.0
    unrealizedPnL = 39.9
    realizedPnL = -16.9


class FakeIB:
    def __init__(self) -> None:
        self.req_pnl_called = False
        self.cancel_pnl_called = False

    def isConnected(self) -> bool:
        return True

    def accountValues(self):
        return []

    async def accountSummaryAsync(self):
        await asyncio.sleep(0)
        return []

    def managedAccounts(self):
        return ["DU123456"]

    def pnl(self, account: str, modelCode: str = ""):
        return [FakePnL()] if self.req_pnl_called else []

    def reqPnL(self, account: str, modelCode: str = ""):
        self.req_pnl_called = True
        return FakePnL()

    def cancelPnL(self, account: str, modelCode: str = ""):
        self.cancel_pnl_called = True


class TwsAccountSummaryTests(unittest.IsolatedAsyncioTestCase):
    async def test_account_summary_uses_live_daily_pnl(self) -> None:
        connector = IbAsyncTwsConnector("paper", {})
        connector._ib = FakeIB()
        connector._status = ConnectionStatus.CONNECTED

        summary = await connector.account_summary()

        self.assertEqual(summary["today_pnl"], 23.0)
        self.assertEqual(summary["unrealized_pnl"], 39.9)
        self.assertEqual(summary["realized_pnl"], -16.9)


if __name__ == "__main__":
    unittest.main()
