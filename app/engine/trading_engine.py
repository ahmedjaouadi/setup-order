from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Mapping
from contextlib import suppress
from datetime import UTC, datetime
from typing import Any, Protocol
from zoneinfo import ZoneInfo

from app.broker.ib_models import BrokerOrderRequest, BrokerPosition
from app.broker.tws_connector import BrokerConnector, create_broker_connector
from app.engine.action_executor import ActionExecutor
from app.engine.broker_reality import (
    REPORT_STATE_KEY,
    broker_tracker_config,
    execution_safety_config,
    freshen_broker_reality_report,
    normalize_broker_order_status,
)
from app.engine.entry_order_executor import EntryOrderExecutor
from app.engine.opportunity_alert_service import OpportunityAlertService
from app.engine.order_manager import OrderManager
from app.engine.position_action_executor import PositionActionExecutor
from app.engine.position_manager import PositionManager
from app.engine.reconciliation import ReconciliationEngine, ReconciliationResult
from app.engine.risk_engine import RiskEngine, RiskLimits
from app.engine.setup_diagnostics import market_snapshot_payload
from app.engine.setup_engine import SetupEngine
from app.engine.setup_lifecycle_service import SetupLifecycleService
from app.engine.setup_status_reporter import SetupStatusReporter
from app.engine.setup_template_service import SetupTemplateService
from app.engine.signal_engine import SignalEngine
from app.engine.state_machine import StateMachine
from app.engine.stock_market_monitor import (
    StockMarketMonitor,
    active_market_symbols,
    float_value,
    int_value,
    stock_analysis_dedupe_key,
    stock_analysis_summary,
    stock_quote_fields_text,
    stock_quote_message,
)
from app.engine.stop_modification_service import StopModificationService
from app.engine.trade_guards import TradeGuardsService
from app.market_data.market_data_service import MarketDataService
from app.models import (
    BotStatus,
    ConnectionStatus,
    EventLevel,
    MarketSnapshot,
    SetupStatus,
    to_jsonable,
)
from app.settings import Settings
from app.storage.event_store import EventStore
from app.storage.repositories import TradingRepository


class Broadcaster(Protocol):
    async def broadcast(self, event: dict[str, Any]) -> None: ...


class NullBroadcaster:
    async def broadcast(self, event: dict[str, Any]) -> None:
        return None


