"""Daily inventory update: read Excel, match SKUs, update Shopify.

Workflow:
  1. Pick the most recent .xlsx/.xls in input/
  2. Read SKU, Quantity, (optional) Title columns
  3. Look up each SKU in sku_cache.json
  4. Push updates to Shopify in batches via inventorySetQuantities
  5. Log results, move the file to archive/
"""
from __future__ import annotations

import csv
import json
import logging
import shutil
import sys
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable

from openpyxl import load_workbook

from config import ARCHIVE_DIR, INPUT_DIR, LOGS_DIR, SKU_CACHE_FILE, Config, load_config
from shopify_client import ShopifyClient, ShopifyUserError

BATCH_SIZE = 100  # inventorySetQuantities accepts up to 250; 100 is a safe margin

SET_QUANTITIES_MUTATION = """
mutation inventorySetOnHand($input: InventorySetQuantitiesInput!, $key: String!) {
  inventorySetQuantities(input: $input) @idempotent(key: $key) {
    inventoryAdjustmentGroup {
      createdAt
      reason
      changes { name delta }
    }
    userErrors { field code message }
  }
}
"""

ADJUST_QUANTITIES_MUTATION = """
mutation inventoryAdjust($input: InventoryAdjustQuantitiesInput!, $key: String!) {
  inventoryAdjustQuantities(input: $input) @idempotent(key: $key) {
    inventoryAdjustmentGroup {
      createdAt
      reason
      changes { name delta }
    }
    userErrors { field code message }
  }
}
"""

def pick_input_file() -> Path:
    files = [
        p for p in INPUT_DIR.iterdir()
        if p.is_file() and p.suffix.lower() in {".xlsx", ".xlsm", ".xls"}
    ]
    if not files:
        raise FileNotFoundError(f"No Excel file found in {INPUT_DIR}")
    # Most recently modified wins
    return max(files, key=lambda p: p.stat().st_mtime)


def read_excel(path: Path, sku_col: str, qty_col: str, title_col: str) -> list[dict]:
    """Returns rows with {sku, qty, title, row_number}."""
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
    qty_idx = find_col(qty_col)
    title_idx = find_col(title_col)

    if sku_idx is None:
        raise ValueError(f"SKU column '{sku_col}' not found. Header row: {header}")
    if qty_idx is None:
        raise ValueError(f"Qty column '{qty_col}' not found. Header row: {header}")

    rows: list[dict] = []
    for i, row in enumerate(rows_iter, start=2):
        if row is None:
            continue
        sku = row[sku_idx] if sku_idx < len(row) else None
        qty = row[qty_idx] if qty_idx < len(row) else None
        title = row[title_idx] if title_idx is not None and title_idx < len(row) else None

        if sku is None and qty is None:
            continue  # blank row

        rows.append({
            "row": i,
            "sku": (str(sku).strip() if sku is not None else ""),
            "qty_raw": qty,
            "title": (str(title).strip() if title is not None else ""),
        })

    return rows


def coerce_qty(raw) -> int | None:
    if raw is None or raw == "":
        return None
    try:
        # Accept "12", "12.0", 12, 12.0 — but not "abc"
        return int(float(str(raw).strip()))
    except (ValueError, TypeError):
        return None


def load_sku_cache() -> dict:
    if not SKU_CACHE_FILE.exists():
        raise FileNotFoundError(
            f"{SKU_CACHE_FILE.name} missing. Run `python build_sku_cache.py` first."
        )
    return json.loads(SKU_CACHE_FILE.read_text(encoding="utf-8"))


def batched(items: list, size: int):
    for i in range(0, len(items), size):
        yield items[i : i + size]


