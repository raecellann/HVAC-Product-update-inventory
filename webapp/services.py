"""Job bodies: preview, apply, cache refresh.

Each takes a ShopifyClient factory (so tests can inject a fake) and a
progress callback. All file layout decisions for a run live here.
"""
from __future__ import annotations

import csv
import dataclasses
import json
import logging
import shutil
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Callable

from config import ARCHIVE_DIR, RUNS_DIR, SKU_CACHE_FILE, Config
from shopify_client import ShopifyClient
from update_inventory import (
    match_rows,
    push_batches,
    read_excel,
    load_sku_cache,
)
from update_prices import (
    build_shopify_price_map,
    download_jsonl as price_download_jsonl,
    match_price_rows,
    push_price_batches,
    read_price_excel,
    start_price_bulk_query,
    stream_match_price_rows,
    wait_for_bulk as price_wait_for_bulk,
)
import compare_inventory as cmp_mod
import build_sku_cache as cache_mod

from . import history, warnings as warning_flags

log = logging.getLogger(__name__)

# Injectable so tests can hand in a fake client.
ClientFactory = Callable[[Config], ShopifyClient]


def _default_factory(cfg: Config) -> ShopifyClient:
    return ShopifyClient(cfg)


def run_dir(run_id: int) -> Path:
    d = RUNS_DIR / str(run_id)
    d.mkdir(parents=True, exist_ok=True)
    return d


# -------------------------------------------------------------------- preview


def preview_job(
    *,
    run_id: int,
    upload_path: Path,
    cfg: Config,
    client_factory: ClientFactory = _default_factory,
    progress_cb: Callable[..., None] | None = None,
) -> None:
    """Read Excel → match SKUs → fetch Shopify quantities → write preview files.

    Never mutates Shopify. On success, records counts/warnings and marks the
    run 'succeeded'. On failure, records the error.
    """
    progress = progress_cb or (lambda *a, **k: None)
    history.set_status(run_id, "running")

    try:
        progress(0, 0, "Reading Excel")
        rows = read_excel(upload_path, cfg.sku_column, cfg.qty_column, cfg.title_column)

        progress(0, 0, "Loading product list")
        cache = load_sku_cache()
        sku_map: dict[str, dict] = cache["skus"]

        progress(0, 0, "Matching SKUs")
        match = match_rows(rows, cache)

        progress(0, 0, "Fetching current Shopify stock (this takes a minute)")
        client = client_factory(cfg)
        cmp_mod.start_bulk_query(client, cfg.location_id)
        url = cmp_mod.wait_for_bulk(client)
        bulk_rows = cmp_mod.download_jsonl(url)
        shopify_qtys = cmp_mod.build_shopify_qty_map(bulk_rows)
        shopify_images = cmp_mod.build_shopify_image_map(bulk_rows)

        progress(0, 0, "Building preview")
        preview_rows: list[dict] = []
        will_change = 0
        for m in match.matched:
            sku_lc = m["sku"].lower()
            cache_hit = sku_map.get(sku_lc, {})
            title = cache_hit.get("title", "")
            sh_q = shopify_qtys.get(sku_lc)
            diff = None if sh_q is None else m["quantity"] - sh_q
            if diff is not None and diff != 0:
                will_change += 1
            preview_rows.append({
                "sku": m["sku"],
                "title": title,
                "image_url": shopify_images.get(sku_lc) or cache_hit.get("image_url"),
                "shopify_qty": sh_q,
                "new_qty": m["quantity"],
                "diff": diff,
            })

        d = run_dir(run_id)
        (d / "preview.json").write_text(json.dumps(preview_rows), encoding="utf-8")
        _write_problems_csv(d / "problems.csv", match)

        counts = {
            "excel_rows": len(rows),
            "matched": len(match.matched),
            "will_change": will_change,
            "unmatched": len(match.unmatched),
            "bad_qty": len(match.bad_qty),
            "duplicate": len(match.duplicate_hits),
            "untracked": len(match.untracked_hits),
            "title_mismatches": len(match.title_mismatches),
        }
        flags = warning_flags.compute_warnings(len(rows), len(match.matched), preview_rows)

        history.record_counts(run_id, counts, flags)
        history.set_status(run_id, "succeeded")
        progress(0, 0, "Preview ready")

    except Exception as e:  # noqa: BLE001
        log.exception("preview_job failed")
        history.set_status(run_id, "failed", error=str(e))
        raise


# ---------------------------------------------------------------------- apply


