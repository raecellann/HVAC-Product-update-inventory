"""Cross-reference enrichment endpoints.

Flow is symmetric to the inventory/preview routes, but there's no file upload:
  POST /api/crossref/crawl           -> start a scan+crawl job (returns run_id, job_id)
  GET  /api/crossref/latest          -> most recent crossref_crawl run (summary)
  GET  /api/crossref/run/{run_id}    -> paginated results for one run
  POST /api/crossref/apply/{run_id}  -> push `found` rows to Shopify metafield
"""
from __future__ import annotations

import json
import os
from pathlib import Path

from fastapi import APIRouter, Body, Depends, HTTPException, Request
from fastapi.responses import JSONResponse

from core.config import Config, RUNS_DIR
from core.crossref import TemplateSource

from .. import crossref_service, history
from ..deps import get_cfg, get_runner
from ..jobs import JobRunner

router = APIRouter(prefix="/api/crossref")


LOOPBACK_HOSTS = {"127.0.0.1", "::1", "localhost"}


def _is_loopback(request: Request | None) -> bool:
    if request is None:
        return False
    client = request.client
    host = (client.host if client else "") or ""
    return host in LOOPBACK_HOSTS


def _apply_disabled(request: Request | None = None) -> bool:
    if _is_loopback(request):
        return False
    return os.environ.get("DISABLE_APPLY", "").strip().lower() in {"1", "true", "yes"}


# ------------------------------------------------------------------ crawl


@router.post("/crawl")
def start_crawl(
    body: dict = Body(...),
    cfg: Config = Depends(get_cfg),
    runner: JobRunner = Depends(get_runner),
) -> JSONResponse:
    url_template = (body.get("url_template") or "").strip()
    if not url_template:
        raise HTTPException(400, "url_template is required.")
    # Fail fast before creating a run row we'll have to mark failed.
    try:
        TemplateSource(url_template=url_template)
    except ValueError as e:
        raise HTTPException(400, str(e))

    if runner.is_busy():
        raise HTTPException(409, "Another job is running. Please wait.")
    run_id = history.create_run("crossref_crawl")
    job_id = runner.try_start(
        "crossref_crawl",
        crossref_service.crawl_job,
        run_id=run_id,
        cfg=cfg,
        url_template=url_template,
    )
    if job_id is None:
        history.set_status(run_id, "failed", error="Another job started first.")
        raise HTTPException(409, "Another job is running. Please wait.")
    return JSONResponse({"run_id": run_id, "job_id": job_id})


# ----------------------------------------------------------------- read


@router.get("/latest")
def latest() -> JSONResponse:
    """Most recent crossref_crawl run (summary only; no results list)."""
    runs = history.list_runs(limit=50)
    recent = next((r for r in runs if r["kind"] == "crossref_crawl"), None)
    if recent is None:
        return JSONResponse({"run": None})
    recent["details_available"] = history.details_available(recent)
    return JSONResponse({"run": recent})


@router.get("/run/{run_id}")
def get_crossref_run(
    run_id: int,
    page: int = 1,
    per_page: int = 50,
    status: str = "all",
    q: str = "",
    request: Request = None,
) -> JSONResponse:
    run = history.get_run(run_id)
    if run is None or run["kind"] != "crossref_crawl":
        raise HTTPException(404, "Cross-reference run not found.")
    if run.get("details_purged"):
        raise HTTPException(410, "Results for this run have expired.")

    path = RUNS_DIR / str(run_id) / "crossref.json"
    if not path.exists():
        return JSONResponse({
            "run":     run,
            "scan":    None,
            "rows":    [],
            "total":   0,
            "page":    1,
            "per_page": per_page,
            "apply_disabled_in_dev": _apply_disabled(request),
        })

    payload = json.loads(path.read_text(encoding="utf-8"))
    rows = payload.get("results") or []

    if status and status != "all":
        rows = [r for r in rows if r.get("status") == status]

    if q:
        ql = q.lower()
        rows = [
            r for r in rows
            if ql in (r.get("sku") or "").lower() or ql in (r.get("title") or "").lower()
        ]

    total = len(rows)
    page = max(1, page)
    per_page = max(1, min(per_page, 500))
    start = (page - 1) * per_page

    return JSONResponse({
        "run":      run,
        "scan":     payload.get("scan"),
        "rows":     rows[start:start + per_page],
        "total":    total,
        "page":     page,
        "per_page": per_page,
        "apply_disabled_in_dev": _apply_disabled(request),
    })


# ----------------------------------------------------------------- apply


@router.post("/apply/{source_run_id}")
def apply_crossref(
    source_run_id: int,
    request: Request,
    cfg: Config = Depends(get_cfg),
    runner: JobRunner = Depends(get_runner),
) -> JSONResponse:
    if _apply_disabled(request):
        raise HTTPException(403, "Apply is disabled in dev mode.")

    source = history.get_run(source_run_id)
    if source is None or source["kind"] != "crossref_crawl":
        raise HTTPException(404, "Crawl run not found.")
    if source["status"] != "succeeded":
        raise HTTPException(400, "The crawl run did not finish successfully.")
    if source.get("applied_at"):
        raise HTTPException(400, "This crawl has already been applied.")
    if source.get("details_purged"):
        raise HTTPException(410, "Crawl results have expired. Please re-run.")
    if runner.is_busy():
        raise HTTPException(409, "Another job is running. Please wait.")

    apply_run_id = history.create_run(
        "crossref_apply",
        parent_run_id=source_run_id,
    )
    job_id = runner.try_start(
        "crossref_apply",
        crossref_service.apply_job,
        run_id=apply_run_id,
        source_run_id=source_run_id,
        cfg=cfg,
    )
    if job_id is None:
        history.set_status(apply_run_id, "failed", error="Another job started first.")
        raise HTTPException(409, "Another job is running. Please wait.")

    # Mark the source as applied the moment we kick off the apply job.
    history.mark_preview_applied(source_run_id)
    return JSONResponse({"run_id": apply_run_id, "job_id": job_id})
