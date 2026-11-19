from __future__ import annotations


def test_low_match_warning():
    from webapp.warnings import compute_warnings

    # 1 matched of 100 rows → 1% → triggers low-match warning
    flags = compute_warnings(100, 1, [{"shopify_qty": 5, "new_qty": 5}])
    assert any("match products in Shopify" in f for f in flags)


def test_mass_zero_warning():
    from webapp.warnings import compute_warnings

    rows = [{"shopify_qty": 5, "new_qty": 0} for _ in range(10)]  # 100% zero-out
    flags = compute_warnings(10, 10, rows)
    assert any("drop to 0 stock" in f for f in flags)


def test_clean_run_has_no_warnings():
    from webapp.warnings import compute_warnings

    rows = [{"shopify_qty": 5, "new_qty": 6} for _ in range(10)]
    flags = compute_warnings(10, 10, rows)
    assert flags == []
