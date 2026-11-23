"""JSON API: upload, apply, status, cache refresh, paginated preview."""
from __future__ import annotations

import json
import os
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, UploadFile
from fastapi.responses import FileResponse, JSONResponse

from config import (
    CONFIRM_BIG_OOS_PCT,
    CONFIRM_BIG_PRICE_JUMP_PCT,
    RUNS_DIR,
    SKU_CACHE_FILE,
    UPLOADS_DIR,
    Config,
)

from .. import history, services
from ..deps import get_cfg, get_runner
from ..jobs import JobRunner

router = APIRouter(prefix="/api")

MAX_UPLOAD_BYTES = 100 * 1024 * 1024  # 100 MB
APPLY_WINDOW_HOURS = 24
ALLOWED_SUFFIXES = {".xlsx", ".xlsm", ".txt", ".csv"}


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
    """Runtime flags shared between the frontend and the backend.

    `disable_apply` toggles the environment badge and disables the push
    button. The confirm-dialog thresholds live here so the UI and the
    backend agree on when the "type PUSH" friction kicks in.
    """
    return JSONResponse({
        "disable_apply": _apply_disabled(),
        "confirm_big_oos_pct":        CONFIRM_BIG_OOS_PCT,
        "confirm_big_price_jump_pct": CONFIRM_BIG_PRICE_JUMP_PCT,
    })


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
            "This format (.xls) is not supported. Please save your file as .xlsx or export it as a .txt and try again.",
        )
    if suffix not in ALLOWED_SUFFIXES:
        raise HTTPException(
            400,
            "Accepted file types: Excel (.xlsx, .xlsm) or text (.txt, .csv).",
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
            "This format (.xls) is not supported. Please save your file as .xlsx or export it as a .txt and try again.",
        )
    if suffix not in ALLOWED_SUFFIXES:
        raise HTTPException(400, "Accepted file types: Excel (.xlsx, .xlsm) or text (.txt, .csv).")

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
            "This format (.xls) is not supported. Please save your file as .xlsx or export it as a .txt and try again.",
        )
    if suffix not in ALLOWED_SUFFIXES:
        raise HTTPException(400, "Accepted file types: Excel (.xlsx, .xlsm) or text (.txt, .csv).")

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


INVENTORY_FILTERS = {"all", "changing", "out_of_stock", "back_in_stock", "no_change"}
PRICE_FILTERS     = {"all", "price_up", "price_down", "price_no_change"}
SORT_KEYS         = {"sku", "title", "old", "new", "diff", "default"}


def _apply_inventory_filter(rows: list[dict], name: str) -> list[dict]:
    if name == "changing":
        return [r for r in rows if r.get("diff") not in (None, 0)]
    if name == "out_of_stock":
        return [r for r in rows if (r.get("shopify_qty") or 0) > 0 and r.get("new_qty") == 0]
    if name == "back_in_stock":
        return [r for r in rows if r.get("shopify_qty") == 0 and (r.get("new_qty") or 0) > 0]
    if name == "no_change":
        return [r for r in rows if r.get("diff") == 0]
    return rows


def _apply_price_filter(rows: list[dict], name: str) -> list[dict]:
    if name == "price_up":
        return [r for r in rows if (r.get("diff") or 0) > 0]
    if name == "price_down":
        return [r for r in rows if (r.get("diff") or 0) < 0]
    if name == "price_no_change":
        return [r for r in rows if r.get("diff") == 0]
    return rows


def _sort_rows(rows: list[dict], sort: str, order: str, *, is_price: bool) -> list[dict]:
    if sort in {"default", ""} or sort not in SORT_KEYS:
        return rows
    reverse = (order == "desc")

    def key_sku(r):   return (r.get("sku") or "").lower()
    def key_title(r): return (r.get("title") or "").lower()
    def key_old(r):   return r.get("shopify_price") if is_price else r.get("shopify_qty")
    def key_new(r):   return r.get("new_price") if is_price else r.get("new_qty")
    def key_diff(r):  return r.get("diff")

    picker = {
        "sku":   key_sku,
        "title": key_title,
        "old":   key_old,
        "new":   key_new,
        "diff":  key_diff,
    }[sort]

    # None sorts last regardless of direction, so the sort stays meaningful
    # when some rows have no Shopify value (unknown qty / unmatched price).
    return sorted(rows, key=lambda r: (picker(r) is None, picker(r) or 0), reverse=reverse)


