"""Parsing des donnees TWS (docs/Lecture_des_donnees_TWS_IBKR.md).

Verifie que le connecteur ne confond pas ordres ouverts, stops et executions:
- les sentinelles UNSET_DOUBLE d'IBKR ne deviennent pas des prix affiches;
- auxPrice n'est traite comme stop que pour les ordres de type stop;
- les ordres saisis manuellement dans TWS (orderId 0) sont identifies par permId;
- les executions viennent de ib.fills() (symbole present, side BUY/SELL).
"""

from __future__ import annotations

import unittest
from types import SimpleNamespace

from app.broker.tws_connector import IbAsyncTwsConnector

UNSET_DOUBLE = 1.7976931348623157e308


class _FakeIB:
    def __init__(self, trades: list | None = None, fills: list | None = None) -> None:
        self._trades = trades or []
        self._fills = fills or []
        self.all_open_orders_requested = False

    def isConnected(self) -> bool:
        return True

    async def reqAllOpenOrdersAsync(self) -> list:
        self.all_open_orders_requested = True
        return list(self._trades)

    def openTrades(self) -> list:
        return list(self._trades)

    def fills(self) -> list:
        return list(self._fills)


def _trade(
    order_type: str,
    *,
    action: str = "BUY",
    order_id: int = 42,
    perm_id: int = 777,
    lmt: float = UNSET_DOUBLE,
    aux: float = UNSET_DOUBLE,
    trail_stop: float = UNSET_DOUBLE,
    qty: int = 10,
    status: str = "Submitted",
) -> SimpleNamespace:
    order = SimpleNamespace(
        orderId=order_id,
        permId=perm_id,
        action=action,
        orderType=order_type,
        totalQuantity=qty,
        lmtPrice=lmt,
        auxPrice=aux,
        trailStopPrice=trail_stop,
        parentId=0,
        ocaGroup="",
        transmit=True,
    )
    return SimpleNamespace(
        contract=SimpleNamespace(symbol="LUNR"),
        order=order,
        orderStatus=SimpleNamespace(status=status, filled=0.0, remaining=float(qty)),
    )


def _connector(ib: _FakeIB) -> IbAsyncTwsConnector:
    connector = IbAsyncTwsConnector("paper", {})
    connector._ib = ib
    return connector


class TwsOpenOrderParsingTests(unittest.IsolatedAsyncioTestCase):
    async def test_limit_order_unset_aux_price_is_not_a_stop(self) -> None:
        connector = _connector(_FakeIB(trades=[_trade("LMT", lmt=25.5)]))
        orders = await connector.open_orders()
        self.assertEqual(len(orders), 1)
        order = orders[0]
        self.assertEqual(order.limit_price, 25.5)
        self.assertIsNone(order.stop_price)
        self.assertIsNone(order.trigger_price)

    async def test_market_order_has_no_prices(self) -> None:
        connector = _connector(_FakeIB(trades=[_trade("MKT")]))
        order = (await connector.open_orders())[0]
        self.assertIsNone(order.limit_price)
        self.assertIsNone(order.stop_price)
        self.assertIsNone(order.trigger_price)

    async def test_stop_order_aux_price_is_the_stop(self) -> None:
        connector = _connector(_FakeIB(trades=[_trade("STP", action="SELL", aux=9.5)]))
        order = (await connector.open_orders())[0]
        self.assertEqual(order.stop_price, 9.5)
        self.assertEqual(order.trigger_price, 9.5)
        self.assertIsNone(order.limit_price)

    async def test_trailing_stop_uses_trail_stop_price_not_trail_amount(self) -> None:
        connector = _connector(
            _FakeIB(trades=[_trade("TRAIL", action="SELL", aux=0.5, trail_stop=12.3)])
        )
        order = (await connector.open_orders())[0]
        self.assertEqual(order.stop_price, 12.3)

    async def test_manual_tws_order_is_identified_by_perm_id(self) -> None:
        connector = _connector(_FakeIB(trades=[_trade("LMT", order_id=0, perm_id=555, lmt=10.0)]))
        order = (await connector.open_orders())[0]
        self.assertIsNone(order.broker_order_id)
        self.assertEqual(order.broker_perm_id, "555")
        self.assertEqual(order.client_order_id, "555")

    async def test_all_open_orders_are_requested(self) -> None:
        ib = _FakeIB(trades=[])
        connector = _connector(ib)
        await connector.open_orders()
        self.assertTrue(ib.all_open_orders_requested)


class TwsExecutionParsingTests(unittest.IsolatedAsyncioTestCase):
    async def test_executions_come_from_fills_with_symbol_and_side(self) -> None:
        fill = SimpleNamespace(
            contract=SimpleNamespace(symbol="LUNR"),
            execution=SimpleNamespace(
                execId="exec-1",
                side="BOT",
                shares=10.0,
                price=25.4,
                orderId=42,
                permId=777,
                time="2026-07-07 15:30:00",
            ),
        )
        connector = _connector(_FakeIB(fills=[fill]))
        executions = await connector.recent_executions()
        self.assertEqual(len(executions), 1)
        execution = executions[0]
        self.assertEqual(execution.symbol, "LUNR")
        self.assertEqual(execution.side, "BUY")
        self.assertEqual(execution.quantity, 10.0)
        self.assertEqual(execution.price, 25.4)
        self.assertEqual(execution.order_id, "42")

    async def test_sld_side_maps_to_sell(self) -> None:
        fill = SimpleNamespace(
            contract=SimpleNamespace(symbol="LUNR"),
            execution=SimpleNamespace(
                execId="exec-2",
                side="SLD",
                shares=5.0,
                price=26.0,
                orderId=0,
                permId=778,
                time="2026-07-07 16:00:00",
            ),
        )
        connector = _connector(_FakeIB(fills=[fill]))
        execution = (await connector.recent_executions())[0]
        self.assertEqual(execution.side, "SELL")
        self.assertIsNone(execution.order_id)


if __name__ == "__main__":
    unittest.main()
