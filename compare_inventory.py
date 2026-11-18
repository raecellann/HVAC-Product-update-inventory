"""Compare Excel 'Qty Available' against current Shopify on-hand at the
target location. Writes a side-by-side CSV. **Does NOT update anything.**

Usage:
  python compare_inventory.py

Output:
  logs/compare_<timestamp>.csv   per-SKU rows: SKU, Excel Qty, Shopify Qty, Diff, Match
  logs/compare_<timestamp>.log   summary stats
"""
from __future__ import annotations

import csv
import json
import logging
import sys
import time
from datetime import datetime
from pathlib import Path

import requests
from openpyxl import load_workbook

from config import INPUT_DIR, LOGS_DIR, SKU_CACHE_FILE, load_config
from shopify_client import ShopifyClient


BULK_QUERY_TEMPLATE = """
mutation {
  bulkOperationRunQuery(
    query: \"\"\"
    {
      productVariants {
        edges {
          node {
            sku
            inventoryItem {
              id
              tracked
              inventoryLevel(locationId: "__LOCATION_ID__") {
                quantities(names: ["available"]) {
                  name
                  quantity
                }
              }
            }
          }
        }
      }
    }
    \"\"\"
  ) {
    bulkOperation { id status }
    userErrors { field message }
  }
}
"""

POLL_QUERY = """
query {
  currentBulkOperation {
    id
    status
    errorCode
    objectCount
    url
    completedAt
  }
}
"""


def pick_input_file() -> Path:
    files = [p for p in INPUT_DIR.iterdir()
             if p.is_file() and p.suffix.lower() in {".xlsx", ".xlsm", ".xls"}]
    if not files:
        raise FileNotFoundError(f"No Excel file found in {INPUT_DIR}")
    return max(files, key=lambda p: p.stat().st_mtime)


def read_excel_qtys(path: Path, sku_col: str, qty_col: str) -> dict[str, int]:
    """Return {sku_lower: qty_int}."""
    wb = load_workbook(path, read_only=True, data_only=True)
    ws = wb.active
    rows = ws.iter_rows(values_only=True)
    header = next(rows)
    header_lc = [(str(h).strip().lower() if h is not None else "") for h in header]
    sku_idx = header_lc.index(sku_col.strip().lower())
    qty_idx = header_lc.index(qty_col.strip().lower())

    out: dict[str, int] = {}
    for row in rows:
        if row is None or sku_idx >= len(row):
            continue
        sku = row[sku_idx]
        if sku is None:
            continue
        sku_s = str(sku).strip().lower()
        if not sku_s:
            continue
        q_raw = row[qty_idx] if qty_idx < len(row) else None
        try:
            q = int(float(q_raw)) if q_raw is not None else 0
        except (ValueError, TypeError):
            continue
        # If SKU appears multiple times in Excel, keep the last one
        out[sku_s] = q
    return out


def start_bulk_query(client: ShopifyClient, location_id: str) -> None:
    query = BULK_QUERY_TEMPLATE.replace("__LOCATION_ID__", location_id)
    data = client.graphql(query)
    op = data["bulkOperationRunQuery"]
    errs = op.get("userErrors") or []
    if errs:
        raise RuntimeError(f"Failed to start bulk query: {errs}")
    bulk = op["bulkOperation"]
    logging.info("Started bulk operation %s (status=%s)", bulk["id"], bulk["status"])


def wait_for_bulk(client: ShopifyClient, poll_interval: int = 10) -> str:
    while True:
        data = client.graphql(POLL_QUERY)
        op = data.get("currentBulkOperation")
        if not op:
            raise RuntimeError("No current bulk operation found")
        status = op["status"]
        count = op.get("objectCount") or 0
        logging.info("Bulk operation status=%s, objects=%s", status, count)
        if status == "COMPLETED":
            if not op.get("url"):
                return ""
            return op["url"]
        if status in {"FAILED", "CANCELED", "EXPIRED"}:
            raise RuntimeError(f"Bulk operation ended: status={status}, error={op.get('errorCode')}")
        time.sleep(poll_interval)


def download_jsonl(url: str) -> list[dict]:
    if not url:
        return []
    logging.info("Downloading bulk result...")
    resp = requests.get(url, stream=True, timeout=600)
    resp.raise_for_status()
    rows: list[dict] = []
    for line in resp.iter_lines(decode_unicode=True):
        if not line:
            continue
        rows.append(json.loads(line))
    logging.info("Downloaded %d rows", len(rows))
    return rows


