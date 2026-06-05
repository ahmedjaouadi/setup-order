from __future__ import annotations

import inspect
import random
import asyncio
import time
import logging
import math
from contextlib import suppress
from abc import ABC, abstractmethod
from datetime import date, datetime, timezone
from typing import Any

from app.broker.broker_errors import BrokerDisconnectedError
from app.broker.ib_models import (
    BrokerOrderRequest,
    BrokerOrderResult,
    BrokerPosition,
)
from app.market_data.indicators import average_true_range
from app.models import ConnectionStatus, OrderStatus


logger = logging.getLogger(__name__)


class BrokerConnector(ABC):
    connector_name = "unknown"
    account_mode = "unknown"
    display_name = "Broker"
    supports_external_account = False

    @abstractmethod
    async def connect(self) -> None:
        raise NotImplementedError

    @abstractmethod
    async def disconnect(self) -> None:
        raise NotImplementedError

    @abstractmethod
    async def status(self) -> ConnectionStatus:
        raise NotImplementedError

    async def health_check(self, timeout: float = 2.0) -> ConnectionStatus:
        return await self.status()

    def diagnostics(self) -> dict[str, Any]:
        return {}

    def set_audit_enabled(self, enabled: bool) -> None:
        return None

    def drain_audit_entries(self) -> list[dict[str, Any]]:
        return []

    async def market_snapshot(self, symbol: str, timeout: float = 4.0) -> dict[str, Any]:
        return {
            "available": False,
            "symbol": symbol.upper(),
            "source": self.connector_name,
            "message": "Market data is not available for this broker.",
        }

    async def historical_bars(
        self,
        symbol: str,
        duration: str,
        bar_size: str,
        timeout: float = 4.0,
    ) -> dict[str, Any]:
        return {
            "available": False,
            "symbol": symbol.upper(),
            "source": self.connector_name,
            "market_data_source": "historical",
            "historical_duration": duration,
            "historical_bar_size": bar_size,
            "message": "Historical data is not available for this broker.",
        }

    @abstractmethod
    async def submit_order(self, request: BrokerOrderRequest) -> BrokerOrderResult:
        raise NotImplementedError

    @abstractmethod
    async def cancel_order(self, broker_order_id: str) -> BrokerOrderResult:
        raise NotImplementedError

    @abstractmethod
    async def open_orders(self) -> list[BrokerOrderRequest]:
        raise NotImplementedError

    @abstractmethod
    async def positions(self) -> list[BrokerPosition]:
        raise NotImplementedError

    @abstractmethod
    async def account_summary(self) -> dict[str, Any]:
        raise NotImplementedError


class SimulatedBrokerConnector(BrokerConnector):
    """In-memory broker used for simulation and development."""

    connector_name = "simulated"
    account_mode = "simulation"
    display_name = "Local simulation"
    supports_external_account = False

    def __init__(self) -> None:
        self._status = ConnectionStatus.DISCONNECTED
        self._next_order_id = 1000
        self._orders: dict[str, BrokerOrderRequest] = {}
        self._positions: dict[str, BrokerPosition] = {}

    async def connect(self) -> None:
        self._status = ConnectionStatus.CONNECTED

    async def disconnect(self) -> None:
        self._status = ConnectionStatus.DISCONNECTED

    async def status(self) -> ConnectionStatus:
        return self._status

    async def submit_order(self, request: BrokerOrderRequest) -> BrokerOrderResult:
        if self._status != ConnectionStatus.CONNECTED:
            raise BrokerDisconnectedError("Simulated broker is disconnected")
        if request.quantity <= 0:
            return BrokerOrderResult(
                accepted=False,
                status=OrderStatus.REJECTED.value,
                reason="Quantity must be positive",
            )
        self._next_order_id += 1
        broker_order_id = str(self._next_order_id)
        perm_id = f"SIM-{broker_order_id}"
        self._orders[broker_order_id] = request
        return BrokerOrderResult(
            accepted=True,
            status=OrderStatus.SUBMITTED.value,
            broker_order_id=broker_order_id,
            broker_perm_id=perm_id,
            reason="Accepted by simulated broker",
        )

    async def cancel_order(self, broker_order_id: str) -> BrokerOrderResult:
        if self._status != ConnectionStatus.CONNECTED:
            raise BrokerDisconnectedError("Simulated broker is disconnected")
        if broker_order_id in self._orders:
            self._orders.pop(broker_order_id)
            return BrokerOrderResult(
                accepted=True,
                status=OrderStatus.CANCELLED.value,
                broker_order_id=broker_order_id,
                reason="Cancelled",
            )
        return BrokerOrderResult(
            accepted=False,
            status=OrderStatus.REJECTED.value,
            broker_order_id=broker_order_id,
            reason="Order not found",
        )

    async def open_orders(self) -> list[BrokerOrderRequest]:
        return list(self._orders.values())

    async def positions(self) -> list[BrokerPosition]:
        return list(self._positions.values())

    async def account_summary(self) -> dict[str, Any]:
        return {
            "available": False,
            "source": self.connector_name,
            "currency": "USD",
            "message": "Local simulation does not expose broker account cash.",
        }

    async def simulate_fill(
        self,
        broker_order_id: str,
        fill_price: float,
    ) -> BrokerPosition | None:
        order = self._orders.pop(broker_order_id, None)
        if not order:
            return None
        signed_quantity = order.quantity if order.side == "BUY" else -order.quantity
        existing = self._positions.get(order.symbol)
        if existing is None:
            position = BrokerPosition(
                symbol=order.symbol,
                quantity=signed_quantity,
                average_price=fill_price,
                current_price=fill_price,
            )
        else:
            new_quantity = existing.quantity + signed_quantity
            if new_quantity == 0:
                self._positions.pop(order.symbol, None)
                return BrokerPosition(
                    symbol=order.symbol,
                    quantity=0,
                    average_price=fill_price,
                    current_price=fill_price,
                )
            position = BrokerPosition(
                symbol=order.symbol,
                quantity=new_quantity,
                average_price=existing.average_price,
                current_price=fill_price,
            )
        self._positions[order.symbol] = position
        return position