def apply_job(
    *,
    run_id: int,
    preview_run_id: int,
    cfg: Config,
    client_factory: ClientFactory = _default_factory,
    progress_cb: Callable[..., None] | None = None,
) -> None:
    """Rebuild the batch from the preview + fresh cache, then push to Shopify.

    Forces dry_run=False and qty_mode='absolute' regardless of .env.
    """
    progress = progress_cb or (lambda *a, **k: None)
    history.set_status(run_id, "running")
    try:
        preview_run = history.get_run(preview_run_id)
        if preview_run is None:
            raise RuntimeError("Preview not found")
        d = RUNS_DIR / str(preview_run_id)
        preview_path = d / "preview.json"
        upload_path = d / "upload.xlsx"
        if not preview_path.exists():
            raise RuntimeError("Preview details are no longer available")

        preview_rows = json.loads(preview_path.read_text(encoding="utf-8"))

        progress(0, 0, "Loading product list")
        cache = load_sku_cache()
        sku_map: dict[str, dict] = cache["skus"]

        # Rebuild matched batch using current cache — don't trust ids that
        # may have shifted since the preview was captured.
        matched: list[dict] = []
        for r in preview_rows:
            hit = sku_map.get(r["sku"].lower())
            if not hit:
                continue  # SKU disappeared from cache since preview; skip silently
            matched.append({
                "sku": r["sku"],
                "inventory_item_id": hit["inventory_item_id"],
                "quantity": r["new_qty"],
                "row": 0,
            })

        cfg_apply = dataclasses.replace(cfg, dry_run=False, qty_mode="absolute")
        client = client_factory(cfg_apply)

        total = len(matched)
        progress(0, total, "Pushing updates")

        def _cb(pushed: int, t: int) -> None:
            progress(pushed, t, f"Pushed {pushed} of {t}")

        result = push_batches(client, cfg_apply, matched, progress_cb=_cb, log=log)

        history.record_apply(run_id, result.pushed, len(result.user_errors))
        history.mark_preview_applied(preview_run_id)

        # Archive the original upload for parity with the CLI workflow.
        if upload_path.exists():
            ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
            stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d_%H%M%S")
            archive_name = (
                preview_run.get("file_name") or f"upload_{preview_run_id}.xlsx"
            )
            dest = ARCHIVE_DIR / f"{Path(archive_name).stem}__{stamp}.xlsx"
            try:
                shutil.copy2(upload_path, dest)
            except OSError:
                log.warning("Could not archive uploaded file to %s", dest)

        if result.user_errors:
            (run_dir(run_id) / "user_errors.json").write_text(
                json.dumps(result.user_errors, indent=2), encoding="utf-8"
            )

        history.set_status(run_id, "succeeded")
        progress(result.pushed, total, "Done")

    except Exception as e:  # noqa: BLE001
        log.exception("apply_job failed")
        history.set_status(run_id, "failed", error=str(e))
        raise


# ------------------------------------------------------------- price preview


def preview_prices_job(
    *,
    run_id: int,
    upload_path: Path,
    cfg: Config,
    client_factory: ClientFactory = _default_factory,
    progress_cb: Callable[..., None] | None = None,
) -> None:
    """Read SKU+Price Excel → match → fetch current Shopify prices → write preview."""
    progress = progress_cb or (lambda *a, **k: None)
    history.set_status(run_id, "running")

    try:
        progress(0, 0, "Loading product list")
        cache = load_sku_cache()
        sku_map: dict[str, dict] = cache["skus"]

        d = run_dir(run_id)
        progress(0, 0, "Reading Excel and matching SKUs")
        # Stream-match instead of materializing all Excel rows. Peak memory is
        # proportional to matched rows (~70k), not the Excel row count (1M+).
        stream_result = stream_match_price_rows(
            upload_path, cfg.sku_column, cfg.price_column, cache,
            problems_csv_path=d / "problems.csv",
        )
        matched_rows = stream_result["matched"]

        progress(0, 0, "Fetching current Shopify prices (this takes a minute)")
        client = client_factory(cfg)
        start_price_bulk_query(client)
        url = price_wait_for_bulk(client)
        bulk_rows = price_download_jsonl(url)
        price_info = build_shopify_price_map(bulk_rows)
        del bulk_rows  # build_shopify_price_map empties it, release the handle

        progress(0, 0, "Building preview")
        preview_rows: list[dict] = []
        will_change = 0
        for m in matched_rows:
            sku_lc = m["sku"].lower()
            cache_hit = sku_map.get(sku_lc, {})
            title = cache_hit.get("title", "")
            info = price_info.get(sku_lc) or {}
            current = info.get("price")
            new = m["new_price"]
            diff = None
            if current is not None:
                try:
                    diff = float(Decimal(new) - Decimal(current))
                except (InvalidOperation, ValueError, TypeError):
                    diff = None
            if diff is not None and diff != 0:
                will_change += 1
            preview_rows.append({
                "sku": m["sku"],
                "title": title,
                "image_url": info.get("image_url") or cache_hit.get("image_url"),
                "shopify_price": current,
                "new_price": new,
                "diff": diff,
                "product_id": info.get("product_id"),
                "variant_id": m["variant_id"],
            })

        (d / "preview.json").write_text(json.dumps(preview_rows), encoding="utf-8")

        counts = {
            "excel_rows": stream_result["excel_row_count"],
            "matched": len(matched_rows),
            "will_change": will_change,
            "unmatched": stream_result["unmatched_count"],
            "bad_price": stream_result["bad_price_count"],
            "duplicate": stream_result["duplicate_count"],
        }

        history.record_counts(run_id, counts)
        history.set_status(run_id, "succeeded")
        progress(0, 0, "Preview ready")

    except Exception as e:  # noqa: BLE001
        log.exception("preview_prices_job failed")
        history.set_status(run_id, "failed", error=str(e))
        raise


