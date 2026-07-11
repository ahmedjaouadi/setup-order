from __future__ import annotations

import asyncio
import logging
from contextlib import suppress
from functools import partial

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app.api import (
    routes_dashboard,
    routes_forecast,
    routes_forecasting,
    routes_intelligence,
    routes_logs,
    routes_market_context,
    routes_observability,
    routes_opportunities,
    routes_opportunity_audit,
    routes_opportunity_radar,
    routes_orders,
    routes_platform,
    routes_positions,
    routes_reports,
    routes_research,
    routes_scoring,
    routes_setups,
    routes_techniques,
    routes_v2_pages,
    websocket,
)
from app.background_jobs import (
    auto_evaluate_detection_outcomes,
    auto_evaluate_forecast_accuracy,
    auto_rebuild_opportunity_shortlist,
    auto_recalculate_forecasts,
    auto_run_learning_loop,
    periodic_loop,
)
from app.data_quality import DataQualityService
from app.engine.trading_engine import TradingEngine
from app.event_bus import EventBus
from app.features import FeatureStore
from app.forecasting.forecast_accuracy_repository import ForecastAccuracyRepository
from app.forecasting.forecast_accuracy_service import ForecastAccuracyService
from app.forecasting.forecast_provider_status import ForecastProviderStatusService
from app.forecasting.forecast_repository import ForecastRepository
from app.forecasting.forecast_service import ForecastService
from app.intelligence.repository import IntelligenceRepository
from app.intelligence.service import IntelligenceService
from app.market_context.repository import MarketContextRepository
from app.market_context.service import MarketContextService
from app.model_lab import ForecastStackBenchmarkService, ModelLabService
from app.observability import ObservabilityService
from app.opportunities import OpportunityScannerService
from app.opportunity_scanner.learning_loop import LearningLoop
from app.opportunity_scanner.outcome_repository import OutcomeRepository
from app.opportunity_scanner.outcome_tracker import OutcomeTracker
from app.opportunity_scanner.technique_repository import TechniqueRepository
from app.opportunity_scanner.technique_seed import (
    apply_builtin_spread_filter_migration,
    seed_builtin_techniques,
)
from app.opportunity_scanner.technique_service import TechniqueService
from app.portfolio_risk import PortfolioRiskService
from app.reports import DailyReportService
from app.scoring import SetupQualityEngine
from app.settings import load_settings
from app.setups.creation_snapshot_service import SetupCreationSnapshotService
from app.storage.database import Database
from app.storage.instance_lock import acquire_instance_lock
from app.storage.repositories import TradingRepository
from app.utils.logger import configure_logging

logger = logging.getLogger(__name__)


async def _start_engine_background(app: FastAPI) -> None:
    try:
        await app.state.engine.start()
        app.state.background_tasks = _start_background_tasks(app)
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        logger.exception("Trading engine startup failed")
        app.state.engine_startup_error = str(exc)
        health = getattr(app.state.engine, "_health", None)
        if isinstance(health, dict):
            health["last_error"] = f"Startup failed: {exc}"


