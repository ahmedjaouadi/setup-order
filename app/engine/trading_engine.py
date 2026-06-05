from __future__ import annotations

import asyncio
import logging
from copy import deepcopy
from contextlib import suppress
from datetime import datetime, timezone
from typing import Any, Protocol
from zoneinfo import ZoneInfo

from app.broker.tws_connector import BrokerConnector, create_broker_connector
from app.engine.order_manager import (
    BrokerModeMismatchError,
    DuplicateOrderError,
    ManagementOnlyEntryError,
    OrderManager,
)
from app.engine.position_manager import PositionManager
from app.engine.reconciliation import ReconciliationEngine
from app.engine.risk_engine import RiskEngine, RiskLimits
from app.engine.setup_engine import SetupEngine
from app.engine.state_machine import StateMachine
from app.market_data.market_data_service import MarketDataService
from app.models import (
    BotStatus,
    ConnectionStatus,
    EventLevel,
    MarketSnapshot,
    SetupStatus,
    SetupRole,
    SignalAction,
    to_jsonable,
)
from app.setups.setup_factory import SetupFactory
from app.settings import Settings
from app.storage.event_store import EventStore
from app.storage.repositories import TradingRepository


class Broadcaster(Protocol):
    async def broadcast(self, event: dict[str, Any]) -> None:
        ...


class NullBroadcaster:
    async def broadcast(self, event: dict[str, Any]) -> None:
        return None


logger = logging.getLogger(__name__)
HEARTBEAT_INTERVAL_SECONDS = 5
HEARTBEAT_STALE_SECONDS = HEARTBEAT_INTERVAL_SECONDS * 3
CHART_TIMEFRAMES: dict[str, dict[str, str]] = {
    "3m": {"label": "3mn", "duration": "2 D", "bar_size": "3 mins"},
    "10m": {"label": "10mn", "duration": "5 D", "bar_size": "10 mins"},
    "15m": {"label": "15mn", "duration": "10 D", "bar_size": "15 mins"},
    "30m": {"label": "30mn", "duration": "20 D", "bar_size": "30 mins"},
    "1h": {"label": "1h", "duration": "30 D", "bar_size": "1 hour"},
    "4h": {"label": "4h", "duration": "90 D", "bar_size": "4 hours"},
    "1d": {"label": "1D", "duration": "30 D", "bar_size": "1 day"},
}


