"""Audit Parts collection for products missing the `custom.cross_reference` metafield.

Writes:
  logs/parts_missing_crossref.csv   — one row per product with no / empty cross_reference
  logs/parts_crossref_summary.txt   — counts summary

Usage:
  python scripts/audit_parts_crossref.py
"""
from __future__ import annotations

import csv
import json
import os
import sys
import time
from pathlib import Path

import requests
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
load_dotenv(ROOT / ".env")

DOMAIN = os.environ["SHOPIFY_STORE_DOMAIN"]
TOKEN = os.environ["SHOPIFY_ACCESS_TOKEN"]
VER = os.environ.get("SHOPIFY_API_VERSION", "2026-10")
URL = f"https://{DOMAIN}/admin/api/{VER}/graphql.json"
HEADERS = {"X-Shopify-Access-Token": TOKEN, "Content-Type": "application/json"}

PARTS_COLLECTION_ID = "gid://shopify/Collection/535600955706"

QUERY = """
query PartsPage($id: ID!, $cursor: String) {
  collection(id: $id) {
    products(first: 100, after: $cursor) {
      pageInfo { hasNextPage endCursor }
      edges {
        node {
          id
          title
          handle
          status
          vendor
          productType
          crossRef: metafield(namespace: "custom", key: "cross_reference") {
            value
            type
          }
        }
      }
    }
  }
}
"""


def gql(query: str, variables: dict) -> dict:
    for attempt in range(6):
        r = requests.post(URL, json={"query": query, "variables": variables}, headers=HEADERS, timeout=60)
        if r.status_code == 429:
            time.sleep(float(r.headers.get("Retry-After", "2")))
            continue
        r.raise_for_status()
        data = r.json()
        if "errors" in data:
            errs = data["errors"]
            if any((e.get("extensions", {}) or {}).get("code") == "THROTTLED" for e in errs):
                time.sleep(2 * (attempt + 1))
                continue
            raise RuntimeError(f"GraphQL errors: {errs}")
        return data["data"]
    raise RuntimeError("retries exhausted")


def is_missing(mf: dict | None) -> bool:
    if mf is None:
        return True
    val = mf.get("value")
    if val is None or val == "":
        return True
    # list.single_line_text_field value is a JSON-encoded list string
    try:
        parsed = json.loads(val)
        if isinstance(parsed, list):
            cleaned = [s for s in parsed if s and str(s).strip()]
            return len(cleaned) == 0
    except (json.JSONDecodeError, TypeError):
        pass
    return False


def main() -> None:
    logs_dir = ROOT / "logs"
    logs_dir.mkdir(exist_ok=True)
    out_csv = logs_dir / "parts_missing_crossref.csv"
    out_summary = logs_dir / "parts_crossref_summary.txt"

    cursor: str | None = None
    total = 0
    missing = 0
    pages = 0
    with out_csv.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(["product_id", "title", "handle", "status", "vendor", "product_type", "admin_url"])

        while True:
            data = gql(QUERY, {"id": PARTS_COLLECTION_ID, "cursor": cursor})
            conn = data["collection"]["products"]
            for edge in conn["edges"]:
                n = edge["node"]
                total += 1
                if is_missing(n.get("crossRef")):
                    missing += 1
                    pid_num = n["id"].rsplit("/", 1)[-1]
                    admin_url = f"https://{DOMAIN.replace('.myshopify.com','')}.myshopify.com/admin/products/{pid_num}"
                    writer.writerow([
                        n["id"], n["title"], n["handle"], n["status"], n["vendor"],
                        n["productType"], admin_url,
                    ])
            pages += 1
            if pages % 10 == 0:
                print(f"  ...page {pages}: scanned {total}, missing {missing}")
            if not conn["pageInfo"]["hasNextPage"]:
                break
            cursor = conn["pageInfo"]["endCursor"]

    summary = (
        f"Parts collection audit\n"
        f"Collection: {PARTS_COLLECTION_ID}\n"
        f"Total products scanned: {total}\n"
        f"Missing cross_reference: {missing}\n"
        f"With cross_reference:    {total - missing}\n"
        f"Missing CSV: {out_csv}\n"
    )
    out_summary.write_text(summary, encoding="utf-8")
    print()
    print(summary)


if __name__ == "__main__":
    main()