def _start_background_tasks(app: FastAPI) -> list[asyncio.Task]:
    settings = getattr(app.state, "settings", None)
    raw = getattr(settings, "raw", {}) if settings is not None else {}
    forecasting = raw.get("forecasting", {}) if isinstance(raw.get("forecasting"), dict) else {}
    forecast_accuracy = (
        raw.get("forecast_accuracy", {}) if isinstance(raw.get("forecast_accuracy"), dict) else {}
    )
    opportunity_scanner = (
        raw.get("opportunity_scanner", {})
        if isinstance(raw.get("opportunity_scanner"), dict)
        else {}
    )
    forecast_interval = int(forecasting.get("auto_recalc_interval_seconds", 900) or 900)
    forecast_accuracy_interval = int(
        forecast_accuracy.get("auto_evaluate_interval_seconds", forecast_interval)
        or forecast_interval
    )
    scan_interval = int(opportunity_scanner.get("scan_interval_seconds", 30) or 30)
    outcome_interval = int(
        opportunity_scanner.get("outcome_evaluation_interval_seconds", 900) or 900
    )
    learning_cfg = (
        opportunity_scanner.get("learning", {})
        if isinstance(opportunity_scanner.get("learning"), dict)
        else {}
    )
    learning_interval = int(learning_cfg.get("interval_seconds", 86400) or 86400)
    tasks: list[asyncio.Task] = []
    if forecast_interval > 0:
        tasks.append(
            asyncio.create_task(
                periodic_loop(
                    name="forecast-auto-recalc",
                    interval_seconds=forecast_interval,
                    callback=partial(auto_recalculate_forecasts, app),
                    initial_delay_seconds=5,
                ),
                name="setup-order-forecast-auto-recalc",
            )
        )
    if scan_interval > 0:
        tasks.append(
            asyncio.create_task(
                periodic_loop(
                    name="opportunity-auto-scan",
                    interval_seconds=scan_interval,
                    callback=partial(auto_rebuild_opportunity_shortlist, app),
                    initial_delay_seconds=5,
                ),
                name="setup-order-opportunity-auto-scan",
            )
        )
    if forecast_accuracy_interval > 0:
        tasks.append(
            asyncio.create_task(
                periodic_loop(
                    name="forecast-accuracy-auto-evaluate",
                    interval_seconds=forecast_accuracy_interval,
                    callback=partial(auto_evaluate_forecast_accuracy, app),
                    initial_delay_seconds=10,
                ),
                name="setup-order-forecast-accuracy-auto-evaluate",
            )
        )
    if outcome_interval > 0:
        tasks.append(
            asyncio.create_task(
                periodic_loop(
                    name="detection-outcome-auto-evaluate",
                    interval_seconds=outcome_interval,
                    callback=partial(auto_evaluate_detection_outcomes, app),
                    initial_delay_seconds=15,
                ),
                name="setup-order-detection-outcome-auto-evaluate",
            )
        )
    if learning_interval > 0:
        tasks.append(
            asyncio.create_task(
                periodic_loop(
                    name="technique-learning-auto-run",
                    interval_seconds=learning_interval,
                    callback=partial(auto_run_learning_loop, app),
                    initial_delay_seconds=20,
                ),
                name="setup-order-technique-learning-auto-run",
            )
        )
    return tasks


