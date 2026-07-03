from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable

from app.engine.setup_diagnostics import (
    build_setup_analysis_trace,
    market_snapshot_payload,
)
from app.engine.signal_engine import SignalEngine
from app.engine.opportunity_alert_service import OpportunityAlertService
from app.market_data.market_data_service import MarketDataService
from app.models import BotStatus, ConnectionStatus, EventLevel, MarketSnapshot, SetupStatus
from app.settings import Settings
from app.storage.event_store import EventStore
from app.storage.repositories import TradingRepository


logger = logging.getLogger(__name__)

SignalHandler = Callable[[dict[str, Any], SetupStatus, Any], Awaitable[None]]


class StockMarketMonitor:
    """Polls stock quotes, evaluates setups, and records market analysis events."""

    def __init__(
        self,
        settings: Settings,
        repository: TradingRepository,
        event_store: EventStore,
        market_data: MarketDataService,
        signal_engine: SignalEngine,
        signal_handler: SignalHandler,
        broker_provider: Callable[[], Any],
        health: dict[str, Any],
        audit_drain: Callable[[], None],
        now_provider: Callable[[], str] | None = None,
        opportunity_alert_service: OpportunityAlertService | None = None,
        data_quality_service: Any | None = None,
        feature_store: Any | None = None,
    ) -> None:
        self.settings = settings
        self.repository = repository
        self.event_store = event_store
        self.market_data = market_data
        self.signal_engine = signal_engine
        self.signal_handler = signal_handler
        self.broker_provider = broker_provider
        self.health = health
        self.audit_drain = audit_drain
        self.now_provider = now_provider or utc_now_iso
        self.opportunity_alert_service = opportunity_alert_service
        self.data_quality_service = data_quality_service
        self.feature_store = feature_store
        self._event_dedupe: dict[str, tuple[str, float]] = {}

    async def poll_active_stock_quotes(
        self,
        runtime_status: str,
        broker_status: ConnectionStatus,
    ) -> None:
        market_config = self.settings.raw.get("market", {})
        enabled = bool(market_config.get("tws_stock_poll_enabled", True))
        interval = int(market_config.get("tws_stock_poll_interval_seconds", 15) or 15)
        timeout = float(market_config.get("tws_stock_quote_timeout_seconds", 4) or 4)
        configured_concurrency = int(
            market_config.get("tws_stock_poll_max_concurrency", 5) or 5
        )
        if not enabled:
            self.health["last_stock_poll_reason"] = "disabled"
            return
        if broker_status != ConnectionStatus.CONNECTED:
            self.health["last_stock_poll_reason"] = "broker_not_connected"
            return
        should_analyze = runtime_status == BotStatus.RUNNING.value
        last_poll_age = age_seconds(self.health.get("last_stock_poll_at"))
        if last_poll_age is not None and last_poll_age < interval:
            return

        symbols = active_market_symbols(self.repository.list_setups())
        now = self.now_provider()
        if not symbols:
            logger.warning("TWS stock poll skipped: no monitored setup symbols")
            self.health.update(
                {
                    "last_stock_poll_at": now,
                    "last_stock_poll_symbols": [],
                    "last_stock_poll_count": 0,
                    "last_stock_poll_ok": 0,
                    "last_stock_poll_errors": 0,
                    "last_stock_poll_reason": "no_monitored_setups",
                    "last_stock_analysis_count": 0,
                }
            )
            self.event_store.record(
                EventLevel.WARNING,
                "stock_poll_skipped",
                "No monitored setup symbols to poll",
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
        broker = self.broker_provider()
        max_concurrency = bounded_concurrency(configured_concurrency, len(symbols))
        semaphore = asyncio.Semaphore(max_concurrency)
        cycle_started = time.perf_counter()

        async def poll_one(symbol: str) -> dict[str, Any]:
            async with semaphore:
                return await self.poll_stock_symbol(
                    symbol=symbol,
                    broker=broker,
                    timeout=timeout,
                    should_analyze=should_analyze,
                    runtime_status=runtime_status,
                )

        results = await asyncio.gather(*(poll_one(symbol) for symbol in symbols))
        cycle_latency_ms = elapsed_ms(cycle_started)
        ok_count = sum(1 for result in results if result.get("quote_ok"))
        error_count = len(results) - ok_count
        analysis_count = sum(int(result.get("analysis_count") or 0) for result in results)

        self.health.update(
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
                "last_stock_poll_latency_ms": cycle_latency_ms,
                "last_stock_poll_max_concurrency": max_concurrency,
                "last_stock_poll_symbol_timings": [
                    result.get("timing", {}) for result in results
                ],
            }
        )
        self.record_stock_poll_timing(
            symbols=symbols,
            results=results,
            cycle_latency_ms=cycle_latency_ms,
            max_concurrency=max_concurrency,
            should_analyze=should_analyze,
        )
        logger.info(
            (
                "TWS stock poll finished: %d symbols, %d quotes OK, "
                "%d errors, %d analyses in %.1f ms"
            ),
            len(symbols),
            ok_count,
            error_count,
            analysis_count,
            cycle_latency_ms,
        )

    async def poll_stock_symbol(
        self,
        symbol: str,
        broker: Any,
        timeout: float,
        should_analyze: bool,
        runtime_status: str,
    ) -> dict[str, Any]:
        symbol_started = time.perf_counter()
        timing: dict[str, Any] = {
            "symbol": symbol.upper(),
            "quote_timeout_seconds": timeout,
        }
        result: dict[str, Any] = {
            "symbol": symbol.upper(),
            "quote_ok": False,
            "analysis_count": 0,
            "timing": timing,
        }
        try:
            quote_started = time.perf_counter()
            quote = await broker.market_snapshot(symbol, timeout=timeout)
            timing["quote_latency_ms"] = elapsed_ms(quote_started)
        except Exception as exc:
            timing["quote_latency_ms"] = elapsed_ms(symbol_started)
            timing["total_symbol_latency_ms"] = elapsed_ms(symbol_started)
            message = f"TWS stock quote missing {symbol}: {exc}"
            logger.warning(message)
            self.event_store.record(
                EventLevel.WARNING,
                "stock_quote_missing",
                message,
                symbol=symbol,
                data={
                    "available": False,
                    "symbol": symbol.upper(),
                    "message": str(exc),
                    "timing": timing,
                },
            )
            self.audit_drain()
            return result

        quote_data = quote if isinstance(quote, dict) else {}
        if quote_data.get("available"):
            snapshot = quote_to_market_snapshot(symbol, quote_data)
            if snapshot is None:
                timing["total_symbol_latency_ms"] = elapsed_ms(symbol_started)
                result["error"] = "missing_price"
                message = f"TWS stock quote missing {symbol}: missing usable price"
                logger.warning(message)
                self.event_store.record(
                    EventLevel.WARNING,
                    "stock_quote_missing",
                    message,
                    symbol=symbol,
                    data={**quote_data, "timing": timing},
                )
                self.audit_drain()
                return result

            result["quote_ok"] = True
            message = stock_quote_message(symbol, {**quote_data, **timing})
            logger.info(message)
            self.event_store.record(
                EventLevel.INFO,
                "stock_quote",
                message,
                symbol=symbol,
                data={**quote_data, "timing": timing},
            )
            if should_analyze:
                processed = await self.analyze_market_snapshot(snapshot, timing=timing)
                result["analysis_count"] = len(processed)
            else:
                self.record_market_tick(snapshot)
                self.record_stock_analysis_skipped(
                    snapshot,
                    f"bot status {runtime_status}",
                    timing=timing,
                )
        else:
            message = (
                quote_data.get("message")
                or f"TWS did not return a usable quote for {symbol}"
            )
            logger.warning(
                "TWS stock quote missing %s: %s %s",
                symbol,
                message,
                stock_quote_fields_text(quote_data),
            )
            self.event_store.record(
                EventLevel.WARNING,
                "stock_quote_missing",
                message,
                symbol=symbol,
                data={**quote_data, "timing": timing},
            )

        timing["total_symbol_latency_ms"] = elapsed_ms(symbol_started)
        self.audit_drain()
        return result

    async def analyze_market_snapshot(
        self,
        snapshot: MarketSnapshot,
        timing: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        timing = timing if timing is not None else {}
        analysis_started = time.perf_counter()
        self.record_market_tick(snapshot)
        evaluation_started = time.perf_counter()
        evaluations = self.signal_engine.evaluate_snapshot(
            snapshot,
            build_setup_analysis_trace,
        )
        timing["evaluation_latency_ms"] = elapsed_ms(evaluation_started)
        processed = [evaluation.processed for evaluation in evaluations]
        if self.opportunity_alert_service is not None:
            self.opportunity_alert_service.enrich_processed_items(processed)
        signal_started = time.perf_counter()
        for evaluation in evaluations:
            await self.signal_handler(
                evaluation.setup,
                evaluation.current_status,
                evaluation.signal,
            )
        timing["signal_handling_latency_ms"] = elapsed_ms(signal_started)
        if processed:
            if self.opportunity_alert_service is not None:
                self.opportunity_alert_service.record_alerts(snapshot, processed)
            timing["analysis_latency_ms"] = elapsed_ms(analysis_started)
            self.record_stock_analysis(snapshot, processed, timing=timing)
        else:
            timing["analysis_latency_ms"] = elapsed_ms(analysis_started)
            self.record_stock_analysis_skipped(
                snapshot,
                "no active setup for symbol",
                timing=timing,
            )
        self.health.update(
            {
                "last_market_analysis_at": self.now_provider(),
                "last_processed_setups": len(processed),
            }
        )
        return processed

    def record_stock_analysis(
        self,
        snapshot: MarketSnapshot,
        processed: list[dict[str, Any]],
        timing: dict[str, Any] | None = None,
    ) -> None:
        symbol = snapshot.symbol.upper()
        summary = stock_analysis_summary(processed)
        message = f"Stock analysis {symbol}: {len(processed)} setup(s) evaluated"
        if summary:
            message = f"{message} ({summary})"
        dedupe_key = stock_analysis_dedupe_key(symbol, processed)
        if self.should_suppress_repeated_event("stock_analysis", symbol, dedupe_key):
            logger.debug("Stock analysis %s repeated without state change", symbol)
            return
        logger.info(message)
        self.event_store.record(
            EventLevel.INFO,
            "stock_analysis",
            message,
            symbol=symbol,
            data={
                "snapshot": market_snapshot_payload(snapshot),
                "processed": processed,
                "timing": timing or {},
            },
        )

    def record_stock_analysis_skipped(
        self,
        snapshot: MarketSnapshot,
        reason: str,
        timing: dict[str, Any] | None = None,
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
                "snapshot": market_snapshot_payload(snapshot),
                "timing": timing or {},
            },
        )

    def record_stock_poll_timing(
        self,
        symbols: list[str],
        results: list[dict[str, Any]],
        cycle_latency_ms: float,
        max_concurrency: int,
        should_analyze: bool,
    ) -> None:
        timings = [
            result.get("timing", {})
            for result in results
            if isinstance(result.get("timing"), dict)
        ]
        quote_latencies = numeric_values(timings, "quote_latency_ms")
        analysis_latencies = numeric_values(timings, "analysis_latency_ms")
        total_latencies = numeric_values(timings, "total_symbol_latency_ms")
        message = (
            f"Stock poll timing: {len(symbols)} symbol(s) in {cycle_latency_ms:.1f} ms "
            f"concurrency={max_concurrency}"
        )
        quote_avg = average(quote_latencies)
        analysis_avg = average(analysis_latencies)
        if quote_avg is not None:
            message = f"{message} quote_avg={quote_avg:.1f} ms"
        if analysis_avg is not None:
            message = f"{message} analysis_avg={analysis_avg:.1f} ms"
        self.event_store.record(
            EventLevel.INFO,
            "stock_poll_timing",
            message,
            data={
                "symbols": symbols,
                "should_analyze": should_analyze,
                "max_concurrency": max_concurrency,
                "cycle_latency_ms": cycle_latency_ms,
                "quote_latency_ms": summary_stats(quote_latencies),
                "analysis_latency_ms": summary_stats(analysis_latencies),
                "total_symbol_latency_ms": summary_stats(total_latencies),
                "timings": timings,
            },
        )

    def should_suppress_repeated_event(
        self,
        event_type: str,
        symbol: str,
        signature: str,
    ) -> bool:
        config = self.settings.raw.get("market", {}).get("event_deduplication", {})
        if not isinstance(config, dict) or not bool(config.get("enabled", True)):
            return False
        cooldown = float(config.get("repeated_hold_cooldown_seconds", 300) or 300)
        key = f"{event_type}:{symbol.upper()}"
        now = time.monotonic()
        previous = self._event_dedupe.get(key)
        if previous is not None and previous[0] == signature:
            if now - previous[1] < cooldown:
                return True
            self._event_dedupe[key] = (signature, now)
            return False

        for event in self.repository.list_events(symbol=symbol.upper(), limit=20):
            if event.get("event_type") != event_type:
                continue
            data = event.get("data") if isinstance(event.get("data"), dict) else {}
            processed = data.get("processed")
            if not isinstance(processed, list):
                continue
            previous_signature = stock_analysis_dedupe_key(symbol, processed)
            if previous_signature != signature:
                break
            age = age_seconds(event.get("timestamp"))
            if age is not None and age < cooldown:
                self._event_dedupe[key] = (signature, now)
                return True
            break

        self._event_dedupe[key] = (signature, now)
        return False

    def record_market_tick(self, snapshot: MarketSnapshot) -> None:
        self.market_data.update(snapshot)
        if self.data_quality_service is not None:
            self.data_quality_service.record_tick(snapshot)
        if self.feature_store is not None:
            self.feature_store.ingest_tick(snapshot, timeframe=snapshot.timeframe)
        self.health.update(
            {
                "last_market_tick_at": self.now_provider(),
                "last_market_symbol": snapshot.symbol.upper(),
            }
        )