# -------------------------------------------------------------- price apply


def apply_prices_job(
    *,
    run_id: int,
    preview_run_id: int,
    cfg: Config,
    client_factory: ClientFactory = _default_factory,
    progress_cb: Callable[..., None] | None = None,
) -> None:
    """Rebuild batch from preview rows, push prices to Shopify."""
    progress = progress_cb or (lambda *a, **k: None)
    history.set_status(run_id, "running")
    try:
        preview_run = history.get_run(preview_run_id)
        if preview_run is None:
            raise RuntimeError("Preview not found")
        d = RUNS_DIR / str(preview_run_id)
        preview_path = d / "preview.json"
        upload_path = d / "upload.xlsx"
        if not preview_path.exists():
            raise RuntimeError("Preview details are no longer available")

        preview_rows = json.loads(preview_path.read_text(encoding="utf-8"))

        matched: list[dict] = []
        for r in preview_rows:
            if not r.get("variant_id") or not r.get("product_id"):
                continue  # need both for the bulk update
            matched.append({
                "sku": r["sku"],
                "variant_id": r["variant_id"],
                "product_id": r["product_id"],
                "new_price": r["new_price"],
            })

        cfg_apply = dataclasses.replace(cfg, dry_run=False)
        client = client_factory(cfg_apply)

        total = len(matched)
        progress(0, total, "Pushing price updates")

        def _cb(pushed: int, t: int) -> None:
            progress(pushed, t, f"Pushed {pushed} of {t}")

        result = push_price_batches(client, cfg_apply, matched, progress_cb=_cb, log=log)

        history.record_apply(run_id, result.pushed, len(result.user_errors))
        history.mark_preview_applied(preview_run_id)

        if upload_path.exists():
            ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
            stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d_%H%M%S")
            archive_name = (
                preview_run.get("file_name") or f"prices_{preview_run_id}.xlsx"
            )
            dest = ARCHIVE_DIR / f"{Path(archive_name).stem}__prices_{stamp}.xlsx"
            try:
                shutil.copy2(upload_path, dest)
            except OSError:
                log.warning("Could not archive uploaded file to %s", dest)

        if result.user_errors:
            (run_dir(run_id) / "user_errors.json").write_text(
                json.dumps(result.user_errors, indent=2), encoding="utf-8"
            )

        history.set_status(run_id, "succeeded")
        progress(result.pushed, total, "Done")

    except Exception as e:  # noqa: BLE001
        log.exception("apply_prices_job failed")
        history.set_status(run_id, "failed", error=str(e))
        raise


# -------------------------------------------------- combined inventory+prices


