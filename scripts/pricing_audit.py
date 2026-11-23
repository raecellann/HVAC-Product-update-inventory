"""Read-only pricing audit: Shopify vs supplier Excel.

Pulls every variant from Shopify via bulkOperationRunQuery (read-only
GraphQL query inside a bulk op — no mutations run). Filters to ACTIVE
products. Joins against the supplier priced Excel on SKU. Computes
expected = cost * 1.30, splits results into cost<$150 / cost>=$150, and
writes a CSV + prints a summary.

Also reads delivery profiles to report what customers are charged on
$150+ orders.

NO writes to Shopify of any kind. No price pushes. No inventory changes.
"""
from __future__ import annotations

import csv
import json
import logging
import sys
import time
from decimal import Decimal, ROUND_HALF_UP, InvalidOperation
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import requests
from openpyxl import load_workbook

from config import PROJECT_ROOT, load_config
from shopify_client import ShopifyClient

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("pricing_audit")

ROOT = PROJECT_ROOT
SUPPLIER_XLSX = ROOT / "EQ8SGBP514YEZY_daily_20261119183001_priced.xlsx"
OUT_CSV = ROOT / "pricing_audit_2026-11-23.csv"
OUT_OVER150_HIGHER_CSV = ROOT / "pricing_audit_over150_higher_2026-11-23.csv"
SHIPPING_JSON = ROOT / "pricing_audit_shipping_2026-11-23.json"

MARKUP = Decimal("1.30")
THRESHOLD = Decimal("150")


def q2(d: Decimal) -> Decimal:
    return d.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