def active_market_symbols(setups: list[dict[str, Any]]) -> list[str]:
    terminal_statuses = {
        SetupStatus.CLOSED.value,
        SetupStatus.CANCELLED.value,
        SetupStatus.EXPIRED.value,
        SetupStatus.INVALIDATED.value,
        SetupStatus.ERROR.value,
        SetupStatus.ERROR_REQUIRES_MANUAL_REVIEW.value,
    }
    symbols = {
        str(setup["symbol"]).upper()
        for setup in setups
        if setup["status"] not in terminal_statuses
    }
    return sorted(symbols)


def quote_to_market_snapshot(
    symbol: str,
    quote: dict[str, Any],
) -> MarketSnapshot | None:
    price = float_value(quote.get("price"))
    if price is None:
        return None
    return MarketSnapshot(
        symbol=symbol,
        price=float(price),
        open=float_value(quote.get("open")),
        high=float_value(quote.get("high")),
        low=float_value(quote.get("low")),
        close=float_value(quote.get("close")) or price,
        bid=float_value(quote.get("bid")),
        ask=float_value(quote.get("ask")),
        spread=float_value(quote.get("spread")),
        spread_bps=float_value(quote.get("spread_bps")),
        volume=float_value(quote.get("volume")),
        bar_volume_15m=float_value(quote.get("bar_volume_15m")),
        avg_volume_15m=float_value(quote.get("avg_volume_15m")),
        volume_ratio_15m=float_value(quote.get("volume_ratio_15m")),
        current_bar_volume=float_value(
            quote.get("current_bar_volume", quote.get("volume"))
        ),
        previous_high=float_value(quote.get("previous_high")),
        daily_close=float_value(quote.get("close")) or price,
        volume_ratio=float_value(quote.get("volume_ratio")),
        volume_ratio_closed_bar=float_value(
            quote.get(
                "volume_ratio_closed_bar",
                quote.get("volume_ratio_15m", quote.get("volume_ratio")),
            )
        ),
        volume_ratio_live=float_value(quote.get("volume_ratio_live")),
        average_volume_ratio_last_2_bars=float_value(
            quote.get("average_volume_ratio_last_2_bars")
        ),
        volume_status=str(quote.get("volume_status") or ""),
        volume_timeframe=str(quote.get("volume_timeframe") or ""),
        volume_comparison_mode=str(quote.get("volume_comparison_mode") or ""),
        volume_sample_days=int_value(quote.get("volume_sample_days")),
        volume_sample_count=int_value(quote.get("volume_sample_count")),
        elapsed_ratio=float_value(quote.get("elapsed_ratio")),
        projected_volume=float_value(quote.get("projected_volume")),
        bar_count=int_value(quote.get("bar_count")),
        bars_15m_count=int_value(quote.get("bars_15m_count")),
        bars_1h_count=int_value(quote.get("bars_1h_count")),
        bars_above_resistance=int_value(quote.get("bars_above_resistance")),
        minimum_tick=float_value(quote.get("minimum_tick")),
        atr_15m=float_value(quote.get("atr_15m")),
        atr_1h=float_value(quote.get("atr_1h")),
        atr_1h_status=str(quote.get("atr_1h_status") or ""),
        atr_1h_bar_size=str(quote.get("atr_1h_bar_size") or ""),
        atr_1h_duration=str(quote.get("atr_1h_duration") or ""),
        atr_1h_use_rth=(
            bool(quote.get("atr_1h_use_rth"))
            if quote.get("atr_1h_use_rth") is not None
            else None
        ),
        bars_required_for_atr=int_value(quote.get("bars_required_for_atr")),
        historical_1h_available=(
            bool(quote.get("historical_1h_available"))
            if quote.get("historical_1h_available") is not None
            else None
        ),
        historical_1h_error=str(quote.get("historical_1h_error") or ""),
        last_successful_atr_1h=float_value(quote.get("last_successful_atr_1h")),
        last_successful_atr_1h_at=(
            str(quote.get("last_successful_atr_1h_at"))
            if quote.get("last_successful_atr_1h_at")
            else None
        ),
        atr_1h_age_seconds=float_value(quote.get("atr_1h_age_seconds")),
        session=str(quote["session"]).upper() if quote.get("session") else None,
        market_open_time=str(quote["market_open_time"])
        if quote.get("market_open_time")
        else None,
        current_time=str(quote["current_time"]) if quote.get("current_time") else None,
        last_confirmed_higher_low=float_value(quote.get("last_confirmed_higher_low")),
        support_level=float_value(quote.get("support_level")),
        successful_retest_low=float_value(quote.get("successful_retest_low")),
        structural_support=float_value(quote.get("structural_support")),
        breakout_already_detected=bool(
            quote.get("breakout_already_detected", False)
        ),
        new_higher_low_confirmed=bool(
            quote.get("new_higher_low_confirmed", False)
        ),
        close_1h=float_value(quote.get("close_1h")),
        market_data_source=str(quote.get("market_data_source") or ""),
        live_quote_source=str(quote.get("live_quote_source") or ""),
        market_data_type_requested=float_value(
            quote.get("market_data_type_requested")
        ),
        market_data_type_actual=float_value(quote.get("market_data_type_actual")),
        live_market_data_status=str(quote.get("live_market_data_status") or ""),
        last_ibkr_error_code=int_value(quote.get("last_ibkr_error_code")),
        last_ibkr_error_message=str(quote.get("last_ibkr_error_message") or ""),
        bar_date=str(quote.get("bar_date") or ""),
        hybrid_signal_bar_size=str(quote.get("hybrid_signal_bar_size") or ""),
        hybrid_atr_1h_bar_size=str(quote.get("hybrid_atr_1h_bar_size") or ""),
        hybrid_sources=quote.get("hybrid_sources")
        if isinstance(quote.get("hybrid_sources"), dict)
        else {},
        market_data_readiness=quote.get("market_data_readiness")
        if isinstance(quote.get("market_data_readiness"), dict)
        else {},
        historical_bars=quote.get("historical_bars")
        if isinstance(quote.get("historical_bars"), list)
        else [],
    )