def preview_both_job(
    *,
    run_id: int,
    upload_path: Path,
    cfg: Config,
    client_factory: ClientFactory = _default_factory,
    progress_cb: Callable[..., None] | None = None,
) -> None:
    """Preview inventory AND prices from a single Excel file.

    Writes `preview.json` (inventory rows) and, if the file has a price
    column, `preview_prices.json` (price rows). Counts are stored under
    nested keys `inventory` and `prices` so the UI can render both sections
    from one run. If the file has no price column, prices are skipped
    silently — the combined flow degrades to inventory-only.
    """
    progress = progress_cb or (lambda *a, **k: None)
    history.set_status(run_id, "running")
    d = run_dir(run_id)

    try:
        # ---------- inventory half ----------
        progress(0, 0, "Reading Excel")
        rows = read_excel(upload_path, cfg.sku_column, cfg.qty_column, cfg.title_column)

        progress(0, 0, "Loading product list")
        cache = load_sku_cache()
        sku_map: dict[str, dict] = cache["skus"]

        progress(0, 0, "Matching SKUs")
        match = match_rows(rows, cache)

        progress(0, 0, "Fetching current Shopify stock (this takes a minute)")
        client = client_factory(cfg)
        cmp_mod.start_bulk_query(client, cfg.location_id)
        url = cmp_mod.wait_for_bulk(client)
        bulk_rows = cmp_mod.download_jsonl(url)
        shopify_qtys = cmp_mod.build_shopify_qty_map(bulk_rows)
        shopify_images = cmp_mod.build_shopify_image_map(bulk_rows)
        del bulk_rows

        progress(0, 0, "Building inventory preview")
        inv_preview: list[dict] = []
        inv_will_change = 0
        for m in match.matched:
            sku_lc = m["sku"].lower()
            cache_hit = sku_map.get(sku_lc, {})
            title = cache_hit.get("title", "")
            sh_q = shopify_qtys.get(sku_lc)
            diff = None if sh_q is None else m["quantity"] - sh_q
            if diff is not None and diff != 0:
                inv_will_change += 1
            inv_preview.append({
                "sku": m["sku"],
                "title": title,
                "image_url": shopify_images.get(sku_lc) or cache_hit.get("image_url"),
                "shopify_qty": sh_q,
                "new_qty": m["quantity"],
                "diff": diff,
            })

        (d / "preview.json").write_text(json.dumps(inv_preview), encoding="utf-8")
        _write_problems_csv(d / "problems.csv", match)
        inv_flags = warning_flags.compute_warnings(len(rows), len(match.matched), inv_preview)

        inv_counts = {
            "excel_rows": len(rows),
            "matched": len(match.matched),
            "will_change": inv_will_change,
            "unmatched": len(match.unmatched),
            "bad_qty": len(match.bad_qty),
            "duplicate": len(match.duplicate_hits),
            "untracked": len(match.untracked_hits),
            "title_mismatches": len(match.title_mismatches),
        }

        # free the intermediate inventory structures before the price pass
        del rows, match, shopify_qtys, shopify_images, inv_preview

        # ---------- prices half (optional) ----------
        price_counts: dict | None = None
        has_prices = False
        try:
            progress(0, 0, "Matching SKUs for prices")
            stream_result = stream_match_price_rows(
                upload_path, cfg.sku_column, cfg.price_column, cache,
                problems_csv_path=d / "problems_prices.csv",
            )
            matched_rows = stream_result["matched"]

            progress(0, 0, "Fetching current Shopify prices (this takes a minute)")
            start_price_bulk_query(client)
            url = price_wait_for_bulk(client)
            bulk_rows = price_download_jsonl(url)
            price_info = build_shopify_price_map(bulk_rows)
            del bulk_rows

            progress(0, 0, "Building price preview")
            price_preview: list[dict] = []
            price_will_change = 0
            for m in matched_rows:
                sku_lc = m["sku"].lower()
                cache_hit = sku_map.get(sku_lc, {})
                title = cache_hit.get("title", "")
                info = price_info.get(sku_lc) or {}
                current = info.get("price")
                new = m["new_price"]
                diff = None
                if current is not None:
                    try:
                        diff = float(Decimal(new) - Decimal(current))
                    except (InvalidOperation, ValueError, TypeError):
                        diff = None
                if diff is not None and diff != 0:
                    price_will_change += 1
                price_preview.append({
                    "sku": m["sku"],
                    "title": title,
                    "image_url": info.get("image_url") or cache_hit.get("image_url"),
                    "shopify_price": current,
                    "new_price": new,
                    "diff": diff,
                    "product_id": info.get("product_id"),
                    "variant_id": m["variant_id"],
                })

            (d / "preview_prices.json").write_text(json.dumps(price_preview), encoding="utf-8")
            price_counts = {
                "excel_rows": stream_result["excel_row_count"],
                "matched": len(matched_rows),
                "will_change": price_will_change,
                "unmatched": stream_result["unmatched_count"],
                "bad_price": stream_result["bad_price_count"],
                "duplicate": stream_result["duplicate_count"],
            }
            has_prices = True
        except ValueError:
            # Price column not present in this Excel — carry on with inventory only.
            log.info("No price column in file — combined preview falls back to inventory-only")
            progress(0, 0, "No price column — inventory only")

        counts = {
            "inventory": inv_counts,
            "prices": price_counts,
            "has_prices": has_prices,
            # Flat fields mirror the inventory numbers so the runs list and
            # the existing "At a glance" cards don't need special-casing.
            "excel_rows": inv_counts["excel_rows"],
            "matched": inv_counts["matched"],
            "will_change": inv_counts["will_change"] + (
                price_counts["will_change"] if price_counts else 0
            ),
            "unmatched": inv_counts["unmatched"],
            "bad_qty": inv_counts["bad_qty"],
            "duplicate": inv_counts["duplicate"],
            "untracked": inv_counts["untracked"],
            "title_mismatches": inv_counts["title_mismatches"],
        }

        history.record_counts(run_id, counts, inv_flags)
        history.set_status(run_id, "succeeded")
        progress(0, 0, "Preview ready")

    except Exception as e:  # noqa: BLE001
        log.exception("preview_both_job failed")
        history.set_status(run_id, "failed", error=str(e))
        raise