class UnavailableBrokerConnector(BrokerConnector):
    """Placeholder for paper/live until a real TWS connector is configured."""

    supports_external_account = True

    def __init__(self, connector_name: str) -> None:
        self.connector_name = connector_name
        self.account_mode = connector_name
        self.display_name = f"IBKR {connector_name} account"
        self._status = ConnectionStatus.DISCONNECTED

    async def connect(self) -> None:
        self._status = ConnectionStatus.ERROR

    async def disconnect(self) -> None:
        self._status = ConnectionStatus.DISCONNECTED

    async def status(self) -> ConnectionStatus:
        return self._status

    async def submit_order(self, request: BrokerOrderRequest) -> BrokerOrderResult:
        return BrokerOrderResult(
            accepted=False,
            status=OrderStatus.REJECTED.value,
            reason=(
                f"{self.display_name} is not connected. Configure TWS/Gateway "
                "authentication before sending paper/live orders."
            ),
        )

    async def cancel_order(self, broker_order_id: str) -> BrokerOrderResult:
        return BrokerOrderResult(
            accepted=False,
            status=OrderStatus.REJECTED.value,
            broker_order_id=broker_order_id,
            reason=f"{self.display_name} is not connected.",
        )

    async def open_orders(self) -> list[BrokerOrderRequest]:
        return []

    async def positions(self) -> list[BrokerPosition]:
        return []

    async def account_summary(self) -> dict[str, Any]:
        return {
            "available": False,
            "source": self.connector_name,
            "currency": "USD",
            "message": f"{self.display_name} is not connected.",
        }


