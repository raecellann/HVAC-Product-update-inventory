"""Price update: read Excel (SKU + Price), match SKUs, push to Shopify.

Workflow mirrors update_inventory.py, but targets variant prices via
`productVariantsBulkUpdate`. Mutations are grouped by productId because
that mutation is per-product.
"""
from __future__ import annotations

import csv
import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Callable

import requests
from openpyxl import load_workbook

from .config import Config
from .shopify_client import ShopifyClient, ShopifyUserError

BATCH_SIZE = 100  # variants per productVariantsBulkUpdate call (same product)
PUSH_WORKERS = 8  # parallel workers for productVariantsBulkUpdate; Shopify throttles per-shop

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

    # Some Excel files declare a used range that extends tens of thousands of
    # rows past the real data. Keep reading tolerates occasional blanks but
    # bails out once we've seen a long stretch with no SKU + no price —
    # otherwise openpyxl walks to row 1,048,576 and the worker OOMs.
    BLANK_RUN_LIMIT = 2000
    rows: list[dict] = []
    blank_run = 0
    for i, row in enumerate(rows_iter, start=2):
        if row is None:
            blank_run += 1
            if blank_run >= BLANK_RUN_LIMIT:
                break
            continue
        sku = row[sku_idx] if sku_idx < len(row) else None
        price = row[price_idx] if price_idx < len(row) else None

        if sku is None and price is None:
            blank_run += 1
            if blank_run >= BLANK_RUN_LIMIT:
                break
            continue

        blank_run = 0
        rows.append({
            "row": i,
            "sku": (str(sku).strip() if sku is not None else ""),
            "price_raw": price,
        })

    wb.close()
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


def stream_match_price_rows(
    path: Path,
    sku_col: str,
    price_col: str,
    cache: dict,
    problems_csv_path: Path | None = None,
    sample_cap: int = 1000,
    progress_cb=None,
) -> dict:
    """Read a supplier file row-by-row and classify against the SKU cache.

    Supports Excel (.xlsx / .xlsm) and tab/comma-separated text (.txt / .csv).
    Peak memory is O(unique matched SKUs), not O(total rows). Problem rows
    (unmatched / bad price / duplicate) stream to a CSV capped at
    `sample_cap` per category so the CSV stays small even when the file is
    dominated by unmatched rows.

    `progress_cb(done, total, message)` is called periodically from the row
    iterator so the caller can drive a UI progress bar. `total` is 0 when
    unknown.

    Returns a dict with:
      matched: list of {sku, variant_id, new_price, row} — deduped by SKU
      excel_row_count, unmatched_count, bad_price_count, duplicate_count
    """
    suffix = path.suffix.lower()
    if suffix in {".txt", ".csv"}:
        row_source = _iter_price_rows_text(path, sku_col, price_col, progress_cb=progress_cb)
    else:
        row_source = _iter_price_rows_xlsx(path, sku_col, price_col, progress_cb=progress_cb)
    return _classify_price_rows(row_source, cache, problems_csv_path, sample_cap)


# Throttle how often we tick the progress callback. Matches the inventory
# reader's cadence so a combined preview advances at the same rate.
_READ_PROGRESS_STEP_ROWS = 2000