def apply_both_job(
    *,
    run_id: int,
    preview_run_id: int,
    cfg: Config,
    client_factory: ClientFactory = _default_factory,
    progress_cb: Callable[..., None] | None = None,
) -> None:
    """Apply inventory then prices from a preview_both run.

    Both halves push with the live `cfg.dry_run=False` config regardless of
    the environment's .env. If either half fails mid-push, we still record
    what was pushed so the dashboard shows accurate counts.
    """
    progress = progress_cb or (lambda *a, **k: None)
    history.set_status(run_id, "running")
    try:
        preview_run = history.get_run(preview_run_id)
        if preview_run is None:
            raise RuntimeError("Preview not found")
        d = RUNS_DIR / str(preview_run_id)
        upload_path = d / "upload.xlsx"

        inv_preview_path = d / "preview.json"
        price_preview_path = d / "preview_prices.json"

        if not inv_preview_path.exists() and not price_preview_path.exists():
            raise RuntimeError("Preview details are no longer available")

        cache = load_sku_cache()
        sku_map: dict[str, dict] = cache["skus"]

        # Build both batches up front so we can show a correct total in progress.
        inv_batch: list[dict] = []
        if inv_preview_path.exists():
            for r in json.loads(inv_preview_path.read_text(encoding="utf-8")):
                hit = sku_map.get(r["sku"].lower())
                if not hit:
                    continue
                inv_batch.append({
                    "sku": r["sku"],
                    "inventory_item_id": hit["inventory_item_id"],
                    "quantity": r["new_qty"],
                    "row": 0,
                })

        price_batch: list[dict] = []
        if price_preview_path.exists():
            for r in json.loads(price_preview_path.read_text(encoding="utf-8")):
                if not r.get("variant_id") or not r.get("product_id"):
                    continue
                price_batch.append({
                    "sku": r["sku"],
                    "variant_id": r["variant_id"],
                    "product_id": r["product_id"],
                    "new_price": r["new_price"],
                })

        total = len(inv_batch) + len(price_batch)

        # ---------- push inventory ----------
        pushed_inv = 0
        errs_inv: list[dict] = []
        if inv_batch:
            cfg_apply = dataclasses.replace(cfg, dry_run=False, qty_mode="absolute")
            client_inv = client_factory(cfg_apply)
            progress(0, total, f"Pushing inventory (0 of {len(inv_batch)})")

            def _inv_cb(pushed: int, t: int) -> None:
                progress(pushed, total, f"Pushing inventory ({pushed} of {t})")

            r = push_batches(client_inv, cfg_apply, inv_batch, progress_cb=_inv_cb, log=log)
            pushed_inv = r.pushed
            errs_inv = r.user_errors or []

        # ---------- push prices ----------
        pushed_price = 0
        errs_price: list[dict] = []
        if price_batch:
            cfg_apply = dataclasses.replace(cfg, dry_run=False)
            client_price = client_factory(cfg_apply)
            progress(pushed_inv, total, f"Pushing prices (0 of {len(price_batch)})")

            def _price_cb(pushed: int, t: int) -> None:
                progress(pushed_inv + pushed, total,
                         f"Pushing prices ({pushed} of {t})")

            r = push_price_batches(client_price, cfg_apply, price_batch, progress_cb=_price_cb, log=log)
            pushed_price = r.pushed
            errs_price = r.user_errors or []

        if errs_inv or errs_price:
            (run_dir(run_id) / "user_errors.json").write_text(
                json.dumps({"inventory": errs_inv, "prices": errs_price}, indent=2),
                encoding="utf-8",
            )

        history.record_apply(run_id, pushed_inv + pushed_price, len(errs_inv) + len(errs_price))
        history.mark_preview_applied(preview_run_id)

        # Archive the original upload — parity with the single-mode flows.
        if upload_path.exists():
            ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
            stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d_%H%M%S")
            archive_name = (
                preview_run.get("file_name") or f"upload_{preview_run_id}.xlsx"
            )
            dest = ARCHIVE_DIR / f"{Path(archive_name).stem}__both_{stamp}.xlsx"
            try:
                shutil.copy2(upload_path, dest)
            except OSError:
                log.warning("Could not archive uploaded file to %s", dest)

        history.set_status(run_id, "succeeded")
        progress(pushed_inv + pushed_price, total, "Done")

    except Exception as e:  # noqa: BLE001
        log.exception("apply_both_job failed")
        history.set_status(run_id, "failed", error=str(e))
        raise