def push_absolute(client, location_id: str, batch: list[dict]) -> list[dict]:
    """batch rows: [{inventory_item_id, quantity, sku}].

    API 2026-10 requires an @idempotent(key: ...) directive and a
    `changeFromQuantity` field per quantity. Passing null for
    changeFromQuantity opts out of the compare-and-set check — safe for a
    daily batch sync where the Excel is source of truth.
    """
    payload = {
        "input": {
            "reason": "correction",
            "name": "available",
            "quantities": [
                {
                    "inventoryItemId": r["inventory_item_id"],
                    "locationId": location_id,
                    "quantity": r["quantity"],
                    "changeFromQuantity": None,
                }
                for r in batch
            ],
        },
        "key": str(uuid.uuid4()),
    }
    data = client.graphql(SET_QUANTITIES_MUTATION, payload)
    result = data["inventorySetQuantities"]
    return result.get("userErrors") or []


def push_delta(client, location_id: str, batch: list[dict]) -> list[dict]:
    payload = {
        "input": {
            "reason": "correction",
            "name": "available",
            "changes": [
                {
                    "inventoryItemId": r["inventory_item_id"],
                    "locationId": location_id,
                    "delta": r["quantity"],
                }
                for r in batch
            ],
        },
        "key": str(uuid.uuid4()),
    }
    data = client.graphql(ADJUST_QUANTITIES_MUTATION, payload)
    result = data["inventoryAdjustQuantities"]
    return result.get("userErrors") or []


@dataclass
class MatchResult:
    matched: list[dict] = field(default_factory=list)
    unmatched: list[dict] = field(default_factory=list)
    bad_qty: list[dict] = field(default_factory=list)
    duplicate_hits: list[dict] = field(default_factory=list)
    untracked_hits: list[dict] = field(default_factory=list)
    title_mismatches: list[dict] = field(default_factory=list)


@dataclass
class PushResult:
    pushed: int = 0
    user_errors: list[dict] = field(default_factory=list)


def match_rows(rows: list[dict], cache: dict) -> MatchResult:
    """Classify Excel rows against the SKU cache.

    Pure function — no logging, no I/O. The returned lists mirror the shape
    produced by the previous inline loop in main() so downstream CSV writers
    and the web layer can share one classifier.
    """
    sku_map: dict[str, dict] = cache["skus"]
    dup_skus: dict[str, list] = cache.get("duplicate_skus", {})

    result = MatchResult()

    for r in rows:
        key = r["sku"].lower()
        if not key:
            result.bad_qty.append({**r, "reason": "blank SKU"})
            continue

        qty = coerce_qty(r["qty_raw"])
        if qty is None:
            result.bad_qty.append({**r, "reason": f"bad quantity: {r['qty_raw']!r}"})
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

        if not hit.get("tracked"):
            result.untracked_hits.append({**r, "shopify_title": hit["title"]})
            continue

        if r["title"] and hit["title"] and r["title"].lower() != hit["title"].lower():
            result.title_mismatches.append({
                "row": r["row"], "sku": r["sku"],
                "excel_title": r["title"], "shopify_title": hit["title"],
            })

        result.matched.append({
            "sku": r["sku"],
            "inventory_item_id": hit["inventory_item_id"],
            "quantity": qty,
            "row": r["row"],
        })

    return result


def push_batches(
    client: ShopifyClient,
    cfg: Config,
    matched: list[dict],
    progress_cb: Callable[[int, int], None] | None = None,
    log: logging.Logger | None = None,
) -> PushResult:
    """Push matched rows to Shopify in BATCH_SIZE-sized batches.

    Respects cfg.qty_mode. Catches ShopifyUserError per batch and records it
    without aborting. Calls progress_cb(pushed, total) after each batch for
    web progress bars.
    """
    log = log or logging.getLogger(__name__)
    push_fn = push_absolute if cfg.qty_mode == "absolute" else push_delta
    total = len(matched)
    result = PushResult()

    for i, batch in enumerate(batched(matched, BATCH_SIZE), start=1):
        try:
            errs = push_fn(client, cfg.location_id, batch)
        except ShopifyUserError as e:
            log.error("Batch %d failed: %s", i, e)
            result.user_errors.append({"batch": i, "error": str(e)})
            if progress_cb:
                progress_cb(result.pushed, total)
            continue
        if errs:
            log.warning("Batch %d returned %d userErrors", i, len(errs))
            for ue in errs:
                log.warning("  userError: %s", ue)
                result.user_errors.append({"batch": i, **ue})
        result.pushed += len(batch)
        if progress_cb:
            progress_cb(result.pushed, total)
        if i % 10 == 0 or result.pushed == total:
            log.info("Pushed %d / %d", result.pushed, total)

    return result


