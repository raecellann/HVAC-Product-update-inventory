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

from config import DATA_DIR, SKU_CACHE_FILE

from . import history, services
from .deps import get_cfg, get_runner
from .routes import api as api_routes

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
    _bootstrap_cache_if_missing()
    yield


def _bootstrap_cache_if_missing() -> None:
    """Auto-start a product list rebuild if sku_cache.json is absent.

    First deploys and any case where the cache file got lost (e.g. disk not
    mounted) would otherwise require the user to click "Refresh product list"
    before anything else works. Kick it off automatically so the site is
    usable without that manual step.
    """
    if SKU_CACHE_FILE.exists():
        return
    try:
        cfg = get_cfg()
    except Exception as e:  # noqa: BLE001 — missing env vars, don't crash startup
        log.warning("Skipping cache bootstrap — config not ready: %s", e)
        return
    run_id = history.create_run("cache_refresh")
    job_id = get_runner().try_start(
        "cache_refresh",
        services.cache_refresh_job,
        run_id=run_id,
        cfg=cfg,
    )
    if job_id is None:
        log.warning("Cache bootstrap skipped — another job is already running")
    else:
        log.info("Cache bootstrap started as run #%d (job %s)", run_id, job_id)


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
