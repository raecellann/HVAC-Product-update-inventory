"""match_rows classifies rows into the six buckets."""
from __future__ import annotations


def test_match_rows_classifies_each_bucket(sample_xlsx, sample_cache):
    from core.update_inventory import read_excel, match_rows

    rows = read_excel(sample_xlsx, "SKU", "Quantity", "Title")
    result = match_rows(rows, sample_cache)

    matched_skus = [m["sku"] for m in result.matched]
    assert "SKU-A" in matched_skus
    assert "SKU-B" in matched_skus
    # SKU-A appears twice in the Excel, both should match (classifier does not dedupe)
    assert matched_skus.count("SKU-A") == 2

    assert [u["sku"] for u in result.unmatched] == ["SKU-MISSING"]
    assert [d["sku"] for d in result.duplicate_hits] == ["SKU-DUP"]
    assert [u["sku"] for u in result.untracked_hits] == ["SKU-UNTRACKED"]

    bad_reasons = {b["sku"]: b["reason"] for b in result.bad_qty}
    assert "SKU-BAD" in bad_reasons and "bad quantity" in bad_reasons["SKU-BAD"]
    assert "" in bad_reasons  # blank SKU row


def test_match_rows_detects_title_mismatch(sample_cache):
    from core.update_inventory import match_rows

    rows = [{"row": 2, "sku": "sku-a", "qty_raw": 1, "title": "Different Title"}]
    result = match_rows(rows, sample_cache)
    assert len(result.matched) == 1
    assert len(result.title_mismatches) == 1
    assert result.title_mismatches[0]["sku"] == "sku-a"
