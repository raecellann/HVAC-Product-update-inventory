"""Build / refresh the SKU -> inventoryItemId cache.

Uses Shopify's Bulk Operations API to fetch every variant in one background job,
then downloads the resulting JSONL and writes sku_cache.json.

Run this:
  - Once before the first update
  - Weekly (or whenever new SKUs are added) via Task Scheduler
  - Manually any time you add/rename products
"""
from __future__ import annotations

import json
import logging
import sys
import time
from pathlib import Path
from typing import Callable

import requests

from config import SKU_CACHE_FILE, Config, load_config
from shopify_client import ShopifyClient

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
log = logging.getLogger("build_sku_cache")


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
            displayName
            image { url }
            inventoryItem { id tracked }
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


def start_bulk_query(client: ShopifyClient) -> str:
    data = client.graphql(BULK_QUERY)
    op = data["bulkOperationRunQuery"]
    errs = op.get("userErrors") or []
    if errs:
        raise RuntimeError(f"Failed to start bulk query: {errs}")
    bulk = op["bulkOperation"]
    log.info("Started bulk operation %s (status=%s)", bulk["id"], bulk["status"])
    return bulk["id"]


def wait_for_bulk(client: ShopifyClient, poll_interval: int = 10) -> str:
    """Poll until the current bulk operation finishes. Returns the JSONL URL."""
    while True:
        data = client.graphql(POLL_QUERY)
        op = data.get("currentBulkOperation")
        if not op:
            raise RuntimeError("No current bulk operation found")
        status = op["status"]
        count = op.get("objectCount") or 0
        log.info("Bulk operation status=%s, objects=%s", status, count)
        if status == "COMPLETED":
            if not op.get("url"):
                log.warning("Bulk completed but no URL (empty result?); writing empty cache")
                return ""
            return op["url"]
        if status in {"FAILED", "CANCELED", "EXPIRED"}:
            raise RuntimeError(f"Bulk operation ended with status={status}, errorCode={op.get('errorCode')}")
        time.sleep(poll_interval)


def download_jsonl(url: str) -> list[dict]:
    if not url:
        return []
    log.info("Downloading bulk result...")
    resp = requests.get(url, stream=True, timeout=300)
    resp.raise_for_status()
    rows: list[dict] = []
    for line in resp.iter_lines(decode_unicode=True):
        if not line:
            continue
        rows.append(json.loads(line))
    log.info("Downloaded %d rows", len(rows))
    return rows


def build_cache(rows: list[dict]) -> dict:
    """
    Returns:
      {
        "skus": { "<sku>": { "inventory_item_id": "...", "variant_id": "...", "title": "..." } },
        "duplicate_skus": { "<sku>": [ {...}, {...} ] },
        "blank_sku_count": int,
        "untracked_count": int
      }
    """
    skus: dict[str, dict] = {}
    duplicates: dict[str, list[dict]] = {}
    blank = 0
    untracked = 0

    for row in rows:
        # Only process ProductVariant nodes. Bulk results don't tag type, but variant rows
        # have an `inventoryItem` field.
        inv = row.get("inventoryItem")
        if not inv:
            continue
        sku = (row.get("sku") or "").strip()
        if not sku:
            blank += 1
            continue
        if not inv.get("tracked"):
            untracked += 1
            # Still cache it so we can report later
        variant_img = (row.get("image") or {}).get("url")
        product_img = ((row.get("product") or {}).get("featuredImage") or {}).get("url")
        entry = {
            "inventory_item_id": inv["id"],
            "variant_id": row["id"],
            "title": (row.get("product") or {}).get("title") or row.get("displayName") or "",
            "tracked": bool(inv.get("tracked")),
            "image_url": variant_img or product_img or "",
        }
        key = sku.lower()
        if key in skus:
            duplicates.setdefault(key, [skus[key]]).append(entry)
        else:
            skus[key] = entry

    # Drop duplicates from the main map so we never guess
    for k in duplicates:
        skus.pop(k, None)

    return {
        "skus": skus,
        "duplicate_skus": duplicates,
        "blank_sku_count": blank,
        "untracked_count": untracked,
    }


def run_cache_refresh(
    cfg: Config,
    progress_cb: Callable[[str], None] | None = None,
    logger: logging.Logger | None = None,
) -> dict:
    """Full refresh flow: bulk query → poll → download → write sku_cache.json.

    Returns the cache dict that was written. progress_cb is called with
    short status strings so the web layer can display them live.
    """
    logger = logger or log
    client = ShopifyClient(cfg)

    if progress_cb:
        progress_cb("Starting bulk query")

    try:
        start_bulk_query(client)
    except Exception as e:
        if "already in progress" in str(e).lower():
            logger.warning("A bulk op is already running; waiting for it instead.")
        else:
            raise

    if progress_cb:
        progress_cb("Waiting for Shopify bulk operation to finish")

    url = wait_for_bulk(client)

    if progress_cb:
        progress_cb("Downloading results")

    rows = download_jsonl(url)
    cache = build_cache(rows)

    Path(SKU_CACHE_FILE).write_text(json.dumps(cache, indent=2), encoding="utf-8")
    logger.info(
        "Wrote %s: %d unique SKUs, %d duplicate SKUs, %d blank, %d untracked",
        SKU_CACHE_FILE.name,
        len(cache["skus"]),
        len(cache["duplicate_skus"]),
        cache["blank_sku_count"],
        cache["untracked_count"],
    )
    if cache["duplicate_skus"]:
        logger.warning(
            "%d SKUs are assigned to multiple variants and were SKIPPED. "
            "Fix them in Shopify then re-run this script.",
            len(cache["duplicate_skus"]),
        )
    return cache


def main() -> int:
    cfg = load_config()
    run_cache_refresh(cfg)
    return 0


if __name__ == "__main__":
    sys.exit(main())
