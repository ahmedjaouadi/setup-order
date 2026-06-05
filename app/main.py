from __future__ import annotations

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app.api import (
    routes_dashboard,
    routes_logs,
    routes_orders,
    routes_positions,
    routes_setups,
    websocket,
)
from app.engine.trading_engine import TradingEngine
from app.settings import load_settings
from app.storage.database import Database
from app.storage.repositories import TradingRepository
from app.utils.logger import configure_logging


def create_app() -> FastAPI:
    settings = load_settings()
    configure_logging(settings.logs_folder)
    database = Database(settings.database_file)
    database.initialize()
    repository = TradingRepository(database)

    app = FastAPI(title="Setup Order", version="0.1.0")
    app.state.settings = settings
    app.state.database = database
    app.state.repository = repository
    app.state.templates = Jinja2Templates(directory="app/gui/templates")
    app.state.engine = TradingEngine(
        settings=settings,
        repository=repository,
        broadcaster=websocket.websocket_manager,
    )

    app.mount("/static", StaticFiles(directory="app/gui/static"), name="static")
    app.include_router(routes_dashboard.router)
    app.include_router(routes_setups.router)
    app.include_router(routes_orders.router)
    app.include_router(routes_positions.router)
    app.include_router(routes_logs.router)
    app.include_router(websocket.router)

    @app.on_event("startup")
    async def startup() -> None:
        await app.state.engine.start()

    @app.on_event("shutdown")
    async def shutdown() -> None:
        await app.state.engine.stop()
        app.state.database.close()

    return app


app = create_app()