def build_shopify_qty_map(rows: list[dict]) -> dict[str, int]:
    """
    Walk the bulk JSONL rows. Variant rows have `sku` and `inventoryItem`.
    Returns {sku_lower: shopify_qty_at_target_location}.
    """
    out: dict[str, int] = {}
    for row in rows:
        inv = row.get("inventoryItem")
        if not inv:
            continue
        sku = (row.get("sku") or "").strip()
        if not sku:
            continue
        level = inv.get("inventoryLevel")
        if not level:
            # Variant has no inventory at this location -> treat as 0
            out[sku.lower()] = 0
            continue
        qty = 0
        for q in (level.get("quantities") or []):
            if q.get("name") == "available":
                qty = int(q.get("quantity") or 0)
                break
        out[sku.lower()] = qty
    return out


def main() -> int:
    LOGS_DIR.mkdir(exist_ok=True)
    stamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")
    log_file = LOGS_DIR / f"compare_{stamp}.log"
    csv_file = LOGS_DIR / f"compare_{stamp}.csv"

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=[logging.FileHandler(log_file, encoding="utf-8"), logging.StreamHandler()],
    )
    log = logging.getLogger("compare_inventory")

    cfg = load_config()
    if not cfg.location_id:
        log.error("SHOPIFY_LOCATION_ID not set.")
        return 1

    log.info("Target location: %s", cfg.location_id)

    excel_path = pick_input_file()
    log.info("Reading Excel: %s", excel_path.name)
    excel_qtys = read_excel_qtys(excel_path, cfg.sku_column, cfg.qty_column)
    log.info("Excel has %d distinct SKUs with parseable qty", len(excel_qtys))

    # Load SKU -> inventoryItemId cache so we know which SKUs are in Shopify
    cache = json.loads(Path(SKU_CACHE_FILE).read_text(encoding="utf-8"))
    sku_map: dict[str, dict] = cache["skus"]

    # We intersect Excel SKUs with Shopify-cached SKUs -> the "matched" universe
    matched_skus = set(excel_qtys.keys()) & set(sku_map.keys())
    log.info("Matched SKUs (in both Excel and Shopify): %d", len(matched_skus))

    # Kick off bulk query to get CURRENT Shopify stock at target location
    client = ShopifyClient(cfg)
    log.info("Starting Shopify bulk query for current inventory at target location...")
    start_bulk_query(client, cfg.location_id)
    url = wait_for_bulk(client)
    rows = download_jsonl(url)
    shopify_qtys = build_shopify_qty_map(rows)
    log.info("Pulled Shopify stock for %d SKUs", len(shopify_qtys))

    # Compare
    same = 0
    differ = 0
    no_shopify_data = 0
    diffs: list[tuple[str, int, int, int]] = []  # (sku, excel, shopify, diff)
    biggest_diffs: list[tuple[str, int, int, int]] = []

    with csv_file.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["sku", "shopify_title", "excel_qty", "shopify_qty", "diff_excel_minus_shopify", "match"])
        for sku in sorted(matched_skus):
            excel_q = excel_qtys[sku]
            if sku not in shopify_qtys:
                no_shopify_data += 1
                hit = sku_map[sku]
                w.writerow([sku, hit.get("title", ""), excel_q, "", "", "NO_SHOPIFY_DATA"])
                continue
            sh_q = shopify_qtys[sku]
            diff = excel_q - sh_q
            match = "YES" if diff == 0 else "NO"
            if diff == 0:
                same += 1
            else:
                differ += 1
                diffs.append((sku, excel_q, sh_q, diff))
            hit = sku_map[sku]
            w.writerow([sku, hit.get("title", ""), excel_q, sh_q, diff, match])

    diffs.sort(key=lambda x: abs(x[3]), reverse=True)
    biggest_diffs = diffs[:20]

    log.info("")
    log.info("=" * 60)
    log.info("COMPARISON SUMMARY")
    log.info("=" * 60)
    log.info("SKUs compared:     %d", len(matched_skus))
    log.info("Already in sync:   %d (%.1f%%)", same, 100 * same / max(len(matched_skus), 1))
    log.info("Different:         %d (%.1f%%)", differ, 100 * differ / max(len(matched_skus), 1))
    log.info("No Shopify data:   %d", no_shopify_data)
    log.info("")
    if diffs:
        avg_diff = sum(d[3] for d in diffs) / len(diffs)
        total_excel = sum(d[1] for d in diffs)
        total_shopify = sum(d[2] for d in diffs)
        log.info("Of SKUs that differ:")
        log.info("  Excel would add/remove this many units in total: %+d", total_excel - total_shopify)
        log.info("  Average difference per SKU: %+.2f", avg_diff)
        log.info("")
        log.info("Top 20 biggest differences (Excel vs Shopify):")
        log.info("  %-20s %12s %12s %12s", "SKU", "Excel qty", "Shopify qty", "Diff")
        for sku, eq, sq, d in biggest_diffs:
            log.info("  %-20s %12d %12d %+12d", sku, eq, sq, d)
    log.info("")
    log.info("Per-SKU report: %s", csv_file.name)

    return 0


if __name__ == "__main__":
    sys.exit(main())
