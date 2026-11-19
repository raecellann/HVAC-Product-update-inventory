"""push_batches respects qty_mode, reports progress, and records userErrors."""
from __future__ import annotations

import dataclasses


def _absolute_resp(user_errors=None):
    return {
        "inventorySetQuantities": {
            "inventoryAdjustmentGroup": {"createdAt": "x", "reason": "r", "changes": []},
            "userErrors": user_errors or [],
        }
    }


def _delta_resp(user_errors=None):
    return {
        "inventoryAdjustQuantities": {
            "inventoryAdjustmentGroup": {"createdAt": "x", "reason": "r", "changes": []},
            "userErrors": user_errors or [],
        }
    }


def _matched(n):
    return [
        {"sku": f"SKU-{i}", "inventory_item_id": f"gid://i/{i}", "quantity": i, "row": i}
        for i in range(n)
    ]


def test_push_batches_absolute(cfg):
    from update_inventory import push_batches
    from tests.conftest import FakeShopifyClient

    client = FakeShopifyClient().respond_to("inventorySetQuantities", _absolute_resp())
    progress = []
    result = push_batches(client, cfg, _matched(3), progress_cb=lambda d, t: progress.append((d, t)))

    assert result.pushed == 3
    assert result.user_errors == []
    assert progress[-1] == (3, 3)
    # exactly one GraphQL call since 3 rows fit in a single batch of 100
    assert len(client.calls) == 1
    assert "inventorySetQuantities" in client.calls[0][0]


def test_push_batches_delta_mode(cfg):
    from update_inventory import push_batches
    from tests.conftest import FakeShopifyClient

    cfg2 = dataclasses.replace(cfg, qty_mode="delta")
    client = FakeShopifyClient().respond_to("inventoryAdjustQuantities", _delta_resp())
    result = push_batches(client, cfg2, _matched(2))
    assert result.pushed == 2
    assert "inventoryAdjustQuantities" in client.calls[0][0]


def test_push_batches_records_user_errors(cfg):
    from update_inventory import push_batches
    from tests.conftest import FakeShopifyClient

    err = {"field": ["x"], "code": "INVALID", "message": "nope"}
    client = FakeShopifyClient().respond_to("inventorySetQuantities", _absolute_resp([err]))
    result = push_batches(client, cfg, _matched(1))
    assert result.pushed == 1
    assert len(result.user_errors) == 1
    assert result.user_errors[0]["message"] == "nope"
