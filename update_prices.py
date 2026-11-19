"""Price update: read Excel (SKU + Price), match SKUs, push to Shopify.

Workflow mirrors update_inventory.py, but targets variant prices via
`productVariantsBulkUpdate`. Mutations are grouped by productId because
that mutation is per-product.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Callable

import requests
from openpyxl import load_workbook

from config import Config
from shopify_client import ShopifyClient, ShopifyUserError

BATCH_SIZE = 100  # variants per productVariantsBulkUpdate call (same product)

VARIANTS_BULK_UPDATE_MUTATION = """
mutation productVariantsBulkUpdate($productId: ID!, $variants: [ProductVariantsBulkInput!]!) {
  productVariantsBulkUpdate(productId: $productId, variants: $variants) {
    productVariants { id price }
    userErrors { field code message }
  }
}
"""

PRICE_BULK_QUERY = """
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
            image { url }
            product { id title featuredImage { url } }
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

PRICE_POLL_QUERY = """
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


def read_price_excel(path: Path, sku_col: str, price_col: str) -> list[dict]:
    """Returns rows with {sku, price_raw, row_number}."""
    wb = load_workbook(path, read_only=True, data_only=True)
    ws = wb.active

    rows_iter = ws.iter_rows(values_only=True)
    try:
        header = next(rows_iter)
    except StopIteration:
        return []

    header_lc = [(str(h).strip().lower() if h is not None else "") for h in header]

    def find_col(name: str) -> int | None:
        name_lc = name.strip().lower()
        if name_lc in header_lc:
            return header_lc.index(name_lc)
        return None

    sku_idx = find_col(sku_col)
    price_idx = find_col(price_col)

    if sku_idx is None:
        raise ValueError(f"SKU column '{sku_col}' not found. Header row: {header}")
    if price_idx is None:
        raise ValueError(f"Price column '{price_col}' not found. Header row: {header}")

    rows: list[dict] = []
    for i, row in enumerate(rows_iter, start=2):
        if row is None:
            continue
        sku = row[sku_idx] if sku_idx < len(row) else None
        price = row[price_idx] if price_idx < len(row) else None

        if sku is None and price is None:
            continue  # blank row

        rows.append({
            "row": i,
            "sku": (str(sku).strip() if sku is not None else ""),
            "price_raw": price,
        })

    return rows


def coerce_price(raw) -> str | None:
    """Return a normalized decimal string ('12.99') or None if not parseable.

    Accepts numbers, "12", "12.5", "$12.99", "12.99 USD", with commas.
    Rejects negatives and non-numeric strings.
    """
    if raw is None or raw == "":
        return None
    try:
        s = str(raw).strip()
        # strip currency symbols and common separators
        s = s.replace("$", "").replace(",", "").replace("USD", "").replace("usd", "").strip()
        if not s:
            return None
        d = Decimal(s)
    except (InvalidOperation, ValueError, TypeError):
        return None
    if d < 0:
        return None
    # Normalize to 2 decimal places — Shopify Money accepts strings like "12.99"
    return f"{d:.2f}"


@dataclass
class PriceMatchResult:
    matched: list[dict] = field(default_factory=list)
    unmatched: list[dict] = field(default_factory=list)
    bad_price: list[dict] = field(default_factory=list)
    duplicate_hits: list[dict] = field(default_factory=list)


@dataclass
class PricePushResult:
    pushed: int = 0
    user_errors: list[dict] = field(default_factory=list)


def match_price_rows(rows: list[dict], cache: dict) -> PriceMatchResult:
    """Classify Excel rows against the SKU cache.

    Pure function. Matched rows carry variant_id + sku + new_price. The
    product_id is NOT in the cache for older cache files, so callers that
    need it should pull it from the preview-time bulk query (see services).
    """
    sku_map: dict[str, dict] = cache["skus"]
    dup_skus: dict[str, list] = cache.get("duplicate_skus", {})

    result = PriceMatchResult()

    for r in rows:
        key = r["sku"].lower()
        if not key:
            result.bad_price.append({**r, "reason": "blank SKU"})
            continue

        price = coerce_price(r["price_raw"])
        if price is None:
            result.bad_price.append({**r, "reason": f"bad price: {r['price_raw']!r}"})
            continue

        if key in dup_skus:
            result.duplicate_hits.append(
                {**r, "reason": "SKU assigned to multiple variants in Shopify"}
            )
            continue

        hit = sku_map.get(key)
        if not hit:
            result.unmatched.append(r)
            continue

        result.matched.append({
            "sku": r["sku"],
            "variant_id": hit["variant_id"],
            "new_price": price,
            "row": r["row"],
        })

    return result


# ---------------- bulk fetch of current prices (for preview diffs) ----------------


def start_price_bulk_query(client: ShopifyClient) -> None:
    data = client.graphql(PRICE_BULK_QUERY)
    op = data["bulkOperationRunQuery"]
    errs = op.get("userErrors") or []
    if errs:
        raise RuntimeError(f"Failed to start bulk query: {errs}")


def wait_for_bulk(client: ShopifyClient, poll_interval: int = 10) -> str:
    while True:
        data = client.graphql(PRICE_POLL_QUERY)
        op = data.get("currentBulkOperation")
        if not op:
            raise RuntimeError("No current bulk operation found")
        status = op["status"]
        if status == "COMPLETED":
            return op.get("url") or ""
        if status in {"FAILED", "CANCELED", "EXPIRED"}:
            raise RuntimeError(
                f"Bulk operation ended: status={status}, error={op.get('errorCode')}"
            )
        time.sleep(poll_interval)


def download_jsonl(url: str) -> list[dict]:
    import json
    if not url:
        return []
    resp = requests.get(url, stream=True, timeout=600)
    resp.raise_for_status()
    rows: list[dict] = []
    for line in resp.iter_lines(decode_unicode=True):
        if not line:
            continue
        rows.append(json.loads(line))
    return rows


def build_shopify_price_map(rows: list[dict]) -> dict[str, dict]:
    """{sku_lower: {'price': '12.99', 'product_id': 'gid://...', 'image_url': '...'}}."""
    out: dict[str, dict] = {}
    for row in rows:
        sku = (row.get("sku") or "").strip()
        if not sku:
            continue
        product = row.get("product") or {}
        if not product.get("id"):
            continue  # not a variant node
        price_raw = row.get("price")
        price = coerce_price(price_raw) if price_raw is not None else None
        variant_img = (row.get("image") or {}).get("url")
        product_img = (product.get("featuredImage") or {}).get("url")
        out[sku.lower()] = {
            "price": price,
            "product_id": product["id"],
            "image_url": variant_img or product_img or "",
        }
    return out


# ------------------------------- push --------------------------------


def push_product_prices(
    client: ShopifyClient, product_id: str, variants: list[dict]
) -> list[dict]:
    """One productVariantsBulkUpdate call. Returns userErrors list."""
    payload = {
        "productId": product_id,
        "variants": [
            {"id": v["variant_id"], "price": v["new_price"]}
            for v in variants
        ],
    }
    data = client.graphql(VARIANTS_BULK_UPDATE_MUTATION, payload)
    result = data["productVariantsBulkUpdate"]
    return result.get("userErrors") or []


def push_price_batches(
    client: ShopifyClient,
    cfg: Config,
    matched: list[dict],
    progress_cb: Callable[[int, int], None] | None = None,
    log: logging.Logger | None = None,
) -> PricePushResult:
    """Push matched price rows, grouped by product_id.

    Each matched row must include `product_id`. If a product has more than
    BATCH_SIZE variants, we split that product across calls.
    """
    log = log or logging.getLogger(__name__)
    total = len(matched)
    result = PricePushResult()

    # Group by product_id. Preserve input ordering within each group.
    by_product: dict[str, list[dict]] = {}
    for m in matched:
        pid = m.get("product_id")
        if not pid:
            result.user_errors.append({"sku": m.get("sku"), "error": "missing product_id"})
            continue
        by_product.setdefault(pid, []).append(m)

    processed = 0
    product_count = 0
    for pid, variants in by_product.items():
        product_count += 1
        for i in range(0, len(variants), BATCH_SIZE):
            chunk = variants[i : i + BATCH_SIZE]
            try:
                errs = push_product_prices(client, pid, chunk)
            except ShopifyUserError as e:
                log.error("Product %s failed: %s", pid, e)
                result.user_errors.append({"product_id": pid, "error": str(e)})
                processed += len(chunk)
                if progress_cb:
                    progress_cb(result.pushed, total)
                continue
            if errs:
                for ue in errs:
                    log.warning("  userError on product %s: %s", pid, ue)
                    result.user_errors.append({"product_id": pid, **ue})
            result.pushed += len(chunk)
            processed += len(chunk)
            if progress_cb:
                progress_cb(result.pushed, total)
        if product_count % 50 == 0:
            log.info("Pushed %d / %d (%d products)", result.pushed, total, product_count)

    log.info("Pushed %d / %d total (%d products)", result.pushed, total, product_count)
    return result
