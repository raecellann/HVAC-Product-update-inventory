"""compare_quantities returns a clean per-SKU list."""
from __future__ import annotations


def test_compare_quantities_three_states(sample_cache):
    from core.compare_inventory import compare_quantities

    excel = {"sku-a": 10, "sku-b": 5, "sku-gone": 1}
    sku_map = sample_cache["skus"]
    shopify = {"sku-a": 10, "sku-b": 2}  # sku-gone missing

    rows = compare_quantities({"sku-a", "sku-b", "sku-gone"}, excel, sku_map, shopify)
    by_sku = {r.sku: r for r in rows}

    assert by_sku["sku-a"].state == "match"
    assert by_sku["sku-a"].diff == 0
    assert by_sku["sku-b"].state == "differ"
    assert by_sku["sku-b"].diff == 3
    assert by_sku["sku-gone"].state == "no_shopify_data"
    assert by_sku["sku-gone"].shopify_qty is None