logger = logging.getLogger(__name__)
HEARTBEAT_INTERVAL_SECONDS = 5
HEARTBEAT_STALE_SECONDS = HEARTBEAT_INTERVAL_SECONDS * 3
ACCOUNT_SNAPSHOT_TTL_SECONDS = 30
# Kept at (or under) the heartbeat cadence so the Ordres & Positions page
# tracks TWS with <= 5s of perceived latency (etape 10.2).
BROKER_RUNTIME_SNAPSHOT_TTL_SECONDS = 5
EQUITY_SNAPSHOT_INTERVAL_SECONDS = 120
# Full dashboard snapshots are expensive (broker RPCs + DB aggregation) and the
# GUI requests one on every page navigation. A short server-side cache makes
# tab switches instant; state-changing engine methods invalidate it so the GUI
# never reads stale data right after an action.
SNAPSHOT_CACHE_TTL_SECONDS = 2.5
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
        data_quality_service: Any | None = None,
        feature_store: Any | None = None,
    ) -> None:
        self.settings = settings
        self.repository = repository
        saved_broker = repository.get_bot_state("broker_selection", {})
        broker_connector = self._normalize_user_connector(
            str(saved_broker.get("connector") or settings.broker_connector)
        )
        for key in ("host", "port", "client_id"):
            if saved_broker.get(key) is not None:
                self.settings.raw["broker"][key] = saved_broker[key]
        self.settings.raw["broker"]["connector"] = broker_connector
        self.settings.raw["app"]["mode"] = broker_connector
        audit_state = self._tws_audit_state()
        self.settings.raw["broker"]["tws_audit_enabled"] = audit_state["enabled"]
        broker_config = dict(self.settings.raw.get("broker", {}))
        broker_config["market_data_policy"] = self.settings.raw.get("market_data", {})
        broker_config["indicator_policy"] = self.settings.raw.get("indicators", {})
        self.broker = broker or create_broker_connector(
            broker_connector,
            broker_config,
        )
        self._apply_tws_audit_settings()
        self.broadcaster = broadcaster or NullBroadcaster()
        self.event_store = EventStore(repository)
        self.setup_engine = SetupEngine(
            repository=repository,
            event_store=self.event_store,
            setups_folder=settings.setups_folder,
        )
        self.setup_status_reporter = SetupStatusReporter(
            settings=settings,
            repository=repository,
            setup_engine=self.setup_engine,
            broker_provider=lambda: self.broker,
        )
        self.setup_template_service = SetupTemplateService(settings)
        self.risk_engine = RiskEngine(RiskLimits.from_config(settings.raw))
        self.order_manager = OrderManager(
            repository=repository,
            event_store=self.event_store,
            broker=self.broker,
            default_entry_order_type=settings.raw["orders"]["default_entry_order_type"],
            default_stop_order_type=settings.raw["orders"]["default_stop_order_type"],
            default_entry_limit_offset=float(
                settings.raw.get("setup_defaults", {}).get("entry", {}).get("limit_offset", 0.05)
            ),
            settings=settings.raw,
        )
        self.trade_guards = TradeGuardsService(repository, settings.raw)
        self.position_manager = PositionManager(
            repository,
            self.event_store,
            on_position_closed=self.trade_guards.record_position_closed,
        )
        self.reconciliation = ReconciliationEngine(
            repository,
            self.event_store,
            self.broker,
            settings.raw,
        )
        self.market_data = MarketDataService()
        self.state_machine = StateMachine()
        self.setup_lifecycle = SetupLifecycleService(
            repository=repository,
            event_store=self.event_store,
            state_machine=self.state_machine,
            settings=settings.raw,
            market_snapshot_provider=lambda symbol: self.market_data.latest(symbol),
        )
        self.signal_engine = SignalEngine(
            repository,
            settings.raw,
            lifecycle_service=self.setup_lifecycle,
            trade_guards=self.trade_guards,
        )
        market_config = settings.raw.get("market", {})
        self.opportunity_alert_service = OpportunityAlertService(
            repository,
            self.event_store,
            near_ready_threshold=float(
                market_config.get("opportunity_near_ready_threshold", 0.96) or 0.96
            ),
            cooldown_seconds=float(
                market_config.get("opportunity_alert_cooldown_seconds", 300) or 300
            ),
        )
        self.action_executor = ActionExecutor(
            repository,
            self.event_store,
            self.state_machine,
        )
        self.position_action_executor = PositionActionExecutor(
            repository,
            self.event_store,
            self.position_manager,
            self.state_machine,
        )
        self.stop_modification_service = StopModificationService(
            repository,
            self.event_store,
            self.broker,
            self.position_manager,
            trade_guards=self.trade_guards,
        )
        self.entry_order_executor = EntryOrderExecutor(
            repository,
            self.event_store,
            self.risk_engine,
            self.order_manager,
            settings=settings.raw,
            lifecycle_service=self.setup_lifecycle,
            trade_guards=self.trade_guards,
        )
        self._monitor_task: asyncio.Task | None = None
        self._snapshot_cache: dict[str, Any] | None = None
        self._snapshot_cache_at: float = 0.0
        self._last_equity_record_at: float = 0.0
        self._account_summary_cache: dict[str, Any] | None = None
        self._account_summary_cached_at: float = 0.0
        self._broker_positions_cache: list[dict[str, Any]] | None = None
        self._broker_positions_cached_at: float = 0.0
        self._broker_executions_cache: list[dict[str, Any]] | None = None
        self._broker_executions_cached_at: float = 0.0
        self._broker_open_orders_cache: list[BrokerOrderRequest] | None = None
        self._broker_open_orders_cached_at: float = 0.0
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
            "last_stock_poll_timeout_seconds": 0,
            "last_stock_analysis_count": 0,
            "last_reconciliation_at": None,
            "last_reconciliation_result": {},
            "last_reconciliation_error": "",
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
        self.stock_market_monitor = StockMarketMonitor(
            settings=settings,
            repository=repository,
            event_store=self.event_store,
            market_data=self.market_data,
            signal_engine=self.signal_engine,
            signal_handler=self._handle_signal,
            broker_provider=lambda: self.broker,
            health=self._health,
            audit_drain=self._drain_broker_audit,
            now_provider=lambda: self._utc_now_iso(),
            opportunity_alert_service=self.opportunity_alert_service,
            data_quality_service=data_quality_service,
            feature_store=feature_store,
        )

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
        self._mark_reconciliation_completed(reconciliation_result)
        self.setup_lifecycle.revalidate_all(force=True)
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
        connector = str(getattr(self.broker, "connector_name", "paper"))
        account_mode = str(getattr(self.broker, "account_mode", "paper"))
        is_simulated = connector == "simulated"
        connection_label = connection
        status_label = status
        mode_label = (
            f"internal {account_mode} test broker"
            if is_simulated
            else f"IBKR {account_mode} account"
        )
        last_error = str(getattr(self.broker, "last_error", ""))
        broker_message = "Internal broker reserved for automated tests."
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

    @staticmethod
    def _normalize_user_connector(connector: str) -> str:
        normalized = str(connector or "").strip().lower()
        if normalized in {"paper", "live"}:
            return normalized
        return "paper"

    def invalidate_snapshot_cache(self) -> None:
        self._snapshot_cache = None
        self._snapshot_cache_at = 0.0

    async def snapshot(self) -> dict[str, Any]:
        if (
            self._snapshot_cache is not None
            and time.monotonic() - self._snapshot_cache_at < SNAPSHOT_CACHE_TTL_SECONDS
        ):
            return self._snapshot_cache
        result = await self._build_snapshot()
        self._snapshot_cache = result
        self._snapshot_cache_at = time.monotonic()
        return result

    async def _build_snapshot(self) -> dict[str, Any]:
        runtime = self.runtime_state()
        setups = self.repository.list_setups()
        local_orders = self.repository.list_orders()
        local_positions = self.repository.list_positions()
        broker_open_orders = await self._broker_open_orders_snapshot()
        broker_positions = await self._broker_positions_snapshot(
            setups,
            local_positions,
            local_orders,
            broker_open_orders,
        )
        positions = self._merge_position_snapshots(
            local_positions,
            broker_positions,
            await self._broker_is_connected(),
        )
        display_orders = self._orders_with_broker_overlay(
            local_orders,
            broker_open_orders,
            setups,
        )
        orders = self.repository.list_orders_with_protection(
            orders=display_orders,
            positions=positions,
        )
        events = self.repository.list_events(limit=200)
        orders = self._enrich_orders_with_event_diagnostics(orders, events)
        stock_pnl = self._stock_pnl(positions)
        direct_positions_pnl = round(sum(float(row["unrealized_pnl"]) for row in stock_pnl), 2)
        account = await self._account_snapshot(direct_positions_pnl)
        broker_reality = self._broker_reality_snapshot()
        self._drain_broker_audit()
        broker_pnl = broker_reality.get("pnl", {}) if isinstance(broker_reality, dict) else {}
        broker_connected = runtime.get("connection") == ConnectionStatus.CONNECTED.value
        broker_pnl_status = str(broker_pnl.get("status") or broker_pnl.get("sync_status") or "")
        broker_pnl_fresh = (
            broker_reality.get("broker_tracker_status") == "OK"
            and broker_pnl.get("source") == "TWS"
            and broker_pnl_status == "OK"
        )
        positions_pnl = (
            self._money(broker_pnl.get("unrealized_pnl"))
            if broker_pnl_fresh and broker_pnl.get("unrealized_pnl") is not None
            else direct_positions_pnl
        )
        if positions_pnl is None:
            positions_pnl = 0.0
        daily_pnl = self._money(broker_pnl.get("daily_pnl")) if broker_pnl_fresh else None
        pnl_source = "TWS" if daily_pnl is not None else "TWS_STALE"
        if daily_pnl is None and not broker_connected:
            daily_pnl = self._money(account.get("today_pnl_live_estimate"))
            pnl_source = "LOCAL_FALLBACK" if daily_pnl is not None else "UNAVAILABLE"
        if daily_pnl is None:
            daily_pnl_for_limits = 0.0
        else:
            daily_pnl_for_limits = daily_pnl
        active_broker_order_count = self._active_broker_order_count(broker_open_orders)
        prepared_broker_order_count = self._broker_order_count_by_status(
            broker_open_orders,
            {"PREPARED_NOT_TRANSMITTED"},
        )
        local_active_order_count = len([order for order in orders if order.get("is_active")])
        open_positions_count = self._broker_reality_int(
            broker_reality,
            "broker_positions_count",
        )
        if open_positions_count is None:
            open_positions_count = len(positions)
        open_orders_count = self._broker_reality_int(
            broker_reality,
            "broker_active_orders",
        )
        if open_orders_count is None:
            open_orders_count = (
                active_broker_order_count if broker_connected else local_active_order_count
            )
        prepared_orders_count = self._broker_reality_int(
            broker_reality,
            "broker_prepared_not_transmitted_orders",
        )
        if prepared_orders_count is None:
            prepared_orders_count = prepared_broker_order_count
        active_setups = [
            setup
            for setup in setups
            if setup["status"]
            not in {
                SetupStatus.CLOSED.value,
                SetupStatus.CANCELLED.value,
                SetupStatus.EXPIRED.value,
                SetupStatus.INVALIDATED.value,
                SetupStatus.ERROR.value,
                SetupStatus.ERROR_REQUIRES_MANUAL_REVIEW.value,
            }
        ]
        max_daily_loss = float(self.settings.raw["risk"]["max_daily_loss_usd"])
        health = self._health_payload(len(active_setups))
        health.update(
            {
                "broker_tracker_status": broker_reality.get("broker_tracker_status"),
                "broker_sync_age_seconds": broker_reality.get("broker_sync_age_seconds"),
                "broker_reality_blocked": broker_reality.get("auto_execution_blocked"),
                "broker_reality_blocking_reasons": broker_reality.get("blocking_reasons", []),
                "broker_reality_mismatch_count": broker_reality.get("mismatch_count", 0),
            }
        )
        self._maybe_record_equity(
            net_liquidation=account.get("net_liquidation"),
            daily_pnl=daily_pnl,
            positions_pnl=positions_pnl,
            open_positions=open_positions_count,
            source=pnl_source,
        )
        return {
            "runtime": runtime,
            "config": {
                "risk": self.settings.raw["risk"],
                "orders": self.settings.raw["orders"],
                "setup_defaults": self.settings.raw.get("setup_defaults", {}),
                "broker": self.settings.raw["broker"],
                "broker_tracker": self.settings.raw.get("broker_tracker", {}),
                "execution_safety": self.settings.raw.get("execution_safety", {}),
                "tws_audit": self._tws_audit_state(),
                "storage": self.settings.raw["storage"],
            },
            "metrics": {
                "active_setups": len(active_setups),
                "auto_execution_setups": len(
                    [setup for setup in active_setups if setup.get("enabled")]
                ),
                "open_positions": open_positions_count,
                "open_orders": open_orders_count,
                "broker_active_orders": open_orders_count,
                "broker_prepared_not_transmitted_orders": prepared_orders_count,
                "historical_orders": len([order for order in orders if not order.get("is_active")]),
                "cancelled_orders": len(
                    [order for order in orders if str(order.get("status") or "") == "CANCELLED"]
                ),
                "filled_orders": len(
                    [order for order in orders if str(order.get("status") or "") == "FILLED"]
                ),
                "daily_pnl": daily_pnl,
                "daily_loss_remaining": (
                    round(max_daily_loss + daily_pnl_for_limits, 2)
                    if daily_pnl is not None
                    else None
                ),
                "positions_pnl": positions_pnl,
                "pnl_until_yesterday": account.get("pnl_until_yesterday"),
                "today_pnl": daily_pnl,
                "today_pnl_broker": account.get("today_pnl_broker"),
                "broker_pnl_source": broker_pnl.get("source", "UNAVAILABLE"),
                "broker_pnl_status": broker_pnl_status or "UNKNOWN",
                "broker_pnl_fresh": broker_pnl_fresh,
                "broker_pnl_age_seconds": broker_pnl.get("age_seconds"),
                "broker_pnl_last_update": broker_pnl.get("last_update"),
                "broker_pnl_reason": broker_pnl.get("reason"),
                "pnl_display_source": pnl_source,
                "remaining_risk": broker_reality.get("remaining_risk"),
                "remaining_risk_status": broker_reality.get("remaining_risk_status"),
                "remaining_risk_reason": broker_reality.get("remaining_risk_reason"),
                "unprotected_positions": broker_reality.get("unprotected_positions", 0),
                "unprotected_orders": broker_reality.get("unprotected_orders", 0),
                "active_stop_orders": broker_reality.get("active_stop_orders", 0),
                "broker_tracker_status": broker_reality.get("broker_tracker_status"),
                "broker_sync_age_seconds": broker_reality.get("broker_sync_age_seconds"),
                "auto_execution_blocked": broker_reality.get("auto_execution_blocked"),
                "account": account,
            },
            "performance": {
                "account": account,
                "stock_pnl": stock_pnl,
            },
            "health": health,
            "broker_reality": broker_reality,
            "setups": setups,
            "positions": positions,
            "orders": orders[:25],
            "executions": await self._broker_executions_snapshot(),
            "events": events[:20],
            "market": [to_jsonable(item) for item in self.market_data.all_latest()],
        }

    def _maybe_record_equity(
        self,
        *,
        net_liquidation: float | None,
        daily_pnl: float | None,
        positions_pnl: float | None,
        open_positions: int,
        source: str,
    ) -> None:
        # Persist a portfolio equity point at most once per interval so the
        # dashboard can draw a real equity curve without flooding the table
        # (snapshot() is also called on every dashboard GET request).
        if net_liquidation is None:
            return
        now = time.monotonic()
        if now - self._last_equity_record_at < EQUITY_SNAPSHOT_INTERVAL_SECONDS:
            return
        try:
            self.repository.record_equity_snapshot(
                net_liquidation=net_liquidation,
                daily_pnl=daily_pnl,
                positions_pnl=positions_pnl,
                open_positions=int(open_positions or 0),
                source=source,
            )
        except Exception:
            logger.exception("Failed to record equity snapshot")
            return
        self._last_equity_record_at = now

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
                self._health["heartbeat_in_progress"] = False

    async def _heartbeat(self, poll_stocks: bool = True) -> None:
        broker_status = await self._broker_health_check()
        broker_check_at = self._utc_now_iso()
        heartbeat_started_at = self._utc_now_iso()
        broker_error = str(getattr(self.broker, "last_error", "") or "")
        self._health.update(
            {
                "last_heartbeat_at": heartbeat_started_at,
                "last_heartbeat_started_at": heartbeat_started_at,
                "last_broker_check_at": broker_check_at,
                "broker_status": broker_status.value,
                "last_broker_error": broker_error,
                "heartbeat_in_progress": True,
            }
        )
        self._drain_broker_audit()
        runtime = self.runtime_state()
        current_status = str(runtime.get("status") or BotStatus.PAUSED.value)
        if (
            broker_status != ConnectionStatus.CONNECTED
            and current_status == BotStatus.RUNNING.value
        ):
            current_status = BotStatus.PAUSED.value
        self.repository.set_bot_state(
            "runtime",
            self._runtime_payload(
                status=current_status,
                connection=broker_status.value,
            ),
        )
        await self._reconcile_if_due(broker_status)
        if poll_stocks:
            await self._poll_active_stock_quotes_with_timeout(current_status, broker_status)
        # Dashboard/monitoring refresh: revalidate pre-entry setups so a stale
        # setup never stays displayed as WAITING_ACTIVATION (throttled).
        self.setup_lifecycle.revalidate_all()
        self._drain_broker_audit()
        broker_diagnostics = self._broker_diagnostics()
        checked_setups = len(
            [
                setup
                for setup in self.repository.list_setups()
                if setup["status"]
                not in {
                    SetupStatus.CLOSED.value,
                    SetupStatus.CANCELLED.value,
                    SetupStatus.EXPIRED.value,
                    SetupStatus.INVALIDATED.value,
                    SetupStatus.ERROR.value,
                    SetupStatus.ERROR_REQUIRES_MANUAL_REVIEW.value,
                }
            ]
        )
        heartbeat_at = self._utc_now_iso()
        self._health.update(
            {
                "last_heartbeat_at": heartbeat_at,
                "last_heartbeat_completed_at": heartbeat_at,
                "heartbeat_in_progress": False,
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
        heartbeat_stale_seconds = self._heartbeat_stale_seconds()
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
        elif heartbeat_age <= heartbeat_stale_seconds:
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
            "heartbeat_stale_seconds": heartbeat_stale_seconds,
            "heartbeat_age_seconds": heartbeat_age,
            "market_tick_age_seconds": self._age_seconds(self._health.get("last_market_tick_at")),
            "market_analysis_age_seconds": self._age_seconds(
                self._health.get("last_market_analysis_at")
            ),
            "stock_poll_age_seconds": self._age_seconds(self._health.get("last_stock_poll_at")),
            "active_setup_count": active_setup_count,
        }

    def _heartbeat_stale_seconds(self) -> int:
        market = self.settings.raw.get("market", {})
        configured = market.get("heartbeat_stale_seconds")
        try:
            seconds = int(float(configured))
        except (TypeError, ValueError):
            seconds = 0
        return max(HEARTBEAT_STALE_SECONDS, seconds)

    async def _reconcile_if_due(self, broker_status: ConnectionStatus) -> None:
        if broker_status != ConnectionStatus.CONNECTED:
            # Reflect the disconnect into the stored broker_reality report right away
            # instead of waiting for the report to age past stale_after_seconds.
            await self._run_reconciliation(mark_completed=False)
            return
        age = self._age_seconds(self._health.get("last_reconciliation_at"))
        if age is not None and age < self._reconciliation_interval_seconds():
            return
        await self._run_reconciliation(mark_completed=True)

    async def _run_reconciliation(self, *, mark_completed: bool) -> None:
        try:
            result = await self.reconciliation.run()
        except Exception as exc:
            message = str(exc)
            self._health["last_reconciliation_error"] = message
            self.event_store.record(
                EventLevel.WARNING,
                "runtime_reconciliation_failed",
                "Runtime broker reconciliation failed",
                data={"error": message},
            )
            return
        if mark_completed:
            self._mark_reconciliation_completed(result)

    def _mark_reconciliation_completed(self, result: Mapping[str, Any]) -> None:
        self._health.update(
            {
                "last_reconciliation_at": self._utc_now_iso(),
                "last_reconciliation_result": result,
                "last_reconciliation_error": "",
            }
        )

    def _reconciliation_interval_seconds(self) -> int:
        tracker = broker_tracker_config(self.settings.raw)
        if tracker["enabled"]:
            return tracker["refresh_seconds"]
        runtime = self.settings.raw.get("runtime", {})
        configured = runtime.get("reconciliation_interval_seconds", 45)
        try:
            seconds = int(float(configured))
        except (TypeError, ValueError):
            seconds = 45
        return max(5, seconds)

    def _broker_reality_snapshot(self) -> dict[str, Any]:
        report = self.repository.get_bot_state(REPORT_STATE_KEY, {})
        if not isinstance(report, dict) or not report.get("broker_last_sync_at"):
            tracker = broker_tracker_config(self.settings.raw)
            safety = execution_safety_config(self.settings.raw)
            status = "DISABLED" if not tracker["enabled"] else "NOT_RUNNING"
            blocked = (
                tracker["enabled"]
                and tracker["block_auto_execution_if_stale"]
                and safety["block_new_entries_if_broker_tracker_stale"]
            )
            return {
                "broker_tracker_status": status,
                "broker_sync_status": status,
                "broker_last_sync_at": None,
                "broker_sync_age_seconds": None,
                "stale_after_seconds": tracker["stale_after_seconds"],
                "refresh_seconds": tracker["refresh_seconds"],
                "broker_connected": False,
                "auto_execution_blocked": blocked,
                "blocking_reasons": ["BROKER_TRACKER_NOT_RUNNING"] if blocked else [],
                "mismatch_count": 0,
                "critical_count": 0,
                "broker_positions_count": None,
                "broker_active_orders": None,
                "broker_prepared_not_transmitted_orders": None,
                "remaining_risk": None,
                "remaining_risk_status": "UNKNOWN_CRITICAL" if blocked else "UNAVAILABLE",
                "remaining_risk_reason": "BROKER_TRACKER_NOT_RUNNING" if blocked else "",
                "unprotected_positions": 0,
                "unprotected_orders": 0,
                "active_stop_orders": 0,
                "rows": [],
                "pnl": {
                    "source": "UNAVAILABLE",
                    "daily_pnl": None,
                    "unrealized_pnl": None,
                    "realized_pnl": None,
                    "status": "STALE",
                    "sync_status": "UNKNOWN",
                    "age_seconds": None,
                    "reason": "NO_RECENT_TWS_PNL_SNAPSHOT",
                },
            }
        return freshen_broker_reality_report(report, settings=self.settings.raw)

    @staticmethod
    def _broker_reality_int(report: dict[str, Any], key: str) -> int | None:
        value = report.get(key) if isinstance(report, dict) else None
        if value is None or value == "":
            return None
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _broker_order_count_by_status(
        orders: list[BrokerOrderRequest],
        statuses: set[str],
    ) -> int:
        return len(
            [
                order
                for order in orders
                if normalize_broker_order_status(
                    order.broker_status or order.raw_status or order.status,
                    transmit=bool(order.transmit),
                )
                in statuses
            ]
        )

    @classmethod
    def _active_broker_order_count(cls, orders: list[BrokerOrderRequest]) -> int:
        return cls._broker_order_count_by_status(
            orders,
            {"PENDING_SUBMIT", "TRANSMITTED", "PARTIALLY_FILLED"},
        )

    async def market_history(
        self,
        symbol: str,
        timeframe: str,
        *,
        duration: str | None = None,
    ) -> dict[str, Any]:
        normalized = self._normalize_chart_timeframe(timeframe)
        config = CHART_TIMEFRAMES[normalized]
        timeout = float(
            self.settings.raw.get("market", {}).get("tws_historical_timeout_seconds")
            or self.settings.raw.get("market", {}).get("tws_stock_quote_timeout_seconds", 4)
            or 4
        )
        result = await self.broker.historical_bars(
            symbol.upper(),
            duration=duration or config["duration"],
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

    async def forecast_market_history(
        self,
        symbol: str,
        timeframe: str,
    ) -> dict[str, Any]:
        """History provider for the forecast stack.

        Decoupled from the chart's fixed window: the forecast needs enough bars
        to satisfy ``forecasting.context_bars`` for the requested bar size, and
        the chart's short duration (e.g. "10 D" for 15m) starves thinly-traded
        or recently-listed symbols below ``min_context_bars``. We size the
        history window from the configured context instead.
        """
        normalized = self._normalize_chart_timeframe(timeframe)
        config = CHART_TIMEFRAMES[normalized]
        forecast_cfg = self.settings.raw.get("forecasting", {})
        if not isinstance(forecast_cfg, dict):
            forecast_cfg = {}
        try:
            context_bars = int(forecast_cfg.get("context_bars", 256) or 256)
        except (TypeError, ValueError):
            context_bars = 256
        # Fetch ~2x the context so gaps, holidays and illiquidity still leave
        # enough usable bars; the model itself caps usage at context_bars.
        desired_bars = max(context_bars * 2, 128)
        duration = self._forecast_history_duration(config["bar_size"], desired_bars)
        return await self.market_history(symbol, normalized, duration=duration)

    @staticmethod
    def _forecast_history_duration(bar_size: str, desired_bars: int) -> str:
        """Build an IB duration string large enough for ``desired_bars``."""

        def ceil_div(numerator: int, denominator: int) -> int:
            return -(-numerator // max(1, denominator))

        bars_per_session = {
            "3 mins": 130,
            "5 mins": 78,
            "10 mins": 39,
            "15 mins": 26,
            "30 mins": 13,
            "1 hour": 7,
            "2 hours": 4,
            "4 hours": 2,
            "1 day": 1,
        }.get(bar_size, 26)
        intraday = "day" not in bar_size and "week" not in bar_size
        if intraday:
            trading_days = ceil_div(desired_bars, bars_per_session)
            # Trading days -> calendar days (weekends) + holiday buffer.
            calendar_days = ceil_div(trading_days * 7, 5) + 3
            calendar_days = max(calendar_days, 5)
            if calendar_days <= 60:
                return f"{calendar_days} D"
            return f"{ceil_div(calendar_days, 30)} M"
        # Daily/weekly bars.
        calendar_days = ceil_div(desired_bars * 7, 5) + 5
        if calendar_days <= 365:
            return f"{calendar_days} D"
        return f"{ceil_div(calendar_days, 365)} Y"

    @staticmethod
    def chart_timeframes() -> list[dict[str, str]]:
        return [{"id": key, "label": value["label"]} for key, value in CHART_TIMEFRAMES.items()]

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
        default_enabled = bool(self.settings.raw.get("broker", {}).get("tws_audit_enabled", True))
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
                "TWS cache" if str(entry.get("detail") or "").lower() == "session cache" else "TWS"
            )
            message = f"{prefix} {request_name} {status}{latency_text}".strip()
            extra = entry.get("extra")
            symbol = None
            if isinstance(extra, dict):
                symbol = extra.get("symbol")
            if (
                request_name in {"reqTickersAsync", "reqMktData", "reqHistoricalDataAsync"}
                and isinstance(extra, dict)
                and symbol
            ):
                quote_text = self._stock_quote_fields_text(extra)
                detail = f": {quote_text}" if quote_text else ""
                message = f"TWS stock data {str(symbol).upper()} {status}" f"{latency_text}{detail}"
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
        return datetime.now(UTC).isoformat()

    @staticmethod
    def _age_seconds(value: Any) -> int | None:
        if not value:
            return None
        try:
            parsed = datetime.fromisoformat(str(value))
        except ValueError:
            return None
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=UTC)
        return max(int((datetime.now(UTC) - parsed).total_seconds()), 0)

    async def _broker_open_orders_snapshot(self) -> list[BrokerOrderRequest]:
        cached = self._broker_open_orders_cache
        cache_age = time.monotonic() - self._broker_open_orders_cached_at
        if cached is not None and cache_age < BROKER_RUNTIME_SNAPSHOT_TTL_SECONDS:
            return list(cached)
        if not await self._broker_is_connected():
            return list(cached or [])
        reader = getattr(self.broker, "open_orders", None)
        if not callable(reader):
            return []
        try:
            orders = await reader()
        except Exception as exc:
            self._health["last_broker_error"] = str(exc)
            return list(cached or [])
        self._broker_open_orders_cache = list(orders or [])
        self._broker_open_orders_cached_at = time.monotonic()
        return list(self._broker_open_orders_cache)

    async def _broker_positions_snapshot(
        self,
        setups: list[dict[str, Any]],
        local_positions: list[dict[str, Any]],
        local_orders: list[dict[str, Any]],
        broker_open_orders: list[BrokerOrderRequest],
    ) -> list[dict[str, Any]]:
        cached = self._broker_positions_cache
        cache_age = time.monotonic() - self._broker_positions_cached_at
        if cached is not None and cache_age < BROKER_RUNTIME_SNAPSHOT_TTL_SECONDS:
            return [dict(position) for position in cached]
        if not await self._broker_is_connected():
            return [dict(position) for position in cached or []]
        reader = getattr(self.broker, "positions", None)
        if not callable(reader):
            return []
        try:
            broker_positions = await reader()
        except Exception as exc:
            self._health["last_broker_error"] = str(exc)
            return [dict(position) for position in cached or []]
        rows = self._broker_position_rows(
            broker_positions or [],
            setups,
            local_positions,
            local_orders,
            broker_open_orders,
        )
        self._broker_positions_cache = rows
        self._broker_positions_cached_at = time.monotonic()
        return [dict(position) for position in rows]

    async def _broker_is_connected(self) -> bool:
        status_reader = getattr(self.broker, "status", None)
        if not callable(status_reader):
            return False
        try:
            return await status_reader() == ConnectionStatus.CONNECTED
        except Exception:
            return False

    async def _broker_executions_snapshot(self) -> list[dict[str, Any]]:
        cached = self._broker_executions_cache
        cache_age = time.monotonic() - self._broker_executions_cached_at
        if cached is not None and cache_age < BROKER_RUNTIME_SNAPSHOT_TTL_SECONDS:
            return [dict(item) for item in cached]
        if not await self._broker_is_connected():
            return [dict(item) for item in cached or []]
        reader = getattr(self.broker, "recent_executions", None)
        if not callable(reader):
            return []
        try:
            executions = await reader()
        except Exception as exc:
            self._health["last_broker_error"] = str(exc)
            return [dict(item) for item in cached or []]
        rows = [
            {
                "execution_id": execution.execution_id,
                "symbol": str(execution.symbol or "").upper(),
                "side": str(execution.side or "").upper(),
                "quantity": execution.quantity,
                "price": execution.price,
                "order_id": execution.order_id,
                "broker_perm_id": execution.broker_perm_id,
                "timestamp": execution.timestamp,
            }
            for execution in executions or []
        ]
        self._broker_executions_cache = rows
        self._broker_executions_cached_at = time.monotonic()
        return [dict(item) for item in rows]

    def _broker_position_rows(
        self,
        broker_positions: list[BrokerPosition],
        setups: list[dict[str, Any]],
        local_positions: list[dict[str, Any]],
        local_orders: list[dict[str, Any]],
        broker_open_orders: list[BrokerOrderRequest],
    ) -> list[dict[str, Any]]:
        setup_by_symbol = self._preferred_setup_by_symbol(setups)
        local_by_symbol = {
            str(position.get("symbol") or "").upper(): position for position in local_positions
        }
        active_stop_by_symbol = self._active_stop_by_symbol(
            local_orders,
            broker_open_orders,
        )
        rows: list[dict[str, Any]] = []
        for broker_position in broker_positions:
            symbol = str(getattr(broker_position, "symbol", "") or "").upper()
            if not symbol:
                continue
            quantity = self._int_value(getattr(broker_position, "quantity", 0)) or 0
            if quantity == 0:
                continue
            average_price = self._float_value(getattr(broker_position, "average_price", None))
            current_price = self._float_value(getattr(broker_position, "current_price", None))
            if average_price is None or current_price is None:
                continue
            broker_unrealized_pnl = self._money(getattr(broker_position, "unrealized_pnl", None))
            if broker_unrealized_pnl is None:
                broker_unrealized_pnl = self._money((current_price - average_price) * quantity)
            local_position = local_by_symbol.get(symbol, {})
            setup = setup_by_symbol.get(symbol, {})
            current_stop = active_stop_by_symbol.get(symbol)
            if current_stop is None:
                current_stop = self._money(local_position.get("current_stop"))
            setup_id = local_position.get("setup_id") or setup.get("setup_id") or f"broker:{symbol}"
            rows.append(
                {
                    "symbol": symbol,
                    "setup_id": setup_id,
                    "quantity": quantity,
                    "average_price": average_price,
                    "current_price": current_price,
                    "unrealized_pnl": broker_unrealized_pnl or 0.0,
                    "realized_pnl": self._money(getattr(broker_position, "realized_pnl", None)),
                    "daily_pnl": self._money(getattr(broker_position, "daily_pnl", None)),
                    "current_stop": current_stop,
                    "risk_remaining": self._position_risk_remaining(
                        quantity,
                        current_price,
                        current_stop,
                    ),
                    "status": "OPEN",
                    "updated_at": self._utc_now_iso(),
                    "source": "broker",
                }
            )
        return rows

    @staticmethod
    def _merge_position_snapshots(
        local_positions: list[dict[str, Any]],
        broker_positions: list[dict[str, Any]],
        broker_connected: bool,
    ) -> list[dict[str, Any]]:
        local_by_symbol: dict[str, dict[str, Any]] = {}
        for position in local_positions:
            symbol = str(position.get("symbol") or "").upper()
            if symbol:
                local_by_symbol[symbol] = dict(position)
        if broker_connected:
            # Broker is the source of truth once connected: a local row whose
            # symbol the broker no longer reports (e.g. closed via TWS without
            # local reconciliation) must not linger in the Positions table.
            by_symbol: dict[str, dict[str, Any]] = {}
            for position in broker_positions:
                symbol = str(position.get("symbol") or "").upper()
                if not symbol:
                    continue
                merged = {**local_by_symbol.get(symbol, {}), **dict(position)}
                if merged.get("current_stop") is None:
                    merged["current_stop"] = local_by_symbol.get(symbol, {}).get("current_stop")
                by_symbol[symbol] = merged
            return [by_symbol[symbol] for symbol in sorted(by_symbol)]
        by_symbol = dict(local_by_symbol)
        for position in broker_positions:
            symbol = str(position.get("symbol") or "").upper()
            if not symbol:
                continue
            merged = {**by_symbol.get(symbol, {}), **dict(position)}
            if merged.get("current_stop") is None:
                merged["current_stop"] = by_symbol.get(symbol, {}).get("current_stop")
            by_symbol[symbol] = merged
        return [by_symbol[symbol] for symbol in sorted(by_symbol)]

    def _orders_with_broker_overlay(
        self,
        local_orders: list[dict[str, Any]],
        broker_orders: list[BrokerOrderRequest],
        setups: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        broker_by_key: dict[str, BrokerOrderRequest] = {}
        for broker_order in broker_orders:
            for key in self._broker_order_keys(broker_order):
                broker_by_key[key] = broker_order

        matched_broker_keys: set[str] = set()
        rows: list[dict[str, Any]] = []
        for order in local_orders:
            row = dict(order)
            matched_broker_order = next(
                (
                    broker_by_key[key]
                    for key in self._local_order_keys(order)
                    if key in broker_by_key
                ),
                None,
            )
            if matched_broker_order is not None:
                row["status"] = self._broker_order_status(matched_broker_order)
                broker_status = self._broker_reality_order_status(matched_broker_order)
                row["broker_order_status"] = broker_status
                row["broker_live_status"] = broker_status
                row["broker_transmit"] = bool(matched_broker_order.transmit)
                if not row.get("broker_perm_id") and matched_broker_order.broker_perm_id:
                    row["broker_perm_id"] = matched_broker_order.broker_perm_id
                matched_broker_keys.update(self._broker_order_keys(matched_broker_order))
            elif str(row.get("status") or "") in {"CREATED", "SUBMITTED"}:
                row["broker_order_status"] = "NO_BROKER_ORDER"
                row["broker_live_status"] = "NO_BROKER_ORDER"
            rows.append(row)

        setup_by_symbol = self._preferred_setup_by_symbol(setups)
        for broker_order in broker_orders:
            broker_keys = self._broker_order_keys(broker_order)
            if broker_keys.intersection(matched_broker_keys):
                continue
            rows.append(self._broker_order_row(broker_order, setup_by_symbol))
        return rows

    def _broker_order_row(
        self,
        broker_order: BrokerOrderRequest,
        setup_by_symbol: dict[str, dict[str, Any]],
    ) -> dict[str, Any]:
        symbol = str(broker_order.symbol or "").upper()
        side = str(broker_order.side or "").upper()
        broker_id = (
            broker_order.client_order_id
            or broker_order.broker_order_id
            or broker_order.broker_perm_id
            or f"{symbol}:{side}"
        )
        localish_id = str(broker_id)
        if not localish_id.startswith(("ord_", "stp_")):
            localish_id = f"broker_{localish_id}"
        return {
            "id": localish_id,
            "setup_id": (setup_by_symbol.get(symbol, {}) or {}).get("setup_id", "broker"),
            "symbol": symbol,
            "side": side,
            "order_type": str(broker_order.order_type or ""),
            "quantity": int(broker_order.quantity or 0),
            "status": self._broker_order_status(broker_order),
            "trigger_price": broker_order.trigger_price,
            "limit_price": broker_order.limit_price,
            "stop_price": broker_order.stop_price if side == "SELL" else None,
            "broker_order_id": broker_order.broker_order_id,
            "broker_perm_id": broker_order.broker_perm_id,
            "parent_id": broker_order.parent_id,
            "oca_group": broker_order.oca_group,
            "created_at": self._utc_now_iso(),
            "updated_at": self._utc_now_iso(),
            "broker_order_status": self._broker_reality_order_status(broker_order),
            "broker_live_status": self._broker_reality_order_status(broker_order),
            "broker_transmit": bool(broker_order.transmit),
        }

    @staticmethod
    def _broker_order_status(order: BrokerOrderRequest) -> str:
        status = str(order.status or "").upper()
        if status in {"CREATED", "SUBMITTED", "FILLED", "CANCELLED", "REJECTED", "ERROR"}:
            return status
        return "SUBMITTED"

    @staticmethod
    def _broker_reality_order_status(order: BrokerOrderRequest) -> str:
        return normalize_broker_order_status(
            order.broker_status or order.raw_status or order.status,
            transmit=bool(order.transmit),
        )

    def _active_stop_by_symbol(
        self,
        local_orders: list[dict[str, Any]],
        broker_orders: list[BrokerOrderRequest],
    ) -> dict[str, float]:
        stops: dict[str, float] = {}
        for order in local_orders:
            if str(order.get("side") or "").upper() != "SELL":
                continue
            if str(order.get("status") or "") not in {"CREATED", "SUBMITTED"}:
                continue
            stop_price = self._money(order.get("stop_price"))
            symbol = str(order.get("symbol") or "").upper()
            if symbol and stop_price is not None:
                stops[symbol] = stop_price
        for broker_order in broker_orders:
            if str(broker_order.side or "").upper() != "SELL":
                continue
            stop_price = self._money(broker_order.stop_price)
            symbol = str(broker_order.symbol or "").upper()
            if symbol and stop_price is not None:
                stops[symbol] = stop_price
        return stops

    @staticmethod
    def _position_risk_remaining(
        quantity: int,
        current_price: float,
        current_stop: float | None,
    ) -> float:
        if current_stop is None:
            return 0.0
        if quantity >= 0:
            return round(max(current_price - current_stop, 0.0) * abs(quantity), 2)
        return round(max(current_stop - current_price, 0.0) * abs(quantity), 2)

    @staticmethod
    def _preferred_setup_by_symbol(
        setups: list[dict[str, Any]],
    ) -> dict[str, dict[str, Any]]:
        preferred: dict[str, dict[str, Any]] = {}
        for setup in setups:
            symbol = str(setup.get("symbol") or "").upper()
            if not symbol:
                continue
            current = preferred.get(symbol)
            if current is None or TradingEngine._setup_preference(
                setup
            ) < TradingEngine._setup_preference(current):
                preferred[symbol] = setup
        return preferred

    @staticmethod
    def _setup_preference(setup: dict[str, Any]) -> int:
        status = str(setup.get("status") or "")
        if status in {
            SetupStatus.IN_POSITION.value,
            SetupStatus.MANAGING_POSITION.value,
            SetupStatus.PARTIAL_EXIT.value,
            SetupStatus.STOP_ORDER_PLACED.value,
            SetupStatus.STOP_PLACED.value,
            SetupStatus.ENTRY_ORDER_PLACED.value,
        }:
            return 0
        if status in {
            SetupStatus.CLOSED.value,
            SetupStatus.CANCELLED.value,
            SetupStatus.EXPIRED.value,
            SetupStatus.INVALIDATED.value,
            SetupStatus.ERROR.value,
            SetupStatus.ERROR_REQUIRES_MANUAL_REVIEW.value,
        }:
            return 2
        return 1

    @staticmethod
    def _local_order_keys(order: dict[str, Any]) -> set[str]:
        return TradingEngine._clean_keys(
            {
                order.get("id"),
                order.get("broker_order_id"),
                order.get("broker_perm_id"),
            }
        )

    @staticmethod
    def _broker_order_keys(order: BrokerOrderRequest) -> set[str]:
        return TradingEngine._clean_keys(
            {
                order.client_order_id,
                order.broker_order_id,
                order.broker_perm_id,
            }
        )

    @staticmethod
    def _clean_keys(values: set[Any]) -> set[str]:
        return {text for value in values for text in [str(value or "").strip()] if text}

    def _stock_pnl(self, positions: list[dict[str, Any]]) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for position in positions:
            symbol = str(position["symbol"]).upper()
            quantity = int(position["quantity"])
            average_price = float(position["average_price"])
            latest = self.market_data.latest(symbol)
            current_price = float(latest.price) if latest else float(position["current_price"])
            broker_unrealized_pnl = self._money(position.get("unrealized_pnl"))
            if str(position.get("source") or "") == "broker" and broker_unrealized_pnl is not None:
                unrealized_pnl = broker_unrealized_pnl
            else:
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
                    "status": (
                        "GAIN" if unrealized_pnl > 0 else "LOSS" if unrealized_pnl < 0 else "FLAT"
                    ),
                    "price_source": "market" if latest else "position",
                }
            )
        return rows

    async def _poll_active_stock_quotes(
        self,
        runtime_status: str,
        broker_status: ConnectionStatus,
    ) -> None:
        await self.stock_market_monitor.poll_active_stock_quotes(
            runtime_status,
            broker_status,
        )

    async def _poll_active_stock_quotes_with_timeout(
        self,
        runtime_status: str,
        broker_status: ConnectionStatus,
    ) -> None:
        timeout = self._stock_poll_total_timeout_seconds()
        try:
            await asyncio.wait_for(
                self._poll_active_stock_quotes(runtime_status, broker_status),
                timeout=timeout,
            )
        except TimeoutError:
            symbols = self._active_market_symbols()
            now = self._utc_now_iso()
            self._health.update(
                {
                    "last_stock_poll_at": now,
                    "last_stock_poll_symbols": symbols,
                    "last_stock_poll_count": len(symbols),
                    "last_stock_poll_ok": 0,
                    "last_stock_poll_errors": len(symbols),
                    "last_stock_poll_reason": "timeout",
                    "last_stock_poll_timeout_seconds": timeout,
                    "last_stock_poll_latency_ms": int(timeout * 1000),
                    "last_stock_analysis_count": 0,
                }
            )
            self.event_store.record(
                EventLevel.WARNING,
                "stock_poll_timeout",
                f"TWS stock poll timed out after {timeout:g}s",
                data={
                    "timeout_seconds": timeout,
                    "symbols": symbols,
                    "runtime_status": runtime_status,
                    "broker_status": broker_status.value,
                },
            )
            self._drain_broker_audit()

    def _stock_poll_total_timeout_seconds(self) -> float:
        market = self.settings.raw.get("market", {})
        configured = market.get("tws_stock_poll_total_timeout_seconds")
        try:
            seconds = float(configured)
        except (TypeError, ValueError):
            seconds = 0
        if seconds > 0:
            return max(0.1, seconds)
        stale_after = self._heartbeat_stale_seconds()
        return float(max(5, min(120, stale_after - HEARTBEAT_INTERVAL_SECONDS)))

    def _active_market_symbols(self) -> list[str]:
        return active_market_symbols(self.repository.list_setups())

    @staticmethod
    def _stock_quote_message(symbol: str, quote: dict[str, Any]) -> str:
        return stock_quote_message(symbol, quote)

    @staticmethod
    def _stock_quote_fields_text(quote: dict[str, Any]) -> str:
        return stock_quote_fields_text(quote)

    @staticmethod
    def _float_value(value: Any) -> float | None:
        return float_value(value)

    @staticmethod
    def _int_value(value: Any) -> int | None:
        return int_value(value)

    async def _account_snapshot(self, positions_pnl: float) -> dict[str, Any]:
        cached = self._account_summary_cache
        cache_age = time.monotonic() - self._account_summary_cached_at
        if cached is None or cache_age >= ACCOUNT_SNAPSHOT_TTL_SECONDS:
            cached = await self._fetch_broker_account_snapshot()
            self._account_summary_cache = cached
            self._account_summary_cached_at = time.monotonic()
        account = dict(cached)
        broker_today_pnl = self._money(account.get("today_pnl"))
        account["today_pnl_broker"] = broker_today_pnl
        account["positions_unrealized_pnl"] = self._money(positions_pnl)
        if account["unrealized_pnl"] is None:
            account["unrealized_pnl"] = account["positions_unrealized_pnl"]
        account["today_pnl_live_estimate"] = self._live_today_pnl_estimate(account)
        self._apply_account_history(account)
        if account.get("today_pnl") is None:
            account["today_pnl"] = account.get("unrealized_pnl")
        if account.get("pnl_until_yesterday") is None:
            account["pnl_until_yesterday"] = account.get("realized_pnl")
        return account

    async def _fetch_broker_account_snapshot(self) -> dict[str, Any]:
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
            "today_pnl": self._money(account.get("today_pnl")),
            "message": account.get("message", ""),
        }
        return account

    @staticmethod
    def _live_today_pnl_estimate(account: dict[str, Any]) -> float | None:
        realized = account.get("realized_pnl")
        positions_unrealized = account.get("positions_unrealized_pnl")
        broker_today = account.get("today_pnl_broker")
        if realized is None and broker_today is not None:
            return TradingEngine._money(broker_today)
        if realized is None and positions_unrealized is None:
            return None
        return TradingEngine._money((realized or 0.0) + (positions_unrealized or 0.0))

    def _enrich_orders_with_event_diagnostics(
        self,
        orders: list[dict[str, Any]],
        events: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        latest_event_by_order_id: dict[str, str] = {}
        for event in events:
            message = self._order_diagnostic_message_from_event(event)
            if not message:
                continue
            data = event.get("data", {})
            if not isinstance(data, dict):
                continue
            for field in ("order_id", "entry_order_id", "stop_order_id"):
                order_id = str(data.get(field) or "").strip()
                if order_id and order_id not in latest_event_by_order_id:
                    latest_event_by_order_id[order_id] = message
        enriched: list[dict[str, Any]] = []
        for order in orders:
            row = dict(order)
            status = str(row.get("status") or "")
            broker_status = str(row.get("broker_order_status") or "")
            if broker_status:
                row["is_active"] = broker_status in {
                    "PENDING_SUBMIT",
                    "TRANSMITTED",
                    "PARTIALLY_FILLED",
                }
            else:
                row["is_active"] = status in {"CREATED", "SUBMITTED"}
            row["lifecycle_bucket"] = "ACTIVE" if row["is_active"] else "HISTORY"
            row["diagnostic_message"] = latest_event_by_order_id.get(
                str(row.get("id") or "")
            ) or self._default_order_diagnostic_message(row)
            enriched.append(row)
        return enriched

    @staticmethod
    def _order_diagnostic_message_from_event(event: dict[str, Any]) -> str:
        event_type = str(event.get("event_type") or "")
        message = str(event.get("message") or "").strip()
        data = event.get("data", {})
        if not isinstance(data, dict):
            data = {}
        failure_reason = str(data.get("failure_reason") or "").strip()
        if event_type == "entry_order_unprotected_blocked" and failure_reason:
            return failure_reason
        if event_type in {
            "entry_order_rejected",
            "entry_order_unprotected_blocked",
            "protective_stop_rejected",
            "order_cancelled",
            "order_cancel_reconciled",
        }:
            if failure_reason and failure_reason.lower() != message.lower():
                return f"{message} | {failure_reason}" if message else failure_reason
            return message
        return ""

    @staticmethod
    def _default_order_diagnostic_message(order: dict[str, Any]) -> str:
        status = str(order.get("status") or "")
        broker_status = str(order.get("broker_order_status") or "")
        side = str(order.get("side") or "").upper()
        stop_order_id = str(order.get("stop_order_id") or "").strip()
        if broker_status in {"PENDING_SUBMIT", "TRANSMITTED", "PARTIALLY_FILLED"}:
            return f"Broker confirms working order: {broker_status}."
        if broker_status == "PREPARED_NOT_TRANSMITTED":
            return "Prepared in TWS but not transmitted. Do not treat as active."
        if broker_status == "NO_BROKER_ORDER":
            return "Local order intent exists, but TWS does not confirm a working order."
        if broker_status in {"REJECTED", "INACTIVE_OR_REJECTED"}:
            return "Broker rejected or inactivated this order."
        if status in {"CREATED", "SUBMITTED"}:
            return "Local active intent; broker confirmation unavailable."
        if status == "CANCELLED" and side == "BUY" and not stop_order_id:
            return "Historical order: cancelled before protective stop activation."
        if status == "CANCELLED":
            return "Historical order: cancelled."
        if status == "FILLED":
            return "Historical order: filled."
        if status == "REJECTED":
            return "Historical order: rejected."
        if status == "ERROR":
            return "Historical order: error."
        return "Historical order."

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
            state["daily_start"] = {key: daily_start[key] for key in sorted(daily_start)[-45:]}
            self.repository.set_bot_state("account_history", state)
        start_today = self._money(daily_start.get(today))
        account["daily_start_equity"] = start_today
        account["initial_equity"] = initial_equity
        if start_today is not None:
            account["equity_change_today"] = self._money(net_liquidation - start_today)
        if start_today is not None and initial_equity is not None:
            account["equity_change_until_yesterday"] = self._money(start_today - initial_equity)

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
        self.invalidate_snapshot_cache()
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
        self.invalidate_snapshot_cache()
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
        self.invalidate_snapshot_cache()
        broker_status = await self._broker_health_check()
        runtime = self._runtime_payload(
            status=BotStatus.PAUSED.value,
            connection=broker_status.value,
        )
        self.repository.set_bot_state("runtime", runtime)
        self.event_store.record(EventLevel.WARNING, "engine_paused", "Trading engine paused")
        await self._broadcast_snapshot()
        return await self.snapshot()

    async def force_sync(self) -> ReconciliationResult:
        self.invalidate_snapshot_cache()
        result = await self.reconciliation.run()
        self._mark_reconciliation_completed(result)
        await self._heartbeat(poll_stocks=False)
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
        self.invalidate_snapshot_cache()
        broker_config = self.settings.raw["broker"]
        connector = self._normalize_user_connector(connector)
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
        self.settings.raw["app"]["mode"] = connector
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
        self.invalidate_snapshot_cache()
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
        connector = str(
            getattr(
                self.broker,
                "connector_name",
                broker_config.get("connector", "paper"),
            )
        )
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
        self.invalidate_snapshot_cache()
        setup = self.repository.get_setup(setup_id)
        if setup is None:
            raise KeyError(setup_id)
        self.repository.set_setup_enabled(setup_id, enabled)
        self.event_store.record(
            EventLevel.INFO,
            "setup_enabled_changed",
            (
                "Setup automatic TWS execution enabled"
                if enabled
                else "Setup automatic TWS execution disabled; monitoring continues"
            ),
            setup_id=setup_id,
            symbol=setup["symbol"],
            data={"enabled": enabled},
        )
        await self._broadcast_snapshot()
        return self.repository.get_setup(setup_id) or {}

    async def set_all_setups_enabled(self, enabled: bool) -> dict[str, Any]:
        self.invalidate_snapshot_cache()
        setups = self.repository.list_setups()
        for setup in setups:
            self.repository.set_setup_enabled(str(setup["setup_id"]), enabled)
        self.event_store.record(
            EventLevel.INFO,
            "all_setups_enabled_changed",
            (
                "Automatic TWS execution enabled for all setups"
                if enabled
                else "Automatic TWS execution disabled for all setups; monitoring continues"
            ),
            data={
                "enabled": enabled,
                "updated_count": len(setups),
                "setup_ids": [setup["setup_id"] for setup in setups],
                "symbols": [setup["symbol"] for setup in setups],
            },
        )
        await self._broadcast_snapshot()
        return {
            "ok": True,
            "enabled": enabled,
            "updated_count": len(setups),
            "setups": self.repository.list_setups(),
        }

    def setup_config_template(self, template_type: str = "universal") -> dict[str, Any]:
        return self.setup_template_service.setup_config_template()

    def configuration_status(self) -> dict[str, Any]:
        return self.setup_status_reporter.configuration_status()

    def setup_arm_status(self, setup_id: str) -> dict[str, Any]:
        setup = self.repository.get_setup(setup_id)
        if setup is None:
            raise KeyError(setup_id)
        config = setup.get("config", {})
        if not isinstance(config, dict):
            config = {}
        arm_validation = self.setup_engine.validate_for_arm(config)
        lifecycle = self.setup_lifecycle.revalidate(setup)
        lifecycle_armable = bool(lifecycle.get("can_be_armed", True))
        disarm_errors = self._disarm_blockers(setup_id)
        already_disarmed = str(setup.get("status") or "") == SetupStatus.DISABLED.value
        disarm_warnings = ["setup is already DISABLED"] if already_disarmed else []
        return {
            "ok": True,
            "setup_id": setup_id,
            "symbol": setup.get("symbol"),
            "status": setup.get("status"),
            "status_reason": lifecycle.get("status_reason"),
            "last_revalidated_at": lifecycle.get("last_revalidated_at"),
            "lifecycle": lifecycle,
            "enabled": bool(setup.get("enabled")),
            "armable": arm_validation.valid and lifecycle_armable,
            "target_status": (
                arm_validation.details.get("arm_validation", {}).get("initial_status")
                if arm_validation.valid
                else None
            ),
            "arm_validation": {
                "allowed": arm_validation.valid and lifecycle_armable,
                "errors": [
                    *arm_validation.errors,
                    *(
                        []
                        if lifecycle_armable
                        else [
                            "Lifecycle revalidation blocks arming: "
                            f"{lifecycle.get('status')} ({lifecycle.get('status_reason')})"
                        ]
                    ),
                ],
                "warnings": arm_validation.warnings,
                "details": {**arm_validation.details, "lifecycle": lifecycle},
            },
            "disarmable": not disarm_errors and not already_disarmed,
            "disarm_validation": {
                "allowed": not disarm_errors and not already_disarmed,
                "errors": disarm_errors,
                "warnings": disarm_warnings,
            },
        }

    async def delete_setup(self, setup_id: str) -> dict[str, Any]:
        self.invalidate_snapshot_cache()
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
        self.invalidate_snapshot_cache()
        canonical = self.setup_engine.canonicalize_config(config)
        validation = self.setup_engine.create_or_update_from_config(config)
        if not validation.valid:
            return {
                "ok": False,
                "errors": validation.errors,
                "warnings": validation.warnings,
                "details": validation.details,
            }
        await self._broadcast_snapshot()
        return {
            "ok": True,
            "setup": self.repository.get_setup(str(canonical.config["setup_id"])),
            "warnings": validation.warnings,
            "details": validation.details,
        }

    async def arm_setup(self, setup_id: str) -> dict[str, Any]:
        self.invalidate_snapshot_cache()
        setup = self.repository.get_setup(setup_id)
        if setup is None:
            raise KeyError(setup_id)
        lifecycle = self.setup_lifecycle.revalidate(setup)
        if not lifecycle.get("can_be_armed", True):
            errors = [
                "Setup cannot be armed: revalidation status "
                f"{lifecycle.get('status')} ({lifecycle.get('status_reason')})",
                *[str(reason) for reason in lifecycle.get("blocking_reasons", [])],
            ]
            self.event_store.record(
                EventLevel.WARNING,
                "setup_arm_blocked_by_lifecycle",
                "; ".join(errors),
                setup_id=setup_id,
                symbol=str(setup.get("symbol", "")).upper() or None,
                data={"lifecycle": lifecycle},
            )
            return {
                "ok": False,
                "errors": errors,
                "warnings": list(lifecycle.get("warnings", [])),
                "details": {"lifecycle": lifecycle},
            }
        validation = self.setup_engine.arm_setup(setup_id)
        if not validation.valid:
            return {
                "ok": False,
                "errors": validation.errors,
                "warnings": validation.warnings,
                "details": validation.details,
            }
        await self._broadcast_snapshot()
        return {
            "ok": True,
            "setup": self.repository.get_setup(setup_id),
            "warnings": validation.warnings,
            "details": validation.details,
        }

    async def disarm_setup(self, setup_id: str) -> dict[str, Any]:
        self.invalidate_snapshot_cache()
        setup = self.repository.get_setup(setup_id)
        if setup is None:
            raise KeyError(setup_id)
        blockers = self._disarm_blockers(setup_id)
        if blockers:
            self.event_store.record(
                EventLevel.WARNING,
                "setup_disarm_blocked",
                "; ".join(blockers),
                setup_id=setup_id,
                symbol=str(setup.get("symbol", "")).upper() or None,
                data={"errors": blockers},
            )
            raise ValueError("; ".join(blockers))
        self.setup_engine.disarm_setup(setup_id)
        await self._broadcast_snapshot()
        return {
            "ok": True,
            "setup": self.repository.get_setup(setup_id),
        }

    def _disarm_blockers(self, setup_id: str) -> list[str]:
        errors: list[str] = []
        active_orders = self.repository.active_orders_for_setup(setup_id)
        if active_orders:
            order_ids = ", ".join(str(order.get("id") or "") for order in active_orders)
            errors.append(f"Cannot disarm setup with active orders: {order_ids}".rstrip(": "))
        open_position = next(
            (
                position
                for position in self.repository.list_positions()
                if position.get("setup_id") == setup_id and int(position.get("quantity") or 0) != 0
            ),
            None,
        )
        if open_position:
            errors.append("Cannot disarm setup with an open position")
        return errors

    async def process_market_snapshot(self, snapshot: MarketSnapshot) -> dict[str, Any]:
        self.invalidate_snapshot_cache()
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
        return await self.stock_market_monitor.analyze_market_snapshot(snapshot)

    def _record_stock_analysis(
        self,
        snapshot: MarketSnapshot,
        processed: list[dict[str, Any]],
    ) -> None:
        self.stock_market_monitor.record_stock_analysis(snapshot, processed)

    def _record_stock_analysis_skipped(
        self,
        snapshot: MarketSnapshot,
        reason: str,
    ) -> None:
        self.stock_market_monitor.record_stock_analysis_skipped(snapshot, reason)

    @staticmethod
    def _stock_analysis_summary(processed: list[dict[str, Any]]) -> str:
        return stock_analysis_summary(processed)

    def _should_suppress_repeated_event(
        self,
        event_type: str,
        symbol: str,
        signature: str,
    ) -> bool:
        return self.stock_market_monitor.should_suppress_repeated_event(
            event_type,
            symbol,
            signature,
        )

    @staticmethod
    def _stock_analysis_dedupe_key(
        symbol: str,
        processed: list[dict[str, Any]],
    ) -> str:
        return stock_analysis_dedupe_key(symbol, processed)

    @staticmethod
    def _market_snapshot_payload(snapshot: MarketSnapshot) -> dict[str, Any]:
        return market_snapshot_payload(snapshot)

    def _record_market_tick(self, snapshot: MarketSnapshot) -> None:
        self.stock_market_monitor.record_market_tick(snapshot)

    async def _handle_signal(
        self, setup: dict[str, Any], current_status: SetupStatus, signal: Any
    ) -> None:
        if self.action_executor.execute_simple_action(setup, current_status, signal):
            return
        if self.position_action_executor.execute_raise_stop_signal(setup, current_status, signal):
            return
        await self.entry_order_executor.execute_entry_ready(setup, signal)

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
        result = await self.stop_modification_service.modify_stop(symbol, new_stop)
        await self._broadcast_snapshot()
        return result

    async def _broadcast_snapshot(self) -> None:
        await self.broadcaster.broadcast({"type": "snapshot", "payload": await self.snapshot()})
