from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any

from app.models import utc_now_iso
from app.utils.market_hours import classify_us_equity_session

logger = logging.getLogger(__name__)

TERMINAL_SETUP_STATUSES = {
    "CANCELLED",
    "COMPLETED",
    "DELETED",
    "EMERGENCY_STOP",
    "EXPIRED",
    "FILLED",
    "CLOSED",
    "IN_POSITION",
    "INVALIDATED",
    "MANAGING_POSITION",
    "REJECTED",
    "ERROR",
    "ERROR_REQUIRES_MANUAL_REVIEW",
}


async def periodic_loop(
    *,
    name: str,
    interval_seconds: int | float,
    callback: Callable[[], Awaitable[Any]],
    initial_delay_seconds: int | float = 0.0,
) -> None:
    if initial_delay_seconds > 0:
        await asyncio.sleep(float(initial_delay_seconds))
    while True:
        try:
            await callback()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("%s failed", name)
        await asyncio.sleep(max(1.0, float(interval_seconds or 0)))


async def auto_recalculate_forecasts(app: Any) -> dict[str, Any]:
    forecast_service = getattr(app.state, "forecast", None)
    repository = getattr(app.state, "repository", None)
    engine = getattr(app.state, "engine", None)
    if forecast_service is None or repository is None or engine is None:
        summary = {"ok": False, "reason": "forecasting_not_ready", "items": []}
        app.state.forecast_auto_refresh = summary
        return summary

    runtime: dict[str, Any] = {}
    if hasattr(engine, "runtime_state"):
        try:
            runtime = engine.runtime_state()
        except Exception as exc:
            summary = {"ok": False, "reason": f"runtime_state_error: {exc}", "items": []}
            app.state.forecast_auto_refresh = summary
            return summary

    connection = str(runtime.get("connection") or "").upper()
    if connection and connection != "CONNECTED":
        summary = {
            "ok": False,
            "reason": f"broker_{connection.lower()}",
            "items": [],
            "generated_at": utc_now_iso(),
        }
        app.state.forecast_auto_refresh = summary
        return summary

    setups = [setup for setup in repository.list_setups() if _setup_needs_refresh(setup)]
    items: list[dict[str, Any]] = []
    for setup in setups:
        symbol = str(setup.get("symbol") or "").upper()
        setup_id = str(setup.get("setup_id") or "")
        if not symbol or not setup_id:
            continue
        config = setup.get("config") if isinstance(setup.get("config"), dict) else {}
        timeframe = _setup_signal_timeframe(
            config, getattr(forecast_service.config, "timeframe", "15m")
        )
        try:
            result = await forecast_service.forecast_ensemble(
                symbol,
                timeframe=timeframe,
                setup_id=setup_id,
                force_refresh=True,
            )
        except Exception as exc:
            logger.exception("Forecast auto refresh failed for %s (%s)", symbol, setup_id)
            items.append(
                {
                    "setup_id": setup_id,
                    "symbol": symbol,
                    "timeframe": timeframe,
                    "status": "ERROR",
                    "error": str(exc),
                }
            )
            continue
        items.append(
            {
                "setup_id": setup_id,
                "symbol": symbol,
                "timeframe": timeframe,
                "status": result.get("status"),
                "generated_at": result.get("generated_at"),
                "forecast_id": result.get("forecast_id"),
            }
        )

    summary = {
        "ok": True,
        "generated_at": utc_now_iso(),
        "setup_count": len(items),
        "successful_count": sum(
            1 for item in items if str(item.get("status") or "").upper() in {"OK", "PARTIAL"}
        ),
        "failed_count": sum(
            1 for item in items if str(item.get("status") or "").upper() not in {"OK", "PARTIAL"}
        ),
        "items": items,
    }
    app.state.forecast_auto_refresh = summary
    return summary


async def auto_rebuild_opportunity_shortlist(app: Any) -> dict[str, Any]:
    scanner = getattr(app.state, "opportunity_scanner", None)
    if scanner is None:
        summary = {"ok": False, "reason": "scanner_not_ready", "items": []}
        app.state.opportunity_auto_refresh = summary
        return summary

    result = await asyncio.to_thread(scanner.rebuild_shortlist)
    summary = {
        "ok": bool(result.get("ok")),
        "generated_at": utc_now_iso(),
        "scan": result.get("scan") or {},
        "shortlist": result.get("shortlist") or {},
        "items": result.get("items") or [],
    }
    app.state.opportunity_auto_refresh = summary
    return summary


async def auto_evaluate_forecast_accuracy(app: Any) -> dict[str, Any]:
    accuracy = getattr(app.state, "forecast_accuracy", None)
    accuracy_repository = getattr(app.state, "forecast_accuracy_repository", None)
    repository = getattr(app.state, "repository", None)
    if accuracy is None or accuracy_repository is None:
        summary = {"ok": False, "reason": "forecast_accuracy_not_ready", "items": []}
        app.state.forecast_accuracy_auto_refresh = summary
        return summary

    try:
        due = await asyncio.to_thread(accuracy_repository.due_outcomes, utc_now_iso())
    except Exception as exc:
        logger.exception("Forecast accuracy due outcome lookup failed")
        summary = {"ok": False, "reason": f"due_lookup_error: {exc}", "items": []}
        app.state.forecast_accuracy_auto_refresh = summary
        return summary

    symbols = sorted({str(item.get("symbol") or "").upper() for item in due if item.get("symbol")})
    observations = {
        symbol: observation
        for symbol in symbols
        for observation in [_latest_market_observation(app, symbol, repository)]
        if observation.get("price") is not None
    }
    result = await asyncio.to_thread(accuracy.evaluate_due, observations)
    scorecards = []
    if int(result.get("evaluated_count") or 0) > 0:
        scorecards = await asyncio.to_thread(accuracy.rebuild_scorecards)

    summary = {
        "ok": True,
        "generated_at": utc_now_iso(),
        "due_count": len(due),
        "priced_symbol_count": len(observations),
        "evaluated_count": int(result.get("evaluated_count") or 0),
        "skipped_without_price": result.get("skipped_without_price", []),
        "scorecard_count": len(scorecards),
        "items": result.get("evaluated", []),
    }
    app.state.forecast_accuracy_auto_refresh = summary
    return summary


