"""Stage the real Shopify cache + a preview of the real Excel into dev-data.

For UI checking only. Does NOT push to Shopify.

What it does:
  1. Copies the production sku_cache.json into dev-data/ so dev UI sees real SKUs.
  2. Runs ONE Shopify bulk query (current qty + image URL per variant at the
     configured location).
  3. Reads the real Excel, matches rows against the cache, builds preview rows
     (with image_url + current qty + new qty + diff), and writes a new preview
     run into dev-data so you can open it at /run.html?id=<new_id>.

Run it:
  python scripts/use_real_data.py path\\to\\file.xlsx
  (defaults to the matching xlsx under input/)
"""
from __future__ import annotations

import json
import logging
import os
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT))

# dev-data is where the dev UI reads from
os.environ.setdefault("DATA_DIR", str(ROOT / "dev-data"))

from core import compare_inventory as cmp_mod  # noqa: E402
from core.build_sku_cache import build_cache as cache_build_cache  # noqa: E402
from core.config import RUNS_DIR, SKU_CACHE_FILE, load_config  # noqa: E402
from core.shopify_client import ShopifyClient  # noqa: E402
from core.update_inventory import load_sku_cache, match_rows, read_excel  # noqa: E402
from webapp import history  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("use_real_data")


def _iso_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _stage_production_cache(prod_cache: Path) -> None:
    """Copy the production sku_cache.json into dev-data if not already present."""
    if not prod_cache.exists():
        raise SystemExit(
            f"Production cache not found at {prod_cache}. "
            "Run `python -m core.build_sku_cache` first."
        )
    SKU_CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
    if SKU_CACHE_FILE.exists() and SKU_CACHE_FILE.stat().st_size == prod_cache.stat().st_size:
        log.info("dev-data cache already matches production (%s bytes) — not copying",
                 prod_cache.stat().st_size)
        return
    log.info("Copying %s -> %s", prod_cache, SKU_CACHE_FILE)
    shutil.copy2(prod_cache, SKU_CACHE_FILE)


def main() -> int:
    excel_arg = sys.argv[1] if len(sys.argv) > 1 else None
    excel_path = Path(excel_arg) if excel_arg else (
        ROOT / "3HVLUJ76N6EH1L.daily.20261115183002.xlsx"
    )
    if not excel_path.exists():
        raise SystemExit(f"Excel file not found: {excel_path}")

    cfg = load_config()
    if not cfg.location_id:
        raise SystemExit("SHOPIFY_LOCATION_ID not set in .env.")

    # 1. Stage production cache into dev-data so matching sees real SKUs.
    _stage_production_cache(ROOT / "sku_cache.json")

    # 2. Read the Excel.
    log.info("Reading Excel: %s", excel_path.name)
    rows = read_excel(excel_path, cfg.sku_column, cfg.qty_column, cfg.title_column)
    log.info("Excel rows: %d", len(rows))

    # 3. Match against cache.
    cache = load_sku_cache()
    sku_map: dict[str, dict] = cache["skus"]
    match = match_rows(rows, cache)
    log.info("Matched=%d  Unmatched=%d  BadQty=%d  Duplicate=%d  Untracked=%d",
             len(match.matched), len(match.unmatched), len(match.bad_qty),
             len(match.duplicate_hits), len(match.untracked_hits))

    # 4. One bulk query for current qty + images.
    client = ShopifyClient(cfg)
    log.info("Starting Shopify bulk query (current qty + image URLs)...")
    cmp_mod.start_bulk_query(client, cfg.location_id)
    url = cmp_mod.wait_for_bulk(client)
    bulk_rows = cmp_mod.download_jsonl(url)
    shopify_qtys = cmp_mod.build_shopify_qty_map(bulk_rows)
    shopify_images = cmp_mod.build_shopify_image_map(bulk_rows)
    log.info("Shopify returned qty for %d SKUs, images for %d SKUs",
             len(shopify_qtys), len(shopify_images))

    # 5. Build preview rows.
    preview_rows: list[dict] = []
    will_change = 0
    for m in match.matched:
        sku_lc = m["sku"].lower()
        cache_hit = sku_map.get(sku_lc, {})
        sh_q = shopify_qtys.get(sku_lc)
        diff = None if sh_q is None else m["quantity"] - sh_q
        if diff is not None and diff != 0:
            will_change += 1
        preview_rows.append({
            "sku": m["sku"],
            "title": cache_hit.get("title", ""),
            "image_url": shopify_images.get(sku_lc) or cache_hit.get("image_url", ""),
            "shopify_qty": sh_q,
            "new_qty": m["quantity"],
            "diff": diff,
        })

    # 6. Write run into dev-data/history + runs/<id>/preview.json.
    history.init_db()
    run_id = history.create_run("preview", file_name=excel_path.name)
    d = RUNS_DIR / str(run_id)
    d.mkdir(parents=True, exist_ok=True)
    (d / "preview.json").write_text(json.dumps(preview_rows), encoding="utf-8")
    # Also copy the Excel in so the apply flow could pick it up (dev apply is disabled anyway).
    shutil.copy2(excel_path, d / "upload.xlsx")

    history.record_counts(run_id, {
        "excel_rows": len(rows),
        "matched": len(match.matched),
        "will_change": will_change,
        "unmatched": len(match.unmatched),
        "bad_qty": len(match.bad_qty),
        "duplicate": len(match.duplicate_hits),
        "untracked": len(match.untracked_hits),
        "title_mismatches": len(match.title_mismatches),
    })
    history.set_status(run_id, "succeeded")
    history.set_cache_meta(len(cache["skus"]), len(cache.get("duplicate_skus") or {}))

    log.info("")
    log.info("Done. Open:  http://localhost:5173/run.html?id=%d", run_id)
    log.info("Rows with images: %d / %d",
             sum(1 for r in preview_rows if r["image_url"]), len(preview_rows))
    return 0


if __name__ == "__main__":
    sys.exit(main())
