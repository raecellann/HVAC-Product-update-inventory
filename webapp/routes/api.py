"""JSON API: upload, apply, status, cache refresh, paginated preview."""
from __future__ import annotations

import json
import os
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, UploadFile
from fastapi.responses import FileResponse, JSONResponse

from config import RUNS_DIR, SKU_CACHE_FILE, UPLOADS_DIR, Config

from .. import history, services
from ..deps import get_cfg, get_runner
from ..jobs import JobRunner

router = APIRouter(prefix="/api")

MAX_UPLOAD_BYTES = 100 * 1024 * 1024  # 100 MB
APPLY_WINDOW_HOURS = 24
ALLOWED_SUFFIXES = {".xlsx", ".xlsm"}


def _apply_disabled() -> bool:
    return os.environ.get("DISABLE_APPLY", "").strip().lower() in {"1", "true", "yes"}


# ---------------------------------------------------------------------- summary


@router.get("/runs")
def list_runs(limit: int = 50) -> JSONResponse:
    runs = history.list_runs(limit=max(1, min(limit, 500)))
    for r in runs:
        r["details_available"] = history.details_available(r)
    return JSONResponse({"runs": runs})


@router.get("/run/{run_id}")
def get_run(run_id: int, runner: JobRunner = Depends(get_runner)) -> JSONResponse:
    run = history.get_run(run_id)
    if run is None:
        raise HTTPException(404, "Run not found.")
    run["details_available"] = history.details_available(run)
    # Compute "can apply now" state so the frontend doesn't have to know the rules.
    can_apply, reason = _apply_eligibility(run)
    run["can_apply"] = can_apply
    run["apply_blocked_reason"] = reason
    run["apply_disabled_in_dev"] = _apply_disabled()
    # If this run is the currently-executing job, surface its in-memory progress
    # so the run detail page can show a live bar without needing the job_id.
    if run["status"] == "running":
        current = runner.current()
        if current and current.get("run_id") == run_id:
            run["live"] = {
                "state": current.get("state"),
                "progress": current.get("progress") or {},
            }
    return JSONResponse(run)


@router.get("/dev/flags")
def dev_flags() -> JSONResponse:
    """Minor conveniences for the frontend to show dev-mode banners."""
    return JSONResponse({"disable_apply": _apply_disabled()})


# ---------------------------------------------------------------------- upload


@router.post("/upload")
async def upload_file(
    file: UploadFile,
    cfg: Config = Depends(get_cfg),
    runner: JobRunner = Depends(get_runner),
) -> JSONResponse:
    if runner.is_busy():
        raise HTTPException(409, "Another job is running. Please wait for it to finish.")

    if not SKU_CACHE_FILE.exists():
        raise HTTPException(
            400,
            "Product list has never been built. "
            "Press 'Refresh product list' on the dashboard first.",
        )

    suffix = Path(file.filename or "").suffix.lower()
    if suffix == ".xls":
        raise HTTPException(
            400,
            "This format (.xls) is not supported. Please save your file as .xlsx and try again.",
        )
    if suffix not in ALLOWED_SUFFIXES:
        raise HTTPException(
            400,
            "Only Excel files (.xlsx or .xlsm) are accepted.",
        )

    UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
    run_id = history.create_run("preview", file_name=file.filename or "")
    dest_dir = services.run_dir(run_id)
    dest = dest_dir / "upload.xlsx"

    # Stream to disk with a size cap.
    written = 0
    with dest.open("wb") as out:
        while True:
            chunk = await file.read(1024 * 1024)
            if not chunk:
                break
            written += len(chunk)
            if written > MAX_UPLOAD_BYTES:
                out.close()
                dest.unlink(missing_ok=True)
                history.set_status(run_id, "failed", error="File too large (>100 MB).")
                raise HTTPException(413, "File too large (>100 MB).")
            out.write(chunk)

    job_id = runner.try_start(
        "preview",
        services.preview_job,
        run_id=run_id,
        upload_path=dest,
        cfg=cfg,
    )
    if job_id is None:
        history.set_status(run_id, "failed", error="Another job started first.")
        raise HTTPException(409, "Another job is running. Please wait.")

    return JSONResponse({"run_id": run_id, "job_id": job_id})


# ------------------------------------------------------------------------ apply


