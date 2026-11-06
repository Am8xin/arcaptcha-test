"""FastAPI application factory.

Layers:
    app.bots       -> implementations (no platform imports besides base/registry)
    app.registry   -> discovery/registration mechanism
    app.runner     -> execution layer (threads, lifecycle, timeouts)
    app.db         -> storage layer (SQLAlchemy, persistent history)
    app.api/web    -> interfaces (REST + HTML) on top of the above
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from pathlib import Path

from .api import router as api_router
from .bots import autodiscover
from .config import settings
from .db import init_db, make_engine, make_session_factory
from .runner import BotRunner
from .web.routes import router as web_router

autodiscover()  # registers every bot shipped in app/bots/

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
)
logging.getLogger("selenium").setLevel(logging.WARNING)
logging.getLogger("undetected_chromedriver").setLevel(logging.WARNING)


def create_app(*, engine=None, session_factory=None, runner=None) -> FastAPI:
    """Build the app.

    ``engine`` / ``session_factory`` / ``runner`` are overridable so tests can
    wire an isolated database and a runner with short timeouts.
    """

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.engine = engine or make_engine()
        init_db(app.state.engine)
        app.state.session_factory = session_factory or make_session_factory(
            app.state.engine
        )
        app.state.runner = runner or BotRunner(app.state.session_factory)
        logging.getLogger("platform").info(
            "platform up: db=%s parallel=%s timeout=%ss",
            settings.database_url.split("://")[0],
            settings.max_parallel_runs,
            settings.run_timeout_seconds,
        )
        try:
            yield
        finally:
            if runner is None:
                app.state.runner.shutdown(wait=False)
            app.state.engine.dispose()

    app = FastAPI(
        title="ARCaptcha Browser Bot Platform",
        description="Extensible platform for defining, running and monitoring browser bots.",
        version="1.0.0",
        lifespan=lifespan,
    )

    static_dir = Path(__file__).resolve().parent / "web" / "static"
    app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")
    app.include_router(api_router)
    app.include_router(web_router)

    @app.get("/healthz")
    def healthz():
        return {"status": "ok"}

    return app


app = create_app()
