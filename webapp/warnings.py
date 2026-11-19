"""Warning flags computed from a preview result.

Thresholds (per spec): matched <50% of Excel rows, or >20% of matched rows
would drop a non-zero Shopify qty to zero. Advisory only — the UI nudges the
user to double-check before applying.
"""
from __future__ import annotations

LOW_MATCH_RATIO = 0.50
MASS_ZERO_RATIO = 0.20


def compute_warnings(
    excel_row_count: int,
    matched_count: int,
    preview_rows: list[dict],
) -> list[str]:
    flags: list[str] = []

    if excel_row_count > 0 and matched_count / excel_row_count < LOW_MATCH_RATIO:
        pct = int(100 * matched_count / excel_row_count)
        flags.append(
            f"Only {pct}% of SKUs in your file match products in Shopify. "
            "The file may be wrong or the product list may need refreshing."
        )

    if matched_count > 0:
        going_to_zero = sum(
            1 for r in preview_rows
            if r.get("shopify_qty") and r.get("new_qty") == 0
        )
        if going_to_zero / matched_count > MASS_ZERO_RATIO:
            pct = int(100 * going_to_zero / matched_count)
            flags.append(
                f"{pct}% of matched products would drop to 0 stock. "
                "Double-check this is what you want before applying."
            )

    return flags