def _iter_price_rows_xlsx(path: Path, sku_col: str, price_col: str, progress_cb=None):
    """Yield (row_number, sku_val, price_val) for an .xlsx/.xlsm file.

    Raises ValueError if a required column is missing — the caller's
    column-missing handler surfaces this as a graceful mode-switch prompt.
    """
    wb = load_workbook(path, read_only=True, data_only=True)
    ws = wb.active
    try:
        total_rows = max(0, (ws.max_row or 0) - 1)
    except Exception:
        total_rows = 0
    rows_iter = ws.iter_rows(values_only=True)
    try:
        header = next(rows_iter)
    except StopIteration:
        wb.close()
        return

    header_lc = [(str(h).strip().lower() if h is not None else "") for h in header]

    def find_col(name: str) -> int | None:
        name_lc = name.strip().lower()
        return header_lc.index(name_lc) if name_lc in header_lc else None

    sku_idx = find_col(sku_col)
    price_idx = find_col(price_col)
    if sku_idx is None:
        wb.close()
        raise ValueError(f"SKU column '{sku_col}' not found. Header row: {header}")
    if price_idx is None:
        wb.close()
        raise ValueError(f"Price column '{price_col}' not found. Header row: {header}")

    BLANK_RUN_LIMIT = 2000
    blank_run = 0
    read = 0
    try:
        for i, row in enumerate(rows_iter, start=2):
            read += 1
            if progress_cb is not None and read % _READ_PROGRESS_STEP_ROWS == 0:
                progress_cb(read, total_rows, "Reading Excel")
            if row is None:
                blank_run += 1
                if blank_run >= BLANK_RUN_LIMIT:
                    break
                continue
            sku_val = row[sku_idx] if sku_idx < len(row) else None
            price_val = row[price_idx] if price_idx < len(row) else None
            if sku_val is None and price_val is None:
                blank_run += 1
                if blank_run >= BLANK_RUN_LIMIT:
                    break
                continue
            blank_run = 0
            yield (i, sku_val, price_val)
    finally:
        wb.close()
        if progress_cb is not None:
            final_total = total_rows if total_rows > 0 else read
            progress_cb(final_total, final_total, "Reading Excel")


def _iter_price_rows_text(path: Path, sku_col: str, price_col: str, progress_cb=None):
    """Yield (row_number, sku_val, price_val) for a .txt/.csv file.

    Mirrors the TSV reader on the inventory side so the same supplier .txt
    works for both halves of a combined preview.
    """
    # Local imports keep this module importable even if update_inventory.py's
    # helpers move; the TSV helpers deliberately live there since the
    # inventory reader was the first to need them.
    from .update_inventory import _open_text, _sniff_delimiter
    import csv as _csv

    with _open_text(path) as f:
        head_sample = f.readline()
        if not head_sample:
            return
        delim = _sniff_delimiter(head_sample)
        f.seek(0)
        reader = _csv.DictReader(f, delimiter=delim)
        fieldnames = [(h or "").strip() for h in (reader.fieldnames or [])]
        header_lc = [h.lower() for h in fieldnames]

        def find_col(name: str) -> str | None:
            nlc = name.strip().lower()
            if nlc in header_lc:
                return fieldnames[header_lc.index(nlc)]
            return None

        sku_key = find_col(sku_col)
        price_key = find_col(price_col)
        if sku_key is None:
            raise ValueError(f"SKU column '{sku_col}' not found. Header row: {fieldnames}")
        if price_key is None:
            raise ValueError(f"Price column '{price_col}' not found. Header row: {fieldnames}")

        read = 0
        for i, rec in enumerate(reader, start=2):
            read += 1
            # Text-mode f.tell() is unreliable inside iteration (OSError in
            # CPython), so we tick row counts with total=0 — the bar stays
            # indeterminate but the "row N" message keeps moving.
            if progress_cb is not None and read % _READ_PROGRESS_STEP_ROWS == 0:
                progress_cb(read, 0, f"Reading file (row {read:,})")
            if rec is None:
                continue
            sku_raw = rec.get(sku_key)
            price_raw = rec.get(price_key)
            if isinstance(sku_raw, str):
                sku_raw = sku_raw.strip()
            if isinstance(price_raw, str):
                price_raw = price_raw.strip()
            if (sku_raw in (None, "")) and (price_raw in (None, "")):
                continue
            yield (i, sku_raw, price_raw)

        if progress_cb is not None:
            progress_cb(read, read, "Reading file")