@router.post("/apply/{preview_run_id}")
def apply_preview(
    preview_run_id: int,
    cfg: Config = Depends(get_cfg),
    runner: JobRunner = Depends(get_runner),
) -> JSONResponse:
    if _apply_disabled():
        raise HTTPException(403, "Apply is disabled in dev mode.")

    preview = history.get_run(preview_run_id)
    if preview is None or preview["kind"] != "preview":
        raise HTTPException(404, "Preview not found.")
    can_apply, reason = _apply_eligibility(preview)
    if not can_apply:
        status = 409 if "already" in reason or "running" in reason else 400
        raise HTTPException(status, reason)

    if runner.is_busy():
        raise HTTPException(409, "Another job is running. Please wait.")

    apply_run_id = history.create_run(
        "apply",
        file_name=preview.get("file_name"),
        parent_run_id=preview_run_id,
    )
    job_id = runner.try_start(
        "apply",
        services.apply_job,
        run_id=apply_run_id,
        preview_run_id=preview_run_id,
        cfg=cfg,
    )
    if job_id is None:
        history.set_status(apply_run_id, "failed", error="Another job started first.")
        raise HTTPException(409, "Another job is running. Please wait.")

    return JSONResponse({"run_id": apply_run_id, "job_id": job_id})


# ---------------------------------------------------------------- upload prices


@router.post("/upload-prices")
async def upload_prices(
    file: UploadFile,
    cfg: Config = Depends(get_cfg),
    runner: JobRunner = Depends(get_runner),
) -> JSONResponse:
    if runner.is_busy():
        raise HTTPException(409, "Another job is running. Please wait for it to finish.")

    if not SKU_CACHE_FILE.exists():
        raise HTTPException(
            400,
            "Product list has never been built. "
            "Press 'Refresh product list' on the dashboard first.",
        )

    suffix = Path(file.filename or "").suffix.lower()
    if suffix == ".xls":
        raise HTTPException(
            400,
            "This format (.xls) is not supported. Please save your file as .xlsx and try again.",
        )
    if suffix not in ALLOWED_SUFFIXES:
        raise HTTPException(400, "Only Excel files (.xlsx or .xlsm) are accepted.")

    UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
    run_id = history.create_run("preview_prices", file_name=file.filename or "")
    dest_dir = services.run_dir(run_id)
    dest = dest_dir / "upload.xlsx"

    written = 0
    with dest.open("wb") as out:
        while True:
            chunk = await file.read(1024 * 1024)
            if not chunk:
                break
            written += len(chunk)
            if written > MAX_UPLOAD_BYTES:
                out.close()
                dest.unlink(missing_ok=True)
                history.set_status(run_id, "failed", error="File too large (>100 MB).")
                raise HTTPException(413, "File too large (>100 MB).")
            out.write(chunk)

    job_id = runner.try_start(
        "preview_prices",
        services.preview_prices_job,
        run_id=run_id,
        upload_path=dest,
        cfg=cfg,
    )
    if job_id is None:
        history.set_status(run_id, "failed", error="Another job started first.")
        raise HTTPException(409, "Another job is running. Please wait.")

    return JSONResponse({"run_id": run_id, "job_id": job_id})


@router.post("/apply-prices/{preview_run_id}")
def apply_prices(
    preview_run_id: int,
    cfg: Config = Depends(get_cfg),
    runner: JobRunner = Depends(get_runner),
) -> JSONResponse:
    if _apply_disabled():
        raise HTTPException(403, "Apply is disabled in dev mode.")

    preview = history.get_run(preview_run_id)
    if preview is None or preview["kind"] != "preview_prices":
        raise HTTPException(404, "Price preview not found.")
    can_apply, reason = _apply_eligibility(preview)
    if not can_apply:
        status = 409 if "already" in reason or "running" in reason else 400
        raise HTTPException(status, reason)

    if runner.is_busy():
        raise HTTPException(409, "Another job is running. Please wait.")

    apply_run_id = history.create_run(
        "apply_prices",
        file_name=preview.get("file_name"),
        parent_run_id=preview_run_id,
    )
    job_id = runner.try_start(
        "apply_prices",
        services.apply_prices_job,
        run_id=apply_run_id,
        preview_run_id=preview_run_id,
        cfg=cfg,
    )
    if job_id is None:
        history.set_status(apply_run_id, "failed", error="Another job started first.")
        raise HTTPException(409, "Another job is running. Please wait.")

    return JSONResponse({"run_id": apply_run_id, "job_id": job_id})


# ------------------------------------------------- combined inventory+prices