class IbAsyncTwsConnector(BrokerConnector):
    supports_external_account = True

    def __init__(self, connector_name: str, config: dict[str, Any]) -> None:
        self.connector_name = connector_name
        self.account_mode = connector_name
        self.display_name = f"IBKR {connector_name} account"
        self.host = str(config.get("host", "127.0.0.1"))
        default_port = 7497 if connector_name == "paper" else 7496
        self.port = int(
            config.get("port")
            or config.get(f"{connector_name}_port")
            or default_port
        )
        self.client_id = int(config.get("client_id", 1001))
        self.reconnect = bool(config.get("reconnect", True))
        self.reconnect_interval_seconds = float(config.get("reconnect_interval_seconds", 5))
        self.audit_enabled = bool(config.get("tws_audit_enabled", True))
        self.market_data_source = str(config.get("market_data_source", "historical")).lower()
        self.historical_duration = str(config.get("historical_duration", "30 D"))
        self.historical_bar_size = str(config.get("historical_bar_size", "1 day"))
        self.historical_what_to_show = str(config.get("historical_what_to_show", "TRADES"))
        self.historical_use_rth = bool(config.get("historical_use_rth", True))
        self.last_error = ""
        self._status = ConnectionStatus.DISCONNECTED
        self._ib = None
        self._last_reconnect_attempt = 0.0
        self._audit_entries: list[dict[str, Any]] = []
        self._diagnostics: dict[str, Any] = {
            "tws_request_count": 0,
            "tws_audit_enabled": self.audit_enabled,
            "tws_audit_pending": 0,
            "last_tws_request": "",
            "last_tws_request_detail": "",
            "last_tws_request_sent_at": None,
            "last_tws_response_at": None,
            "last_tws_latency_ms": None,
            "last_tws_request_status": "",
            "last_tws_request_error": "",
        }

    async def connect(self) -> None:
        self._last_reconnect_attempt = time.monotonic()
        try:
            from ib_async import IB
        except ImportError:
            self._status = ConnectionStatus.ERROR
            self.last_error = "Install ib_async: python -m pip install -r requirements.txt"
            return
        base_client_id = self.client_id
        candidates = self._client_id_candidates(base_client_id)
        failures: list[str] = []
        attempted: list[int] = []
        for candidate in candidates:
            attempted.append(candidate)
            ib = IB()
            started = self._record_tws_request_sent(
                "connectAsync",
                f"{self.host}:{self.port} clientId={candidate}",
            )
            try:
                await ib.connectAsync(
                    self.host,
                    self.port,
                    clientId=candidate,
                    timeout=5,
                )
                self._record_tws_request_result(started, "OK")
                self._ib = ib
                self.client_id = candidate
                self._status = ConnectionStatus.CONNECTED
                self.last_error = ""
                return
            except Exception as exc:
                self._record_tws_request_result(started, "ERROR", _error_text(exc))
                failures.append(f"{candidate}: {_error_text(exc)}")
                if ib.isConnected():
                    ib.disconnect()
                if _is_endpoint_unavailable_error(exc):
                    break
        self._ib = None
        self.client_id = base_client_id
        self._status = ConnectionStatus.ERROR
        tried = ", ".join(str(item) for item in attempted)
        detail = "; ".join(failures[-3:])
        self.last_error = (
            f"Cannot connect to TWS/Gateway at {self.host}:{self.port}. "
            f"Tried client IDs: {tried}. Last errors: {detail}"
        )

    def _client_id_candidates(self, base_client_id: int) -> list[int]:
        sequential = [base_client_id + offset for offset in range(10)]
        random_ids = [random.randint(2000, 9999) for _ in range(5)]
        candidates: list[int] = []
        for client_id in [*sequential, *random_ids]:
            if client_id > 0 and client_id not in candidates:
                candidates.append(client_id)
        return candidates

    async def disconnect(self) -> None:
        if self._ib is not None and self._is_connected():
            started = self._record_tws_request_sent(
                "disconnect",
                f"{self.host}:{self.port} clientId={self.client_id}",
            )
            try:
                self._ib.disconnect()
            except Exception as exc:
                self._record_tws_request_result(started, "ERROR", _error_text(exc))
                raise
            self._record_tws_request_result(started, "OK")
        self._status = ConnectionStatus.DISCONNECTED

    async def status(self) -> ConnectionStatus:
        if self._ib is not None and self._is_connected():
            return ConnectionStatus.CONNECTED
        if self._status == ConnectionStatus.CONNECTED:
            self._mark_disconnected("TWS/Gateway connection lost.")
        return self._status

    async def health_check(self, timeout: float = 2.0) -> ConnectionStatus:
        if self._ib is None:
            if self._status == ConnectionStatus.ERROR:
                await self._maybe_reconnect()
                return self._status
            self._mark_disconnected("TWS/Gateway is disconnected.")
            await self._maybe_reconnect()
            return self._status
        if not self._is_connected():
            self._mark_disconnected("TWS/Gateway is disconnected.")
            await self._maybe_reconnect()
            return self._status
        started = self._record_tws_request_sent(
            "reqCurrentTime",
            f"{self.host}:{self.port} clientId={self.client_id}",
        )
        try:
            await asyncio.wait_for(self._ib.reqCurrentTimeAsync(), timeout=timeout)
        except Exception as exc:
            self._record_tws_request_result(started, "ERROR", _error_text(exc))
            self._mark_disconnected(
                f"TWS/Gateway heartbeat failed: {_error_text(exc)}"
            )
            with suppress(Exception):
                self._ib.disconnect()
            return self._status
        self._record_tws_request_result(started, "OK")
        self._status = ConnectionStatus.CONNECTED
        self.last_error = ""
        return ConnectionStatus.CONNECTED

    def diagnostics(self) -> dict[str, Any]:
        return {
            **self._diagnostics,
            "tws_audit_enabled": self.audit_enabled,
            "tws_audit_pending": len(self._audit_entries),
        }

    def set_audit_enabled(self, enabled: bool) -> None:
        self.audit_enabled = bool(enabled)
        if not self.audit_enabled:
            self._audit_entries.clear()
        self._diagnostics.update(
            {
                "tws_audit_enabled": self.audit_enabled,
                "tws_audit_pending": len(self._audit_entries),
            }
        )

    def drain_audit_entries(self) -> list[dict[str, Any]]:
        entries = list(self._audit_entries)
        self._audit_entries.clear()
        self._diagnostics["tws_audit_pending"] = 0
        return entries

    async def submit_order(self, request: BrokerOrderRequest) -> BrokerOrderResult:
        if await self.status() != ConnectionStatus.CONNECTED or self._ib is None:
            raise BrokerDisconnectedError(self.last_error or "TWS/Gateway is disconnected")
        try:
            contract = self._stock_contract(request.symbol)
            await self._qualify_contract(contract)
            order = self._build_order(request)
            started = self._record_tws_request_sent(
                "placeOrder",
                (
                    f"symbol={request.symbol} side={request.side} "
                    f"type={request.order_type} quantity={request.quantity} "
                    f"trigger={request.trigger_price} limit={request.limit_price} "
                    f"stop={request.stop_price}"
                ),
            )
            try:
                trade = self._ib.placeOrder(contract, order)
                await self._sleep(0.2)
            except Exception as exc:
                self._record_tws_request_result(started, "ERROR", _error_text(exc))
                raise
            broker_order_id = str(getattr(trade.order, "orderId", "") or "")
            perm_id = str(getattr(trade.order, "permId", "") or "")
            status = str(getattr(trade.orderStatus, "status", "") or "Submitted")
            self._record_tws_request_result(
                started,
                "OK",
                extra={
                    "broker_order_id": broker_order_id,
                    "broker_perm_id": perm_id,
                    "order_status": status,
                },
            )
            return BrokerOrderResult(
                accepted=status.lower() not in {"inactive", "cancelled", "api cancelled"},
                status=OrderStatus.SUBMITTED.value,
                broker_order_id=broker_order_id or None,
                broker_perm_id=perm_id or None,
                reason=f"Accepted by TWS: {status}",
            )
        except Exception as exc:
            return BrokerOrderResult(
                accepted=False,
                status=OrderStatus.REJECTED.value,
                reason=str(exc),
            )

    async def cancel_order(self, broker_order_id: str) -> BrokerOrderResult:
        if await self.status() != ConnectionStatus.CONNECTED or self._ib is None:
            raise BrokerDisconnectedError(self.last_error or "TWS/Gateway is disconnected")
        for trade in self._ib.trades():
            if str(getattr(trade.order, "orderId", "")) == str(broker_order_id):
                started = self._record_tws_request_sent(
                    "cancelOrder",
                    f"orderId={broker_order_id}",
                )
                try:
                    self._ib.cancelOrder(trade.order)
                except Exception as exc:
                    self._record_tws_request_result(started, "ERROR", _error_text(exc))
                    return BrokerOrderResult(
                        accepted=False,
                        status=OrderStatus.REJECTED.value,
                        broker_order_id=str(broker_order_id),
                        reason=str(exc),
                    )
                self._record_tws_request_result(started, "OK")
                return BrokerOrderResult(
                    accepted=True,
                    status=OrderStatus.CANCELLED.value,
                    broker_order_id=str(broker_order_id),
                    reason="Cancel sent to TWS",
                )
        return BrokerOrderResult(
            accepted=False,
            status=OrderStatus.REJECTED.value,
            broker_order_id=str(broker_order_id),
            reason="Order not found in TWS session",
        )

    async def open_orders(self) -> list[BrokerOrderRequest]:
        if await self.status() != ConnectionStatus.CONNECTED or self._ib is None:
            return []
        requests: list[BrokerOrderRequest] = []
        started = self._record_tws_request_sent("openTrades", "session cache")
        try:
            trades = list(self._ib.openTrades())
        except Exception as exc:
            self._record_tws_request_result(started, "ERROR", _error_text(exc))
            return []
        self._record_tws_request_result(started, "OK", extra={"count": len(trades)})
        for trade in trades:
            contract = trade.contract
            order = trade.order
            requests.append(
                BrokerOrderRequest(
                    client_order_id=str(getattr(order, "orderId", "")),
                    setup_id="broker",
                    symbol=str(getattr(contract, "symbol", "")),
                    side=str(getattr(order, "action", "")),
                    order_type=_normalize_order_type(str(getattr(order, "orderType", ""))),
                    quantity=int(abs(float(getattr(order, "totalQuantity", 0) or 0))),
                    trigger_price=_float_or_none(getattr(order, "auxPrice", None)),
                    limit_price=_float_or_none(getattr(order, "lmtPrice", None)),
                    stop_price=_float_or_none(getattr(order, "auxPrice", None)),
                )
            )
        return requests

    async def positions(self) -> list[BrokerPosition]:
        if await self.status() != ConnectionStatus.CONNECTED or self._ib is None:
            return []
        started = self._record_tws_request_sent("positions", "session cache")
        try:
            positions = self._ib.positions()
        except Exception as exc:
            self._record_tws_request_result(started, "ERROR", _error_text(exc))
            return []
        self._record_tws_request_result(started, "OK", extra={"count": len(positions)})
        portfolio_by_symbol: dict[str, Any] = {}
        portfolio_started = self._record_tws_request_sent("portfolio", "session cache")
        try:
            for item in self._ib.portfolio():
                symbol = str(getattr(item.contract, "symbol", "") or "")
                if symbol:
                    portfolio_by_symbol[symbol] = item
        except Exception as exc:
            self._record_tws_request_result(portfolio_started, "ERROR", _error_text(exc))
            portfolio_by_symbol = {}
        else:
            self._record_tws_request_result(
                portfolio_started,
                "OK",
                extra={"count": len(portfolio_by_symbol)},
            )
        result: list[BrokerPosition] = []
        for position in positions:
            contract = position.contract
            symbol = str(getattr(contract, "symbol", ""))
            average_price = float(getattr(position, "avgCost", 0) or 0)
            quantity = int(float(getattr(position, "position", 0) or 0))
            current_price = average_price
            portfolio_item = portfolio_by_symbol.get(symbol)
            if portfolio_item is not None:
                average_price = float(
                    getattr(portfolio_item, "averageCost", average_price)
                    or average_price
                )
                current_price = float(
                    getattr(portfolio_item, "marketPrice", current_price)
                    or current_price
                )
            result.append(
                BrokerPosition(
                    symbol=symbol,
                    quantity=quantity,
                    average_price=average_price,
                    current_price=current_price,
                )
            )
        return result

    async def account_summary(self) -> dict[str, Any]:
        empty = {
            "available": False,
            "source": self.connector_name,
            "currency": "USD",
            "message": "Account summary is not available.",
        }
        if await self.status() != ConnectionStatus.CONNECTED or self._ib is None:
            return {**empty, "message": self.last_error or "TWS/Gateway is disconnected."}
        try:
            started = self._record_tws_request_sent("accountValues", "session cache")
            values = self._ib.accountValues()
            self._record_tws_request_result(started, "OK", extra={"count": len(values)})
            if not values:
                started = self._record_tws_request_sent("accountSummaryAsync", "account summary")
                values = await self._ib.accountSummaryAsync()
                self._record_tws_request_result(started, "OK", extra={"count": len(values)})
        except Exception as exc:
            self._record_tws_request_result(started, "ERROR", _error_text(exc))
            return {**empty, "message": str(exc)}

        summary = {
            "available": False,
            "source": self.connector_name,
            "currency": "USD",
            "net_liquidation": None,
            "cash": None,
            "buying_power": None,
            "available_funds": None,
            "gross_position_value": None,
            "previous_day_equity": None,
            "realized_pnl": None,
            "unrealized_pnl": None,
            "message": "",
        }
        fields = {
            "AccruedCash": "cash",
            "CashBalance": "cash",
            "NetLiquidation": "net_liquidation",
            "NetLiquidationByCurrency": "net_liquidation",
            "SettledCash": "cash",
            "TotalCashValue": "cash",
            "TotalCashBalance": "cash",
            "BuyingPower": "buying_power",
            "AvailableFunds": "available_funds",
            "FullAvailableFunds": "available_funds",
            "GrossPositionValue": "gross_position_value",
            "PreviousDayEquityWithLoanValue": "previous_day_equity",
            "RealizedPnL": "realized_pnl",
            "UnrealizedPnL": "unrealized_pnl",
        }
        for item in values or []:
            tag = str(getattr(item, "tag", "") or "")
            field = fields.get(tag)
            if not field:
                continue
            currency = str(getattr(item, "currency", "") or "USD")
            if currency not in {"", "USD", "BASE"}:
                continue
            value = _number_or_none(getattr(item, "value", None))
            if value is None:
                continue
            summary[field] = value
            if currency and currency != "BASE":
                summary["currency"] = currency
        summary["available"] = any(
            summary.get(field) is not None
            for field in (
                "net_liquidation",
                "cash",
                "buying_power",
                "available_funds",
                "realized_pnl",
                "unrealized_pnl",
            )
        )
        if not summary["available"]:
            summary["message"] = "TWS did not return account summary values."
        return summary

    async def market_snapshot(self, symbol: str, timeout: float = 4.0) -> dict[str, Any]:
        symbol = symbol.upper()
        empty = {
            "available": False,
            "source": self.connector_name,
            "symbol": symbol,
            "message": "Market data is not available.",
        }
        if await self.status() != ConnectionStatus.CONNECTED or self._ib is None:
            return {**empty, "message": self.last_error or "TWS/Gateway is disconnected."}
        contract = self._stock_contract(symbol)
        try:
            await self._qualify_contract(contract)
        except Exception as exc:
            return {**empty, "message": str(exc)}
        if self.market_data_source in {"historical", "ohlcv"}:
            return await self._historical_market_snapshot(
                symbol,
                contract,
                timeout=timeout,
            )
        if self.market_data_source in {"snapshot", "live"}:
            return await self._ticker_market_snapshot(symbol, contract, timeout=timeout)
        snapshot = await self._ticker_market_snapshot(symbol, contract, timeout=timeout)
        if snapshot.get("available"):
            return snapshot
        historical = await self._historical_market_snapshot(
            symbol,
            contract,
            timeout=timeout,
        )
        if historical.get("available"):
            return historical
        return {
            **historical,
            "message": historical.get("message") or snapshot.get("message") or empty["message"],
            "snapshot_message": snapshot.get("message"),
        }

    async def _ticker_market_snapshot(
        self,
        symbol: str,
        contract: Any,
        timeout: float,
    ) -> dict[str, Any]:
        empty = {
            "available": False,
            "source": self.connector_name,
            "market_data_source": "snapshot",
            "symbol": symbol,
            "message": "Market data is not available.",
        }
        started = self._record_tws_request_sent(
            "reqTickersAsync",
            f"symbol={symbol} snapshot=true",
        )
        try:
            tickers = await asyncio.wait_for(
                self._ib.reqTickersAsync(contract),
                timeout=timeout,
            )
        except Exception as exc:
            self._record_tws_request_result(
                started,
                "ERROR",
                _error_text(exc),
                extra={"symbol": symbol},
            )
            return {**empty, "message": str(exc)}
        ticker = tickers[0] if tickers else None
        fields = _ticker_fields(ticker)
        price = _ticker_price(ticker)
        self._record_tws_request_result(
            started,
            "OK",
            extra={
                "symbol": symbol,
                "market_data_source": "snapshot",
                "price": price,
                **fields,
            },
        )
        if price is None:
            return {
                **empty,
                "message": "TWS returned a quote without a usable price.",
                **fields,
            }
        return {
            "available": True,
            "source": self.connector_name,
            "market_data_source": "snapshot",
            "symbol": symbol,
            "price": price,
            **fields,
            "message": "",
        }

    async def _historical_market_snapshot(
        self,
        symbol: str,
        contract: Any,
        timeout: float,
        duration: str | None = None,
        bar_size: str | None = None,
    ) -> dict[str, Any]:
        duration = duration or self.historical_duration
        bar_size = bar_size or self.historical_bar_size
        started = self._record_tws_request_sent(
            "reqHistoricalDataAsync",
            (
                f"symbol={symbol} duration={duration} "
                f"barSize={bar_size} "
                f"whatToShow={self.historical_what_to_show} "
                f"useRTH={int(self.historical_use_rth)}"
            ),
        )
        try:
            bars = await asyncio.wait_for(
                self._ib.reqHistoricalDataAsync(
                    contract,
                    "",
                    duration,
                    bar_size,
                    self.historical_what_to_show,
                    self.historical_use_rth,
                    1,
                    False,
                ),
                timeout=timeout,
            )
        except Exception as exc:
            self._record_tws_request_result(
                started,
                "ERROR",
                _error_text(exc),
                extra={"symbol": symbol, "market_data_source": "historical"},
            )
            return {
                "available": False,
                "source": self.connector_name,
                "market_data_source": "historical",
                "symbol": symbol,
                "message": str(exc),
            }
        snapshot = _historical_quote_from_bars(
            symbol,
            self.connector_name,
            bars,
            bar_size=bar_size,
        )
        snapshot.update(
            {
                "historical_duration": duration,
                "historical_bar_size": bar_size,
                "historical_what_to_show": self.historical_what_to_show,
                "historical_use_rth": self.historical_use_rth,
            }
        )
        self._record_tws_request_result(
            started,
            "OK",
            extra={
                "symbol": symbol,
                "market_data_source": "historical",
                "bar_count": snapshot.get("bar_count"),
                "bar_date": snapshot.get("bar_date"),
                "price": snapshot.get("price"),
                "open": snapshot.get("open"),
                "high": snapshot.get("high"),
                "low": snapshot.get("low"),
                "close": snapshot.get("close"),
                "volume": snapshot.get("volume"),
                "previous_high": snapshot.get("previous_high"),
                "volume_ratio": snapshot.get("volume_ratio"),
            },
        )
        return snapshot

    async def historical_bars(
        self,
        symbol: str,
        duration: str,
        bar_size: str,
        timeout: float = 4.0,
    ) -> dict[str, Any]:
        symbol = symbol.upper()
        empty = {
            "available": False,
            "source": self.connector_name,
            "market_data_source": "historical",
            "symbol": symbol,
            "historical_duration": duration,
            "historical_bar_size": bar_size,
            "message": "Historical data is not available.",
        }
        if await self.status() != ConnectionStatus.CONNECTED or self._ib is None:
            return {**empty, "message": self.last_error or "TWS/Gateway is disconnected."}
        contract = self._stock_contract(symbol)
        try:
            await self._qualify_contract(contract)
        except Exception as exc:
            return {**empty, "message": str(exc)}
        return await self._historical_market_snapshot(
            symbol,
            contract,
            timeout=timeout,
            duration=duration,
            bar_size=bar_size,
        )

    def _is_connected(self) -> bool:
        return bool(self._ib is not None and self._ib.isConnected())

    def _mark_disconnected(self, message: str) -> None:
        self._status = ConnectionStatus.DISCONNECTED
        self.last_error = message

    async def _maybe_reconnect(self) -> None:
        if not self.reconnect:
            return
        now = time.monotonic()
        if now - self._last_reconnect_attempt < self.reconnect_interval_seconds:
            return
        self._last_reconnect_attempt = now
        await self.connect()

    def _record_tws_request_sent(self, name: str, detail: str = "") -> dict[str, Any]:
        sent_at = _utc_now_iso()
        sequence = int(self._diagnostics.get("tws_request_count") or 0) + 1
        self._diagnostics.update(
            {
                "tws_request_count": sequence,
                "last_tws_request": name,
                "last_tws_request_detail": detail,
                "last_tws_request_sent_at": sent_at,
                "last_tws_response_at": None,
                "last_tws_latency_ms": None,
                "last_tws_request_status": "SENT",
                "last_tws_request_error": "",
            }
        )
        return {
            "started": time.perf_counter(),
            "sequence": sequence,
            "request": name,
            "detail": detail,
            "sent_at": sent_at,
        }

    def _record_tws_request_result(
        self,
        started: dict[str, Any],
        status: str,
        error: str = "",
        extra: dict[str, Any] | None = None,
    ) -> None:
        response_at = _utc_now_iso()
        started_at = float(started.get("started") or time.perf_counter())
        latency_ms = round((time.perf_counter() - started_at) * 1000, 1)
        self._diagnostics.update(
            {
                "last_tws_response_at": response_at,
                "last_tws_latency_ms": latency_ms,
                "last_tws_request_status": status,
                "last_tws_request_error": error,
            }
        )
        entry = {
            "sequence": started.get("sequence"),
            "request": started.get("request", ""),
            "detail": started.get("detail", ""),
            "sent_at": started.get("sent_at"),
            "response_at": response_at,
            "latency_ms": latency_ms,
            "status": status,
            "error": error,
            "connector": self.connector_name,
            "account_mode": self.account_mode,
            "host": self.host,
            "port": self.port,
            "client_id": self.client_id,
        }
        if extra:
            entry["extra"] = extra
        if self.audit_enabled:
            self._audit_entries.append(entry)
            del self._audit_entries[:-500]
            log_prefix = (
                "TWS cache read"
                if str(entry["detail"]).lower() == "session cache"
                else "TWS request"
            )
            if status == "OK":
                log = logger.debug if _is_routine_tws_success(entry) else logger.info
                extra_text = _format_tws_extra_for_log(entry.get("extra"))
                log(
                    "%s OK: %s %s %.1fms",
                    log_prefix,
                    entry["request"],
                    _append_log_detail(entry["detail"], extra_text),
                    latency_ms,
                )
            else:
                logger.error(
                    "%s ERROR: %s %s %.1fms %s",
                    log_prefix,
                    entry["request"],
                    entry["detail"],
                    latency_ms,
                    error,
                )
        self._diagnostics["tws_audit_pending"] = len(self._audit_entries)

    def _stock_contract(self, symbol: str):
        from ib_async import Stock

        return Stock(symbol, "SMART", "USD")

    async def _qualify_contract(self, contract) -> None:
        started = self._record_tws_request_sent(
            "qualifyContractsAsync",
            _contract_detail(contract),
        )
        try:
            result = self._ib.qualifyContractsAsync(contract)
            if inspect.isawaitable(result):
                await result
        except Exception as exc:
            self._record_tws_request_result(started, "ERROR", _error_text(exc))
            raise
        self._record_tws_request_result(started, "OK")

    def _build_order(self, request: BrokerOrderRequest):
        from ib_async import LimitOrder, MarketOrder, StopLimitOrder, StopOrder

        if request.order_type == "MKT":
            return MarketOrder(request.side, request.quantity)
        if request.order_type == "LMT":
            return LimitOrder(request.side, request.quantity, request.limit_price)
        if request.order_type == "STP":
            return StopOrder(request.side, request.quantity, request.stop_price)
        if request.order_type == "STP_LMT":
            return StopLimitOrder(
                request.side,
                request.quantity,
                request.limit_price,
                request.trigger_price,
            )
        raise ValueError(f"Unsupported TWS order type: {request.order_type}")

    async def _sleep(self, seconds: float) -> None:
        result = self._ib.sleep(seconds)
        if inspect.isawaitable(result):
            await result