def _classify_price_rows(
    row_source,
    cache: dict,
    problems_csv_path: Path | None,
    sample_cap: int,
) -> dict:
    """Shared per-row classification for xlsx and tsv price streams."""
    sku_map: dict[str, dict] = cache["skus"]
    dup_skus: dict[str, list] = cache.get("duplicate_skus", {})

    matched_by_sku: dict[str, dict] = {}
    excel_row_count = 0
    unmatched_count = 0
    bad_price_count = 0
    duplicate_count = 0
    samples = {"unmatched": 0, "bad_price": 0, "duplicate_sku": 0}

    csv_file = None
    csv_writer = None
    if problems_csv_path is not None:
        csv_file = problems_csv_path.open("w", newline="", encoding="utf-8")
        csv_writer = csv.writer(csv_file)
        csv_writer.writerow(["category", "row", "sku", "price_raw", "reason"])

    def record_problem(category: str, row: int, sku: str, price_raw, reason: str) -> None:
        if csv_writer and samples[category] < sample_cap:
            csv_writer.writerow([category, row, sku, price_raw, reason])
            samples[category] += 1

    try:
        for (i, sku_val, price_val) in row_source:
            excel_row_count += 1
            sku = (str(sku_val).strip() if sku_val is not None else "")
            key = sku.lower()
            if not key:
                bad_price_count += 1
                record_problem("bad_price", i, sku, price_val, "blank SKU")
                continue

            price_str = coerce_price(price_val)
            if price_str is None:
                bad_price_count += 1
                record_problem("bad_price", i, sku, price_val, f"bad price: {price_val!r}")
                continue

            if key in dup_skus:
                duplicate_count += 1
                record_problem("duplicate_sku", i, sku, price_val,
                               "SKU assigned to multiple variants in Shopify")
                continue

            hit = sku_map.get(key)
            if not hit:
                unmatched_count += 1
                record_problem("unmatched", i, sku, price_val, "")
                continue

            matched_by_sku[key] = {
                "sku": sku,
                "variant_id": hit["variant_id"],
                "new_price": price_str,
                "row": i,
            }
    finally:
        if csv_file is not None:
            csv_file.close()
            if all(v == 0 for v in samples.values()):
                try:
                    problems_csv_path.unlink()
                except OSError:
                    pass

    return {
        "matched": list(matched_by_sku.values()),
        "excel_row_count": excel_row_count,
        "unmatched_count": unmatched_count,
        "bad_price_count": bad_price_count,
        "duplicate_count": duplicate_count,
    }


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
    """{sku_lower: {'price': '12.99', 'product_id': 'gid://...', 'image_url': '...'}}.

    Mutates `rows` — pops each entry as it's consumed so we don't hold the
    input list AND the output dict in memory at once for 70k+ variants.
    """
    out: dict[str, dict] = {}
    while rows:
        row = rows.pop()
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
    workers: int = PUSH_WORKERS,
) -> PricePushResult:
    """Push matched price rows, grouped by product_id, in parallel.

    Each matched row must include `product_id`. If a product has more than
    BATCH_SIZE variants, we split that product across calls. Shopify's
    throttle is per-shop, so parallel workers mostly help absorb burst
    capacity in the cost bucket; steady-state is still throttle-bound.
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

    # Flatten to a list of (product_id, chunk) tasks.
    tasks: list[tuple[str, list[dict]]] = []
    for pid, variants in by_product.items():
        for i in range(0, len(variants), BATCH_SIZE):
            tasks.append((pid, variants[i : i + BATCH_SIZE]))

    log.info(
        "Pushing %d variants across %d products in %d tasks with %d workers",
        total, len(by_product), len(tasks), workers,
    )

    lock = threading.Lock()
    completed_tasks = 0

    def _push_one(pid: str, chunk: list[dict]) -> tuple[str, list[dict], list[dict] | None, str | None]:
        """Returns (product_id, chunk, user_errors_or_none, exception_msg_or_none)."""
        try:
            errs = push_product_prices(client, pid, chunk)
            return pid, chunk, errs or [], None
        except ShopifyUserError as e:
            return pid, chunk, None, str(e)

    with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="price-push") as pool:
        futures = [pool.submit(_push_one, pid, chunk) for pid, chunk in tasks]
        for fut in as_completed(futures):
            pid, chunk, errs, exc_msg = fut.result()
            with lock:
                completed_tasks += 1
                if exc_msg is not None:
                    log.error("Product %s failed: %s", pid, exc_msg)
                    result.user_errors.append({"product_id": pid, "error": exc_msg})
                else:
                    if errs:
                        for ue in errs:
                            log.warning("  userError on product %s: %s", pid, ue)
                            result.user_errors.append({"product_id": pid, **ue})
                    result.pushed += len(chunk)
                if progress_cb:
                    progress_cb(result.pushed, total)
                if completed_tasks % 100 == 0:
                    log.info(
                        "Pushed %d / %d variants (%d / %d tasks)",
                        result.pushed, total, completed_tasks, len(tasks),
                    )

    log.info("Pushed %d / %d variants total", result.pushed, total)
    return result