def stock_quote_message(symbol: str, quote: dict[str, Any]) -> str:
    fields = stock_quote_fields_text(quote)
    if not fields:
        return f"TWS stock quote {symbol}: no market fields"
    return f"TWS stock quote {symbol}: {fields}"


def stock_quote_fields_text(quote: dict[str, Any]) -> str:
    keys = (
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
        "open",
        "high",
        "low",
        "close",
        "volume",
        "bar_volume_15m",
        "avg_volume_15m",
        "volume_ratio_15m",
        "current_bar_volume",
        "previous_high",
        "volume_ratio",
        "volume_ratio_closed_bar",
        "volume_ratio_live",
        "average_volume_ratio_last_2_bars",
        "volume_status",
        "volume_timeframe",
        "volume_comparison_mode",
        "volume_sample_days",
        "elapsed_ratio",
        "projected_volume",
        "minimum_tick",
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
        "atr_1h_age_seconds",
        "hybrid_signal_bar_size",
        "hybrid_atr_1h_bar_size",
        "bars_15m_count",
        "bars_1h_count",
        "bars_required_for_atr",
        "last_ibkr_error_code",
        "last_ibkr_error_message",
        "quote_latency_ms",
        "evaluation_latency_ms",
        "signal_handling_latency_ms",
        "analysis_latency_ms",
        "total_symbol_latency_ms",
        "readiness",
        "session",
        "current_time",
        "market_open_time",
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


def stock_analysis_summary(processed: list[dict[str, Any]]) -> str:
    counts: dict[str, int] = {}
    for item in processed:
        action = str(item.get("action") or "UNKNOWN")
        counts[action] = counts.get(action, 0) + 1
    return " ".join(f"{action}={counts[action]}" for action in sorted(counts))


def stock_analysis_dedupe_key(
    symbol: str,
    processed: list[dict[str, Any]],
) -> str:
    parts: list[tuple[Any, ...]] = []
    for item in processed:
        metadata = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
        analysis = (
            metadata.get("analysis")
            if isinstance(metadata.get("analysis"), dict)
            else {}
        )
        opportunity_score = (
            item.get("opportunity_score")
            if isinstance(item.get("opportunity_score"), dict)
            else {}
        )
        missing = analysis.get("missing_conditions")
        blocking = analysis.get("blocking_conditions")
        percent = opportunity_score.get("percent")
        percent_bucket = int(round(percent)) if isinstance(percent, (int, float)) else None
        parts.append(
            (
                item.get("setup_id"),
                item.get("status"),
                item.get("action"),
                item.get("reason"),
                opportunity_score.get("label"),
                percent_bucket,
                tuple(missing) if isinstance(missing, list) else (),
                tuple(blocking) if isinstance(blocking, list) else (),
            )
        )
    return repr((symbol.upper(), tuple(parts)))


def float_value(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def int_value(value: Any) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def bounded_concurrency(configured: int, symbol_count: int) -> int:
    if symbol_count <= 0:
        return 1
    return max(1, min(int(configured or 1), symbol_count))


def elapsed_ms(started: float) -> float:
    return round((time.perf_counter() - started) * 1000, 1)


def numeric_values(items: list[dict[str, Any]], key: str) -> list[float]:
    values: list[float] = []
    for item in items:
        value = float_value(item.get(key))
        if value is not None:
            values.append(value)
    return values


def average(values: list[float]) -> float | None:
    if not values:
        return None
    return round(sum(values) / len(values), 1)


def summary_stats(values: list[float]) -> dict[str, float | int | None]:
    if not values:
        return {
            "count": 0,
            "min": None,
            "avg": None,
            "max": None,
        }
    return {
        "count": len(values),
        "min": round(min(values), 1),
        "avg": average(values),
        "max": round(max(values), 1),
    }


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def age_seconds(value: Any) -> int | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return max(int((datetime.now(timezone.utc) - parsed).total_seconds()), 0)
