"""Background job bodies for the cross-reference crawler.

Three phases run inside a single JobRunner slot:
  1. SCAN  — paginate the Parts collection, collect product_id/sku/title/current
             cross_reference value. Done via one Shopify GraphQL loop.
  2. CRAWL — call core.crossref.Crawler on the SKUs that are missing values.
  3. WRITE — persist a results JSON under RUNS_DIR/{run_id}/crossref.json.

A separate `apply_job` pushes selected rows back to Shopify's
custom.cross_reference metafield.
"""
from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import asdict
from pathlib import Path
from typing import Callable

import requests

from core.config import Config, RUNS_DIR
from core.crossref import Crawler, CrawlRow, TemplateSource
from core.shopify_client import ShopifyClient

from . import history
from .jobs import JobCancelled

log = logging.getLogger(__name__)

def _parts_collection_id() -> str:
    """Return the Parts collection GID to scan.

    Required at run time via the PARTS_COLLECTION_ID env var — kept out of
    source so the store's internal catalog layout doesn't leak into git.
    """
    val = os.environ.get("PARTS_COLLECTION_ID", "").strip()
    if not val:
        raise RuntimeError(
            "PARTS_COLLECTION_ID is not set. Add it to .env as the GID of the "
            "Shopify collection to scan (e.g. gid://shopify/Collection/123...)."
        )
    return val


SCAN_QUERY = """
query PartsPage($id: ID!, $cursor: String) {
  collection(id: $id) {
    products(first: 100, after: $cursor) {
      pageInfo { hasNextPage endCursor }
      edges {
        node {
          id
          title
          handle
          vendor
          variants(first: 1) {
            edges { node { sku } }
          }
          crossRef: metafield(namespace: "custom", key: "cross_reference") {
            value
          }
        }
      }
    }
  }
}
"""


def _crossref_value_is_empty(mf: dict | None) -> bool:
    if not mf:
        return True
    val = mf.get("value")
    if not val:
        return True
    try:
        parsed = json.loads(val)
        if isinstance(parsed, list):
            return not [s for s in parsed if s and str(s).strip()]
    except (json.JSONDecodeError, TypeError):
        pass
    return False


def run_dir(run_id: int) -> Path:
    d = RUNS_DIR / str(run_id)
    d.mkdir(parents=True, exist_ok=True)
    return d


def _cache_slug(source_name: str) -> str:
    """Turn 'www.alltemp.ca' into 'www_alltemp_ca' — safe as a filename."""
    import re as _re
    return _re.sub(r"[^A-Za-z0-9]+", "_", source_name).strip("_") or "default"


# ---------------------------------------------------------------- crawl job


def crawl_job(
    *,
    run_id: int,
    cfg: Config,
    url_template: str,
    progress_cb: Callable[..., None],
) -> None:
    """End-to-end: scan Parts collection → crawl missing SKUs → write results.

    `url_template` must contain one of the TemplateSource placeholders
    ({sku}, {sku_lower}, {sku_upper}). It's persisted with the run so the UI
    can show which site/template produced a given result.
    """
    history.set_status(run_id, "running")
    try:
        _crawl_job_body(run_id=run_id, cfg=cfg, url_template=url_template,
                        progress_cb=progress_cb)
        history.set_status(run_id, "succeeded")
    except JobCancelled:
        # Preserve any partial results we've already written before re-raising
        # so JobRunner can mark the in-memory job state as 'cancelled'.
        history.set_status(run_id, "cancelled", error="Cancelled by user")
        raise
    except Exception as e:  # noqa: BLE001
        history.set_status(run_id, "failed", error=str(e))
        raise