# -------------------------------------------------------------- cache refresh


def cache_refresh_job(
    *,
    run_id: int,
    cfg: Config,
    client_factory: ClientFactory = _default_factory,
    progress_cb: Callable[..., None] | None = None,
) -> None:
    """Rebuild sku_cache.json via Shopify Bulk Operations."""
    progress = progress_cb or (lambda *a, **k: None)
    history.set_status(run_id, "running")
    try:
        # Inject our factory by monkey-less path: call the pieces directly.
        client = client_factory(cfg)

        progress(0, 0, "Starting Shopify bulk query")
        try:
            cache_mod.start_bulk_query(client)
        except Exception as e:  # noqa: BLE001
            if "already in progress" not in str(e).lower():
                raise

        progress(0, 0, "Waiting for Shopify to finish")
        url = cache_mod.wait_for_bulk(client)

        progress(0, 0, "Downloading results")
        rows = cache_mod.download_jsonl(url)

        progress(0, 0, "Writing product list")
        cache = cache_mod.build_cache(rows)
        Path(SKU_CACHE_FILE).write_text(json.dumps(cache, indent=2), encoding="utf-8")

        history.set_cache_meta(len(cache["skus"]), len(cache["duplicate_skus"]))
        history.record_counts(run_id, {
            "sku_count": len(cache["skus"]),
            "duplicate_count": len(cache["duplicate_skus"]),
            "blank_sku_count": cache["blank_sku_count"],
            "untracked_count": cache["untracked_count"],
        })
        history.set_status(run_id, "succeeded")
        progress(0, 0, "Product list refreshed")

    except Exception as e:  # noqa: BLE001
        log.exception("cache_refresh_job failed")
        history.set_status(run_id, "failed", error=str(e))
        raise


# ------------------------------------------------------------------ utilities


def _write_problems_csv(path: Path, match) -> None:
    if not (match.unmatched or match.bad_qty or match.duplicate_hits or match.untracked_hits):
        return
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["category", "row", "sku", "title", "qty_raw", "reason_or_shopify_title"])
        for r in match.unmatched:
            w.writerow(["unmatched", r["row"], r["sku"], r["title"], r["qty_raw"], ""])
        for r in match.bad_qty:
            w.writerow(["bad_qty", r["row"], r["sku"], r["title"], r["qty_raw"], r["reason"]])
        for r in match.duplicate_hits:
            w.writerow(["duplicate_sku", r["row"], r["sku"], r["title"], r["qty_raw"], r["reason"]])
        for r in match.untracked_hits:
            w.writerow(["untracked", r["row"], r["sku"], r["title"], r["qty_raw"], r["shopify_title"]])


def _write_price_problems_csv(path: Path, match) -> None:
    if not (match.unmatched or match.bad_price or match.duplicate_hits):
        return
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["category", "row", "sku", "price_raw", "reason"])
        for r in match.unmatched:
            w.writerow(["unmatched", r["row"], r["sku"], r["price_raw"], ""])
        for r in match.bad_price:
            w.writerow(["bad_price", r["row"], r["sku"], r["price_raw"], r["reason"]])
        for r in match.duplicate_hits:
            w.writerow(["duplicate_sku", r["row"], r["sku"], r["price_raw"], r["reason"]])
