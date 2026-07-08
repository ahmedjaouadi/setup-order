from __future__ import annotations

import asyncio
import inspect
import logging
import math
import random
import re
import time
from abc import ABC, abstractmethod
from contextlib import suppress
from datetime import UTC, date, datetime
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ib_async import IB, Order

from app.broker.broker_errors import BrokerDisconnectedError
from app.broker.ib_models import (
    BrokerExecution,
    BrokerOrderRequest,
    BrokerOrderResult,
    BrokerPosition,
)
from app.market_data.indicators import average_true_range, simple_average_true_range
from app.models import ConnectionStatus, OrderStatus
from app.utils.market_hours import current_us_equity_session_context

logger = logging.getLogger(__name__)
ATR_PERIOD = 14
BARS_REQUIRED_FOR_ATR = ATR_PERIOD + 1
LIVE_MARKET_DATA_TYPE = 1
MARKET_DATA_TYPE_LABELS = {
    1: "LIVE",
    2: "FROZEN",
    3: "DELAYED",
    4: "DELAYED_FROZEN",
}


class LiveQuoteTimeoutError(RuntimeError):
    """Raised when a live ticker does not produce a complete bid/ask quote in time."""


class LiveQuoteRegistry:
    def __init__(self, ib: Any) -> None:
        self._ib = ib
        self._subscriptions: dict[str, dict[str, Any]] = {}

    def subscribe(
        self,
        symbol: str,
        contract: Any,
        market_data_type: int,
    ) -> tuple[dict[str, Any], bool]:
        key = self._key(symbol, market_data_type)
        existing = self._subscriptions.get(key)
        if existing is not None:
            return existing, False

        req_id = self._ib.client.getReqId()
        ticker = self._ib.wrapper.startTicker(req_id, contract, "mktData")
        try:
            self._ib.client.reqMktData(req_id, contract, "", False, False, [])
        except Exception:
            with suppress(Exception):
                self._ib.wrapper.endTicker(ticker, "mktData")
            raise

        entry = {
            "key": key,
            "symbol": symbol.upper(),
            "contract": contract,
            "contract_detail": _contract_detail(contract),
            "market_data_type_requested": int(market_data_type),
            "req_id": req_id,
            "ticker": ticker,
            "source": "reqMktData",
            "subscribed_at": _utc_now_iso(),
            "last_quote_at": None,
            "last_quote_monotonic": None,
            "last_signature": None,
        }
        self._subscriptions[key] = entry
        return entry, True

    def latest(
        self,
        symbol: str,
        market_data_type: int,
    ) -> dict[str, Any] | None:
        entry = self._subscriptions.get(self._key(symbol, market_data_type))
        if entry is None:
            return None
        return self._snapshot(entry)

    def diagnostics(self, symbol: str | None = None) -> dict[str, Any]:
        normalized = symbol.upper() if symbol else None
        snapshots = [
            self._snapshot(entry)
            for entry in self._subscriptions.values()
            if normalized is None or entry["symbol"] == normalized
        ]
        return {
            "active_subscription_count": len(snapshots),
            "subscriptions": snapshots,
        }

    def unsubscribe_all(self) -> None:
        for key in list(self._subscriptions):
            self._unsubscribe_key(key)

    def unsubscribe(self, symbol: str, market_data_type: int) -> bool:
        key = self._key(symbol, market_data_type)
        if key not in self._subscriptions:
            return False
        self._unsubscribe_key(key)
        return True

    def _snapshot(self, entry: dict[str, Any]) -> dict[str, Any]:
        ticker = entry.get("ticker")
        fields = _ticker_fields(ticker)
        price = _ticker_price(ticker)
        spread = calculate_spread(fields.get("bid"), fields.get("ask"))
        spread_bps = _spread_bps(fields.get("bid"), fields.get("ask"), spread)
        signature = (
            price,
            fields.get("bid"),
            fields.get("ask"),
            fields.get("last"),
            fields.get("market_data_type_actual"),
            fields.get("open"),
            fields.get("high"),
            fields.get("low"),
            fields.get("close"),
            fields.get("volume"),
            spread,
        )
        quote_values = tuple(value for index, value in enumerate(signature) if index != 4)
        if any(value is not None for value in quote_values) and signature != entry.get(
            "last_signature"
        ):
            entry["last_quote_at"] = _utc_now_iso()
            entry["last_quote_monotonic"] = time.monotonic()
            entry["last_signature"] = signature

        quote_age = None
        if entry.get("last_quote_monotonic") is not None:
            quote_age = round(time.monotonic() - float(entry["last_quote_monotonic"]), 3)

        snapshot = {
            "subscription": {
                "active": True,
                "req_id": entry.get("req_id"),
                "source": entry.get("source"),
                "subscribed_at": entry.get("subscribed_at"),
                "contract": entry.get("contract_detail"),
                "market_data_type_requested": entry.get("market_data_type_requested"),
                "market_data_type_actual": fields.get("market_data_type_actual"),
            },
            "symbol": entry.get("symbol"),
            "req_id": entry.get("req_id"),
            "contract": entry.get("contract_detail"),
            "timestamp": entry.get("last_quote_at"),
            "quote_age_seconds": quote_age,
            "price": price,
            **fields,
            "live_market_data_status": _live_market_data_status(
                fields.get("market_data_type_actual")
            ),
            "spread": spread,
            "spread_bps": spread_bps,
        }
        snapshot["quote_state"] = _live_quote_state(snapshot)
        snapshot["missing_fields"] = _missing_live_quote_fields(snapshot)
        return snapshot

    def _unsubscribe_key(self, key: str) -> None:
        entry = self._subscriptions.pop(key, None)
        if entry is None:
            return
        cancel_req_id = entry.get("req_id")
        ticker = entry.get("ticker")
        if ticker is not None:
            with suppress(Exception):
                cancel_req_id = self._ib.wrapper.endTicker(ticker, "mktData") or cancel_req_id
        if cancel_req_id:
            with suppress(Exception):
                self._ib.client.cancelMktData(cancel_req_id)

    @staticmethod
    def _key(symbol: str, market_data_type: int) -> str:
        return f"{symbol.upper()}:{int(market_data_type)}"


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

    async def can_submit_orders(self) -> bool:
        return await self.status() == ConnectionStatus.CONNECTED

    async def order_statuses(self) -> dict[str, str]:
        return {}

    async def recent_executions(self) -> list[BrokerExecution]:
        return []

    def market_data_diagnostics(self, symbol: str) -> dict[str, Any]:
        return {
            "symbol": symbol.upper(),
            "message": "Market data diagnostics are not available for this broker.",
        }

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

    async def modify_stop_order(
        self,
        broker_order_id: str,
        new_stop: float,
    ) -> BrokerOrderResult:
        """Modify the stop level of a working stop order at the broker.

        Default implementation rejects: connectors that support in-place order
        modification override this.
        """
        return BrokerOrderResult(
            accepted=False,
            status=OrderStatus.REJECTED.value,
            broker_order_id=str(broker_order_id),
            reason=f"Stop modification is not supported by the {self.connector_name} broker.",
        )

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
    account_mode = "paper"
    display_name = "Internal test broker"
    supports_external_account = False

    def __init__(self, account_mode: str = "paper") -> None:
        normalized_mode = str(account_mode or "paper").strip().lower()
        if normalized_mode not in {"paper", "live"}:
            normalized_mode = "paper"
        self.account_mode = normalized_mode
        self.display_name = f"Internal {normalized_mode} test broker"
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

    def diagnostics(self) -> dict[str, Any]:
        return {
            "connector": self.connector_name,
            "account_mode": self.account_mode,
            "status": self._status.value,
            "open_orders": len(self._orders),
            "positions": len([item for item in self._positions.values() if item.quantity != 0]),
            "supports_external_account": self.supports_external_account,
        }

    async def market_snapshot(self, symbol: str, timeout: float = 4.0) -> dict[str, Any]:
        normalized = symbol.upper()
        position = self._positions.get(normalized)
        if position is None:
            return {
                "available": False,
                "symbol": normalized,
                "source": self.connector_name,
                "message": "No simulated market price is available for this symbol.",
            }
        return {
            "available": True,
            "symbol": normalized,
            "source": self.connector_name,
            "market_data_source": "simulated_position",
            "price": position.current_price,
            "last": position.current_price,
            "timestamp": _utc_now_iso(),
        }

    async def submit_order(self, request: BrokerOrderRequest) -> BrokerOrderResult:
        if self._status != ConnectionStatus.CONNECTED:
            raise BrokerDisconnectedError("Simulated broker is disconnected")
        request.symbol = request.symbol.upper()
        request.side = request.side.upper()
        request.order_type = _normalize_order_type(request.order_type)
        if request.quantity <= 0:
            return BrokerOrderResult(
                accepted=False,
                status=OrderStatus.REJECTED.value,
                reason="Quantity must be positive",
            )
        self._next_order_id += 1
        broker_order_id = str(self._next_order_id)
        perm_id = f"SIM-{broker_order_id}"
        request.status = OrderStatus.SUBMITTED.value
        request.broker_order_id = broker_order_id
        request.broker_perm_id = perm_id
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

    async def modify_stop_order(
        self,
        broker_order_id: str,
        new_stop: float,
    ) -> BrokerOrderResult:
        if self._status != ConnectionStatus.CONNECTED:
            raise BrokerDisconnectedError("Simulated broker is disconnected")
        order = self._orders.get(broker_order_id)
        if order is None:
            return BrokerOrderResult(
                accepted=False,
                status=OrderStatus.REJECTED.value,
                broker_order_id=broker_order_id,
                reason="Order not found",
            )
        order.stop_price = new_stop
        if order.order_type == "STP_LMT":
            order.trigger_price = new_stop
        return BrokerOrderResult(
            accepted=True,
            status=OrderStatus.SUBMITTED.value,
            broker_order_id=broker_order_id,
            broker_perm_id=order.broker_perm_id,
            reason="Stop modified by simulated broker",
        )

    async def open_orders(self) -> list[BrokerOrderRequest]:
        return list(self._orders.values())

    async def order_statuses(self) -> dict[str, str]:
        statuses: dict[str, str] = {}
        for broker_order_id, request in self._orders.items():
            status = request.status or OrderStatus.SUBMITTED.value
            statuses[str(broker_order_id)] = status
            statuses[str(request.client_order_id)] = status
            if request.broker_perm_id:
                statuses[str(request.broker_perm_id)] = status
        return statuses

    async def recent_executions(self) -> list[BrokerExecution]:
        return []

    async def positions(self) -> list[BrokerPosition]:
        return list(self._positions.values())

    async def account_summary(self) -> dict[str, Any]:
        return {
            "available": False,
            "source": self.connector_name,
            "currency": "USD",
            "message": "Internal test broker does not expose broker account cash.",
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

    async def recent_executions(self) -> list[BrokerExecution]:
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
        self.port = int(config.get("port") or config.get(f"{connector_name}_port") or default_port)
        self.client_id = int(config.get("client_id", 1001))
        self.reconnect = bool(config.get("reconnect", True))
        self.reconnect_interval_seconds = float(config.get("reconnect_interval_seconds", 5))
        self.audit_enabled = bool(config.get("tws_audit_enabled", True))
        self.market_data_source = str(config.get("market_data_source", "historical")).lower()
        self.market_data_type = int(config.get("market_data_type", 1))
        self.market_data_type_fallbacks = _int_list(config.get("market_data_type_fallbacks", []))
        self.live_quote_wait_seconds = float(config.get("live_quote_wait_seconds", 2.0))
        self.historical_request_timeout_seconds = float(
            config.get("historical_request_timeout_seconds")
            or config.get("historical_timeout_seconds")
            or 12.0
        )
        self.market_data_policy = _market_data_policy_config(
            config,
            mode=self.account_mode,
        )
        self.indicator_policy = _indicator_policy_config(config)
        self.market_data_ttl = _market_data_ttl_config(config)
        self.market_data_ttl["atr_1h_seconds"] = max(
            self.market_data_ttl["atr_1h_seconds"],
            float(self.indicator_policy["atr_1h"]["stale_after_minutes"]) * 60,
            3600.0,
        )
        self.historical_duration = str(config.get("historical_duration", "30 D"))
        self.historical_bar_size = str(config.get("historical_bar_size", "1 day"))
        self.hybrid_signal_duration = str(config.get("hybrid_signal_duration", "5 D"))
        self.hybrid_signal_bar_size = str(config.get("hybrid_signal_bar_size", "15 mins"))
        self.hybrid_atr_1h_duration = str(config.get("hybrid_atr_1h_duration", "30 D"))
        self.hybrid_atr_1h_bar_size = str(config.get("hybrid_atr_1h_bar_size", "1 hour"))
        self.historical_what_to_show = str(config.get("historical_what_to_show", "TRADES"))
        self.historical_use_rth = bool(config.get("historical_use_rth", True))
        self.stock_exchange = str(config.get("stock_exchange", "SMART") or "SMART")
        self.primary_exchange = str(config.get("primary_exchange", "") or "")
        raw_primary_by_symbol = config.get("primary_exchange_by_symbol", {})
        self.primary_exchange_by_symbol = (
            {
                str(symbol).strip().upper(): str(exchange).strip()
                for symbol, exchange in raw_primary_by_symbol.items()
                if str(symbol).strip() and str(exchange).strip()
            }
            if isinstance(raw_primary_by_symbol, dict)
            else {}
        )
        self.last_error = ""
        self._status = ConnectionStatus.DISCONNECTED
        self._ib: IB | None = None
        self._live_quotes: LiveQuoteRegistry | None = None
        self._account_pnl_account: str | None = None
        self._historical_cache: dict[tuple[str, str, str], dict[str, Any]] = {}
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
                self._live_quotes = LiveQuoteRegistry(ib)
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
            self._unsubscribe_live_quotes()
            self._unsubscribe_account_pnl()
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
        self._live_quotes = None
        self._account_pnl_account = None
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
            self._mark_disconnected(f"TWS/Gateway heartbeat failed: {_error_text(exc)}")
            with suppress(Exception):
                self._ib.disconnect()
            return self._status
        self._record_tws_request_result(started, "OK")
        self._status = ConnectionStatus.CONNECTED
        self.last_error = ""
        return ConnectionStatus.CONNECTED

    def diagnostics(self) -> dict[str, Any]:
        live_quote_diagnostics = (
            self._live_quotes.diagnostics()
            if self._live_quotes is not None
            else {"active_subscription_count": 0, "subscriptions": []}
        )
        return {
            **self._diagnostics,
            "tws_audit_enabled": self.audit_enabled,
            "tws_audit_pending": len(self._audit_entries),
            "live_quotes": live_quote_diagnostics,
            "historical_cache_entries": len(self._historical_cache),
        }

    def market_data_diagnostics(self, symbol: str) -> dict[str, Any]:
        symbol = symbol.upper()
        live_quotes = (
            self._live_quotes.diagnostics(symbol)
            if self._live_quotes is not None
            else {"active_subscription_count": 0, "subscriptions": []}
        )
        historical = [
            {
                "symbol": key_symbol,
                "duration": duration,
                "bar_size": bar_size,
                "updated_at": entry.get("updated_at"),
                "age_seconds": _age_seconds(entry.get("updated_at")),
                "status": (
                    "STALE"
                    if _is_older_than(
                        entry.get("updated_at"),
                        self._historical_cache_ttl_seconds(bar_size),
                    )
                    else "OK"
                ),
            }
            for (key_symbol, duration, bar_size), entry in self._historical_cache.items()
            if key_symbol == symbol
        ]
        return {
            "symbol": symbol,
            "subscription": live_quotes,
            "indicators": historical,
            "readiness_policy": {
                "live_quote_seconds": self.market_data_ttl["live_quote_seconds"],
                "atr_15m_seconds": self.market_data_ttl["atr_15m_seconds"],
                "atr_1h_seconds": self.market_data_ttl["atr_1h_seconds"],
                "market_data": self.market_data_policy,
                "indicators": self.indicator_policy,
            },
            "atr_1h_request": {
                "barSizeSetting": self.hybrid_atr_1h_bar_size,
                "durationStr": self.hybrid_atr_1h_duration,
                "whatToShow": self.historical_what_to_show,
                "useRTH": self.historical_use_rth,
                "bars_required_for_atr": BARS_REQUIRED_FOR_ATR,
            },
            "last_ibkr_error": self._diagnostics.get("last_tws_request_error") or None,
            "last_ibkr_error_code": _ibkr_error_code(
                self._diagnostics.get("last_tws_request_error")
            ),
            "last_ibkr_error_message": (self._diagnostics.get("last_tws_request_error") or None),
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
            status_detail = _trade_status_detail(trade)
            result_status = _tws_order_status_to_order_status(status)
            accepted = result_status not in {
                OrderStatus.CANCELLED.value,
                OrderStatus.REJECTED.value,
                OrderStatus.ERROR.value,
            }
            self._record_tws_request_result(
                started,
                "OK",
                extra={
                    "broker_order_id": broker_order_id,
                    "broker_perm_id": perm_id,
                    "order_status": status,
                    "status_detail": status_detail,
                },
            )
            reason = f"Accepted by TWS: {status}"
            if status_detail:
                if accepted:
                    reason = f"{reason} | {status_detail}"
                else:
                    reason = f"TWS status {status}: {status_detail}"
            return BrokerOrderResult(
                accepted=accepted,
                status=result_status,
                broker_order_id=broker_order_id or None,
                broker_perm_id=perm_id or None,
                reason=reason,
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

    async def modify_stop_order(
        self,
        broker_order_id: str,
        new_stop: float,
    ) -> BrokerOrderResult:
        if await self.status() != ConnectionStatus.CONNECTED or self._ib is None:
            raise BrokerDisconnectedError(self.last_error or "TWS/Gateway is disconnected")
        for trade in self._ib.trades():
            if str(getattr(trade.order, "orderId", "")) != str(broker_order_id):
                continue
            order = trade.order
            order_type = str(getattr(order, "orderType", "") or "").upper()
            if order_type not in {"STP", "STP LMT", "STP_LMT", "TRAIL"}:
                return BrokerOrderResult(
                    accepted=False,
                    status=OrderStatus.REJECTED.value,
                    broker_order_id=str(broker_order_id),
                    reason=f"Order {broker_order_id} is not a stop order ({order_type})",
                )
            started = self._record_tws_request_sent(
                "modifyOrder",
                f"orderId={broker_order_id} new_stop={new_stop}",
            )
            try:
                # TWS modifies an order in place when placeOrder is re-sent
                # with the same orderId; auxPrice carries the stop trigger.
                order.auxPrice = new_stop
                trade = self._ib.placeOrder(trade.contract, order)
                await self._sleep(0.2)
            except Exception as exc:
                self._record_tws_request_result(started, "ERROR", _error_text(exc))
                return BrokerOrderResult(
                    accepted=False,
                    status=OrderStatus.REJECTED.value,
                    broker_order_id=str(broker_order_id),
                    reason=str(exc),
                )
            status = str(getattr(trade.orderStatus, "status", "") or "Submitted")
            result_status = _tws_order_status_to_order_status(status)
            accepted = result_status not in {
                OrderStatus.CANCELLED.value,
                OrderStatus.REJECTED.value,
                OrderStatus.ERROR.value,
            }
            self._record_tws_request_result(
                started,
                "OK" if accepted else "ERROR",
                extra={"order_status": status, "new_stop": new_stop},
            )
            return BrokerOrderResult(
                accepted=accepted,
                status=result_status,
                broker_order_id=str(broker_order_id),
                broker_perm_id=str(getattr(order, "permId", "") or "") or None,
                reason=f"Stop modification sent to TWS: {status}",
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
        # reqAllOpenOrders includes orders entered manually in TWS (other
        # client ids); reqOpenOrders only returns this API client's orders.
        refresh = getattr(self._ib, "reqAllOpenOrdersAsync", None) or self._ib.reqOpenOrdersAsync
        refresh_started = self._record_tws_request_sent(
            getattr(refresh, "__name__", "reqAllOpenOrdersAsync"),
            "refresh open orders",
        )
        try:
            await asyncio.wait_for(refresh(), timeout=2.0)
        except Exception as exc:
            self._record_tws_request_result(refresh_started, "ERROR", _error_text(exc))
        else:
            self._record_tws_request_result(refresh_started, "OK")
        requests: list[BrokerOrderRequest] = []
        started = self._record_tws_request_sent("openTrades", "session cache")
        try:
            trades = list(self._ib.openTrades())
        except Exception as exc:
            self._record_tws_request_result(started, "ERROR", _error_text(exc))
            raise
        self._record_tws_request_result(started, "OK", extra={"count": len(trades)})
        for trade in trades:
            contract = trade.contract
            order = trade.order
            raw_status = str(getattr(trade.orderStatus, "status", "") or "")
            # Orders entered manually in TWS come back with orderId 0; only
            # permId identifies them reliably.
            raw_order_id = str(getattr(order, "orderId", "") or "")
            broker_order_id = raw_order_id if raw_order_id not in {"", "0"} else ""
            broker_perm_id = str(getattr(order, "permId", "") or "") or None
            status = _tws_order_status_to_order_status(raw_status)
            filled_quantity = _float_or_none(getattr(trade.orderStatus, "filled", None))
            remaining_quantity = _float_or_none(getattr(trade.orderStatus, "remaining", None))
            order_type = _normalize_order_type(str(getattr(order, "orderType", "")))
            # auxPrice is the stop trigger only for stop-type orders; for a
            # plain LMT/MKT order the field is an UNSET sentinel, not a stop.
            aux_price = _ib_price_or_none(getattr(order, "auxPrice", None))
            is_stop_type = order_type in {"STP", "STP_LMT"} or order_type.startswith("TRAIL")
            if order_type.startswith("TRAIL"):
                # For trailing stops auxPrice is the trailing amount; the
                # actual trigger lives in trailStopPrice.
                stop_price = _ib_price_or_none(getattr(order, "trailStopPrice", None))
            else:
                stop_price = aux_price if is_stop_type else None
            requests.append(
                BrokerOrderRequest(
                    client_order_id=broker_order_id or broker_perm_id or "",
                    setup_id="broker",
                    symbol=str(getattr(contract, "symbol", "")),
                    side=str(getattr(order, "action", "")),
                    order_type=order_type,
                    quantity=int(abs(float(getattr(order, "totalQuantity", 0) or 0))),
                    trigger_price=aux_price if is_stop_type else None,
                    limit_price=_ib_price_or_none(getattr(order, "lmtPrice", None)),
                    stop_price=stop_price,
                    parent_id=str(getattr(order, "parentId", "") or "") or None,
                    oca_group=str(getattr(order, "ocaGroup", "") or "") or None,
                    transmit=bool(getattr(order, "transmit", True)),
                    status=status,
                    raw_status=raw_status,
                    broker_status=_tws_raw_order_status_to_broker_status(
                        raw_status,
                        bool(getattr(order, "transmit", True)),
                    ),
                    broker_order_id=broker_order_id or None,
                    broker_perm_id=broker_perm_id,
                    filled_quantity=filled_quantity,
                    remaining_quantity=remaining_quantity,
                )
            )
        return requests

    async def order_statuses(self) -> dict[str, str]:
        if await self.status() != ConnectionStatus.CONNECTED or self._ib is None:
            return {}
        started = self._record_tws_request_sent("trades", "session cache")
        try:
            trades = list(self._ib.trades())
        except Exception as exc:
            self._record_tws_request_result(started, "ERROR", _error_text(exc))
            return {}
        self._record_tws_request_result(started, "OK", extra={"count": len(trades)})
        statuses: dict[str, str] = {}
        for trade in trades:
            order = trade.order
            status = _tws_order_status_to_order_status(
                str(getattr(trade.orderStatus, "status", "") or "")
            )
            order_id = str(getattr(order, "orderId", "") or "")
            perm_id = str(getattr(order, "permId", "") or "")
            if order_id:
                statuses[order_id] = status
            if perm_id:
                statuses[perm_id] = status
        return statuses

    async def recent_executions(self) -> list[BrokerExecution]:
        if await self.status() != ConnectionStatus.CONNECTED or self._ib is None:
            return []
        # ib.fills() pairs each execution with its contract; ib.executions()
        # returns bare Execution objects without symbol information.
        started = self._record_tws_request_sent("fills", "session cache")
        try:
            fills = list(self._ib.fills())
        except Exception as exc:
            self._record_tws_request_result(started, "ERROR", _error_text(exc))
            return []
        self._record_tws_request_result(started, "OK", extra={"count": len(fills)})
        rows: list[BrokerExecution] = []
        for fill in fills:
            contract = getattr(fill, "contract", None)
            exec_detail = getattr(fill, "execution", fill)
            side = str(getattr(exec_detail, "side", "") or "").upper()
            rows.append(
                BrokerExecution(
                    execution_id=str(getattr(exec_detail, "execId", "") or ""),
                    symbol=str(getattr(contract, "symbol", "") or ""),
                    side={"BOT": "BUY", "SLD": "SELL"}.get(side, side),
                    quantity=float(getattr(exec_detail, "shares", 0) or 0),
                    price=float(getattr(exec_detail, "price", 0) or 0),
                    order_id=str(getattr(exec_detail, "orderId", "") or "") or None,
                    broker_perm_id=str(getattr(exec_detail, "permId", "") or "") or None,
                    timestamp=str(getattr(exec_detail, "time", "") or "") or None,
                )
            )
        return rows

    async def positions(self) -> list[BrokerPosition]:
        if await self.status() != ConnectionStatus.CONNECTED or self._ib is None:
            return []
        refresh_started = self._record_tws_request_sent("reqPositionsAsync", "refresh positions")
        try:
            await asyncio.wait_for(self._ib.reqPositionsAsync(), timeout=2.0)
        except Exception as exc:
            self._record_tws_request_result(refresh_started, "ERROR", _error_text(exc))
        else:
            self._record_tws_request_result(refresh_started, "OK")
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
                    getattr(portfolio_item, "averageCost", average_price) or average_price
                )
                current_price = float(
                    getattr(portfolio_item, "marketPrice", current_price) or current_price
                )
                unrealized_pnl = _number_or_none(getattr(portfolio_item, "unrealizedPNL", None))
                realized_pnl = _number_or_none(getattr(portfolio_item, "realizedPNL", None))
            else:
                unrealized_pnl = None
                realized_pnl = None
            result.append(
                BrokerPosition(
                    symbol=symbol,
                    quantity=quantity,
                    average_price=average_price,
                    current_price=current_price,
                    market_price=current_price,
                    unrealized_pnl=unrealized_pnl,
                    realized_pnl=realized_pnl,
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
        account_pnl = self._ensure_account_pnl_subscription()
        if account_pnl is not None:
            daily_pnl = _number_or_none(getattr(account_pnl, "dailyPnL", None))
            unrealized_pnl = _number_or_none(getattr(account_pnl, "unrealizedPnL", None))
            realized_pnl = _number_or_none(getattr(account_pnl, "realizedPnL", None))
            if daily_pnl is not None:
                summary["today_pnl"] = daily_pnl
            if unrealized_pnl is not None and summary.get("unrealized_pnl") is None:
                summary["unrealized_pnl"] = unrealized_pnl
            if realized_pnl is not None and summary.get("realized_pnl") is None:
                summary["realized_pnl"] = realized_pnl
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
        if self.market_data_source in {"hybrid", "combined", "enriched"}:
            return await self._hybrid_market_snapshot(
                symbol,
                contract,
                timeout=timeout,
            )
        if self.market_data_source in {"historical", "ohlcv"}:
            return await self._historical_market_snapshot(
                symbol,
                contract,
                timeout=max(float(timeout or 0), self.historical_request_timeout_seconds),
            )
        if self.market_data_source in {"snapshot", "live"}:
            return await self._ticker_market_snapshot(symbol, contract, timeout=timeout)
        snapshot = await self._ticker_market_snapshot(symbol, contract, timeout=timeout)
        if snapshot.get("available"):
            return snapshot
        historical = await self._historical_market_snapshot(
            symbol,
            contract,
            timeout=max(float(timeout or 0), self.historical_request_timeout_seconds),
        )
        if historical.get("available"):
            return historical
        return {
            **historical,
            "message": historical.get("message") or snapshot.get("message") or empty["message"],
            "snapshot_message": snapshot.get("message"),
        }

    async def _hybrid_market_snapshot(
        self,
        symbol: str,
        contract: Any,
        timeout: float,
    ) -> dict[str, Any]:
        historical_timeout = max(float(timeout or 0), self.historical_request_timeout_seconds)
        signal = await self._historical_market_snapshot(
            symbol,
            contract,
            timeout=historical_timeout,
            duration=self.hybrid_signal_duration,
            bar_size=self.hybrid_signal_bar_size,
            cache_profile="hybrid_signal",
        )
        live = await self._ticker_market_snapshot(symbol, contract, timeout=timeout)
        atr_1h = await self._historical_market_snapshot(
            symbol,
            contract,
            timeout=historical_timeout,
            duration=self.hybrid_atr_1h_duration,
            bar_size=self.hybrid_atr_1h_bar_size,
        )
        snapshot = _merge_hybrid_market_snapshot(
            symbol=symbol,
            source=self.connector_name,
            signal=signal,
            live=live,
            atr_1h=atr_1h,
            ttl=self.market_data_ttl,
            policy=self.market_data_policy,
            indicator_policy=self.indicator_policy,
        )
        last_error = self._diagnostics.get("last_tws_request_error") or ""
        snapshot["last_ibkr_error_code"] = _ibkr_error_code(last_error)
        snapshot["last_ibkr_error_message"] = last_error
        return snapshot

    async def _ticker_market_snapshot(
        self,
        symbol: str,
        contract: Any,
        timeout: float,
    ) -> dict[str, Any]:
        best_snapshot: dict[str, Any] | None = None
        data_types = _unique_ints([self.market_data_type, *self.market_data_type_fallbacks])
        for market_data_type in data_types:
            snapshot = await self._ticker_market_snapshot_for_type(
                symbol,
                contract,
                timeout=timeout,
                market_data_type=market_data_type,
            )
            if snapshot.get("available") and snapshot.get("spread") is not None:
                return snapshot
            if snapshot.get("available") and best_snapshot is None:
                best_snapshot = snapshot
            elif best_snapshot is None:
                best_snapshot = snapshot
        return best_snapshot or {
            "available": False,
            "source": self.connector_name,
            "market_data_source": "live",
            "symbol": symbol,
            "message": "Market data is not available.",
            "live_quote_source": "reqMktData",
            "market_data_type_requested": self.market_data_type,
        }

    async def _ticker_market_snapshot_for_type(
        self,
        symbol: str,
        contract: Any,
        timeout: float,
        market_data_type: int,
    ) -> dict[str, Any]:
        empty = {
            "available": False,
            "source": self.connector_name,
            "market_data_source": "live",
            "symbol": symbol,
            "message": "Market data is not available.",
            "live_quote_source": "reqMktData",
            "market_data_type_requested": market_data_type,
        }
        if self._ib is None:
            return {**empty, "message": "TWS/Gateway is disconnected."}
        if self._live_quotes is None:
            self._live_quotes = LiveQuoteRegistry(self._ib)

        last_failure: dict[str, Any] | None = None
        for attempt in (1, 2):
            started: dict[str, Any] | None = None
            entry: dict[str, Any] | None = None
            try:
                with suppress(Exception):
                    self._ib.client.reqMarketDataType(market_data_type)
                entry, created = self._live_quotes.subscribe(symbol, contract, market_data_type)
                if not created:
                    existing = self._live_quotes.latest(symbol, market_data_type) or {}
                    existing_state = _live_quote_state(existing)
                    if existing_state != "READY" and attempt == 1:
                        reset_started = self._record_tws_request_sent(
                            "reqMktData",
                            (
                                f"symbol={symbol} snapshot=false "
                                f"marketDataType={market_data_type} "
                                f"reset stale reqId={entry.get('req_id')}"
                            ),
                        )
                        reset_error = f"{existing_state}: resetting stale live quote subscription"
                        reset_extra = _live_quote_extra(
                            symbol,
                            contract,
                            market_data_type,
                            existing,
                            entry,
                            reset_reason="stale_empty_subscription",
                            retry_attempt=attempt,
                        )
                        self._record_tws_request_result(
                            reset_started,
                            "ERROR",
                            reset_error,
                            extra=reset_extra,
                        )
                        self._live_quotes.unsubscribe(symbol, market_data_type)
                        continue
                if created:
                    started = self._record_tws_request_sent(
                        "reqMktData",
                        (
                            f"symbol={symbol} snapshot=false "
                            f"marketDataType={market_data_type} reqId={entry.get('req_id')}"
                        ),
                    )
                wait_seconds = max(0.1, min(timeout, self.live_quote_wait_seconds))
                await asyncio.wait_for(
                    self._wait_for_live_ticker(entry, wait_seconds),
                    timeout=timeout,
                )
            except Exception as exc:
                latest = self._live_quotes.latest(symbol, market_data_type) or {}
                quote_state = _live_quote_state(latest)
                error = _error_text(exc)
                if quote_state != "READY" and quote_state not in error:
                    error = f"{quote_state}: {error}"
                if started is None:
                    started = self._record_tws_request_sent(
                        "reqMktData",
                        f"symbol={symbol} snapshot=false marketDataType={market_data_type}",
                    )
                extra = _live_quote_extra(
                    symbol,
                    contract,
                    market_data_type,
                    latest,
                    entry,
                    retry_attempt=attempt,
                )
                self._record_tws_request_result(started, "ERROR", error, extra=extra)
                self._diagnostics.update(
                    {
                        "last_live_quote_error": error,
                        "last_live_quote_symbol": symbol,
                        "last_live_quote_extra": extra,
                    }
                )
                last_failure = {
                    **empty,
                    "message": error,
                    "quote_state": quote_state,
                    "missing_fields": _missing_live_quote_fields(latest),
                    **{key: value for key, value in latest.items() if key != "subscription"},
                }
                break
            else:
                break

        if last_failure is not None:
            return last_failure

        latest = self._live_quotes.latest(symbol, market_data_type) or {}
        fields = {
            "bid": latest.get("bid"),
            "ask": latest.get("ask"),
            "last": latest.get("last"),
            "market_data_type_actual": latest.get("market_data_type_actual"),
            "live_market_data_status": _live_market_data_status(
                latest.get("market_data_type_actual")
            ),
            "open": latest.get("open"),
            "high": latest.get("high"),
            "low": latest.get("low"),
            "close": latest.get("close"),
            "volume": latest.get("volume"),
            "spread": latest.get("spread"),
            "spread_bps": latest.get("spread_bps"),
        }
        price = latest.get("price")
        quote_timestamp = latest.get("timestamp") or _utc_now_iso()
        extra = _live_quote_extra(
            symbol,
            contract,
            market_data_type,
            latest,
            entry,
            timestamp=quote_timestamp,
            price=price,
            **fields,
        )
        quote_state = str(extra.get("quote_state") or _live_quote_state(latest))
        if quote_state != "READY":
            error = f"{quote_state}: TWS returned an incomplete live quote."
            if started is None:
                started = self._record_tws_request_sent(
                    "reqMktData",
                    f"symbol={symbol} snapshot=false marketDataType={market_data_type}",
                )
            self._record_tws_request_result(started, "ERROR", error, extra=extra)
            self._diagnostics.update(
                {
                    "last_live_quote_error": error,
                    "last_live_quote_symbol": symbol,
                    "last_live_quote_extra": extra,
                }
            )
            return {
                **empty,
                "message": error,
                "quote_state": quote_state,
                "missing_fields": _missing_live_quote_fields(latest),
                **fields,
            }
        if started is not None:
            self._record_tws_request_result(started, "OK", extra=extra)
        self._diagnostics.update(
            {
                "last_live_quote_error": "",
                "last_live_quote_symbol": symbol,
                "last_live_quote_extra": extra,
            }
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
            "market_data_source": "live",
            "symbol": symbol,
            "price": price,
            **fields,
            "quote_state": quote_state,
            "missing_fields": _missing_live_quote_fields(latest),
            "live_quote_source": "reqMktData",
            "market_data_type_requested": market_data_type,
            "timestamp": quote_timestamp,
            "quote_age_seconds": latest.get("quote_age_seconds"),
            "subscription": latest.get("subscription", {}),
            "message": "",
        }

    async def _historical_market_snapshot(
        self,
        symbol: str,
        contract: Any,
        timeout: float,
        duration: str | None = None,
        bar_size: str | None = None,
        cache_profile: str | None = None,
    ) -> dict[str, Any]:
        duration = duration or self.historical_duration
        bar_size = bar_size or self.historical_bar_size
        cache_key = self._historical_cache_key(symbol, duration, bar_size)
        cached = self._historical_cache.get(cache_key)
        if cached and not _is_older_than(
            cached.get("updated_at"),
            self._historical_cache_ttl_seconds(bar_size, cache_profile=cache_profile),
        ):
            return self._cached_historical_payload(cache_key, "HIT")
        if self._ib is None:
            return {
                "available": False,
                "source": self.connector_name,
                "symbol": symbol,
                "message": self.last_error or "TWS/Gateway is disconnected.",
            }

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
            error_text = _error_text(exc)
            self._record_tws_request_result(
                started,
                "ERROR",
                error_text,
                extra={"symbol": symbol, "market_data_source": "historical"},
            )
            if cached:
                fallback = self._cached_historical_payload(cache_key, "STALE_FALLBACK")
                return {
                    **fallback,
                    "historical_1h_error": (
                        error_text
                        if _is_one_hour_bar_size(bar_size)
                        else fallback.get("historical_1h_error")
                    ),
                    "message": f"{error_text}; using cached historical data.",
                }
            payload = {
                "available": False,
                "source": self.connector_name,
                "market_data_source": "historical",
                "symbol": symbol,
                "message": error_text,
            }
            if _is_one_hour_bar_size(bar_size):
                payload.update(
                    _atr_1h_diagnostics_payload(
                        atr_1h=None,
                        bar_count=0,
                        bar_size=bar_size,
                        duration=duration,
                        use_rth=self.historical_use_rth,
                        error=error_text,
                    )
                )
            return payload
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
                "timestamp": _utc_now_iso(),
            }
        )
        if _is_one_hour_bar_size(bar_size):
            snapshot.update(
                _atr_1h_diagnostics_payload(
                    atr_1h=snapshot.get("atr_1h"),
                    bar_count=snapshot.get("bar_count"),
                    bar_size=bar_size,
                    duration=duration,
                    use_rth=self.historical_use_rth,
                    error=snapshot.get("historical_1h_error"),
                    last_successful=_last_successful_atr_1h(cached),
                    successful_at=snapshot.get("timestamp"),
                )
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
                "atr_15m": snapshot.get("atr_15m"),
                "atr_1h": snapshot.get("atr_1h"),
                "historical_bar_size": snapshot.get("historical_bar_size"),
            },
        )
        if (
            _is_one_hour_bar_size(bar_size)
            and snapshot.get("available")
            and snapshot.get("atr_1h") in (None, "")
            and _last_successful_atr_1h(cached).get("value") is not None
        ):
            fallback = self._cached_historical_payload(cache_key, "STALE_FALLBACK")
            return {
                **fallback,
                "historical_1h_error": snapshot.get("historical_1h_error"),
                "message": (
                    snapshot.get("historical_1h_error")
                    or "ATR 1h refresh incomplete; using cached historical data."
                ),
            }
        if snapshot.get("available"):
            self._historical_cache[cache_key] = {
                "updated_at": snapshot["timestamp"],
                "snapshot": dict(snapshot),
            }
        elif cached:
            fallback = self._cached_historical_payload(cache_key, "STALE_FALLBACK")
            return {
                **fallback,
                "message": snapshot.get("message") or "Using cached historical data.",
            }
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
            timeout=max(float(timeout or 0), self.historical_request_timeout_seconds),
            duration=duration,
            bar_size=bar_size,
        )

    @staticmethod
    def _historical_cache_key(
        symbol: str,
        duration: str,
        bar_size: str,
    ) -> tuple[str, str, str]:
        return (symbol.upper(), str(duration), str(bar_size))

    def _historical_cache_ttl_seconds(
        self,
        bar_size: str,
        *,
        cache_profile: str | None = None,
    ) -> float:
        if cache_profile == "hybrid_signal":
            return self.market_data_ttl["hybrid_signal_seconds"]
        normalized = str(bar_size or "").strip().lower()
        if "15" in normalized and "min" in normalized:
            return self.market_data_ttl["atr_15m_seconds"]
        if (
            "1 hour" in normalized
            or "1h" in normalized
            or ("60" in normalized and "min" in normalized)
        ):
            return self.market_data_ttl["atr_1h_seconds"]
        return self.market_data_ttl["historical_seconds"]

    def _cached_historical_payload(
        self,
        cache_key: tuple[str, str, str],
        cache_status: str,
    ) -> dict[str, Any]:
        cached = self._historical_cache[cache_key]
        updated_at = cached.get("updated_at")
        payload = dict(cached.get("snapshot") or {})
        duration = cache_key[1]
        bar_size = cache_key[2]
        payload.update(
            {
                "cached": True,
                "cache_status": cache_status,
                "cache_updated_at": updated_at,
                "cache_age_seconds": _age_seconds(updated_at),
            }
        )
        if _is_one_hour_bar_size(bar_size):
            payload.update(
                _atr_1h_diagnostics_payload(
                    atr_1h=payload.get("atr_1h"),
                    bar_count=payload.get("bar_count"),
                    bar_size=bar_size,
                    duration=duration,
                    use_rth=payload.get("historical_use_rth", self.historical_use_rth),
                    error=payload.get("historical_1h_error"),
                    last_successful=_last_successful_atr_1h(cached),
                    stale=cache_status != "HIT"
                    or _is_older_than(updated_at, self._historical_cache_ttl_seconds(bar_size)),
                )
            )
        return payload

    def _is_connected(self) -> bool:
        return bool(self._ib is not None and self._ib.isConnected())

    def _mark_disconnected(self, message: str) -> None:
        self._live_quotes = None
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
                extra_text = _format_tws_extra_for_log(entry.get("extra"))
                detail = _append_log_detail(entry["detail"], extra_text)
                logger.error(
                    "%s ERROR: %s %s %.1fms %s",
                    log_prefix,
                    entry["request"],
                    detail,
                    latency_ms,
                    error,
                )
        self._diagnostics["tws_audit_pending"] = len(self._audit_entries)

    def _stock_contract(self, symbol: str):
        from ib_async import Stock

        normalized_symbol = str(symbol or "").strip().upper()
        primary_exchange = self.primary_exchange_by_symbol.get(
            normalized_symbol,
            self.primary_exchange,
        )
        if primary_exchange:
            return Stock(
                normalized_symbol,
                self.stock_exchange,
                "USD",
                primaryExchange=primary_exchange,
            )
        return Stock(normalized_symbol, self.stock_exchange, "USD")

    async def _qualify_contract(self, contract) -> None:
        if self._ib is None:
            raise BrokerDisconnectedError(self.last_error or "TWS/Gateway is disconnected")
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

    def _ensure_account_pnl_subscription(self):
        if self._ib is None or not self._is_connected():
            return None
        account = self._account_pnl_account
        if not account:
            accounts = [item for item in self._ib.managedAccounts() if item]
            account = accounts[0] if accounts else None
            self._account_pnl_account = account
        if not account:
            return None
        pnl_objects = self._ib.pnl(account)
        if pnl_objects:
            return pnl_objects[0]
        started = self._record_tws_request_sent("reqPnL", f"account={account}")
        try:
            pnl = self._ib.reqPnL(account)
        except Exception as exc:
            self._record_tws_request_result(started, "ERROR", _error_text(exc))
            return None
        self._record_tws_request_result(started, "OK")
        return pnl

    def _unsubscribe_account_pnl(self) -> None:
        if self._ib is None or not self._account_pnl_account:
            return
        with suppress(Exception):
            self._ib.cancelPnL(self._account_pnl_account)

    def _build_order(self, request: BrokerOrderRequest) -> Order:
        from ib_async import LimitOrder, MarketOrder, StopLimitOrder, StopOrder

        order: Order
        if request.order_type == "MKT":
            order = MarketOrder(request.side, request.quantity)
        elif request.order_type == "LMT":
            if request.limit_price is None:
                raise ValueError("LMT order requires a limit_price")
            order = LimitOrder(request.side, request.quantity, request.limit_price)
        elif request.order_type == "STP":
            if request.stop_price is None:
                raise ValueError("STP order requires a stop_price")
            order = StopOrder(request.side, request.quantity, request.stop_price)
        elif request.order_type == "STP_LMT":
            if request.limit_price is None or request.trigger_price is None:
                raise ValueError("STP_LMT order requires a limit_price and trigger_price")
            order = StopLimitOrder(
                request.side,
                request.quantity,
                request.limit_price,
                request.trigger_price,
            )
        else:
            raise ValueError(f"Unsupported TWS order type: {request.order_type}")
        if request.parent_id:
            order.parentId = int(request.parent_id)
        if request.oca_group:
            order.ocaGroup = str(request.oca_group)
        order.transmit = bool(request.transmit)
        return order

    async def _sleep(self, seconds: float) -> None:
        await asyncio.sleep(seconds)

    async def _wait_for_live_ticker(self, entry: dict[str, Any], seconds: float) -> None:
        deadline = time.monotonic() + max(seconds, 0)
        while time.monotonic() < deadline:
            ticker = entry.get("ticker")
            fields = _ticker_fields(ticker)
            price = _ticker_price(ticker)
            if (
                price is not None
                and calculate_spread(fields.get("bid"), fields.get("ask")) is not None
            ):
                return
            await asyncio.sleep(0.05)
        ticker = entry.get("ticker")
        fields = _ticker_fields(ticker)
        raise LiveQuoteTimeoutError(
            "LIVE_QUOTE_TIMEOUT: "
            f"symbol={entry.get('symbol')} "
            f"req_id={entry.get('req_id')} "
            f"bid={fields.get('bid')} "
            f"ask={fields.get('ask')} "
            f"last={fields.get('last')} "
            f"market_data_type_actual={fields.get('market_data_type_actual')}"
        )

    def _cancel_market_data_subscription(self, req_id: int | None, ticker: Any) -> None:
        if self._ib is None:
            return
        cancel_req_id = req_id
        if ticker is not None:
            with suppress(Exception):
                cancel_req_id = self._ib.wrapper.endTicker(ticker, "mktData") or cancel_req_id
        if cancel_req_id:
            with suppress(Exception):
                self._ib.client.cancelMktData(cancel_req_id)

    def _unsubscribe_live_quotes(self) -> None:
        if self._live_quotes is None:
            return
        self._live_quotes.unsubscribe_all()


def create_broker_connector(
    connector_name: str,
    config: dict[str, Any] | None = None,
) -> BrokerConnector:
    connector = connector_name.strip().lower()
    if connector == "simulated":
        simulated_account_mode = str((config or {}).get("account_mode", "paper"))
        return SimulatedBrokerConnector(account_mode=simulated_account_mode)
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


# The IB API reports unset price fields as UNSET_DOUBLE (max float), not None.
_IB_UNSET_PRICE_THRESHOLD = 1.7e308


def _ib_price_or_none(value: Any) -> float | None:
    number = _float_or_none(value)
    if number is None:
        return None
    if not math.isfinite(number) or abs(number) >= _IB_UNSET_PRICE_THRESHOLD:
        return None
    return number


def _number_or_none(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _int_list(value: Any) -> list[int]:
    if value in (None, ""):
        return []
    raw_values = value if isinstance(value, (list, tuple, set)) else str(value).split(",")
    result: list[int] = []
    for item in raw_values:
        try:
            result.append(int(item))
        except (TypeError, ValueError):
            continue
    return result


def _unique_ints(values: list[int]) -> list[int]:
    result: list[int] = []
    seen: set[int] = set()
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result


def _market_data_ttl_config(config: dict[str, Any]) -> dict[str, float]:
    raw = config.get("market_data_ttl", {})
    raw = raw if isinstance(raw, dict) else {}
    return {
        "live_quote_seconds": float(raw.get("live_quote_seconds", 20)),
        "hybrid_signal_seconds": float(raw.get("hybrid_signal_seconds", 20)),
        "atr_15m_seconds": float(raw.get("atr_15m_seconds", 1200)),
        "atr_1h_seconds": float(raw.get("atr_1h_seconds", 5400)),
        "historical_seconds": float(raw.get("historical_seconds", 300)),
    }


def _market_data_policy_config(config: dict[str, Any], *, mode: str) -> dict[str, Any]:
    raw = config.get("market_data_policy") or config.get("market_data") or {}
    raw = raw if isinstance(raw, dict) else {}
    normalized_mode = _readiness_mode(mode)
    return {
        "mode": normalized_mode,
        "require_live_market_data_for_live_orders": bool(
            raw.get("require_live_market_data_for_live_orders", True)
        ),
        "require_live_market_data_for_paper_orders": bool(
            raw.get("require_live_market_data_for_paper_orders", False)
        ),
        "allow_delayed_market_data_in_paper": bool(
            raw.get("allow_delayed_market_data_in_paper", True)
        ),
        "allow_delayed_market_data_in_simulation": bool(
            raw.get("allow_delayed_market_data_in_simulation", True)
        ),
    }


def _indicator_policy_config(config: dict[str, Any]) -> dict[str, Any]:
    raw = config.get("indicator_policy") or config.get("indicators") or {}
    raw = raw if isinstance(raw, dict) else {}
    atr_1h = raw.get("atr_1h", {}) if isinstance(raw.get("atr_1h"), dict) else {}
    return {
        "atr_1h": {
            "required_for_paper": bool(atr_1h.get("required_for_paper", False)),
            "required_for_live": bool(atr_1h.get("required_for_live", True)),
            "allow_stale_in_paper": bool(atr_1h.get("allow_stale_in_paper", True)),
            "stale_after_minutes": float(atr_1h.get("stale_after_minutes", 120) or 120),
        }
    }


def _age_seconds(timestamp: Any) -> float | None:
    if not timestamp:
        return None
    try:
        value = str(timestamp).replace("Z", "+00:00")
        parsed = datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return round((datetime.now(UTC) - parsed.astimezone(UTC)).total_seconds(), 3)


def _is_older_than(timestamp: Any, ttl_seconds: float | None) -> bool:
    age = _age_seconds(timestamp)
    if age is None or ttl_seconds is None:
        return False
    return age > ttl_seconds


def _error_text(error: Exception) -> str:
    text = str(error).strip()
    return text or error.__class__.__name__


def _utc_now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _contract_detail(contract: Any) -> str:
    symbol = str(getattr(contract, "symbol", "") or "")
    exchange = str(getattr(contract, "exchange", "") or "")
    primary_exchange = str(getattr(contract, "primaryExchange", "") or "")
    currency = str(getattr(contract, "currency", "") or "")
    sec_type = str(getattr(contract, "secType", "") or "")
    details = [
        f"symbol={symbol}" if symbol else "",
        f"secType={sec_type}" if sec_type else "",
        f"exchange={exchange}" if exchange else "",
        f"primaryExchange={primary_exchange}" if primary_exchange else "",
        f"currency={currency}" if currency else "",
    ]
    return " ".join(item for item in details if item)


def _ticker_fields(ticker: Any) -> dict[str, float | None]:
    if ticker is None:
        return {
            "bid": None,
            "ask": None,
            "last": None,
            "market_data_type_actual": None,
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
        "market_data_type_actual": _number_or_none(getattr(ticker, "marketDataType", None)),
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


def _live_quote_state(quote: dict[str, Any]) -> str:
    price = _number_or_none(quote.get("price"))
    bid = _number_or_none(quote.get("bid"))
    ask = _number_or_none(quote.get("ask"))
    last = _number_or_none(quote.get("last"))
    spread = calculate_spread(bid, ask)
    if price is not None and spread is not None:
        return "READY"
    if all(value is None for value in (price, bid, ask, last)):
        return "EMPTY_QUOTE"
    if price is not None or last is not None or bid is not None or ask is not None:
        return "NO_BID_ASK"
    return "NO_PRICE"


def _missing_live_quote_fields(quote: dict[str, Any]) -> list[str]:
    missing: list[str] = []
    if _number_or_none(quote.get("price")) is None:
        missing.append("price")
    if _number_or_none(quote.get("bid")) is None:
        missing.append("bid")
    if _number_or_none(quote.get("ask")) is None:
        missing.append("ask")
    if calculate_spread(quote.get("bid"), quote.get("ask")) is None:
        missing.append("spread")
    return missing


def _live_quote_extra(
    symbol: str,
    contract: Any,
    market_data_type: int,
    latest: dict[str, Any],
    entry: dict[str, Any] | None = None,
    **extra_fields: Any,
) -> dict[str, Any]:
    quote_state = _live_quote_state(latest)
    actual_type = latest.get("market_data_type_actual")
    return {
        "symbol": symbol,
        "req_id": latest.get("req_id") or (entry or {}).get("req_id"),
        "contract": latest.get("contract") or _contract_detail(contract),
        "market_data_source": "live",
        "live_quote_source": "reqMktData",
        "market_data_type_requested": market_data_type,
        "market_data_type_actual": actual_type,
        "live_market_data_status": _live_market_data_status(actual_type),
        "timestamp": latest.get("timestamp"),
        "quote_age_seconds": latest.get("quote_age_seconds"),
        "quote_state": quote_state,
        "missing_fields": _missing_live_quote_fields(latest),
        "price": latest.get("price"),
        "bid": latest.get("bid"),
        "ask": latest.get("ask"),
        "last": latest.get("last"),
        "open": latest.get("open"),
        "high": latest.get("high"),
        "low": latest.get("low"),
        "close": latest.get("close"),
        "volume": latest.get("volume"),
        "spread": latest.get("spread"),
        "spread_bps": latest.get("spread_bps"),
        **extra_fields,
    }


def calculate_spread(bid: float | None, ask: float | None) -> float | None:
    bid_value = _number_or_none(bid)
    ask_value = _number_or_none(ask)
    if bid_value is None or ask_value is None:
        return None
    if bid_value <= 0 or ask_value <= 0:
        return None
    if ask_value < bid_value:
        return None
    return round(ask_value - bid_value, 4)


def _spread_bps(
    bid: float | None,
    ask: float | None,
    spread: float | None = None,
) -> float | None:
    spread_value = spread if spread is not None else calculate_spread(bid, ask)
    bid_value = _number_or_none(bid)
    ask_value = _number_or_none(ask)
    if spread_value is None or bid_value is None or ask_value is None:
        return None
    mid = (bid_value + ask_value) / 2
    if mid <= 0:
        return None
    return round((spread_value / mid) * 10_000, 4)


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
        "req_id",
        "contract",
        "market_data_source",
        "live_quote_source",
        "market_data_type_requested",
        "market_data_type_actual",
        "live_market_data_status",
        "quote_state",
        "missing_fields",
        "reset_reason",
        "retry_attempt",
        "price",
        "bid",
        "ask",
        "last",
        "spread",
        "spread_bps",
        "quote_age_seconds",
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
        "atr_1h_status",
        "atr_1h_bar_size",
        "atr_1h_duration",
        "atr_1h_use_rth",
        "historical_1h_available",
        "historical_1h_error",
        "last_successful_atr_1h",
        "last_successful_atr_1h_at",
        "hybrid_signal_bar_size",
        "hybrid_atr_1h_bar_size",
        "historical_bar_size",
        "bars_15m_count",
        "bars_1h_count",
        "readiness",
        "bar_date",
        "bar_count",
    )
    parts = [f"{key}={extra[key]}" for key in keys if extra.get(key) not in (None, "")]
    return " ".join(parts)


def _is_one_hour_bar_size(bar_size: Any) -> bool:
    normalized = str(bar_size or "").strip().lower()
    return normalized in {"1 hour", "1h", "60 mins", "60 min", "60 minutes"}


def _atr_1h_diagnostics_payload(
    *,
    atr_1h: Any,
    bar_count: Any,
    bar_size: Any,
    duration: Any = None,
    use_rth: Any = None,
    error: Any = "",
    last_successful: dict[str, Any] | None = None,
    stale: bool = False,
    successful_at: Any = None,
) -> dict[str, Any]:
    atr_value = _number_or_none(atr_1h)
    bars = _int_or_none(bar_count) or 0
    last_successful = last_successful or {}
    status = (
        "STALE" if stale and atr_value is not None else "OK" if atr_value is not None else "MISSING"
    )
    last_successful_at = (
        (successful_at or last_successful.get("timestamp"))
        if atr_value is not None
        else last_successful.get("timestamp")
    )
    return {
        "atr_1h": atr_value,
        "atr_1h_status": status,
        "atr_1h_bar_size": str(bar_size or "1 hour"),
        "atr_1h_duration": str(duration or ""),
        "atr_1h_use_rth": bool(use_rth) if use_rth is not None else None,
        "bars_1h_count": bars,
        "bars_required_for_atr": BARS_REQUIRED_FOR_ATR,
        "historical_1h_available": bars > 0,
        "historical_1h_error": str(error or ""),
        "last_successful_atr_1h": (
            atr_value if atr_value is not None else last_successful.get("value")
        ),
        "last_successful_atr_1h_at": last_successful_at,
        "atr_1h_age_seconds": _age_seconds(last_successful_at),
    }


def _last_successful_atr_1h(cached: dict[str, Any] | None) -> dict[str, Any]:
    if not cached:
        return {}
    raw_snapshot = cached.get("snapshot")
    snapshot: dict[str, Any] = raw_snapshot if isinstance(raw_snapshot, dict) else {}
    value = _number_or_none(snapshot.get("atr_1h"))
    if value is None:
        return {}
    return {
        "value": value,
        "timestamp": snapshot.get("timestamp") or cached.get("updated_at"),
    }


def _ibkr_error_code(message: Any) -> int | None:
    text = str(message or "")
    patterns = (
        r"\bcode[=: ]+(\d{3,5})\b",
        r"\berror[=: ]+(\d{3,5})\b",
        r"\bIBKR[^\d]{0,10}(\d{3,5})\b",
    )
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match:
            return int(match.group(1))
    return None


def _historical_quote_from_bars(
    symbol: str,
    source: str,
    bars: Any,
    bar_size: str = "",
) -> dict[str, Any]:
    one_hour_bars = _is_one_hour_bar_size(bar_size)
    rows = [
        row
        for row in (_bar_to_ohlcv(bar) for bar in list(bars or []))
        if row.get("close") is not None
    ]
    if not rows:
        payload = {
            "available": False,
            "source": source,
            "market_data_source": "historical",
            "symbol": symbol,
            "bar_count": 0,
            "message": f"No historical OHLCV data returned for {symbol}.",
        }
        if one_hour_bars:
            payload.update(
                _atr_1h_diagnostics_payload(
                    atr_1h=None,
                    bar_count=0,
                    bar_size=bar_size,
                    error=payload["message"],
                )
            )
        return payload
    latest = rows[-1]
    previous = rows[-2] if len(rows) > 1 else {}
    price = _number_or_none(latest.get("close"))
    if price is None or price <= 0:
        payload = {
            "available": False,
            "source": source,
            "market_data_source": "historical",
            "symbol": symbol,
            "bar_count": len(rows),
            "bar_date": latest.get("date"),
            "message": f"Historical OHLCV for {symbol} did not include a usable close.",
        }
        if one_hour_bars:
            payload.update(
                _atr_1h_diagnostics_payload(
                    atr_1h=None,
                    bar_count=len(rows),
                    bar_size=bar_size,
                    error=payload["message"],
                )
            )
        return payload
    atr = average_true_range(rows, period=ATR_PERIOD)
    bar_size_normalized = str(bar_size or "").strip().lower()
    atr_15m = atr if "15" in bar_size_normalized and "min" in bar_size_normalized else None
    atr_1h = simple_average_true_range(rows, period=ATR_PERIOD) if one_hour_bars else None
    volume_stats = _historical_volume_stats(rows, sample_days=20)
    volume_ratio = volume_stats["ratio"]
    average_volume = volume_stats["average_volume"]
    payload = {
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
        "bar_volume_15m": latest.get("volume"),
        "avg_volume_15m": average_volume,
        "previous_high": previous.get("high"),
        "volume_ratio": volume_ratio,
        "volume_ratio_15m": volume_ratio,
        "volume_ratio_closed_bar": volume_ratio,
        "volume_timeframe": (
            "15m" if "15" in bar_size_normalized and "min" in bar_size_normalized else bar_size
        ),
        "volume_comparison_mode": volume_stats["comparison_mode"],
        "volume_sample_days": volume_stats["sample_days"],
        "volume_sample_count": volume_stats["sample_count"],
        "atr_15m": atr_15m,
        "atr_1h": atr_1h,
        "atr_period": ATR_PERIOD,
        "atr_ready": atr_1h is not None if one_hour_bars else atr is not None,
        "bars_required_for_atr": BARS_REQUIRED_FOR_ATR,
        "bar_date": latest.get("date"),
        "bar_count": len(rows),
        "historical_bars": rows[-180:],
        "message": "",
    }
    if one_hour_bars:
        payload.update(
            _atr_1h_diagnostics_payload(
                atr_1h=atr_1h,
                bar_count=len(rows),
                bar_size=bar_size,
                error=(
                    ""
                    if atr_1h is not None
                    else f"Need at least {BARS_REQUIRED_FOR_ATR} closed 1h bars to compute ATR 1h({ATR_PERIOD})."
                ),
            )
        )
    return payload


def _merge_hybrid_market_snapshot(
    symbol: str,
    source: str,
    signal: dict[str, Any],
    live: dict[str, Any],
    atr_1h: dict[str, Any],
    ttl: dict[str, float] | None = None,
    policy: dict[str, Any] | None = None,
    indicator_policy: dict[str, Any] | None = None,
) -> dict[str, Any]:
    ttl = ttl or _market_data_ttl_config({})
    policy = policy or _default_market_data_policy(source)
    indicator_policy = indicator_policy or _default_indicator_policy()
    signal_available = bool(signal.get("available"))
    live_available = bool(live.get("available"))
    atr_1h_available = bool(atr_1h.get("available"))
    base = dict(signal if signal_available else live if live_available else atr_1h)
    if not base:
        base = {
            "available": False,
            "source": source,
            "symbol": symbol.upper(),
            "message": "Market data is not available.",
        }

    for key in ("bid", "ask", "last"):
        if live.get(key) not in (None, ""):
            base[key] = live[key]
    for key in (
        "live_quote_source",
        "market_data_type_requested",
        "market_data_type_actual",
        "quote_age_seconds",
        "quote_state",
        "missing_fields",
    ):
        if live.get(key) not in (None, ""):
            base[key] = live[key]
    if live.get("price") not in (None, ""):
        base["price"] = live["price"]
    if signal.get("atr_15m") not in (None, ""):
        base["atr_15m"] = signal["atr_15m"]
    if atr_1h.get("atr_1h") not in (None, ""):
        base["atr_1h"] = atr_1h["atr_1h"]
    for key in (
        "atr_1h_status",
        "atr_1h_bar_size",
        "atr_1h_duration",
        "atr_1h_use_rth",
        "bars_required_for_atr",
        "historical_1h_available",
        "historical_1h_error",
        "last_successful_atr_1h",
        "last_successful_atr_1h_at",
        "atr_1h_age_seconds",
    ):
        if atr_1h.get(key) not in (None, ""):
            base[key] = atr_1h[key]
    computed_live_status = _live_market_data_status(live.get("market_data_type_actual"))
    base["live_market_data_status"] = (
        computed_live_status
        if live.get("market_data_type_actual") not in (None, "")
        else live.get("live_market_data_status") or computed_live_status
    )
    if signal.get("historical_bars"):
        base["historical_bars"] = signal["historical_bars"]
    spread = calculate_spread(base.get("bid"), base.get("ask"))
    if spread is not None:
        base["spread"] = spread
        base["spread_bps"] = _spread_bps(base.get("bid"), base.get("ask"), spread)

    session_context = current_us_equity_session_context(
        live.get("timestamp") or signal.get("timestamp") or atr_1h.get("timestamp")
    )
    if base.get("session") in (None, ""):
        base["session"] = session_context.session
    if base.get("current_time") in (None, ""):
        base["current_time"] = session_context.current_time
    if base.get("market_open_time") in (None, ""):
        base["market_open_time"] = session_context.market_open_time

    base.update(
        {
            "available": bool(
                base.get("price") not in (None, "")
                and (signal_available or live_available or atr_1h_available)
            ),
            "source": source,
            "market_data_source": "hybrid",
            "symbol": symbol.upper(),
            "hybrid_sources": {
                "signal": _hybrid_source_status(signal),
                "live": _hybrid_source_status(live),
                "atr_1h": _hybrid_source_status(atr_1h),
            },
            "hybrid_signal_bar_size": signal.get("historical_bar_size"),
            "hybrid_atr_1h_bar_size": atr_1h.get("historical_bar_size"),
            "bars_15m_count": signal.get("bar_count"),
            "bars_1h_count": atr_1h.get("bar_count"),
            "last_ibkr_error_code": None,
            "last_ibkr_error_message": "",
        }
    )
    base["market_data_readiness"] = _hybrid_market_data_readiness(
        base,
        signal=signal,
        live=live,
        atr_1h=atr_1h,
        ttl=ttl,
        policy=policy,
        indicator_policy=indicator_policy,
    )
    base["readiness"] = base["market_data_readiness"]["status"]
    messages = [
        str(item.get("message") or "") for item in (signal, live, atr_1h) if item.get("message")
    ]
    base["message"] = " | ".join(messages)
    return base


def _trade_status_detail(trade: Any) -> str:
    details: list[str] = []
    advanced_error = str(getattr(trade, "advancedError", "") or "").strip()
    if advanced_error:
        details.append(advanced_error)
    for entry in getattr(trade, "log", []) or []:
        message = str(getattr(entry, "message", "") or "").strip()
        if message and message not in details:
            details.append(message)
    return " | ".join(details[:3])


def _hybrid_source_status(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "available": bool(payload.get("available")),
        "market_data_source": payload.get("market_data_source"),
        "live_quote_source": payload.get("live_quote_source"),
        "market_data_type_requested": payload.get("market_data_type_requested"),
        "market_data_type_actual": payload.get("market_data_type_actual"),
        "live_market_data_status": payload.get("live_market_data_status"),
        "quote_state": payload.get("quote_state"),
        "missing_fields": payload.get("missing_fields") or [],
        "message": payload.get("message") or "",
        "bar_size": payload.get("historical_bar_size"),
        "bar_count": payload.get("bar_count"),
        "bar_date": payload.get("bar_date"),
        "atr_1h_status": payload.get("atr_1h_status"),
        "historical_1h_error": payload.get("historical_1h_error"),
        "last_successful_atr_1h": payload.get("last_successful_atr_1h"),
        "last_successful_atr_1h_at": payload.get("last_successful_atr_1h_at"),
        "atr_1h_age_seconds": payload.get("atr_1h_age_seconds"),
        "updated_at": payload.get("timestamp") or payload.get("cache_updated_at"),
        "cache_status": payload.get("cache_status"),
        "atr_ready": payload.get("atr_ready"),
    }


def _hybrid_market_data_readiness(
    snapshot: dict[str, Any],
    *,
    signal: dict[str, Any],
    live: dict[str, Any],
    atr_1h: dict[str, Any],
    ttl: dict[str, float],
    policy: dict[str, Any] | None = None,
    indicator_policy: dict[str, Any] | None = None,
) -> dict[str, Any]:
    policy = policy or _default_market_data_policy(snapshot.get("source"))
    indicator_policy = indicator_policy or _default_indicator_policy()
    mode = _readiness_mode(policy.get("mode") or snapshot.get("source"))
    live_updated_at = live.get("timestamp")
    signal_updated_at = signal.get("timestamp") or signal.get("cache_updated_at")
    atr_1h_updated_at = atr_1h.get("timestamp") or atr_1h.get("cache_updated_at")
    live_status = _live_market_data_status(live.get("market_data_type_actual"))
    atr_1h_status = str(atr_1h.get("atr_1h_status") or "").upper()
    atr_1h_policy = (
        indicator_policy.get("atr_1h", {})
        if isinstance(indicator_policy.get("atr_1h"), dict)
        else {}
    )
    atr_1h_stale_allowed = (
        mode == "paper"
        and bool(atr_1h_policy.get("allow_stale_in_paper", True))
        and snapshot.get("atr_1h") not in (None, "")
        and (
            atr_1h_status == "STALE"
            or _is_older_than(
                atr_1h.get("last_successful_atr_1h_at") or atr_1h_updated_at,
                float(atr_1h_policy.get("stale_after_minutes", 120) or 120) * 60,
            )
        )
    )
    warnings: list[str] = []
    blocking_reasons: list[str] = []
    fields_list = [
        _readiness_field(
            "last",
            snapshot.get("last") or snapshot.get("price"),
            source=snapshot.get("market_data_source"),
            updated_at=signal_updated_at or live_updated_at,
            blocking=True,
        ),
        _readiness_field(
            "bid",
            snapshot.get("bid"),
            source=live.get("live_quote_source") or "reqMktData",
            updated_at=live_updated_at,
            blocking=True,
            required_for="order_submission",
            stale_after_seconds=ttl["live_quote_seconds"],
        ),
        _readiness_field(
            "ask",
            snapshot.get("ask"),
            source=live.get("live_quote_source") or "reqMktData",
            updated_at=live_updated_at,
            blocking=True,
            required_for="order_submission",
            stale_after_seconds=ttl["live_quote_seconds"],
        ),
        _readiness_field(
            "spread",
            snapshot.get("spread"),
            source="local_calculation",
            updated_at=live_updated_at,
            blocking=True,
            required_for="order_submission",
            stale_after_seconds=ttl["live_quote_seconds"],
        ),
    ]
    fields = {item["name"]: item for item in fields_list}
    live_decision = _live_market_data_policy_decision(
        live_status=live_status,
        snapshot=snapshot,
        fields=fields,
        policy=policy,
    )
    if live_decision.get("warning"):
        warnings.append(str(live_decision["warning"]))
    if live_decision.get("blocking_reason"):
        blocking_reasons.append(str(live_decision["blocking_reason"]))
    fields_list.extend(
        [
            _readiness_field(
                "live_market_data",
                1 if live_status == "LIVE" else live.get("market_data_type_actual"),
                source="reqMktData",
                updated_at=live_updated_at,
                blocking=bool(live_decision.get("blocking")),
                required_for="order_submission",
                stale_after_seconds=ttl["live_quote_seconds"],
                status_override=None if live_status == "LIVE" else str(live_decision["status"]),
                detail=_market_data_type_label(live.get("market_data_type_actual")),
            ),
            _readiness_field(
                "bars_15m",
                signal.get("bar_count"),
                source="reqHistoricalData",
                updated_at=signal_updated_at,
                blocking=True,
                minimum=15,
                stale_after_seconds=ttl["atr_15m_seconds"],
            ),
            _readiness_field(
                "atr_15m",
                snapshot.get("atr_15m"),
                source="local_ATR_14",
                updated_at=signal_updated_at,
                blocking=True,
                stale_after_seconds=ttl["atr_15m_seconds"],
            ),
            _readiness_field(
                "bars_1h",
                atr_1h.get("bar_count"),
                source="reqHistoricalData",
                updated_at=atr_1h_updated_at,
                blocking=not atr_1h_stale_allowed,
                minimum=15,
                stale_after_seconds=ttl["atr_1h_seconds"],
                status_override="WARNING" if atr_1h_stale_allowed else None,
            ),
            _readiness_field(
                "atr_1h",
                snapshot.get("atr_1h"),
                source="local_ATR_14",
                updated_at=atr_1h_updated_at,
                blocking=not atr_1h_stale_allowed,
                stale_after_seconds=ttl["atr_1h_seconds"],
                status_override=(
                    "WARNING"
                    if atr_1h_stale_allowed
                    else (
                        atr_1h.get("atr_1h_status")
                        if atr_1h.get("atr_1h_status") in {"ERROR", "STALE"}
                        else None
                    )
                ),
                detail=atr_1h.get("historical_1h_error") or "",
            ),
        ]
    )
    if atr_1h_stale_allowed:
        warnings.append("WARNING_STALE_ATR_1H")
    fields = {item["name"]: item for item in fields_list}
    missing = _readiness_missing_fields(fields)
    blocking_reasons.extend(
        f"BLOCKED_MISSING_{name.upper()}" for name in missing if name != "live_market_data"
    )
    warmup_ready = all(
        not item.get("blocking")
        for item in fields.values()
        if item["name"] in {"last", "bars_15m", "atr_15m", "bars_1h", "atr_1h"}
    )
    order_submission_ready = all(
        not item.get("blocking")
        for item in fields.values()
        if item["name"] in {"bid", "ask", "spread", "live_market_data"}
    )
    status = _market_readiness_status(missing)
    return {
        "status": status,
        "mode": mode,
        "missing": missing,
        "warnings": _dedupe_non_empty(warnings),
        "blocking_reasons": _dedupe_non_empty(blocking_reasons),
        "config_ready": True,
        "warmup_ready": warmup_ready,
        "signal_evaluation_ready": warmup_ready,
        "order_submission_ready": order_submission_ready,
        "non_live_market_data_allowed_for_test": bool(live_decision.get("allowed_for_test")),
        "market_data_type_requested": live.get("market_data_type_requested"),
        "market_data_type_actual": live.get("market_data_type_actual"),
        "live_market_data_status": live_status,
        "atr_1h_status": fields["atr_1h"]["status"],
        "atr_1h_age_seconds": snapshot.get("atr_1h_age_seconds")
        or fields["atr_1h"].get("age_seconds"),
        "quote_age_seconds": live.get("quote_age_seconds"),
        "policy": {
            "market_data": policy,
            "indicators": indicator_policy,
        },
        "fields": fields,
        "field_list": fields_list,
    }


def _readiness_field(
    name: str,
    value: Any,
    *,
    source: str | None,
    updated_at: Any = None,
    blocking: bool = False,
    required_for: str = "analysis",
    minimum: float | None = None,
    stale_after_seconds: float | None = None,
    status_override: str | None = None,
    detail: str = "",
) -> dict[str, Any]:
    number = _number_or_none(value)
    if minimum is not None:
        ok = number is not None and number >= minimum
    else:
        ok = value not in (None, "")
    age = _age_seconds(updated_at)
    stale = bool(
        ok and stale_after_seconds is not None and age is not None and age > stale_after_seconds
    )
    status = status_override or ("STALE" if stale else "OK" if ok else "MISSING")
    return {
        "name": name,
        "status": status,
        "value": value,
        "source": source or "",
        "last_update": updated_at or "",
        "age_seconds": age,
        "ttl_seconds": stale_after_seconds,
        "blocking": bool(blocking and status != "OK"),
        "required_for": required_for,
        "detail": detail,
    }


def _is_realtime_market_data(live: dict[str, Any]) -> bool:
    actual = _number_or_none(live.get("market_data_type_actual"))
    return actual is not None and int(actual) == LIVE_MARKET_DATA_TYPE


def _live_market_data_status(market_data_type_actual: Any) -> str:
    actual = _number_or_none(market_data_type_actual)
    if actual is None:
        return "UNKNOWN"
    return MARKET_DATA_TYPE_LABELS.get(int(actual), f"UNKNOWN_{int(actual)}")


def _market_data_type_label(market_data_type_actual: Any) -> str:
    actual = _number_or_none(market_data_type_actual)
    if actual is None:
        return "UNKNOWN"
    return MARKET_DATA_TYPE_LABELS.get(int(actual), f"UNKNOWN_{int(actual)}")


def _readiness_mode(mode: Any) -> str:
    normalized = str(mode or "paper").strip().lower()
    if normalized in {"simulation", "sim", "simulated"}:
        return "simulation"
    if normalized == "live":
        return "live"
    return "paper"


def _default_market_data_policy(mode: Any) -> dict[str, Any]:
    return _market_data_policy_config({}, mode=_readiness_mode(mode))


def _default_indicator_policy() -> dict[str, Any]:
    return _indicator_policy_config({})


def _live_market_data_policy_decision(
    *,
    live_status: str,
    snapshot: dict[str, Any],
    fields: dict[str, dict[str, Any]],
    policy: dict[str, Any],
) -> dict[str, Any]:
    mode = _readiness_mode(policy.get("mode"))
    if live_status == "LIVE":
        return {
            "status": "OK",
            "blocking": False,
            "warning": "",
            "blocking_reason": "",
            "allowed_for_test": False,
        }
    requires_live = (
        bool(policy.get("require_live_market_data_for_live_orders", True))
        if mode == "live"
        else (
            bool(policy.get("require_live_market_data_for_paper_orders", False))
            if mode == "paper"
            else False
        )
    )
    allows_delayed = (
        bool(policy.get("allow_delayed_market_data_in_simulation", True))
        if mode == "simulation"
        else (
            bool(policy.get("allow_delayed_market_data_in_paper", True))
            if mode == "paper"
            else False
        )
    )
    essentials_ready = _delayed_market_data_essentials_ready(snapshot, fields)
    if not requires_live and allows_delayed and essentials_ready:
        return {
            "status": "WARNING",
            "blocking": False,
            "warning": "WARNING_NOT_LIVE_MARKET_DATA",
            "blocking_reason": "",
            "allowed_for_test": True,
        }
    return {
        "status": "BLOCKED",
        "blocking": True,
        "warning": "",
        "blocking_reason": "BLOCKED_NOT_LIVE_MARKET_DATA",
        "allowed_for_test": False,
    }


def _delayed_market_data_essentials_ready(
    snapshot: dict[str, Any],
    fields: dict[str, dict[str, Any]],
) -> bool:
    for name in ("last", "bid", "ask", "spread"):
        field = fields.get(name)
        if not field or field.get("status") != "OK":
            return False
    for key in ("open", "high", "low", "close"):
        if _number_or_none(snapshot.get(key)) is None:
            return False
    return _number_or_none(snapshot.get("volume")) is not None


def _dedupe_non_empty(items: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        text = str(item or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        result.append(text)
    return result


def _readiness_missing_fields(fields: dict[str, dict[str, Any]]) -> list[str]:
    missing: list[str] = []
    for name, field in fields.items():
        if not field.get("blocking") or field.get("status") == "OK":
            continue
        if name == "bars_1h" and _field_blocks(fields.get("atr_1h")):
            continue
        if name == "bars_15m" and _field_blocks(fields.get("atr_15m")):
            continue
        missing.append(name)
    return missing


def _field_blocks(field: dict[str, Any] | None) -> bool:
    return bool(field and field.get("blocking") and field.get("status") != "OK")


def _market_readiness_status(missing: list[str]) -> str:
    if not missing:
        return "READY"
    live_missing = "live_market_data" in missing
    indicator_missing = any(
        item in {"atr_15m", "atr_1h", "bars_15m", "bars_1h"} for item in missing
    )
    other_missing = [
        item
        for item in missing
        if item not in {"live_market_data", "atr_15m", "atr_1h", "bars_15m", "bars_1h"}
    ]
    if live_missing and not indicator_missing and not other_missing:
        return "PAUSED_NOT_LIVE_MARKET_DATA"
    if indicator_missing and not live_missing and not other_missing:
        return "PAUSED_MISSING_INDICATOR_DATA"
    return "PAUSED_MISSING_MARKET_DATA"


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
    return _historical_volume_stats(rows)["ratio"]


def _historical_volume_stats(
    rows: list[dict[str, Any]],
    sample_days: int = 20,
) -> dict[str, Any]:
    if len(rows) < 2:
        return {
            "ratio": None,
            "average_volume": None,
            "comparison_mode": "VOLUME_DATA_MISSING",
            "sample_days": sample_days,
            "sample_count": 0,
        }
    latest_volume = _number_or_none(rows[-1].get("volume"))
    latest_slot = _bar_time_slot(rows[-1].get("date"))
    same_slot_volumes = [
        volume
        for row in rows[:-1]
        if latest_slot and _bar_time_slot(row.get("date")) == latest_slot
        for volume in [_number_or_none(row.get("volume"))]
        if volume is not None and volume > 0
    ]
    if same_slot_volumes:
        previous_volumes = same_slot_volumes[-sample_days:]
        comparison_mode = "SAME_TIME_OF_DAY"
    else:
        previous_volumes = [
            volume
            for volume in (_number_or_none(row.get("volume")) for row in rows[:-1])
            if volume is not None and volume > 0
        ]
        comparison_mode = "RECENT_BARS"
    if latest_volume is None or latest_volume <= 0 or not previous_volumes:
        return {
            "ratio": None,
            "average_volume": None,
            "comparison_mode": comparison_mode,
            "sample_days": sample_days,
            "sample_count": len(previous_volumes),
        }
    average_volume = sum(previous_volumes) / len(previous_volumes)
    if average_volume <= 0:
        return {
            "ratio": None,
            "average_volume": None,
            "comparison_mode": comparison_mode,
            "sample_days": sample_days,
            "sample_count": len(previous_volumes),
        }
    return {
        "ratio": round(latest_volume / average_volume, 4),
        "average_volume": round(average_volume, 4),
        "comparison_mode": comparison_mode,
        "sample_days": sample_days if comparison_mode == "SAME_TIME_OF_DAY" else None,
        "sample_count": len(previous_volumes),
    }


def _bar_time_slot(value: Any) -> str | None:
    if value in (None, ""):
        return None
    text = str(value).strip()
    if " " not in text:
        return None
    time_part = text.rsplit(" ", 1)[-1]
    return time_part[:5] if len(time_part) >= 5 else None


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


def _tws_order_status_to_order_status(value: str) -> str:
    normalized = str(value or "").strip().lower().replace(" ", "")
    if normalized in {"cancelled", "apicancelled"}:
        return OrderStatus.CANCELLED.value
    if normalized in {"inactive", "rejected"}:
        return OrderStatus.REJECTED.value
    if normalized == "filled":
        return OrderStatus.FILLED.value
    if normalized in {"pendingcancel", "pendingcancelsubmit"}:
        return OrderStatus.CANCELLED.value
    return OrderStatus.SUBMITTED.value


def _tws_raw_order_status_to_broker_status(value: str, transmit: bool = True) -> str:
    if not transmit:
        return "PREPARED_NOT_TRANSMITTED"
    normalized = str(value or "").strip().lower().replace(" ", "")
    if normalized == "pendingsubmit":
        return "PENDING_SUBMIT"
    if normalized in {"presubmitted", "submitted"}:
        return "TRANSMITTED"
    if normalized == "filled":
        return "FILLED"
    if normalized in {"partiallyfilled", "partialfilled"}:
        return "PARTIALLY_FILLED"
    if normalized in {"cancelled", "apicancelled", "pendingcancel", "pendingcancelsubmit"}:
        return "CANCELLED"
    if normalized == "inactive":
        return "INACTIVE_OR_REJECTED"
    if normalized == "rejected":
        return "REJECTED"
    return "UNKNOWN"
