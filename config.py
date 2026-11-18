from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent
load_dotenv(PROJECT_ROOT / ".env")


def _require(name: str) -> str:
    val = os.environ.get(name, "").strip()
    if not val or val.startswith("REPLACE_ME") or val.startswith("your-store"):
        raise RuntimeError(
            f"Missing or placeholder value for {name} in .env. "
            f"Copy .env.example to .env and fill in real values."
        )
    return val


@dataclass(frozen=True)
class Config:
    store_domain: str
    access_token: str
    api_version: str
    location_id: str
    qty_mode: str  # "absolute" or "delta"
    sku_column: str
    qty_column: str
    title_column: str
    dry_run: bool

    @property
    def graphql_url(self) -> str:
        return f"https://{self.store_domain}/admin/api/{self.api_version}/graphql.json"


def load_config() -> Config:
    qty_mode = os.environ.get("QTY_MODE", "absolute").strip().lower()
    if qty_mode not in {"absolute", "delta"}:
        raise RuntimeError(f"QTY_MODE must be 'absolute' or 'delta', got: {qty_mode!r}")

    raw_loc = os.environ.get("SHOPIFY_LOCATION_ID", "").strip()
    location_id = "" if "REPLACE_ME" in raw_loc or not raw_loc else raw_loc

    return Config(
        store_domain=_require("SHOPIFY_STORE_DOMAIN"),
        access_token=_require("SHOPIFY_ACCESS_TOKEN"),
        api_version=os.environ.get("SHOPIFY_API_VERSION", "2026-10").strip(),
        location_id=location_id,
        qty_mode=qty_mode,
        sku_column=os.environ.get("EXCEL_SKU_COLUMN", "SKU").strip(),
        qty_column=os.environ.get("EXCEL_QTY_COLUMN", "Quantity").strip(),
        title_column=os.environ.get("EXCEL_TITLE_COLUMN", "Title").strip(),
        dry_run=os.environ.get("DRY_RUN", "true").strip().lower() in {"1", "true", "yes"},
    )


INPUT_DIR = PROJECT_ROOT / "input"
ARCHIVE_DIR = PROJECT_ROOT / "archive"
LOGS_DIR = PROJECT_ROOT / "logs"
SKU_CACHE_FILE = PROJECT_ROOT / "sku_cache.json"
