"""Verify whether the 10 test SKUs now match their expected 30% final_price on Shopify."""
from __future__ import annotations

import sys
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.config import load_config
from core.shopify_client import ShopifyClient

TEST_SKUS = [
    ("952-2210-932", Decimal("1486.49"), Decimal("1345.99")),
    ("950-3000-941", Decimal("1808.95"), Decimal("1754.99")),
    ("950-2600-941", Decimal("1813.67"), Decimal("1549.99")),
    ("476-0253-644", Decimal("243.66"),  Decimal("267.99")),
    ("476-0278-644", Decimal("127.74"),  Decimal("115.99")),
    ("950-3800-941", Decimal("4626.88"), Decimal("4187.99")),
    ("564-0177-816", Decimal("52.60"),   Decimal("58.99")),
    ("476-0788-644", Decimal("171.25"),  Decimal("154.99")),
    ("712-0910-753", Decimal("117.22"),  Decimal("106.99")),
    ("950-1800-941", Decimal("1275.70"), Decimal("978.99")),
]

LOOKUP = """
query($query: String!) {
  productVariants(first: 10, query: $query) {
    edges { node { sku price } }
  }
}
"""


def q2(d: Decimal) -> Decimal:
    return d.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def main() -> None:
    cfg = load_config()
    client = ShopifyClient(cfg)

    print(f"{'SKU':<18} {'expected':>10} {'was':>10} {'now':>10} {'status':<12}")
    print("-" * 65)
    synced = 0
    for sku, expected, was in TEST_SKUS:
        data = client.graphql(LOOKUP, {"query": f"sku:{sku}"})
        edges = data["productVariants"]["edges"]
        if not edges:
            print(f"{sku:<18} {expected:>10} {was:>10} {'MISSING':>10}")
            continue
        current = q2(Decimal(edges[0]["node"]["price"]))
        if current == q2(expected):
            status = "SYNCED"
            synced += 1
        elif current == q2(was):
            status = "unchanged"
        else:
            status = "other"
        print(f"{sku:<18} {expected:>10} {was:>10} {current:>10} {status:<12}")
    print("-" * 65)
    print(f"{synced}/{len(TEST_SKUS)} match expected 30%-markup final price on Shopify")


if __name__ == "__main__":
    main()