class TradingEngine:
    def __init__(
        self,
        settings: Settings,
        repository: TradingRepository,
        broker: BrokerConnector | None = None,
        broadcaster: Broadcaster | None = None,
    ) -> None:
        self.settings = settings
        self.repository = repository
        saved_broker = repository.get_bot_state("broker_selection", {})
        broker_connector = str(saved_broker.get("connector") or settings.broker_connector)
        for key in ("host", "port", "client_id"):
            if saved_broker.get(key) is not None:
                self.settings.raw["broker"][key] = saved_broker[key]
        self.settings.raw["broker"]["connector"] = broker_connector
        self.settings.raw["app"]["mode"] = (
            "simulation" if broker_connector == "simulated" else broker_connector
        )
        audit_state = self._tws_audit_state()
        self.settings.raw["broker"]["tws_audit_enabled"] = audit_state["enabled"]
        self.broker = broker or create_broker_connector(
            broker_connector,
            self.settings.raw.get("broker", {}),
        )
        self._apply_tws_audit_settings()
        self.broadcaster = broadcaster or NullBroadcaster()
        self.event_store = EventStore(repository)
        self.setup_engine = SetupEngine(
            repository=repository,
            event_store=self.event_store,
            setups_folder=settings.setups_folder,
        )
        self.risk_engine = RiskEngine(RiskLimits.from_config(settings.raw))
        self.order_manager = OrderManager(
            repository=repository,
            event_store=self.event_store,
            broker=self.broker,
            default_entry_order_type=settings.raw["orders"]["default_entry_order_type"],
            default_stop_order_type=settings.raw["orders"]["default_stop_order_type"],
            default_entry_limit_offset=float(
                settings.raw.get("setup_defaults", {})
                .get("entry", {})
                .get("limit_offset", 0.05)
            ),
        )
        self.position_manager = PositionManager(repository, self.event_store)
        self.reconciliation = ReconciliationEngine(repository, self.event_store, self.broker)
        self.market_data = MarketDataService()
        self.state_machine = StateMachine()
        self._monitor_task: asyncio.Task | None = None
        self._health: dict[str, Any] = {
            "started_at": None,
            "last_heartbeat_at": None,
            "last_broker_check_at": None,
            "last_setup_check_at": None,
            "last_market_tick_at": None,
            "last_market_analysis_at": None,
            "last_market_symbol": "",
            "last_stock_poll_at": None,
            "last_stock_poll_symbols": [],
            "last_stock_poll_count": 0,
            "last_stock_poll_ok": 0,
            "last_stock_poll_errors": 0,
            "last_stock_poll_reason": "",
            "last_stock_analysis_count": 0,
            "broker_status": ConnectionStatus.DISCONNECTED.value,
            "last_broker_error": "",
            "tws_request_count": 0,
            "last_tws_request": "",
            "last_tws_request_detail": "",
            "last_tws_request_sent_at": None,
            "last_tws_response_at": None,
            "last_tws_latency_ms": None,
            "last_tws_request_status": "",
            "last_tws_request_error": "",
            "heartbeat_count": 0,
            "last_checked_setups": 0,
            "last_processed_setups": 0,
            "last_error": "",
        }

    async def start(self) -> None:
        self._mark_engine_started()
        await self.broker.connect()
        broker_status = await self._broker_health_check()
        self._store_active_broker_selection()
        self.repository.set_bot_state(
            "runtime",
            self._runtime_payload(
                status=(
                    BotStatus.RUNNING.value
                    if broker_status == ConnectionStatus.CONNECTED
                    else BotStatus.PAUSED.value
                ),
                connection=broker_status.value,
            ),
        )
        loaded = self.setup_engine.load_all()
        reconciliation_result = await self.reconciliation.run()
        self.event_store.record(
            EventLevel.INFO,
            "engine_started",
            "Trading engine started",
            data={
                "loaded_setups": len(loaded),
                "reconciliation": reconciliation_result,
            },
        )
        await self._heartbeat()
        self._start_monitor()
        await self._broadcast_snapshot()

    async def stop(self) -> None:
        await self._stop_monitor()
        await self.broker.disconnect()
        self.repository.set_bot_state(
            "runtime",
            self._runtime_payload(
                status=BotStatus.PAUSED.value,
                connection=ConnectionStatus.DISCONNECTED.value,
            ),
        )
        self.event_store.record(EventLevel.INFO, "engine_stopped", "Trading engine stopped")
        await self._broadcast_snapshot()

    def runtime_state(self) -> dict[str, Any]:
        default = self._runtime_payload(
            status=BotStatus.PAUSED.value,
            connection=ConnectionStatus.DISCONNECTED.value,
        )
        state = self.repository.get_bot_state("runtime", default)
        return {**default, **state}

    def _runtime_payload(self, status: str, connection: str) -> dict[str, Any]:
        connector = str(getattr(self.broker, "connector_name", "simulated"))
        account_mode = str(getattr(self.broker, "account_mode", "simulation"))
        is_simulated = connector == "simulated"
        connection_label = "SIMULATED" if is_simulated and connection == "CONNECTED" else connection
        status_label = "SIM RUNNING" if is_simulated and status == "RUNNING" else status
        mode_label = "local simulation" if is_simulated else f"IBKR {account_mode} account"
        last_error = str(getattr(self.broker, "last_error", ""))
        broker_message = "Local simulated broker only. No TWS/IBKR account is used."
        if not is_simulated:
            broker_message = (
                f"Connected to TWS/Gateway for the {account_mode} account."
                if connection == ConnectionStatus.CONNECTED.value
                else last_error
                or "TWS/Gateway is not connected. Check API settings, port and client ID."
            )
        return {
            "status": status,
            "status_label": status_label,
            "mode": self.settings.raw.get("app", {}).get("mode", self.settings.app_mode),
            "mode_label": mode_label,
            "connection": connection,
            "connection_label": connection_label,
            "broker_connector": connector,
            "broker_account_mode": account_mode,
            "broker_display_name": str(getattr(self.broker, "display_name", "Broker")),
            "broker_host": getattr(self.broker, "host", ""),
            "broker_port": getattr(self.broker, "port", ""),
            "broker_client_id": getattr(self.broker, "client_id", ""),
            "broker_message": broker_message,
            "can_submit_orders": connection == "CONNECTED",
        }

    async def snapshot(self) -> dict[str, Any]:
        runtime = self.runtime_state()
        setups = self.repository.list_setups()
        positions = self.repository.list_positions()
        orders = self.repository.list_orders()
        stock_pnl = self._stock_pnl(positions)
        positions_pnl = round(sum(float(row["unrealized_pnl"]) for row in stock_pnl), 2)
        account = await self._account_snapshot(positions_pnl)
        self._drain_broker_audit()
        events = self.repository.list_events(limit=20)
        daily_pnl = self._money(account.get("today_pnl"))
        if daily_pnl is None:
            daily_pnl = positions_pnl
        active_setups = [
            setup
            for setup in setups
            if setup["enabled"]
            and setup["status"]
            not in {
                SetupStatus.CLOSED.value,
                SetupStatus.CANCELLED.value,
                SetupStatus.EXPIRED.value,
                SetupStatus.INVALIDATED.value,
                SetupStatus.DISABLED.value,
                SetupStatus.ERROR.value,
                SetupStatus.ERROR_REQUIRES_MANUAL_REVIEW.value,
            }
        ]
        max_daily_loss = float(self.settings.raw["risk"]["max_daily_loss_usd"])
        health = self._health_payload(len(active_setups))
        return {
            "runtime": runtime,
            "config": {
                "risk": self.settings.raw["risk"],
                "orders": self.settings.raw["orders"],
                "setup_defaults": self.settings.raw.get("setup_defaults", {}),
                "broker": self.settings.raw["broker"],
                "tws_audit": self._tws_audit_state(),
                "storage": self.settings.raw["storage"],
            },
            "metrics": {
                "active_setups": len(active_setups),
                "open_positions": len(positions),
                "open_orders": len(
                    [
                        order
                        for order in orders
                        if order["status"] in {"CREATED", "SUBMITTED"}
                    ]
                ),
                "daily_pnl": daily_pnl,
                "daily_loss_remaining": round(max_daily_loss + daily_pnl, 2),
                "positions_pnl": positions_pnl,
                "pnl_until_yesterday": account.get("pnl_until_yesterday"),
                "today_pnl": daily_pnl,
                "account": account,
            },
            "performance": {
                "account": account,
                "stock_pnl": stock_pnl,
            },
            "health": health,
            "setups": setups,
            "positions": positions,
            "orders": orders[:25],
            "events": events,
            "market": [to_jsonable(item) for item in self.market_data.all_latest()],
        }

    def _mark_engine_started(self) -> None:
        now = self._utc_now_iso()
        self._health.update(
            {
                "started_at": now,
                "last_heartbeat_at": now,
                "last_error": "",
            }
        )

    def _start_monitor(self) -> None:
        if self._monitor_task and not self._monitor_task.done():
            return
        self._monitor_task = asyncio.create_task(self._monitor_loop())

    async def _stop_monitor(self) -> None:
        if not self._monitor_task:
            return
        self._monitor_task.cancel()
        with suppress(asyncio.CancelledError):
            await self._monitor_task
        self._monitor_task = None

    async def _monitor_loop(self) -> None:
        while True:
            await asyncio.sleep(HEARTBEAT_INTERVAL_SECONDS)
            try:
                await self._heartbeat()
                await self._broadcast_snapshot()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.exception("Engine heartbeat failed")
                self._health["last_error"] = str(exc)

    async def _heartbeat(self) -> None:
        broker_status = await self._broker_health_check()
        broker_check_at = self._utc_now_iso()
        self._drain_broker_audit()
        broker_error = str(getattr(self.broker, "last_error", "") or "")
        runtime = self.runtime_state()
        current_status = str(runtime.get("status") or BotStatus.PAUSED.value)
        if broker_status != ConnectionStatus.CONNECTED and current_status == BotStatus.RUNNING.value:
            current_status = BotStatus.PAUSED.value
        self.repository.set_bot_state(
            "runtime",
            self._runtime_payload(
                status=current_status,
                connection=broker_status.value,
            ),
        )
        await self._poll_active_stock_quotes(current_status, broker_status)
        self._drain_broker_audit()
        broker_diagnostics = self._broker_diagnostics()
        checked_setups = len(
            [
                setup
                for setup in self.repository.list_setups()
                if setup["enabled"]
                and setup["status"]
                not in {
                    SetupStatus.CLOSED.value,
                    SetupStatus.CANCELLED.value,
                    SetupStatus.EXPIRED.value,
                    SetupStatus.INVALIDATED.value,
                    SetupStatus.DISABLED.value,
                    SetupStatus.ERROR.value,
                    SetupStatus.ERROR_REQUIRES_MANUAL_REVIEW.value,
                }
            ]
        )
        heartbeat_at = self._utc_now_iso()
        self._health.update(
            {
                "last_heartbeat_at": heartbeat_at,
                "last_broker_check_at": broker_check_at,
                "last_setup_check_at": heartbeat_at,
                "broker_status": broker_status.value,
                "last_broker_error": broker_error,
                **broker_diagnostics,
                "heartbeat_count": int(self._health.get("heartbeat_count") or 0) + 1,
                "last_checked_setups": checked_setups,
                "last_error": "",
            }
        )

    def _health_payload(self, active_setup_count: int) -> dict[str, Any]:
        heartbeat_age = self._age_seconds(self._health.get("last_heartbeat_at"))
        broker_status = str(self._health.get("broker_status") or "")
        if broker_status in {ConnectionStatus.DISCONNECTED.value, ConnectionStatus.ERROR.value}:
            status = "BROKER_DOWN"
            label = broker_status
        elif self._health.get("last_error"):
            status = "ERROR"
            label = "HEARTBEAT ERROR"
        elif heartbeat_age is None:
            status = "STARTING"
            label = "CHECKING"
        elif heartbeat_age <= HEARTBEAT_STALE_SECONDS:
            status = "OK"
            label = f"LIVE {heartbeat_age}s"
        else:
            status = "STALE"
            label = f"STALE {heartbeat_age}s"
        return {
            **self._health,
            "status": status,
            "label": label,
            "heartbeat_interval_seconds": HEARTBEAT_INTERVAL_SECONDS,
            "heartbeat_stale_seconds": HEARTBEAT_STALE_SECONDS,
            "heartbeat_age_seconds": heartbeat_age,
            "market_tick_age_seconds": self._age_seconds(self._health.get("last_market_tick_at")),
            "market_analysis_age_seconds": self._age_seconds(self._health.get("last_market_analysis_at")),
            "stock_poll_age_seconds": self._age_seconds(self._health.get("last_stock_poll_at")),
            "active_setup_count": active_setup_count,
        }

    async def market_history(
        self,
        symbol: str,
        timeframe: str,
    ) -> dict[str, Any]:
        normalized = self._normalize_chart_timeframe(timeframe)
        config = CHART_TIMEFRAMES[normalized]
        timeout = float(
            self.settings.raw.get("market", {}).get("tws_stock_quote_timeout_seconds", 4)
            or 4
        )
        result = await self.broker.historical_bars(
            symbol.upper(),
            duration=config["duration"],
            bar_size=config["bar_size"],
            timeout=timeout,
        )
        self._drain_broker_audit()
        return {
            **result,
            "timeframe": normalized,
            "timeframe_label": config["label"],
            "available_timeframes": self.chart_timeframes(),
        }

    @staticmethod
    def chart_timeframes() -> list[dict[str, str]]:
        return [
            {"id": key, "label": value["label"]}
            for key, value in CHART_TIMEFRAMES.items()
        ]

    @staticmethod
    def _normalize_chart_timeframe(timeframe: str) -> str:
        normalized = str(timeframe or "1d").strip().lower()
        aliases = {
            "3mn": "3m",
            "3min": "3m",
            "3 mins": "3m",
            "10mn": "10m",
            "10min": "10m",
            "10 mins": "10m",
            "15mn": "15m",
            "15min": "15m",
            "15 mins": "15m",
            "30mn": "30m",
            "30min": "30m",
            "30 mins": "30m",
            "60m": "1h",
            "60mn": "1h",
            "60min": "1h",
            "1 hour": "1h",
            "4 hours": "4h",
            "1 day": "1d",
            "1D": "1d",
            "d": "1d",
        }
        normalized = aliases.get(normalized, normalized)
        if normalized not in CHART_TIMEFRAMES:
            allowed = ", ".join(item["label"] for item in CHART_TIMEFRAMES.values())
            raise ValueError(f"Unsupported chart timeframe: {timeframe}. Allowed: {allowed}")
        return normalized

    async def _broker_health_check(self) -> ConnectionStatus:
        checker = getattr(self.broker, "health_check", None)
        if callable(checker):
            return await checker()
        return await self.broker.status()

    def _broker_diagnostics(self) -> dict[str, Any]:
        diagnostics = getattr(self.broker, "diagnostics", None)
        if callable(diagnostics):
            result = diagnostics()
            if isinstance(result, dict):
                return result
        return {}

    def _tws_audit_state(self) -> dict[str, bool]:
        default_enabled = bool(
            self.settings.raw.get("broker", {}).get("tws_audit_enabled", True)
        )
        saved = self.repository.get_bot_state("tws_audit", {})
        return {"enabled": bool(saved.get("enabled", default_enabled))}

    def _apply_tws_audit_settings(self) -> None:
        enabled = self._tws_audit_state()["enabled"]
        self.settings.raw["broker"]["tws_audit_enabled"] = enabled
        setter = getattr(self.broker, "set_audit_enabled", None)
        if callable(setter):
            setter(enabled)

    def _drain_broker_audit(self) -> None:
        drain = getattr(self.broker, "drain_audit_entries", None)
        if not callable(drain):
            return
        entries = drain()
        if not entries:
            return
        for entry in entries:
            if self._should_skip_tws_audit_event(entry):
                continue
            request_name = str(entry.get("request") or "request")
            status = str(entry.get("status") or "")
            latency = entry.get("latency_ms")
            latency_text = f" in {latency} ms" if latency is not None else ""
            level = EventLevel.INFO if status == "OK" else EventLevel.ERROR
            prefix = (
                "TWS cache"
                if str(entry.get("detail") or "").lower() == "session cache"
                else "TWS"
            )
            message = f"{prefix} {request_name} {status}{latency_text}".strip()
            extra = entry.get("extra")
            symbol = None
            if isinstance(extra, dict):
                symbol = extra.get("symbol")
            if (
                request_name in {"reqTickersAsync", "reqHistoricalDataAsync"}
                and isinstance(extra, dict)
                and symbol
            ):
                quote_text = self._stock_quote_fields_text(extra)
                detail = f": {quote_text}" if quote_text else ""
                message = (
                    f"TWS stock data {str(symbol).upper()} {status}"
                    f"{latency_text}{detail}"
                )
            self.event_store.record(
                level,
                "tws_request",
                message,
                symbol=str(symbol).upper() if symbol else None,
                data=entry,
            )

    @staticmethod
    def _should_skip_tws_audit_event(entry: dict[str, Any]) -> bool:
        if str(entry.get("status") or "") != "OK":
            return False
        return str(entry.get("request") or "") in {"accountValues", "reqCurrentTime"}

    @staticmethod
    def _utc_now_iso() -> str:
        return datetime.now(timezone.utc).isoformat()

    @staticmethod
    def _age_seconds(value: Any) -> int | None:
        if not value:
            return None
        try:
            parsed = datetime.fromisoformat(str(value))
        except ValueError:
            return None
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return max(int((datetime.now(timezone.utc) - parsed).total_seconds()), 0)

    def _stock_pnl(self, positions: list[dict[str, Any]]) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for position in positions:
            symbol = str(position["symbol"]).upper()
            quantity = int(position["quantity"])
            average_price = float(position["average_price"])
            latest = self.market_data.latest(symbol)
            current_price = float(latest.price) if latest else float(position["current_price"])
            unrealized_pnl = round((current_price - average_price) * quantity, 2)
            cost_basis = abs(average_price * quantity)
            pnl_percent = round((unrealized_pnl / cost_basis) * 100, 2) if cost_basis else None
            rows.append(
                {
                    "symbol": symbol,
                    "setup_id": position.get("setup_id"),
                    "quantity": quantity,
                    "average_price": self._money(average_price),
                    "current_price": self._money(current_price),
                    "market_value": self._money(current_price * quantity),
                    "unrealized_pnl": unrealized_pnl,
                    "pnl_percent": pnl_percent,
                    "current_stop": self._money(position.get("current_stop")),
                    "risk_remaining": self._money(position.get("risk_remaining")),
                    "status": "GAIN" if unrealized_pnl > 0 else "LOSS" if unrealized_pnl < 0 else "FLAT",
                    "price_source": "market" if latest else "position",
                }
            )
        return rows

    async def _poll_active_stock_quotes(
        self,
        runtime_status: str,
        broker_status: ConnectionStatus,
    ) -> None:
        market_config = self.settings.raw.get("market", {})
        enabled = bool(market_config.get("tws_stock_poll_enabled", True))
        interval = int(market_config.get("tws_stock_poll_interval_seconds", 15) or 15)
        timeout = float(market_config.get("tws_stock_quote_timeout_seconds", 4) or 4)
        if not enabled:
            self._health["last_stock_poll_reason"] = "disabled"
            return
        if broker_status != ConnectionStatus.CONNECTED:
            self._health["last_stock_poll_reason"] = "broker_not_connected"
            return
        should_analyze = runtime_status == BotStatus.RUNNING.value
        last_poll_age = self._age_seconds(self._health.get("last_stock_poll_at"))
        if last_poll_age is not None and last_poll_age < interval:
            return

        symbols = self._active_market_symbols()
        now = self._utc_now_iso()
        if not symbols:
            logger.warning("TWS stock poll skipped: no active enabled setup symbols")
            self._health.update(
                {
                    "last_stock_poll_at": now,
                    "last_stock_poll_symbols": [],
                    "last_stock_poll_count": 0,
                    "last_stock_poll_ok": 0,
                    "last_stock_poll_errors": 0,
                    "last_stock_poll_reason": "no_active_setups",
                    "last_stock_analysis_count": 0,
                }
            )
            self.event_store.record(
                EventLevel.WARNING,
                "stock_poll_skipped",
                "No active enabled setup symbols to poll",
            )
            return

        logger.info(
            "TWS stock poll started: %d symbols (%s)",
            len(symbols),
            ", ".join(symbols),
        )
        ok_count = 0
        error_count = 0
        analysis_count = 0
        for symbol in symbols:
            quote = await self.broker.market_snapshot(symbol, timeout=timeout)
            if quote.get("available"):
                ok_count += 1
                price = self._money(quote.get("price"))
                snapshot = MarketSnapshot(
                    symbol=symbol,
                    price=float(price),
                    open=self._money(quote.get("open")),
                    high=self._money(quote.get("high")),
                    low=self._money(quote.get("low")),
                    close=self._money(quote.get("close")) or price,
                    bid=self._money(quote.get("bid")),
                    ask=self._money(quote.get("ask")),
                    volume=self._float_value(quote.get("volume")),
                    current_bar_volume=self._float_value(
                        quote.get("current_bar_volume", quote.get("volume"))
                    ),
                    previous_high=self._money(quote.get("previous_high")),
                    daily_close=self._money(quote.get("close")) or price,
                    volume_ratio=self._float_value(quote.get("volume_ratio")),
                    volume_ratio_closed_bar=self._float_value(
                        quote.get("volume_ratio_closed_bar", quote.get("volume_ratio"))
                    ),
                    volume_ratio_live=self._float_value(quote.get("volume_ratio_live")),
                    average_volume_ratio_last_2_bars=self._float_value(
                        quote.get("average_volume_ratio_last_2_bars")
                    ),
                    elapsed_ratio=self._float_value(quote.get("elapsed_ratio")),
                    projected_volume=self._float_value(quote.get("projected_volume")),
                    bar_count=self._int_value(quote.get("bar_count")),
                    bars_above_resistance=self._int_value(
                        quote.get("bars_above_resistance")
                    ),
                    minimum_tick=self._float_value(quote.get("minimum_tick")),
                    atr_15m=self._float_value(quote.get("atr_15m")),
                    atr_1h=self._float_value(quote.get("atr_1h")),
                    session=str(quote["session"]).upper()
                    if quote.get("session")
                    else None,
                    market_open_time=str(quote["market_open_time"])
                    if quote.get("market_open_time")
                    else None,
                    current_time=str(quote["current_time"])
                    if quote.get("current_time")
                    else None,
                    last_confirmed_higher_low=self._money(
                        quote.get("last_confirmed_higher_low")
                    ),
                    support_level=self._money(quote.get("support_level")),
                    successful_retest_low=self._money(quote.get("successful_retest_low")),
                    structural_support=self._money(quote.get("structural_support")),
                    breakout_already_detected=bool(
                        quote.get("breakout_already_detected", False)
                    ),
                    new_higher_low_confirmed=bool(
                        quote.get("new_higher_low_confirmed", False)
                    ),
                    close_1h=self._money(quote.get("close_1h")),
                    historical_bars=quote.get("historical_bars")
                    if isinstance(quote.get("historical_bars"), list)
                    else [],
                )
                message = self._stock_quote_message(symbol, quote)
                logger.info(message)
                self.event_store.record(
                    EventLevel.INFO,
                    "stock_quote",
                    message,
                    symbol=symbol,
                    data=quote,
                )
                if should_analyze:
                    processed = await self._analyze_market_snapshot(snapshot)
                    analysis_count += len(processed)
                else:
                    self._record_market_tick(snapshot)
                    self._record_stock_analysis_skipped(
                        snapshot,
                        f"bot status {runtime_status}",
                    )
            else:
                error_count += 1
                message = (
                    quote.get("message")
                    or f"TWS did not return a usable quote for {symbol}"
                )
                logger.warning(
                    "TWS stock quote missing %s: %s %s",
                    symbol,
                    message,
                    self._stock_quote_fields_text(quote),
                )
                self.event_store.record(
                    EventLevel.WARNING,
                    "stock_quote_missing",
                    message,
                    symbol=symbol,
                    data=quote,
                )
            self._drain_broker_audit()

        self._health.update(
            {
                "last_stock_poll_at": now,
                "last_stock_poll_symbols": symbols,
                "last_stock_poll_count": len(symbols),
                "last_stock_poll_ok": ok_count,
                "last_stock_poll_errors": error_count,
                "last_stock_poll_reason": (
                    "ok"
                    if should_analyze and error_count == 0
                    else "quote_only_bot_not_running"
                    if error_count == 0
                    else "partial"
                ),
                "last_stock_analysis_count": analysis_count,
            }
        )
        logger.info(
            "TWS stock poll finished: %d symbols, %d quotes OK, %d errors, %d analyses",
            len(symbols),
            ok_count,
            error_count,
            analysis_count,
        )

    def _active_market_symbols(self) -> list[str]:
        terminal_statuses = {
            SetupStatus.CLOSED.value,
            SetupStatus.CANCELLED.value,
            SetupStatus.EXPIRED.value,
            SetupStatus.INVALIDATED.value,
            SetupStatus.DISABLED.value,
            SetupStatus.ERROR.value,
            SetupStatus.ERROR_REQUIRES_MANUAL_REVIEW.value,
        }
        symbols = {
            str(setup["symbol"]).upper()
            for setup in self.repository.list_setups()
            if setup["enabled"] and setup["status"] not in terminal_statuses
        }
        return sorted(symbols)

    @classmethod
    def _stock_quote_message(cls, symbol: str, quote: dict[str, Any]) -> str:
        fields = cls._stock_quote_fields_text(quote)
        if not fields:
            return f"TWS stock quote {symbol}: no market fields"
        return f"TWS stock quote {symbol}: {fields}"

    @staticmethod
    def _stock_quote_fields_text(quote: dict[str, Any]) -> str:
        keys = (
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
            "current_bar_volume",
            "previous_high",
            "volume_ratio",
            "volume_ratio_closed_bar",
            "volume_ratio_live",
            "average_volume_ratio_last_2_bars",
            "elapsed_ratio",
            "projected_volume",
            "minimum_tick",
            "atr_15m",
            "atr_1h",
            "session",
            "bar_date",
            "bar_count",
            "bars_above_resistance",
        )
        parts = [
            f"{key}={quote[key]}"
            for key in keys
            if quote.get(key) not in (None, "")
        ]
        return " ".join(parts)

    @staticmethod
    def _float_value(value: Any) -> float | None:
        if value in (None, ""):
            return None
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _int_value(value: Any) -> int | None:
        if value in (None, ""):
            return None
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    async def _account_snapshot(self, positions_pnl: float) -> dict[str, Any]:
        try:
            account = await self.broker.account_summary()
        except Exception as exc:
            account = {
                "available": False,
                "source": str(getattr(self.broker, "connector_name", "unknown")),
                "currency": "USD",
                "message": str(exc),
            }
        account = {
            "available": bool(account.get("available", False)),
            "source": account.get("source", str(getattr(self.broker, "connector_name", "unknown"))),
            "currency": account.get("currency", "USD"),
            "net_liquidation": self._money(account.get("net_liquidation")),
            "cash": self._money(account.get("cash")),
            "buying_power": self._money(account.get("buying_power")),
            "available_funds": self._money(account.get("available_funds")),
            "gross_position_value": self._money(account.get("gross_position_value")),
            "previous_day_equity": self._money(account.get("previous_day_equity")),
            "realized_pnl": self._money(account.get("realized_pnl")),
            "unrealized_pnl": self._money(account.get("unrealized_pnl")),
            "positions_unrealized_pnl": self._money(positions_pnl),
            "message": account.get("message", ""),
        }
        if account["unrealized_pnl"] is None:
            account["unrealized_pnl"] = account["positions_unrealized_pnl"]
        self._apply_account_history(account)
        if account.get("today_pnl") is None:
            previous_day_equity = account.get("previous_day_equity")
            net_liquidation = account.get("net_liquidation")
            if previous_day_equity is not None and net_liquidation is not None:
                account["today_pnl"] = self._money(net_liquidation - previous_day_equity)
            else:
                account["today_pnl"] = account.get("unrealized_pnl")
        if account.get("pnl_until_yesterday") is None:
            account["pnl_until_yesterday"] = account.get("realized_pnl")
        return account

    def _apply_account_history(self, account: dict[str, Any]) -> None:
        net_liquidation = account.get("net_liquidation")
        if net_liquidation is None:
            return
        today = self._local_date()
        state = self.repository.get_bot_state("account_history", {})
        raw_daily_start = state.get("daily_start", {})
        daily_start = dict(raw_daily_start) if isinstance(raw_daily_start, dict) else {}
        changed = False
        initial_equity = self._money(state.get("initial_equity"))
        if initial_equity is None:
            initial_equity = account.get("previous_day_equity") or net_liquidation
            state["initial_equity"] = initial_equity
            changed = True
        if today not in daily_start:
            daily_start[today] = account.get("previous_day_equity") or net_liquidation
            changed = True
        if changed:
            state["daily_start"] = {
                key: daily_start[key] for key in sorted(daily_start)[-45:]
            }
            self.repository.set_bot_state("account_history", state)
        start_today = self._money(daily_start.get(today))
        account["daily_start_equity"] = start_today
        account["initial_equity"] = initial_equity
        if start_today is not None:
            account["today_pnl"] = self._money(net_liquidation - start_today)
            account["pnl_until_yesterday"] = self._money(start_today - initial_equity)

    def _local_date(self) -> str:
        timezone_name = str(self.settings.raw.get("app", {}).get("timezone", "UTC"))
        try:
            timezone = ZoneInfo(timezone_name)
        except Exception:
            timezone = ZoneInfo("UTC")
        return datetime.now(timezone).date().isoformat()

    @staticmethod
    def _money(value: Any) -> float | None:
        if value in (None, ""):
            return None
        try:
            return round(float(value), 2)
        except (TypeError, ValueError):
            return None

    async def emergency_stop(self) -> dict[str, Any]:
        runtime = self.runtime_state()
        runtime["status"] = BotStatus.EMERGENCY_STOP.value
        self.repository.set_bot_state("runtime", runtime)
        if self.settings.raw["emergency_stop"].get("cancel_entry_orders", True):
            for order in self.repository.list_orders():
                if order["status"] == "SUBMITTED" and order["side"] == "BUY":
                    await self.order_manager.cancel_order(order["id"])
        self.event_store.record(
            EventLevel.CRITICAL,
            "emergency_stop",
            "Emergency stop activated",
        )
        await self._broadcast_snapshot()
        return await self.snapshot()

    async def resume(self) -> dict[str, Any]:
        runtime = self.runtime_state()
        broker_status = await self._broker_health_check()
        if broker_status != ConnectionStatus.CONNECTED:
            runtime = self._runtime_payload(
                status=BotStatus.PAUSED.value,
                connection=broker_status.value,
            )
            runtime["broker_message"] = "Cannot run: broker account is not connected."
            self.repository.set_bot_state("runtime", runtime)
            self.event_store.record(
                EventLevel.WARNING,
                "engine_resume_blocked",
                runtime["broker_message"],
            )
            await self._broadcast_snapshot()
            return await self.snapshot()
        runtime = self._runtime_payload(
            status=BotStatus.RUNNING.value,
            connection=broker_status.value,
        )
        self.repository.set_bot_state("runtime", runtime)
        self.event_store.record(EventLevel.INFO, "engine_resumed", "Trading engine resumed")
        await self._broadcast_snapshot()
        return await self.snapshot()

    async def pause(self) -> dict[str, Any]:
        broker_status = await self._broker_health_check()
        runtime = self._runtime_payload(
            status=BotStatus.PAUSED.value,
            connection=broker_status.value,
        )
        self.repository.set_bot_state("runtime", runtime)
        self.event_store.record(EventLevel.WARNING, "engine_paused", "Trading engine paused")
        await self._broadcast_snapshot()
        return await self.snapshot()

    async def force_sync(self) -> dict[str, int]:
        result = await self.reconciliation.run()
        await self._heartbeat()
        self._drain_broker_audit()
        await self._broadcast_snapshot()
        return result

    async def set_broker_connector(
        self,
        connector: str,
        host: str | None = None,
        port: int | None = None,
        client_id: int | None = None,
    ) -> dict[str, Any]:
        broker_config = self.settings.raw["broker"]
        if host:
            broker_config["host"] = host
        if port is not None:
            broker_config["port"] = port
            if connector in {"paper", "live"}:
                broker_config[f"{connector}_port"] = port
        if client_id is not None:
            broker_config["client_id"] = client_id
        broker_config["tws_audit_enabled"] = self._tws_audit_state()["enabled"]
        next_broker = create_broker_connector(connector, broker_config)
        await self.broker.disconnect()
        self._drain_broker_audit()
        self.broker = next_broker
        self._apply_tws_audit_settings()
        self.order_manager.broker = self.broker
        self.reconciliation.broker = self.broker
        self.settings.raw["broker"]["connector"] = connector
        self.settings.raw["app"]["mode"] = (
            "simulation" if connector == "simulated" else connector
        )
        await self.broker.connect()
        broker_status = await self._broker_health_check()
        self._store_active_broker_selection()
        runtime = self._runtime_payload(
            status=(
                BotStatus.RUNNING.value
                if broker_status == ConnectionStatus.CONNECTED
                else BotStatus.PAUSED.value
            ),
            connection=broker_status.value,
        )
        self.repository.set_bot_state("runtime", runtime)
        self.event_store.record(
            EventLevel.INFO,
            "broker_connector_selected",
            f"Broker connector selected: {connector}",
            data={"connector": connector, "connection": broker_status.value},
        )
        await self._broadcast_snapshot()
        return await self.snapshot()

    async def set_tws_audit_enabled(self, enabled: bool) -> dict[str, Any]:
        state = {"enabled": bool(enabled)}
        self.repository.set_bot_state("tws_audit", state)
        self._apply_tws_audit_settings()
        self.event_store.record(
            EventLevel.INFO,
            "tws_audit_updated",
            "TWS audit enabled" if state["enabled"] else "TWS audit disabled",
            data=state,
        )
        await self._broadcast_snapshot()
        return await self.snapshot()

    def _store_active_broker_selection(self) -> None:
        broker_config = self.settings.raw["broker"]
        connector = str(getattr(self.broker, "connector_name", broker_config.get("connector", "simulated")))
        broker_config["connector"] = connector
        broker_config["host"] = getattr(self.broker, "host", broker_config.get("host"))
        broker_config["port"] = getattr(self.broker, "port", broker_config.get("port"))
        broker_config["client_id"] = getattr(
            self.broker,
            "client_id",
            broker_config.get("client_id"),
        )
        self.repository.set_bot_state(
            "broker_selection",
            {
                "connector": broker_config.get("connector"),
                "host": broker_config.get("host"),
                "port": broker_config.get("port"),
                "client_id": broker_config.get("client_id"),
            },
        )

    async def set_setup_enabled(self, setup_id: str, enabled: bool) -> dict[str, Any]:
        setup = self.repository.get_setup(setup_id)
        if setup is None:
            raise KeyError(setup_id)
        self.repository.set_setup_enabled(setup_id, enabled)
        self.event_store.record(
            EventLevel.INFO,
            "setup_enabled_changed",
            "Setup enabled" if enabled else "Setup disabled",
            setup_id=setup_id,
            symbol=setup["symbol"],
            data={"enabled": enabled},
        )
        await self._broadcast_snapshot()
        return self.repository.get_setup(setup_id) or {}

    async def delete_setup(self, setup_id: str) -> dict[str, Any]:
        setup = self.repository.get_setup(setup_id)
        if setup is None:
            raise KeyError(setup_id)
        if self.repository.active_orders_for_setup(setup_id):
            raise ValueError("Cannot delete a setup with active orders")
        position = next(
            (
                item
                for item in self.repository.list_positions()
                if item.get("setup_id") == setup_id and int(item.get("quantity") or 0) != 0
            ),
            None,
        )
        if position:
            raise ValueError("Cannot delete a setup with an open position")
        file_deleted = self.setup_engine.delete_setup_file(setup_id)
        self.repository.delete_setup(setup_id)
        self.event_store.record(
            EventLevel.INFO,
            "setup_deleted",
            "Setup deleted",
            setup_id=setup_id,
            symbol=setup["symbol"],
            data={"file_deleted": file_deleted},
        )
        await self._broadcast_snapshot()
        return {"ok": True, "setup_id": setup_id, "file_deleted": file_deleted}

    async def save_setup(self, config: dict[str, Any]) -> dict[str, Any]:
        validation = self.setup_engine.create_or_update_from_config(config)
        if not validation.valid:
            return {"ok": False, "errors": validation.errors}
        await self._broadcast_snapshot()
        return {"ok": True, "setup": self.repository.get_setup(config["setup_id"])}

    async def process_market_snapshot(self, snapshot: MarketSnapshot) -> dict[str, Any]:
        runtime = self.runtime_state()
        if runtime.get("status") != BotStatus.RUNNING.value:
            self._record_market_tick(snapshot)
            self.event_store.record(
                EventLevel.WARNING,
                "market_snapshot_ignored",
                "Bot is not running",
                symbol=snapshot.symbol.upper(),
            )
            await self._broadcast_snapshot()
            return {"ok": False, "reason": "Bot is not running"}
        processed = await self._analyze_market_snapshot(snapshot)
        await self._broadcast_snapshot()
        return {"ok": True, "processed": processed}

    async def _analyze_market_snapshot(self, snapshot: MarketSnapshot) -> list[dict[str, Any]]:
        self._record_market_tick(snapshot)
        symbol = snapshot.symbol.upper()
        processed: list[dict[str, Any]] = []
        for setup in self.repository.list_setups():
            if not setup["enabled"] or setup["symbol"] != symbol:
                continue
            current_status = SetupStatus(setup["status"])
            if current_status in {
                SetupStatus.CLOSED,
                SetupStatus.CANCELLED,
                SetupStatus.EXPIRED,
                SetupStatus.INVALIDATED,
                SetupStatus.DISABLED,
                SetupStatus.ERROR,
                SetupStatus.ERROR_REQUIRES_MANUAL_REVIEW,
            }:
                continue
            strategy = SetupFactory.create(setup["config"])
            signal = strategy.evaluate(snapshot, current_status)
            trace = self._setup_analysis_trace(setup, snapshot, current_status, signal)
            processed.append(
                {
                    "setup_id": setup["setup_id"],
                    "setup_type": setup["setup_type"],
                    "status": current_status.value,
                    "action": signal.action.value,
                    "reason": signal.reason,
                    "target_status": signal.target_status.value
                    if signal.target_status
                    else None,
                    "entry_price": signal.entry_price,
                    "stop_loss": signal.stop_loss,
                    "new_stop": signal.new_stop,
                    "metadata": to_jsonable(signal.metadata),
                    "trace": trace,
                }
            )
            await self._handle_signal(setup, current_status, signal)
        if processed:
            self._record_stock_analysis(snapshot, processed)
        else:
            self._record_stock_analysis_skipped(snapshot, "no active setup for symbol")
        self._health.update(
            {
                "last_market_analysis_at": self._utc_now_iso(),
                "last_processed_setups": len(processed),
            }
        )
        return processed

    def _record_stock_analysis(
        self,
        snapshot: MarketSnapshot,
        processed: list[dict[str, Any]],
    ) -> None:
        symbol = snapshot.symbol.upper()
        summary = self._stock_analysis_summary(processed)
        message = f"Stock analysis {symbol}: {len(processed)} setup(s) evaluated"
        if summary:
            message = f"{message} ({summary})"
        logger.info(message)
        self.event_store.record(
            EventLevel.INFO,
            "stock_analysis",
            message,
            symbol=symbol,
            data={
                "snapshot": self._market_snapshot_payload(snapshot),
                "processed": processed,
            },
        )

    def _record_stock_analysis_skipped(
        self,
        snapshot: MarketSnapshot,
        reason: str,
    ) -> None:
        symbol = snapshot.symbol.upper()
        message = f"Stock analysis skipped {symbol}: {reason}"
        logger.info(message)
        self.event_store.record(
            EventLevel.INFO,
            "stock_analysis_skipped",
            message,
            symbol=symbol,
            data={
                "reason": reason,
                "snapshot": self._market_snapshot_payload(snapshot),
            },
        )

    @staticmethod
    def _stock_analysis_summary(processed: list[dict[str, Any]]) -> str:
        counts: dict[str, int] = {}
        for item in processed:
            action = str(item.get("action") or "UNKNOWN")
            counts[action] = counts.get(action, 0) + 1
        return " ".join(
            f"{action}={counts[action]}" for action in sorted(counts)
        )

    def _setup_analysis_trace(
        self,
        setup: dict[str, Any],
        snapshot: MarketSnapshot,
        current_status: SetupStatus,
        signal: Any,
    ) -> dict[str, Any]:
        config = setup.get("config", {})
        entry = config.get("entry", {}) if isinstance(config.get("entry", {}), dict) else {}
        setup_type = str(setup.get("setup_type") or config.get("setup_type") or "")
        setup_role = str(config.get("setup_role", SetupRole.ENTRY_AND_MANAGEMENT.value))
        checks: list[dict[str, Any]] = []

        def add(
            label: str,
            state: str,
            actual: Any = None,
            expected: Any = None,
            detail: str = "",
        ) -> None:
            check = {
                "label": label,
                "state": state,
            }
            if actual not in (None, ""):
                check["actual"] = to_jsonable(actual)
            if expected not in (None, ""):
                check["expected"] = to_jsonable(expected)
            if detail:
                check["detail"] = detail
            checks.append(check)

        price = self._float_value(snapshot.price)
        close = self._float_value(snapshot.close if snapshot.close is not None else snapshot.price)
        volume_ratio = self._float_value(snapshot.volume_ratio)
        entry_enabled = bool(entry.get("enabled", True))
        allows_entry = setup_role in {
            SetupRole.ENTRY_AND_MANAGEMENT.value,
            SetupRole.ENTRY_ONLY.value,
        }
        status_text = current_status.value
        status_waiting = status_text.startswith("WAITING") or status_text in {
            SetupStatus.LOADED.value,
            SetupStatus.VALIDATED.value,
            SetupStatus.RECONCILING_EXISTING_POSITION.value,
        }

        add(
            "Setup actif",
            "ok" if setup.get("enabled") and config.get("enabled", True) is not False else "bad",
            "ON" if setup.get("enabled") else "OFF",
            "ON",
        )
        add(
            "Role entree",
            "ok" if allows_entry and entry_enabled else "info" if not allows_entry else "bad",
            f"{setup_role} / {'entry ON' if entry_enabled else 'entry OFF'}",
            "ENTRY_AND_MANAGEMENT ou ENTRY_ONLY + entry ON",
        )
        add(
            "Statut suivi",
            "wait" if status_waiting else "ok",
            current_status.value,
            "statut non terminal",
        )
        add(
            "Donnees marche",
            "ok" if price is not None and price > 0 else "bad",
            price,
            "prix exploitable",
            f"timeframe={snapshot.timeframe} timestamp={snapshot.timestamp}",
        )

        if setup_type == "momentum_breakout":
            metadata = signal.metadata if isinstance(signal.metadata, dict) else {}
            analysis = metadata.get("analysis") if isinstance(metadata.get("analysis"), dict) else {}
            market = analysis.get("market") if isinstance(analysis.get("market"), dict) else {}
            spread_check = (
                analysis.get("spread_check")
                if isinstance(analysis.get("spread_check"), dict)
                else {}
            )
            stale = analysis.get("stale") if isinstance(analysis.get("stale"), dict) else {}
            validation = (
                analysis.get("validation")
                if isinstance(analysis.get("validation"), dict)
                else {}
            )
            offsets = analysis.get("offsets") if isinstance(analysis.get("offsets"), dict) else {}
            stop_meta = (
                analysis.get("protective_stop")
                if isinstance(analysis.get("protective_stop"), dict)
                else {}
            )
            risk_preview = (
                analysis.get("risk_preview")
                if isinstance(analysis.get("risk_preview"), dict)
                else {}
            )
            missed_retest = (
                analysis.get("missed_retest")
                if isinstance(analysis.get("missed_retest"), dict)
                else {}
            )
            missing_conditions = analysis.get("missing_conditions")
            blocking_conditions = analysis.get("blocking_conditions")
            if not isinstance(missing_conditions, list):
                missing_conditions = []
            if not isinstance(blocking_conditions, list):
                blocking_conditions = []
            resistance = self._float_value(analysis.get("resistance"))
            maximum_limit = self._float_value(
                analysis.get("active_limit_price")
                if analysis.get("active_limit_price") is not None
                else analysis.get("maximum_limit_price")
            )
            add(
                "Donnees obligatoires",
                "bad" if missing_conditions else "ok",
                ", ".join(missing_conditions) if missing_conditions else "completes",
                "bid/ask/spread/ATR/tick/volume/structure",
            )
            add(
                "Spread acceptable",
                "ok" if spread_check.get("ok") else "bad" if spread_check else "wait",
                (
                    f"spread={spread_check.get('spread')} "
                    f"bps={spread_check.get('spread_bps')}"
                ),
                (
                    f"bps<={spread_check.get('max_spread_bps')} "
                    f"spread<={spread_check.get('max_spread_atr')}"
                ),
            )
            add(
                "Trigger dynamique",
                "bad" if "raw_trigger_offset above cap" in offsets.get("blocking", []) else "ok" if offsets else "wait",
                offsets.get("trigger_offset"),
                offsets.get("trigger_offset_cap"),
                f"raw={offsets.get('raw_trigger_offset')} tick={offsets.get('minimum_tick')}",
            )
            add(
                "Limite dynamique",
                "bad" if "raw_limit_offset above cap" in offsets.get("blocking", []) else "ok" if offsets else "wait",
                offsets.get("limit_offset"),
                offsets.get("limit_offset_cap"),
                f"raw={offsets.get('raw_limit_offset')}",
            )
            add(
                "Prix vs resistance",
                self._threshold_state(price, resistance, ">"),
                price,
                f"> {resistance}",
            )
            add(
                "Transmission ask <= limite",
                self._threshold_state(market.get("ask"), maximum_limit, "<="),
                market.get("ask"),
                maximum_limit,
                "Le bot ne court pas apres le prix.",
            )
            add(
                "Setup depasse",
                "bad" if stale.get("is_missed_breakout") else "ok" if stale else "wait",
                market.get("ask"),
                f"{maximum_limit} + {stale.get('buffer')}",
                f"buffer_raw={stale.get('buffer_raw')} hard_cap={stale.get('hard_cap')}",
            )
            add(
                "Validation FAST_BREAKOUT",
                "ok" if validation.get("fast_breakout_valid") else "wait",
                validation.get("volume_ratio_closed_bar"),
                "close>resistance et RVOL>=1.50",
            )
            add(
                "Validation CONFIRMED_BREAKOUT",
                "ok" if validation.get("confirmed_breakout_valid") else "wait",
                (
                    f"bars={validation.get('bars_above_resistance')} "
                    f"avg_rvol={validation.get('average_volume_ratio_last_2_bars')}"
                ),
                "2 closes>resistance et RVOL moyen>=1.15",
            )
            if missed_retest:
                add(
                    "Validation BREAKOUT_RETEST",
                    "ok" if validation.get("breakout_retest_valid") else "wait",
                    (
                        f"low={missed_retest.get('current_low')} "
                        f"higher_low={missed_retest.get('new_higher_low_confirmed')}"
                    ),
                    self._range_text(
                        self._float_value(missed_retest.get("zone_min")),
                        self._float_value(missed_retest.get("zone_max")),
                    ),
                    "retest + close>=resistance + higher low + RVOL>=1.00",
                )
            add(
                "Validation retenue",
                "ok" if validation.get("valid") else "wait",
                validation.get("path") or "aucune",
                "FAST, CONFIRMED ou RETEST",
            )
            add(
                "Trigger entree",
                "info",
                analysis.get("active_trigger_price", analysis.get("trigger_price")),
                "round_up(resistance + trigger_offset)",
            )
            add(
                "Stop structurel",
                "bad" if stop_meta.get("missing") else "ok" if stop_meta else "wait",
                stop_meta.get("initial_stop_loss"),
                "support structurel - stop_buffer",
                f"support={stop_meta.get('structural_support')} buffer={stop_meta.get('stop_buffer')}",
            )
            add(
                "Risque worst-case",
                "ok" if risk_preview.get("risk_per_share", 0) > 0 else "wait",
                risk_preview.get("risk_per_share"),
                "maximum_limit_price - initial_stop_loss",
            )
            add(
                "Quantite maximale",
                "bad" if risk_preview.get("maximum_quantity") == 0 else "ok" if risk_preview else "wait",
                (
                    f"capital={risk_preview.get('quantity_by_capital')} "
                    f"risk={risk_preview.get('quantity_by_risk')}"
                ),
                risk_preview.get("maximum_quantity"),
            )
            add(
                "Conditions bloquantes",
                "bad" if blocking_conditions else "ok",
                " | ".join(str(item) for item in blocking_conditions) if blocking_conditions else "aucune",
                "aucune",
            )
        elif setup_type == "breakout_retest":
            breakout = config.get("breakout", {}) if isinstance(config.get("breakout", {}), dict) else {}
            retest = config.get("retest", {}) if isinstance(config.get("retest", {}), dict) else {}
            daily_level = self._float_value(breakout.get("daily_close_above"))
            zone_min = self._float_value(retest.get("zone_min"))
            zone_max = self._float_value(retest.get("zone_max"))
            no_close_below = self._float_value(retest.get("no_close_below")) or zone_min
            daily_close = self._float_value(snapshot.daily_close if snapshot.daily_close is not None else close)
            bullish = self._snapshot_bullish_confirmation(snapshot)
            add(
                "Invalidation retest",
                self._threshold_state(close, no_close_below, ">="),
                close,
                f">= {no_close_below}",
            )
            add(
                "Breakout journalier",
                self._threshold_state(daily_close, daily_level, ">"),
                daily_close,
                f"> {daily_level}",
            )
            add(
                "Prix dans zone retest",
                self._range_state(price, zone_min, zone_max),
                price,
                self._range_text(zone_min, zone_max),
            )
            add(
                "Bougie de confirmation",
                "ok" if bullish else "wait",
                "haussiere" if bullish else "non confirmee",
                "close > open ou bullish_candle",
            )
        elif setup_type == "aggressive_rebound":
            support = config.get("support_zone", {}) if isinstance(config.get("support_zone", {}), dict) else {}
            invalidation = config.get("invalidation", {}) if isinstance(config.get("invalidation", {}), dict) else {}
            zone_min = self._float_value(support.get("min"))
            zone_max = self._float_value(support.get("max"))
            close_below = self._float_value(invalidation.get("close_below")) or zone_min
            previous_high = self._float_value(snapshot.previous_high or snapshot.high or zone_max)
            bullish = self._snapshot_bullish_confirmation(snapshot)
            add("Invalidation support", self._threshold_state(close, close_below, ">="), close, f">= {close_below}")
            add("Prix dans support", self._range_state(price, zone_min, zone_max), price, self._range_text(zone_min, zone_max))
            add("Bougie haussiere", "ok" if bullish else "wait", "oui" if bullish else "non", "confirmation")
            add("Cloture au-dessus precedent high", self._threshold_state(close, previous_high, ">"), close, f"> {previous_high}")
        elif setup_type == "range_breakout":
            range_config = config.get("range", {}) if isinstance(config.get("range", {}), dict) else {}
            high = self._float_value(range_config.get("high"))
            low = self._float_value(range_config.get("low"))
            add("Invalidation range", self._threshold_state(close, low, ">="), close, f">= {low}")
            add("Cassure range high", self._threshold_state(price, high, ">"), price, f"> {high}")
        elif setup_type == "pullback_continuation":
            ema20 = self._float_value(snapshot.ema_20)
            ema50 = self._float_value(snapshot.ema_50)
            bullish = self._snapshot_bullish_confirmation(snapshot)
            add("EMA disponibles", "ok" if ema20 is not None and ema50 is not None else "wait", f"EMA20={ema20} EMA50={ema50}", "EMA20 + EMA50")
            add("Filtre tendance EMA50", self._threshold_state(price, ema50, ">="), price, f">= {ema50}")
            add("Tendance EMA20 > EMA50", self._threshold_state(ema20, ema50, ">"), ema20, f"> {ema50}")
            add("Pullback vers EMA20", self._threshold_state(price, ema20, "<="), price, f"<= {ema20}")
            add("Bougie de reprise", "ok" if bullish else "wait", "haussiere" if bullish else "non confirmee", "close > open ou bullish_candle")
        elif setup_type in {"position_management", "runner", "trailing_runner"}:
            add(
                "Mode gestion",
                "info",
                setup_type,
                "position existante",
                "Ce setup gere une position; il ne cherche pas une nouvelle entree.",
            )

        action = signal.action.value
        if action == SignalAction.ENTRY_READY.value:
            add("Signal entree", "ok", signal.entry_price, "ENTRY_READY")
            add("Controle risque", "info", "a lancer", "apres signal entree")
        elif action == SignalAction.INVALIDATE.value:
            add("Signal entree", "bad", signal.reason, "setup valide")
        else:
            add("Signal entree", "wait", signal.reason, "ENTRY_READY")

        return {
            "phase": self._analysis_phase_label(setup_type, current_status),
            "summary": f"{action}: {signal.reason}",
            "next_step": self._analysis_next_step(signal),
            "checks": checks,
        }

    @staticmethod
    def _analysis_phase_label(setup_type: str, status: SetupStatus) -> str:
        if status == SetupStatus.WAITING_ACTIVATION:
            return "Surveillance activation"
        if status == SetupStatus.WAITING_ENTRY_SIGNAL:
            return "Recherche signal entree"
        if status == SetupStatus.MISSED_BREAKOUT:
            return "Breakout manque"
        if status == SetupStatus.WAITING_RETEST:
            return "Attente retest apres breakout manque"
        if status == SetupStatus.REARMED_ON_NEW_BASE:
            return "Rearme sur nouvelle base"
        if status == SetupStatus.EXPIRED:
            return "Setup expire"
        if status in {SetupStatus.IN_POSITION, SetupStatus.MANAGING_POSITION}:
            return "Gestion position"
        if setup_type in {"position_management", "runner", "trailing_runner"}:
            return "Gestion uniquement"
        return status.value

    @staticmethod
    def _analysis_next_step(signal: Any) -> str:
        action = signal.action
        if action == SignalAction.ENTRY_READY:
            return "Verifier le risque, calculer la taille, puis envoyer l'ordre d'entree."
        if action == SignalAction.STATUS_CHANGE and signal.target_status:
            if signal.target_status == SetupStatus.MISSED_BREAKOUT:
                return "Ne pas entrer au marche; attendre une zone de retest propre."
            if signal.target_status == SetupStatus.WAITING_RETEST:
                return "Observer le retest et exiger une confirmation avant de rearmer."
            if signal.target_status == SetupStatus.REARMED_ON_NEW_BASE:
                return "Surveiller la nouvelle resistance locale et le nouveau trigger."
            if signal.target_status == SetupStatus.EXPIRED:
                return "Arreter la recherche d'entree pour ce setup."
            return f"Passer au statut {signal.target_status.value} et continuer la surveillance."
        if action == SignalAction.INVALIDATE:
            return "Invalider le setup et stopper la recherche d'entree."
        if action == SignalAction.RAISE_STOP:
            return "Monter le stop de protection selon la regle de gestion."
        return f"Continuer a surveiller: {signal.reason}"

    @staticmethod
    def _threshold_state(actual: float | None, expected: float | None, operator: str) -> str:
        if actual is None or expected is None:
            return "wait"
        if operator == ">":
            return "ok" if actual > expected else "wait"
        if operator == ">=":
            return "ok" if actual >= expected else "wait"
        if operator == "<":
            return "ok" if actual < expected else "wait"
        if operator == "<=":
            return "ok" if actual <= expected else "wait"
        return "info"

    @staticmethod
    def _range_state(actual: float | None, low: float | None, high: float | None) -> str:
        if actual is None or low is None or high is None:
            return "wait"
        return "ok" if low <= actual <= high else "wait"

    @staticmethod
    def _range_text(low: float | None, high: float | None) -> str:
        if low is None or high is None:
            return "zone non renseignee"
        return f"{low} - {high}"

    @staticmethod
    def _snapshot_bullish_confirmation(snapshot: MarketSnapshot) -> bool:
        if snapshot.bullish_candle:
            return True
        if snapshot.close is not None and snapshot.open is not None:
            return snapshot.close > snapshot.open
        return False

    @staticmethod
    def _market_snapshot_payload(snapshot: MarketSnapshot) -> dict[str, Any]:
        payload: dict[str, Any] = {}
        for key in (
            "symbol",
            "price",
            "timestamp",
            "timeframe",
            "open",
            "high",
            "low",
            "close",
            "bid",
            "ask",
            "volume",
            "current_bar_volume",
            "previous_high",
            "daily_close",
            "volume_ratio",
            "volume_ratio_closed_bar",
            "volume_ratio_live",
            "average_volume_ratio_last_2_bars",
            "elapsed_ratio",
            "projected_volume",
            "bar_count",
            "bars_above_resistance",
            "minimum_tick",
            "atr_15m",
            "atr_1h",
            "session",
            "market_open_time",
            "current_time",
            "last_confirmed_higher_low",
            "support_level",
            "successful_retest_low",
            "structural_support",
            "breakout_already_detected",
            "new_higher_low_confirmed",
            "close_1h",
            "ema_20",
            "ema_50",
            "bullish_candle",
        ):
            value = getattr(snapshot, key)
            if key == "bullish_candle" or value not in (None, ""):
                payload[key] = to_jsonable(value)
        payload["symbol"] = snapshot.symbol.upper()
        return payload

    def _record_market_tick(self, snapshot: MarketSnapshot) -> None:
        self.market_data.update(snapshot)
        self._health.update(
            {
                "last_market_tick_at": self._utc_now_iso(),
                "last_market_symbol": snapshot.symbol.upper(),
            }
        )

    async def _handle_signal(self, setup: dict[str, Any], current_status: SetupStatus, signal: Any) -> None:
        if signal.action == SignalAction.HOLD:
            return
        if signal.action == SignalAction.INVALIDATE and signal.target_status:
            self._transition_setup(setup, current_status, signal.target_status, signal.reason)
            return
        if signal.action == SignalAction.STATUS_CHANGE and signal.target_status:
            self._transition_setup(setup, current_status, signal.target_status, signal.reason)
            return
        if signal.action == SignalAction.RAISE_STOP and signal.new_stop is not None:
            moved = self.position_manager.raise_stop(setup["symbol"], signal.new_stop)
            if moved and signal.target_status:
                self._transition_setup(setup, current_status, signal.target_status, signal.reason)
            return
        if signal.action == SignalAction.ENTRY_READY:
            setup_role = str(
                setup.get("config", {}).get(
                    "setup_role",
                    SetupRole.ENTRY_AND_MANAGEMENT.value,
                )
            )
            if setup_role == SetupRole.MANAGEMENT_ONLY.value:
                self.event_store.record(
                    EventLevel.CRITICAL,
                    "management_only_entry_blocked",
                    "MANAGEMENT_ONLY setup cannot place an entry order",
                    setup_id=setup["setup_id"],
                    symbol=setup["symbol"],
                )
                self.repository.update_setup_status(
                    setup["setup_id"],
                    SetupStatus.ERROR_REQUIRES_MANUAL_REVIEW.value,
                    "MANAGEMENT_ONLY entry signal blocked",
                )
                return
            if signal.entry_price is None or signal.stop_loss is None:
                self.event_store.record(
                    EventLevel.ERROR,
                    "entry_signal_rejected",
                    "Entry signal missing price or stop",
                    setup_id=setup["setup_id"],
                    symbol=setup["symbol"],
                )
                return
            effective_setup = self._setup_with_signal_overrides(setup, signal)
            open_positions = len(self.repository.list_positions())
            exposure = sum(
                float(position["average_price"]) * int(position["quantity"])
                for position in self.repository.list_positions()
            )
            daily_pnl = sum(float(position["unrealized_pnl"]) for position in self.repository.list_positions())
            decision = self.risk_engine.evaluate(
                setup_config=effective_setup["config"],
                entry_price=signal.entry_price,
                stop_loss=signal.stop_loss,
                open_positions=open_positions,
                current_exposure_usd=exposure,
                daily_pnl_usd=daily_pnl,
            )
            if not decision.approved:
                self.event_store.record(
                    EventLevel.RISK,
                    "entry_rejected_by_risk",
                    decision.reason,
                    setup_id=setup["setup_id"],
                    symbol=setup["symbol"],
                )
                return
            try:
                await self.order_manager.place_entry_order(effective_setup, decision)
            except BrokerModeMismatchError as exc:
                self.event_store.record(
                    EventLevel.RISK,
                    "broker_mode_mismatch",
                    str(exc),
                    setup_id=setup["setup_id"],
                    symbol=setup["symbol"],
                )
            except ManagementOnlyEntryError as exc:
                self.event_store.record(
                    EventLevel.CRITICAL,
                    "management_only_entry_blocked",
                    str(exc),
                    setup_id=setup["setup_id"],
                    symbol=setup["symbol"],
                )
                self.repository.update_setup_status(
                    setup["setup_id"],
                    SetupStatus.ERROR_REQUIRES_MANUAL_REVIEW.value,
                    str(exc),
                )
            except DuplicateOrderError as exc:
                self.event_store.record(
                    EventLevel.RISK,
                    "duplicate_order_blocked",
                    str(exc),
                    setup_id=setup["setup_id"],
                    symbol=setup["symbol"],
                )

    @staticmethod
    def _setup_with_signal_overrides(
        setup: dict[str, Any],
        signal: Any,
    ) -> dict[str, Any]:
        metadata = signal.metadata if isinstance(signal.metadata, dict) else {}
        entry_overrides = metadata.get("entry_overrides")
        risk_overrides = metadata.get("risk_overrides")
        if not isinstance(entry_overrides, dict) and not isinstance(risk_overrides, dict):
            return setup
        effective = deepcopy(setup)
        config = effective.setdefault("config", {})
        if isinstance(entry_overrides, dict):
            entry = config.setdefault("entry", {})
            for key, value in entry_overrides.items():
                if value is not None:
                    entry[key] = value
        if isinstance(risk_overrides, dict):
            risk = config.setdefault("risk", {})
            for key, value in risk_overrides.items():
                if value is not None:
                    risk[key] = value
        return effective

    def _transition_setup(
        self,
        setup: dict[str, Any],
        current_status: SetupStatus,
        target_status: SetupStatus,
        reason: str,
    ) -> None:
        try:
            new_status = self.state_machine.transition(current_status, target_status)
        except Exception as exc:
            logger.warning("Rejected transition for %s: %s", setup["setup_id"], exc)
            self.event_store.record(
                EventLevel.ERROR,
                "setup_transition_rejected",
                str(exc),
                setup_id=setup["setup_id"],
                symbol=setup["symbol"],
            )
            return
        self.repository.update_setup_status(setup["setup_id"], new_status.value, reason)
        self.event_store.record(
            EventLevel.INFO,
            "setup_status_changed",
            reason,
            setup_id=setup["setup_id"],
            symbol=setup["symbol"],
            data={"from": current_status.value, "to": new_status.value},
        )

    async def simulate_fill_order(
        self,
        order_id: str,
        fill_price: float,
    ) -> dict[str, Any]:
        position = await self.order_manager.simulate_fill_order(order_id, fill_price)
        await self._broadcast_snapshot()
        if position is None:
            return {"ok": False, "reason": "Order cannot be filled"}
        return {"ok": True, "position": to_jsonable(position)}

    async def move_stop(self, symbol: str, new_stop: float) -> dict[str, Any]:
        moved = self.position_manager.raise_stop(symbol, new_stop)
        await self._broadcast_snapshot()
        return {"ok": moved}

    async def _broadcast_snapshot(self) -> None:
        await self.broadcaster.broadcast(
            {"type": "snapshot", "payload": await self.snapshot()}
        )