BULK_QUERY = """
mutation {
  bulkOperationRunQuery(
    query: \"\"\"
    {
      productVariants {
        edges {
          node {
            id
            sku
            price
            product {
              id
              title
              vendor
              status
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

CURRENT_BULK = """
query { currentBulkOperation { id status } }
"""

CANCEL_BULK = """
mutation cancel($id: ID!) {
  bulkOperationCancel(id: $id) {
    bulkOperation { id status }
    userErrors { field message }
  }
}
"""


DELIVERY_PROFILES_QUERY = """
{
  deliveryProfiles(first: 20) {
    edges {
      node {
        id
        name
        default
        profileLocationGroups {
          locationGroup {
            locations(first: 10) {
              edges { node { id name } }
            }
          }
          locationGroupZones(first: 25) {
            edges {
              node {
                zone {
                  id
                  name
                  countries { code { countryCode } provinces { code } }
                }
                methodDefinitions(first: 50) {
                  edges {
                    node {
                      id
                      name
                      active
                      description
                      rateProvider {
                        __typename
                        ... on DeliveryRateDefinition {
                          price { amount currencyCode }
                        }
                      }
                      methodConditions {
                        field
                        operator
                        conditionCriteria {
                          __typename
                          ... on MoneyV2 { amount currencyCode }
                          ... on Weight { value unit }
                        }
                      }
                    }
                  }
                }
              }
            }
          }
        }
      }
    }
  }
}
"""


def load_supplier_costs() -> dict[str, dict]:
    """Returns {sku_lower: {cost, raw_sku, final_price_supplier}}."""
    log.info("Loading supplier Excel: %s", SUPPLIER_XLSX.name)
    wb = load_workbook(SUPPLIER_XLSX, read_only=True, data_only=True)
    ws = wb["Daily Inventory"]
    rows = ws.iter_rows(values_only=True)
    header = next(rows)
    idx = {h: i for i, h in enumerate(header)}

    need = ["Item", "sell_price", "final_price", "price_plus_markup",
            "free_shipping", "shipping_fee", "Truck Shipment"]
    for n in need:
        if n not in idx:
            raise RuntimeError(f"Missing column {n!r} in supplier Excel. Got: {header}")

    out: dict[str, dict] = {}
    dupes = 0
    BLANK_RUN_LIMIT = 2000
    blank_run = 0
    for r in rows:
        if r is None:
            blank_run += 1
            if blank_run >= BLANK_RUN_LIMIT:
                break
            continue
        item = r[idx["Item"]]
        cost = r[idx["sell_price"]]
        if item is None and cost is None:
            blank_run += 1
            if blank_run >= BLANK_RUN_LIMIT:
                break
            continue
        blank_run = 0
        if item is None or cost is None:
            continue
        sku = str(item).strip()
        if not sku:
            continue
        try:
            cost_d = Decimal(str(cost))
        except (InvalidOperation, ValueError):
            continue
        key = sku.lower()
        entry = {
            "raw_sku": sku,
            "cost": cost_d,
            "supplier_final_price": r[idx["final_price"]],
            "supplier_pm": r[idx["price_plus_markup"]],
            "supplier_free_ship": r[idx["free_shipping"]],
            "supplier_shipping_fee": r[idx["shipping_fee"]],
            "supplier_truck": r[idx["Truck Shipment"]],
        }
        if key in out:
            dupes += 1
            # last one wins; note duplicate
            out[key] = entry
        else:
            out[key] = entry
    wb.close()
    log.info("Supplier rows loaded: %d unique SKUs, %d duplicate overwrites", len(out), dupes)
    return out


def cancel_existing_bulk(client: ShopifyClient) -> None:
    try:
        data = client.graphql(CURRENT_BULK)
        op = data.get("currentBulkOperation")
        if op and op.get("status") in {"CREATED", "RUNNING"}:
            log.warning("A bulk op is already %s (id=%s). Cancelling so we can start a fresh query.",
                        op["status"], op["id"])
            client.graphql(CANCEL_BULK, {"id": op["id"]})
            # Wait briefly for cancel
            for _ in range(30):
                time.sleep(2)
                d2 = client.graphql(CURRENT_BULK)
                st = (d2.get("currentBulkOperation") or {}).get("status")
                if st in {"CANCELED", "COMPLETED", "FAILED", "EXPIRED", None}:
                    break
    except Exception as e:
        log.warning("Could not inspect/cancel existing bulk op: %s", e)


def start_bulk(client: ShopifyClient) -> None:
    data = client.graphql(BULK_QUERY)
    op = data["bulkOperationRunQuery"]
    errs = op.get("userErrors") or []
    if errs:
        raise RuntimeError(f"Failed to start bulk query: {errs}")
    log.info("Started bulk operation %s", op["bulkOperation"]["id"])


def wait_for_bulk(client: ShopifyClient) -> str:
    while True:
        data = client.graphql(POLL_QUERY)
        op = data.get("currentBulkOperation")
        if not op:
            raise RuntimeError("No current bulk operation found")
        status = op["status"]
        count = op.get("objectCount") or 0
        log.info("Bulk status=%s objects=%s", status, count)
        if status == "COMPLETED":
            return op.get("url") or ""
        if status in {"FAILED", "CANCELED", "EXPIRED"}:
            raise RuntimeError(f"Bulk ended: status={status} error={op.get('errorCode')}")
        time.sleep(10)


def download_jsonl(url: str) -> list[dict]:
    if not url:
        return []
    log.info("Downloading bulk JSONL ...")
    resp = requests.get(url, stream=True, timeout=600)
    resp.raise_for_status()
    rows = []
    for line in resp.iter_lines(decode_unicode=True):
        if not line:
            continue
        rows.append(json.loads(line))
    log.info("Downloaded %d bulk rows", len(rows))
    return rows


def main() -> int:
    cfg = load_config()
    client = ShopifyClient(cfg)

    supplier = load_supplier_costs()

    cancel_existing_bulk(client)
    start_bulk(client)
    url = wait_for_bulk(client)
    bulk_rows = download_jsonl(url)

    # Two row types come back: ProductVariant and Product (parent). Variants
    # have 'sku'/'price'; parent product edges also appear via __parentId when
    # nested. Here we query productVariants.edges.node with product {...}, so
    # each line should be a variant with embedded product. But bulk ops flatten
    # nested connections — so parent product nodes may also appear. Filter to
    # rows that look like variants (have 'sku' key AND numeric 'price').
    log.info("Processing %d rows ...", len(bulk_rows))

    total_variants = 0
    active_variants = 0
    matched = 0
    unmatched_store_skus: list[dict] = []
    results: list[dict] = []

    for row in bulk_rows:
        # Variant nodes have 'price' field. Product nodes have 'vendor'/'status'
        # but no 'price'. Variants also have 'product' sub-object.
        if "price" not in row or "sku" not in row:
            continue
        product = row.get("product") or {}
        # When bulk flattens, 'product' may be absent on variant row. If so we
        # can't filter by status; skip those. Our query embeds product fields
        # so 'product' should be present.
        total_variants += 1
        status = (product.get("status") or "").upper()
        if status != "ACTIVE":
            continue
        active_variants += 1

        sku_raw = (row.get("sku") or "").strip()
        price_raw = row.get("price")
        try:
            store_price = Decimal(str(price_raw))
        except (InvalidOperation, ValueError, TypeError):
            store_price = None

        if not sku_raw:
            unmatched_store_skus.append({"sku": "", "title": product.get("title", ""),
                                         "vendor": product.get("vendor", ""),
                                         "store_price": str(store_price) if store_price is not None else "",
                                         "reason": "blank SKU"})
            continue

        hit = supplier.get(sku_raw.lower())
        if not hit:
            unmatched_store_skus.append({"sku": sku_raw, "title": product.get("title", ""),
                                         "vendor": product.get("vendor", ""),
                                         "store_price": str(store_price) if store_price is not None else "",
                                         "reason": "SKU not in supplier file"})
            continue

        matched += 1
        cost = hit["cost"]
        expected = q2(cost * MARKUP)
        diff = None
        if store_price is not None:
            diff = q2(store_price - expected)
        group = "under_150" if cost < THRESHOLD else "over_150"

        results.append({
            "sku": sku_raw,
            "title": product.get("title", ""),
            "vendor": product.get("vendor", ""),
            "cost": str(cost),
            "store_price": str(store_price) if store_price is not None else "",
            "expected": str(expected),
            "difference": str(diff) if diff is not None else "",
            "group": group,
        })

    log.info("Total variants seen: %d", total_variants)
    log.info("Active variants   : %d", active_variants)
    log.info("Matched to supplier: %d", matched)
    log.info("Unmatched         : %d (store SKUs not in supplier file)", len(unmatched_store_skus))

    # ---- write main CSV
    with OUT_CSV.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=[
            "sku", "title", "vendor", "cost", "store_price", "expected", "difference", "group"
        ])
        w.writeheader()
        w.writerows(results)
    log.info("Wrote %s (%d rows)", OUT_CSV.name, len(results))

    # ---- buckets
    def classify(rows, group):
        exact = higher = lower = missing = 0
        higher_rows = []
        for r in rows:
            if r["group"] != group:
                continue
            if r["difference"] == "":
                missing += 1
                continue
            d = Decimal(r["difference"])
            if d == 0:
                exact += 1
            elif d > 0:
                higher += 1
                higher_rows.append(r)
            else:
                lower += 1
        return exact, higher, lower, missing, higher_rows

    e_u, h_u, l_u, m_u, _ = classify(results, "under_150")
    e_o, h_o, l_o, m_o, higher_over = classify(results, "over_150")

    print()
    print("=" * 70)
    print("PRICING AUDIT SUMMARY — read-only, no writes to Shopify")
    print("=" * 70)
    print()
    print(f"Variants pulled from Shopify     : {total_variants}")
    print(f"  ACTIVE variants                : {active_variants}")
    print(f"  Matched to supplier Excel SKU  : {matched}")
    print(f"  Unmatched (store SKU not in file): {len(unmatched_store_skus)}")
    print()
    print("Group: cost < $150")
    print(f"  Total matched variants         : {e_u + h_u + l_u + m_u}")
    print(f"  Store price == cost * 1.30     : {e_u}")
    print(f"  Store price >  cost * 1.30     : {h_u}")
    print(f"  Store price <  cost * 1.30     : {l_u}")
    if m_u: print(f"  Missing store price            : {m_u}")
    print()
    print("Group: cost >= $150 (should be cost*1.30 + shipping fee per rule)")
    print(f"  Total matched variants         : {e_o + h_o + l_o + m_o}")
    print(f"  Store price == cost * 1.30     : {e_o}")
    print(f"  Store price >  cost * 1.30     : {h_o}")
    print(f"  Store price <  cost * 1.30     : {l_o}")
    if m_o: print(f"  Missing store price            : {m_o}")
    print()

    # ---- analyze shipping-fee pattern among $150+ higher
    if higher_over:
        # Write dedicated CSV
        with OUT_OVER150_HIGHER_CSV.open("w", newline="", encoding="utf-8") as f:
            fieldnames = ["sku", "title", "vendor", "cost", "store_price", "expected",
                          "extra_over_markup"]
            w = csv.DictWriter(f, fieldnames=fieldnames)
            w.writeheader()
            for r in higher_over:
                w.writerow({
                    "sku": r["sku"], "title": r["title"], "vendor": r["vendor"],
                    "cost": r["cost"], "store_price": r["store_price"],
                    "expected": r["expected"], "extra_over_markup": r["difference"],
                })
        log.info("Wrote %s (%d rows)", OUT_OVER150_HIGHER_CSV.name, len(higher_over))

        # Bucket the extras
        from collections import Counter
        extras = [Decimal(r["difference"]) for r in higher_over]
        buckets = Counter()
        for e in extras:
            # bucket to nearest $1 for histogram feel
            buckets[int(e.quantize(Decimal("1"), rounding=ROUND_HALF_UP))] += 1
        top = buckets.most_common(15)
        print(f"$150+ 'higher' extras — top 15 buckets (rounded $):")
        for amt, cnt in top:
            print(f"  +${amt:>6}  x {cnt}")
        print(f"  min extra: ${min(extras)}   max extra: ${max(extras)}")

    # ---- shipping settings
    print()
    print("=" * 70)
    print("Shipping settings (delivery profiles)")
    print("=" * 70)
    try:
        ship = client.graphql(DELIVERY_PROFILES_QUERY)
        SHIPPING_JSON.write_text(json.dumps(ship, indent=2), encoding="utf-8")
        log.info("Wrote raw shipping JSON to %s", SHIPPING_JSON.name)

        for p_edge in ship["deliveryProfiles"]["edges"]:
            profile = p_edge["node"]
            print(f"\nProfile: {profile['name']} (default={profile['default']})")
            for plg in profile.get("profileLocationGroups") or []:
                locs = [e["node"]["name"]
                        for e in (plg.get("locationGroup") or {}).get("locations", {}).get("edges", [])]
                if locs:
                    print(f"  Locations: {', '.join(locs)}")
                for z_edge in (plg.get("locationGroupZones") or {}).get("edges", []):
                    zn = z_edge["node"]
                    zone = zn["zone"]
                    countries = ", ".join(
                        (c.get("code") or {}).get("countryCode", "?")
                        for c in zone.get("countries", [])
                    ) or "(no countries)"
                    print(f"  Zone: {zone['name']} [{countries}]")
                    for m_edge in (zn.get("methodDefinitions") or {}).get("edges", []):
                        m = m_edge["node"]
                        rp = m.get("rateProvider") or {}
                        price_part = ""
                        if rp.get("__typename") == "DeliveryRateDefinition":
                            price_part = f" @ {(rp.get('price') or {}).get('amount', '?')} {(rp.get('price') or {}).get('currencyCode', '')}"
                        elif rp.get("__typename"):
                            price_part = f" via {rp['__typename']}"
                        conds = m.get("methodConditions") or []
                        cond_strs = []
                        for c in conds:
                            cc = c.get("conditionCriteria") or {}
                            tn = cc.get("__typename")
                            if tn == "MoneyV2":
                                cond_strs.append(f"{c.get('field')} {c.get('operator')} {cc.get('amount')} {cc.get('currencyCode')}")
                            elif tn == "Weight":
                                cond_strs.append(f"{c.get('field')} {c.get('operator')} {cc.get('value')} {cc.get('unit')}")
                            else:
                                cond_strs.append(f"{c.get('field')} {c.get('operator')} ?")
                        cond_part = f"   (conditions: {'; '.join(cond_strs)})" if cond_strs else ""
                        print(f"    - {m['name']} [active={m['active']}]{price_part}{cond_part}")
    except Exception as e:
        log.exception("Failed to read delivery profiles: %s", e)

    return 0


if __name__ == "__main__":
    sys.exit(main())
