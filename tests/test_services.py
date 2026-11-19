"""End-to-end preview + apply with a fake Shopify client."""
from __future__ import annotations

import json


class ScriptedBulkClient:
    """Fakes the three bulk-related calls used by preview + cache refresh."""

    def __init__(self, cfg=None):
        self.cfg = cfg
        self.calls: list[tuple[str, dict]] = []
        self._bulk_poll_responses: list[dict] = []
        self._set_response: dict | None = None
        self._bulk_start_response = {
            "bulkOperationRunQuery": {
                "bulkOperation": {"id": "gid://bulk/1", "status": "CREATED"},
                "userErrors": [],
            }
        }
        self._download_url = "http://fake-bulk.invalid/result.jsonl"

    def with_poll_sequence(self, responses):
        self._bulk_poll_responses = list(responses)
        return self

    def with_set_response(self, resp):
        self._set_response = resp
        return self

    def graphql(self, query: str, variables=None):
        self.calls.append((query, variables or {}))
        if "bulkOperationRunQuery" in query:
            return self._bulk_start_response
        if "currentBulkOperation" in query:
            # Pop from sequence until COMPLETED remains
            if self._bulk_poll_responses:
                return self._bulk_poll_responses.pop(0)
            return {
                "currentBulkOperation": {
                    "id": "gid://bulk/1",
                    "status": "COMPLETED",
                    "errorCode": None,
                    "objectCount": 0,
                    "url": self._download_url,
                    "completedAt": "x",
                }
            }
        if "inventorySetQuantities" in query:
            assert self._set_response is not None, "set response not registered"
            return self._set_response
        raise AssertionError(f"Unexpected query: {query[:200]}")


def _completed(url):
    return {
        "currentBulkOperation": {
            "id": "gid://bulk/1",
            "status": "COMPLETED",
            "errorCode": None,
            "objectCount": 2,
            "url": url,
            "completedAt": "x",
        }
    }


def _set_ok():
    return {
        "inventorySetQuantities": {
            "inventoryAdjustmentGroup": {"createdAt": "x", "reason": "r", "changes": []},
            "userErrors": [],
        }
    }


def test_preview_then_apply(cfg, sample_xlsx, sample_cache, monkeypatch, tmp_path):
    from webapp import services, history
    history.init_db()

    # Fake the bulk JSONL download so no network is hit.
    def fake_download(url):
        # Two variants: SKU-A at qty 2 (will change to 10), SKU-B at qty 5 (no change)
        return [
            {
                "sku": "SKU-A",
                "inventoryItem": {
                    "id": "gid://shopify/InventoryItem/101",
                    "tracked": True,
                    "inventoryLevel": {
                        "quantities": [{"name": "available", "quantity": 2}]
                    },
                },
            },
            {
                "sku": "SKU-B",
                "inventoryItem": {
                    "id": "gid://shopify/InventoryItem/102",
                    "tracked": True,
                    "inventoryLevel": {
                        "quantities": [{"name": "available", "quantity": 5}]
                    },
                },
            },
        ]

    monkeypatch.setattr(services.cmp_mod, "download_jsonl", fake_download)

    # Preview
    preview_run_id = history.create_run("preview", file_name="input.xlsx")
    run_d = services.run_dir(preview_run_id)
    upload_path = run_d / "upload.xlsx"
    upload_path.write_bytes(sample_xlsx.read_bytes())

    client = ScriptedBulkClient().with_poll_sequence([_completed("http://fake/x")])
    services.preview_job(
        run_id=preview_run_id,
        upload_path=upload_path,
        cfg=cfg,
        client_factory=lambda c: client,
    )

    run = history.get_run(preview_run_id)
    assert run["status"] == "succeeded"
    assert run["counts"]["matched"] >= 2
    assert run["counts"]["will_change"] >= 1  # SKU-A: 2 -> 10
    preview_rows = json.loads((run_d / "preview.json").read_text(encoding="utf-8"))
    skus = {r["sku"] for r in preview_rows}
    assert {"SKU-A", "SKU-B"}.issubset(skus)

    # Apply
    apply_run_id = history.create_run("apply", parent_run_id=preview_run_id)
    apply_client = ScriptedBulkClient().with_set_response(_set_ok())
    services.apply_job(
        run_id=apply_run_id,
        preview_run_id=preview_run_id,
        cfg=cfg,
        client_factory=lambda c: apply_client,
    )

    apply_run = history.get_run(apply_run_id)
    assert apply_run["status"] == "succeeded"
    assert apply_run["pushed"] >= 2

    # The apply call must have gone to inventorySetQuantities (absolute mode)
    assert any("inventorySetQuantities" in q for q, _ in apply_client.calls)
    # And NEVER to inventoryAdjustQuantities (delta mode must be locked out)
    assert not any("inventoryAdjustQuantities" in q for q, _ in apply_client.calls)

    # Preview is now marked applied; a second apply must be rejected at the route
    # layer. Here we just assert the DB state.
    assert history.get_run(preview_run_id)["applied_at"] is not None


def test_preview_surfaces_missing_sku_column(cfg, sample_cache, tmp_path, monkeypatch):
    """A file with the wrong header gives a clear error, not a traceback."""
    from openpyxl import Workbook
    from webapp import services, history
    history.init_db()

    wb = Workbook()
    ws = wb.active
    ws.append(["Product Code", "Qty", "Name"])  # neither "SKU" nor "Quantity"
    ws.append(["X", 1, "x"])
    bad = tmp_path / "bad.xlsx"
    wb.save(bad)

    preview_run_id = history.create_run("preview", file_name="bad.xlsx")
    run_d = services.run_dir(preview_run_id)
    upload_path = run_d / "upload.xlsx"
    upload_path.write_bytes(bad.read_bytes())

    client = ScriptedBulkClient()
    try:
        services.preview_job(
            run_id=preview_run_id,
            upload_path=upload_path,
            cfg=cfg,
            client_factory=lambda c: client,
        )
    except Exception:
        pass

    run = history.get_run(preview_run_id)
    assert run["status"] == "failed"
    assert "SKU column" in (run["error"] or "")