def create_broker_connector(
    connector_name: str,
    config: dict[str, Any] | None = None,
) -> BrokerConnector:
    connector = connector_name.strip().lower()
    if connector == "simulated":
        return SimulatedBrokerConnector()
    if connector in {"paper", "live"}:
        return IbAsyncTwsConnector(connector, config or {})
    raise ValueError("broker.connector must be simulated, paper or live")


def _float_or_none(value: Any) -> float | None:
    if value in (None, "", 0, 0.0):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _number_or_none(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _error_text(error: Exception) -> str:
    text = str(error).strip()
    return text or error.__class__.__name__


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _contract_detail(contract: Any) -> str:
    symbol = str(getattr(contract, "symbol", "") or "")
    exchange = str(getattr(contract, "exchange", "") or "")
    currency = str(getattr(contract, "currency", "") or "")
    sec_type = str(getattr(contract, "secType", "") or "")
    details = [
        f"symbol={symbol}" if symbol else "",
        f"secType={sec_type}" if sec_type else "",
        f"exchange={exchange}" if exchange else "",
        f"currency={currency}" if currency else "",
    ]
    return " ".join(item for item in details if item)


def _ticker_fields(ticker: Any) -> dict[str, float | None]:
    if ticker is None:
        return {
            "bid": None,
            "ask": None,
            "last": None,
            "open": None,
            "high": None,
            "low": None,
            "close": None,
            "volume": None,
        }
    return {
        "bid": _number_or_none(getattr(ticker, "bid", None)),
        "ask": _number_or_none(getattr(ticker, "ask", None)),
        "last": _number_or_none(getattr(ticker, "last", None)),
        "open": _number_or_none(getattr(ticker, "open", None)),
        "high": _number_or_none(getattr(ticker, "high", None)),
        "low": _number_or_none(getattr(ticker, "low", None)),
        "close": _number_or_none(getattr(ticker, "close", None)),
        "volume": _number_or_none(getattr(ticker, "volume", None)),
    }


def _ticker_price(ticker: Any) -> float | None:
    if ticker is None:
        return None
    market_price = None
    with suppress(Exception):
        market_price = _number_or_none(ticker.marketPrice())
    if market_price is not None and market_price > 0:
        return market_price
    fields = _ticker_fields(ticker)
    for key in ("last", "close"):
        value = fields.get(key)
        if value is not None and value > 0:
            return value
    bid = fields.get("bid")
    ask = fields.get("ask")
    if bid is not None and ask is not None and bid > 0 and ask > 0:
        return round((bid + ask) / 2, 4)
    for value in (bid, ask):
        if value is not None and value > 0:
            return value
    return None


def _is_routine_tws_success(entry: dict[str, Any]) -> bool:
    return str(entry.get("request") or "") in {"accountValues", "reqCurrentTime"}


def _append_log_detail(detail: Any, extra_text: str) -> str:
    detail_text = str(detail or "").strip()
    if not extra_text:
        return detail_text
    return f"{detail_text} {extra_text}".strip()


def _format_tws_extra_for_log(extra: Any) -> str:
    if not isinstance(extra, dict):
        return ""
    keys = (
        "symbol",
        "market_data_source",
        "price",
        "bid",
        "ask",
        "last",
        "open",
        "high",
        "low",
        "close",
        "volume",
        "previous_high",
        "volume_ratio",
        "volume_ratio_closed_bar",
        "atr_15m",
        "atr_1h",
        "bar_date",
        "bar_count",
    )
    parts = [
        f"{key}={extra[key]}"
        for key in keys
        if extra.get(key) not in (None, "")
    ]
    return " ".join(parts)


def _historical_quote_from_bars(
    symbol: str,
    source: str,
    bars: Any,
    bar_size: str = "",
) -> dict[str, Any]:
    rows = [
        row
        for row in (_bar_to_ohlcv(bar) for bar in list(bars or []))
        if row.get("close") is not None
    ]
    if not rows:
        return {
            "available": False,
            "source": source,
            "market_data_source": "historical",
            "symbol": symbol,
            "bar_count": 0,
            "message": f"No historical OHLCV data returned for {symbol}.",
        }
    latest = rows[-1]
    previous = rows[-2] if len(rows) > 1 else {}
    price = _number_or_none(latest.get("close"))
    if price is None or price <= 0:
        return {
            "available": False,
            "source": source,
            "market_data_source": "historical",
            "symbol": symbol,
            "bar_count": len(rows),
            "bar_date": latest.get("date"),
            "message": f"Historical OHLCV for {symbol} did not include a usable close.",
        }
    atr = average_true_range(rows, period=14)
    bar_size_normalized = str(bar_size or "").strip().lower()
    atr_15m = atr if "15" in bar_size_normalized and "min" in bar_size_normalized else None
    atr_1h = atr if (
        "1 hour" in bar_size_normalized
        or "1h" in bar_size_normalized
        or "60" in bar_size_normalized and "min" in bar_size_normalized
    ) else None
    return {
        "available": True,
        "source": source,
        "market_data_source": "historical",
        "symbol": symbol,
        "price": price,
        "bid": None,
        "ask": None,
        "last": price,
        "open": latest.get("open"),
        "high": latest.get("high"),
        "low": latest.get("low"),
        "close": latest.get("close"),
        "volume": latest.get("volume"),
        "previous_high": previous.get("high"),
        "volume_ratio": _historical_volume_ratio(rows),
        "volume_ratio_closed_bar": _historical_volume_ratio(rows),
        "atr_15m": atr_15m,
        "atr_1h": atr_1h,
        "bar_date": latest.get("date"),
        "bar_count": len(rows),
        "historical_bars": rows[-180:],
        "message": "",
    }


def _bar_to_ohlcv(bar: Any) -> dict[str, Any]:
    return {
        "date": _date_text(getattr(bar, "date", None)),
        "open": _number_or_none(getattr(bar, "open", None)),
        "high": _number_or_none(getattr(bar, "high", None)),
        "low": _number_or_none(getattr(bar, "low", None)),
        "close": _number_or_none(getattr(bar, "close", None)),
        "volume": _int_or_none(getattr(bar, "volume", None)),
    }


def _historical_volume_ratio(rows: list[dict[str, Any]]) -> float | None:
    if len(rows) < 2:
        return None
    latest_volume = _number_or_none(rows[-1].get("volume"))
    previous_volumes = [
        volume
        for volume in (_number_or_none(row.get("volume")) for row in rows[:-1])
        if volume is not None and volume > 0
    ]
    if latest_volume is None or latest_volume <= 0 or not previous_volumes:
        return None
    average_volume = sum(previous_volumes) / len(previous_volumes)
    if average_volume <= 0:
        return None
    return round(latest_volume / average_volume, 4)


def _date_text(value: Any) -> str | None:
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    return str(value)


def _int_or_none(value: Any) -> int | None:
    number = _number_or_none(value)
    return int(number) if number is not None else None


def _is_endpoint_unavailable_error(error: Exception) -> bool:
    if isinstance(error, ConnectionRefusedError):
        return True
    text = f"{error.__class__.__name__}: {error}".lower()
    return any(
        item in text
        for item in (
            "connectionrefused",
            "connection refused",
            "refusé la connexion",
            "refused the network connection",
            "connect call failed",
            "network is unreachable",
            "no route to host",
        )
    )


def _normalize_order_type(value: str) -> str:
    normalized = value.upper().replace(" ", "_")
    if normalized == "STP_LMT":
        return "STP_LMT"
    return normalized
