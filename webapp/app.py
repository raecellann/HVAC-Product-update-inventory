"""FastAPI app factory.

- In production: `frontend/dist` is served as static files (built by Vite).
- In dev: the Vite dev server (port 5173) serves the HTML + assets and
  proxies `/api` and `/download` to this FastAPI (default port 8001).
"""
from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from core.config import DATA_DIR

from . import history
from .routes import api as api_routes
from .routes import crossref as crossref_routes

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
log = logging.getLogger("webapp")

FRONTEND_DIST = Path(__file__).parent.parent / "frontend" / "dist"


@asynccontextmanager
async def _lifespan(_app: FastAPI):
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    history.init_db()
    interrupted = history.mark_interrupted_runs()
    if interrupted:
        log.warning("Marked %d in-flight run(s) as interrupted after restart", interrupted)
    purged = history.purge_old_run_details()
    if purged:
        log.info("Purged details for %d old run(s)", purged)
    yield


def create_app() -> FastAPI:
    app = FastAPI(title="HVAC Inventory", lifespan=_lifespan)

    @app.middleware("http")
    async def _no_cache_api(request, call_next):
        response = await call_next(request)
        if request.url.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
        return response

    @app.get("/healthz")
    def _healthz() -> dict:
        return {"ok": True}

    app.include_router(api_routes.router)
    app.include_router(crossref_routes.router)

    if FRONTEND_DIST.exists():
        app.mount("/", StaticFiles(directory=str(FRONTEND_DIST), html=True), name="frontend")
    else:
        @app.get("/")
        def _no_frontend():
            raise HTTPException(
                503,
                "Frontend not built. Run `npm install && npm run build`, "
                "or in dev open the Vite server on port 5173.",
            )

    return app


app = create_app()