@router.get("/run/{run_id}/preview")
def run_preview(
    run_id: int,
    page: int = 1,
    q: str = "",
    per_page: int = 50,
    filter: str = "all",
    sort: str = "default",
    order: str = "asc",
) -> JSONResponse:
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

    is_price = run.get("kind") == "preview_prices"
    f = (filter or "all").strip().lower()
    if is_price and f in PRICE_FILTERS:
        rows = _apply_price_filter(rows, f)
    elif not is_price and f in INVENTORY_FILTERS:
        rows = _apply_inventory_filter(rows, f)

    rows = _sort_rows(rows, (sort or "default").strip().lower(),
                      (order or "asc").strip().lower(), is_price=is_price)

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
def run_preview_prices(
    run_id: int,
    page: int = 1,
    q: str = "",
    per_page: int = 50,
    filter: str = "all",
    sort: str = "default",
    order: str = "asc",
) -> JSONResponse:
    """Paginated price rows for a combined preview_both run.

    Mirrors /run/{id}/preview but reads preview_prices.json. Used by the
    combined preview page to show two tables.
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

    f = (filter or "all").strip().lower()
    if f in PRICE_FILTERS:
        rows = _apply_price_filter(rows, f)

    rows = _sort_rows(rows, (sort or "default").strip().lower(),
                      (order or "asc").strip().lower(), is_price=True)

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


# --------------------------------------------------------- problem rows (JSON)


@router.get("/run/{run_id}/problems")
def run_problems(
    run_id: int,
    page: int = 1,
    per_page: int = 50,
    kind: str = "all",
    q: str = "",
) -> JSONResponse:
    """Paginated 'Rows we couldn't use' with a plain-language reason per row.

    Reads the additive `problems.json` written by preview jobs. Falls back to
    an empty list when the file doesn't exist (older runs, or runs that had
    no problem rows at all). The existing /problems.csv endpoint is
    untouched for users who want to download the full file.
    """
    run = history.get_run(run_id)
    if run is None:
        raise HTTPException(404, "Run not found.")
    if run.get("details_purged"):
        raise HTTPException(410, "Details for this run have expired.")

    path = RUNS_DIR / str(run_id) / "problems.json"
    if not path.exists():
        return JSONResponse({
            "rows": [], "total": 0, "page": 1, "per_page": per_page,
            "truncated_kinds": [], "per_kind_cap": 0,
        })

    payload = json.loads(path.read_text(encoding="utf-8"))
    # Writer wraps the list as {rows, truncated_kinds, per_kind_cap}; older
    # runs (if any) may have stored the bare list. Handle both.
    if isinstance(payload, list):
        rows = payload
        truncated_kinds: list[str] = []
        per_kind_cap = 0
    else:
        rows = payload.get("rows") or []
        truncated_kinds = payload.get("truncated_kinds") or []
        per_kind_cap = payload.get("per_kind_cap") or 0

    if kind and kind != "all":
        rows = [r for r in rows if r.get("kind") == kind]

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
        "truncated_kinds": truncated_kinds,
        "per_kind_cap":    per_kind_cap,
    })


# --------------------------------------------------------------- aggregates


def _inv_direction(rows: list[dict]) -> dict:
    """Classify inventory rows by direction. `back_in_stock` was 0, now > 0."""
    out = {"up": 0, "down": 0, "to_zero": 0, "back_in_stock": 0,
           "no_change": 0, "unknown": 0}
    for r in rows:
        diff = r.get("diff")
        sh = r.get("shopify_qty")
        new = r.get("new_qty")
        if diff is None:
            out["unknown"] += 1
        elif diff == 0:
            out["no_change"] += 1
        elif diff > 0:
            if sh == 0 and (new or 0) > 0:
                out["back_in_stock"] += 1
            else:
                out["up"] += 1
        elif new == 0:
            out["to_zero"] += 1
        else:
            out["down"] += 1
    return out


def _price_direction(rows: list[dict], big_jump_pct: float) -> dict:
    """Classify price rows by direction. `big_jump_count` is rows whose
    abs(new_price - old_price) / old_price exceeds `big_jump_pct`."""
    out = {"up": 0, "down": 0, "no_change": 0, "unknown": 0, "big_jump_count": 0}
    for r in rows:
        diff = r.get("diff")
        old = r.get("shopify_price")
        if diff is None:
            out["unknown"] += 1
        elif diff == 0:
            out["no_change"] += 1
        elif diff > 0:
            out["up"] += 1
        else:
            out["down"] += 1
        try:
            if old not in (None, 0, "0", "") and diff not in (None, 0):
                ratio = abs(float(diff)) / float(old)
                if ratio > big_jump_pct:
                    out["big_jump_count"] += 1
        except (TypeError, ValueError):
            pass
    return out


@router.get("/run/{run_id}/aggregates")
def run_aggregates(run_id: int) -> JSONResponse:
    """Chart-ready summaries over the whole run (not a page of rows).

    Returns:
      match_breakdown:  matched / unmatched / bad_qty / duplicate / untracked
      change_direction: up / down / to_zero / back_in_stock / no_change / unknown
                        (inventory; empty for price-only runs)
      price_direction:  up / down / no_change / unknown / big_jump_count
                        (prices; empty for inventory-only runs)
      summary:          the six tile values the preview page renders
    """
    run = history.get_run(run_id)
    if run is None:
        raise HTTPException(404, "Run not found.")
    counts = run.get("counts") or {}
    kind = run.get("kind") or ""

    # For preview_both, match counts for inventory live under counts["inventory"];
    # for the inventory-only kind they're at the top level. Prefer the nested
    # block so preview_both stays accurate.
    inv_counts = counts.get("inventory") if isinstance(counts.get("inventory"), dict) else counts
    match_breakdown = {
        "matched":   inv_counts.get("matched",   0),
        "unmatched": inv_counts.get("unmatched", 0),
        "bad_qty":   inv_counts.get("bad_qty",   0),
        "duplicate": inv_counts.get("duplicate", 0),
        "untracked": inv_counts.get("untracked", 0),
    }

    change_direction = {}
    price_direction  = {}

    preview_path        = RUNS_DIR / str(run_id) / "preview.json"
    preview_prices_path = RUNS_DIR / str(run_id) / "preview_prices.json"

    # preview.json's shape depends on the run kind. For `preview_prices` it
    # already contains price rows; for `preview` / `preview_both` it contains
    # inventory rows.
    if preview_path.exists():
        rows = json.loads(preview_path.read_text(encoding="utf-8"))
        if kind == "preview_prices":
            price_direction = _price_direction(rows, CONFIRM_BIG_PRICE_JUMP_PCT)
        else:
            change_direction = _inv_direction(rows)

    if preview_prices_path.exists():
        rows = json.loads(preview_prices_path.read_text(encoding="utf-8"))
        price_direction = _price_direction(rows, CONFIRM_BIG_PRICE_JUMP_PCT)

    items_changing  = (change_direction.get("up", 0) + change_direction.get("down", 0)
                       + change_direction.get("to_zero", 0) + change_direction.get("back_in_stock", 0)
                       + price_direction.get("up", 0) + price_direction.get("down", 0))

    summary = {
        "items_changing":     items_changing,
        "going_out_of_stock": change_direction.get("to_zero", 0),
        "back_in_stock":      change_direction.get("back_in_stock", 0),
        "price_up":           price_direction.get("up", 0),
        "price_down":         price_direction.get("down", 0),
        "unmatched_skus":     match_breakdown.get("unmatched", 0),
        "big_price_jump_count": price_direction.get("big_jump_count", 0),
    }

    return JSONResponse({
        "match_breakdown":  match_breakdown,
        "change_direction": change_direction,
        "price_direction":  price_direction,
        "summary":          summary,
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
