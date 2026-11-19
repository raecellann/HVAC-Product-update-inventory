"""Seed script for UI work.

Creates:
  - $DATA_DIR/sku_cache.json          — a tiny fake catalog
  - $DATA_DIR/history.sqlite          — run history with 4 example runs
  - $DATA_DIR/runs/<id>/preview.json  — detail files so the preview page renders
  - $DATA_DIR/runs/<id>/problems.csv  — for the "download problems" link

Run it via: `npm run seed`
"""
from __future__ import annotations

import json
import os
import random
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

# Make the project importable
HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT))

from config import DATA_DIR, RUNS_DIR, SKU_CACHE_FILE
from webapp import history


def iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def seed_cache() -> dict:
    cache = {
        "skus": {
            f"sku-{i:03d}": {
                "inventory_item_id": f"gid://shopify/InventoryItem/{1000+i}",
                "variant_id": f"gid://shopify/ProductVariant/{2000+i}",
                "title": f"Sample product {i:03d}",
                "tracked": True,
            }
            for i in range(60)
        },
        "duplicate_skus": {
            "sku-dup": [
                {"inventory_item_id": "gid://shopify/InventoryItem/9001", "title": "Dup one", "tracked": True},
                {"inventory_item_id": "gid://shopify/InventoryItem/9002", "title": "Dup two", "tracked": True},
            ]
        },
        "blank_sku_count": 0,
        "untracked_count": 0,
    }
    SKU_CACHE_FILE.write_text(json.dumps(cache, indent=2), encoding="utf-8")
    return cache


def make_preview_rows(cache, zero_out_ratio=0.0):
    rng = random.Random(42)
    rows = []
    skus = list(cache["skus"].items())
    zero_count = int(len(skus) * zero_out_ratio)
    for i, (sku, info) in enumerate(skus):
        shopify_qty = rng.randint(0, 50)
        if i < zero_count:
            new_qty = 0
        else:
            new_qty = max(0, shopify_qty + rng.randint(-5, 10))
        rows.append({
            "sku": sku.upper(),
            "title": info["title"],
            "shopify_qty": shopify_qty,
            "new_qty": new_qty,
            "diff": new_qty - shopify_qty,
        })
    return rows


def write_run_files(run_id: int, preview_rows: list[dict]) -> None:
    d = RUNS_DIR / str(run_id)
    d.mkdir(parents=True, exist_ok=True)
    (d / "preview.json").write_text(json.dumps(preview_rows), encoding="utf-8")
    (d / "upload.xlsx").write_bytes(b"PK\x03\x04fake-excel")
    with (d / "problems.csv").open("w", encoding="utf-8", newline="") as f:
        f.write("category,row,sku,title,qty_raw,reason_or_shopify_title\n")
        f.write("unmatched,10,NOT-IN-CACHE,Unknown widget,5,\n")
        f.write("bad_qty,11,SKU-BAD,Has bad qty,oops,bad quantity: 'oops'\n")


def seed_runs(cache) -> None:
    now = datetime.now(timezone.utc)
    rows_clean = make_preview_rows(cache, zero_out_ratio=0.0)
    rows_warn  = make_preview_rows(cache, zero_out_ratio=0.5)  # mass-zero warning

    # ------- 1. Clean preview, waiting to be applied --------
    rid_clean = history.create_run("preview", file_name="stock_clean.xlsx")
    _backdate_run(rid_clean, now - timedelta(hours=1))
    write_run_files(rid_clean, rows_clean)
    will_change_clean = sum(1 for r in rows_clean if r["diff"] != 0)
    history.record_counts(rid_clean, {
        "excel_rows": len(rows_clean),
        "matched": len(rows_clean),
        "will_change": will_change_clean,
        "unmatched": 1, "bad_qty": 1, "duplicate": 0, "untracked": 0, "title_mismatches": 0,
    }, warnings=[])
    history.set_status(rid_clean, "succeeded")

    # ------- 2. Preview with BOTH warnings (low match + mass zero) --------
    rid_warn = history.create_run("preview", file_name="stock_suspicious.xlsx")
    _backdate_run(rid_warn, now - timedelta(hours=2))
    write_run_files(rid_warn, rows_warn)
    will_change_warn = sum(1 for r in rows_warn if r["diff"] != 0)
    history.record_counts(rid_warn, {
        "excel_rows": 500,  # lots of rows in file, few matched → low-match flag
        "matched": len(rows_warn),
        "will_change": will_change_warn,
        "unmatched": 440, "bad_qty": 0, "duplicate": 0, "untracked": 0, "title_mismatches": 0,
    }, warnings=[
        "Only 12% of SKUs in your file match products in Shopify. The file may be wrong or the product list may need refreshing.",
        "50% of matched products would drop to 0 stock. Double-check this is what you want before applying.",
    ])
    history.set_status(rid_warn, "succeeded")

    # ------- 3. A preview that was applied (so there's an apply record too) --------
    rid_applied_prev = history.create_run("preview", file_name="stock_yesterday.xlsx")
    _backdate_run(rid_applied_prev, now - timedelta(days=1, hours=2))
    write_run_files(rid_applied_prev, rows_clean)
    history.record_counts(rid_applied_prev, {
        "excel_rows": len(rows_clean),
        "matched": len(rows_clean),
        "will_change": will_change_clean,
        "unmatched": 0, "bad_qty": 0, "duplicate": 0, "untracked": 0, "title_mismatches": 0,
    }, warnings=[])
    history.set_status(rid_applied_prev, "succeeded")
    history.mark_preview_applied(rid_applied_prev)

    rid_apply = history.create_run("apply", file_name="stock_yesterday.xlsx", parent_run_id=rid_applied_prev)
    _backdate_run(rid_apply, now - timedelta(days=1, hours=1, minutes=30))
    history.record_apply(rid_apply, pushed=len(rows_clean), user_errors=0)
    history.record_counts(rid_apply, {
        "excel_rows": len(rows_clean),
        "matched": len(rows_clean),
        "will_change": will_change_clean,
    }, warnings=[])
    history.set_status(rid_apply, "succeeded")

    # ------- 4. Interrupted run (restart killed it) --------
    rid_int = history.create_run("preview", file_name="stock_crashed.xlsx")
    _backdate_run(rid_int, now - timedelta(hours=6))
    history.set_status(rid_int, "interrupted", error="Interrupted by server restart")

    # Cache meta — mark fresh
    history.set_cache_meta(len(cache["skus"]), len(cache["duplicate_skus"]))


def _backdate_run(run_id: int, when: datetime) -> None:
    """history.create_run stamps `now` — override for seeded demo data."""
    import sqlite3
    from config import HISTORY_DB
    con = sqlite3.connect(HISTORY_DB, isolation_level=None)
    con.execute("UPDATE runs SET created_at=? WHERE id=?", (iso(when), run_id))
    con.close()


def main() -> int:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    print(f"Seeding into: {DATA_DIR}")

    # Fresh DB every seed so repeated runs are predictable.
    db = (DATA_DIR / "history.sqlite")
    for p in [db, db.with_suffix(".sqlite-wal"), db.with_suffix(".sqlite-shm")]:
        if p.exists():
            p.unlink()

    history.init_db()
    cache = seed_cache()
    seed_runs(cache)
    print("Done.")
    print(f"  {len(cache['skus'])} fake SKUs in sku_cache.json")
    print("  4 runs: clean preview, warnings preview, applied apply, interrupted preview")
    return 0


if __name__ == "__main__":
    sys.exit(main())