def create_app() -> FastAPI:
    settings = load_settings()
    configure_logging(settings.logs_folder)
    # Refuse to start if another live process already owns this database:
    # one writer per SQLite file, one engine per TWS account (see
    # instance_lock.py for the 4-concurrent-instances incident this prevents).
    instance_lock = acquire_instance_lock(settings.database_file)
    database = Database(settings.database_file)
    database.initialize()
    seed_builtin_techniques(TechniqueRepository(database))
    repository = TradingRepository(database)
    intelligence_repository = IntelligenceRepository(database)
    forecast_repository = ForecastRepository(database)
    forecast_accuracy_repository = ForecastAccuracyRepository(database)
    forecast_accuracy = ForecastAccuracyService(forecast_accuracy_repository, settings.raw)
    market_context_repository = MarketContextRepository(database)
    intelligence_service = IntelligenceService(
        repository=intelligence_repository,
        defaults=settings.raw,
    )
    market_context_service = MarketContextService(
        market_repository=market_context_repository,
        trading_repository=repository,
        settings=settings.raw,
    )

    app = FastAPI(title="Setup Order", version="0.1.0")
    app.state.settings = settings
    app.state.instance_lock = instance_lock
    app.state.database = database
    app.state.repository = repository
    app.state.intelligence_repository = intelligence_repository
    app.state.intelligence = intelligence_service
    app.state.market_context_repository = market_context_repository
    app.state.market_context = market_context_service
    app.state.templates = Jinja2Templates(directory="app/gui/templates")
    app.state.forecast_repository = forecast_repository
    app.state.forecast_accuracy_repository = forecast_accuracy_repository
    app.state.forecast_accuracy = forecast_accuracy
    app.state.data_quality = DataQualityService(repository, settings.raw)
    app.state.feature_store = FeatureStore(repository, settings.raw)
    app.state.engine = TradingEngine(
        settings=settings,
        repository=repository,
        broadcaster=websocket.websocket_manager,
        data_quality_service=app.state.data_quality,
        feature_store=app.state.feature_store,
    )
    # One-shot, traced migration (TODO 7.7): builtin rules in base gain the
    # spread_pct <= 0.5 liquidity condition, with revision bump + trace. Runs
    # after the engine so refusals are auditable through its event store.
    apply_builtin_spread_filter_migration(
        TechniqueRepository(database),
        repository,
        app.state.engine.event_store,
    )
    app.state.forecast = ForecastService(
        settings=settings.raw,
        repository=forecast_repository,
        trading_repository=repository,
        market_history_provider=app.state.engine.forecast_market_history,
        accuracy_service=forecast_accuracy,
    )
    app.state.forecast_provider_status = ForecastProviderStatusService(
        settings.raw, app.state.forecast
    )
    app.state.setup_creation_snapshots = SetupCreationSnapshotService(
        repository, app.state.engine.market_data.latest
    )
    app.state.scoring = SetupQualityEngine(
        repository=repository,
        forecast_repository=forecast_repository,
        settings=settings.raw,
        forecast_accuracy_service=forecast_accuracy,
    )
    app.state.opportunity_scanner = OpportunityScannerService(
        repository=repository,
        scoring=app.state.scoring,
        event_store=app.state.engine.event_store,
        settings=settings.raw,
    )
    app.state.outcome_tracker = OutcomeTracker(OutcomeRepository(database))
    app.state.techniques = TechniqueService(
        TechniqueRepository(database),
        stats_provider=app.state.outcome_tracker.technique_stats,
        outcomes_provider=app.state.outcome_tracker.repository.outcomes_for_technique,
        feedback_recorder=app.state.outcome_tracker.repository.set_feedback,
        event_store=app.state.engine.event_store,
    )
    app.state.learning_loop = LearningLoop(
        TechniqueRepository(database),
        app.state.outcome_tracker.technique_stats,
        event_store=app.state.engine.event_store,
        settings=settings.raw,
    )
    app.state.portfolio_risk = PortfolioRiskService(repository, settings.raw)
    app.state.model_lab = ModelLabService(repository, settings.raw)
    app.state.forecast_stack_benchmark = ForecastStackBenchmarkService(
        repository,
        forecast_service=app.state.forecast,
    )
    app.state.daily_reports = DailyReportService(repository)
    app.state.event_bus = EventBus(repository, app.state.engine.event_store)
    app.state.observability = ObservabilityService(
        repository,
        snapshot_provider=app.state.engine.snapshot,
    )
    app.state.background_tasks = []

    app.mount("/static", StaticFiles(directory="app/gui/static"), name="static")

    @app.get("/api/health")
    async def health() -> dict:
        return await app.state.observability.health()

    app.include_router(routes_dashboard.router)
    app.include_router(routes_setups.router)
    app.include_router(routes_techniques.router)
    app.include_router(routes_forecast.router)
    app.include_router(routes_forecasting.router)
    app.include_router(routes_intelligence.router)
    app.include_router(routes_market_context.router)
    app.include_router(routes_observability.router)
    app.include_router(routes_opportunities.router)
    app.include_router(routes_opportunity_radar.router)
    app.include_router(routes_opportunity_audit.router)
    app.include_router(routes_platform.router)
    app.include_router(routes_research.router)
    app.include_router(routes_scoring.router)
    app.include_router(routes_orders.router)
    app.include_router(routes_positions.router)
    app.include_router(routes_reports.router)
    app.include_router(routes_logs.router)
    app.include_router(routes_v2_pages.router)
    app.include_router(websocket.router)

    @app.on_event("startup")
    async def startup() -> None:
        app.state.engine_startup_error = ""
        app.state.engine_start_task = asyncio.create_task(
            _start_engine_background(app),
            name="setup-order-engine-start",
        )

    @app.on_event("shutdown")
    async def shutdown() -> None:
        for task in getattr(app.state, "background_tasks", []):
            if task is not None and not task.done():
                task.cancel()
                with suppress(asyncio.CancelledError):
                    await task
        start_task = getattr(app.state, "engine_start_task", None)
        if start_task is not None and not start_task.done():
            start_task.cancel()
            with suppress(asyncio.CancelledError):
                await start_task
        await app.state.engine.stop()
        app.state.database.close()
        app.state.instance_lock.release()

    return app


app = create_app()