@router.post("/upload-both")
async def upload_both(
    file: UploadFile,
    cfg: Config = Depends(get_cfg),
    runner: JobRunner = Depends(get_runner),
) -> JSONResponse:
    """Upload an Excel and preview BOTH inventory and prices in one run.

    If the file only has a Qty column (no final_price), the combined job
    gracefully degrades to inventory-only — the UI shows one section instead
    of two.
    """
    if runner.is_busy():
        raise HTTPException(409, "Another job is running. Please wait for it to finish.")

    if not SKU_CACHE_FILE.exists():
        raise HTTPException(
            400,
            "Product list has never been built. "
            "Press 'Refresh product list' on the dashboard first.",
        )

    suffix = Path(file.filename or "").suffix.lower()
    if suffix == ".xls":
        raise HTTPException(
            400,
            "This format (.xls) is not supported. Please save your file as .xlsx and try again.",
        )
    if suffix not in ALLOWED_SUFFIXES:
        raise HTTPException(400, "Only Excel files (.xlsx or .xlsm) are accepted.")

    UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
    run_id = history.create_run("preview_both", file_name=file.filename or "")
    dest_dir = services.run_dir(run_id)
    dest = dest_dir / "upload.xlsx"

    written = 0
    with dest.open("wb") as out:
        while True:
            chunk = await file.read(1024 * 1024)
            if not chunk:
                break
            written += len(chunk)
            if written > MAX_UPLOAD_BYTES:
                out.close()
                dest.unlink(missing_ok=True)
                history.set_status(run_id, "failed", error="File too large (>100 MB).")
                raise HTTPException(413, "File too large (>100 MB).")
            out.write(chunk)

    job_id = runner.try_start(
        "preview_both",
        services.preview_both_job,
        run_id=run_id,
        upload_path=dest,
        cfg=cfg,
    )
    if job_id is None:
        history.set_status(run_id, "failed", error="Another job started first.")
        raise HTTPException(409, "Another job is running. Please wait.")

    return JSONResponse({"run_id": run_id, "job_id": job_id})


@router.post("/apply-both/{preview_run_id}")
def apply_both(
    preview_run_id: int,
    cfg: Config = Depends(get_cfg),
    runner: JobRunner = Depends(get_runner),
) -> JSONResponse:
    if _apply_disabled():
        raise HTTPException(403, "Apply is disabled in dev mode.")

    preview = history.get_run(preview_run_id)
    if preview is None or preview["kind"] != "preview_both":
        raise HTTPException(404, "Combined preview not found.")
    can_apply, reason = _apply_eligibility(preview)
    if not can_apply:
        status = 409 if "already" in reason or "running" in reason else 400
        raise HTTPException(status, reason)

    if runner.is_busy():
        raise HTTPException(409, "Another job is running. Please wait.")

    apply_run_id = history.create_run(
        "apply_both",
        file_name=preview.get("file_name"),
        parent_run_id=preview_run_id,
    )
    job_id = runner.try_start(
        "apply_both",
        services.apply_both_job,
        run_id=apply_run_id,
        preview_run_id=preview_run_id,
        cfg=cfg,
    )
    if job_id is None:
        history.set_status(apply_run_id, "failed", error="Another job started first.")
        raise HTTPException(409, "Another job is running. Please wait.")

    return JSONResponse({"run_id": apply_run_id, "job_id": job_id})


# ------------------------------------------------------------------ cache refresh


@router.post("/cache/refresh")
def refresh_cache(
    cfg: Config = Depends(get_cfg),
    runner: JobRunner = Depends(get_runner),
) -> JSONResponse:
    if runner.is_busy():
        raise HTTPException(409, "Another job is running. Please wait.")
    run_id = history.create_run("cache_refresh")
    job_id = runner.try_start(
        "cache_refresh",
        services.cache_refresh_job,
        run_id=run_id,
        cfg=cfg,
    )
    if job_id is None:
        history.set_status(run_id, "failed", error="Another job started first.")
        raise HTTPException(409, "Another job is running. Please wait.")
    return JSONResponse({"run_id": run_id, "job_id": job_id})


@router.get("/cache/status")
def cache_status() -> JSONResponse:
    meta = history.get_cache_meta() or {}
    return JSONResponse(meta)


# ---------------------------------------------------------------------- status


@router.get("/status/{job_id}")
def job_status(job_id: str, runner: JobRunner = Depends(get_runner)) -> JSONResponse:
    job = runner.get(job_id)
    if job is None:
        raise HTTPException(404, "Job not found.")
    return JSONResponse(job)


# ---------------------------------------------------------------- preview query