async def auto_evaluate_detection_outcomes(app: Any) -> dict[str, Any]:
    tracker = getattr(app.state, "outcome_tracker", None)
    if tracker is None:
        summary = {"ok": False, "reason": "outcome_tracker_not_ready"}
        app.state.detection_outcomes_auto_refresh = summary
        return summary

    # Evaluation and stats are frozen outside regular trading hours.
    if classify_us_equity_session(datetime.now(UTC)) != "RTH":
        summary = {"ok": True, "skipped": "outside_rth", "evaluated": 0, "expired": 0}
        app.state.detection_outcomes_auto_refresh = summary
        return summary

    now = utc_now_iso()
    try:
        due = await asyncio.to_thread(tracker.repository.due_outcomes, now)
    except Exception as exc:
        logger.exception("Detection outcome due lookup failed")
        summary = {"ok": False, "reason": f"due_lookup_error: {exc}"}
        app.state.detection_outcomes_auto_refresh = summary
        return summary

    engine = getattr(app.state, "engine", None)
    symbols = sorted({str(item.get("symbol") or "").upper() for item in due if item.get("symbol")})
    bars_by_symbol = {symbol: await _daily_bars(engine, symbol) for symbol in symbols}

    def provider(symbol: str, start: str, end: str) -> list[dict[str, Any]]:
        return _bars_in_window(bars_by_symbol.get(symbol.upper(), []), start, end)

    result = await asyncio.to_thread(tracker.evaluate_due, provider, now=now)
    summary = {
        "ok": True,
        "generated_at": now,
        "due_count": len(due),
        "evaluated": int(result.get("evaluated") or 0),
        "expired": int(result.get("expired") or 0),
    }
    app.state.detection_outcomes_auto_refresh = summary
    return summary


async def _daily_bars(engine: Any, symbol: str) -> list[dict[str, Any]]:
    if engine is None or not hasattr(engine, "market_history"):
        return []
    try:
        payload = await engine.market_history(symbol, "1d")
    except Exception:
        logger.debug("Daily history unavailable for %s", symbol, exc_info=True)
        return []
    bars = payload.get("historical_bars") if isinstance(payload, dict) else None
    return bars if isinstance(bars, list) else []


def _bars_in_window(bars: list[dict[str, Any]], start: str, end: str) -> list[dict[str, Any]]:
    start_date = _date_key(start)
    end_date = _date_key(end)
    window: list[dict[str, Any]] = []
    for bar in bars:
        if not isinstance(bar, dict):
            continue
        bar_date = _date_key(str(bar.get("date") or ""))
        if bar_date and start_date <= bar_date <= end_date:
            window.append(bar)
    return window


def _date_key(value: str) -> str:
    """Normalise a date/datetime string to YYYYMMDD for windowing."""
    digits = "".join(ch for ch in value if ch.isdigit())
    return digits[:8]


def _setup_needs_refresh(setup: dict[str, Any]) -> bool:
    status = str(setup.get("status") or "").upper()
    return status not in TERMINAL_SETUP_STATUSES


def _setup_signal_timeframe(config: dict[str, Any], default: str) -> str:
    timeframes = config.get("timeframes") if isinstance(config.get("timeframes"), dict) else {}
    timeframe = timeframes.get("signal") if isinstance(timeframes, dict) else None
    return str(timeframe or config.get("timeframe") or default or "15m")


def _latest_market_observation(app: Any, symbol: str, repository: Any | None) -> dict[str, Any]:
    engine = getattr(app.state, "engine", None)
    market_data = getattr(engine, "market_data", None)
    latest = (
        market_data.latest(symbol)
        if market_data is not None and hasattr(market_data, "latest")
        else None
    )
    if latest is not None:
        price = _number(getattr(latest, "close", None), getattr(latest, "price", None))
        path = _bar_path(getattr(latest, "historical_bars", None))
        if price is not None:
            return {
                "price": price,
                "high": _number(getattr(latest, "high", None)),
                "low": _number(getattr(latest, "low", None)),
                "path": path,
            }

    if repository is None or not hasattr(repository, "list_events"):
        return {"price": None, "high": None, "low": None, "path": []}
    events = repository.list_events(symbol=symbol, event_type="stock_quote", limit=1)
    event = events[0] if events else {}
    data = (
        event.get("data") if isinstance(event, dict) and isinstance(event.get("data"), dict) else {}
    )
    return {
        "price": _number(
            data.get("price"),
            data.get("last"),
            data.get("last_price"),
            data.get("close"),
            data.get("market_price"),
        ),
        "high": _number(data.get("high"), data.get("day_high")),
        "low": _number(data.get("low"), data.get("day_low")),
        "path": _bar_path(data.get("historical_bars")),
    }


def _bar_path(bars: Any) -> list[float]:
    if not isinstance(bars, list):
        return []
    path: list[float] = []
    for item in bars:
        if not isinstance(item, dict):
            continue
        value = _number(item.get("close"), item.get("price"))
        if value is not None:
            path.append(value)
    return path


def _number(*values: Any) -> float | None:
    for value in values:
        if value in (None, ""):
            continue
        try:
            return float(value)
        except (TypeError, ValueError):
            continue
    return None
