"""Terminal-based price updater: read Excel, preview changes, push to Shopify.

Usage:
  python tools/update_prices_cli.py <path-to-xlsx>              # preview only (safe, no changes)
  python tools/update_prices_cli.py <path-to-xlsx> --push       # preview then push to Shopify

Reads the Shopify access token from .env (same as the web app).
Writes a diff CSV to logs/price_diff_<timestamp>.csv.
"""
from __future__ import annotations

import argparse
import csv
import json
import logging
import sys
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.config import LOGS_DIR, SKU_CACHE_FILE, load_config  # noqa: E402
from core.shopify_client import ShopifyClient  # noqa: E402
from core.update_prices import (  # noqa: E402
    build_shopify_price_map,
    download_jsonl,
    match_price_rows,
    push_price_batches,
    read_price_excel,
    start_price_bulk_query,
    wait_for_bulk,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
log = logging.getLogger("update_prices_cli")


def main() -> int:
    parser = argparse.ArgumentParser(description="Push Shopify price updates from Excel.")
    parser.add_argument("excel_path", help="Path to the Excel file (SKU + final_price columns)")
    parser.add_argument("--push", action="store_true",
                        help="Actually push to Shopify (default is preview-only).")
    parser.add_argument("--yes", action="store_true",
                        help="Skip the confirmation prompt when using --push.")
    args = parser.parse_args()

    excel_path = Path(args.excel_path)
    if not excel_path.exists():
        log.error("File not found: %s", excel_path)
        return 1

    cfg = load_config()
    log.info("Store: %s", cfg.store_domain)
    log.info("SKU column: %r | Price column: %r", cfg.sku_column, cfg.price_column)

    # ---- 1. Read Excel ----
    log.info("Reading %s ...", excel_path.name)
    rows = read_price_excel(excel_path, cfg.sku_column, cfg.price_column)
    log.info("Rows read: %d", len(rows))

    # ---- 2. Match against local SKU cache ----
    if not SKU_CACHE_FILE.exists():
        log.error("sku_cache.json missing. Run `python -m core.build_sku_cache` first.")
        return 1
    cache = json.loads(SKU_CACHE_FILE.read_text(encoding="utf-8"))
    log.info("Cache: %d unique SKUs, %d duplicates",
             len(cache["skus"]), len(cache.get("duplicate_skus", {})))

    match = match_price_rows(rows, cache)
    log.info(
        "Matched=%d | Unmatched=%d | Bad price=%d | Duplicate SKUs=%d",
        len(match.matched), len(match.unmatched),
        len(match.bad_price), len(match.duplicate_hits),
    )

    if not match.matched:
        log.warning("Nothing matched. Exiting.")
        return 0

    # ---- 3. Pull current Shopify prices + product IDs via bulk query ----
    log.info("Fetching current Shopify prices (this takes a minute)...")
    client = ShopifyClient(cfg)
    start_price_bulk_query(client)
    url = wait_for_bulk(client)
    bulk_rows = download_jsonl(url)
    price_info = build_shopify_price_map(bulk_rows)
    log.info("Pulled Shopify data for %d SKUs", len(price_info))

    # ---- 4. Build diff list (only rows with actual change) ----
    changes = []
    no_change = 0
    missing_product_id = 0
    for m in match.matched:
        sku_lc = m["sku"].lower()
        info = price_info.get(sku_lc) or {}
        current = info.get("price")
        new = m["new_price"]
        pid = info.get("product_id")
        if not pid:
            missing_product_id += 1
            continue
        if current is not None:
            try:
                diff = Decimal(new) - Decimal(current)
            except (InvalidOperation, ValueError):
                diff = None
            if diff is not None and diff == 0:
                no_change += 1
                continue
        changes.append({
            "sku": m["sku"],
            "variant_id": m["variant_id"],
            "product_id": pid,
            "current_price": current,
            "new_price": new,
        })

    log.info(
        "Price changes: %d | No change (same price): %d | Missing from Shopify fetch: %d",
        len(changes), no_change, missing_product_id,
    )

    # ---- 5. Write diff CSV ----
    LOGS_DIR.mkdir(exist_ok=True)
    stamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")
    diff_file = LOGS_DIR / f"price_diff_{stamp}.csv"
    with diff_file.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["sku", "current_price", "new_price", "diff"])
        for c in changes:
            cur = c["current_price"]
            diff = ""
            if cur is not None:
                try:
                    diff = str(Decimal(c["new_price"]) - Decimal(cur))
                except (InvalidOperation, ValueError):
                    pass
            w.writerow([c["sku"], cur if cur is not None else "", c["new_price"], diff])
    log.info("Diff CSV: %s", diff_file)

    if not changes:
        log.info("Nothing to push. Exiting.")
        return 0

    # ---- 6. Push (or stop at preview) ----
    if not args.push:
        log.info("Preview only (no changes made). Re-run with --push to update Shopify.")
        return 0

    if not args.yes:
        print()
        print(f"About to push {len(changes)} price changes to {cfg.store_domain}.")
        print("This cannot be undone. Review the CSV above first if you're not sure.")
        answer = input("Type 'yes' to proceed: ").strip().lower()
        if answer != "yes":
            log.info("Aborted by user.")
            return 0

    log.info("Pushing %d price changes to Shopify...", len(changes))
    result = push_price_batches(client, cfg, changes, log=log)
    log.info("Pushed: %d | Errors: %d", result.pushed, len(result.user_errors))

    if result.user_errors:
        err_file = LOGS_DIR / f"price_errors_{stamp}.json"
        err_file.write_text(json.dumps(result.user_errors, indent=2), encoding="utf-8")
        log.warning("Errors written to %s", err_file)
        return 2

    log.info("DONE.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