@router.get("/run/{run_id}/preview")
def run_preview(run_id: int, page: int = 1, q: str = "", per_page: int = 50) -> JSONResponse:
    run = history.get_run(run_id)
    if run is None:
        raise HTTPException(404, "Run not found.")
    if run.get("details_purged"):
        raise HTTPException(410, "Details for this run have expired.")

    path = RUNS_DIR / str(run_id) / "preview.json"
    if not path.exists():
        return JSONResponse({"rows": [], "total": 0, "page": 1, "per_page": per_page})

    rows = json.loads(path.read_text(encoding="utf-8"))

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
        "rows": rows[start:start + per_page],
        "total": total,
        "page": page,
        "per_page": per_page,
    })


@router.get("/run/{run_id}/preview-prices")
def run_preview_prices(run_id: int, page: int = 1, q: str = "", per_page: int = 50) -> JSONResponse:
    """Paginated price rows for a combined preview_both run.

    Mirrors /run/{id}/preview but reads preview_prices.json. Used by the
    combined preview page to show two tables side by side.
    """
    run = history.get_run(run_id)
    if run is None:
        raise HTTPException(404, "Run not found.")
    if run.get("details_purged"):
        raise HTTPException(410, "Details for this run have expired.")

    path = RUNS_DIR / str(run_id) / "preview_prices.json"
    if not path.exists():
        return JSONResponse({"rows": [], "total": 0, "page": 1, "per_page": per_page})

    rows = json.loads(path.read_text(encoding="utf-8"))

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
        "rows": rows[start:start + per_page],
        "total": total,
        "page": page,
        "per_page": per_page,
    })


# --------------------------------------------------------------- aggregates


@router.get("/run/{run_id}/aggregates")
def run_aggregates(run_id: int) -> JSONResponse:
    """Chart-ready summaries: match breakdown + change direction counts."""
    run = history.get_run(run_id)
    if run is None:
        raise HTTPException(404, "Run not found.")
    counts = run.get("counts") or {}

    match_breakdown = {
        "matched": counts.get("matched", 0),
        "unmatched": counts.get("unmatched", 0),
        "bad_qty": counts.get("bad_qty", 0),
        "duplicate": counts.get("duplicate", 0),
        "untracked": counts.get("untracked", 0),
    }

    change_direction = {"up": 0, "down": 0, "to_zero": 0, "no_change": 0, "unknown": 0}
    path = RUNS_DIR / str(run_id) / "preview.json"
    if path.exists():
        rows = json.loads(path.read_text(encoding="utf-8"))
        for r in rows:
            diff = r.get("diff")
            new_qty = r.get("new_qty")
            if diff is None:
                change_direction["unknown"] += 1
            elif diff == 0:
                change_direction["no_change"] += 1
            elif diff > 0:
                change_direction["up"] += 1
            elif new_qty == 0:
                change_direction["to_zero"] += 1
            else:
                change_direction["down"] += 1

    return JSONResponse({
        "match_breakdown": match_breakdown,
        "change_direction": change_direction,
    })


# ------------------------------------------------------------------ CSV download


@router.get("/run/{run_id}/problems.csv")
def problems_csv(run_id: int) -> FileResponse:
    run = history.get_run(run_id)
    if run is None:
        raise HTTPException(404, "Run not found.")
    path = RUNS_DIR / str(run_id) / "problems.csv"
    if not path.exists():
        raise HTTPException(404, "No problem rows for this run.")
    return FileResponse(
        path,
        media_type="text/csv",
        filename=f"problems_run_{run_id}.csv",
    )


# ----------------------------------------------------------------------- helpers


def _parse_iso(s: str) -> datetime:
    return datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)


def _apply_eligibility(run: dict) -> tuple[bool, str]:
    """Returns (can_apply, reason_if_not).

    Centralised so the frontend's apply button and the actual apply endpoint
    agree on the rules.
    """
    if run["kind"] not in {"preview", "preview_prices", "preview_both"}:
        return False, "Only previews can be applied."
    if run["status"] != "succeeded":
        return False, "Preview did not finish successfully."
    if run.get("applied_at"):
        return False, "This preview has already been applied."
    if run.get("details_purged"):
        return False, "Preview details have expired. Please upload again."
    created = _parse_iso(run["created_at"])
    if datetime.now(timezone.utc) - created > timedelta(hours=APPLY_WINDOW_HOURS):
        return False, (
            "This preview is more than 24 hours old. "
            "Shopify stock may have changed — please upload the file again."
        )
    return True, ""