def _crawl_job_body(
    *,
    run_id: int,
    cfg: Config,
    url_template: str,
    progress_cb: Callable[..., None],
) -> None:
    # Fail fast with a readable error if the template is malformed, before we
    # make any Shopify calls.
    source = TemplateSource(
        url_template=url_template,
        session=requests.Session(),
    )

    client = ShopifyClient(cfg)

    # ---- Phase 1: scan collection ----
    progress_cb(0, 0, "Scanning Parts collection...")
    products: list[dict] = []
    cursor: str | None = None
    pages = 0
    while True:
        data = client.graphql(SCAN_QUERY, {"id": _parts_collection_id(), "cursor": cursor})
        conn = data["collection"]["products"]
        for edge in conn["edges"]:
            n = edge["node"]
            variants = (n.get("variants") or {}).get("edges") or []
            sku = (variants[0]["node"].get("sku") if variants else "") or ""
            products.append({
                "product_id": n["id"],
                "title":      n["title"],
                "handle":     n["handle"],
                "vendor":     n.get("vendor") or "",
                "sku":        sku.strip(),
                "has_crossref": not _crossref_value_is_empty(n.get("crossRef")),
                "current_crossref": (n.get("crossRef") or {}).get("value") or "",
            })
        pages += 1
        progress_cb(len(products), len(products),
                    f"Scanning Parts collection... ({len(products)} seen, page {pages})")
        if not conn["pageInfo"]["hasNextPage"]:
            break
        cursor = conn["pageInfo"]["endCursor"]

    # Partition.
    needs_crawl = [p for p in products if not p["has_crossref"] and p["sku"]]
    skipped_no_sku = [p for p in products if not p["has_crossref"] and not p["sku"]]

    scan_summary = {
        "collection_id":     _parts_collection_id(),
        "url_template":      url_template,
        "source":            source.name,
        "total_products":    len(products),
        "has_crossref":      sum(1 for p in products if p["has_crossref"]),
        "missing_crossref":  sum(1 for p in products if not p["has_crossref"]),
        "to_crawl":          len(needs_crawl),
        "skipped_no_sku":    len(skipped_no_sku),
    }

    out_dir = run_dir(run_id)
    (out_dir / "scan.json").write_text(json.dumps(scan_summary, indent=2), encoding="utf-8")

    # ---- Phase 2: crawl ----
    rows = [CrawlRow(product_id=p["product_id"], sku=p["sku"], title=p["title"])
            for p in needs_crawl]

    # Per-template cache file — switching sites shouldn't collide on cache keys
    # (the key is sku+source.name, but keeping the files separate keeps each
    # source's history cleanly inspectable).
    cache_name = f"crossref_cache_{_cache_slug(source.name)}.sqlite"
    cache_path = RUNS_DIR / cache_name
    crawler = Crawler(
        sources=[source],
        cache_path=cache_path,
        sleep_seconds=float(os.environ.get("CROSSREF_SLEEP_SECONDS", "1.0")),
    )

    results: list[dict] = []

    def _cb(i: int, total: int, result) -> None:
        progress_cb(i, total, f"Crawling {result.sku} ({i}/{total})")

    for result in crawler.lookup_many(rows, use_cache=True, progress_cb=_cb):
        results.append(asdict(result))

    # Attach skipped products so the UI shows the full picture.
    for p in skipped_no_sku:
        results.append({
            "product_id": p["product_id"], "sku": "", "title": p["title"],
            "status": "skipped", "source": None, "url": None, "upcs": [],
            "error": "no SKU on primary variant",
        })

    payload = {
        "scan":    scan_summary,
        "results": results,
    }
    (out_dir / "crossref.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")

    counts = {
        "scanned":   scan_summary["total_products"],
        "missing":   scan_summary["missing_crossref"],
        "crawled":   len(rows),
        "found":     sum(1 for r in results if r["status"] == "found"),
        "not_found": sum(1 for r in results if r["status"] == "not_found"),
        "skipped":   sum(1 for r in results if r["status"] == "skipped"),
    }
    history.record_counts(run_id, counts)

    progress_cb(
        len(rows), len(rows),
        f"Done. Found {counts['found']}/{len(rows)}.",
    )


# ------------------------------------------------------------ apply job


METAFIELDS_SET_MUTATION = """
mutation metafieldsSet($metafields: [MetafieldsSetInput!]!) {
  metafieldsSet(metafields: $metafields) {
    metafields { id }
    userErrors { field message code }
  }
}
"""


def apply_job(
    *,
    run_id: int,
    source_run_id: int,
    cfg: Config,
    progress_cb: Callable[..., None],
) -> None:
    """Push rows from a prior crawl result to the cross_reference metafield.

    Only rows the crawler flagged as `found` with at least one UPC are pushed.
    Each metafield value is a JSON-encoded list of strings (the Shopify type is
    `list.single_line_text_field`).
    """
    history.set_status(run_id, "running")
    try:
        _apply_job_body(run_id=run_id, source_run_id=source_run_id,
                        cfg=cfg, progress_cb=progress_cb)
        history.set_status(run_id, "succeeded")
    except JobCancelled:
        history.set_status(run_id, "cancelled", error="Cancelled by user")
        raise
    except Exception as e:  # noqa: BLE001
        history.set_status(run_id, "failed", error=str(e))
        raise


def _apply_job_body(
    *,
    run_id: int,
    source_run_id: int,
    cfg: Config,
    progress_cb: Callable[..., None],
) -> None:
    source_path = run_dir(source_run_id) / "crossref.json"
    if not source_path.exists():
        raise RuntimeError(f"Crawl results not found for run {source_run_id}.")

    payload = json.loads(source_path.read_text(encoding="utf-8"))
    results = [r for r in payload.get("results", [])
               if r.get("status") == "found" and r.get("upcs")]

    client = ShopifyClient(cfg)

    pushed = 0
    user_errors: list[dict] = []

    BATCH_SIZE = 25
    total = len(results)
    progress_cb(0, total, f"Pushing {total} cross-reference values...")

    for i in range(0, total, BATCH_SIZE):
        batch = results[i: i + BATCH_SIZE]
        metafields = [
            {
                "ownerId":   r["product_id"],
                "namespace": "custom",
                "key":       "cross_reference",
                "type":      "list.single_line_text_field",
                "value":     json.dumps(r["upcs"]),
            }
            for r in batch
        ]
        data = client.graphql(METAFIELDS_SET_MUTATION, {"metafields": metafields})
        payload_resp = data.get("metafieldsSet") or {}
        errs = payload_resp.get("userErrors") or []
        user_errors.extend(errs)
        pushed += len(payload_resp.get("metafields") or [])
        progress_cb(min(i + BATCH_SIZE, total), total,
                    f"Pushed {min(i + BATCH_SIZE, total)}/{total}")

    out_dir = run_dir(run_id)
    (out_dir / "apply.json").write_text(json.dumps({
        "source_run_id": source_run_id,
        "total":         total,
        "pushed":        pushed,
        "user_errors":   user_errors,
    }, indent=2), encoding="utf-8")

    history.record_counts(run_id, {
        "total":       total,
        "pushed":      pushed,
        "user_errors": len(user_errors),
    })

    progress_cb(total, total, f"Pushed {pushed}/{total}. Errors: {len(user_errors)}.")
