"""Test fixtures: isolated DATA_DIR + fake ShopifyClient + sample xlsx."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))


@pytest.fixture(autouse=True)
def isolated_data_dir(tmp_path, monkeypatch):
    """Point DATA_DIR at a fresh tmp dir and reload config-dependent modules."""
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("SHOPIFY_STORE_DOMAIN", "fake.myshopify.com")
    monkeypatch.setenv("SHOPIFY_ACCESS_TOKEN", "shpat_FAKE_TOKEN_1234567890")
    monkeypatch.setenv("SHOPIFY_LOCATION_ID", "gid://shopify/Location/1")
    monkeypatch.setenv("DRY_RUN", "true")
    monkeypatch.setenv("QTY_MODE", "absolute")
    # Pin Excel column names so a developer's .env can't override test fixtures.
    monkeypatch.setenv("EXCEL_SKU_COLUMN", "SKU")
    monkeypatch.setenv("EXCEL_QTY_COLUMN", "Quantity")
    monkeypatch.setenv("EXCEL_TITLE_COLUMN", "Title")

    import importlib
    import config as _cfg
    importlib.reload(_cfg)
    # These modules import config's module-level paths; reload so they see the new DATA_DIR.
    for name in ("update_inventory", "compare_inventory", "build_sku_cache"):
        if name in sys.modules:
            importlib.reload(sys.modules[name])
    if "webapp.history" in sys.modules:
        importlib.reload(sys.modules["webapp.history"])
    if "webapp.services" in sys.modules:
        importlib.reload(sys.modules["webapp.services"])
    yield tmp_path


@pytest.fixture
def cfg():
    from config import load_config
    return load_config()


@pytest.fixture
def sample_cache(tmp_path):
    """Write a sku_cache.json with a known SKU map."""
    from config import SKU_CACHE_FILE
    cache = {
        "skus": {
            "sku-a": {
                "inventory_item_id": "gid://shopify/InventoryItem/101",
                "variant_id": "gid://shopify/ProductVariant/1",
                "title": "Widget A",
                "tracked": True,
            },
            "sku-b": {
                "inventory_item_id": "gid://shopify/InventoryItem/102",
                "variant_id": "gid://shopify/ProductVariant/2",
                "title": "Widget B",
                "tracked": True,
            },
            "sku-untracked": {
                "inventory_item_id": "gid://shopify/InventoryItem/103",
                "variant_id": "gid://shopify/ProductVariant/3",
                "title": "Untracked Widget",
                "tracked": False,
            },
        },
        "duplicate_skus": {
            "sku-dup": [
                {"inventory_item_id": "gid://shopify/InventoryItem/201", "title": "Dup 1", "tracked": True},
                {"inventory_item_id": "gid://shopify/InventoryItem/202", "title": "Dup 2", "tracked": True},
            ]
        },
        "blank_sku_count": 0,
        "untracked_count": 1,
    }
    SKU_CACHE_FILE.write_text(json.dumps(cache), encoding="utf-8")
    return cache


@pytest.fixture
def sample_xlsx(tmp_path):
    """Build a small .xlsx fixture and return the path."""
    from openpyxl import Workbook
    wb = Workbook()
    ws = wb.active
    ws.append(["SKU", "Quantity", "Title"])
    ws.append(["SKU-A", 10, "Widget A"])
    ws.append(["SKU-B", 5, "Widget B"])
    ws.append(["SKU-A", 11, "Widget A dup in excel"])  # excel dup — ignored by matcher, second wins in map
    ws.append(["SKU-MISSING", 3, "Not in cache"])
    ws.append(["SKU-DUP", 7, "Duplicate SKU in Shopify"])
    ws.append(["SKU-UNTRACKED", 4, "Untracked Widget"])
    ws.append(["SKU-BAD", "oops", "Bad qty"])
    ws.append(["", 9, "Blank sku"])
    path = tmp_path / "input.xlsx"
    wb.save(path)
    return path


class FakeShopifyClient:
    """Stand-in that returns scripted responses keyed by a substring of the query.

    Register responses via `.respond_to(substring, response_dict)` or by
    assigning to `.mutations`/`.queries`. Every call is recorded on `.calls`.
    """

    def __init__(self, cfg=None):
        self.cfg = cfg
        self.calls: list[tuple[str, dict]] = []
        self._responses: list[tuple[str, dict]] = []

    def respond_to(self, substring: str, response: dict) -> "FakeShopifyClient":
        self._responses.append((substring, response))
        return self

    def graphql(self, query: str, variables: dict | None = None) -> dict:
        self.calls.append((query, variables or {}))
        for substr, resp in self._responses:
            if substr in query:
                return resp
        raise AssertionError(f"FakeShopifyClient: no response registered for query:\n{query[:200]}")


@pytest.fixture
def fake_client_factory():
    """Returns a function that builds a configured FakeShopifyClient."""
    def _factory(responses=None):
        c = FakeShopifyClient()
        for sub, r in (responses or []):
            c.respond_to(sub, r)
        return lambda cfg: c, c
    return _factory
