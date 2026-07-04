from __future__ import annotations

from collections.abc import Callable
from typing import Any

from app.observability.decision_trace_repository import DecisionTraceRepository
from app.observability.decision_trace_service import DecisionTraceService
from app.storage.repositories import TradingRepository

SnapshotProvider = Callable[[], Any]


class ObservabilityService:
    def __init__(
        self,
        repository: TradingRepository,
        snapshot_provider: SnapshotProvider | None = None,
    ) -> None:
        self.repository = repository
        self.snapshot_provider = snapshot_provider
        self.decision_trace_service = DecisionTraceService(DecisionTraceRepository(repository))

    async def health(self) -> dict[str, Any]:
        snapshot = await self._snapshot()
        health = snapshot.get("health", {}) if isinstance(snapshot, dict) else {}
        return {
            "app": "Setup Order",
            "status": health.get("status", "ok"),
            "label": health.get("label", "ok"),
            "broker_status": health.get("broker_status"),
            "last_error": health.get("last_error", ""),
            "heartbeat_age_seconds": health.get("heartbeat_age_seconds"),
        }

    async def metrics(self) -> dict[str, Any]:
        snapshot = await self._snapshot()
        metrics = snapshot.get("metrics", {}) if isinstance(snapshot, dict) else {}
        health = snapshot.get("health", {}) if isinstance(snapshot, dict) else {}
        return {
            **metrics,
            "tws_connection_status": health.get("broker_status"),
            "tws_reconnect_count": health.get("tws_reconnect_count", 0),
            "last_tick_age_seconds": health.get("market_tick_age_seconds"),
            "last_candle_close_age_seconds": health.get("market_analysis_age_seconds"),
            "setup_evaluations_per_minute": metrics.get("setup_evaluations_per_minute", 0),
            "scanner_runs_per_minute": metrics.get("scanner_runs_per_minute", 0),
            "forecast_runs_per_hour": metrics.get("forecast_runs_per_hour", 0),
            "blocked_entries_count": len(
                self.repository.list_events(event_type="entry_signal_blocked", limit=1000)
            ),
            "manual_review_count": len(
                [
                    setup
                    for setup in self.repository.list_setups()
                    if setup.get("status")
                    in {"MANUAL_REVIEW_REQUIRED", "ERROR_REQUIRES_MANUAL_REVIEW"}
                ]
            ),
        }

    async def system_status(self) -> dict[str, Any]:
        snapshot = await self._snapshot()
        return {
            "runtime": snapshot.get("runtime", {}) if isinstance(snapshot, dict) else {},
            "health": snapshot.get("health", {}) if isinstance(snapshot, dict) else {},
            "metrics": await self.metrics(),
            "open_positions": len(self.repository.list_positions()),
            "open_orders": len(
                [
                    order
                    for order in self.repository.list_orders()
                    if order.get("status") in {"CREATED", "SUBMITTED"}
                ]
            ),
        }

    def decision_trace(self, trace_id: str) -> dict[str, Any] | None:
        return self.decision_trace_service.get(trace_id)

    def decision_traces(
        self,
        *,
        setup_id: str | None = None,
        symbol: str | None = None,
        scenario_id: str | None = None,
        opportunity_id: str | None = None,
        decision_type: str | None = None,
        final_decision: str | None = None,
        date: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        return self.decision_trace_service.list(
            setup_id=setup_id,
            symbol=symbol,
            scenario_id=scenario_id,
            opportunity_id=opportunity_id,
            decision_type=decision_type,
            final_decision=final_decision,
            date=date,
            limit=limit,
        )

    def decision_traces_for_entity(
        self,
        entity_type: str,
        entity_id: str,
        *,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        return self.decision_trace_service.list_for_entity(
            entity_type,
            entity_id,
            limit=limit,
        )

    async def _snapshot(self) -> dict[str, Any]:
        if self.snapshot_provider is None:
            return {}
        result = self.snapshot_provider()
        if hasattr(result, "__await__"):
            result = await result
        return result if isinstance(result, dict) else {}