def main() -> int:
    LOGS_DIR.mkdir(exist_ok=True)
    ARCHIVE_DIR.mkdir(exist_ok=True)
    stamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")
    log_file = LOGS_DIR / f"run_{stamp}.log"
    unmatched_file = LOGS_DIR / f"unmatched_{stamp}.csv"

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=[logging.FileHandler(log_file, encoding="utf-8"), logging.StreamHandler()],
    )
    log = logging.getLogger("update_inventory")

    cfg = load_config()
    if not cfg.location_id:
        log.error("SHOPIFY_LOCATION_ID is not set in .env. Run find_location.py and paste the GID.")
        return 1
    log.info("Mode: %s | Dry run: %s | Location: %s", cfg.qty_mode, cfg.dry_run, cfg.location_id)

    excel_path = pick_input_file()
    log.info("Reading %s", excel_path.name)
    rows = read_excel(excel_path, cfg.sku_column, cfg.qty_column, cfg.title_column)
    log.info("Loaded %d rows from Excel", len(rows))

    cache = load_sku_cache()
    match = match_rows(rows, cache)

    log.info(
        "Matched=%d | Unmatched=%d | Bad qty=%d | Duplicate SKUs=%d | Untracked=%d | Title mismatches=%d",
        len(match.matched), len(match.unmatched), len(match.bad_qty),
        len(match.duplicate_hits), len(match.untracked_hits), len(match.title_mismatches),
    )

    # Write unmatched / problem rows to CSV for review
    if match.unmatched or match.bad_qty or match.duplicate_hits or match.untracked_hits:
        with unmatched_file.open("w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["category", "row", "sku", "title", "qty_raw", "reason_or_shopify_title"])
            for r in match.unmatched:
                w.writerow(["unmatched", r["row"], r["sku"], r["title"], r["qty_raw"], ""])
            for r in match.bad_qty:
                w.writerow(["bad_qty", r["row"], r["sku"], r["title"], r["qty_raw"], r["reason"]])
            for r in match.duplicate_hits:
                w.writerow(["duplicate_sku", r["row"], r["sku"], r["title"], r["qty_raw"], r["reason"]])
            for r in match.untracked_hits:
                w.writerow(["untracked", r["row"], r["sku"], r["title"], r["qty_raw"], r["shopify_title"]])
        log.info("Problem rows written to %s", unmatched_file.name)

    if match.title_mismatches:
        tm_file = LOGS_DIR / f"title_mismatches_{stamp}.csv"
        with tm_file.open("w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["row", "sku", "excel_title", "shopify_title"])
            for r in match.title_mismatches:
                w.writerow([r["row"], r["sku"], r["excel_title"], r["shopify_title"]])
        log.info("Title mismatches written to %s", tm_file.name)

    if cfg.dry_run:
        log.warning("DRY_RUN=true — not sending updates to Shopify. Review logs, then set DRY_RUN=false in .env.")
        return 0

    if not match.matched:
        log.warning("Nothing to push. Exiting.")
        return 0

    # Push in batches
    client = ShopifyClient(cfg)
    push = push_batches(client, cfg, match.matched, log=log)

    if push.user_errors:
        err_file = LOGS_DIR / f"user_errors_{stamp}.json"
        err_file.write_text(json.dumps(push.user_errors, indent=2), encoding="utf-8")
        log.warning("%d userErrors written to %s", len(push.user_errors), err_file.name)

    # Archive the processed file
    archive_path = ARCHIVE_DIR / f"{excel_path.stem}__{stamp}{excel_path.suffix}"
    shutil.move(str(excel_path), str(archive_path))
    log.info("Archived input file to %s", archive_path.name)

    log.info("DONE. Pushed %d updates to Shopify.", push.pushed)
    return 0 if not push.user_errors else 2


if __name__ == "__main__":
    sys.exit(main())
