"""DRY-RUN price preview for the client's pricing rule.

Rule (applied to `sell_price` = supplier cost):
  cost <= 0             -> skip
  cost < 150            -> new_price = round(cost * 1.30, 2)
  cost >= 150           -> new_price = round(cost * 1.30, 2) + 150.00

Reads the supplier Excel (sheet "Daily Inventory"), pulls ACTIVE variants
from Shopify live (with vendor + current price), matches on SKU, writes
price_update_preview_<date>.csv, and prints the summary the client asked for.

No writes to Shopify.
"""
from __future__ import annotations

import csv
import json
import logging
import sys
import time
from collections import defaultdict
from datetime import date
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from pathlib import Path

import requests
from openpyxl import load_workbook

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT))

from config import load_config  # noqa: E402
from shopify_client import ShopifyClient  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("price_rule_dry_run")

EXCEL_PATH = ROOT / "EQ8SGBP514YEZY_daily_20261119183001_priced_with_shipping.xlsx"
SHEET_NAME = "Daily Inventory"
SKU_COL = "Item"
COST_COL = "sell_price"

CENT = Decimal("0.01")
MARKUP = Decimal("1.30")
BIG_ADDER = Decimal("150.00")
BIG_CUTOFF = Decimal("150")

# Bulk query: ACTIVE products only, pull variant id, sku, price, product title + vendor.
BULK_QUERY = """
mutation {
  bulkOperationRunQuery(
    query: \"\"\"
    {
      products(query: "status:active") {
        edges {
          node {
            id
            title
            vendor
            status
            variants {
              edges {
                node {
                  id
                  sku
                  price
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


def apply_rule(cost: Decimal) -> tuple[Decimal, str] | None:
    if cost <= 0:
        return None
    marked = (cost * MARKUP).quantize(CENT, rounding=ROUND_HALF_UP)
    if cost < BIG_CUTOFF:
        return marked, "under150"
    return marked + BIG_ADDER, "over150"


def coerce_decimal(raw) -> Decimal | None:
    if raw is None or raw == "":
        return None
    try:
        s = str(raw).strip().replace("$", "").replace(",", "")
        s = s.replace("USD", "").replace("usd", "").strip()
        if not s:
            return None
        return Decimal(s)
    except (InvalidOperation, ValueError, TypeError):
        return None


def read_supplier_file() -> list[dict]:
    """Returns rows like {row, sku, cost}. Blank SKU / bad-cost rows dropped silently
    but counted. Zero/negative cost rows are retained with cost <= 0 so the summary
    can report skips.
    """
    if not EXCEL_PATH.exists():
        raise FileNotFoundError(f"Supplier file not found: {EXCEL_PATH}")
    log.info("Reading %s (sheet %r)", EXCEL_PATH.name, SHEET_NAME)
    wb = load_workbook(EXCEL_PATH, read_only=True, data_only=True)
    if SHEET_NAME not in wb.sheetnames:
        wb.close()
        raise RuntimeError(f"Sheet {SHEET_NAME!r} not found; sheets: {wb.sheetnames}")
    ws = wb[SHEET_NAME]
    rows_iter = ws.iter_rows(values_only=True)
    header = next(rows_iter)
    hdr_lc = [(str(h).strip().lower() if h is not None else "") for h in header]

    def find(name: str) -> int:
        try:
            return hdr_lc.index(name.lower())
        except ValueError:
            raise RuntimeError(f"Column {name!r} not found. Header: {header}")

    sku_idx = find(SKU_COL)
    cost_idx = find(COST_COL)

    out: list[dict] = []
    blank_run = 0
    for i, row in enumerate(rows_iter, start=2):
        if row is None:
            blank_run += 1
            if blank_run >= 2000:
                break
            continue
        sku_v = row[sku_idx] if sku_idx < len(row) else None
        cost_v = row[cost_idx] if cost_idx < len(row) else None
        if sku_v is None and cost_v is None:
            blank_run += 1
            if blank_run >= 2000:
                break
            continue
        blank_run = 0
        sku = str(sku_v).strip() if sku_v is not None else ""
        if not sku:
            continue
        cost = coerce_decimal(cost_v)
        out.append({"row": i, "sku": sku, "cost": cost, "cost_raw": cost_v})
    wb.close()
    log.info("Supplier rows (non-blank SKU): %d", len(out))
    return out


def start_bulk(client: ShopifyClient) -> None:
    data = client.graphql(BULK_QUERY)
    op = data["bulkOperationRunQuery"]
    errs = op.get("userErrors") or []
    if errs:
        raise RuntimeError(f"bulkOperationRunQuery userErrors: {errs}")
    log.info("Bulk started: %s", op["bulkOperation"])


def wait_bulk(client: ShopifyClient) -> str:
    while True:
        data = client.graphql(POLL_QUERY)
        op = data.get("currentBulkOperation")
        if not op:
            raise RuntimeError("No current bulk operation")
        st = op["status"]
        log.info("Bulk status=%s objects=%s", st, op.get("objectCount"))
        if st == "COMPLETED":
            return op.get("url") or ""
        if st in {"FAILED", "CANCELED", "EXPIRED"}:
            raise RuntimeError(f"Bulk ended status={st} err={op.get('errorCode')}")
        time.sleep(10)


def download_jsonl(url: str) -> list[dict]:
    if not url:
        return []
    r = requests.get(url, stream=True, timeout=600)
    r.raise_for_status()
    out = []
    for line in r.iter_lines(decode_unicode=True):
        if line:
            out.append(json.loads(line))
    return out


def build_active_variant_map(rows: list[dict]) -> tuple[dict, dict]:
    """From bulk JSONL rows build:
       variants_by_sku: {sku_lower: {variant_id, sku, price, product_id, title, vendor}}
       dup_skus:         {sku_lower: [entry, entry, ...]}
       Only ACTIVE products are included. Blank SKUs ignored.
    """
    # Bulk JSONL has one row per node; children carry `__parentId` to link back.
    # Products have `status`, `title`, `vendor`; variants have `sku`, `price`, `__parentId`.
    products: dict[str, dict] = {}
    variants: list[dict] = []
    for r in rows:
        node_id = r.get("id") or ""
        if "/ProductVariant/" in node_id:
            variants.append(r)
        elif "/Product/" in node_id:
            products[node_id] = {
                "title": r.get("title") or "",
                "vendor": r.get("vendor") or "",
                "status": r.get("status") or "",
            }

    variants_by_sku: dict[str, dict] = {}
    dup_skus: dict[str, list[dict]] = {}
    non_active_skipped = 0
    blank_sku = 0
    for v in variants:
        pid = v.get("__parentId") or ""
        prod = products.get(pid)
        if not prod:
            continue
        if (prod.get("status") or "").upper() != "ACTIVE":
            non_active_skipped += 1
            continue
        sku = (v.get("sku") or "").strip()
        if not sku:
            blank_sku += 1
            continue
        entry = {
            "variant_id": v["id"],
            "sku": sku,
            "price": v.get("price"),
            "product_id": pid,
            "title": prod["title"],
            "vendor": prod["vendor"],
        }
        key = sku.lower()
        if key in dup_skus:
            dup_skus[key].append(entry)
        elif key in variants_by_sku:
            dup_skus[key] = [variants_by_sku.pop(key), entry]
        else:
            variants_by_sku[key] = entry
    log.info(
        "Active variants: %d unique, %d dup-SKUs, %d blank-SKU, %d non-active variants skipped",
        len(variants_by_sku), len(dup_skus), blank_sku, non_active_skipped,
    )
    return variants_by_sku, dup_skus


def main() -> int:
    cfg = load_config()
    log.info("Store: %s", cfg.store_domain)

    # 1. Supplier rows
    supplier = read_supplier_file()

    # Identify file-level duplicate SKUs
    file_sku_rows: dict[str, list[dict]] = defaultdict(list)
    for r in supplier:
        file_sku_rows[r["sku"].lower()].append(r)
    file_duplicates = {k: v for k, v in file_sku_rows.items() if len(v) > 1}
    log.info("Supplier-file duplicate SKUs: %d", len(file_duplicates))

    # 2. Live Shopify bulk pull (ACTIVE only)
    client = ShopifyClient(cfg)
    log.info("Starting Shopify bulk query (ACTIVE products)...")
    start_bulk(client)
    url = wait_bulk(client)
    bulk_rows = download_jsonl(url)
    log.info("Downloaded %d bulk rows", len(bulk_rows))
    variants_by_sku, store_duplicates = build_active_variant_map(bulk_rows)

    # 3. Classify
    matched: list[dict] = []
    skipped_cost_zero_or_neg: list[dict] = []
    skipped_bad_cost: list[dict] = []
    unmatched_in_store: list[dict] = []
    file_dup_skipped: list[dict] = []
    store_dup_skipped: list[dict] = []

    for r in supplier:
        key = r["sku"].lower()
        if key in file_duplicates:
            file_dup_skipped.append(r)
            continue
        if key in store_duplicates:
            store_dup_skipped.append(r)
            continue
        cost = r["cost"]
        if cost is None:
            skipped_bad_cost.append(r)
            continue
        if cost <= 0:
            skipped_cost_zero_or_neg.append(r)
            continue
        hit = variants_by_sku.get(key)
        if not hit:
            unmatched_in_store.append(r)
            continue
        ruled = apply_rule(cost)
        if ruled is None:
            skipped_cost_zero_or_neg.append(r)
            continue
        new_price, group = ruled
        # current price from store
        cur_raw = hit.get("price")
        try:
            current = Decimal(str(cur_raw)) if cur_raw not in (None, "") else None
        except (InvalidOperation, ValueError):
            current = None
        diff = (new_price - current) if current is not None else None
        matched.append({
            "variant_id": hit["variant_id"],
            "sku": hit["sku"],
            "title": hit["title"],
            "vendor": hit["vendor"],
            "product_id": hit["product_id"],
            "cost": cost,
            "current_price": current,
            "new_price": new_price,
            "diff": diff,
            "group": group,
        })

    # 4. Preview CSV
    today = date.today().isoformat()
    preview_csv = ROOT / f"price_update_preview_{today}.csv"
    with preview_csv.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow([
            "variant_id", "sku", "title", "vendor",
            "cost", "current_price", "new_price", "difference", "group",
        ])
        for m in matched:
            w.writerow([
                m["variant_id"], m["sku"], m["title"], m["vendor"],
                f"{m['cost']:.2f}",
                "" if m["current_price"] is None else f"{m['current_price']:.2f}",
                f"{m['new_price']:.2f}",
                "" if m["diff"] is None else f"{m['diff']:.2f}",
                m["group"],
            ])
    log.info("Preview CSV: %s", preview_csv)

    # Auxiliary CSVs for the skip buckets (helps audit)
    def write_aux(name: str, rows: list[dict], cols: list[str]) -> None:
        if not rows:
            return
        p = ROOT / f"price_update_{name}_{today}.csv"
        with p.open("w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(cols)
            for r in rows:
                w.writerow([r.get(c, "") for c in cols])
        log.info("%s CSV (%d rows): %s", name, len(rows), p)

    write_aux("file_duplicate_skus",
              [{"row": r["row"], "sku": r["sku"], "cost": r.get("cost") or ""} for r in file_dup_skipped],
              ["row", "sku", "cost"])
    write_aux("store_duplicate_skus",
              [{"row": r["row"], "sku": r["sku"], "cost": r.get("cost") or "",
                "variant_count": len(store_duplicates[r["sku"].lower()])}
               for r in store_dup_skipped],
              ["row", "sku", "cost", "variant_count"])

    # 5. Summary
    changes_up = [m for m in matched if m["diff"] is not None and m["diff"] > 0]
    changes_down = [m for m in matched if m["diff"] is not None and m["diff"] < 0]
    unchanged = [m for m in matched if m["diff"] is not None and m["diff"] == 0]
    new_no_current = [m for m in matched if m["diff"] is None]

    def stats(rows: list[dict]) -> tuple[Decimal, Decimal]:
        diffs = [r["diff"] for r in rows if r["diff"] is not None]
        if not diffs:
            return Decimal("0.00"), Decimal("0.00")
        total = sum(diffs, Decimal("0"))
        avg = (total / Decimal(len(diffs))).quantize(CENT, ROUND_HALF_UP)
        return total.quantize(CENT, ROUND_HALF_UP), avg

    under = [m for m in matched if m["group"] == "under150"]
    over = [m for m in matched if m["group"] == "over150"]
    under_total, under_avg = stats(under)
    over_total, over_avg = stats(over)

    biggest_increases = sorted(
        (m for m in matched if m["diff"] is not None),
        key=lambda m: m["diff"],
        reverse=True,
    )[:20]
    decreases = sorted(changes_down, key=lambda m: m["diff"])

    def fmt_money(d):
        return "" if d is None else f"${d:.2f}"

    print()
    print("=" * 78)
    print(f"DRY RUN PRICE PREVIEW — HVAC MORE ({cfg.store_domain})   {today}")
    print("=" * 78)
    print(f"Supplier file:        {EXCEL_PATH.name}")
    print(f"Supplier rows read:   {len(supplier)}")
    print(f"  file-dup SKUs:      {len(file_dup_skipped)} rows ({len(file_duplicates)} unique SKUs)")
    print(f"  store-dup SKUs:     {len(store_dup_skipped)} rows ({len(store_duplicates)} unique SKUs in Shopify)")
    print(f"  cost <= 0 / blank:  {len(skipped_cost_zero_or_neg)} / {len(skipped_bad_cost)}")
    print(f"  SKU not in store:   {len(unmatched_in_store)}")
    print(f"Active Shopify SKUs:  {len(variants_by_sku)}")
    print()
    print(f"MATCHED (would preview): {len(matched)}")
    print(f"  would CHANGE price:   {len(changes_up) + len(changes_down)}  (up {len(changes_up)} / down {len(changes_down)})")
    print(f"  would stay the SAME:  {len(unchanged)}")
    print(f"  no current price:     {len(new_no_current)}")
    print()
    print("By pricing-rule group:")
    print(f"  under150:  {len(under):>6} items   total diff {fmt_money(under_total):>12}   avg {fmt_money(under_avg)}")
    print(f"  over150:   {len(over):>6} items   total diff {fmt_money(over_total):>12}   avg {fmt_money(over_avg)}")
    print()
    def fmt_row(m):
        sku = m["sku"]
        vendor = m["vendor"] or ""
        cur = f"${m['current_price']:.2f}" if m["current_price"] is not None else "-"
        new = f"${m['new_price']:.2f}"
        diff = f"${m['diff']:.2f}" if m["diff"] is not None else "-"
        group = m["group"]
        title = (m["title"] or "")[:40]
        return f"  {sku:<22.22} {vendor:<22.22} {cur:>9} {new:>9} {diff:>9} {group:<8} {title}"

    header = f"  {'SKU':<22} {'vendor':<22} {'cur':>9} {'new':>9} {'diff':>9} {'group':<8} title"
    print("Top 20 LARGEST INCREASES:")
    print(header)
    for m in biggest_increases:
        print(fmt_row(m))
    print()
    print(f"ALL PRICE DECREASES (new < current): {len(decreases)}")
    if decreases:
        print(header)
        for m in decreases:
            print(fmt_row(m))
    print()
    print(f"CSV written: {preview_csv}")
    print("Reply with 'APPROVED, PUSH PRICES' to push. Nothing has been changed yet.")
    print("=" * 78)
    return 0


if __name__ == "__main__":
    sys.exit(main())
